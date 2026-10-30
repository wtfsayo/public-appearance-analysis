from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__
from . import analysis
from .reporting import report
from .store import InputError, connect, load_json


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="appearance", description="Evidence-linked analysis of public communication. Semantic interpretation is performed by the Claude Code plugin, not fabricated by this CLI.")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--project", type=Path, default=Path("appearance-analysis"), help="Analysis project directory, separate from the plugin installation")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create an evidence project and the default six-measure rubric")
    commands.add_parser("doctor", help="Inspect tools without downloading models or creating a project")
    discovery = commands.add_parser("discover", help="Find and verify recent long-form YouTube candidates")
    discovery.add_argument("query")
    discovery.add_argument("--limit", type=int, default=10)
    discovery.add_argument("--min-duration", type=float, default=1200)
    discovery.add_argument("--after")
    ingest = commands.add_parser("ingest", help="Acquire public URL or local media and original timed captions")
    ingest.add_argument("source")
    ingest.add_argument("--subject")
    ingest.add_argument("--captions", type=Path)
    ingest.add_argument("--title")
    ingest.add_argument("--rights", choices=["third-party", "user-provided", "licensed", "public-domain"], default="third-party")
    ingest.add_argument("--no-audio", action="store_true")
    transcript = commands.add_parser("import-transcript", help="Import supplied VTT, SRT or timed JSON, without inventing timestamps")
    transcript.add_argument("file", type=Path)
    transcript.add_argument("--subject", required=True)
    transcript.add_argument("--title")
    transcript.add_argument("--source-url")
    transcript.add_argument("--rights", choices=["third-party", "user-provided", "licensed", "public-domain"], default="user-provided")
    models = commands.add_parser("setup-models", help="Download checksum-pinned public speech models")
    models.add_argument("--models-dir", type=Path)
    diarize = commands.add_parser("diarize", help="Run local voice segmentation, refinement and independent checks")
    diarize.add_argument("source_id")
    diarize.add_argument("--speakers", type=int)
    diarize.add_argument("--distance", type=float, default=0.6)
    diarize.add_argument("--models-dir", type=Path)
    diarize.add_argument("--threads", type=int, default=2)
    asr = commands.add_parser("transcribe", help="Run optional local source-language ASR; never silently replace existing captions")
    asr.add_argument("source_id")
    asr.add_argument("--model", default="small")
    asr.add_argument("--language")
    mapping = commands.add_parser("map-speakers", help="Attach names using spoken, publisher or explicit supplied evidence")
    mapping.add_argument("source_id")
    mapping.add_argument("--input", type=Path, required=True)
    packet = commands.add_parser("packets", help="Freeze source cues into bounded complete-review packets")
    packet.add_argument("source_id")
    packet.add_argument("--size", type=int, default=80)
    packet.add_argument("--context", type=int, default=3)
    review = commands.add_parser("review-batch", help="Import exact cue coverage and original analytical observations")
    review.add_argument("--input", type=Path, required=True)
    revision = commands.add_parser("revise-observation", help="Correct an observation and invalidate dependent narrative/ratings")
    revision.add_argument("observation_id")
    revision.add_argument("--input", type=Path, required=True)
    observations = commands.add_parser("observations", help="List stored analytical evidence, not full source transcripts")
    observations.add_argument("--subject")
    observations.add_argument("--source")
    for command, help_text in [("import-profile", "Import an original evidence-linked person profile"), ("import-scores", "Import subjective ratings, with null for missing evidence"), ("import-comparison", "Import a role-aware comparison across subjects")]:
        item = commands.add_parser(command, help=help_text)
        item.add_argument("file", type=Path)
    weights = commands.add_parser("weights", help="Change finite nonnegative rubric weights")
    weights.add_argument("--input", type=Path, required=True)
    scores = commands.add_parser("scorecard", help="Compute rankings; missing active evidence prevents an overall score")
    scores.add_argument("--scope", default="all")
    sensitivity = commands.add_parser("sensitivity", help="Recompute a changed weight without overwriting the baseline")
    sensitivity.add_argument("--measure", choices=list(analysis.RUBRIC), default="engagement")
    sensitivity.add_argument("--factor", type=float, default=2)
    sensitivity.add_argument("--scope", default="all")
    commands.add_parser("audit", help="Inspect complete-source coverage, identity limits and data integrity")
    rendering = commands.add_parser("report", help="Publish original analysis, not full third-party transcripts")
    rendering.add_argument("--output", type=Path)
    commands.add_parser("status", help="Show sources, subjects and review coverage")
    schemas = commands.add_parser("schema", help="Show the authoritative model-input JSON schema")
    schemas.add_argument("kind", choices=list(analysis.SCHEMAS))
    return root


