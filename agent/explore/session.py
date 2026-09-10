"""One exploration over one workdir: the web in, files out.

**This branch runs LangChain's deep-research agent, not our own prose.** The
explorer's own prompt lost to it on every axis a recorded run could measure --
13 searches, 0 sources opened, one file written at the end, every query phrased
as keywords ([15.7](../../docs/15-explorer.md#157-measured-against-a-reference-research-agent))
-- so the shape here is upstream's and the deviations are the ones this pool
forces ([deep_prompts.py](deep_prompts.py), [research_tools.py](research_tools.py)).

The three things that changed, and what each was for:

1. **An orchestrator over a researcher sub-agent.** The orchestrator plans,
   delegates, consolidates citations and writes the report; it never searches.
   The sub-agent's raw page dumps stay in the sub-agent's context, which is
   what makes fetching whole pages affordable at all.
2. **`tavily_search` returns the page, not a summary of it.** The old
   `web_search`/`read_url` pair made reading optional and the agent always
   declined. This tool has no such affordance.
3. **`think_tool` after every search.** A forced pause between retrieving and
   deciding to retrieve again. The failure it addresses is thirteen searches
   that never asked whether the twelfth had added anything.

And one that is not upstream's, added after reading the traces of the runs
above: **the tool surface is chosen rather than inherited**
([tools.py](tools.py)). `create_deep_agent` hands every agent a coding agent's
suite; this one is offered no `ls`, `glob`, `grep` or `execute`, and reads its
own directory from the system prompt instead ([notes.py](notes.py)). The project
tree went with them: what this agent should read of a repository is what the
brief names, because the agent that owns the repository is the one delegating
([15.5](../../docs/15-explorer.md#155-what-it-is-allowed-to-do)).

What did *not* change: the loop, the jail, the pool, the failover, and the fact
that the deliverable is a file on disk that outlives the run. The A2A handler
above it ([a2a.py](a2a.py)) is untouched -- it still collects `/research/*.md`
and reports them as artifacts, which is why the report is written there rather
than at the workdir root the way upstream does.

The grounded-Gemini search this replaced (`web_search`/`read_url`, a Gemini
model searching on the agent's behalf and returning its summary) is deleted
rather than kept as a fallback. It cost no third-party credits, which was the
argument for keeping it, and it is also the thing that produced a run of 13
searches and 0 opened sources -- a fallback nobody should fall back to is just
a second prompt to keep true. `git log` has it
([15.9](../../docs/15-explorer.md#159-what-the-grounded-gemini-search-was)).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from langchain_core.messages import HumanMessage
from langchain_core.tracers.context import collect_runs

from agent.code import trace as run_trace
from agent.code.session import CONTEXT_FLOOR, RECURSION_LIMIT, _trace_locator
from agent.explore import deep_prompts, notes, prompt, research_tools
from agent.explore import tools as tool_surface
from agent.explore.research_tools import NoSearchPool, check_pool  # noqa: F401
from agent.runtime.trace import tracer_from_env

logger = logging.getLogger("harness.explore")

# Where the notes go unless the caller names somewhere else. The launcher
# creates it, because an agent that has to `mkdir` before its first write spends
# a step discovering that and sometimes writes to `/` instead.
#
# It is a *parameter* rather than a constant because a directory is what
# separates one investigation from the next, and what lets a second run continue
# the first: point two questions at one directory and their notes interleave
# under names nobody chose to be distinct
# ([notes.py](notes.py), [15.5.2](../../docs/15-explorer.md#1552-the-research-directory-and-how-to-see-it)).
RESEARCH_DIR = notes.DEFAULT_DIR

# Upstream's numbers, kept. The defaults bias to one sub-agent anyway, and
# lowering a documented limit before observing it spend anything would be
# guessing at a budget rather than measuring one.
MAX_CONCURRENT_RESEARCH_UNITS = 3
MAX_RESEARCHER_ITERATIONS = 3
MAX_SEARCHES_PER_SUBAGENT = 5

# Files the run produces that answer nothing: the question it was given and the
# plan for answering it. A run that wrote only these has not researched anything,
# and there is nothing for a review to review.
_NOT_A_DELIVERABLE = ("research_request", "research_plan", "review")

# The last step of the workflow, asked again when the run finished without it.
#
# **Why this exists as code and not only as prose.** Every skipped step in this
# agent's recorded history was a step the prompt already asked for: the control
# run exceeded a stated search budget, the v2 candidate skipped the verification
# round it was told to spend, the v3 candidate never wrote the plan note. Step 6
# is the one that decides whether the deliverable answers the question, so it is
# the worst one to leave to a model that has just decided it is finished.
#
# It asks **once**, and only when the run produced something to review. If the
# agent declines, the run ends anyway: this can cost a model call, and it must
# never be able to hold a session open.
REVIEW_FOLLOW_UP = (
    "Before this session ends: you have not written the review. Step 6 of your "
    "workflow is not optional and it is not a formality.\n\n"
    "Read the research request from disk, then read the report you saved. Go "
    "through what the request actually asked for, one item at a time, and say "
    "for each whether it was answered, partly answered, or not answered, naming "
    "the section that answers it. Correct what you find with `edit_file` rather "
    "than rewriting a file whole. Then save the review as described in "
    "\"Reviewing your own output\".\n\n"
    "If you conclude the request *was* fully answered, that is a fine outcome — "
    "write the review saying so, and say what you checked to be sure."
)


def orchestrator_prompt() -> str:
    """Upstream's two orchestrator sections, joined the way upstream joins them.

    Left saying `/research/`. `prompt.build` retargets the whole assembly at the
    end, and a section that retargeted itself first would be substituted twice --
    `/research/cv-spain/cv-spain/final_report.md`, which is a real path this
    caught ([notes.retarget](notes.py)).
    """
    return (
        f"Today's research date is {date.today().isoformat()}. Use this date for "
        "the report; distinguish it from publication dates of sources.\n\n"
        + deep_prompts.RESEARCH_WORKFLOW_INSTRUCTIONS
        + "\n\n" + "=" * 80 + "\n\n"
        + deep_prompts.SUBAGENT_DELEGATION_INSTRUCTIONS.format(
            max_concurrent_research_units=MAX_CONCURRENT_RESEARCH_UNITS,
            max_researcher_iterations=MAX_RESEARCHER_ITERATIONS,
            max_searches_per_subagent=MAX_SEARCHES_PER_SUBAGENT,
        )
    )


def researcher_subagent(tools: list, research_dir: str = RESEARCH_DIR) -> dict:
    """The `research-agent` sub-agent, as upstream declares it.

    It gets its own copy of the surface middleware because the framework hands
    it its own copy of the filesystem tools -- without it the researcher would
    be the one thing in this system still able to `grep` the project.
    """
    from langchain.agents.middleware import ToolCallLimitMiddleware

    return {
        "name": "research-agent",
        "description": ("Delegate research to the sub-agent researcher. Only "
                        "give this researcher one topic at a time."),
        "system_prompt": notes.retarget(
            deep_prompts.RESEARCHER_INSTRUCTIONS.format(
                date=date.today().isoformat(),
                max_searches=MAX_SEARCHES_PER_SUBAGENT),
            research_dir),
        "tools": tools,
        "middleware": [
            # Keep the stated budget real, but let the researcher save findings
            # and explain gaps after search is exhausted. Per invocation.
            ToolCallLimitMiddleware(
                tool_name="tavily_search", run_limit=MAX_SEARCHES_PER_SUBAGENT,
                exit_behavior="continue"),
            tool_surface.ToolSurfaceMiddleware(research_dir),
        ],
    }


def general_purpose_subagent(tools: list, research_dir: str = RESEARCH_DIR) -> dict:
    """Upstream's fallback sub-agent, held to this agent's tool surface.

    `create_deep_agent` adds a `general-purpose` sub-agent whenever the caller
    does not declare one, and the only way to decline it is a harness profile
    keyed to the model -- which here is the pool, shared with every other agent
    in this repo ([tools.py](tools.py)). So it is declared rather than left to
    the default.

    Declaring it is also the fix for a hole. The default inherits the framework's
    filesystem tools and *not* our middleware, so an agent that has no `grep`
    could delegate to one that has -- and to one under no search budget either.
    Same surface, same limit, or the tailoring only holds for the agent that
    happens to be in front.
    """
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    spec = dict(GENERAL_PURPOSE_SUBAGENT)
    spec["description"] = (
        "General-purpose sub-agent with this agent's own tools and a fresh "
        "context. It searches the web and reads files; it cannot list, glob or "
        "grep the project, and it has no shell. Prefer `research-agent` for "
        "anything that is research.")
    spec["tools"] = tools
    spec["middleware"] = researcher_subagent(tools, research_dir)["middleware"]
    return spec


def _notes_by_kind(research_path: Path) -> tuple:
    """`(reviews, deliverables)` under the research directory, path -> size.

    Reviews are matched on the filename the prompt asks for -- `review.md`, or
    `review-<topic>.md` when one about another question is already there -- so a
    second question in the same directory gets its own review rather than
    counting the first one's.
    """
    reviews, deliverables = {}, {}
    if not Path(research_path).is_dir():
        return reviews, deliverables
    for note in sorted(Path(research_path).rglob("*.md")):
        stem = note.stem.lower()
        if stem.startswith("review"):
            reviews[note] = note.stat().st_size
        elif not stem.startswith(_NOT_A_DELIVERABLE):
            deliverables[note] = note.stat().st_size
    return reviews, deliverables


def invoke_with_review(agent, state, config, research_path: Path) -> dict:
    """Run the agent, and ask once more if it finished without reviewing.

    Returns the final state, whichever invocation produced it. The follow-up is
    a second `invoke` over the conversation the first one left, which is exactly
    what a person reading the closing message would do -- no graph surgery, and
    the review lands in the same trace and the same jail as everything else.
    """
    before_reviews, _ = _notes_by_kind(research_path)
    final = agent.invoke(state, config)

    reviews, deliverables = _notes_by_kind(research_path)
    reviewed = any(before_reviews.get(path) != size
                   for path, size in reviews.items())
    if reviewed or not deliverables:
        if not deliverables:
            logger.info("No deliverable was written, so there is nothing to "
                        "review; ending the run.")
        return final

    logger.info("The run finished without a review. Asking for step 6 once.")
    messages = list(final.get("messages") or []) + [
        HumanMessage(REVIEW_FOLLOW_UP)]
    final = agent.invoke({"messages": messages}, config)

    after, _ = _notes_by_kind(research_path)
    if not any(before_reviews.get(path) != size for path, size in after.items()):
        # Said, not enforced. A research note nobody reviewed is still worth
        # more than a run held open arguing about it.
        logger.warning("The run ended without a review note even after being "
                       "asked. Its report has not been checked against the "
                       "request it answers.")
    return final


def build_agent(workdir: Path, model, pool, *,
                floor: int = CONTEXT_FLOOR, members: int = 0,
                research_dir: str = RESEARCH_DIR,
                extra_middleware: Optional[Sequence] = None):
    """The compiled research orchestrator over a jailed backend and the pool.

    `research_dir` is a path *inside* the workdir -- `research`, or
    `research/2026-09-cv-spain` for one investigation among several. An existing
    one is continued rather than replaced: its notes are there to be listed,
    read and cited, which is the whole reason the run says which directory it
    is working in.
    """
    from deepagents import create_deep_agent

    from agent.runtime.backend import RestrictedShellBackend

    workdir = Path(workdir)
    research_dir = research_dir.strip("/") or notes.DEFAULT_DIR
    research_path = workdir / research_dir
    research_path.mkdir(parents=True, exist_ok=True)

    # No programs at all, unchanged. `execute` is still installed -- the backend
    # satisfies SandboxBackendProtocol either way -- but every command comes back
    # refused with the backend's own explanation.
    backend = RestrictedShellBackend(root_dir=str(workdir), allowed_programs=(),
                                     allow_git=False, allow_shell=False)

    tools = list(research_tools.make_research_tools(pool).values())
    # The one tool that is neither upstream's nor the framework's. It is what
    # `ls` was doing badly: the agent asks what its own research holds, when it
    # wants to know, rather than being told on every call.
    tools.append(notes.make_status_tool(research_path, research_dir))

    # This project's own preamble first, then upstream's workflow. The preamble
    # carries facts about *this* system that upstream cannot know -- which pool
    # is serving the call, where the jail's `/` is, that nobody is watching --
    # and the workflow carries the method.
    #
    # No project tree. The coding agent's prompt opens with one
    # (agent/code/context.py) because it has to find its way around a repository
    # it was dropped into; this agent is *given* the question, and the paths
    # worth reading belong in the brief that asked it. A tree here is a hundred
    # lines inviting the one thing the tool surface no longer supports.
    system_prompt = prompt.build(
        floor, members=members, research_dir=research_dir,
        extra_sections=[orchestrator_prompt()])

    # Ours, after everything the framework installs: the surface middleware has
    # to run late enough to see the tools the filesystem and subagent middleware
    # inject, which is exactly where `create_deep_agent` puts caller middleware.
    middleware = [tool_surface.ToolSurfaceMiddleware(research_dir),
                  *(extra_middleware or [])]

    return create_deep_agent(
        model=model,
        # The orchestrator holds the same tools upstream gives it, and is told
        # never to use them for research. Kept rather than removed because
        # upstream keeps them, and a checked fact about a run that ignores that
        # instruction is worth more than a run that could not have disobeyed.
        tools=tools,
        system_prompt=system_prompt,
        backend=backend,
        middleware=middleware,
        subagents=[researcher_subagent(tools, research_dir),
                   general_purpose_subagent(tools, research_dir)],
    )


def run_session(model, task: str, workdir: Path, pool, config=None,
                floor: int = CONTEXT_FLOOR, members: int = 0,
                research_dir: str = RESEARCH_DIR,
                trace_path: Optional[Path] = None) -> tuple:
    """Run one exploration. Returns (final_state, trace_written)."""
    config = dict(config or {})
    config.setdefault("recursion_limit", RECURSION_LIMIT)
    workdir = Path(workdir)

    jsonl = tracer_from_env()
    if jsonl is not None:
        config["callbacks"] = list(config.get("callbacks") or []) + [jsonl]

    agent = build_agent(workdir, model, pool, floor=floor, members=members,
                        research_dir=research_dir)

    with collect_runs() as collected:
        final = invoke_with_review(agent, {"messages": [HumanMessage(task)]},
                                   config, workdir / research_dir)

    trace_id, project_id = _trace_locator(collected.traced_runs)
    project_id = run_trace.resolve_project_id(project_id)

    written = None
    if trace_path is not None:
        tree = run_trace.fetch_tree(trace_id, project_id) if trace_id else None
        written = run_trace.write(trace_path, tree, meta={
            "trace_id": trace_id,
            "project_id": project_id,
            "workdir": str(workdir),
            "harness": "explore",
            "research_dir": research_dir,
            "context_floor": floor,
            "eligible_providers": members,
            "search_accounts": len(getattr(pool, "accounts", []) or []),
            "tracing_enabled": run_trace.tracing_enabled(),
        })
        if written:
            logger.info(f"Wrote the run record to {written}")

    return final, written
