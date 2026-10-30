# CLI operation

Use a separate user-owned analysis directory. In plugin skills, `${CLAUDE_PLUGIN_ROOT}` is substituted by Claude Code. In a terminal, set `PLUGIN_ROOT` to the downloaded plugin directory yourself. Always quote paths and names. Treat user arguments as data; do not interpolate arbitrary text as shell code.

```sh
"${CLAUDE_PLUGIN_ROOT}/bin/appearance" --project "$PROJECT" status
```

The wrapper resolves its own plugin root and preserves arguments. It invokes `uv run --project ROOT appearance ...`, adding `--extra media` for `diarize` and `--extra asr` for `transcribe` only. Core commands, `doctor` and `setup-models` do not install those large extras. `uv` manages the Python environment; media binaries remain external prerequisites. No plugin command edits Claude Code settings.

## Commands

Global `--project DIRECTORY` comes before the subcommand. Commands emit JSON to stdout, including report paths. User-facing failures exit 2 with concise stderr. Read the actual JSON response; never guess IDs or output paths. Shell redirection can save a response but does not make raw packets safe to publish.

```text
init
doctor
discover QUERY [--limit N] [--min-duration SECONDS] [--after DATE]
ingest SOURCE [--subject NAME] [--captions PATH] [--title TITLE] [--rights RIGHTS] [--no-audio]
import-transcript FILE --subject NAME [--title TITLE] [--source-url URL] [--rights RIGHTS]
setup-models [--models-dir PATH]
diarize SOURCE_ID [--speakers N] [--distance FLOAT] [--models-dir PATH] [--threads N]
transcribe SOURCE_ID [--model NAME_OR_PATH] [--language CODE]
map-speakers SOURCE_ID --input FILE
packets SOURCE_ID [--size N] [--context N]
review-batch --input FILE
revise-observation OBSERVATION_ID --input FILE
observations [--subject NAME] [--source SOURCE_ID]
import-profile FILE
import-scores FILE
import-comparison FILE
weights --input FILE
scorecard [--scope SCOPE]
sensitivity [--measure MEASURE] [--factor FLOAT] [--scope SCOPE]
audit
report [--output PATH]
status
schema {review,observation,profile,scores,comparison,speakers}
```

Discovery defaults favor 20-minute candidates; limit is 1-50 and results use actual upload dates, with unknown dates kept distinct. Date ordering applies only within metadata-enriched relevance-search candidates; it is not a global most-recent search. Packet defaults are 80 target cues plus 3 context cues. For long/dense sources, lower `--size`; do not load all raw packets into one prompt. Read packet files and response paths actually returned by the command. Context cues can support evidence but cannot expand a batch's review target list.

Packet size is 1-150 and context is 0-20. Keep the original packet size when resuming an existing source. The response has `source_id`, `source_cues` and `packets`; each packet entry has `batch_id`, `path`, `reviewed`. Packet files contain `target_cues`, `context_before`, `context_after` and speaker/language metadata. Derive review target IDs from `target_cues[*].id` in order. Media paths returned by ingestion are project-relative; resolve them against the analysis project root, not the plugin or shell directory. Read other returned paths as specified by their command.

Read `schema KIND` before composing import payloads. The [JSON reference](json-contract.md) explains citation rules and semantic requirements that a schema alone cannot establish. `status`, `observations` and `audit` expose stored state for resuming an analysis. `audit` reports structural checks, not an independent semantic review.

`ingest` accepts local media or supported HTTP(S) sources. Remote retrieval requires `yt-dlp`; audio decoding/probing requires `ffmpeg` and `ffprobe`. `--no-audio` permits a caption-only workflow but cannot support voice analysis. `import-transcript` accepts time-aligned VTT, SRT or a JSON array with actual `start`, `end`, `text` and optional `speaker`; free text does not have invented timestamps. A transcript's final cue is a duration basis, not proof that it covers the original media.

Diarize before packets. Supply `--speakers` only when source evidence supports a participant count; do not assume two people. Anonymous voice clusters, uncertain cues and model diagnostics are not named identities. Transcription uses original-language faster-whisper output and may download the requested ASR model when invoked. Existing captions remain the main cues; ASR provides a cross-check. Without captions, ASR can supply real timed cues. Once packets exist, use a new analysis project for a changed transcript or speaker segmentation rather than mutating frozen evidence.

To correct an already imported judgment, read `schema observation`, write a complete replacement observation, and run `revise-observation OBSERVATION_ID --input FILE`. The source and subject stay the same. The command archives the previous observation and invalidates that subject's stored profiles/ratings and all project comparisons. Reassess and re-import those artifacts after the correction, then audit and render a fresh report. This corrects interpretation; it does not unfreeze source cues or speaker mapping.
