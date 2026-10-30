---
name: score
description: Produce evidence-backed subjective communication scorecards with explicit weights and sensitivity.
argument-hint: "[subjects] [analysis project directory] [priorities]"
disable-model-invocation: true
---

# Score reviewed communication

Request: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/references/rubric.md`, `${CLAUDE_PLUGIN_ROOT}/references/methodology.md`, `${CLAUDE_PLUGIN_ROOT}/references/cli.md` and `${CLAUDE_PLUGIN_ROOT}/references/json-contract.md`. Use `${CLAUDE_PLUGIN_ROOT}/bin/appearance --project "$PROJECT" ...` with quoted data arguments.

1. Read `status`, `observations` and existing profiles. Identify scope, source coverage and the subject's role/opportunities. If evidence is missing, complete the methodology's entire-source packet workflow first. A score is not a substitute for reviewing the appearance.
2. Rate exactly clarity, structure, precision, responsiveness, engagement and adaptability using the rubric's anchors. Supply integer 0-10 or `null`, a bounded rationale and actual observation IDs. Cite patterns and counterexamples; do not assign impressive-looking precision unsupported by the anchors. A monologue may leave responsiveness null. Caption artifacts and accent are not performance deficits.
3. Read `schema scores`, write the actual payload to the user's project, and `import-scores FILE`. Fix unsupported evidence rather than relaxing validation. Keep source-specific versus `all` scope consistent.
4. Inspect `scorecard` for actual current weights. Keep the equal-weight baseline unless the user explicitly supplies different priorities; when applying those priorities, write a plain weights object and `weights --input FILE`. State the resulting weights. Never fill missing active scores with zero or deactivate measures merely to obtain an overall. Retain null overalls and compare assessable measures individually. Sensitivity scenarios do not replace the baseline.
5. Run `scorecard --scope SCOPE` and `sensitivity --measure engagement --factor 2 --scope SCOPE`, or an explicitly justified alternative perturbation. Explain the actual weighted-mean change and any ranking change. This is weighting sensitivity, not an error bar or confidence interval.
6. Run `audit` and seek `public-appearance:evidence-reviewer` review of evidence, missingness and context, or perform that review yourself. Render `report` after evidence issues are resolved. Present subjective ratings beside their rationales, coverage and limits; keep diagnostics separate. No IQ, deception, clinical, protected-trait or hidden-motive scoring.
