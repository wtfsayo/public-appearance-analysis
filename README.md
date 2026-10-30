# Public Appearance

A Claude Code plugin for analyzing observable communication in complete public appearances. It works with interviews, lectures, panels, demonstrations, hearings and other public formats. It is not tied to a profession, political domain or particular person.

Claude reads the actual source packets and makes semantic judgments. Python retrieves and prepares sources, stores evidence in SQLite, validates citations, computes weighted scores and renders the report. There is no external LLM API, keyword-based substitute for analysis, or CLI command that secretly invents profiles.

## Load the plugin

Install [Claude Code](https://code.claude.com/docs/en/setup) and [uv](https://docs.astral.sh/uv/getting-started/installation/); `uv` provisions Python 3.11 or newer if needed. Then add the GitHub marketplace and install the plugin:

```sh
claude plugin marketplace add wtfsayo/public-appearance-analysis
claude plugin install public-appearance@public-appearance-local --scope user
```

Use `--scope project` for a shared project installation or `--scope local` for this workspace only. From a downloaded copy, `claude plugin marketplace add "$PWD"` registers the same marketplace locally, and `claude --plugin-dir "$PWD"` loads the plugin for one session. Restart Claude Code after installation if needed. This package does not edit settings or install itself. It has no hooks, daemons, MCP wrappers or external LLM credentials.

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

[references/setup.md](references/setup.md) lists the prerequisites per task (yt-dlp, FFmpeg, the `media` and `asr` extras, models) with install commands. `/public-appearance:setup` reads it and checks your machine with `doctor`.

The POSIX `bin/appearance` wrapper invokes `uv` using its own plugin root, regardless of your current working directory, and preserves all arguments. Only `diarize` selects the `media` extra and only `transcribe` selects the `asr` extra; `uv` installs them on first use. The Python environment lives outside the plugin directory (`$APPEARANCE_VENV`, else `$CLAUDE_PLUGIN_DATA/venv`, else `~/.local/share/public-appearance/venv`), so plugin updates reuse it. On Windows, use WSL/Git Bash for the wrapper or the direct `uv run` commands below.

## Start a separate analysis project

Set `PLUGIN_ROOT` to the downloaded plugin directory and `PROJECT` to a user-owned directory outside it. The following commands work from a terminal; `${CLAUDE_PLUGIN_ROOT}` in the skills is a Claude Code substitution, not a shell variable you must set globally. `init` creates the project; every other command refuses a directory without one, and the wrapper refuses projects inside the plugin directory.

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

Without a user-specified period, the host favors recent full appearances. Results use actual upload dates and keep missing dates distinct. Date ordering applies only within metadata-enriched relevance-search candidates, not all appearances on the platform. YouTube search is not an exhaustive chronology. The host must inspect completeness, original publisher and context; a "LIVE" title does not prove a loop is a full interview. Only selected sources become analyzed sources. `discover`, full `ingest`, `transcribe`, `diarize` and `setup-models` can take many minutes on long sources; Claude runs them in the background.

### Optional audio work

After ingest, set `SOURCE_ID` to the actual ID returned by the CLI. Finish audio/caption preparation before generating review packets.

```sh
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" setup-models
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" transcribe "$SOURCE_ID" --model small
"$PLUGIN_ROOT/bin/appearance" --project "$PROJECT" diarize "$SOURCE_ID"
```

Diarization uses real speech segmentation, ERes2Net embeddings and clustering, with independent TitaNet sample/pair diagnostics where there are enough voices. Anonymous labels and embedding-consistency diagnostics do not establish identity or accuracy. Supply `--speakers N` only when introductions or other source evidence justify the count. Keep uncertain cue labels uncertain.

Model downloads, origins and licenses are described in [references/setup.md](references/setup.md#models). `--language CODE` supplies a known source language for transcription. When captions already exist, ASR remains a cross-check rather than replacing them. Caption language metadata can be wrong, especially for transliteration and code-switching. Retain disagreements rather than silently polishing uncertain text.

If ASR is needed, transcribe before diarization so anonymous voice assignments attach to its new cues. Then map names and generate packets; the first `packets` call freezes the source's evidence ([evidence freeze](references/cli.md#evidence-freeze)). Numeric scores need a supported speaker mapping, so caption-only sources without speaker labels can only receive null ratings ([ratings](references/json-contract.md#ratings-schema-scores)).

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
2. Generate `packets SOURCE_ID --size 80 --context 3`. Read each returned packet file in order, or have `source-analyst` read it. Lower the size for dense sources. Review every target cue, preserving exact IDs and all question/answer context. Keep a question ledger and scan later packets for delayed replies before calling an exchange a non-answer.
3. Write finalized review JSON for every batch, including batches with no observations. Use `schema review`, then `review-batch --input FILE`; keep the actual returned observation IDs. To correct an imported judgment later, use `revise-observation` ([observation correction](references/json-contract.md#observation-correction-schema-observation)).
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

The [rubric](references/rubric.md) anchors clarity, structure, precision, responsiveness, engagement and adaptability on integer 0-10 scales, and defines null missingness, weights and sensitivity. Each non-null rating cites an observation with a supported speaker identity and explains the role/context. `null` means unassessable, not poor. Sensitivity recomputes a specified changed weight; it is not a confidence interval, significance test or psychometric validation.

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

Acceptance checks for long real sources (run from the repository with `uv run`):

```sh
# Rolling YouTube auto-captions: parsed words within 1.1x of a last-line reference, no 10 ms flash cues.
uv run python scripts/check_caption_dedupe.py TRACK.en-orig.vtt
# Packet bytes relative to stored cue text for one source, after `packets`.
uv run python scripts/packet_size_ratio.py "$PROJECT" "$SOURCE_ID"
```

Tests and these checks establish execution and evidence bookkeeping, not real-world speaker-attribution accuracy, psychometric validity or guaranteed host-model judgments. Review the underlying evidence before publishing a report. Plugin-directory validation may warn about the optional missing marketplace icon.
