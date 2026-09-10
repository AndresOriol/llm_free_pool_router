# Explore Machintl experiment

## Mechanical verdict

**Not promoted.** This is one sequential observation per arm, with no hidden-test acceptance result. Control and candidate v1 share `gemini-3.5-flash`, but quota and provider state changed over time; candidate v2 is on `gemini-3.6-flash` after 3.5 quota depletion. The promotion rule therefore cannot turn lower token totals into a configuration decision. Both candidate reports have decision-critical evidence failures.

The numbers below are provider-only `tokens_in` from each completed run's [experiment record](../runs/20260910_explore_machintl_control/experiment.json) and [candidate record](../runs/20260910_explore_machintl_candidate/experiment.json). The recent wrapper-only metrics correction does not change these paired live totals.

| arm | status | calls / errors | tokens in | searches | evidence outcome |
| --- | --- | ---: | ---: | ---: | --- |
| baseline | interrupted | 5 / 4 | 0 recorded | 0 | no completed model response |
| routerfix | interrupted | 6 / 3 | 18,883 | 0 | two successful model steps; sixth attempt in flight at kill |
| control | completed | 60 / 15 | 2,306,924 | 19 | report produced; decision-critical support fails |
| candidate v1 | completed | 60 / 10 | 1,802,268 | 10 | better work products; rejected for citation and legal-evidence failures |
| candidate v2 | completed | 57 / 9 | 1,454,307 | 10 | lower cost, but unsupported legal and commercial certainty |

## Open-coded run roll-call

1. **Baseline — provider recycling prevented research.** [The record](../runs/20260910_explore_machintl_baseline/experiment.json) reports operator interruption, five calls and four bounces; [output.log](../runs/20260910_explore_machintl_baseline/output.log) records successive Gemini 3.8 503s and the router returning to a cooled account. No tool call or completed model output appears in [trace.jsonl](../runs/20260910_explore_machintl_baseline/trace.jsonl). This is an infrastructure stopping case, not a research-quality result.
2. **Routerfix — attempt selection allowed progress but did not settle availability.** [The record](../runs/20260910_explore_machintl_routerfix/experiment.json) has six calls, three bounces and 18,883 input tokens. [output.log](../runs/20260910_explore_machintl_routerfix/output.log) shows three 503s interleaved with two completed model steps before interruption; the sixth attempt was in flight at kill, and [trace.jsonl](../runs/20260910_explore_machintl_routerfix/trace.jsonl) contains only `ls` and todo writes. It establishes recovery from the baseline's immediate loop, not research capability.
3. **Control — completed research over-searched and made unsupported decision claims.** [The record](../runs/20260910_explore_machintl_control/experiment.json) records 19 searches, 60 provider calls, 15 errors, 2,306,924 input tokens, and only the request plus final report. The evidence audit identifies a contradicted conversion of €1.856bn unknown loss into all theft, unsupported months-payback, and unsupported zero-privacy conclusion ([evidence review §2](../../../.codex/artifacts/2026-09-10-explore-evidence-review.md)); the exact report claims are in [final report lines 54, 117, 121 and 143](../runs/20260910_explore_machintl_control/workdir/research/final_report.md), with the countervailing AECOC text in trace turn 22 ([trace.json](../runs/20260910_explore_machintl_control/trace.json)).
4. **Candidate v1 — lower cost and better persistence did not produce reliable evidence binding.** [The record](../runs/20260910_explore_machintl_candidate/experiment.json) records 10 searches, five saved Markdown files, 60 calls, 10 errors and 1,802,268 input tokens. Per-researcher middleware held searches to five while preserving writing. But the report cites Veesion's own legal page as the authority for CNIL/AEPD conclusions ([source list line 150](../runs/20260910_explore_machintl_candidate/workdir/research/final_report.md); retrieved in [trace turn 15](../runs/20260910_explore_machintl_candidate/trace.json)), and uses retail bibliography entries [1]–[6] for industrial findings ([report lines 105–107 and 144–151](../runs/20260910_explore_machintl_candidate/workdir/research/final_report.md)). It also dates a September 10 run as June ([line 23](../runs/20260910_explore_machintl_candidate/workdir/research/final_report.md); [trace run.start](../runs/20260910_explore_machintl_candidate/trace.json)). These are evidence-quality failures, not prose grading.
5. **Candidate v2 — citation presentation improved, but critical claims remain unverified.** [The record](../runs/20260910_explore_machintl_candidate_v2/experiment.json) records 57 calls, nine errors, 1,454,307 input tokens, ten searches, 48 steps and five Markdown files. It corrects the date ([final report line 3](../runs/20260910_explore_machintl_candidate_v2/workdir/research/final_report.md)) and uses direct links. Yet it declares gesture analysis permissible under legitimate interest and low/minimal risk ([lines 75–78](../runs/20260910_explore_machintl_candidate_v2/workdir/research/final_report.md)) while its cited AEPD material concerns biometric AI, not the stated retail-gesture conclusion ([sources lines 226–232](../runs/20260910_explore_machintl_candidate_v2/workdir/research/final_report.md)). It asserts pricing bands, 45%–60% margins and four-to-six-deal breakeven ([lines 33, 161–168 and 188–202](../runs/20260910_explore_machintl_candidate_v2/workdir/research/final_report.md)) without a retrieved primary pricing or customer-unit-economics source. Its [trace](../runs/20260910_explore_machintl_candidate_v2/trace.json) records only two delegated tasks and no separate critical-gap verification delegation. The lower cost therefore does not establish decision-ready research.

