"""Check rolling auto-caption de-duplication against a last-line reference.

Usage: uv run python scripts/check_caption_dedupe.py TRACK.vtt [--max-ratio 1.1]

The reference keeps the last visible line of each caption block and drops a line equal to the
previous kept line. Exits 1 when parsed cue words exceed the reference by more than the ratio or
any parsed cue lasts 20 ms or less (a surviving flash block).
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

from appearance_analysis.captions import parse_file

_TAG = re.compile(r"<[^>]*>")


def reference_words(path: Path) -> int:
    words, previous = 0, None
    for block in path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n\n"):
        lines = block.split("\n")
        if not any("-->" in line for line in lines):
            continue
        payload = lines[[index for index, line in enumerate(lines) if "-->" in line][0] + 1:]
        visible = [" ".join(html.unescape(_TAG.sub("", line)).split()) for line in payload]
        visible = [line for line in visible if line]
        if visible and visible[-1] != previous:
            words += len(visible[-1].split())
            previous = visible[-1]
    return words


def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arguments.add_argument("track", type=Path)
    arguments.add_argument("--max-ratio", type=float, default=1.1)
    args = arguments.parse_args()
    cues = parse_file(args.track)
    parsed = sum(len(cue["text"].split()) for cue in cues)
    reference = reference_words(args.track)
    flashes = sum(cue["end"] - cue["start"] <= 0.02 for cue in cues)
    ratio = parsed / reference if reference else float("inf")
    print(f"cues={len(cues)} parsed_words={parsed} reference_words={reference} ratio={ratio:.3f} flash_cues={flashes}")
    return 0 if ratio <= args.max_ratio and not flashes else 1


if __name__ == "__main__":
    sys.exit(main())
