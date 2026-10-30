from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appearance_analysis import analysis, captions, media
from appearance_analysis.store import InputError, add_source, connect, get_source, load_json, replace_cues, source_dir, source_id


class IngestionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = connect(self.root)
        self.addCleanup(self.db.close)

    def timed_file(self, end=4):
        path = self.root / "supplied.json"
        path.write_text(json.dumps([{"start": 0, "end": end, "speaker": "Guest", "text": "A wholly fictional example for a workshop."}]))
        return path

    def test_overlapping_unanchored_repetition_is_preserved(self):
        path = self.root / "repetition.vtt"
        path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nyes\n\n00:00:01.000 --> 00:00:03.000\nyes\n")
        parsed = captions.parse_file(path)
        self.assertEqual([(row["start"], row["end"], row["text"]) for row in parsed], [(0, 2, "yes"), (1, 3, "yes")])

    def test_anchored_rolling_carry_is_not_repeated_in_review(self):
        path = self.root / "rolling.vtt"
        path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nWe<00:00:00.500> should<00:00:01.000> start\n\n00:00:01.000 --> 00:00:04.000\nWe should start\n<00:00:02.000> and<00:00:03.000> stop\n")
        parsed = captions.parse_file(path)
        self.assertEqual([(row["start"], row["text"]) for row in parsed], [(0, "We should start"), (2, "and stop")])

    def test_whitespace_payload_and_multiple_named_turns_survive(self):
        path = self.root / "turns.srt"
        path.write_text("1\n00:00:00,000 --> 00:00:03,000\nRowan: One question.\n \nCasey: One answer.\n")
        parsed = captions.parse_file(path)
        self.assertEqual([(row["speaker"], row["text"]) for row in parsed], [("Rowan", "One question."), ("Casey", "One answer.")])

    def test_public_query_identity_does_not_collapse_and_secrets_are_rejected(self):
        one = media._public_url("https://example.org/watch?episode=one")
        two = media._public_url("https://example.org/watch?episode=two")
        self.assertNotEqual(source_id(one), source_id(two))
        with self.assertRaises(InputError) as caught:
            media._public_url("https://example.org/watch?episode=one&access_token=synthetic-sensitive-value")
        self.assertNotIn("synthetic-sensitive-value", str(caught.exception))

    def test_caption_failure_can_recover_on_explicit_reingest(self):
        url = "https://example.org/watch?episode=one"
        identifier = source_id(url)
        caption = source_dir(self.root, identifier) / "captions.original.json"
        caption.write_text(json.dumps([{"start": 0, "end": 4, "text": "A fictional demonstration."}]))
        rows = captions.parse_file(caption)
        info = {"webpage_url": url, "title": "Synthetic workshop", "duration": 30, "upload_date": "20250101", "language": "en", "uploader": "Synthetic publisher"}
        descriptor = {"language": "en", "language_uncertain": False, "automatic": False, "origin": "publisher captions"}
        with patch.object(media, "_extract", return_value=info), patch.object(media, "_download_captions", side_effect=[InputError("Temporary network failure"), (caption, rows, descriptor)]):
            first = media.ingest(self.db, self.root, url, subject="Casey", no_audio=True)
            recovered = media.ingest(self.db, self.root, url, subject="Casey", no_audio=True)
        self.assertEqual(first["cue_count"], 0)
        self.assertEqual(recovered["cue_count"], 1)
        self.assertNotIn("caption_error", json.loads(get_source(self.db, identifier)["metadata"]))

    def test_caption_derived_duration_refreshes_but_media_duration_does_not(self):
        first = media.import_transcript(self.db, self.root, self.timed_file(4), subject="Casey")
        second = media.import_transcript(self.db, self.root, self.timed_file(9), subject="Casey")
        self.assertEqual((first["duration"], second["duration"]), (4, 9))
        source = get_source(self.db, first["source_id"])
        metadata = json.loads(source["metadata"])
        metadata["duration_basis"] = "ffprobe full media"
        self.db.execute("UPDATE sources SET duration=30,metadata=? WHERE id=?", (json.dumps(metadata), source["id"]))
        self.db.commit()
        third = media.import_transcript(self.db, self.root, self.timed_file(12), subject="Casey")
        self.assertEqual(third["duration"], 30)

    def test_replaced_transcript_cannot_reuse_old_identity_evidence(self):
        imported = media.import_transcript(self.db, self.root, self.timed_file(), subject="Casey")
        source = imported["source_id"]
        cue = self.db.execute("SELECT id FROM cues WHERE source_id=?", (source,)).fetchone()[0]
        analysis.map_speakers(self.db, source, {"speakers": [{"speaker": "Guest", "name": "Casey", "role": "guest", "status": "supported", "evidence": {"kind": "spoken_introduction", "cue_ids": [cue], "note": "Synthetic identity evidence in the earlier supplied version."}}]})
        replace_cues(self.db, source, [{"start": 0, "end": 4, "text": "A different fictional guest now speaks.", "speaker": "Guest", "stable": True}])
        self.db.commit()
        cue = self.db.execute("SELECT id FROM cues WHERE source_id=?", (source,)).fetchone()[0]
        batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
        result = analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": [cue], "observations": [{"subject": "Casey", "kind": "framing", "claim": "The new guest introduces a different topic.", "question_cue_ids": [], "response_cue_ids": [], "evidence_cue_ids": [cue], "alternatives": [], "unresolved": "", "inference": True}]})
        stored = self.db.execute("SELECT attribution FROM observations WHERE id=?", (result["observation_ids"][0],)).fetchone()[0]
        self.assertEqual(stored, "uncertain")

    def test_independent_asr_context_is_bounded_to_the_packet_window(self):
        source = add_source(self.db, locator="https://example.org/synthetic", kind="transcript", title="Synthetic", subject="Casey")
        replace_cues(self.db, source, [{"start": 2, "end": 5, "text": "A fictional answer."}])
        self.db.executemany("INSERT INTO asr_segments VALUES(?,?,?,?,?,?)", [(source, 0, 0, 2, "Before the window.", "en"), (source, 1, 3, 6, "Overlapping recognition.", "en"), (source, 2, 6, 8, "After the window.", "en")])
        self.db.commit()
        packet = analysis.packets(self.db, self.root, source, context=0)["packets"][0]
        payload = load_json(packet["path"])
        self.assertEqual([row["segment_index"] for row in payload["independent_asr_cues"]], [1])

    def test_symlink_database_is_not_opened(self):
        self.db.close()
        outside = self.root / "outside.sqlite"
        (self.root / "analysis.sqlite").replace(outside)
        (self.root / "analysis.sqlite").symlink_to(outside)
        with self.assertRaises(InputError):
            connect(self.root)


if __name__ == "__main__":
    unittest.main()
