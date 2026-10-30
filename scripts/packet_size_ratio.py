"""Report packet bytes relative to stored cue text bytes for one source.

Usage: uv run python scripts/packet_size_ratio.py PROJECT SOURCE_ID [--max-ratio R]

Run after `packets`. Exits 1 when --max-ratio is given and total packet bytes exceed it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from appearance_analysis.store import connect, source_dir


def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arguments.add_argument("project", type=Path)
    arguments.add_argument("source_id")
    arguments.add_argument("--max-ratio", type=float)
    args = arguments.parse_args()
    db = connect(args.project)
    try:
        text_bytes = sum(len(row[0].encode()) for row in db.execute("SELECT text FROM cues WHERE source_id=?", (args.source_id,)))
    finally:
        db.close()
    packet_bytes = sum(path.stat().st_size for path in (source_dir(args.project, args.source_id) / "packets").glob("*.json"))
    ratio = packet_bytes / text_bytes if text_bytes else float("inf")
    print(f"packet_bytes={packet_bytes} cue_text_bytes={text_bytes} ratio={ratio:.3f}")
    return 1 if args.max_ratio is not None and ratio > args.max_ratio else 0


if __name__ == "__main__":
    sys.exit(main())
