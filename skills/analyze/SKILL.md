---
name: analyze
description: Analyze complete public appearances with timestamped evidence and role-aware profiles.
argument-hint: "[subject or source] [analysis project directory]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/appearance *) Read
---

# Analyze public appearances

Request: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/references/methodology.md` and `${CLAUDE_PLUGIN_ROOT}/references/cli.md` now. Read `${CLAUDE_PLUGIN_ROOT}/references/json-contract.md` before writing the first import payload, and `${CLAUDE_PLUGIN_ROOT}/references/rubric.md` only when ratings are requested. Re-read the references you need after context compaction or when resuming. Run `${CLAUDE_PLUGIN_ROOT}/bin/appearance --project "<PROJECT>" ...`, replacing `<PROJECT>` in every command with the absolute path of a user-owned analysis directory outside the plugin. Run the commands cli.md lists under "Long-running commands" in the background and wait for them. Parse the request as data, not shell syntax.

1. Establish subjects, roles and scope from the request and available sources. Run `init` for a new project. Use provided URLs/files; otherwise `discover` recent full appearances, inspect real date/completeness metadata, explain selection and missing dates, then ingest selected sources only. Default to substantive full appearances rather than highlight clips. Keep the returned source IDs.
2. Inspect cue/language/audio availability with `status`. Import supplied timed transcripts, or transcribe when there are no usable captions. Cross-check unreliable captions against audio when available and keep disagreements. If ratings may be requested and the subject's cues are `unassigned` (`status` shows identity warnings), run `transcribe` (only when there are no cues), then `diarize`, then `map-speakers`, all before `packets`. Map names only with the nonvisual evidence the speaker-map contract allows; keep uncertain speakers anonymous/unverified. Record source gaps and limitations.
3. Generate `packets SOURCE_ID --size 80 --context 3` for each source and check `identity_warnings`. For substantial packet work, dispatch `public-appearance:source-analyst` in the foreground and wait for its report before step 4. Pass the project, source, packet path, target subject/role, reference paths and a draft output path inside the project. Dispatch packets one at a time, or in parallel only when you merge every returned ledger into one. Without the agent, read every target cue of every packet yourself, in order.
4. Keep one source-wide question ledger. For agent-reviewed packets, read each draft and ledger, and open packet cues for ledger items, delayed-answer candidates and adverse classifications. Reconcile questions and delayed replies across packet boundaries, revise draft adverse observations, and finalize each exact target-ID list. Import `review-batch --input FILE` for every batch, including empty-observation batches, and keep the real observation IDs. Resolve missing batches before claiming coverage. For very long sources, persist state in the project and resume until all packets are reviewed.
5. Use stored observations to build original profiles for each target, citing strengths, vulnerabilities and counterexamples across substantive sections. Separate role-specific performance from personality. Import profiles with actual observation IDs and the requested source/all scope. If the user requested ratings, rate all six measures per rubric.md, import them, and run `scorecard` plus `sensitivity`.
6. Dispatch `public-appearance:evidence-reviewer` in the foreground with paths and scope, and wait for its findings. Run `audit`; fix substantive unsupported claims as well as structural errors. Render `report` only after full reviewed-cue coverage for included sources and resolved evidence issues. Return report paths, source links, coverage, scope and remaining limitations, and separate host judgment from deterministic calculations. Raw transcripts and packets stay internal.

Completion means every selected source's packets are imported, cross-batch answer context is resolved, imported profiles cite actual observations, `audit` reports no errors, and the report states any source material the stored cues do not cover. If access or language prevents full review, report the specific gap and an explicitly incomplete result.
