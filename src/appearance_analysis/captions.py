from __future__ import annotations

import html
import math
import re
from pathlib import Path

from .store import InputError, load_json


_CLOCK = r"(?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3}"
_TIMING = re.compile(rf"^\s*({_CLOCK})\s+-->\s+({_CLOCK})(?:\s+.*)?$")
_TAG = re.compile(r"<([^>]*)>")
_LABEL = re.compile(r"^(?:(?:>>|[-–—])\s*)?([\w(][\w .’'()\-]{0,79}):[ \t]+(.+)$", re.DOTALL)
# Lowercased words that mark a sentence prefix ("The real question is: ...") rather than a name.
# Single-letter "A"/"I" stay allowed because "Q:"/"A:" are common transcript labels.
_NOT_NAME_WORDS = frozenset({
    "an", "the", "is", "are", "was", "were", "be", "been", "am", "this", "that", "these", "those",
    "my", "your", "our", "his", "her", "their", "its", "me", "we", "you", "they", "he", "she", "it",
    "here", "there", "what", "which", "who", "why", "how", "when", "where", "and", "or", "but", "so",
    "to", "of", "in", "on", "for", "with", "as", "at", "by", "from", "if", "then", "than", "not",
})
_ROLLING_SETTINGS = re.compile(r"\balign:start\b.*\bposition:0%")


def _clock(value: str) -> float:
    fields = value.replace(",", ".").split(":")
    try:
        seconds = float(fields[-1])
        minutes = int(fields[-2])
        stamp = seconds + minutes * 60 + (int(fields[0]) * 3600 if len(fields) == 3 else 0)
    except (ValueError, OverflowError) as exc:
        raise InputError("Caption timestamp is outside the supported numeric range.") from exc
    if seconds >= 60 or (len(fields) == 3 and minutes >= 60) or not math.isfinite(stamp):
        raise InputError("Caption timestamps must be finite with seconds and minutes below 60.")
    return stamp


def _interval(start: object, end: object, index: int) -> tuple[float, float]:
    if isinstance(start, bool) or isinstance(end, bool):
        raise InputError(f"Invalid caption interval at cue {index}.")
    try:
        first, last = float(start), float(end)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InputError(f"Invalid caption interval at cue {index}.") from exc
    if not math.isfinite(first) or not math.isfinite(last) or first < 0 or last <= first:
        raise InputError(f"Caption {index} needs finite times with 0 <= start < end.")
    return first, last


def _label(text: str) -> tuple[str, str] | None:
    """Split a leading `Name: words` speaker label; None when the prefix is ordinary prose."""
    match = _LABEL.match(text)
    if match is None:
        return None
    name = match[1].strip()
    tokens = name.split()
    if not 1 <= len(tokens) <= 4 or name.lower() in {"http", "https"}:
        return None
    for token in tokens:
        bare = token.strip("()")
        if not bare or bare.lower() in _NOT_NAME_WORDS or not (bare[0].isupper() or bare[0].isdigit()):
            return None
    return name, match[2].strip()


def _speaker(text: str, voice: str | None) -> tuple[str, str, bool]:
    if voice:
        return text, voice, True
    labelled = _label(text)
    if labelled:
        return labelled[1], labelled[0], True
    return text, "unassigned", False


def _payload(payload: str, start: float, end: float) -> list[dict]:
    lines = payload.splitlines()
    labelled = [index for index, line in enumerate(lines)
                if _label(html.unescape(_TAG.sub("", line)).strip())]
    if len(labelled) > 1 and not re.search(r"<v(?:[.\s])", payload):
        boundaries = sorted({0, *labelled, len(lines)})
        return [cue for first, last in zip(boundaries, boundaries[1:])
                for cue in _payload("\n".join(lines[first:last]), start, end)]
    groups: list[dict] = []
    voice: str | None = None
    pieces: list[str] = []
    anchors: list[tuple[int, float]] = []
    position = 0

    def flush() -> None:
        nonlocal pieces, anchors, position
        raw = "".join(pieces)
        text = " ".join(raw.split())
        if text:
            label_text, speaker, stable = _speaker(text, voice)
            if label_text:
                first_anchor_words = None
                if anchors:
                    first_anchor_words = len(raw[:anchors[0][0]].split())
                groups.append({
                    "start": start, "end": end, "text": label_text,
                    "speaker": speaker, "stable": stable, "channel": "captions",
                    "_anchors": anchors, "_leading_words": first_anchor_words,
                    "_leading_break": bool(anchors and "\n" in raw[:anchors[0][0]]),
                })
        pieces, anchors, position = [], [], 0

    previous = 0
    for tag in _TAG.finditer(payload):
        fragment = html.unescape(payload[previous:tag.start()])
        pieces.append(fragment)
        position += len(fragment)
        value = tag[1].strip()
        if re.fullmatch(_CLOCK, value):
            stamp = _clock(value)
            if start - 0.001 <= stamp <= end + 0.001:
                anchors.append((position, max(start, min(stamp, end))))
        elif value.startswith("v ") or value.startswith("v\t") or value.startswith("v."):
            flush()
            voice = html.unescape(re.sub(r"^v(?:\.[^\s]+)?\s+", "", value)).strip()
        elif value == "/v":
            flush()
            voice = None
        elif value.lower() in {"br", "br/", "br /"}:
            pieces.append("\n")
            position += 1
        previous = tag.end()
    pieces.append(html.unescape(payload[previous:]))
    flush()
    return groups


def _overlap_words(previous: list[str], current: list[str], maximum: int) -> int:
    for count in range(min(len(previous), len(current), maximum), 0, -1):
        if previous[-count:] == current[:count]:
            return count
    return 0


