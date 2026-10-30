from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .analysis import audit, scorecard
from .store import InputError, atomic_text, get_source


def escape(value: object) -> str:
    text = str(value).replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", text)


def clock(value: float) -> str:
    seconds = max(0, int(value))
    return f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"


def citation(db, observation) -> str:
    identifiers = json.loads(observation["evidence_ids"])
    rows = [db.execute("SELECT start,end FROM cues WHERE id=?", (identifier,)).fetchone() for identifier in identifiers]
    start, end = min(row[0] for row in rows), max(row[1] for row in rows)
    source = get_source(db, observation["source_id"])
    label = f"{escape(source['title'])}, {clock(start)}–{clock(end)}"
    parsed = urlsplit(source["locator"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return f"{label} (local/supplied source {escape(source['id'])})"
    if parsed.hostname.lower() in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in {"t", "start"}]
        query.append(("t", str(int(start))))
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    else:
        url = source["locator"]
    url = url.replace("(", "%28").replace(")", "%29")
    return f"[{label}]({url})"


def _observations(db, identifiers: list[str]) -> list:
    return [db.execute("SELECT * FROM observations WHERE id=?", (identifier,)).fetchone() for identifier in dict.fromkeys(identifiers)]


def _profile_ids(payload: dict) -> list[str]:
    return [identifier for key in ("strengths", "vulnerabilities", "counterexamples") for point in payload[key] for identifier in point["observation_ids"]]


def _tokens(value: str) -> list[str]:
    return re.findall(r"\w+(?:['’]\w+)?", value.casefold())


def _check_reproduction(db, texts: dict[str, list[str]], rendered: str) -> None:
    tokens = _tokens(rendered)
    for identifier, pieces in texts.items():
        source = get_source(db, identifier)
        if source["rights"] in {"licensed", "public-domain"}:
            continue
        quoted = sum(len(quote.split()) for text in pieces for quote in re.findall(r'["“]([^"”\n]+)["”]', text))
        if quoted > 90:
            raise InputError(f"Third-party quotation budget exceeded for {identifier}. Use original paraphrases.")
        original = _tokens(" ".join(row[0] for row in db.execute("SELECT text FROM cues WHERE source_id=? ORDER BY cue_index", (identifier,))))
        ngrams = {tuple(original[index:index + 8]) for index in range(max(0, len(original) - 7))}
        covered = set()
        for index in range(max(0, len(tokens) - 7)):
            if tuple(tokens[index:index + 8]) in ngrams:
                covered.update(range(index, index + 8))
        if len(covered) > 90:
            raise InputError(f"Report extensively copies third-party source {identifier}; rewrite as original analysis.")


def report(db, project, *, output: Path | None = None) -> dict:
    profiles = [json.loads(row[0]) for row in db.execute("SELECT payload FROM profiles ORDER BY subject,scope")]
    comparisons = [json.loads(row[0]) for row in db.execute("SELECT payload FROM comparisons ORDER BY id")]
    if not profiles:
        raise InputError("No evidence-linked profiles have been imported.")
    required_sources = set()
    public_text = defaultdict(list)
    accounted_observations = set()
    for payload in profiles:
        observations = _observations(db, _profile_ids(payload))
        sources = {row["source_id"] for row in observations}
        required_sources.update(sources)
        for source in sources:
            public_text[source].extend([payload["summary"], *payload["limits"]])
        for key in ("strengths", "vulnerabilities", "counterexamples"):
            for point in payload[key]:
                for source in {row["source_id"] for row in _observations(db, point["observation_ids"])}:
                    public_text[source].append(point["text"])
        for observation in observations:
            if observation["id"] in accounted_observations:
                continue
            accounted_observations.add(observation["id"])
            public_text[observation["source_id"]].extend([observation["claim"], observation["unresolved"], *json.loads(observation["alternatives"])])
    for payload in comparisons:
        sources = {row["source_id"] for point in payload["points"] for row in _observations(db, point["observation_ids"])}
        required_sources.update(sources)
        for source in sources:
            public_text[source].extend([payload["summary"], *payload["limits"]])
        for point in payload["points"]:
            for source in {row["source_id"] for row in _observations(db, point["observation_ids"])}:
                public_text[source].extend([point["text"], *point["alternatives"]])
    for rating in db.execute("SELECT * FROM ratings"):
        observations = _observations(db, json.loads(rating["evidence_ids"]))
        for source in {row["source_id"] for row in observations}:
            required_sources.add(source)
            public_text[source].append(rating["rationale"])
    for source in required_sources:
        metadata = get_source(db, source)
        public_text[source].extend([metadata["title"], metadata["publisher"] or ""])
        for speaker in db.execute("SELECT name,role,evidence FROM speakers WHERE source_id=?", (source,)):
            public_text[source].extend([speaker["name"], speaker["role"], json.loads(speaker["evidence"])["note"]])
    checks = audit(db)
    incomplete = [row for row in checks["coverage"] if row["source_id"] in required_sources and not row["complete_source_review"]]
    if incomplete:
        raise InputError("Complete-source review is required before publication: " + ", ".join(row["source_id"] for row in incomplete))
    lines = ["# Public-appearance analysis", "",
             "Original evidence-linked analysis of the selected appearances. Interpretations concern communication in these settings, not IQ, diagnosis, hidden motives, personality essence or a finding of deception.", "",
             "The source records are automatic or supplied transcripts, with optional local audio processing. Complete-source review is not a guarantee that every spoken word was recognized correctly. No human listening audit is implied.", "",
             "## Source scope", "", "| Source | Publisher | Upload date | Duration | Source/model limitations |", "|---|---|---|---|---|"]
    for identifier in sorted(required_sources):
        source = get_source(db, identifier)
        duration = clock(source["duration"]) if source["duration"] else "Unknown"
        run = db.execute("SELECT diagnostics FROM audio_runs WHERE source_id=?", (identifier,)).fetchone()
        diagnostics = json.loads(run[0]) if run else {}
        diarized = "diarization" in diagnostics
        asr_cues = db.execute("SELECT 1 FROM cues WHERE source_id=? AND channel='asr' LIMIT 1", (identifier,)).fetchone()
        basis = "ASR-derived transcript" if asr_cues else "Source/supplied captions"
        limitations = f"{basis}; model diarization recorded; short/overlapping turns remain approximate" if diarized else f"{basis}; no model diarization recorded"
        lines.append(f"| {escape(source['title'])} | {escape(source['publisher'] or 'Not established')} | {escape(source['upload_date'] or 'Unknown; not evidence of recency')} | {duration} | {limitations} |")
    unused = [row for row in checks["coverage"] if row["source_id"] not in required_sources]
    if unused:
        lines.extend(["", "Other ingested sources are not used in these conclusions: " + ", ".join(escape(row["source_id"]) for row in unused) + ". Their review may be unfinished."])
    lines.extend(["", "## Attribution and recording diagnostics", ""])
    for identifier in sorted(required_sources):
        source = get_source(db, identifier)
        lines.extend([f"### {escape(source['title'])}", ""])
        mappings = db.execute("SELECT * FROM speakers WHERE source_id=? ORDER BY speaker", (identifier,)).fetchall()
        if not mappings:
            lines.append("No evidence-supported names have been mapped. Named conclusions require caution.")
        for mapping in mappings:
            evidence = json.loads(mapping["evidence"])
            lines.append(f"- {escape(mapping['speaker'])}: {escape(mapping['name'])}; {escape(mapping['role'])}; attribution {escape(mapping['status'])}. Evidence: {escape(evidence['note'])}")
        for metric in db.execute("SELECT name,value,unit,note FROM measurements WHERE source_id=? ORDER BY name", (identifier,)):
            lines.append(f"- Recording/model diagnostic {escape(metric['name'])}: {metric['value']:.4g} {escape(metric['unit'])}. {escape(metric['note'])}")
        lines.append("")
    for scope in [row[0] for row in db.execute("SELECT DISTINCT scope FROM ratings ORDER BY scope")]:
        card = scorecard(db, scope=scope)
        lines.extend([f"## Quantitative judgments — {escape(scope)}", "",
                      "These are subjective anchored 0–10 judgments, not measured intelligence, truthfulness, vocal skill or persuasion. Use the measure-specific behavioral anchors: 0–2 low, 4–6 mixed, 8–10 strong; 3 and 7 fall between bands. Small differences are not established measurement separation. N/A means insufficient evidence, not zero.", "",
                      "| Rank | Subject | Clarity | Structure | Precision | Responsiveness | Engagement tools | Adaptability | Overall |", "|---|---|---|---|---|---|---|---|---|"])
        for row in card["rows"]:
            scores = [str(row["scores"].get(measure)) if row["scores"].get(measure) is not None else "N/A" for measure in card["weights"]]
            overall = f"{row['overall']:.1f}" if row["overall"] is not None else "N/A"
            lines.append("| " + " | ".join([str(row["rank"]) if row["rank"] is not None else "—", escape(row["subject"]), *scores, overall]) + " |")
        lines.extend(["", "Weights: " + ", ".join(f"{escape(measure)}={weight:g}" for measure, weight in card["weights"].items()) + ". Overall is unavailable if an active criterion lacks evidence.", ""])
        for rating in db.execute("SELECT * FROM ratings WHERE scope=? ORDER BY subject,measure", (scope,)):
            links = "; ".join(citation(db, row) for row in _observations(db, json.loads(rating["evidence_ids"])))
            lines.append(f"- {escape(rating['subject'])}, {escape(rating['measure'])}: {escape(rating['rationale'])}" + (f" Evidence: {links}." if links else " No numeric evidence claim."))
        lines.append("")
    for payload in comparisons:
        lines.extend([f"## {escape(payload['title'])}", "", "[INFERENCE] " + escape(payload["summary"]), ""])
        for point in payload["points"]:
            links = "; ".join(citation(db, row) for row in _observations(db, point["observation_ids"]))
            lines.extend(["- [INFERENCE] " + escape(point["text"]) + " Evidence: " + links + "."])
            for alternative in point["alternatives"]:
                lines.append("  - Alternative/context: " + escape(alternative))
        lines.extend(["", "Limits: " + " ".join(escape(item) for item in payload["limits"]), ""])
    emitted = set()
    for payload in profiles:
        lines.extend([f"## {escape(payload['subject'])} — {escape(payload['scope'])}", "", "[INFERENCE] " + escape(payload["summary"]), ""])
        for key, title in (("strengths", "Strengths"), ("vulnerabilities", "Vulnerabilities"), ("counterexamples", "Counterexamples")):
            lines.extend([f"### {title}", ""])
            if not payload[key]:
                lines.append("No supported example recorded; absence is not proof of the opposite.")
            for point in payload[key]:
                observations = _observations(db, point["observation_ids"])
                lines.append("- [INFERENCE] " + escape(point["text"]) + " Evidence: " + "; ".join(citation(db, row) for row in observations) + ".")
                if not any(row["attribution"] == "supported" for row in observations):
                    lines.append("  - Attribution caveat: this point has no stable, identity-supported observation.")
            lines.append("")
        lines.extend(["### Interpretation limits", "", *["- " + escape(item) for item in payload["limits"]], "", "### Exchange evidence", ""])
        for observation in _observations(db, _profile_ids(payload)):
            if observation["id"] in emitted:
                continue
            emitted.add(observation["id"])
            marker = "[INFERENCE] " if observation["inference"] else ""
            lines.extend([f"- **{escape(observation['kind'])}**: {marker}{escape(observation['claim'])} {citation(db, observation)}. Attribution: {escape(observation['attribution'])}."])
            if observation["unresolved"]:
                lines.append("  - Still unresolved: " + escape(observation["unresolved"]))
            for alternative in json.loads(observation["alternatives"]):
                lines.append("  - Alternative/context: " + escape(alternative))
        lines.append("")
    lines.extend(["## Method limits", "",
                  "Questions, opportunities, languages, recording conditions and interviewer pressure differ. An unasked question is not an evasion. A premise correction, privacy boundary or lack of current knowledge can be legitimate while leaving requested information unavailable. Check delayed answers before calling a non-answer.", "",
                  "Voice similarity, coverage and estimated speaker count are diagnostics, not identity or accuracy guarantees. No face matching supports names. No pitch, projection, pacing, gestures, stage presence or precise interruption measures are inferred from captions or embeddings.", "",
                  "Public reports contain original analysis and timestamped paraphrases, not full third-party transcript reproductions. Literal-copy and quotation checks are conservative safeguards, not a legal clearance or a complete detector of translated reproduction.", ""])
    for warning in checks["warnings"]:
        if warning.get("source_id") is None or warning.get("source_id") in required_sources:
            lines.append("- Data limitation: " + escape(warning["problem"]))
    content = "\n".join(lines)
    _check_reproduction(db, public_text, content)
    destination = Path(output) if output else Path(project) / "reports" / "analysis.md"
    path = atomic_text(destination, content)
    with db:
        db.execute("INSERT OR REPLACE INTO rendered_reports VALUES(?,?,0)",
                   (str(path.resolve()), hashlib.sha256(content.encode()).hexdigest()))
    return {"report": str(path.resolve()), "profiles": len(profiles), "comparisons": len(comparisons), "sources": sorted(required_sources), "complete_source_review": True}
