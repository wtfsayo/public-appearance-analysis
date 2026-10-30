from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from appearance_analysis import analysis, cli
from appearance_analysis.reporting import report
from appearance_analysis.store import InputError, RUBRIC, add_source, connect, load_json, replace_cues


class EvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = connect(self.root, create=True)
        self.addCleanup(self.db.close)
        self.addCleanup(self.temporary.cleanup)

    def source(self, person="Casey", suffix="one", rights="user-provided"):
        identifier = add_source(self.db, locator=f"https://example.org/{suffix}", kind="transcript", title=f"Synthetic {suffix} interview", subject=person, rights=rights, duration=30)
        replace_cues(self.db, identifier, [
            {"start": 0, "end": 4, "text": "When can the prototype be tested?", "speaker": "Host", "stable": True},
            {"start": 4, "end": 10, "text": "The trial date is undecided. We can publish a test plan on Friday.", "speaker": "Guest", "stable": True},
            {"start": 10, "end": 14, "text": "Which earlier decision would you change?", "speaker": "Host", "stable": True},
            {"start": 14, "end": 23, "text": "We used too many sensors in the first design. The next trial uses one camera.", "speaker": "Guest", "stable": True},
        ])
        self.db.commit()
        ids = [row[0] for row in self.db.execute("SELECT id FROM cues WHERE source_id=? ORDER BY cue_index", (identifier,))]
        analysis.map_speakers(self.db, identifier, {"speakers": [{"speaker": "Guest", "name": person, "role": "synthetic interview guest", "status": "supported", "evidence": {"kind": "user_provided", "cue_ids": [ids[1]], "note": "The supplied synthetic transcript explicitly identifies this fictional guest."}}]})
        return identifier, ids

    def observation(self, person, ids, **updates):
        value = {"subject": person, "kind": "partial_answer", "claim": "The guest separates a decision still pending from a nearer public deliverable.", "question_cue_ids": [ids[0]], "response_cue_ids": [ids[1]], "evidence_cue_ids": ids[:2], "alternatives": ["The trial date may genuinely remain undecided."], "unresolved": "A firm trial date.", "inference": True}
        value.update(updates)
        return value

    def complete(self, person="Casey", suffix="one"):
        source, ids = self.source(person, suffix)
        packets = analysis.packets(self.db, self.root, source, size=2)
        observation_id = None
        for index, packet in enumerate(packets["packets"]):
            target = load_json(packet["path"])["target_cues"]
            observations = [self.observation(person, ids)] if index == 0 else []
            result = analysis.review_batch(self.db, {"batch_id": packet["batch_id"], "reviewed_cue_ids": [row[0] for row in target], "observations": observations})
            if result["observation_ids"]:
                observation_id = result["observation_ids"][0]
        return source, ids, observation_id

    def profile(self, person, observation_id):
        return {"subject": person, "scope": "all", "summary": "The guest offers a limited practical commitment without claiming the full plan is ready.", "strengths": [{"text": "Distinguishes a preliminary deliverable from an unsettled decision.", "observation_ids": [observation_id]}], "vulnerabilities": [], "counterexamples": [], "limits": ["A short synthetic example, not a general personality assessment."]}

    def ratings(self, person, observation_id, values=None):
        values = values or [8, 8, 8, 8, 8, 8]
        return {"subject": person, "scope": "all", "ratings": [{"measure": measure, "score": score, "rationale": "A subjective test rating linked to the supplied example; not a measured trait.", "observation_ids": [observation_id] if score is not None else []} for measure, score in zip(RUBRIC, values)]}

    def test_partial_review_cannot_publish_a_complete_source(self):
        source, ids = self.source()
        packets = analysis.packets(self.db, self.root, source, size=2)
        result = analysis.review_batch(self.db, {"batch_id": packets["packets"][0]["batch_id"], "reviewed_cue_ids": ids[:2], "observations": [self.observation("Casey", ids)]})
        analysis.import_profile(self.db, self.profile("Casey", result["observation_ids"][0]))
        with self.assertRaises(InputError):
            report(self.db, self.root)
        self.assertFalse(analysis.audit(self.db)["complete"])

    def test_missing_review_id_is_rejected_without_partial_writes(self):
        source, ids = self.source()
        batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
        with self.assertRaises(InputError):
            analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids[:-1], "observations": [self.observation("Casey", ids)]})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM cue_reviews").fetchone()[0], 0)

    def test_cross_source_evidence_and_unasked_nonanswer_are_rejected(self):
        source, ids = self.source()
        other, foreign_ids = self.source("Morgan", "two")
        batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
        bad = self.observation("Casey", ids, evidence_cue_ids=[ids[0], foreign_ids[1]])
        with self.assertRaises(InputError):
            analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids, "observations": [self.observation("Casey", ids), bad]})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
        bad = self.observation("Casey", ids, kind="non_answer", question_cue_ids=[])
        with self.assertRaises(InputError):
            analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids, "observations": [bad]})

    def test_frozen_source_and_stale_snapshot_are_rejected(self):
        source, ids = self.source()
        batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
        with self.assertRaises(InputError):
            replace_cues(self.db, source, [{"start": 0, "end": 2, "text": "Changed source"}])
        self.db.execute("UPDATE cues SET text='Out-of-band source alteration' WHERE id=?", (ids[1],))
        self.db.commit()
        with self.assertRaises(InputError):
            analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids, "observations": [self.observation("Casey", ids)]})

    def test_uncertain_identity_cannot_support_a_numeric_score(self):
        source, ids = self.source()
        self.db.execute("UPDATE speakers SET status='unverified' WHERE source_id=?", (source,))
        self.db.commit()
        batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
        identifier = analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids, "observations": [self.observation("Casey", ids)]})["observation_ids"][0]
        with self.assertRaises(InputError) as caught:
            analysis.import_scores(self.db, self.ratings("Casey", identifier))
        self.assertIn("speaker mapping", str(caught.exception))
        analysis.import_scores(self.db, self.ratings("Casey", identifier, [None] * 6))
        self.assertIsNone(analysis.scorecard(self.db)["rows"][0]["overall"])

    def test_unlabeled_captions_warn_that_scores_need_a_speaker_mapping(self):
        source = add_source(self.db, locator="https://example.org/unlabeled", kind="transcript", title="Synthetic unlabeled", subject="Sam Speaker", duration=10)
        replace_cues(self.db, source, [{"start": 0, "end": 4, "text": "An unlabeled synthetic sentence."},
                                       {"start": 4, "end": 8, "text": "Another unlabeled synthetic sentence."}])
        self.db.commit()
        self.assertIn("Sam Speaker", [item.get("subject") for item in analysis.audit(self.db)["warnings"]])
        result = analysis.packets(self.db, self.root, source)
        self.assertEqual([(item["source_id"], item["subject"]) for item in result["identity_warnings"]], [(source, "Sam Speaker")])
        self.assertIn("map-speakers", result["identity_warnings"][0]["problem"])

    def test_mapped_subject_has_no_identity_warning(self):
        source, _ = self.source()
        self.assertEqual(analysis.packets(self.db, self.root, source)["identity_warnings"], [])

    def test_packets_are_compact_cue_arrays(self):
        source, ids = self.source()
        packet = analysis.packets(self.db, self.root, source, size=2, context=1)["packets"][0]
        raw = Path(packet["path"]).read_text()
        self.assertNotIn("\n", raw)
        payload = json.loads(raw)
        self.assertEqual(payload["cue_fields"], ["id", "start", "end", "speaker", "text"])
        self.assertEqual(payload["target_cues"][0], [ids[0], 0, 4, "Host", "When can the prototype be tested?"])
        self.assertEqual([row[0] for row in payload["context_after"]], [ids[2]])
        self.assertNotIn("independent_asr_cues", payload)

    def run_cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = cli.main(list(argv))
            except SystemExit as stopped:
                code = stopped.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_requires_an_initialized_nonempty_project(self):
        self.assertEqual(self.run_cli("--project", "", "status")[0], 2)
        missing = self.root / "missing" / "project"
        code, _, error = self.run_cli("--project", str(missing), "status")
        self.assertEqual(code, 2)
        self.assertIn("run init first", error)
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(self.run_cli("--project", str(missing), "init")[0], 0)
        self.assertEqual(self.run_cli("--project", str(missing), "status")[0], 0)

    def test_cli_observations_are_paged(self):
        self.complete()
        self.complete("Morgan", "two")
        code, output, _ = self.run_cli("--project", str(self.root), "observations", "--limit", "1")
        self.assertEqual(code, 0)
        page = json.loads(output)
        self.assertEqual((len(page["observations"]), page["total"], page["next_offset"]), (1, 2, 1))
        page = json.loads(self.run_cli("--project", str(self.root), "observations", "--limit", "1", "--offset", "1")[1])
        self.assertEqual((len(page["observations"]), page["next_offset"]), (1, None))

    def test_missing_measure_is_not_zero_and_disabled_weight_does_not_penalize(self):
        _, _, observation = self.complete()
        analysis.import_scores(self.db, self.ratings("Casey", observation, [8, 8, 8, None, 8, 8]))
        first = analysis.scorecard(self.db)["rows"][0]
        self.assertIsNone(first["overall"])
        self.assertIsNone(first["rank"])
        self.assertEqual(first["missing_active_measures"], ["responsiveness"])
        analysis.set_weights(self.db, {"responsiveness": 0})
        self.assertEqual(analysis.scorecard(self.db)["rows"][0]["overall"], 8)

    def test_weight_sensitivity_reverses_order_without_mutating_baseline(self):
        _, _, casey = self.complete()
        _, _, morgan = self.complete("Morgan", "two")
        analysis.import_scores(self.db, self.ratings("Casey", casey, [8, 8, 8, 8, 2, 8]))
        analysis.import_scores(self.db, self.ratings("Morgan", morgan, [7, 7, 7, 7, 9, 7]))
        baseline = analysis.scorecard(self.db)
        self.assertEqual(baseline["rows"][0]["subject"], "Morgan")
        scenario = analysis.sensitivity(self.db, measure="engagement", factor=0)
        self.assertEqual(scenario["scenario"]["rows"][0]["subject"], "Casey")
        self.assertEqual(analysis.scorecard(self.db), baseline)

    def test_equal_totals_share_rank_and_zero_scores_are_real_values(self):
        _, _, casey = self.complete()
        _, _, morgan = self.complete("Morgan", "two")
        analysis.import_scores(self.db, self.ratings("Casey", casey, [0] * 6))
        analysis.import_scores(self.db, self.ratings("Morgan", morgan, [0] * 6))
        rows = analysis.scorecard(self.db)["rows"]
        self.assertEqual([(row["rank"], row["overall"]) for row in rows], [(1, 0), (1, 0)])

    def test_invalid_weight_sets_are_atomic(self):
        original = [tuple(row) for row in self.db.execute("SELECT * FROM rubric ORDER BY measure")]
        for value in [float("nan"), float("inf"), -1, True]:
            with self.subTest(value=value), self.assertRaises(InputError):
                analysis.set_weights(self.db, {"clarity": value})
        with self.assertRaises(InputError):
            analysis.set_weights(self.db, {measure: 0 for measure in RUBRIC})
        self.assertEqual([tuple(row) for row in self.db.execute("SELECT * FROM rubric ORDER BY measure")], original)

    def test_scores_cannot_borrow_another_subjects_evidence_or_use_boolean_scores(self):
        _, _, casey = self.complete()
        _, _, morgan = self.complete("Morgan", "two")
        with self.assertRaises(InputError):
            analysis.import_scores(self.db, self.ratings("Casey", morgan))
        payload = self.ratings("Casey", casey)
        payload["ratings"][0]["score"] = True
        with self.assertRaises(InputError):
            analysis.import_scores(self.db, payload)

    def test_asr_does_not_masquerade_as_diarization(self):
        source, _, observation = self.complete()
        self.db.execute("INSERT INTO audio_runs VALUES(?,?,?)", (source, '{"asr":{}}', '{"asr":{"segment_count":2}}'))
        self.db.commit()
        analysis.import_profile(self.db, self.profile("Casey", observation))
        coverage = analysis.audit(self.db)["coverage"][0]
        self.assertTrue(coverage["asr_processed"])
        self.assertFalse(coverage["audio_processed"])

    def test_correction_invalidates_cached_judgments_but_retains_source_review(self):
        _, ids, casey = self.complete()
        _, _, morgan = self.complete("Morgan", "two")
        analysis.import_profile(self.db, self.profile("Casey", casey))
        analysis.import_profile(self.db, self.profile("Morgan", morgan))
        analysis.import_scores(self.db, self.ratings("Casey", casey))
        analysis.import_scores(self.db, self.ratings("Morgan", morgan))
        analysis.import_comparison(self.db, {"title": "Synthetic comparison", "summary": "A role-aware comparison of two fictional responses.", "points": [{"text": "Their limits differ in the supplied examples.", "observation_ids": [casey, morgan], "alternatives": ["The questions differ."]}], "limits": ["Synthetic examples."]})
        analysis.revise_observation(self.db, casey, self.observation("Casey", ids, unresolved="A date remains unknown, but the plan commitment should be credited."))
        self.assertEqual([row[0] for row in self.db.execute("SELECT subject FROM profiles")], ["Morgan"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM ratings WHERE subject='Casey'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM comparisons").fetchone()[0], 0)
        self.assertTrue(analysis.audit(self.db)["complete"])

    def test_correction_marks_tracked_outputs_stale_until_regenerated(self):
        _, ids, observation = self.complete()
        analysis.import_profile(self.db, self.profile("Casey", observation))
        path = report(self.db, self.root)["report"]
        result = analysis.revise_observation(self.db, observation, self.observation("Casey", ids, unresolved="An updated uncertainty judgment."))
        self.assertEqual(self.db.execute("SELECT stale FROM rendered_reports WHERE path=?", (path,)).fetchone()[0], 1)
        self.assertEqual(result["stale_reports_marked"], [path])
        self.assertEqual(result["stale_reports_not_modified"], [])
        analysis.import_profile(self.db, self.profile("Casey", observation))
        report(self.db, self.root)
        self.assertEqual(self.db.execute("SELECT stale FROM rendered_reports WHERE path=?", (path,)).fetchone()[0], 0)

    def test_correction_does_not_overwrite_an_externally_changed_report(self):
        _, ids, observation = self.complete()
        analysis.import_profile(self.db, self.profile("Casey", observation))
        path = Path(report(self.db, self.root)["report"])
        path.write_text("Separately edited user material.")
        result = analysis.revise_observation(self.db, observation, self.observation("Casey", ids, unresolved="A revised judgment."))
        self.assertEqual(path.read_text(), "Separately edited user material.")
        self.assertEqual(result["stale_reports_not_modified"], [str(path)])

    def test_report_escapes_untrusted_markdown_and_keeps_evidence_links(self):
        source, _, observation = self.complete()
        self.db.execute("UPDATE sources SET title='![untrusted](https://example.org/pixel)' WHERE id=?", (source,))
        self.db.commit()
        analysis.import_profile(self.db, self.profile("Casey", observation))
        rendered = Path(report(self.db, self.root)["report"]).read_text()
        self.assertNotIn("![untrusted](", rendered)
        self.assertIn("https://example.org/one", rendered)
        self.assertIn("00:00:00–00:00:10", rendered)

    def test_bulk_third_party_copy_is_not_published(self):
        source, ids = self.source(rights="third-party")
        protected = " ".join(f"term{index}" for index in range(120))
        self.db.execute("UPDATE cues SET text=? WHERE id=?", (protected, ids[1]))
        self.db.commit()
        batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
        observation = analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids, "observations": [self.observation("Casey", ids, claim=protected)]})["observation_ids"][0]
        analysis.import_profile(self.db, self.profile("Casey", observation))
        with self.assertRaises(InputError):
            report(self.db, self.root)
        self.assertFalse((self.root / "reports/analysis.md").exists())

    def test_supplied_transcript_copy_cannot_hide_in_rendered_limits(self):
        source, ids = self.source(rights="user-provided")
        protected = " ".join(f"term{index}" for index in range(120))
        self.db.execute("UPDATE cues SET text=? WHERE id=?", (protected, ids[1]))
        self.db.commit()
        batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
        observation = analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids, "observations": [self.observation("Casey", ids)]})["observation_ids"][0]
        profile = self.profile("Casey", observation)
        profile["limits"] = [protected]
        analysis.import_profile(self.db, profile)
        with self.assertRaises(InputError):
            report(self.db, self.root)
        profile["limits"] = ["This is a synthetic supplied example with narrow evidential scope."]
        analysis.import_profile(self.db, profile)
        _, _, other = self.complete("Morgan", "two")
        analysis.import_profile(self.db, self.profile("Morgan", other))
        analysis.import_comparison(self.db, {"title": "Synthetic comparison", "summary": "A bounded comparison.", "points": [{"text": "The examples have different contexts.", "observation_ids": [observation, other], "alternatives": []}], "limits": [protected]})
        with self.assertRaises(InputError):
            report(self.db, self.root)

    def test_quote_budget_is_source_scoped_without_disabling_copy_limits(self):
        profiles = []
        for person in ("Casey", "Morgan"):
            source, ids = self.source(person, person)
            excerpt = " ".join(f"{person.lower()}word{index}" for index in range(50))
            self.db.execute("UPDATE cues SET text=? WHERE id=?", (excerpt, ids[1]))
            self.db.commit()
            batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
            finding = self.observation(person, ids, kind="framing", claim="The speaker introduces a synthetic demonstration phrase.", question_cue_ids=[], response_cue_ids=[], evidence_cue_ids=[ids[1]])
            observation = analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids, "observations": [finding]})["observation_ids"][0]
            profile = self.profile(person, observation)
            profile["summary"] = f'An attributed example: "{excerpt}"'
            analysis.import_profile(self.db, profile)
            profiles.append((profile, excerpt))
        report(self.db, self.root)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rendered_reports WHERE stale=0").fetchone()[0], 1)
        profile, excerpt = profiles[0]
        profile["summary"] = f'Two repeated excerpts: "{excerpt}" "{excerpt}"'
        analysis.import_profile(self.db, profile)
        with self.assertRaises(InputError):
            report(self.db, self.root)

    def test_shared_observation_quotes_are_counted_once_but_rendered_profile_quotes_are_not(self):
        source, ids = self.source()
        excerpt = " ".join(f"caseyword{index}" for index in range(50))
        self.db.execute("UPDATE cues SET text=? WHERE id=?", (excerpt, ids[1]))
        self.db.commit()
        batch = analysis.packets(self.db, self.root, source)["packets"][0]["batch_id"]
        finding = self.observation("Casey", ids, kind="framing", claim=f'A synthetic example: "{excerpt}"', question_cue_ids=[], response_cue_ids=[], evidence_cue_ids=[ids[1]])
        observation = analysis.review_batch(self.db, {"batch_id": batch, "reviewed_cue_ids": ids, "observations": [finding]})["observation_ids"][0]
        profile = self.profile("Casey", observation)
        analysis.import_profile(self.db, profile)
        profile["scope"] = source
        analysis.import_profile(self.db, profile)
        report(self.db, self.root)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rendered_reports WHERE stale=0").fetchone()[0], 1)
        profile["summary"] = f'A repeated excerpt: "{excerpt}"'
        analysis.import_profile(self.db, profile)
        with self.assertRaises(InputError):
            report(self.db, self.root)

    def test_finite_large_weights_do_not_overflow_sql_scores_or_integer_binding(self):
        _, _, observation = self.complete()
        analysis.import_scores(self.db, self.ratings("Casey", observation))
        analysis.set_weights(self.db, {"clarity": 1e308})
        self.assertEqual(analysis.scorecard(self.db)["rows"][0]["overall"], 8)
        analysis.set_weights(self.db, {"clarity": 10**100})
        self.assertEqual(analysis.scorecard(self.db)["rows"][0]["overall"], 8)
        with self.assertRaises(InputError):
            analysis.set_weights(self.db, {"clarity": 10**400})

    def test_nonfinite_json_is_rejected(self):
        path = self.root / "invalid.json"
        path.write_text('{"clarity":NaN}')
        with self.assertRaises(InputError):
            load_json(path)


if __name__ == "__main__":
    unittest.main()
