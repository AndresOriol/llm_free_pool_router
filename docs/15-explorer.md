[← Wiki index](README.md)

# 15. The web explorer

*The second agent: one that reads the web instead of a repository, and leaves
Markdown behind instead of a diff. Why the web is reachable at all on a free
tier, why searching is a tool call rather than a bound tool, and why it runs no
programs.*

## 15.1 What it is for

[agent/explore/](../agent/explore/). The coding agent
([6](06-agent.md)) is good at changing a project and has no way to find anything
out. Anything it needs from outside — an API's current limits, a library's
actual signature, what a vendor charges this month — has to be typed into its
brief by a human who looked it up.

The explorer is the half that looks it up.

```bash
python -m agent.explore ../my-project < question.md   # researches, writes /research
python -m agent.code    ../my-project < brief.md      # builds, reads /research
```

**They meet on disk.** The explorer writes `/research/*.md` into a workdir; the
coding agent, pointed at that same workdir, reads them like any other file. No
shared state and no deliverable in flight — which is why it is safe to run them
hours apart, or to run the explorer once and the coding agent five times against
what it found.

That constraint is also why the prompt spends most of its length on the written
record ([system_prompt.md](../agent/explore/system_prompt.md)). The explorer's
closing message is not the deliverable and nobody reads it. The files are the
deliverable, because the thing that reads them next is an agent that was not
there.

> **Amended.** *"They meet on disk"* used to read *"and nowhere else… no message
> bus, no protocol to keep in step"*, and the coding agent can now ask the explorer for a report directly
> ([16. The agent protocol](16-agent-protocol.md)). What the protocol carries is
> the *request* and the *status*, never the deliverable: a research note is still
> a file on disk that outlives the exchange, so everything above still holds. A
> human sequencing the two runs by hand still works and is still the default way
> to use the explorer alone.

## 15.2 The web, on a free tier

Google's Gemini API grounds a call in Google Search (`google_search`) and in
named pages (`url_context`). Both are reachable with the keys this project
already pools — no scraping, no search-API signup, no new secret, nothing that
strains anyone's terms of service.

| | Allowance | Notes |
| --- | --- | --- |
| `google_search` | **5,000 grounded searches/month**, free, shared across the Gemini 3.x family | $14/1,000 after that on a paid key; a free key simply stops |
| `url_context` | **free**, no per-call fee | the fetched page is charged as ordinary input tokens |
| `url_context` limits | 20 URLs/request, 34 MB/URL | public pages only: no paywalls, no YouTube, no Workspace docs, no private hosts |

Measured against everything else in this pool that is a generous budget: 5,000
searches a month against Gemini flash members that allow twenty *requests a day*
([5. Providers](05-providers.md)). The scarce resource is the model call that
wraps the search, not the search.

### 15.2.1 The two tools are granted separately

Groq cannot do either — grounding is a Gemini API feature. The interesting line
is inside the Gemini platform, and it is **not** where the model families are.
Probed against the live API on 2026-08-26:

| Member | `google_search` | `url_context` |
| --- | :-: | :-: |
| `gemini-*` | ✓ | ✓ |
| `gemma-*` | ✓ | ✗ `400 INVALID_ARGUMENT` |
| Groq | ✗ | ✗ |

Gemma searches perfectly well — six citations on the probe — and refuses
`url_context` outright with *"Url Context as tool is not enabled for this
model"*. So there are **two pools, not one**, and each tool asks its own
question ([search.py](../agent/explore/search.py)).

**This is worth getting right, and it was got wrong first.** The original filter
reasoned from the model family — Gemma is not a Gemini, so assume no built-in
tools — and excluded Gemma from searching. That threw away most of the pool's
real search capacity: Gemma carries **1,500 requests a day** against the flash
tier's twenty ([5.4](05-providers.md#54-current-free-tier-limits)). On this pool
the correction took the search side from 16 members to 20, and moved the
*primary* search member from a flash-lite to a Gemma.

The lesson generalises: a vendor's capability grant is a fact to probe, not to
infer from a model's name. Re-probe before widening either list — it can change
without notice, and the failure mode is a member that 400s every call and cools
down an account that was never at fault.

