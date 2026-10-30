---
name: audit
description: Audit source coverage, attribution, analytical claims, score evidence and publication boundaries.
argument-hint: "[analysis project directory]"
disable-model-invocation: true
---

# Audit an analysis

Request: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/references/methodology.md`, `${CLAUDE_PLUGIN_ROOT}/references/json-contract.md`, `${CLAUDE_PLUGIN_ROOT}/references/rubric.md` and `${CLAUDE_PLUGIN_ROOT}/references/cli.md`. Use `${CLAUDE_PLUGIN_ROOT}/bin/appearance --project "$PROJECT" ...`; quote data arguments.

1. Inspect `status`, `audit`, `observations` and the actual project artifacts. CLI audit is deterministic structural validation; a clean result is not evidence that the interpretation is sound.
2. Ask `public-appearance:evidence-reviewer` to inspect source-linked claims, or perform the same review yourself. Supply project and reference paths, scope and artifact paths. Require exact missing batch IDs, unsupported claims/citations, uncertain identities, question-context omissions, delayed replies and counterexamples. Check full stored-cue coverage separately from completeness of the original appearance.
3. Review nonvisual name evidence, multilingual/transliteration uncertainty, ASR disagreements, and role/opportunity confounds. Check all six score rationales, null missingness, actual weights and sensitivity claims. Flag caption-only claims about voice, persuasion or interruption, and private-trait inference.
4. Inspect report excerpts and links. Keep third-party transcripts and internal packets out of public output. Confirm that summaries are original analysis and that quotations are brief and necessary.
5. Return findings ordered by consequence, each with artifact path, real evidence ID/timestamp and a concrete correction. Distinguish confirmed defects from unresolved questions. Do not change judgments or overwrite imported state silently. When asked to repair an imported observation, read `schema observation` and use `revise-observation OBSERVATION_ID --input FILE` with a complete corrected observation for the same subject/source. The command archives the old record and invalidates that subject's profiles/ratings and all comparisons. Reassess and re-import those artifacts, then rerun the structural audit and render a new report. Frozen cues/speaker maps require a new project, not this correction path. Do not claim a semantic finding is fixed merely because JSON validates.

Completion means both the deterministic result and the semantic evidence findings are reported, with remaining access/language/coverage gaps explicit. An incomplete source remains incomplete.
