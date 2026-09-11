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
record ([prompts/system.md](../agent/explore/prompts/system.md)). The explorer's
closing message is not the deliverable: it is printed for whoever ran the
command, and says which files to open. The files are the deliverable, because the thing that reads them next is an agent that was not
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
([agent/runtime/web.py](../agent/runtime/web.py)).

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
([check_pool](../agent/runtime/web.py)).

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
| `read_file`, `write_file`, `edit_file` | ✓ | ✓ |
| `write_todos`, `task` | ✓ | ✓ |
| `ls`, `glob`, `grep` | ✓ | — |
| `execute` — `python`, `pytest`, `git` | ✓ | — |
| `tavily_search`, `think_tool`, `research_status` | — | ✓ |

The coding agent needs a shell to close its own loop — write a test, run it,
react to the result. A researcher has no loop to close, so its backend is
the jail alone ([`JailedFilesystemBackend`](../agent/runtime/backend.py)): it
cannot run a program, and because it cannot, the framework never offers
`execute` at all. Nothing is lost, and the blast radius of an unattended run
drops to the files it writes
([6.2](06-agent.md#62-the-blast-radius)).

`ShellAllowListMiddleware` is not installed here, unlike on the coding agent:
there is no shell for it to mirror.

**Where the agent's behaviour is written down.** Everything above is a
property of *text*, and that is a rule this agent is held to rather than an
observation about it. The package is two Python files and two directories of
Markdown:

| what | where |
| --- | --- |
| what the agent is for, what it cannot do, when it is finished | [prompts/system.md](../agent/explore/prompts/system.md) |
| the research method, ported from upstream | `prompts/workflow.md`, `delegation.md`, `researcher.md` |
| what each tool is for, and how to use it | `tool_descriptions/<tool>.md`, one file per tool |
| the budgets, the tools taken away, the framework prose removed, and the assembly | [agent.py](../agent/explore/agent.py), read top to bottom |
| running it | [`__main__.py`](../agent/explore/__main__.py) |

Every Markdown file is a template filled from one dictionary
(`template_values` in [agent.py](../agent/explore/agent.py)): the research
directory, the budgets, today's date, and the three sections shared with the
coding agent. The Python left over is assembly: fill the files, filter a list,
hand the result to `create_deep_agent`. Nothing in it decides what the agent *does*, and a
change to how the agent works should be a change to a Markdown file. When a
behaviour was implemented as code here it has been removed again — see the
review in [15.5.4](#1554-the-review-at-the-end), which was a wrapper for one
commit and is now four sentences in the prompt.

### 15.5.1 The surface is chosen, not inherited

*The rows marked — above are the change. Read after the first traces of the
decision-led runs ([15.8.4](#1584-decision-led-research-candidate)): the agent
had been handed a coding agent's tools and was using them as one.*

`create_deep_agent` installs one suite on everything built with it —
`write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`,
`execute`, `task` — and that suite is shaped for the job [6](06-agent.md) does:
find your way around a repository you were dropped into, then change it. Three of
them are removed here ([agent.py](../agent/explore/agent.py)) and the fourth is
never offered, each for a reason about the same scarce thing, a model call
against a per-day request budget:

- **`ls`, `glob`, `grep` are repository discovery**, and this agent has two
  other sources of paths. `research_status` says what its own research holds
  ([15.5.2](#1552-the-research-directory-and-how-to-see-it)), and a project file
  worth reading is one the *request* named — the caller is
  usually the coding agent, which knows the repository already
  ([16](16-agent-protocol.md)). Searching a repo with the research budget is
  doing the other agent's job with the wrong account.
- **`execute` used to be offered by the framework and refused by the backend**
  on every command. An always-refused tool can only ever cost a step to learn
  what the schema could have said — this project's own measured lesson, that a
  description advertising a capability the backend does not have causes failed
  calls ([agent/runtime/tools.py](../agent/runtime/tools.py)). The backend is
  now the jail without the shell, and the framework offers `execute` only on a
  backend that can execute, so there is nothing left to hide.

**The project tree went with them.** The coding agent's prompt opens with a
depth-limited listing of the repository ([agent/code/context.py](../agent/code/context.py))
because two or three tool calls spent discovering the shape of a project are the
most expensive calls in a run. That argument does not transfer: this agent is
*given* its question, and a tree in its prompt is a hundred lines inviting
exactly the exploration the surface no longer supports.

Four descriptions are rewritten for the same reason the tools are. Upstream's
`read_file` explains itself in terms of *codebase exploration*; `write_file`
opens by telling the agent to prefer editing something that already exists; and
`write_todos` closes by insisting the deliverable is the final message — which
here is false, since the closing message only points its caller at the files
([`__main__.py`](../agent/explore/__main__.py)) and the files are the deliverable
([15.1](#151-what-it-is-for)). A tool description contradicting the system prompt
is worse than a thin one.

**Why a middleware rather than a `HarnessProfile`.** Upstream's documented way to
drop a built-in is to register a profile against the model. Profiles are keyed by
provider or `provider:model`, and the model here is one `RouterChatModel` shared
by every agent in this repo — so no registration narrow enough to describe the
explorer exists, and one that matched would take the coding agent's shell with
it. Filtering the request applies to exactly the agent it is installed on. The
researcher sub-agent gets its own copy, because the framework hands it its own
copy of the filesystem tools.

### 15.5.2 The research directory, and how to see it

**Which directory is a parameter of the run**, not a constant:

```bash
python -m agent.explore ./project --research-dir research/cv-spain < question.md
python -m agent.explore ./project --research-dir research/cv-spain < follow-up.md   # continues it
```

That is what makes a second question a continuation rather than a collision.
Point two investigations at one directory and their notes interleave under names
nobody chose to be distinct, and "is this note about my question?" becomes
something the agent has to infer from filenames; point the second run at the
first one's directory deliberately and its notes are there to be listed, read,
extended and cited. The prompts and tool descriptions write the path as
`/{research_dir}/`, filled in with every other template value.

**What is in it comes from `research_status`**
([agent.py](../agent/explore/agent.py)), a tool: every note's
path and size, asked for when the answer is wanted.

That is a reversal. It was first built as a listing injected into the system
prompt on *every* model call, on the argument that the directory is the agent's
own output and therefore, unlike the coding agent's project tree, changes exactly
when it starts mattering. The argument is still true and no longer decides it:
the injection was paid for on every call including the great majority that never
needed it, it fed the prompt this agent already had too much of
([15.5.3](#1553-what-the-framework-says-that-is-not-true-here)), and it could
only ever answer *what notes exist* — where the next question is always *what is
in this one*, which is `read_file` and a path. A tool is asked, and it is the
seam where "list the paths" becomes "say what state the research is in" without
touching a prompt.

The reasons it exists at all are unchanged: the orchestrator has to know whether
a `final_report.md` about someone else's question is sitting there before it
writes over it ([15.8.2](#1582-what-this-pool-forced-us-to-change)), parallel
researchers must not choose the same filename, and after summarization a note
written an hour ago is nowhere in the conversation but is still on disk. An empty
directory says so rather than returning nothing: "nothing is written yet" is the
state in which a crash costs the whole run
([15.8.3](#1583-what-it-costs-and-what-was-given-up)).

### 15.5.3 What the framework says that is not true here

Removing tools left the framework describing an agent this is not.
`create_deep_agent` appends its own prompt sections, and after the filtering
above four of them were wrong in a way that costs more than tokens:

| section | what it said | why it goes |
| --- | --- | --- |
| `FILESYSTEM_SYSTEM_PROMPT` | lists `ls`, `glob`, `grep` among the tools available | three of the six are not offered |
| `TASK_SYSTEM_PROMPT` | 3,700 characters on when to spawn a sub-agent | the ported research workflow answers this three sections earlier |
| `WRITE_TODOS_SYSTEM_PROMPT` | "write your final answer in the message AFTER your last `write_todos` call … The user wants the result" | the answer is a file; the closing message only points its caller at it ([`__main__.py`](../agent/explore/__main__.py)) |
| `BASE_AGENT_PROMPT` | "The user can see your responses and tool outputs in real time", plus progress updates and clarifying questions | nobody is watching, and the headless preamble says so two thousand characters earlier |

The last two are the expensive ones. A run that recites its report into a reply
pays for the report twice — once into the file that is the deliverable and once
into a message whose reader is about to open the file anyway — and that is a measured behaviour of the runs in
[15.8.4](#1584-decision-led-research-candidate), not a hypothetical. The same
correction is made on the researcher sub-agent, whose instructions now say the
reply is a pointer to its note and cap it at 200 words.

They are matched as the **imported constants**, so an upstream rewording makes
the removal fail loudly rather than half-apply
([agent.py](../agent/explore/agent.py)).

**What this arithmetic comes to.** Measured on the assembled agent, before any
conversation: system prompt plus every tool schema is 26,864 characters, against
47,187 for the same agent with the framework's suite and prose left as they
come — a 43% cut, most of it text that described tools it does not have. Whether a shorter, truer
prompt produces better research is exactly what
[the v3 comparison](../evals/results/reports/2026-09-10-explore-machintl.md)
says has not been shown.

**One hole this closed.** `create_deep_agent` adds a `general-purpose` sub-agent
whenever the caller declares none, and the default inherits the framework's
filesystem tools *without* the middleware above — so an agent with no `grep`
could delegate to one that had it, under no search budget either. It is declared
explicitly now, with the same surface and the same limit
([agent.py](../agent/explore/agent.py)). A tailoring that only holds for the
agent in front is not a tailoring.

**Unmeasured.** All of this is argued from traces and from a deterministic
character count, not from a result.

### 15.5.4 The review at the end

*Step 6 of the workflow, and the only step whose absence is invisible in the
output: an unreviewed report reads exactly like a reviewed one.*

The run does not end when the report is written. It ends when the agent has read
the request back off disk, read the report it saved, and answered **one question
per thing the request asked for** — answered, partly, or not answered, naming
the section that answers it — corrected what it found with `edit_file`, and
saved that account as `/research/review.md`
([prompts/workflow.md](../agent/explore/prompts/workflow.md)).

Three things about the shape of it:

**It is asked against the request, not against the prose.** A long report is not
evidence that the question was answered, and the failure this catches is the one
the runs in [15.8.4](#1584-decision-led-research-candidate) actually made: a
report that answers a neighbouring question well while the asked one goes
untouched. The review is also where a decision-critical claim gets named as
resting on a vendor's own page, a single source, or an inference — which is
precisely the defect the v3 comparison found and no reader of the report alone
could see.

**Corrections are edits, not rewrites.** `write_file` on a thirty-thousand
character report costs the whole report in output tokens and drops whatever the
model does not retype. This is the job `edit_file` exists for, and until this
step existed it had none — recorded runs called it zero times
([15.5.1](#1551-the-surface-is-chosen-not-inherited)).

**Nothing in the harness makes it happen.** There was, for one commit: a
wrapper that noticed a run ending without a review and asked once more. It is
gone, and the reasoning for removing it is the same reasoning this whole page
keeps arriving at from the other side — *the behaviour of an agent belongs in
the words it is given, not in the code around it*. A run that only reviews
because a Python function noticed is a run whose operator has to read Python to
know what the agent does.

So the system prompt says it instead, in the place a model reads before it
decides it is finished: the review is the last thing you do, the report being
written is step 5 and not the end, and *if you are about to write a final
message and there is no review note, you are not finished*
([prompts/system.md](../agent/explore/prompts/system.md)).

**And it is counted.** `research_trajectory` gains one check: that a review note
was written
([research_trajectory.py](../evals/research_trajectory.py)). That is the whole
arrangement: **the prompt asks, and the eval counts.** If runs keep shipping
without a review the check says so, and the answer is better words — or, if
words demonstrably will not do it, a mechanism argued for by that evidence
rather than by anticipation.

### 15.5.5 What each call carries

What the model sees is assembled, per call, from a few places, and knowing
which is how to change what the agent does:

| | orchestrator | `research-agent` |
| --- | --- | --- |
| system prompt | `system.md`, `workflow.md`, `delegation.md`, then the framework's list of sub-agents | `researcher.md` |
| tool schemas | one per tool, each described by `tool_descriptions/<name>.md` | the same |
| conversation | the request, its own tool calls, and each sub-agent's *reply* | the brief it was given, its searches, and the pages they returned |
| what outlives it | the files in the research directory | the note it saved there |

The framework summarizes a conversation that grows too long; the files are
never summarized. That is the design: pages are read in a sub-agent's
conversation and end with it, the orchestrator sees only a pointer to the note
the sub-agent saved, and anything any agent needs later — the plan, the
findings, the report, the review — is on disk, where `research_status` lists it
and `read_file` opens it.

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
`think_tool` ([prompts/](../agent/explore/prompts/),
[agent.py](../agent/explore/agent.py)). This repo already
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

Each is marked `ADAPTED` in the prompt files, so the next reader can diff
against the source rather than guess. The first four are forced by this pool:

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

The rest were argued from this project's own runs:

5. **The orchestrator is told the search budget too.** Upstream bounds only the
   sub-agent; a pool bounded by requests per day is better served by an
   orchestrator that knows the numbers than by one left to infer them.
6. **No `read_url`.** `tavily_search` returns the page, so there is no second
   tool to skip ([15.7.2](#1572-the-one-difference-that-was-not-copied-and-then-was)).
7. **Decision-led briefs and saved findings**
   ([15.8.4](#1584-decision-led-research-candidate)).
8. **A tool list that matches the tools.** Upstream's `ls /research` becomes
   `research_status`, and the researcher is told the tools it actually holds
   ([15.5.1](#1551-the-surface-is-chosen-not-inherited)).
9. **A review step** after the report
   ([15.5.4](#1554-the-review-at-the-end)).
10. **The reply is a pointer, not a second copy of the findings**, for the
    researcher and the orchestrator alike
    ([15.5.3](#1553-what-the-framework-says-that-is-not-true-here)).

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

### 15.8.4 Decision-led research candidate

Branch `codex/improve-explore-research` adapts the workflow to the Machintl
reference business-analysis session. Before delegation, the orchestrator saves
a plan tying the reader's decision and constraints to evidence requirements.
Independent scenarios stay separate; shared constraints such as legality or
economics get scoped workstreams when they could change the decision. Briefs
carry context and a unique findings path, and researchers are asked to save
partial evidence before returning. The numeric delegation and search limits
remain unchanged. `ToolCallLimitMiddleware` now enforces the five-search limit
separately for each researcher invocation; it blocks further searches while
leaving file writes and synthesis available. The control run exceeded that
limit and accumulated large histories that then hit per-minute token quotas.

The report leads with findings for each scenario, uses comparable tables,
distinguishes sourced facts, vendor claims and estimates, and names validation
steps that could change its recommendation. The orchestrator receives the
current research date explicitly. Researchers and the synthesis use direct
source links: the first candidate reused note-local citation numbers in the
combined bibliography, binding industrial claims to retail sources. Three sources are sufficient only
when they cover the assigned questions. These are instructions, not guarantees
of citation quality or crash recovery. The
[capability queue](../.codex/artifacts/2026-09-10-explore-capability-issues.md)
records the reference, live evidence and future eval contracts; no claim of
parity with Claude follows from copying its method.

The [live comparison](../evals/results/reports/2026-09-10-explore-machintl.md)
completed one control and two candidate reports. The revised candidate saved
sector findings and corrected the date and citation-number collisions, but
still made unsupported legal and commercial claims. It did not perform the
requested follow-up verification. The candidate is **not promoted**: one task
cannot establish a quality gain, and the last run also changed models after
daily quota exhaustion. The deterministic search-budget and routing checks
pass; they establish infrastructure behavior, not research quality.

Reading those traces produced one further change, argued separately because it
is about the agent's tools rather than its method: the surface it is offered is
now chosen rather than inherited from the framework
([15.5.1](#1551-the-surface-is-chosen-not-inherited)). Also unmeasured.

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
