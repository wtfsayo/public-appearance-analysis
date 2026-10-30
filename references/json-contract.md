# Host-written JSON

Run `schema KIND` to obtain the current machine schema. Use UTF-8 JSON with finite numbers, not Markdown fences. Store payloads under the user's project. Source IDs, cue IDs, batch IDs and observation IDs come from real command output; labels below describe fields, not IDs to copy.

## Speaker map (`schema speakers`)

The root is `{"speakers": [...]}`. Each entry has `speaker` (an existing cue/segment label), `name`, `role`, `status` (`supported` or `unverified`) and `evidence`.

Evidence requires `kind`, `cue_ids` and a meaningful `note`. Kinds are `spoken_introduction`, `publisher_credit`, `cross_recording_voice` and `user_provided`. Publisher credit also requires `url`; spoken introduction and cross-recording voice require nonempty supporting cue IDs. Include `cue_ids: []` when the allowed support is a publisher URL or explicit user-provided mapping without cue evidence. User-provided mapping means the user actually supplied it. A cluster centroid alone is not evidence of a name. Preserve unverified status when evidence does not support attribution. Import with `map-speakers SOURCE_ID --input FILE`.

## Batch review (`schema review`)

The root has `batch_id`, `reviewed_cue_ids` and `observations`. The reviewed IDs must equal the packet's target IDs exactly, in the same order.

Packets retain original cues plus any overlapping `independent_asr_cues` for the target/context window. ASR is another fallible source, not a tie-breaker or proof of accuracy. Complete caption/ASR/diarization preparation before creating packets; those evidence inputs are then frozen.

Every observation has:

- `subject`: the target's exact stored name.
- `kind`: one of the kinds below.
- `claim`: original, bounded analytical paraphrase.
- `question_cue_ids`, `response_cue_ids`, `evidence_cue_ids`: arrays of real cue IDs from that source. Answer kinds require question and response evidence. Other kinds can use empty question/response arrays.
- `alternatives`: plausible alternative explanations, or an empty array when none applies.
- `unresolved`: the specific unresolved point, or an empty string if none remains.
- `inference`: a boolean distinguishing interpretation from direct observation.

Kinds are `direct_answer`, `partial_answer`, `premise_rejection`, `boundary`, `bridge`, `strategic_ambiguity`, `humorous_deflection`, `non_answer`, `framing`, `reasoning`, `structure`, `engagement`, `adaptation`, `correction`, `uncertainty`.

Evidence may include context outside the packet target but must stay inside the same source. Finalize delayed-answer checks before importing adverse classifications. `observations: []` is valid; the exact review target list is still required. `review-batch --input FILE` returns generated observation IDs. Do not manufacture or reuse guessed IDs.

## Observation correction (`schema observation`)

For a delayed reply or an unsupported interpretation discovered after import, submit one complete observation object with the fields above to `revise-observation OBSERVATION_ID --input FILE`. Do not include the ID inside the JSON. The existing ID is retained, source/subject cannot change, and the prior record is archived. The command invalidates the subject's profiles/ratings and all project comparisons, which must be reassessed and re-imported before the next report. It cannot rewrite frozen source text, segmentation or speaker maps.

Rendered report paths and content hashes are tracked. Corrections mark matching generated files stale without overwriting externally modified files; inspect `stale_reports_not_modified` and audit warnings before sharing an old artifact.

## Profile (`schema profile`)

The root has `subject`, `scope`, `summary`, `strengths`, `vulnerabilities`, `counterexamples`, `limits`. Scope is `all` or an actual source ID. Each point in the three evidence arrays has `text` and `observation_ids`. Limits are strings, with at least one required. Use empty evidence arrays rather than invented strengths/weaknesses; at least one cited point must support the summary overall. Every cited observation must belong to the profile's subject and scope. Read all source cues relevant to the profile before rendering a finished report. Import with `import-profile FILE`.

## Ratings (`schema scores`)

The root has `subject`, `scope` and `ratings`. Supply exactly all six measures `clarity`, `structure`, `precision`, `responsiveness`, `engagement`, `adaptability`. Each rating has `measure`, `score` (integer 0-10 or JSON `null`), `rationale` and `observation_ids`. Non-null ratings require evidence. Null requires a reason and never means zero. Follow the anchors in [rubric.md](rubric.md), not impressionistic personality labels. Import with `import-scores FILE`.

Weights use a plain measure-to-number object. For example, `{"engagement": 2}` updates just that measure; `{"clarity":1,"structure":1,"precision":1,"responsiveness":1,"engagement":1,"adaptability":1}` restores equal weights. All values must be finite and nonnegative; the active sum must stay positive. Import with `weights --input FILE`.

## Comparison (`schema comparison`)

The root has `title`, `summary`, `points`, `limits`. Each point has `text`, `observation_ids`, `alternatives`. At least one point and one limit are required. Cite observations across at least two subjects overall and support both sides of each comparative claim. A comparison's evidence cannot be replaced by profile prose or numerical totals. Explain role, source-selection, language and opportunity confounds in the point or limits. Import with `import-comparison FILE`.

## Completion

Retain the imported JSON and returned IDs as internal provenance, then run `audit`. Schema validation can detect malformed citations and missing batches; the host/evidence reviewer must still check whether the cited words justify the claim. Render the report only after resolving those evidence issues. Public output is the original analytical report, not these internal source-bearing artifacts.
