# Setup and prerequisites

## Dependencies by task

| Task | Requirements |
| --- | --- |
| Import timed transcripts, review evidence, score and report | `uv`, Python 3.11+ and the core package (`jsonschema`) |
| Discover or download supported public URLs | `yt-dlp` executable |
| Probe/decode local or downloaded audio | `ffmpeg` and `ffprobe` executables |
| Diarize anonymous voices | `media` extra (NumPy, SoundFile, scikit-learn, sherpa-onnx) plus models from `setup-models` |
| Transcribe source-language speech | `asr` extra (faster-whisper) plus the requested ASR model |

On macOS with Homebrew: `brew install ffmpeg yt-dlp`. On Debian/Ubuntu, install `ffmpeg` with the system package manager and a current `yt-dlp` with `uv tool install yt-dlp`. Other platforms follow the tools' official instructions. The executables must be on `PATH`. Current YouTube extraction may also need yt-dlp-ejs and a JavaScript runtime (deno, node or bun); `doctor` lists them.

The wrapper installs the `media` extra on the first `diarize` and the `asr` extra on the first `transcribe`; `doctor` reports them as "installed on first diarize/transcribe" until then. The extras are large. On Windows, run the wrapper from WSL or Git Bash.

## Numeric scores need speaker identity

Caption-only work (`ingest --no-audio`, or transcripts without speaker labels) yields `unassigned` cues, so every rating for those sources is null. Numeric scores need diarization plus a supported speaker map before packets ([json-contract](json-contract.md#ratings-schema-scores)). Tell the user this when they choose caption-only setup.

## Models

`setup-models` downloads SHA256-pinned public diarization assets from the [sherpa-onnx project](https://github.com/k2-fsa/sherpa-onnx) into the standard user cache, or into `--models-dir PATH`. Pins verify expected content, not upstream signatures. Origin and license information is kept in the model manifest; third-party model licenses apply separately from this project's MIT license. Diarization models are not an ASR prerequisite.

`transcribe` uses faster-whisper in the source language and downloads the requested model (`--model small` by default) on first use; `--model PATH` selects a local model directory instead.