def execute(args) -> dict:
    command = args.command
    if command == "doctor":
        from .media import doctor
        return doctor()
    if command == "schema":
        return analysis.SCHEMAS[args.kind]
    if command == "setup-models":
        from .models import setup_models
        return setup_models(args.models_dir)
    db = connect(args.project)
    try:
        if command == "init":
            return {"project": str(args.project.expanduser().resolve()), "database": "analysis.sqlite", "rubric": [dict(row) for row in db.execute("SELECT * FROM rubric")]}
        if command == "discover":
            from .media import discover
            return discover(db, args.query, limit=args.limit, min_duration=args.min_duration, after=args.after)
        if command == "ingest":
            from .media import ingest
            return ingest(db, args.project, args.source, subject=args.subject, captions=args.captions, title=args.title, rights=args.rights, no_audio=args.no_audio)
        if command == "import-transcript":
            from .media import import_transcript
            return import_transcript(db, args.project, args.file, subject=args.subject, title=args.title, source_url=args.source_url, rights=args.rights)
        if command == "diarize":
            from .audio import diarize
            return diarize(db, args.project, args.source_id, speakers=args.speakers, distance=args.distance, models_dir=args.models_dir, threads=args.threads)
        if command == "transcribe":
            from .audio import transcribe
            return transcribe(db, args.project, args.source_id, model=args.model, language=args.language)
        if command == "map-speakers":
            return analysis.map_speakers(db, args.source_id, load_json(args.input))
        if command == "packets":
            return analysis.packets(db, args.project, args.source_id, size=args.size, context=args.context)
        if command == "review-batch":
            return analysis.review_batch(db, load_json(args.input))
        if command == "revise-observation":
            return analysis.revise_observation(db, args.observation_id, load_json(args.input))
        if command == "observations":
            conditions, values = [], []
            if args.subject:
                conditions.append("subject=?")
                values.append(args.subject)
            if args.source:
                conditions.append("source_id=?")
                values.append(args.source)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            result = []
            for row in db.execute("SELECT * FROM observations" + where + " ORDER BY source_id,id", values):
                value = dict(row)
                for key in ("question_ids", "response_ids", "evidence_ids", "alternatives"):
                    value[key] = json.loads(value[key])
                result.append(value)
            return {"observations": result}
        if command == "import-profile":
            return analysis.import_profile(db, load_json(args.file))
        if command == "import-scores":
            return analysis.import_scores(db, load_json(args.file))
        if command == "import-comparison":
            return analysis.import_comparison(db, load_json(args.file))
        if command == "weights":
            return analysis.set_weights(db, load_json(args.input))
        if command == "scorecard":
            return analysis.scorecard(db, scope=args.scope)
        if command == "sensitivity":
            return analysis.sensitivity(db, measure=args.measure, factor=args.factor, scope=args.scope)
        if command == "audit":
            return analysis.audit(db)
        if command == "report":
            return report(db, args.project, output=args.output)
        if command == "status":
            return {"subjects": [row[0] for row in db.execute("SELECT name FROM subjects ORDER BY name")],
                    "sources": [dict(row) for row in db.execute("SELECT id,title,publisher,upload_date,duration,language,rights FROM sources ORDER BY imported_at,id")],
                    "review": analysis.audit(db)}
        raise InputError("Unknown command.")
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        value = execute(arguments)
        print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (InputError, OSError, sqlite3.Error) as exc:
        print(f"appearance: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
