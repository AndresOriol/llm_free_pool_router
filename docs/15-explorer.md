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
> ([16. Delegation](16-delegation.md)) — by running this command. What that adds is
> the *request* and the *status*, never the deliverable: a research note is still
> a file on disk that outlives the exchange, so everything above still holds. A
> human sequencing the two runs by hand still works and is still the default way
> to use the explorer alone.

## 15.2 The web, on a free tier

Search is [Tavily](https://tavily.com): a search API built for agents, whose free
tier is **1,000 credits a month** on a key you get by signing up. One
`tavily_search` spends one credit for the URL discovery; fetching the pages costs
nothing but the HTTP round trips, because the tool does that itself
([research_tools.py](../agent/explore/research_tools.py)).

Held against everything else here, that budget is comfortable and genuinely
scarce at the same time. Comfortable next to the *model* pool — twenty requests
a day per flash model per account
([5.4](05-providers.md#54-current-free-tier-limits)) — so searching is not what a
research run exhausts first. Scarce in that credits do not refill until the month
turns, and there is no cooldown to wait through: an exhausted key is exhausted.

**So the pool pattern applies to search too.** `TavilyPoolRouter` holds one
account per key and fails over on rate limits and errors, exactly as the model
router does for providers ([3. The pool model](03-pool-model.md)). Keys are
`TAVILY_API_KEY_1`, `_2`, ... in `llm_router/.env` and are picked up without
touching config.

**Today the pool holds one account**, which means failover has nowhere to go. A
run says so once at startup rather than letting you find out at the wall
([check_pool](../agent/explore/research_tools.py)).

### 15.2.1 A capability is a fact to probe, not to infer

The lesson worth keeping from the search this replaced
([15.9](#159-what-the-grounded-gemini-search-was)), because it generalises past
the code that taught it.

That search ran inside the Gemini API, and which pool members could use it was
**not** predictable from the model family. Probed against the live API: every
`gemini-*` could both search and open a URL; every `gemma-*` searched perfectly
well and refused `url_context` with a `400`. Reasoning from the name — *Gemma is
not a Gemini, so assume no built-in tools* — excluded Gemma from searching and
threw away most of the pool's real search capacity, because Gemma carries 1,500
requests a day against the flash tier's twenty.

Re-probe before widening any capability list. The failure mode is a member that
`400`s every call and cools down an account that was never at fault.

## 15.3 Why the search tool fetches the page

`tavily_search` does not return search results. It asks Tavily for URLs, fetches
each one over HTTP, converts it to markdown, and returns **that**. The model
reads the page.

This is the whole reason the search was replaced, and the argument is measured
rather than aesthetic. The previous tool returned a *summary* of what a search
had found, and opening the real page was a second tool the agent could choose to
call. It never chose to: a recorded run made 13 searches and 0 reads, and wrote a
report full of exact figures — instance counts, percentages, version numbers —
not one of which had been traced to a source
([15.7](#157-measured-against-a-reference-research-agent)).

**The fix is not a firmer instruction.** It is a tool that does not offer the
failure. There is no longer a read step to skip.

### 15.3.1 What that costs, and the two guards on it

A page is large. Returning them whole is what upstream does and it is not safe
here, because the conversation carrying them is routed against a 128,000-token
floor and one unlucky documentation page can displace everything the agent had
already learned.

- **Each page is cut at 20,000 characters** (~5,000 tokens), and the cut says so
  — a truncated source that announces itself is very different from one that
  appears to end mid-sentence.
- **The orchestrator never searches.** Pages land in the researcher sub-agent's
  context, and what comes back to the orchestrator is its findings rather than
  its raw fetches ([15.8](#158-the-deep-research-port)). That is what makes
  fetching whole pages affordable at all.

A fetch that fails — a 403, a timeout, a paywall — comes back as readable text
naming the error, with the URL and title intact. The agent can act on "this page
will not open"; it cannot act on a traceback.

## 15.4 Which account serves a search

`TavilyPoolRouter` walks its accounts in order and returns the first one not in
cooldown. A failure benches that account with exponential backoff capped at five
minutes — sixty seconds for anything that looks like a rate limit or a quota,
thirty for everything else — and the search retries on the next account rather
than failing the run.

That is deliberately simpler than the model router, which sorts by a
hand-assigned priority and filters by context size
([4. Failover](04-failover.md)). Neither applies here: Tavily accounts are
interchangeable, there is nothing to prefer between two keys, and a search has no
context window. Ordering that carries no information is ordering to maintain.

## 15.5 What it is allowed to do

Same jail as the coding agent, rooted at `workdir`, with one difference:
**it runs no programs at all.**

| | coding agent | explorer |
| --- | --- | --- |
| read / write / edit files | ✓ | ✓ |
| `python`, `pytest` | ✓ | — |
| `git` (by subcommand) | ✓ | — |
| `tavily_search`, `think_tool` | — | ✓ |

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

Per `tavily_search` call:

- **one Tavily credit** against the 1,000/month, for the URL discovery;
- **one HTTP fetch per result** (two by default), off any metered budget — it is
  someone else's server, not a pool account;
- **one model call** in the sub-agent that asked, against the pool's daily
  request budget. This is the scarce one.

`think_tool` costs a model call and nothing else. A whole run costs an
orchestrator conversation plus one sub-agent conversation per topic, which is the
trade [15.8.3](#1583-what-it-costs-and-what-was-given-up) is about and which
nothing has measured yet.

The scarce resource is still the model call that wraps the search rather than the
search itself — the same shape as before, for a different reason.

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

### 15.7.2 The one difference that was not copied, and then was

**Their search tool reads the page; ours returned a summary and hoped.** Copying
it was rejected first, on the grounds that every fetch here is a model call
against a daily request budget and making each search cost two would halve the
questions a run could ask. The prompt would carry the rule instead, and
[research_trajectory](../evals/research_trajectory.py) would check whether it was
followed — with the note that *if the check keeps failing, the prompt is the
wrong instrument and the tool is the right one*.

**It was copied.** Two things changed the arithmetic. Tavily arrived, so the
fetch is an HTTP request rather than a model call and the doubling never
happens. And the sub-agent split means the pages land somewhere that is thrown
away, so paying for them in context is bounded
([15.3.1](#1531-what-that-costs-and-the-two-guards-on-it)).

Per-topic sub-agents were also rejected here and adopted in
[15.8](#158-the-deep-research-port), for a reason that reads backwards until you
see it: a sub-agent looked like pure added cost, and it is what makes fetching
whole pages affordable.

## 15.8 The deep-research port

*Branch `harness/deep-research`. [15.7](#157-measured-against-a-reference-research-agent)
argued the explorer's method lost to LangChain's reference agent on every axis a
run could measure. This is the branch that stops arguing and runs theirs.*

The port is close to verbatim — `RESEARCH_WORKFLOW_INSTRUCTIONS`,
`SUBAGENT_DELEGATION_INSTRUCTIONS` and `RESEARCHER_INSTRUCTIONS` from
`langchain-ai/deepagents-quickstarts` (MIT), plus `tavily_search` and
`think_tool` ([deep_prompts.py](../agent/explore/deep_prompts.py),
[research_tools.py](../agent/explore/research_tools.py)). This repo already
ports `deepagents-code`'s prompt for the coding agent; this is the same move on
the research side.

### 15.8.1 The three changes that matter

| | Before | Now |
| --- | --- | --- |
| Search | `web_search` returns a Gemini model's **summary**; `read_url` opens the page and is optional | `tavily_search`: Tavily finds URLs, httpx fetches each, markdownify converts — **the page is what reaches the model** |
| Reflection | none | `think_tool` after every search: what did I find, what is missing, do I stop |
| Shape | one agent, one conversation, all topics | orchestrator + `research-agent` sub-agent; the orchestrator plans, delegates, consolidates citations and writes the report, and never searches |

**The first is the one this branch exists for.** A recorded run made 13 searches
and 0 `read_url` calls, and wrote a report of exact figures none of which had
been traced to a page. The fix is not a firmer instruction; it is a tool that
does not offer the failure. Verified live: one search returned 29,477 characters
across two results, both fetched, no summary in between.

The third has a second benefit that is specific to this pool: the sub-agent's
raw page dumps stay in the sub-agent's context. Fetching whole pages is only
affordable because the orchestrator never sees them.

### 15.8.2 What this pool forced us to change

Four deviations, each marked `ADAPTED` in the ported prompt so the next reader
can diff against the source rather than guess:

1. **`/research/` rather than the workdir root.** Upstream writes
   `/research_request.md` and `/final_report.md` at the root. Here the workdir is
   a project a coding agent then works in, and a report at the root lands in the
   diff it produces. The command's summary names the `/research/*.md` notes a
   run wrote, so this is also what makes a delegated report come back as one
   ([16](16-delegation.md)).
2. **A pool, not a client.** Upstream builds one `TavilyClient`. Search here goes
   through `TavilyPoolRouter`, so an account at its monthly credit wall fails
   over instead of ending the run — the argument the model pool already rests on.
   **Today the pool holds one account**, so there is nothing to fail over to; the
   run warns about that once rather than discovering it at the wall.
3. **Pages are clipped at 20,000 characters.** Upstream returns them whole. One
   documentation page can outweigh everything the agent had learned, and the
   conversation carrying it is routed against a 128,000-token floor. The cut says
   it is a cut.
4. **The report is named against collision.** Upstream is single-shot; this
   explorer answers repeated delegations into one workdir, so a second question
   would overwrite the first's `final_report.md`.

### 15.8.3 What it costs, and what was given up

A research task is now an orchestrator conversation **plus** a sub-agent
conversation, where it used to be one. Against that, the sub-agent's context
holds the pages and the orchestrator's does not, so the totals are not
obviously worse — and nothing has measured them yet. `search_accounts` and the
per-run trace are in the record; the number to read first is `tokens_in`.

**One thing was given up.** The old prompt told the agent to write files as it
went, so a run that died halfway left half its findings. The orchestrator
synthesizes *after* its sub-agents return, so the report is necessarily the last
thing written and a crash before it leaves only `research_request.md`. That is
upstream's design rather than drift, so
[research_trajectory](../evals/research_trajectory.py) records it instead of
failing the run for it — but it is a real regression in crash-resilience for an
agent meant to run unattended, and it is the first thing to revisit if a long
run dies late.

## 15.9 What the grounded-Gemini search was

*Deleted, and recorded here because the reasoning outlived the code and one of
the lessons generalises.*

Before Tavily, searching went through the Gemini API's own grounding: a pool
member was asked a question with the `google_search` tool bound, and it searched
and answered. `read_url` did the same with `url_context` for one named page. It
cost **no third-party credits at all** — 5,000 grounded searches a month, free,
on keys this project already held — which is why it was built and why deleting it
was not obvious.

Three things it got right, and they are why the replacement had to keep them:

- **Citations come back out of band.** Grounding metadata is not in the
  conversation, so a model that searches with the tool *bound* has no URLs to
  write down. Routing the search through a tool call put the source list into a
  `ToolMessage`, as text the model could copy. Every source in a note existed
  because of that decision.
- **The citations are redirect links** (`vertexaisearch.cloud.google.com/...`),
  useless to a reader and unopenable, so each was resolved with a `HEAD`
  request — in parallel, and best-effort, because a dozen serial waits on other
  people's servers is a minute of a metered run.
- **A search with no sources was labelled as such**, because a grounded call that
  answered from memory and one that looked something up are indistinguishable
  otherwise.

**What it got wrong is the one thing that mattered.** What reached the model was
a summary, and reading the real page was optional. The agent never took the
option ([15.3](#153-why-the-search-tool-fetches-the-page)).

It was kept for one commit as a fallback against Tavily's free tier running out,
and then deleted: a fallback nobody should fall back to is a second prompt and a
second tool set to keep true, in exchange for restoring a failure mode already
measured once. `git log` has it.

---

**Previous:** [← 14. Quota panel](14-quota-panel.md) · **Next:** [16. Delegation →](16-delegation.md)
