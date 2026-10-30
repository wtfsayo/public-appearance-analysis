from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from itertools import islice
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from . import captions as caption_parser
from .store import InputError, add_source, get_source, measurement, replace_cues, source_dir, source_id


_RIGHTS = {"third-party", "user-provided", "licensed", "public-domain"}
_SECRET_QUERY_KEYS = {
    "key", "apikey", "sig", "lsig", "signature", "policy", "keypairid", "expires", "expiry",
    "token", "accesstoken", "refreshtoken", "idtoken", "auth", "authorization", "authcode",
    "password", "passwd", "pwd", "credential", "secret", "jwt", "session", "sessionid",
    "awsaccesskeyid",
}
# Parallel yt-dlp metadata extractions in `discover`; each takes roughly 10-15 s.
_DISCOVER_WORKERS = 6


def _date(value: str | None, label: str = "Date") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputError(f"{label} must be ISO YYYY-MM-DD or YYYYMMDD.")
    try:
        if re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").date().isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise InputError(f"{label} must be a valid ISO YYYY-MM-DD or YYYYMMDD date.") from exc


def _duration(value: object, *, allow_zero: bool = False) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InputError("Duration must be a finite number of seconds.")
    try:
        seconds = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise InputError("Duration must be a finite number of seconds.") from exc
    if not math.isfinite(seconds) or seconds < 0 or (not allow_zero and seconds == 0):
        raise InputError("Duration must be finite and positive." if not allow_zero else
                         "Duration must be finite and nonnegative.")
    return seconds


