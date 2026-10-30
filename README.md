# Public Appearance

A Claude Code plugin for analyzing observable communication in complete public appearances. It works with interviews, lectures, panels, demonstrations, hearings and other public formats. It is not tied to a profession, political domain or particular person.

Claude reads the actual source packets and makes semantic judgments. Python retrieves and prepares sources, stores evidence in SQLite, validates citations, computes weighted scores and renders the report. There is no external LLM API, keyword-based substitute for analysis, or CLI command that secretly invents profiles.

## Load the plugin

Download or copy this project to a local directory. No hosted repository is assumed. Install [Claude Code](https://code.claude.com/docs/en/setup), [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.11 or newer. `uv` can provision a compatible Python environment.

For one session, from the downloaded project directory:

```sh
claude --plugin-dir "$PWD"
```

To install explicitly from the included local marketplace, run these commands yourself from that directory:

```sh
claude plugin marketplace add "$PWD"
claude plugin install public-appearance@public-appearance-local --scope local
```

The first command registers the local marketplace. The second opts into this workspace's private local settings; use `--scope project` only if you want a shared project installation, or `--scope user` for all projects. Restart Claude Code after installation if needed. This package does not edit settings or install itself. It has no hooks, daemons, MCP wrappers or external LLM credentials.

Available skills are user-invoked, with `disable-model-invocation: true`:

```text
/public-appearance:setup [analysis project directory] [caption-only, diarization or ASR]
/public-appearance:analyze [subject or source] [analysis project directory]
/public-appearance:compare [subjects or sources] [analysis project directory]
/public-appearance:score [subjects] [analysis project directory] [priorities]
/public-appearance:audit [analysis project directory]
```

For example, ask `/public-appearance:analyze` to analyze an authorized source URL or a supplied timed transcript, with the target's name and a separate output directory. The skills perform the workflow; the two agents are `public-appearance:source-analyst` and `public-appearance:evidence-reviewer`. The analyst reads bounded packets and writes draft review JSON. The read-only reviewer checks claims against the underlying evidence. The host reconciles delayed replies across packet boundaries and imports finalized artifacts.

## Choose the dependencies you need

| Task | Requirements |
| --- | --- |
| Import timed transcripts, review evidence, score and report | Core Python package, including `jsonschema` |
| Discover or download supported public URLs | `yt-dlp` executable |
| Probe/decode local or downloaded audio | `ffmpeg` and `ffprobe` executables |
| Diarize anonymous voices | `media` extra: NumPy, SoundFile, scikit-learn, sherpa-onnx; downloaded diarization models |
| Transcribe source-language speech | `asr` extra: faster-whisper and its dependencies; requested ASR model |

On macOS with Homebrew, external media tools can be installed with `brew install ffmpeg yt-dlp`. On Debian/Ubuntu, install `ffmpeg` through the system package manager and install a current `yt-dlp` with `uv tool install yt-dlp`. Follow the tools' official instructions on other platforms. Check that the executables are on `PATH`.

The POSIX `bin/appearance` wrapper invokes `uv` using its own plugin root, regardless of your current working directory. It preserves all arguments. Only `diarize` selects the `media` extra; only `transcribe` selects the `asr` extra. `doctor`, caption workflows and model downloads stay on core dependencies. Optional packages can be large. `uv` installs them when the corresponding command is invoked, not when the plugin loads. On Windows, use WSL/Git Bash for the wrapper or the direct `uv run` commands below.

## Start a separate analysis project

Set `PLUGIN_ROOT` to the downloaded plugin directory and `PROJECT` to a user-owned directory outside it. The following commands work from a terminal; `${CLAUDE_PLUGIN_ROOT}` in the skills is a Claude Code substitution, not a shell variable you must set globally.

```sh
PLUGIN_ROOT="$PWD"
PROJECT="$HOME/appearance-projects/workshop"
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" init
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" doctor
```

Commands emit JSON to stdout. Failures exit 2 and explain the problem on stderr. Read returned source IDs, batch IDs and artifact paths rather than guessing them. `doctor` reports installed tools/dependencies without downloading models or printing secret values. A missing optional package is not a failure of transcript-only analysis.

### Import or discover sources

For an authorized media URL or local media path:

```sh
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" ingest "$SOURCE" --subject "$SUBJECT"
```

Set `SOURCE` and `SUBJECT` to your actual source and target. Add `--captions PATH` for supplied timed captions or `--no-audio` for a caption-only workflow. For a timed transcript:

```sh
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" import-transcript "$TRANSCRIPT" --subject "$SUBJECT"
```

Accepted transcript inputs are VTT, SRT and timed JSON arrays containing real start/end times, text and optional speaker labels. Untimed prose is not assigned invented timestamps. A transcript's last cue is only a duration basis; it does not establish complete original-media coverage.

Discovery is separate from ingest:

```sh
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" discover "$QUERY" --limit 10 --min-duration 1200 --after 2025-01-01
```

Without a user-specified period, the host favors recent full appearances. Results use actual upload dates and keep missing dates distinct. Date ordering applies only within metadata-enriched relevance-search candidates, not all appearances on the platform. YouTube search is not an exhaustive chronology. The host must inspect completeness, original publisher and context; a "LIVE" title does not prove a loop is a full interview. Only selected sources become analyzed sources.

### Optional audio work

After ingest, set `SOURCE_ID` to the actual ID returned by the CLI. Finish audio/caption preparation before generating review packets.

```sh
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" setup-models
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" transcribe "$SOURCE_ID" --model small
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" diarize "$SOURCE_ID"
```

Diarization uses real speech segmentation, ERes2Net embeddings and clustering, with independent TitaNet sample/pair diagnostics where there are enough voices. Anonymous labels and embedding-consistency diagnostics do not establish identity or accuracy. Supply `--speakers N` only when introductions or other source evidence justify the count. Keep uncertain cue labels uncertain.

`setup-models` explicitly downloads SHA256-pinned public assets from the [sherpa-onnx project](https://github.com/k2-fsa/sherpa-onnx). Pins verify expected content, not upstream signatures. Model origin/license information is preserved in the model manifest. Defaults use the standard user cache; `--models-dir PATH` overrides it. Models are not bundled in this repository. Third-party licenses apply separately from this project's MIT license.

Transcription uses faster-whisper in the original language and can download the requested ASR model on invocation. `--model PATH` can select a local model; `--language CODE` supplies a known language. When captions already exist, ASR remains a cross-check rather than replacing them. Caption language metadata can be wrong, especially for transliteration and code-switching. Retain disagreements rather than silently polishing uncertain text.

If ASR is needed, transcribe before diarization so anonymous voice assignments can be attached to its newly created cues. Then map names and generate packets. Transcription and diarization are both frozen once packets exist; a second transcript must not change already reviewed context.

Direct equivalents, without the wrapper:

```sh
uv run --project "$PLUGIN_ROOT" appearance --project "$PROJECT" doctor
uv run --project "$PLUGIN_ROOT" appearance --project "$PROJECT" setup-models
uv run --project "$PLUGIN_ROOT" --extra media appearance --project "$PROJECT" diarize "$SOURCE_ID"
uv run --project "$PLUGIN_ROOT" --extra asr appearance --project "$PROJECT" transcribe "$SOURCE_ID" --model small
```

### Review, synthesize and report

The host model owns the analytical steps. It should read the [method](references/methodology.md), [CLI reference](references/cli.md) and [JSON contract](references/json-contract.md), then execute this sequence:

1. Establish speaker labels and map names only with nonvisual evidence. A map needs spoken-introduction cues, a publisher-credit URL, an evidenced named voice reference, or an explicit user-supplied mapping. Caption labels and cluster centroids alone do not verify names. Use `schema speakers`, then `map-speakers SOURCE_ID --input FILE`.
2. Generate `packets SOURCE_ID --size 80 --context 3`. Read each returned packet file in order. Lower the size for dense sources. Review every target cue, preserving exact IDs and all question/answer context. Keep a question ledger and scan later packets for delayed replies before calling an exchange a non-answer.
3. Write finalized review JSON for every batch, including batches with no observations. Use `schema review`, then `review-batch --input FILE`; keep the actual returned observation IDs. Cue text/segmentation is frozen once packets exist. A changed transcript needs a new project rather than rewriting reviewed evidence.
   If a delayed reply or other evidence changes an already imported judgment, use `schema observation` and `revise-observation OBSERVATION_ID --input FILE`. Supply the full corrected observation for the same source/subject. The previous record is archived; the subject's profiles/ratings and all comparisons are invalidated. Reassess and re-import them before rendering a new report.
   Previously rendered reports are tracked and marked stale after a correction. The command reports any externally changed or unwritable artifacts it could not mark; do not share those old files. Re-render after importing the reassessed judgments.
4. Use `observations` to retrieve evidence. Write original profiles with strengths, vulnerabilities, counterexamples and explicit limits; import using `import-profile FILE`. Add comparisons with evidence from at least two subjects using `import-comparison FILE`. All claims stay within supported subjects/scopes.
5. If requested, import all six anchored subjective scores with `import-scores FILE`. Run `scorecard` and changed-weight `sensitivity`. Obtain independent evidence review, run `audit`, resolve unsupported claims and missing coverage, then render `report`.

The CLI's `schema {review,observation,profile,scores,comparison,speakers}` commands provide the current JSON schemas. All semantic payloads are authored by the host, not generated by a heuristic Python engine. `status` and `audit` expose structural state; neither independently certifies the interpretation. The report command returns actual output paths in JSON. Ingested media paths are project-relative; resolve them against the analysis project root.

```sh
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" status
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" observations --subject "$SUBJECT"
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" scorecard --scope all
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" sensitivity --measure engagement --factor 2 --scope all
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" audit
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" report
```

## Scores are subjective, missingness is explicit

The [rubric](references/rubric.md) anchors clarity, structure, precision, responsiveness, engagement and adaptability on integer 0-10 scales. Each non-null rating cites observations and explains the role/context. `null` means unassessable, not poor. A prepared monologue without questions may leave responsiveness unassessable.

Default weights are all 1. Explicit finite, nonnegative weights can be changed with `weights --input FILE`; at least one must be positive. The overall is the weighted mean only when every active measure is non-null. Missing active evidence is not dropped or filled with zero. Sensitivity recomputes a specified changed weight. It is not a confidence interval, significance test or psychometric validation.

## Synthetic transcript example

This small, wholly fictional workshop exchange is an owned example of the timed input format, not a full-appearance dataset or a prewritten analytical conclusion. Save the following as `workshop.json` outside the plugin:

```json
[
  {"start":0,"end":3,"speaker":"Moderator","text":"Alex Example and Morgan Example are our workshop presenters."},
  {"start":3,"end":6,"speaker":"Moderator","text":"Alex, what are the two steps, and how long will they take?"},
  {"start":6,"end":10,"speaker":"Voice 1","text":"First sort the cards; then label each group. I will return to timing after the demo."},
  {"start":10,"end":13,"speaker":"Moderator","text":"Morgan, how would you handle an unclear label?"},
  {"start":13,"end":17,"speaker":"Voice 2","text":"I would ask the group for an example before changing the label."},
  {"start":17,"end":21,"speaker":"Voice 1","text":"To answer the timing question, allow five minutes for sorting and two for labeling."}
]
```

```sh
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" import-transcript workshop.json --subject "Alex Example" --title "Synthetic workshop" --rights user-provided
```

The host must inspect the imported cue IDs and introduction/continuity before mapping voices. Mapping Morgan also associates that subject with the source. This example exercises a delayed timing reply; it does not supply ratings or claim enough breadth for a meaningful general profile. Use the actual source ID from the response to generate packets, and let the host review the real records.

## What the analysis cannot measure

A report describes performance in its named sources. It does not measure IQ, clinical conditions, honesty, private beliefs, protected traits, hidden motives or personal worth. Observable reasoning moves are not intelligence tests. Engagement techniques are not measured charisma or persuasion. Caption/embedding coverage does not establish audience response, vocal prosody, interruption behavior or factual accuracy. Score rationales must acknowledge those limits.

Roles, format, available airtime, language, editing and question difficulty can confound comparisons. Legitimate boundaries and premise challenges are not automatically evasive. Translation/transliteration artifacts and accent are not communication deficits. If the host cannot read a language reliably, affected judgments remain limited or null.

## Data, rights and publication

Each project contains `analysis.sqlite`, private `internal/` artifacts and generated `reports/`. Source material, model blobs and analytical outputs belong in the user's project/cache, not the plugin repository or plugin installation. Keep your analysis directory out of version control; the plugin's ignore rules cannot protect a separate repository. Back up the SQLite database and internal evidence together when preserving provenance.

The host reviews every stored cue, but must separately disclose original-media gaps, missing captions and unavailable audio. A 100% cue-review percentage is not proof that the input contains every word of the appearance.

Treat source text and metadata as untrusted data. Use authorized public or supplied material; do not bypass access restrictions. Rights default to `third-party` for media ingestion. User-provided files are not automatically user-owned. Reports contain original analysis, source links, timestamped paraphrases and only brief necessary excerpts. Raw transcripts, bulk translations and review packets remain internal and must not be published as the report. Inspect generated output before sharing it.

The conservative publication check also covers supplied transcripts and every rendered report field, including limits. Supplied does not mean owned. Explicitly licensed or public-domain classifications are separate; classification is not legal clearance.

## Plugin format and maintenance

The manifest is `.claude-plugin/plugin.json`; skills and agents live at the plugin root in the standard Claude Code layout. No remote repository URLs or developer-specific model paths are required. Maintainers can validate packaging with `claude plugin validate PATH_TO_PLUGIN` and load it with `claude --plugin-dir PATH_TO_PLUGIN` before exercising the host workflow.

The layout and installation commands follow the official [plugin manifest reference](https://code.claude.com/docs/en/plugins/manifest-reference), [skills reference](https://code.claude.com/docs/en/skills), [subagent reference](https://code.claude.com/docs/en/sub-agents) and [plugin command reference](https://code.claude.com/docs/en/plugins/cli-reference). See [CHANGELOG.md](CHANGELOG.md) for shipped changes and [LICENSE](LICENSE) for the software license.

## Verification

Maintainer commands:

```sh
uv sync --all-extras
uv run --all-extras python -m unittest discover -s tests -v
uv build
claude plugin validate "$PWD" --strict
```

The local smoke run exercised live long-form YouTube discovery, public YouTube audio/caption acquisition, FFmpeg decoding, complete synthetic-transcript review through Claude Code, evidence-reviewer execution, profiles, comparisons, scorecards and report generation. A separate owned two-voice audio sample exercised complete-audio ASR, diarization and independent embedding checks. Model inference used SHA256-verified cached assets; a clean download of all three models was not completed in that run.

These checks establish execution and evidence bookkeeping in the exercised environment, not real-world speaker-attribution accuracy, psychometric validity or guaranteed host-model judgments. Review the underlying evidence before publishing a report. Plugin-directory validation may warn about the optional missing marketplace icon.
