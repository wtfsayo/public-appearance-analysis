# Subjective communication rubric

The host assigns six integer ratings from 0 through 10, or `null`. These are anchored interpretive judgments about performance in the reviewed sources, not psychometric measurements. Apply the same anchors across subjects, adjusted to their roles and actual opportunities. Cite observation IDs for every non-null rating, including counterexamples when they qualify the rationale.

| Measure | 0-2 | 4-6 | 8-10 |
| --- | --- | --- | --- |
| clarity | Central point repeatedly remains unclear in relevant opportunities. | Main point is recoverable, with some avoidable ambiguity or dense explanations. | Main points and qualifications are consistently understandable to the stated audience. |
| structure | Claims and reasons are repeatedly hard to follow. | Organization is serviceable but uneven, with some loose transitions or unexplained detours. | Claims, reasons, examples and qualifications form a sustained, navigable explanation. |
| precision | Relevant terms or boundaries repeatedly stay unspecified. | Some concrete distinctions; important claims sometimes remain broad or underspecified. | Relevant distinctions, specifics and limits are explicit and sustained. This is not a factual-correctness score. |
| responsiveness | Actual questions repeatedly remain unanswered after the full appearance is checked. | Answers address some elements; follow-ups or later replies resolve some gaps. | Questions and follow-ups receive direct uptake or explicit, relevant premise challenges/boundaries. |
| engagement | Few accessible examples or rapport techniques when the format offers opportunities. | Some useful examples, narrative, humor or rapport, with uneven audience adaptation. | Sustained, relevant examples or rapport techniques make the material accessible. Persuasion is unmeasured. |
| adaptability | Repeated relevant follow-ups or correction opportunities receive little uptake. | Some repair, qualification or adjustment, alongside missed opportunities. | Follow-ups, corrections and audience needs consistently lead to relevant adjustment. Vocal calmness is unmeasured. |

Use 3 and 7 for evidence between the neighboring bands. Within a band, explain the integer choice through the pattern's breadth, recurrence and counterexamples. Reserve 0 and 10 for unusually strong source-wide evidence at the endpoint. A single good line or weak exchange rarely supports an extreme. Apply role-specific expectations without silently changing the measure's meaning.

`null` means the available source does not permit assessment. For example, a prepared monologue without questions may leave responsiveness unassessable. Explain why; zero means observed poor performance, not missing data. Do not penalize accent, disability, translation artifacts, limited airtime or a role with no relevant opportunity.

## Weights and sensitivity

Defaults give each measure weight 1. Weights are explicit, finite and nonnegative, with a positive active sum. An inactive measure has weight 0. The CLI computes the weighted mean as `sum(weight * score) / sum(weight)` only when every active measure has a non-null score. It does not silently drop missing active measures or fill them with zero.

Keep the equal-weight baseline unless the user explicitly supplies different priorities. Missing evidence is not a reason to change a baseline weight to zero. If any active measure is null, retain the null overall and compare supported measures individually. Weight sensitivity is a separately labeled scenario, not permission to manufacture a complete ranking.

User priorities can change weights. Record the actual weights alongside the result and explain the choice. Run `sensitivity --measure engagement --factor 2 --scope all` for a concrete alternative weighting, and inspect how totals or ordering change. This is a changed-weight calculation, not a confidence interval, significance test or proof of a stable ranking. Sensitivity to one measure does not cover every plausible weighting. Compare the underlying evidence and context even when numerical differences look large.