## Cross-run reading

Candidate v1 reduced actual searches from 19 to 10 and provider input by 504,656 tokens (21.9%) while leaving report writing available. Candidate v2 kept ten searches and reduced input by a further 347,961 tokens versus v1 (19.3%). These are descriptive only: runs were sequential, n=1, and v2 changed models. V2 corrected date and direct-link mechanics but did not demonstrate the intended independent verification of legal or commercial gaps; it also retained blanket privacy/liability certainty. The candidate's additional plan and sector notes are useful persistence evidence, but do not compensate for unsupported conclusions.

The repository suite passed 372 tests in 68 seconds ([v2 tests.log](../runs/20260910_explore_machintl_candidate_v2/tests.log)). That validates the recorded code state, not the report's factual support.

## Recommendations

1. **Evidence contracts, not a prompt-only acceptance claim.** Target: the completed-report support failures (control denominator/ROI/privacy; v1 authority/scope/provenance; v2 gesture legality and commercial-unit economics). Add fixed-source probes for denominator preservation, ROI inputs, authority class, citation scope, and ambiguous worker visibility. Measure: exact claim-support pass/fail against supplied text, with no uncited decisive conclusion. This recommendation validates a design written by this project, so the fixed sources and grading rubric need human review.
2. **Repeat an interleaved control/candidate comparison on one model and fresh quota.** Target: unmeasured cost/reliability effect of the middleware and prompt changes. Measure: at least three paired runs per arm, provider-only input tokens, actual searches, provider errors, and the evidence contracts above. Do not promote on raw completion or n=1 token savings.
3. **Keep the per-researcher cap and test the recovery path.** Target: control's 19-search overshoot (one instance) and budget exhaustion's risk of suppressing synthesis. Measure: each researcher makes at most five search executions, writes any findings reached before exhaustion, and final output labels unanswered critical questions. This is a continuation of a mechanism we authored; it needs the paired run above to show that its cost reduction does not buy silence.

## What the evidence cannot support

- A pass rate, hidden-test result, or promotion: none is present, and n=1 is not a comparative sample.
- A causal claim that candidate v1's prompt/middleware caused its lower token use or error count; sequential quota drift confounds both.
- A factual verdict on every market or legal statement. The audit samples decision-critical claims and distinguishes contradicted claims from unsupported ones; it does not replace legal or market research.
- That v2's direct URLs or prompt changes caused its lower token total. Its different model and depleted 3.5 quota make it a confounded observation, not a v1 comparison.

## Conflict of interest

We author both the harness changes and this assessment. That makes the lower candidate-v1 cost attractive evidence, while its source-binding defects cut against the same work. The report therefore treats the result as a rejected quality candidate and calls for fixed-source, human-reviewable contracts rather than claiming the design helped.

## Comments