A pool that cannot search at all is refused **before the run**, not during it;
the alternative is an agent spending its whole budget writing a research note
about having no research. A pool that can search but cannot read a URL — nothing
but Gemma — is a warning, not an error: `read_url` explains why it cannot open a
page and `web_search` carries on.

## 15.3 Why searching is a tool call

Gemini 3 will run `google_search` alongside ordinary function calling, so the
obvious design is to bind it to the explorer's own model and let searching
happen inside a step already being paid for. It costs nothing extra and it was
rejected anyway.

**The citations are not in the conversation.** They come back in
`response_metadata["grounding_metadata"]`, which the model cannot see. Bound
directly, the agent searches, answers from what it found, and has no URLs to
write down — and a research note whose sources cannot be checked is precisely
the failure this agent exists to avoid.

Routing the search through a tool puts the source list in a `ToolMessage`, as
text the model can copy verbatim into a file. The cost is one extra request per
search, paid to make the answer auditable.

### 15.3.1 Citations have to be un-redirected

Every `uri` in `grounding_metadata` is a
`vertexaisearch.cloud.google.com/grounding-api-redirect/…` link rather than the
source, and the human-readable `title` is usually just a bare domain. Written
into a note unresolved it is worthless twice over: a reader cannot see where the
claim came from without following it, and `read_url` cannot open it.

One `HEAD` request per citation turns it back into
`https://ai.google.dev/gemini-api/docs/pricing`. They are resolved in parallel —
a dozen independent waits on other people's servers, in sequence, is a minute of
a metered run spent on HTTP round trips — and best-effort: a redirect that will
not resolve is reported as-is, which is worse than the real URL and much better
than a dropped source.

### 15.3.2 What a search comes back as

```
The free-tier monthly allowance for Grounding with Google Search is 5,000
prompts per month for Gemini 3.x models. After that the cost is $14 per 1,000.

Searched for: Gemini API grounding free tier allowance and pricing

Sources:
[1] google.dev - https://ai.google.dev/gemini-api/docs/pricing
[2] cloudzero.com - https://www.cloudzero.com/blog/gemini-pricing/
```

Three parts, and each is there because its absence caused a specific problem:

- **The answer.** If it is empty the `finish_reason` is named, so the agent
  retries a narrower question instead of concluding the web is silent. Observed:
  a grounded call that searched, returned citations, and wrote nothing.
- **The queries Google actually ran**, which are rarely the words asked for.
- **The sources — or an explicit warning that there are none.** No sources means
  the model answered from memory. The agent has to be told, or it launders a
  recollection into a cited fact.

## 15.4 Which member serves a search

`SearchRouter` holds the **same provider objects** as the main pool rather than
copies. Cooldown lives on the provider, so a search that exhausts an account is
immediately visible to the conversation routing through that same account, and
vice versa. Two routers over one set of members is the honest model of one free
tier being spent two ways.

