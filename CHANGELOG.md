# Changelog

## 1.1.0

- Rolling YouTube auto-captions (`<c>` word timing or `align:start position:0%`) keep each spoken line once instead of about three times.
- Speaker labels are split from caption text only when the prefix looks like a name (`HOST:`, `>> SARAH CHEN:`); sentences such as "The real question is: …" keep their full text. Timed JSON takes speakers only from its `speaker` field.
- `discover` skips search hits shorter than `--min-duration`, enriches the rest in parallel, stops once `--limit` dated candidates qualify and reports progress on stderr.
- Packet files are compact JSON with cue arrays (`cue_fields`); `packets` and `audit` warn when a subject has no supported speaker mapping, and the numeric-score error names that cause.
- `diarize` and `transcribe` print summaries and write full diagnostics to `diarization.json` / `asr.json`; `observations` pages with `--limit`/`--offset`.
- Only `init` creates a project; other commands require an existing project, reject an empty `--project`, and refuse projects inside the plugin directory.
- The wrapper keeps its uv environment outside the versioned plugin directory; `doctor` reports optional extras as installed on first use.
- Skills pre-approve the wrapper, use a literal `<PROJECT>` placeholder, run long commands in the background, wait for agents, load references on demand and state completion criteria. Setup prerequisites moved to `references/setup.md`.

## 1.0.0

- Added the `public-appearance` Claude Code plugin with user-invoked analyze, compare, score, audit and setup skills, a bounded source analyst, and a read-only evidence reviewer.
- Added a local marketplace manifest and an `uv` wrapper that selects optional dependencies by command without changing Claude Code settings.
- Added a project-local SQLite evidence workflow for source discovery/ingestion, timed transcripts, nonvisual speaker mapping, full-cue review packets, original profiles, comparisons and reports.
- Added archived observation corrections with invalidation of dependent profiles, ratings and comparisons, plus stale-output marking that preserves external file edits.
- Added optional real audio diarization, independent embedding checks and source-language ASR using the prepared PCM waveform, with content-pinned model setup and provenance.
- Added six anchored subjective communication measures, SQL weighted means/ranks, null missingness, explicit user priorities and changed-weight sensitivity without replacing the baseline.
- Preserved ambiguous repeated speech, public query identity, recoverable caption acquisition and caption-versus-media duration provenance.
- Froze captions, speaker mappings and audio/ASR evidence at packet creation; included independent ASR context and publication checks covering supplied material and rendered limits.
- Documented source-wide question/answer context, delayed replies, multilingual/transliteration limits, role adjustment, rights and private-trait boundaries.
