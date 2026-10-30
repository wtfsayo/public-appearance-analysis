# CLI operation

Run every command through the wrapper. In plugin skills Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}`; in a terminal, put the downloaded plugin directory in its place. Replace `<PROJECT>` with the absolute path of the user-owned analysis directory in every command, because shell variables do not persist between Bash calls. Quote paths and names, and treat user arguments as data, never as shell code.

```sh
"${CLAUDE_PLUGIN_ROOT}/bin/appearance" --project "<PROJECT>" status
```

`init` creates the project. Every other project command exits 2 with `no project at PATH; run init first` when the directory has no `analysis.sqlite`, and projects inside the plugin directory are refused. An empty `--project` is rejected.

The wrapper resolves its own plugin root and preserves arguments. It runs `uv run --project ROOT appearance ...`, adding `--extra media` for `diarize` and `--extra asr` for `transcribe` only. The Python environment lives outside the versioned plugin directory, at `$APPEARANCE_VENV`, else `$CLAUDE_PLUGIN_DATA/venv`, else `${XDG_DATA_HOME:-~/.local/share}/public-appearance/venv`, so plugin updates reuse installed extras. Media binaries remain external prerequisites ([setup](setup.md)). No plugin command edits Claude Code settings.

## Long-running commands

`discover`, `ingest` without `--no-audio`, `transcribe`, `diarize` and `setup-models` can run for many minutes on 1–3 hour sources. Run them with `run_in_background: true`, or with `timeout: 600000`. Wait for completion, then read the saved JSON output. Progress lines go to stderr.

## Commands

Global `--project DIRECTORY` comes before the subcommand. Commands emit JSON to stdout, including report paths. User-facing failures exit 2 with concise stderr. Read the actual JSON response for IDs and output paths. Shell redirection can save a response but does not make raw packets safe to publish.

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
observations [--subject NAME] [--source SOURCE_ID] [--limit N] [--offset N]
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

Discovery defaults favor 20-minute candidates, and limit is 1-50. Search hits whose search-listed duration is below `--min-duration` are skipped before enrichment. The rest are enriched in parallel, in relevance order, until `limit` dated candidates qualify (`stopped_after_limit`). Results use actual upload dates, with unknown dates kept distinct. Date ordering applies only within the enriched candidates; it is not a global most-recent search.

`observations` returns up to `--limit` records (default 200, max 1000) plus `total` and `next_offset`; page with `--offset` until `next_offset` is null.

## Packets

Packet size is 1-150 and context is 0-20; defaults are 80 target cues plus 3 context cues. Lower `--size` for long or dense sources, and keep the original size when resuming a source. The response has `source_id`, `source_cues`, `packets` (each with `batch_id`, `path`, `reviewed`) and `identity_warnings`. A non-empty `identity_warnings` names subjects whose observations cannot support numeric scores ([json-contract](json-contract.md#ratings-schema-scores)).

Packet files are compact JSON. `cue_fields` names the columns of every cue array in `context_before`, `target_cues` and `context_after`: `[id, start, end, speaker, text]`. Review target IDs are `target_cues[*][0]`, in order. Context cues can support evidence but never join a batch's target list. `independent_asr_cues` (columns in `asr_fields`) appears only when ASR overlaps the window. `unstable_speaker_cue_ids` and `non_caption_channels` appear only when some cue differs from a stable caption label.

Media paths returned by ingestion are project-relative; resolve them against the analysis project root. Read other returned paths as their command specifies.

## Evidence freeze

Finish captions, ASR, diarization and speaker mapping before `packets`: the first `packets` call for a source freezes its cue text, segmentation and speaker map. `diarize`, `transcribe`, `map-speakers` and re-imported captions are then refused for that source; a changed transcript or speaker segmentation needs a new analysis project. Corrections to imported judgments use `revise-observation` ([json-contract](json-contract.md#observation-correction-schema-observation)) and leave frozen evidence unchanged.

## Sources and audio

`ingest` accepts local media or supported HTTP(S) sources. Remote retrieval requires `yt-dlp`; audio decoding/probing requires `ffmpeg` and `ffprobe`. `--no-audio` permits a caption-only workflow but cannot support voice analysis. `import-transcript` accepts time-aligned VTT, SRT or a JSON array with actual `start`, `end`, `text` and optional `speaker`; untimed text is rejected. JSON text is never split into a speaker label; use the `speaker` field. A transcript's final cue is a duration basis, not proof that it covers the original media.

When ASR is needed, transcribe before diarizing so voice assignments attach to the new cues. Supply `--speakers` only when source evidence supports a participant count. Anonymous voice clusters, uncertain cues and model diagnostics are not named identities. Transcription uses original-language faster-whisper output and may download the requested ASR model. Existing captions remain the main cues and ASR is a cross-check; without captions, ASR supplies the timed cues. `diarize` and `transcribe` print a summary and write full diagnostics to the returned `diagnostics_path`.

Read `schema KIND` before composing import payloads; [json-contract](json-contract.md) gives the rules a schema cannot express. `status`, `observations` and `audit` expose stored state for resuming an analysis. `audit` reports structural checks, not an independent semantic review.