**It reaches for the cheapest capable member first**, which is the reverse of
what `AutonomousLLMRouter` does everywhere else
([3.4](03-pool-model.md#34-priority-tiers)). Searching is not judgement: the
grounded call retrieves and summarizes, and the thinking happens afterwards, in
the explorer's own conversation on whichever member the main pool picked. Left
in priority order, every search would spend one of the reasoning tier's
twenty-a-day requests on retrieval. So the comparison is inverted, and it climbs
the tiers only as each cheap member exhausts itself.

Inverting it turns the pool's *last resort* into the search tier's *first
choice*, and that is exactly right. Gemma sits at priority 31–32 because it is
the weakest judgement in the pool; it also holds 1,500 requests a day, which
makes it the best retrieval budget by two orders of magnitude. A recorded search
routed to `gemma-4-26b-a4b-it` on the first attempt and answered with sources.

Everything else is the ordinary failover loop
([4.5](04-failover.md#45-the-failover-loop)): a search built on
`RouterChatModel` inherits it, so an account rate-limited mid-research costs one
reroute rather than the run. A recorded run walked five members in eleven
seconds and finished.

## 15.5 What it is allowed to do

Same jail as the coding agent, rooted at `workdir`, with one difference:
**it runs no programs at all.**

| | coding agent | explorer |
| --- | --- | --- |
| read / write / edit files | ✓ | ✓ |
| `python`, `pytest` | ✓ | — |
| `git` (by subcommand) | ✓ | — |
| `web_search`, `read_url` | — | ✓ |

The coding agent needs a shell to close its own loop — write a test, run it,
react to the result. A researcher has no loop to close, so the allowlist is
empty and `execute` refuses everything with the backend's own explanation, as a
readable tool result rather than an exception. Nothing is lost, and the blast
radius of an unattended run drops to the files it writes
([6.2](06-agent.md#62-the-blast-radius)).

`ShellAllowListMiddleware` is not installed here, unlike on the coding agent:
with an empty allowlist there is nothing for it to mirror, and a rule that exists
in two places is worse than one that exists in one.

## 15.6 What it costs a run

Per `web_search` or `read_url` call:

- **one Gemini-platform request** against the pool, from the cheapest capable
  member — in practice a Gemma one, which is the budget you can most afford;
- **one grounded search** against the 5,000/month, for `web_search` only;
- **up to twelve `HEAD` requests** to resolve citations, in parallel, off the
  pool entirely.

The prompt is explicit that this is metered and that a seventh confirming source
costs the same as a first source on the next question. On a free tier the
discipline of stopping when the answer stops moving is not a nicety.

## 15.7 Measured against a reference research agent

*Written after the first live research run. It did not fail — it produced a
27,000-word cited report that reads well — and it diverged from its own
instructions in four measurable ways.*

LangChain ships a deep-research agent on this same harness
([docs](https://docs.langchain.com/oss/python/deepagents/deep-research)), which
makes it the honest thing to compare against rather than a blank page.

| | LangChain deep-research | This explorer, as it ran |
| --- | --- | --- |
| Reading a source | `tavily_search` **fetches the page and converts it to markdown** — reading is not a separate act | `web_search` returns a *summary*; `read_url` is a second, optional tool. **0 calls in 13 searches** |
| Stopping | numeric: three sources, or the last two searches agreed, or **five searches total** | prose: "stop when the answer stops moving". **13 searches** |
| Topic isolation | one sub-agent per topic, fresh context each | one conversation across four topics |
| Deliverable | `/research_request.md` + `/final_report.md`, numbered `### Sources` | one note per topic — **got one note for four topics** |

### 15.7.1 What the divergences cost, and what was changed

Three of the four were the prompt's own rules going unfollowed, so the fix was
to make them checkable rather than to add new ones:

- **Nothing was ever opened.** Every figure in the report — 2,294 instances, 500
  in Verified, 4.8% for Claude 2 — came from a grounded summary. The citations
  were real (spot-checked; the arXiv ids are correct), so this is not
  fabrication. It is a report whose numbers were never traced to a page, which
  is a different and quieter problem. The prompt now makes opening the source a
  precondition for writing a figure down, and names the claim types it means.
- **No budget existed to exceed.** "Stop when the answer stops moving" is not a
  stopping rule an agent can check itself against. It is now ten searches,
  three agreeing sources, or two searches that said the same thing — the
  reference's shape, with a larger number because that agent spends a fresh
  sub-agent per topic and this one does not.
- **Everything was written at the end**, in one file, after the last search. A
  crash on search twelve would have left nothing. The prompt now asks for the
  first file after two searches.
- **The prompt taught the behaviour it forbade.** `web_search`'s description
  says *"Ask a full question, not keywords"*; three sections below, the guidance
  offered `"gemini api google_search tool request format"` as the good example.
  All thirteen queries were keyword strings. The example was the bug.

### 15.7.2 The one difference not copied

**Their search tool reads the page; ours returns a summary and hopes.** That is
the structural version of the first row, and copying it would fold `read_url`
into `web_search` so that grounding is not optional. It was not done, because on
this pool every fetch is a model call against a daily request budget
([15.6](#156-what-it-costs-a-run)) and making every search cost two would halve
the questions a run can ask. So the prompt carries the rule and
[research_trajectory](../evals/research_trajectory.py) checks whether it was
followed. **If the check keeps failing, the prompt is the wrong instrument and
the tool is the right one.**

Per-topic sub-agents were also not copied: on this harness a sub-agent is a
whole session, and four topics would be four sessions against a free tier.

---

**Previous:** [← 14. Quota panel](14-quota-panel.md) · **Next:** [16. The agent protocol →](16-agent-protocol.md)
