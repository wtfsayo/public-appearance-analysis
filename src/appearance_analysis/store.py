from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable


class InputError(ValueError):
    pass


RUBRIC = {
    "clarity": "How readily the point can be understood; not accent or English proficiency.",
    "structure": "Organization of claims, reasons, examples and qualifications.",
    "precision": "Specificity, distinctions and appropriately bounded claims; not presumed factual correctness.",
    "responsiveness": "Uptake of the actual question, including explicit and relevant limits.",
    "engagement": "Accessible examples, narrative, humor and rapport; not measured persuasion.",
    "adaptability": "Repair, qualification and uptake of follow-ups; not inferred vocal calmness.",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS subjects(name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sources(
 id TEXT PRIMARY KEY,locator TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,
 title TEXT NOT NULL,publisher TEXT,upload_date TEXT,duration REAL,language TEXT,
 rights TEXT NOT NULL DEFAULT 'third-party',subject TEXT REFERENCES subjects(name),
 metadata TEXT NOT NULL DEFAULT '{}',audio_path TEXT,wav_path TEXT,captions_path TEXT,
 track_language TEXT,imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS source_subjects(
 source_id TEXT NOT NULL REFERENCES sources(id),subject TEXT NOT NULL REFERENCES subjects(name),
 PRIMARY KEY(source_id,subject)
);
CREATE TABLE IF NOT EXISTS candidates(
 locator TEXT PRIMARY KEY,title TEXT NOT NULL,publisher TEXT,upload_date TEXT,duration REAL,metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cues(
 id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),cue_index INTEGER NOT NULL,
 start REAL NOT NULL,end REAL NOT NULL,text TEXT NOT NULL,speaker TEXT NOT NULL DEFAULT 'unassigned',
 stable INTEGER NOT NULL DEFAULT 0,channel TEXT NOT NULL DEFAULT 'captions',UNIQUE(source_id,cue_index),
 CHECK(start>=0 AND end>start),CHECK(length(trim(text))>0)
);
CREATE TABLE IF NOT EXISTS speakers(
 source_id TEXT NOT NULL REFERENCES sources(id),speaker TEXT NOT NULL,name TEXT NOT NULL,
 role TEXT NOT NULL,evidence TEXT NOT NULL,status TEXT NOT NULL,
 PRIMARY KEY(source_id,speaker)
);
CREATE TABLE IF NOT EXISTS segments(
 source_id TEXT NOT NULL REFERENCES sources(id),start REAL NOT NULL,end REAL NOT NULL,speaker TEXT NOT NULL,
 score REAL,margin REAL,PRIMARY KEY(source_id,start,end,speaker)
);
CREATE TABLE IF NOT EXISTS audio_runs(
 source_id TEXT PRIMARY KEY REFERENCES sources(id),config TEXT NOT NULL,diagnostics TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_samples(
 id INTEGER PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),speaker TEXT NOT NULL,
 start REAL NOT NULL,end REAL NOT NULL,model TEXT NOT NULL,embedding BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_pairs(
 source_id TEXT NOT NULL REFERENCES sources(id),sample_a INTEGER NOT NULL,sample_b INTEGER NOT NULL,
 same_speaker INTEGER NOT NULL,cosine REAL NOT NULL,PRIMARY KEY(source_id,sample_a,sample_b)
);
CREATE TABLE IF NOT EXISTS asr_segments(
 source_id TEXT NOT NULL REFERENCES sources(id),segment_index INTEGER NOT NULL,
 start REAL NOT NULL,end REAL NOT NULL,text TEXT NOT NULL,language TEXT,
 PRIMARY KEY(source_id,segment_index)
);
CREATE TABLE IF NOT EXISTS measurements(
 source_id TEXT NOT NULL REFERENCES sources(id),name TEXT NOT NULL,value REAL NOT NULL,unit TEXT NOT NULL,note TEXT NOT NULL,
 PRIMARY KEY(source_id,name)
);
CREATE TABLE IF NOT EXISTS batches(
 id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),target_ids TEXT NOT NULL,
 packet_hash TEXT NOT NULL,reviewed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cue_reviews(
 cue_id INTEGER PRIMARY KEY REFERENCES cues(id),batch_id TEXT NOT NULL REFERENCES batches(id)
);
CREATE TABLE IF NOT EXISTS observations(
 id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),subject TEXT NOT NULL REFERENCES subjects(name),
 kind TEXT NOT NULL,claim TEXT NOT NULL,question_ids TEXT NOT NULL,response_ids TEXT NOT NULL,evidence_ids TEXT NOT NULL,
 alternatives TEXT NOT NULL,unresolved TEXT NOT NULL,inference INTEGER NOT NULL,attribution TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles(
 subject TEXT NOT NULL REFERENCES subjects(name),scope TEXT NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(subject,scope)
);
CREATE TABLE IF NOT EXISTS rubric(
 measure TEXT PRIMARY KEY,definition TEXT NOT NULL,weight REAL NOT NULL CHECK(weight>=0)
);
CREATE TABLE IF NOT EXISTS ratings(
 subject TEXT NOT NULL REFERENCES subjects(name),scope TEXT NOT NULL,measure TEXT NOT NULL REFERENCES rubric(measure),
 score INTEGER,rationale TEXT NOT NULL,evidence_ids TEXT NOT NULL,
 PRIMARY KEY(subject,scope,measure),CHECK(score IS NULL OR (score>=0 AND score<=10))
);
CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rendered_reports(
 path TEXT PRIMARY KEY,content_sha256 TEXT NOT NULL,stale INTEGER NOT NULL DEFAULT 0
);
"""


def connect(project: str | Path, *, create: bool = False) -> sqlite3.Connection:
    """Open a project database; only `create=True` (the `init` command) may create a new project."""
    root = Path(project).expanduser().resolve()
    if any((root / name).is_symlink() for name in ("internal", "reports", "analysis.sqlite")):
        raise InputError("Project data directories and database must not be symbolic links.")
    if not create and not (root / "analysis.sqlite").is_file():
        raise InputError(f"no project at {root}; run init first")
    root.mkdir(parents=True, exist_ok=True)
    (root / "internal").mkdir(exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    db = sqlite3.connect(root / "analysis.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if version and version[0] != "1":
        db.close()
        raise InputError("Unsupported project schema version; use a matching plugin release.")
    db.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")
    db.executemany("INSERT OR IGNORE INTO rubric VALUES(?,?,1)", RUBRIC.items())
    db.commit()
    return db


def source_id(locator: str) -> str:
    return "s_" + hashlib.sha256(locator.encode()).hexdigest()[:16]


def source_dir(project: str | Path, identifier: str) -> Path:
    if not identifier.startswith("s_") or len(identifier) != 18 or any(c not in "0123456789abcdef" for c in identifier[2:]):
        raise InputError("Invalid source identifier.")
    root = Path(project).expanduser().resolve()
    internal = root / "internal"
    if internal.is_symlink():
        raise InputError("The project's internal directory must not be a symbolic link.")
    folder = internal / identifier
    if folder.is_symlink():
        raise InputError("A source directory must not be a symbolic link.")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get_source(db: sqlite3.Connection, identifier: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM sources WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise InputError(f"Unknown source: {identifier}")
    return row


def add_source(db: sqlite3.Connection, *, locator: str, kind: str, title: str,
               publisher: str | None = None, upload_date: str | None = None,
               duration: float | None = None, language: str | None = None,
               rights: str = "third-party", subject: str | None = None,
               metadata: dict | None = None) -> str:
    if rights not in {"third-party", "user-provided", "licensed", "public-domain"}:
        raise InputError("Unknown source rights classification.")
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise InputError("Source duration must be finite and positive.")
    identifier = source_id(locator)
    if subject:
        db.execute("INSERT OR IGNORE INTO subjects VALUES(?)", (subject,))
    db.execute("""INSERT INTO sources(id,locator,kind,title,publisher,upload_date,duration,language,rights,subject,metadata)
      VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      title=excluded.title,publisher=excluded.publisher,upload_date=excluded.upload_date,
      duration=COALESCE(excluded.duration,sources.duration),language=excluded.language,
      subject=COALESCE(sources.subject,excluded.subject),metadata=excluded.metadata""",
      (identifier, locator, kind, title, publisher, upload_date, duration, language, rights, subject,
       json.dumps(metadata or {}, ensure_ascii=False, allow_nan=False)))
    if subject:
        db.execute("INSERT OR IGNORE INTO source_subjects VALUES(?,?)", (identifier, subject))
    return identifier


def replace_cues(db: sqlite3.Connection, identifier: str, rows: Iterable[dict]) -> int:
    get_source(db, identifier)
    if db.execute("SELECT 1 FROM batches WHERE source_id=? LIMIT 1", (identifier,)).fetchone():
        raise InputError("Source cues are frozen after packets are generated. Import changed captions into a new project.")
    validated = []
    previous = -1.0
    for index, row in enumerate(rows, 1):
        start, end = float(row["start"]), float(row["end"])
        text = str(row["text"]).strip()
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or start < previous or not text:
            raise InputError(f"Invalid or unordered caption interval at cue {index}.")
        previous = start
        validated.append((identifier, index, start, end, text, row.get("speaker", "unassigned"),
                          int(bool(row.get("stable", False))), row.get("channel", "captions")))
    if not validated:
        raise InputError("No usable timed cues found. Supply SRT, VTT, timed JSON, or run audio transcription.")
    db.execute("DELETE FROM speakers WHERE source_id=?", (identifier,))
    db.execute("DELETE FROM cues WHERE source_id=?", (identifier,))
    db.executemany("INSERT INTO cues(source_id,cue_index,start,end,text,speaker,stable,channel) VALUES(?,?,?,?,?,?,?,?)", validated)
    return len(validated)


def measurement(db: sqlite3.Connection, identifier: str, name: str, value: float, unit: str, note: str) -> None:
    if not math.isfinite(value):
        raise InputError("A measurement must be finite.")
    db.execute("INSERT OR REPLACE INTO measurements VALUES(?,?,?,?,?)", (identifier, name, value, unit, note))


def load_json(path: str | Path) -> Any:
    def invalid_constant(value: str) -> None:
        raise InputError(f"Non-finite JSON number: {value}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid_constant)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"Cannot read JSON input: {exc}") from exc


def atomic_text(path: str | Path, content: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise InputError("Refusing to overwrite a symbolic link.")
    temporary = target.with_name(target.name + ".tmp")
    if temporary.exists():
        raise InputError(f"Temporary output already exists: {temporary}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
