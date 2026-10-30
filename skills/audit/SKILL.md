---
name: audit
description: Audit source coverage, attribution, analytical claims, score evidence and publication boundaries.
argument-hint: "[analysis project directory]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/appearance *) Read
---

# Audit an analysis

Request: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/references/methodology.md`, `${CLAUDE_PLUGIN_ROOT}/references/json-contract.md`, `${CLAUDE_PLUGIN_ROOT}/references/rubric.md` and `${CLAUDE_PLUGIN_ROOT}/references/cli.md`. Run `${CLAUDE_PLUGIN_ROOT}/bin/appearance --project "<PROJECT>" ...`, replacing `<PROJECT>` in every command with the absolute project path, and quote data arguments.

1. Inspect `status`, `audit`, `observations` (page with `--offset` until `next_offset` is null) and the actual project artifacts. CLI audit is deterministic structural validation; a clean result is not evidence that the interpretation is sound.
2. Dispatch `public-appearance:evidence-reviewer` in the foreground to inspect source-linked claims, and wait for its findings; without the agent, perform the same review yourself. Supply project and reference paths, scope and artifact paths. Require exact missing batch IDs, unsupported claims/citations, uncertain identities, question-context omissions, delayed replies and counterexamples. Check full stored-cue coverage separately from completeness of the original appearance.
3. Review nonvisual name evidence, identity warnings, multilingual/transliteration uncertainty, ASR disagreements, and role/opportunity confounds. Check all six score rationales, null missingness, actual weights and sensitivity claims. Flag caption-only claims about voice, persuasion or interruption, and private-trait inference.
4. Inspect report excerpts and links. Keep third-party transcripts and internal packets out of public output. Confirm that summaries are original analysis and that quotations are brief and necessary.
5. Return findings ordered by consequence, each with artifact path, real evidence ID/timestamp and a concrete correction. Distinguish confirmed defects from unresolved questions. Change imported state only when the user asks for a repair, and report every change. To repair an imported observation, follow json-contract "Observation correction", reassess and re-import the invalidated artifacts, then rerun `audit` and render a new report. A semantic finding counts as fixed only after the corrected evidence is re-read, not when the JSON validates.

Completion means both the deterministic result and the semantic evidence findings are reported, with remaining access/language/coverage gaps explicit. An incomplete source remains incomplete.
