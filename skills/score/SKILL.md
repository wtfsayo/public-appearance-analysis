---
name: score
description: Produce evidence-backed subjective communication scorecards with explicit weights and sensitivity.
argument-hint: "[subjects] [analysis project directory] [priorities]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/appearance *) Read
---

# Score reviewed communication

Request: $ARGUMENTS

Read `${CLAUDE_PLUGIN_ROOT}/references/rubric.md`, `${CLAUDE_PLUGIN_ROOT}/references/cli.md` and the Ratings section of `${CLAUDE_PLUGIN_ROOT}/references/json-contract.md`. Read `${CLAUDE_PLUGIN_ROOT}/references/methodology.md` and the rest of the contract only when evidence is missing and you run the packet workflow. Run `${CLAUDE_PLUGIN_ROOT}/bin/appearance --project "<PROJECT>" ...`, replacing `<PROJECT>` in every command with the absolute project path, and quote data arguments. Run the commands cli.md lists under "Long-running commands" in the background and wait for them.

1. Read `status`, `observations` and existing profiles. Identify scope, source coverage and the subject's role/opportunities. If evidence is missing, complete the methodology's entire-source packet workflow first; a score is not a substitute for reviewing the appearance.
2. Check identity before rating. If `status` lists an identity warning for the subject in a source, that source's observations are `uncertain`, and a rating that cites only them must be null. When that source has no packets yet, run `transcribe` (only when there are no cues), `diarize` and `map-speakers` first. When packets exist, tell the user that numeric scores from that source need a new project with diarization and speaker mapping before packets.
3. Rate exactly clarity, structure, precision, responsiveness, engagement and adaptability against the rubric's anchors. Supply integer 0-10 or `null`, a bounded rationale and actual observation IDs, citing patterns and counterexamples. A monologue may leave responsiveness null. Caption artifacts and accent are not performance deficits.
4. Read `schema scores`, write the payload to the user's project, and run `import-scores FILE`. Fix unsupported evidence rather than relaxing validation. Keep source-specific versus `all` scope consistent.
5. Apply weights as rubric.md "Weights and sensitivity" describes. Run `scorecard --scope SCOPE` and `sensitivity --measure engagement --factor 2 --scope SCOPE`, or an explicitly justified alternative perturbation, and explain the actual weighted-mean and ranking changes.
6. Run `audit`. Dispatch `public-appearance:evidence-reviewer` in the foreground to review evidence, missingness and context, and wait for its findings; without the agent, perform that review yourself. Render `report` after evidence issues are resolved. Present subjective ratings beside their rationales, coverage and limits, with diagnostics kept separate. Score only the six communication measures; IQ, deception, clinical, protected-trait and hidden-motive judgments stay out of scope.

Completion means all six measures are imported for each requested subject and scope, every non-null score cites a supported observation, every null has a stated reason, `scorecard` and `sensitivity` results are reported with the actual weights, and the rendered report path is returned.
