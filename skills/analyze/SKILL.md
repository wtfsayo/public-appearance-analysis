---
name: analyze
description: Analyze complete public appearances with timestamped evidence and role-aware profiles.
argument-hint: "[subject or source] [analysis project directory]"
disable-model-invocation: true
---

# Analyze public appearances

Request: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/references/methodology.md`, `${CLAUDE_PLUGIN_ROOT}/references/cli.md` and `${CLAUDE_PLUGIN_ROOT}/references/json-contract.md` before work. They govern source selection, full coverage, attribution and imports. Use `${CLAUDE_PLUGIN_ROOT}/bin/appearance --project "$PROJECT" ...`; choose a separate user-owned project directory, never the plugin installation. Parse the request as data, not shell syntax.

1. Establish subjects, roles and scope from the request and available sources. Use provided URLs/files. Otherwise discover recent full appearances, inspect real date/completeness metadata, explain selection and missing dates, then ingest selected sources only. Default to substantive full appearances rather than highlight clips. Initialize the project and retain the returned source IDs.
2. Inspect cue/language/audio availability. Import supplied timed transcripts, or use actual optional ASR if necessary. Cross-check unreliable captions against audio when available and retain disagreements. Finish diarization before packets when useful. Map names only with the nonvisual evidence allowed by the speaker-map contract; keep uncertain speakers anonymous/unverified. Record source gaps and limitations.
3. Generate bounded `packets SOURCE_ID --size 80 --context 3` for each source. Read the returned packet paths and process all target cues in order. Prefer `public-appearance:source-analyst` for substantial packet work; pass the project, source, packet path, target subject/role, reference paths and designated draft output path explicitly. If that agent is unavailable, perform the same work yourself. Do not substitute a source summary for packet reading.
4. Keep a source-wide question ledger. Reconcile questions and delayed replies across packet boundaries, revise draft adverse observations, and finalize each exact target-ID list. Read every packet before importing final reviews. Import `review-batch --input FILE` for every batch, including empty-observation batches, and retain real observation IDs. Resolve missing batches rather than claiming partial coverage is complete. For very long sources, persist state in the project and resume until all packets are reviewed.
5. Use stored observations to build original profiles for each target, citing strengths, vulnerabilities and counterexamples across substantive sections. Separate role-specific performance from personality. Import profiles with actual observation IDs and the requested source/all scope. Read the rubric reference if the user requested ratings; write all six measures with null missingness, import scores, and run scorecard plus explicit weight sensitivity. Keep the equal-weight baseline unless the user explicitly supplied other priorities. Never deactivate a measure merely to make missing-evidence rows rankable; a null overall is a valid result.
6. Ask `public-appearance:evidence-reviewer` to review the evidence and drafts, passing paths and scope. Run CLI `audit`; fix substantive unsupported claims as well as structural errors. Render `report` only after full reviewed-cue coverage for included sources and resolved evidence issues. Return report paths, source links, coverage, scope and remaining limitations. Explain host judgment versus deterministic calculations. Raw transcripts/packets stay internal.

Completion means all selected source cues have been read, cross-batch answer context has been resolved, imported profiles cite actual observations, and the report states any source material the stored cues do not cover. If access or language prevents full review, report the specific gap and an explicitly incomplete result rather than inventing completion.