def _public_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise InputError("Source URL is malformed.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise InputError("Source URL must use http or https and include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise InputError("Use a public source URL without embedded credentials.")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        if (normalized in _SECRET_QUERY_KEYS or normalized.startswith(("xamz", "xgoog", "oauth")) or
                normalized.endswith(("token", "signature", "password", "credential", "secret"))):
            raise InputError("Use a public source URL without signed, authentication or credential parameters.")
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", parsed.query, ""))


def _yt_command() -> list[str]:
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise InputError("yt-dlp is required for public URL discovery/ingestion. Install yt-dlp and retry; run doctor for dependency status.")


def _binary(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise InputError(f"{name} is required for media ingestion. Install FFmpeg (including ffprobe), or use --no-audio with timed captions.")
    return executable


def _run(argv: list[str], *, purpose: str, timeout: int = 180) -> str:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise InputError(f"{purpose} timed out. Check connectivity or source availability and retry.") from exc
    except OSError as exc:
        raise InputError(f"Cannot execute the tool required for {purpose.lower()}. Run doctor and check installation.") from exc
    if completed.returncode:
        diagnostic = completed.stderr.lower()
        if "429" in diagnostic or "too many requests" in diagnostic:
            reason = "The publisher is rate-limiting requests; retry later."
        elif "private" in diagnostic or "sign in" in diagnostic or "login" in diagnostic:
            reason = "The source may require authentication; use an authorized local copy or a public source."
        elif "geo" in diagnostic or "not available in your country" in diagnostic:
            reason = "The source may be region-restricted; use an authorized local copy."
        elif "unsupported url" in diagnostic:
            reason = "The URL is not supported by the installed yt-dlp."
        elif "no audio" in diagnostic or "does not contain any stream" in diagnostic:
            reason = "No usable audio stream was found."
        else:
            reason = "Check source availability and tool versions with doctor; use an authorized local copy if needed."
        # Downloader diagnostics can contain expiring signed URLs and credentials.
        raise InputError(f"{purpose} failed (exit {completed.returncode}). {reason}")
    return completed.stdout


def _yt_options() -> list[str]:
    return [*_yt_command(), "--ignore-config", "--no-plugin-dirs", "--no-cache-dir",
            "--no-warnings", "--no-progress", "--no-colors", "--socket-timeout", "30"]


def _extract(locator: str) -> dict:
    output = _run([*_yt_options(), "--no-playlist", "--dump-single-json", "--skip-download",
                   "--", locator], purpose="Source metadata extraction")
    try:
        info = json.loads(output)
    except json.JSONDecodeError as exc:
        raise InputError("yt-dlp returned invalid metadata; update yt-dlp and retry.") from exc
    if not isinstance(info, dict) or "entries" in info or info.get("_type") in {"playlist", "multi_video"}:
        raise InputError("Ingest one appearance URL at a time, not a playlist or channel.")
    return info


def _filtered_metadata(info: dict) -> dict:
    saved: dict = {"metadata_origin": "yt-dlp", "ingestion_metadata_version": 1}
    for key in ("id", "extractor", "extractor_key", "channel_id", "uploader_id", "availability",
                "live_status", "is_live", "was_live", "language"):
        value = info.get(key)
        if isinstance(value, (str, bool)):
            saved[key] = value
    return saved


def _candidate(info: dict, fallback: str) -> tuple[dict, dict]:
    locator = _public_url(info.get("webpage_url") or fallback)
    title = str(info.get("title") or info.get("id") or "Untitled public appearance")
    publisher = info.get("channel") or info.get("uploader")
    try:
        uploaded = _date(info.get("upload_date"), "Publisher upload date")
    except InputError:
        uploaded = None
    try:
        duration = _duration(info.get("duration"))
    except InputError:
        duration = None
    metadata = _filtered_metadata(info)
    if info.get("upload_date") and uploaded is None:
        metadata["upload_date_status"] = "invalid publisher metadata"
    candidate = {"url": locator, "locator": locator, "title": title,
                 "publisher": str(publisher) if publisher else None,
                 "upload_date": uploaded, "duration": duration,
                 "language": info.get("language") if isinstance(info.get("language"), str) else None,
                 "live_status": info.get("live_status")}
    return candidate, metadata


def discover(db: sqlite3.Connection, query: str, *, limit: int = 10,
             min_duration: float = 1200, after: str | None = None) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise InputError("Discovery requires a nonempty search query of at most 1000 characters.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise InputError("Discovery limit must be an integer from 1 to 50.")
    minimum = _duration(min_duration, allow_zero=True)
    if minimum is None:
        raise InputError("Minimum duration must be a finite nonnegative number of seconds.")
    boundary = _date(after, "After date")
    search_count = min(100, max(20, limit * 3))
    output = _run([*_yt_options(), "--flat-playlist", "--dump-single-json", "--skip-download",
                   "--", f"ytsearch{search_count}:{query.strip()}"], purpose="Public appearance search")
    try:
        search = json.loads(output)
    except json.JSONDecodeError as exc:
        raise InputError("yt-dlp returned invalid search metadata; update yt-dlp and retry.") from exc
    entries = search.get("entries") if isinstance(search, dict) else None
    if not isinstance(entries, list):
        raise InputError("yt-dlp search did not return a candidate list.")
    dated: list[dict] = []
    undated: list[dict] = []
    unknown_duration: list[dict] = []
    failures: list[dict] = []
    seen: set[str] = set()
    pending: list[tuple[str, str]] = []
    skipped_short = 0
    for entry in entries[:search_count]:
        if not isinstance(entry, dict):
            continue
        locator = entry.get("webpage_url") or entry.get("url")
        if not isinstance(locator, str) or not locator.startswith(("https://", "http://")):
            video_id = entry.get("id")
            if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                continue
            locator = f"https://www.youtube.com/watch?v={video_id}"
        safe_locator = _public_url(locator)
        if safe_locator in seen:
            continue
        seen.add(safe_locator)
        # Flat search already reports most durations; skip short hits before the slow per-video extraction.
        try:
            flat_duration = _duration(entry.get("duration"))
        except InputError:
            flat_duration = None
        if flat_duration is not None and flat_duration < minimum:
            skipped_short += 1
            continue
        pending.append((locator, safe_locator))
    enriched = 0
    stopped_early = False
    queued = iter(pending)
    in_flight: deque = deque()
    with ThreadPoolExecutor(max_workers=_DISCOVER_WORKERS) as pool, db:
        # At most one extraction per worker is in flight, so none starts once `limit` candidates qualify.
        for locator, safe_locator in islice(queued, _DISCOVER_WORKERS):
            in_flight.append((pool.submit(_extract, locator), safe_locator))
        number = 0
        while in_flight:
            future, safe_locator = in_flight.popleft()
            number += 1
            try:
                candidate, metadata = _candidate(future.result(), safe_locator)
            except InputError as exc:
                candidate = None
                failures.append({"url": safe_locator, "error": str(exc)})
            print(f"discover: enriched {number}/{len(pending)}", file=sys.stderr, flush=True)
            if candidate is not None:
                enriched += 1
                db.execute("""INSERT INTO candidates(locator,title,publisher,upload_date,duration,metadata)
                  VALUES(?,?,?,?,?,?) ON CONFLICT(locator) DO UPDATE SET title=excluded.title,
                  publisher=excluded.publisher,upload_date=excluded.upload_date,duration=excluded.duration,
                  metadata=excluded.metadata""", (candidate["locator"], candidate["title"], candidate["publisher"],
                  candidate["upload_date"], candidate["duration"], json.dumps(metadata, ensure_ascii=False, allow_nan=False)))
                if candidate["duration"] is None:
                    unknown_duration.append(candidate)
                elif candidate["duration"] >= minimum:
                    if candidate["upload_date"] is None:
                        undated.append(candidate)
                    elif boundary is None or candidate["upload_date"] >= boundary:
                        dated.append(candidate)
            following = next(queued, None)
            if len(dated) >= limit:
                stopped_early = bool(in_flight) or following is not None
                for remaining, _ in in_flight:
                    remaining.cancel()
                break
            if following is not None:
                in_flight.append((pool.submit(_extract, following[0]), following[1]))
    dated.sort(key=lambda candidate: candidate["upload_date"], reverse=True)
    return {"query": query.strip(), "candidates": dated[:limit], "missing_upload_date": undated,
            "missing_duration": unknown_duration, "metadata_failures": failures,
            "searched": len(entries[:search_count]), "skipped_short": skipped_short, "enriched": enriched,
            "stopped_after_limit": stopped_early,
            "min_duration": minimum, "after_inclusive": boundary,
            "limitations": [
                "YouTube ytsearch is relevance-ranked, not an exhaustive upload-date search. Results are newest only within the enriched search sample.",
                "Enrichment follows relevance order and stops once enough dated candidates qualify (stopped_after_limit); later search hits are not inspected.",
                "Candidates with no actual upload date are listed separately and are not claimed to be recent; unknown durations cannot satisfy the duration filter.",
                "Upload dates are publisher metadata, not necessarily recording dates. LIVE labels do not establish a genuine continuous interview; inspect the source.",
            ]}


def _input_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InputError(f"{label} must be an existing readable regular file.") from exc
    if not resolved.is_file():
        raise InputError(f"{label} must be an existing readable regular file.")
    return resolved


def _relative(project: str | Path, path: Path) -> str:
    root = Path(project).expanduser().resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise InputError("Cached media paths must stay inside the analysis project.") from exc


def _cached_file(project: str | Path, folder: Path, value: str | None) -> Path | None:
    if not value:
        return None
    root = Path(project).expanduser().resolve()
    path = root / value
    if path.is_symlink():
        raise InputError("Cached source files must not be symbolic links.")
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError as exc:
        raise InputError("Cached source paths must stay inside their project source directory.") from exc
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size == 0:
        raise InputError("A cached source file is empty or is not a regular file; remove the damaged cache entry and retry.")
    return path


def _target(folder: Path, name: str) -> Path:
    path = folder / name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise InputError("Refusing to overwrite a symbolic link or non-file in the source cache.")
    return path


def _copy_original(source: Path, folder: Path, name: str) -> Path:
    target = _target(folder, name)
    if source == target:
        return target
    try:
        with tempfile.TemporaryDirectory(prefix=".copy-", dir=folder) as temporary:
            copied = Path(temporary) / "original"
            shutil.copyfile(source, copied)
            copied.replace(target)
    except OSError as exc:
        raise InputError("Cannot preserve the supplied source inside the analysis project; check file permissions and free disk space.") from exc
    return target


def _extension(path: Path) -> str:
    extension = path.suffix.lower()
    return extension if re.fullmatch(r"\.[a-z0-9]{1,10}", extension) else ".bin"


def _probe(path: Path) -> dict:
    output = _run([_binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
                   "-show_entries", "format=duration,format_name:stream=index,codec_type,codec_name,sample_rate,channels,duration",
                   "-of", "json", "-i", str(path)], purpose="Media probing")
    try:
        probe = json.loads(output)
    except json.JSONDecodeError as exc:
        raise InputError("ffprobe returned invalid media metadata.") from exc
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if not streams:
        raise InputError("The media has no audio stream. Supply a file with audible source material.")
    duration = None
    for value in [probe.get("format", {}).get("duration"), *(stream.get("duration") for stream in streams)]:
        try:
            duration = _duration(value)
        except InputError:
            continue
        if duration is not None:
            break
    if duration is None:
        raise InputError("ffprobe could not establish a finite positive media duration; use a complete, seekable media file.")
    return {"duration": duration, "format": probe.get("format", {}).get("format_name"),
            "audio_streams": [{key: stream[key] for key in ("index", "codec_name", "sample_rate", "channels")
                               if key in stream} for stream in streams]}


def _decode(audio: Path, folder: Path) -> tuple[Path, dict]:
    target = _target(folder, "audio.mono16k.wav")
    with tempfile.TemporaryDirectory(prefix=".decode-", dir=folder) as temporary:
        decoded = Path(temporary) / "audio.wav"
        _run([_binary("ffmpeg"), "-nostdin", "-v", "error", "-n", "-protocol_whitelist", "file,pipe",
              "-i", str(audio), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
              "-f", "wav", str(decoded)], purpose="Full-audio decoding", timeout=14400)
        probe = _probe(decoded)
        stream = probe["audio_streams"][0]
        if str(stream.get("sample_rate")) != "16000" or stream.get("channels") != 1:
            raise InputError("Decoded audio is not mono 16 kHz; check the installed FFmpeg.")
        decoded.replace(target)
    return target, probe


def _download_audio(locator: str, folder: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix=".download-", dir=folder) as temporary:
        _run([*_yt_options(), "--no-playlist", "--abort-on-error", "--no-write-info-json",
              "--no-write-subs", "--no-write-auto-subs", "--fixup", "never", "--format", "bestaudio/best",
              "--output", str(Path(temporary) / "audio.%(ext)s"), "--", locator],
             purpose="Full-audio download", timeout=14400)
        downloaded = [path for path in Path(temporary).iterdir()
                      if path.is_file() and path.suffix not in {".part", ".ytdl", ".json"}]
        if len(downloaded) != 1 or downloaded[0].stat().st_size == 0:
            raise InputError("yt-dlp did not produce one complete media file. Check source availability and retry.")
        target = _target(folder, "audio.original" + _extension(downloaded[0]))
        downloaded[0].replace(target)
    return target


def _select_track(info: dict) -> tuple[dict, dict] | None:
    language = info.get("language")
    original = language.lower().split("-")[0] if isinstance(language, str) and language else None
    choices: list[tuple[tuple[int, int, int], dict, dict]] = []
    for automatic, tracks in ((False, info.get("subtitles")), (True, info.get("automatic_captions"))):
        if not isinstance(tracks, dict):
            continue
        for code, formats in tracks.items():
            if not isinstance(code, str) or code in {"live_chat", "danmaku"} or not isinstance(formats, list):
                continue
            for track in formats:
                if not isinstance(track, dict) or track.get("ext") not in {"vtt", "srt"}:
                    continue
                url = track.get("url")
                if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
                    continue
                parameters = dict(parse_qsl(urlsplit(url).query))
                name = str(track.get("name") or "")
                if any(key.lower() == "tlang" for key in parameters) or "translated" in name.lower():
                    continue
                marker = code.endswith("-orig") or "original" in name.lower()
                language_match = bool(original and code.lower().split("-")[0] == original)
                known_original = bool(marker or language_match)
                descriptor = {"language": code, "automatic": automatic,
                              "original_language_confirmed_by_metadata": known_original,
                              "language_uncertain": not known_original, "spoken_language_verified": False,
                              "format": track["ext"], "origin": "publisher captions"}
                rank = (int(known_original), int(not automatic), int(track["ext"] == "vtt"))
                choices.append((rank, track, descriptor))
    if not choices:
        return None
    _, track, descriptor = max(choices, key=lambda choice: choice[0])
    return track, descriptor


def _download_captions(info: dict, folder: Path) -> tuple[Path, list[dict], dict] | None:
    selected = _select_track(info)
    if selected is None:
        return None
    track, descriptor = selected
    headers: dict[str, str] = {}
    for supplied in (info.get("http_headers"), track.get("http_headers")):
        if isinstance(supplied, dict):
            headers.update({key: value for key, value in supplied.items()
                            if isinstance(key, str) and isinstance(value, str)})
    target = _target(folder, "captions.original." + track["ext"])
    try:
        with tempfile.TemporaryDirectory(prefix=".captions-", dir=folder) as temporary:
            downloaded = Path(temporary) / ("captions." + track["ext"])
            with urlopen(Request(track["url"], headers=headers), timeout=60) as response, downloaded.open("xb") as output:
                count = 0
                while chunk := response.read(65536):
                    count += len(chunk)
                    if count > 64 * 1024 * 1024:
                        raise InputError("The caption track exceeds the 64 MiB safety limit; supply a smaller timed transcript.")
                    output.write(chunk)
            rows = caption_parser.parse_file(downloaded)
            downloaded.replace(target)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("Original-caption retrieval failed. Audio is retained; supply timed captions or run transcribe for ASR.") from exc
    return target, rows, descriptor


def _validate_options(subject: str | None, title: str | None, rights: str) -> None:
    if rights not in _RIGHTS:
        raise InputError("Unknown source rights classification.")
    if subject is not None and (not isinstance(subject, str) or not subject.strip()):
        raise InputError("Subject must be nonempty when supplied.")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise InputError("Title must be nonempty when supplied.")


def _same_cues(db: sqlite3.Connection, identifier: str, rows: list[dict]) -> bool:
    current = db.execute("SELECT start,end,text,speaker,stable,channel FROM cues WHERE source_id=? ORDER BY cue_index",
                         (identifier,)).fetchall()
    return len(current) == len(rows) and all(
        (old["start"], old["end"], old["text"], old["speaker"], bool(old["stable"]), old["channel"]) ==
        (new["start"], new["end"], new["text"], new.get("speaker", "unassigned"),
         bool(new.get("stable", False)), new.get("channel", "captions"))
        for old, new in zip(current, rows)
    )


def _check_cue_change(db: sqlite3.Connection, identifier: str, rows: list[dict]) -> bool:
    changed = not _same_cues(db, identifier, rows)
    if changed and db.execute("SELECT 1 FROM batches WHERE source_id=? LIMIT 1", (identifier,)).fetchone():
        raise InputError("Source cues are frozen after packets are generated. Import changed captions into a new project.")
    return changed


def _result(db: sqlite3.Connection, identifier: str, warnings: list[str], *, cached: bool = False) -> dict:
    source = get_source(db, identifier)
    count = db.execute("SELECT COUNT(*) FROM cues WHERE source_id=?", (identifier,)).fetchone()[0]
    metadata = json.loads(source["metadata"])
    if count:
        status = "ready"
        actions = [] if source["wav_path"] else ["Audio analysis requires cached mono audio; rerun ingest without --no-audio if desired."]
    elif source["wav_path"]:
        status = "needs_asr"
        actions = [f"Run transcribe {identifier} with the optional ASR extra, or supply a VTT/SRT/timed JSON transcript."]
    elif source["audio_path"]:
        status = "needs_audio_decode_and_transcript"
        actions = ["Install FFmpeg including ffprobe, rerun ingest to decode cached full audio, then run transcribe or provide timed captions."]
    else:
        status = "needs_transcript"
        actions = ["Supply timed captions, or rerun ingest without --no-audio and run transcribe with the optional ASR extra."]
    return {"source_id": identifier, "locator": source["locator"], "title": source["title"],
            "publisher": source["publisher"], "upload_date": source["upload_date"],
            "duration": source["duration"], "duration_basis": metadata.get("duration_basis"),
            "cue_count": count, "status": status, "cached": cached,
            "audio_path": source["audio_path"], "wav_path": source["wav_path"],
            "captions_path": source["captions_path"], "track_language": source["track_language"],
            "caption_state": metadata.get("caption_state"), "caption_track": metadata.get("caption_track"),
            "warnings": warnings, "next_actions": actions,
            "speaker_labels": "Explicit caption labels are source labels, not verified identities."}


def ingest(db: sqlite3.Connection, project: str | Path, locator: str, *, subject: str | None = None,
           captions: Path | None = None, title: str | None = None, rights: str = "third-party",
           no_audio: bool = False) -> dict:
    _validate_options(subject, title, rights)
    if not isinstance(locator, str) or not locator.strip():
        raise InputError("Ingest requires a public URL or local media path.")
    if not isinstance(no_audio, bool):
        raise InputError("no_audio must be a boolean.")
    explicit = _input_file(captions, "Caption input") if captions is not None else None
    rows = caption_parser.parse_file(explicit) if explicit else None
    try:
        parsed = urlsplit(locator)
    except ValueError as exc:
        raise InputError("Source URL is malformed.") from exc
    remote = parsed.scheme.lower() in {"http", "https"}
    local: Path | None = None
    if remote:
        origin = _public_url(locator)
    else:
        if parsed.scheme == "file":
            if parsed.netloc not in {"", "localhost"}:
                raise InputError("Local file URLs must not refer to a remote host.")
            local = _input_file(unquote(parsed.path), "Local media")
        elif parsed.scheme:
            raise InputError("Source must be a public http(s) URL or a local media path.")
        else:
            local = _input_file(locator, "Local media")
        origin = local.as_uri()
    identifier = source_id(origin)
    existing = db.execute("SELECT * FROM sources WHERE id=?", (identifier,)).fetchone()
    changed_cues = _check_cue_change(db, identifier, rows) if rows is not None else False
    folder = source_dir(project, identifier)
    old_metadata = json.loads(existing["metadata"]) if existing else {}
    metadata = dict(old_metadata)
    warnings: list[str] = []
    audio_path = _cached_file(project, folder, existing["audio_path"]) if existing else None
    wav_path = _cached_file(project, folder, existing["wav_path"]) if existing else None
    caption_path = _cached_file(project, folder, existing["captions_path"]) if existing else None
    track_language = existing["track_language"] if existing else None
    info: dict | None = None
    if remote and (not existing or metadata.get("metadata_origin") != "yt-dlp"):
        info = _extract(locator)
        candidate, filtered = _candidate(info, origin)
        metadata.update(filtered)
        source_title = title or candidate["title"]
        publisher, uploaded, duration = candidate["publisher"], candidate["upload_date"], candidate["duration"]
        language = candidate["language"]
        metadata["duration_basis"] = "publisher metadata; not audio-probed" if duration else "unknown"
    elif existing:
        source_title = title or existing["title"]
        publisher, uploaded, duration, language = (existing[key] for key in ("publisher", "upload_date", "duration", "language"))
    else:
        source_title, publisher, uploaded, duration, language = title or local.stem, None, None, None, None
        metadata.update({"metadata_origin": "ffprobe", "ingestion_metadata_version": 1})
    if not remote and not metadata.get("media_probe"):
        metadata["media_probe"] = _probe(local)
        duration = metadata["media_probe"]["duration"]
        metadata["duration_basis"] = "ffprobe supplied local media; audio caching skipped" if no_audio else "ffprobe full media"
    if not no_audio:
        try:
            if audio_path is None:
                audio_path = _download_audio(locator, folder) if remote else _copy_original(
                    local, folder, "media.original" + _extension(local))
            if not metadata.get("media_probe"):
                metadata["media_probe"] = _probe(audio_path)
            duration = metadata["media_probe"]["duration"]
            metadata["duration_basis"] = "ffprobe full media"
            if wav_path is None:
                wav_path, metadata["wav_probe"] = _decode(audio_path, folder)
            metadata.pop("audio_error", None)
        except InputError as exc:
            if not remote and audio_path is None:
                raise
            metadata["audio_error"] = str(exc)
            warnings.append(str(exc))
    if explicit:
        caption_path = _copy_original(explicit, folder, "captions.provided" + explicit.suffix.lower())
        track_language = None
        metadata["caption_state"] = "provided"
        metadata.pop("caption_error", None)
        metadata["caption_track"] = {"origin": "user-provided timed transcript", "language_uncertain": True,
                                     "original_language_confirmed_by_metadata": False}
    elif (caption_path is None and metadata.get("caption_state") in {None, "retrieval_failed"} and
          not db.execute("SELECT 1 FROM batches WHERE source_id=? LIMIT 1", (identifier,)).fetchone()):
        if remote:
            try:
                info = info if info is not None else _extract(locator)
                downloaded = _download_captions(info, folder)
                if downloaded:
                    caption_path, rows, descriptor = downloaded
                    changed_cues = _check_cue_change(db, identifier, rows)
                    metadata["caption_state"] = "available"
                    metadata["caption_track"] = descriptor
                    track_language = descriptor["language"]
                else:
                    metadata["caption_state"] = "missing_original_track"
                metadata.pop("caption_error", None)
            except InputError as exc:
                metadata["caption_state"] = "retrieval_failed"
                metadata["caption_error"] = str(exc)
                warnings.append(str(exc))
        else:
            metadata["caption_state"] = "not_provided"
    if rows is None and not db.execute("SELECT 1 FROM cues WHERE source_id=? LIMIT 1", (identifier,)).fetchone():
        warnings.append("No usable source captions are available. No transcript has been invented; use supplied timed captions or ASR.")
    if metadata.get("caption_track", {}).get("language_uncertain"):
        warnings.append("Caption language/original-language status is uncertain; inspect the text and use source-language ASR if needed.")
    with db:
        add_source(db, locator=origin, kind="url" if remote else "local-media", title=source_title,
                   publisher=publisher, upload_date=uploaded, duration=duration, language=language,
                   rights=rights, subject=subject.strip() if subject else None, metadata=metadata)
        db.execute("UPDATE sources SET audio_path=?,wav_path=?,captions_path=?,track_language=? WHERE id=?",
                   (_relative(project, audio_path) if audio_path else None,
                    _relative(project, wav_path) if wav_path else None,
                    _relative(project, caption_path) if caption_path else None, track_language, identifier))
        if rows is not None and changed_cues:
            replace_cues(db, identifier, rows)
        if duration:
            measurement(db, identifier, "source_duration", duration, "seconds", metadata["duration_basis"])
        count = db.execute("SELECT COUNT(*) FROM cues WHERE source_id=?", (identifier,)).fetchone()[0]
        measurement(db, identifier, "caption_cue_count", float(count), "cues",
                    "Timed cue count is a processing diagnostic, not proof of transcript accuracy or coverage.")
    return _result(db, identifier, warnings, cached=existing is not None)


def import_transcript(db: sqlite3.Connection, project: str | Path, path: Path, *, subject: str,
                      title: str | None = None, source_url: str | None = None,
                      rights: str = "user-provided") -> dict:
    _validate_options(subject, title, rights)
    if subject is None:
        raise InputError("Transcript import requires a subject.")
    supplied = _input_file(path, "Transcript input")
    rows = caption_parser.parse_file(supplied)
    origin = _public_url(source_url) if source_url is not None else supplied.as_uri()
    identifier = source_id(origin)
    existing = db.execute("SELECT * FROM sources WHERE id=?", (identifier,)).fetchone()
    changed = _check_cue_change(db, identifier, rows)
    folder = source_dir(project, identifier)
    preserved = _copy_original(supplied, folder, "captions.provided" + supplied.suffix.lower())
    metadata = json.loads(existing["metadata"]) if existing else {}
    metadata.update({"metadata_origin": "provided timed transcript", "caption_state": "provided",
                     "caption_duration": max(row["end"] for row in rows),
                     "caption_track": {"origin": "user-provided timed transcript", "language_uncertain": True,
                                       "original_language_confirmed_by_metadata": False}})
    caption_derived = (not existing or not existing["duration"] or
                       metadata.get("duration_basis") == "last caption end; not media duration")
    duration = metadata["caption_duration"] if caption_derived else existing["duration"]
    if caption_derived:
        metadata["duration_basis"] = "last caption end; not media duration"
    with db:
        add_source(db, locator=origin, kind="transcript", title=title or (existing["title"] if existing else supplied.stem),
                   publisher=existing["publisher"] if existing else None,
                   upload_date=existing["upload_date"] if existing else None, duration=duration,
                   language=existing["language"] if existing else None, rights=rights,
                   subject=subject.strip(), metadata=metadata)
        db.execute("UPDATE sources SET captions_path=?,track_language=NULL WHERE id=?",
                   (_relative(project, preserved), identifier))
        if changed:
            replace_cues(db, identifier, rows)
        measurement(db, identifier, "source_duration", duration, "seconds",
                    metadata.get("duration_basis", "existing source duration"))
        measurement(db, identifier, "caption_cue_count", float(len(rows)), "cues",
                    "User-provided timed cues; caption end is not evidence of complete media coverage.")
    return _result(db, identifier, ["Duration is based on the last caption end unless a real media duration was already recorded.",
                                  "Provided speaker names are unverified source labels; preserve the source attribution evidence."],
                   cached=existing is not None)


def doctor() -> dict:
    binaries: dict = {}
    for name in ("yt-dlp", "ffmpeg", "ffprobe", "deno", "node", "bun"):
        executable = shutil.which(name)
        entry: dict = {"installed": executable is not None}
        if executable:
            argument = "-version" if name in {"ffmpeg", "ffprobe"} else "--version"
            try:
                output = _run([executable, argument], purpose=f"{name} version lookup", timeout=15)
                match = re.search(r"\b\d+(?:[.\-]\d+){1,4}(?:[a-zA-Z0-9.+_-]*)", output)
                entry["version"] = match[0] if match else "version not reported"
            except InputError:
                entry["version_status"] = "installed but version lookup failed"
        binaries[name] = entry
    dependencies: dict = {}
    for module, distribution in (("jsonschema", "jsonschema"), ("yt_dlp", "yt-dlp"), ("numpy", "numpy"),
                                 ("soundfile", "soundfile"), ("sklearn", "scikit-learn"),
                                 ("sherpa_onnx", "sherpa-onnx"), ("faster_whisper", "faster-whisper")):
        available = importlib.util.find_spec(module) is not None
        entry = {"installed": available}
        if available:
            try:
                entry["version"] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                entry["version_status"] = "module available; package metadata unavailable"
        dependencies[module] = entry
    diarization_ready = all(dependencies[name]["installed"] for name in ("numpy", "soundfile", "sklearn", "sherpa_onnx"))
    return {"binaries": binaries, "python_dependencies": dependencies,
            "capabilities": {"url_sources": binaries["yt-dlp"]["installed"] or dependencies["yt_dlp"]["installed"],
                             "audio_decode": binaries["ffmpeg"]["installed"] and binaries["ffprobe"]["installed"],
                             # The wrapper installs these extras on the first diarize/transcribe call.
                             "diarization_dependencies": "installed" if diarization_ready else "installed on first diarize",
                             "asr_dependency": "installed" if dependencies["faster_whisper"]["installed"] else "installed on first transcribe"},
            "notes": ["No models are loaded or downloaded by doctor. Installed modules do not establish model availability.",
                      "Install the FFmpeg binaries and yt-dlp for public media. Diarization uses the media extra; transcription uses the asr extra.",
                      "Current YouTube extraction may require yt-dlp-ejs and a supported JavaScript runtime; consult yt-dlp installation guidance."]}
