from __future__ import annotations

import importlib.metadata
import json
import math
import re
import sys
import time
import unicodedata
import wave
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from . import models, store
from .store import InputError


def _wav_path(db, project, source_id):
    source = store.get_source(db, source_id)
    if not source["wav_path"]:
        raise InputError("Source has no decoded WAV. Ingest its complete audio before diarization or transcription.")
    folder = store.source_dir(project, source_id).resolve()
    path = Path(source["wav_path"])
    if not path.is_absolute():
        path = Path(project).expanduser().resolve() / path
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder):
        raise InputError("Source WAV must be a regular file inside its project source directory. Re-ingest the source.")
    return source, path.resolve()


def _unfrozen(db, source_id):
    if db.execute("SELECT 1 FROM batches WHERE source_id=? LIMIT 1", (source_id,)).fetchone():
        raise InputError("Source audio evidence is frozen after packets are generated. Diarize and transcribe before packets, or use a new project.")


def _previous_run(db, source_id):
    row = db.execute("SELECT config,diagnostics FROM audio_runs WHERE source_id=?", (source_id,)).fetchone()
    return (json.loads(row[0]), json.loads(row[1])) if row else ({}, {})


def _save_run(db, source_id, kind, config, diagnostics):
    previous_config, previous_diagnostics = _previous_run(db, source_id)
    previous_config[kind] = config
    previous_diagnostics[kind] = diagnostics
    db.execute("INSERT OR REPLACE INTO audio_runs VALUES(?,?,?)", (
        source_id, json.dumps(previous_config, ensure_ascii=False, allow_nan=False),
        json.dumps(previous_diagnostics, ensure_ascii=False, allow_nan=False),
    ))


