# Explore research: capability issues and future evals

*Branch `codex/improve-explore-research`; 2026-09-10; Machintl reference analysis,
live explore runs and candidate changes from master `0b7676e`.*

The target is research that helps a constrained reader make a decision: scoped
questions, evidence appropriate to those questions, separate scenario findings
and a synthesis that preserves uncertainty. The reference is a behavioral
example, not a verified answer key. This queue separates observed infrastructure
failures from capability hypotheses until a recorded run establishes them.

## 1. Reference and experiment

Claude Code session `90bfa0cf-7099-4d7a-a68c-8fbcb16bc83d` in the sibling
`machintl` project began on September 7 and continued September 10. The request
concerns two Spanish founders selling adaptable computer-vision hardware and
software plus implementation: retail theft detection and industrial quality
control with additional sensors. The reference split competition by sector,
researched legal constraints and economics separately, then split stalled retail
work into vendors and demand. Five notes and an HTML synthesis survive in
`../machintl/research/` and `../machintl/two-markets-one-stack.html` (paths from
the repository root). The six researcher traces contain 183 WebSearch and 44
WebFetch calls, including the stalled attempt; these are not pool cost metrics.

Baseline input is the original request, with no reference answers in its empty
workdir: [request](../../evals/results/runs/20260910_explore_machintl_baseline/request.md).
See [explorer design](../../docs/15-explorer.md) and
[promotion rule](../../docs/08-evaluation-method.md#87-the-promotion-rule).

## 2. Observed: slow errors recycle the highest-priority members

**Evidence:** baseline `output.log`, 11:41:12–11:43:49 local time: four completed
503 responses, all Gemini 3.8 high-demand errors, alternating accounts 1 and 2;
the fifth attempt returns to account 1. No successful model step or research tool
call occurred before operator interruption. Each error took approximately
32–42 seconds, exceeding the other member's 30-second cooldown. There were
60 context-eligible members, so this was not exhaustion of the whole pool.

**Cause:** priority selection had no per-request attempt history. Additionally,
availability polling reset `consecutive_failures` on cooldown expiry, preventing
exponential backoff across successive refused requests.

**Fix and regression contract:** prefer untried fitting available members within
one request, while permitting retry when only tried members remain; reset the
failure count after success. Deterministic regression lives in
[test_failover.py](../../tests/agent/test_failover.py). This is a router regression
test, not a model-quality dataset example. Provider overload itself is external.

## 3. Capability candidates to validate in recorded trajectories

| ID | Scenario and context | Failure signature | Desired behavior / later eval |
| --- | --- | --- | --- |
| EXP-01 | Two markets, two founders, Spain, physical deployment | Generic vendor overview omits buyer, substitutes, local access or support burden | First-decision probe checks whether the plan identifies decision-changing evidence requirements and preserves both scenarios. Do not require a fixed number of workstreams. |
| EXP-02 | Shared legal and economic constraints across different use cases | One broad five-search assignment leaves blocking questions unanswered but reports completion | Check delegation briefs carry scope, user constraints, evidence requirements and a saved findings path; final coverage distinguishes unresolved work. |
| EXP-03 | Several vendor pages, no independent demand evidence | Stops at three sources and infers market attractiveness | A research continuation probe supplies three vendor pages and missing buyer evidence; expect targeted demand research or explicit uncertainty if budget is exhausted. |
| EXP-04 | Report includes costs, market size or regulatory dates | Real URL attached to an unsupported figure, wrong denominator or cross-jurisdiction conclusion | Give fixed source documents with dates and units; grade claim support, estimate arithmetic, and distinction between enacted law and proposals. Never use the reference's conclusions as gold. |
| EXP-05 | Workstream fails after useful research; another completes | All useful findings remain in conversation, or report silently fills the missing work with memory | Interrupt after retrieved evidence; expect saved partial findings and an explicit incomplete workstream. Test report collision separately. |
| EXP-06 | Primary evidence is inaccessible, a PDF or a long clipped page | Search URL treated as proof although the relevant content was not read | A tool-context probe presents fetch failure/truncation; expect another accessible primary source, a narrower search, or an explicitly unverified claim. |
| EXP-07 | Long multi-stream research and a nominal per-researcher budget | More than five searches by a researcher; large repeated histories trigger TPM refusals | Integration check must count actual search executions per invocation, including parallel requests. Candidate uses the existing SDK tool limiter; saving findings must remain possible after exhaustion. |
| EXP-08 | Industrial inspection context does not specify whether workers enter camera view | Declares "zero GDPR risk" simply because the use case is industrial | Fixed-context probe includes incidental employees and product hardware; expect privacy and product-compliance questions, not blanket exemption. |
| EXP-09 | Two researcher notes each number their own sources from 1 | Industrial claims keep local numbers after the combined bibliography assigns those numbers to retail pages | Supply two disjoint source sets; each final claim must retain its original source identity. Candidate v2 uses direct links instead of renumbering. |
| EXP-10 | Current research date differs from source publication dates | Final report adopts a source month as its own research date | Give an explicit runtime date plus differently dated sources; require correct report provenance and separate publication dates. |

The control already demonstrates EXP-05's lack of interim findings (18 searches,
only the request saved), EXP-07's budget overshoot, and EXP-08's blanket privacy
claim in `final_report.md`, sections 4–5. EXP-04 is also visible internally: its
retail section identifies 1.856 billion euros as **unknown loss**, then the ROI
and comparison relabel the entire amount as **theft** despite explicitly giving
external theft's share as 57%. Its claim of payback "within months" contains no
system price or customer-level savings calculation. These are evidence-linked
failure cases, independent of whether the reference's opposite verdict is right.

