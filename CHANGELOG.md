# Changelog

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