def _embedding(extractor, samples, sample_rate, np):
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
    stream.input_finished()
    if not extractor.is_ready(stream):
        return None
    vector = np.asarray(extractor.compute(stream), dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or norm <= 0:
        raise InputError("Speaker model produced an invalid embedding; no assignments were saved.")
    return vector / norm


def _speech_intervals(raw, duration):
    changes = defaultdict(int)
    for segment in raw:
        start, end = max(0.0, segment["start"]), min(duration, segment["end"])
        if end > start:
            changes[start] += 1
            changes[end] -= 1
    speech, overlap = [], []
    active, previous = 0, 0.0
    for point, delta in sorted(changes.items()):
        if point > previous:
            if active:
                if speech and speech[-1][1] == previous:
                    speech[-1][1] = point
                else:
                    speech.append([previous, point])
            if active > 1:
                if overlap and overlap[-1][1] == previous:
                    overlap[-1][1] = point
                else:
                    overlap.append([previous, point])
        active += delta
        previous = point
    return speech, overlap


def _intersection(start, end, intervals):
    return sum(max(0.0, min(end, right) - max(start, left)) for left, right in intervals if left < end and right > start)


def _cluster(vectors, distance, speakers, clustering, np):
    if len(vectors) == 1:
        natural = np.zeros(1, dtype=int)
    else:
        natural = clustering(n_clusters=None, distance_threshold=distance, metric="cosine", linkage="average").fit_predict(vectors)
    natural_count = len(set(natural.tolist()))
    if speakers is None or speakers == natural_count:
        labels = natural
    elif speakers > len(vectors):
        raise InputError(f"Only {len(vectors)} usable windows exist, fewer than the supplied {speakers} speakers. Need more clean speech.")
    elif speakers == 1:
        labels = np.zeros(len(vectors), dtype=int)
    else:
        labels = clustering(n_clusters=speakers, metric="cosine", linkage="average").fit_predict(vectors)
    order = sorted(set(labels.tolist()), key=lambda label: int(np.flatnonzero(labels == label)[0]))
    remap = {label: index for index, label in enumerate(order)}
    labels = np.asarray([remap[int(label)] for label in labels], dtype=int)
    centroids = np.stack([vectors[labels == index].mean(axis=0) for index in range(len(order))])
    norms = np.linalg.norm(centroids, axis=1, keepdims=True)
    if (norms <= 0).any():
        raise InputError("Speaker clustering has a zero-norm centroid; no assignments were saved.")
    centroids /= norms
    similarities = vectors @ centroids.T
    scores = similarities[np.arange(len(labels)), labels]
    if len(order) > 1:
        competing = similarities.copy()
        competing[np.arange(len(labels)), labels] = -2
        margins = scores - competing.max(axis=1)
    else:
        margins = np.full(len(labels), np.nan)
    return labels, scores, margins, natural_count


def _refined_segments(windows, speech, overlap):
    boundaries = {point for interval in speech + overlap for point in interval}
    for window in windows:
        boundaries.update((window["start"], window["end"]))
    centers = [(window["start"] + window["end"]) / 2 for window in windows]
    boundaries.update((left + right) / 2 for left, right in zip(centers, centers[1:]))
    segments, cursor = [], 0
    ordered = sorted(boundaries)
    for start, end in zip(ordered, ordered[1:]):
        midpoint = (start + end) / 2
        if not _intersection(start, end, speech):
            continue
        while cursor + 1 < len(windows) and abs(centers[cursor + 1] - midpoint) < abs(centers[cursor] - midpoint):
            cursor += 1
        window = windows[cursor] if windows else None
        if window and window["start"] <= midpoint <= window["end"] and not _intersection(start, end, overlap):
            speaker, score, margin = window["speaker"], window["score"], window["margin"]
        else:
            speaker, score, margin = "unassigned", None, None
        segments.append({"start": start, "end": end, "speaker": speaker, "score": score, "margin": margin})
    return segments


def _independent_checks(samples, sample_rate, windows, voice_count, extractor, duration, np):
    selected = []
    for voice_index in range(voice_count):
        label = f"Voice {voice_index + 1}"
        candidates = [window for window in windows if window["speaker"] == label and window["speech_fraction"] >= 0.95
                      and window["overlap_fraction"] == 0 and window["end"] - window["start"] >= 2.0]
        chosen = []
        for third in range(3):
            local = [window for window in candidates if min(2, int(((window["start"] + window["end"]) / 2) / duration * 3)) == third]
            local.sort(key=lambda window: (window["margin"] if window["margin"] is not None else 0, window["score"]), reverse=True)
            for window in local:
                if all(window["end"] <= previous["start"] or window["start"] >= previous["end"] for previous in chosen):
                    chosen.append(window)
                    break
        # A voice confined to one third may still have repeated clean samples there.
        for window in sorted(candidates, key=lambda candidate: candidate["score"], reverse=True):
            if len(chosen) >= 3:
                break
            if all(window["end"] <= previous["start"] or window["start"] >= previous["end"] for previous in chosen):
                chosen.append(window)
        for window in sorted(chosen, key=lambda candidate: candidate["start"]):
            start, end = window["start"], window["end"]
            vector = _embedding(extractor, samples[int(start * sample_rate):int(end * sample_rate)], sample_rate, np)
            if vector is not None:
                selected.append({"speaker": label, "start": start, "end": end,
                                 "third": min(2, int(((start + end) / 2) / duration * 3)), "embedding": vector})
    pairs, within, between = [], [], []
    for index, left in enumerate(selected):
        for other, right in enumerate(selected[index + 1:], index + 1):
            same = left["speaker"] == right["speaker"]
            cosine = float(np.clip(left["embedding"] @ right["embedding"], -1, 1))
            pairs.append({"sample_a": index, "sample_b": other, "same_speaker": same, "cosine": cosine})
            (within if same else between).append(cosine)
    counts = {f"Voice {index + 1}": sum(sample["speaker"] == f"Voice {index + 1}" for sample in selected) for index in range(voice_count)}
    sufficient = voice_count >= 2 and all(count >= 2 for count in counts.values()) and bool(within) and bool(between)
    gap = min(within) - max(between) if within and between else None
    diagnostics = {
        "status": "consistent_on_sampled_intervals" if sufficient and gap > 0 else ("disagreement" if sufficient else "insufficient_verification"),
        "sample_counts": counts, "within_cosines": within, "between_cosines": between,
        "worst_within_minus_best_between": gap,
        "samples": [{key: value for key, value in sample.items() if key != "embedding"} for sample in selected],
        "pairs": pairs,
        "limits": "Independent embedding consistency only, not identity or diarization accuracy. Clean means model-selected single-voice speech, not human-verified. Samples occur only where that voice is observed.",
    }
    return selected, pairs, diagnostics


def diarize(db, project, source_id, *, speakers: int | None = None, distance: float = 0.6,
            models_dir: Path | None = None, threads: int = 2) -> dict:
    if speakers is not None and (isinstance(speakers, bool) or not isinstance(speakers, int) or speakers < 1):
        raise InputError("--speakers must be a positive participant count supported by source evidence; omit it when unknown.")
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise InputError("--threads must be a positive integer.")
    if not math.isfinite(distance) or not 0 < distance < 2:
        raise InputError("--distance must be finite and between 0 and 2 (cosine distance).")
    _unfrozen(db, source_id)
    source, wav_path = _wav_path(db, project, source_id)
    try:
        import numpy as np
        import sherpa_onnx
        import soundfile as sf
        from sklearn.cluster import AgglomerativeClustering
    except (ImportError, OSError) as exc:
        raise InputError("Diarization needs the media extra (numpy, soundfile, scikit-learn, sherpa-onnx). Run uv with --extra media; check native audio libraries if import fails.") from exc
    paths = models.verified_models(models_dir)
    started = time.monotonic()
    try:
        samples, sample_rate = sf.read(wav_path, dtype="float32", always_2d=False)
    except (OSError, RuntimeError) as exc:
        raise InputError(f"Cannot decode source WAV: {exc}") from exc
    if samples.ndim != 1 or sample_rate != 16000 or not len(samples) or not np.isfinite(samples).all():
        raise InputError("Diarization requires finite, nonempty mono 16 kHz WAV audio. Re-ingest with ffmpeg.")
    duration = len(samples) / sample_rate
    extraction_config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(paths["eres2net"]), num_threads=threads, provider="cpu")
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(paths["segmentation"]), window_shift_ratio=0.1),
            num_threads=threads, provider="cpu"),
        embedding=extraction_config,
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=distance),
        min_duration_on=0.3, min_duration_off=0.5,
    )
    if not config.validate():
        raise InputError("sherpa-onnx rejected the pinned diarization model configuration.")
    print("Processing complete-audio segmentation and initial diarization", file=sys.stderr, flush=True)
    progress = [-1]

    def callback(processed, total):
        bucket = int(processed / total * 10) if total else 0
        if bucket != progress[0]:
            print(f"Segmentation {min(100, bucket * 10)}%", file=sys.stderr, flush=True)
            progress[0] = bucket
        return 0

    try:
        engine = sherpa_onnx.OfflineSpeakerDiarization(config)
        raw = [{"start": float(segment.start), "end": float(segment.end), "speaker": int(segment.speaker)}
               for segment in engine.process(samples, callback=callback).sort_by_start_time()]
        speech, overlap = _speech_intervals(raw, duration)
        if not speech:
            raise InputError("The segmentation model detected no speech. No speaker assignments were saved.")
        extractor = sherpa_onnx.SpeakerEmbeddingExtractor(extraction_config)
        windows, embeddings, rejected = [], [], []
        starts = [index * 1.5 for index in range(max(1, math.floor(max(0, duration - 3) / 1.5) + 1))]
        last_start = max(0.0, duration - 3.0)
        if starts[-1] < last_start - 0.01:
            starts.append(last_start)
        print(f"Refining {len(starts)} dense 3-second windows", file=sys.stderr, flush=True)
        for index, start in enumerate(starts):
            end = min(duration, start + 3.0)
            fraction = _intersection(start, end, speech) / (end - start)
            overlapping = _intersection(start, end, overlap) / (end - start)
            initial_voices = defaultdict(float)
            for segment in raw:
                initial_voices[segment["speaker"]] += max(0.0, min(end, segment["end"]) - max(start, segment["start"]))
            purity = max(initial_voices.values(), default=0.0) / (end - start)
            if fraction < 0.8 or overlapping > 0.1 or purity < 0.8 or end - start < 1.0:
                rejected.append({"start": start, "end": end, "speech_fraction": fraction, "overlap_fraction": overlapping,
                                 "initial_single_voice_fraction": purity, "reason": "insufficient_single_voice_speech"})
                continue
            vector = _embedding(extractor, samples[int(start * sample_rate):int(end * sample_rate)], sample_rate, np)
            if vector is None:
                rejected.append({"start": start, "end": end, "reason": "embedding_not_ready"})
                continue
            windows.append({"start": start, "end": end, "speech_fraction": fraction, "overlap_fraction": overlapping,
                            "initial_single_voice_fraction": purity})
            embeddings.append(vector)
            if index and index % 200 == 0:
                print(f"Embedded window {index}/{len(starts)}", file=sys.stderr, flush=True)
        if not embeddings:
            raise InputError("No usable dense speech windows. Need at least one second of clean speech; captions were not changed.")
        vectors = np.stack(embeddings)
        labels, scores, margins, natural_count = _cluster(vectors, distance, speakers, AgglomerativeClustering, np)
        voice_count = len(set(labels.tolist()))
        count_changed = speakers is not None and natural_count != speakers
        for index, window in enumerate(windows):
            score = float(np.clip(scores[index], -1, 1))
            margin = float(margins[index]) if np.isfinite(margins[index]) else None
            stable = score >= 0.65 and (margin is None or margin >= 0.1)
            window.update(cluster=f"Voice {int(labels[index]) + 1}", speaker=f"Voice {int(labels[index]) + 1}" if stable else "unassigned", score=score, margin=margin)
        segments = _refined_segments(windows, speech, overlap)
        titan_config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(paths["titanet"]), num_threads=threads, provider="cpu")
        if not titan_config.validate():
            raise InputError("sherpa-onnx rejected the pinned TitaNet model configuration.")
        checks, pairs, verification = _independent_checks(samples, sample_rate, windows, voice_count,
                                                         sherpa_onnx.SpeakerEmbeddingExtractor(titan_config), duration, np)
    except (RuntimeError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(f"Local diarization failed: {exc}. No speaker assignments were saved.") from exc
    cue_rows = db.execute("SELECT * FROM cues WHERE source_id=? ORDER BY cue_index", (source_id,)).fetchall()
    assignments = []
    _, prior_diagnostics = _previous_run(db, source_id)
    originals = prior_diagnostics.get("diarization", {}).get("original_cue_labels", [])
    original_by_id = {item["cue_id"]: item for item in originals}
    for cue in cue_rows:
        original_by_id.setdefault(cue["id"], {"cue_id": cue["id"], "speaker": cue["speaker"], "stable": bool(cue["stable"])})
        coverage = defaultdict(float)
        for segment in segments:
            amount = max(0.0, min(cue["end"], segment["end"]) - max(cue["start"], segment["start"]))
            coverage[segment["speaker"]] += amount
        voiced = sum(coverage.values())
        ranked = sorted(((amount, label) for label, amount in coverage.items() if label != "unassigned"), reverse=True)
        best, label = ranked[0] if ranked else (0.0, "unassigned")
        share = best / voiced if voiced else 0.0
        stable = share >= 0.8 and best / (cue["end"] - cue["start"]) >= 0.5
        assignments.append({"cue_id": cue["id"], "speaker": label if stable else "unassigned", "stable": stable,
                            "dominant_voiced_share": share, "assigned_seconds": best})
    speech_seconds = sum(end - start for start, end in speech)
    assigned_seconds = sum(segment["end"] - segment["start"] for segment in segments if segment["speaker"] != "unassigned")
    issues = []
    if count_changed:
        issues.append("Supplied speaker count changed threshold clustering; forced merges or splits need human review.")
    if verification["status"] != "consistent_on_sampled_intervals":
        issues.append(f"Independent TitaNet check: {verification['status']}.")
    if assigned_seconds < 0.8 * speech_seconds:
        issues.append("More than 20% of model-detected speech remains unassigned.")
    diagnostics = {
        "status": "needs_review" if issues else "model_consistent_not_human_verified",
        "issues": issues, "duration_seconds": duration, "speech_seconds": speech_seconds,
        "overlap_seconds": sum(end - start for start, end in overlap), "assigned_speech_seconds": assigned_seconds,
        "natural_cluster_count": natural_count, "requested_speakers": speakers, "cluster_count": voice_count,
        "count_adjustment": ("forced_merges" if natural_count > speakers else "forced_splits") if count_changed else "none",
        "initial_segments": raw, "speech_intervals": speech, "overlap_intervals": overlap,
        "dense_windows": windows, "rejected_windows": rejected, "independent_titanet": verification,
        "cue_assignments": assignments, "original_cue_labels": list(original_by_id.values()),
        "elapsed_seconds": time.monotonic() - started,
        "limits": ["Anonymous within-source clusters, not named identities or verified participant counts.",
                   "Cosine scores and margins are embedding diagnostics, not calibrated confidence or accuracy.",
                   "Speech/overlap masks come from model diarization intervals and may miss speech or overlap.",
                   "Dense-window boundaries have approximately 1.5-second resolution; short turns and mixed windows can be wrong.",
                   "Clustering is language/domain dependent; manually inspect intros, switches and disputed cues."],
    }
    run_config = {
        "wav_path": str(wav_path), "wav_sha256": models.sha256(wav_path), "sample_rate": sample_rate,
        "models": {key: {"path": str(path), "sha256": models.MODELS[key]["sha256"]} for key, path in paths.items()},
        "versions": {package: importlib.metadata.version(package) for package in ("numpy", "sherpa-onnx", "soundfile", "scikit-learn")},
        "speakers": speakers, "distance": distance, "threads": threads, "device": "cpu",
        "window_seconds": 3, "hop_seconds": 1.5, "minimum_speech_fraction": 0.8, "maximum_overlap_fraction": 0.1,
        "minimum_initial_single_voice_fraction": 0.8,
        "assignment_minimum_cosine": 0.65, "assignment_minimum_margin": 0.1, "embedding_storage": "little-endian float32, L2 normalized",
    }
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        _unfrozen(db, source_id)
        db.execute("DELETE FROM voice_pairs WHERE source_id=?", (source_id,))
        db.execute("DELETE FROM voice_samples WHERE source_id=?", (source_id,))
        db.execute("DELETE FROM segments WHERE source_id=?", (source_id,))
        removed_maps = db.execute("DELETE FROM speakers WHERE source_id=?", (source_id,)).rowcount
        diagnostics["invalidated_speaker_maps"] = removed_maps
        db.executemany("INSERT INTO segments VALUES(?,?,?,?,?,?)", (
            (source_id, segment["start"], segment["end"], segment["speaker"], segment["score"], segment["margin"]) for segment in segments))
        db.executemany("UPDATE cues SET speaker=?,stable=? WHERE id=? AND source_id=?", (
            (assignment["speaker"], int(assignment["stable"]), assignment["cue_id"], source_id) for assignment in assignments))
        db.executemany("INSERT INTO voice_samples(source_id,speaker,start,end,model,embedding) VALUES(?,?,?,?,?,?)", (
            (source_id, window["speaker"], window["start"], window["end"], "eres2net", vector.astype("<f4", copy=False).tobytes())
            for window, vector in zip(windows, embeddings)))
        sample_ids = []
        for sample in checks:
            cursor = db.execute("INSERT INTO voice_samples(source_id,speaker,start,end,model,embedding) VALUES(?,?,?,?,?,?)", (
                source_id, sample["speaker"], sample["start"], sample["end"], "titanet", sample["embedding"].astype("<f4", copy=False).tobytes()))
            sample_ids.append(cursor.lastrowid)
        db.executemany("INSERT INTO voice_pairs VALUES(?,?,?,?,?)", (
            (source_id, sample_ids[pair["sample_a"]], sample_ids[pair["sample_b"]], int(pair["same_speaker"]), pair["cosine"]) for pair in pairs))
        _save_run(db, source_id, "diarization", run_config, diagnostics)
        store.measurement(db, source_id, "audio_duration", duration, "seconds", "Complete decoded WAV duration, not caption duration.")
        store.measurement(db, source_id, "model_speech_coverage", speech_seconds / duration, "fraction", "Model-detected speech, not transcript completeness or accuracy.")
        store.measurement(db, source_id, "speaker_assignment_coverage", assigned_seconds / speech_seconds, "fraction", "Assigned model speech; not diarization accuracy.")
    return {"source_id": source_id, "status": diagnostics["status"], "voices": [f"Voice {index + 1}" for index in range(voice_count)],
            "cue_count": len(assignments), "assigned_cues": sum(assignment["stable"] for assignment in assignments),
            "segments": len(segments), "diagnostics": diagnostics, "config": run_config}


def _normalized_text(text):
    return "".join(character for character in unicodedata.normalize("NFKC", text).casefold() if character.isalnum())


def transcribe(db, project, source_id, *, model: str = "small", language: str | None = None) -> dict:
    if not isinstance(model, str) or not model.strip():
        raise InputError("--model must be a faster-whisper model name or local model directory.")
    if language is not None and not re.fullmatch(r"[a-z]{2,3}", language):
        raise InputError("--language must be a source-language code, such as en, fr or zh; omit it for detection.")
    _unfrozen(db, source_id)
    source, wav_path = _wav_path(db, project, source_id)
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model
    except (ImportError, OSError) as exc:
        raise InputError("Transcription needs faster-whisper and its native runtime. Run uv with --extra asr; no external LLM API is used.") from exc
    try:
        with wave.open(str(wav_path), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
            if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2 or not duration:
                raise InputError("Transcription requires nonempty mono 16 kHz signed 16-bit PCM WAV audio. Re-ingest with ffmpeg.")
            pcm = wav.readframes(wav.getnframes())
            if len(pcm) != wav.getnframes() * 2:
                raise InputError("Source PCM WAV is truncated. Re-ingest the complete source.")
    except (OSError, wave.Error) as exc:
        raise InputError(f"Cannot read source PCM WAV: {exc}") from exc
    waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    waveform *= 1 / 32768.0
    started = time.monotonic()
    print("Transcribing complete source audio in its source language", file=sys.stderr, flush=True)
    try:
        cache = models.default_models_dir().parent / "whisper"
        local_model = Path(model).expanduser()
        model_path = local_model.resolve() if local_model.is_dir() else Path(download_model(model, cache_dir=str(cache)))
        engine = WhisperModel(str(model_path), device="cpu", compute_type="int8")
        multilingual = bool(engine.model.is_multilingual)
        if not multilingual and language not in {None, "en"}:
            raise InputError("The selected Whisper model is English-only. Choose a multilingual model for the supplied source language.")
        generated, info = engine.transcribe(waveform, language=language, task="transcribe", beam_size=5,
                                            vad_filter=False, condition_on_previous_text=False, word_timestamps=False)
        segments, segment_diagnostics = [], []
        previous = -1.0
        for index, segment in enumerate(generated):
            start, end = float(segment.start), float(segment.end)
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or start < previous:
                raise InputError(f"Whisper returned an invalid timed segment at index {index}; nothing was saved.")
            previous = start
            segments.append({"start": start, "end": end, "text": segment.text.strip(), "language": info.language})
            segment_diagnostics.append({"index": index, "start": start, "end": end,
                                        "avg_logprob": float(segment.avg_logprob), "no_speech_probability": float(segment.no_speech_prob),
                                        "compression_ratio": float(segment.compression_ratio)})
    except (RuntimeError, ValueError, OSError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(f"Local faster-whisper transcription failed: {exc}. Check the model name/path, available memory and download access.") from exc
    if not any(segment["text"] for segment in segments):
        raise InputError("Whisper produced no speech text. Existing captions were not changed.")
    cues = db.execute("SELECT * FROM cues WHERE source_id=? ORDER BY cue_index", (source_id,)).fetchall()
    cross_check = []
    first = 0
    for cue in cues:
        while first < len(segments) and segments[first]["end"] <= cue["start"]:
            first += 1
        matches = []
        cursor = first
        while cursor < len(segments) and segments[cursor]["start"] < cue["end"]:
            if segments[cursor]["end"] > cue["start"]:
                matches.append(cursor)
            cursor += 1
        asr_text = " ".join(segments[index]["text"] for index in matches)
        original, recognized = _normalized_text(cue["text"]), _normalized_text(asr_text)
        similarity = SequenceMatcher(None, original, recognized, autojunk=False).ratio() if original and recognized else None
        cross_check.append({"cue_id": cue["id"], "start": cue["start"], "end": cue["end"], "asr_segment_indices": matches,
                            "normalized_character_similarity": similarity,
                            "status": "no_asr_overlap" if not recognized else ("text_differs" if similarity is None or similarity < 0.6 else "text_similar")})
    probability = float(info.language_probability)
    diagnostics = {
        "duration_seconds": duration, "processed_duration_seconds": float(info.duration), "segment_count": len(segments),
        "language": info.language, "language_probability": probability if language is None and multilingual else None,
        "language_basis": ("explicit_user_source_language" if language else
                           ("model_detection" if multilingual else "english_only_model_assumption")),
        "source_metadata_language": source["language"], "caption_track_language": source["track_language"],
        "cross_check": cross_check, "segment_diagnostics": segment_diagnostics,
        "existing_cues_preserved": bool(cues), "seeded_cues": 0, "elapsed_seconds": time.monotonic() - started,
        "limits": ["ASR text and language detection can be wrong, especially for names, code-switching and music.",
                   "Language detection is a model estimate, not proof of every segment's language.",
                   "Character similarity uses overlapping timed spans; it is not word error rate, fact checking or caption accuracy.",
                   "Disagreements are retained for listening/review; source captions are not silently corrected."],
    }
    model_files = {str(path.relative_to(model_path)): models.sha256(path) for path in sorted(model_path.iterdir()) if path.is_file()}
    run_config = {"model": model, "model_path": str(model_path), "model_files_sha256": model_files,
                  "backend": "faster-whisper", "version": importlib.metadata.version("faster-whisper"),
                  "model_origin": "user-provided local directory" if local_model.is_dir() else "faster-whisper Hugging Face model resolver",
                  "license_notice": "Whisper originals use MIT; inspect the selected model's own card/license for converted or fine-tuned models.",
                  "original_whisper": "https://github.com/openai/whisper",
                  "device": "cpu", "compute_type": "int8", "task": "transcribe", "language": language,
                  "beam_size": 5, "vad_filter": False, "condition_on_previous_text": False,
                  "wav_path": str(wav_path), "wav_sha256": models.sha256(wav_path),
                  "model_cache": str(cache)}
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        _unfrozen(db, source_id)
        current_cues = db.execute("SELECT id,start,end,text FROM cues WHERE source_id=? ORDER BY cue_index", (source_id,)).fetchall()
        if [tuple(cue) for cue in current_cues] != [(cue["id"], cue["start"], cue["end"], cue["text"]) for cue in cues]:
            raise InputError("Source cues changed during transcription. Retry the cross-check against the current source.")
        if not cues:
            diagnostics["seeded_cues"] = store.replace_cues(db, source_id, (
                {**segment, "channel": "asr", "speaker": "unassigned", "stable": False} for segment in segments if segment["text"]))
        db.execute("DELETE FROM asr_segments WHERE source_id=?", (source_id,))
        db.executemany("INSERT INTO asr_segments VALUES(?,?,?,?,?,?)", (
            (source_id, index, segment["start"], segment["end"], segment["text"], segment["language"])
            for index, segment in enumerate(segments)))
        _save_run(db, source_id, "asr", run_config, diagnostics)
        store.measurement(db, source_id, "asr_segments", len(segments), "count", "All complete-audio source-language ASR segments, not an accuracy measurement.")
    return {"source_id": source_id, "asr_segments": len(segments), "seeded_cues": diagnostics["seeded_cues"],
            "existing_cues_preserved": bool(cues), "diagnostics": diagnostics, "config": run_config}