These are future dataset/scenario specifications, not newly published LangSmith
examples. Record observed outcomes and run IDs below before promoting a
capability hypothesis into a measured recurring issue.

The first candidate demonstrates EXP-09 and EXP-10: industrial [1]–[6] point to
retail sources in the combined bibliography, and it declares June 2026 despite
running September 10. The independent
[evidence audit](2026-09-10-explore-evidence-review.md) identifies exact report
lines and source text. This is why v1 is rejected despite improved file structure
and 21.9% lower recorded provider input than the control.

## 4. Instrumentation and source-access limitations

The trace records both provider and router end events with the same token usage.
Raw summation doubled cost on these runs. The metric now deduplicates paired
spans while preserving wrapper-only legacy/direct usage; the quota ledger was
already counting provider calls once. Older stored summaries are not rewritten.

The trajectory checker has two limitations to address when creating evals:
its global 15-search threshold omits the allowed three delegation rounds, and
it exempts the deep workflow from the early-writing check. Use actual per-worker
executions and first **findings** persistence, not the request/plan files, to
judge the candidate. The five-search cap is now enforced per researcher by the
SDK and tested with a parallel overflow batch and two independent invocations.

The search tool's failure text permits citing a search snippet, but currently
does not return that snippet. It also has no direct URL-following tool, and
clips each fetched page at 20,000 characters. These are tool-context constraints
for EXP-06, not grounds for a model to treat unseen content as verified.

## 5. Run outcomes

The original baseline and full-pool router check were interrupted after
reproducing the routing failure and verifying pool traversal respectively.
The fixed-model control retains master's research prompt so a routing failure
is not misreported as a research-quality failure. Three reports completed:

| Run suffix | Model | Provider input tokens | Searches | Provider errors / attempts | Outcome |
| --- | --- | ---: | ---: | ---: | --- |
| `control` | Gemini 3.5 Flash | 2,306,924 | 19 | 15 / 60 | Unsupported economic and legal certainty; no workstream files |
| `candidate` | Gemini 3.5 Flash | 1,802,268 | 10 | 10 / 60 | Saved sector findings, but citation collisions and wrong report date; rejected |
| `candidate_v2` | Gemini 3.6 Flash | 1,454,307 | 10 | 9 / 57 | Correct date and direct links; grounding still insufficient; not promoted |

All IDs begin `20260910_explore_machintl_`. These are single live tasks without
hidden-test verdicts, not success rates. V2 changed models after 3.5 exhausted
its daily quota, so its cost and quality cannot establish a causal gain.

V2 still demonstrates EXP-02 and EXP-04: only the initial two delegations ran,
despite unresolved legal and economic questions. Its final report calls pricing
"verified" and projects 45–60% gross margins and four to six deals per year for
cash-flow break-even without the necessary inputs. It treats a biometric-exam
AEPD case as support for retail gesture-analysis legality. Direct links fix the
number collision, but do not establish that a page supports its attached claim.
These claims remain in the generated report as evidence; they were not manually
repaired. Source grounding and early persistence remain future work.

The server-side 504 and 503 failures recovered. Backoff increased to 60 seconds
after repeated failure; the observed timeout did not justify changing the
existing SDK deadline. Routing regressions, usage deduplication and the enforced
search budget pass the full **372-test** suite. The
[batch report](../../evals/results/reports/2026-09-10-explore-machintl.md) records
the evidence and comparison limits. No candidate is promoted to master.
