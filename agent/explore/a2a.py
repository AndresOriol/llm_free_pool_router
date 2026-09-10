"""The explorer as an addressable agent: its card, and the handler behind it.

This is the server half of the protocol for `agent/explore`. It changes nothing
about how the explorer works -- same prompt, same jail, same web tools, same
session -- and only gives it an address and a way to say what came back.

The explorer's deliverable was already a set of files
([15.1](../../docs/15-explorer.md#151-what-it-is-for)), so the mapping onto A2A
is the one the spec was designed for and not a translation: each research note
becomes an `Artifact` holding a `FilePart` that points at it. What the protocol
adds is the *request* and the *status* -- who asked, for what, and whether it
worked -- which is precisely what the filesystem handoff could never carry, and
what a human sequencing the two runs was supplying by hand.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from langchain_core.messages import HumanMessage

from agent.protocol.types import (AgentCard, AgentSkill, Artifact, FilePart,
                                  Message, Task, TaskState)

logger = logging.getLogger("harness.explore")

CARD = AgentCard(
    name="explore",
    version="0.1.0",
    description=("Researches the open web and writes a cited report to "
                 "`/research/final_report.md`. It reads whole pages, not "
                 "search snippets; it cannot run or change this project. It "
                 "also cannot browse it: name the absolute path of any file it "
                 "should read (`/pkg/module.py`), because it has no `ls`, "
                 "`glob` or `grep` and will not go looking."),
    skills=[
        AgentSkill(
            id="web_research",
            name="Web research",
            description=("Answer a question that cannot be answered from this "
                         "repository — current API limits, a library's real "
                         "signature, what a vendor charges now — and leave a "
                         "cited note behind."),
            tags=["research", "web", "search", "citations"],
            examples=[
                "What are the current free-tier rate limits for the Cerebras "
                "inference API, per model? I need to decide whether it is worth "
                "adding to the pool.",
                "Does langchain-core still expose `BaseChatModel._generate` as "
                "the sync entry point in the version this repo pins, and what "
                "replaced it if not? Our subclass is "
                "`/agent/runtime/chat_model.py`.",
            ],
        ),
    ],
)


def _notes(research_dir: Path) -> dict:
    """Every note in the research directory, by path, with its size."""
    if not research_dir.is_dir():
        return {}
    return {p: p.stat().st_size for p in sorted(research_dir.rglob("*.md"))}


def make_handler(model, workdir: Path, pool, *, floor: int, members: int,
                 recursion_limit: int, research_dir: Optional[str] = None):
    """The handler the registry serves for `explore`.

    Takes the pool and the workdir once, at wiring time, and closes over them --
    so the delegate runs on the *same* provider objects as its caller and shares
    their cooldown, which is the main reason this transport is local at all
    ([16.3](../../docs/16-agent-protocol.md#163-why-the-transport-is-local)).
    """
    from agent.explore.session import (RESEARCH_DIR, build_agent,
                                       invoke_with_review)

    workdir = Path(workdir)
    mount = (research_dir or RESEARCH_DIR).strip("/") or RESEARCH_DIR
    research_path = workdir / mount

    def handle(task: Task) -> Task:
        request = task.history[-1].text if task.history else ""
        if not request:
            return task.advance(TaskState.REJECTED, Message.agent(
                "The request was empty; there is nothing to research."))

        # Only the notes *this* task wrote. Without the before-shot a second
        # delegation reports the first one's files as its own findings, and the
        # caller reads a stale note believing it answers the new question.
        before = _notes(research_path)

        # No callbacks of its own. The delegate runs inside the caller's tool
        # call, so LangChain's run context already hands it the caller's
        # handlers -- including the JSONL tracer -- and a delegation's cost is
        # inside the run's totals without asking
        # ([10. Metrics](../../docs/10-metrics.md)).
        #
        # Adding `tracer_from_env()` here as well seemed harmless and was not:
        # it registers a *second* handler on the same file, so every delegated
        # step is written twice. Caught on the first live run -- the caller's
        # own tools appeared once each and the delegate's `web_search` twenty
        # times for ten searches, which would have doubled the one number the
        # comparison rests on.
        config = {"recursion_limit": recursion_limit}

        agent = build_agent(workdir, model, pool, floor=floor, members=members,
                            research_dir=mount)
        # Same ending as a run started from the command line: the report is
        # checked against the request before the task is reported complete
        # ([15.5.4](../../docs/15-explorer.md#1554-the-review-at-the-end)).
        final = invoke_with_review(agent, {"messages": [HumanMessage(request)]},
                                   config, research_path)

        after = _notes(research_path)
        written = [p for p, size in after.items()
                   if before.get(p) != size]

        for path in written:
            rel = path.relative_to(workdir).as_posix()
            task.artifacts.append(Artifact(
                name=path.name,
                description=f"Research note written for task {task.id[:8]}.",
                parts=[FilePart(uri=rel, name=path.name)],
                metadata={"bytes": after[path]},
            ))

        closing = _closing_text(final)
        task.history.append(Message.agent(closing, task_id=task.id,
                                          context_id=task.context_id))
        return task.advance(TaskState.COMPLETED,
                            Message.agent(closing, task_id=task.id,
                                          context_id=task.context_id))

    return handle


def _closing_text(final) -> str:
    """The delegate's last message, flattened and clipped.

    Clipped hard on purpose. The explorer's prompt is explicit that its closing
    message is not the deliverable and nobody reads it
    ([15.1](../../docs/15-explorer.md#151-what-it-is-for)); letting a rambling
    sign-off into the caller's context would spend the caller's tokens on the
    one part of the run that was never meant to be read.
    """
    messages = (final or {}).get("messages") or []
    if not messages:
        return "The exploration ended without a closing message."
    text = getattr(messages[-1], "content", "")
    if isinstance(text, list):  # content blocks
        text = " ".join(str(b.get("text", "")) for b in text
                        if isinstance(b, dict))
    text = str(text).strip()
    return text[:2000] + ("…" if len(text) > 2000 else "")