def _deduplicate(cues: list[dict]) -> list[dict]:
    output: list[dict] = []
    prior_by_speaker: dict[str, dict] = {}
    for index, cue in enumerate(cues):
        prior = prior_by_speaker.get(cue["speaker"])
        words = cue["text"].split()
        leading = cue["_leading_words"]
        if prior and cue["start"] <= prior["end"] + 0.1:
            previous_words = prior["text"].split()
            if prior["_anchors"] and leading is not None and cue["_leading_break"]:
                maximum = leading
            else:
                maximum = 0
            if (prior["_anchors"] and not cue["_anchors"] and words == previous_words and
                    cue["end"] - cue["start"] <= 0.1 and index + 1 < len(cues)):
                following = cues[index + 1]
                if (following["speaker"] == cue["speaker"] and following["_anchors"] and
                        following["_leading_break"] and following["start"] <= cue["end"] + 0.1 and
                        _overlap_words(previous_words, following["text"].split(),
                                       following["_leading_words"]) == len(previous_words)):
                    continue
            removed = _overlap_words(previous_words, words, maximum)
            if removed:
                words = words[removed:]
                if leading is not None and removed == leading and cue["_anchors"]:
                    cue = dict(cue, start=cue["_anchors"][0][1])
        prior_by_speaker[cue["speaker"]] = cue
        if words and cue["end"] > cue["start"]:
            output.append({key: value for key, value in dict(cue, text=" ".join(words)).items()
                           if not key.startswith("_")})
    return sorted(output, key=lambda cue: cue["start"])


def _rolling_new_lines(payload: list[str], carry: str | None) -> tuple[list[str], str | None]:
    """Drop the line a YouTube auto-caption block repeats from the previous block.

    Each rolling block shows the previous block's last line followed by the newly spoken line,
    and a 10 ms flash block shows only the repeated line. Returns the unseen raw lines and the
    block's last visible line, which the next block will repeat.
    """
    visible = [(line, " ".join(html.unescape(_TAG.sub("", line)).split())) for line in payload]
    visible = [(line, text) for line, text in visible if text]
    if not visible:
        return [], carry
    last = visible[-1][1]
    if carry is not None and visible[0][1] == carry:
        visible = visible[1:]
    return [line for line, _ in visible], last


def _parse_text(content: str, *, vtt: bool) -> list[dict]:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # YouTube auto-captions: word-timed `<c>` spans or `align:start position:0%` cue settings.
    rolling = vtt and ("<c>" in content or _ROLLING_SETTINGS.search(content) is not None)
    carry: str | None = None
    cues: list[dict] = []
    previous_start = -1.0
    index = 0
    while index < len(lines):
        line = lines[index]
        if vtt and (line == "NOTE" or line.startswith("NOTE ") or line in {"STYLE", "REGION"}):
            index += 1
            while index < len(lines) and lines[index] != "":
                index += 1
            continue
        timing = _TIMING.match(line)
        if timing is None:
            if "-->" in line:
                raise InputError("Malformed caption timestamp line.")
            index += 1
            continue
        start, end = _interval(_clock(timing[1]), _clock(timing[2]), len(cues) + 1)
        if start < previous_start:
            raise InputError("Caption cues must be ordered by start time.")
        previous_start = start
        index += 1
        payload: list[str] = []
        # A whitespace-only line can occur inside rolling YouTube captions.
        while index < len(lines) and lines[index] != "" and not _TIMING.match(lines[index]):
            if "-->" in lines[index]:
                raise InputError("Malformed caption timestamp line.")
            payload.append(lines[index])
            index += 1
        if rolling:
            payload, carry = _rolling_new_lines(payload, carry)
        cues.extend(_payload("\n".join(payload), start, end))
    if vtt and not rolling:
        return _deduplicate(cues)
    return [{key: value for key, value in cue.items() if not key.startswith("_")} for cue in cues]


def parse_file(path: Path) -> list[dict]:
    """Read timed source captions; speaker strings remain unverified source labels."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise InputError("Caption input must be an existing regular file.")
    extension = source.suffix.lower()
    if extension == ".json":
        payload = load_json(source)
        if not isinstance(payload, list):
            raise InputError("Timed JSON must be an array of {start, end, text, speaker?} records.")
        rows: list[dict] = []
        previous_start = -1.0
        for index, cue in enumerate(payload, 1):
            if not isinstance(cue, dict) or not {"start", "end", "text"} <= cue.keys():
                raise InputError(f"Timed JSON cue {index} requires start, end and text.")
            start, end = _interval(cue["start"], cue["end"], index)
            if start < previous_start:
                raise InputError("Caption cues must be ordered by start time.")
            previous_start = start
            text = cue["text"]
            if not isinstance(text, str) or not text.strip():
                raise InputError(f"Timed JSON cue {index} requires nonempty text.")
            voice = cue.get("speaker")
            if voice is not None and (not isinstance(voice, str) or not voice.strip()):
                raise InputError(f"Timed JSON cue {index} has an invalid speaker label.")
            # Timed JSON carries speakers in its own field; text is never split into a label.
            rows.append({"start": start, "end": end, "text": " ".join(text.split()),
                         "speaker": voice.strip() if voice else "unassigned",
                         "stable": bool(voice), "channel": "captions"})
    elif extension in {".vtt", ".srt"}:
        try:
            content = source.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise InputError("Cannot read captions as UTF-8; provide UTF-8 VTT, SRT or timed JSON.") from exc
        rows = _parse_text(content, vtt=extension == ".vtt")
    else:
        raise InputError("Unsupported transcript format; supply VTT, SRT or timed JSON, not untimed text.")
    if not rows:
        raise InputError("No usable timed cues found; supply timed captions or run audio transcription.")
    return rows
