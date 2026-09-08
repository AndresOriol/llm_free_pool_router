"""The improvement agent as an addressable agent: its card, and the handler.

The server half of the protocol for `agent/improve`, and the counterpart of
[agent/code/a2a.py](../code/a2a.py) and
[agent/explore/a2a.py](../explore/a2a.py). It changes nothing about how the
agent works and only gives it an address and a way to say what came back.

**What a pass delivers is a change to the ledger.** Not a commit -- it cannot
write one -- and not a report either. The deliverable is which issues moved and
where to, so each issue this pass created or amended becomes an `Artifact`
holding a `FilePart` pointing at its JSON, with the status in its metadata.
That is the one thing a caller needs in order to decide whether to look.

An agent whose whole job is to *cause* work elsewhere is also the one most
likely to report success having caused none, so a pass that touched nothing says
so in as many words rather than returning an empty artifact list and a cheerful
sign-off.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.messages import HumanMessage

from agent.improve.issues import IssueStore
from agent.protocol.types import (AgentCard, AgentSkill, Artifact, DataPart,
                                  FilePart, Message, Task, TaskState)

logger = logging.getLogger("harness.improve")

CARD = AgentCard(
    name="improve",
    version="0.1.0",
    description=("Reads the runs this project's agents have recorded, names "
                 "what keeps going wrong, has the coding agent fix it, and "
                 "checks whether it stopped. It never edits code itself."),
    skills=[
        AgentSkill(
            id="improve_agent",
            name="Improve an agent from its traces",
            description=("Find a failure that recurs across recorded runs, "
                         "diagnose it against the harness source, delegate the "
                         "fix, and verify it against fresh runs."),
            tags=["traces", "evaluation", "diagnosis", "regression"],
            examples=[
                "Work the ledger: check every open issue against the runs "
                "recorded since it was delegated, and close or reopen it.",
                "The last batch of `code` runs failed mostly as `stopping`. "
                "Split that class by what actually happened and open an issue "
                "for the largest group.",
            ],
        ),
    ],
)


def _issues(store: IssueStore) -> dict:
    """Every issue by id, with the mtime that says whether it moved."""
    if not store.directory.is_dir():
        return {}
    return {path.stem: path.stat().st_mtime
            for path in sorted(store.directory.glob("*.json"))}


def make_handler(model, workdir: Path, *, floor: int, members: int,
                 recursion_limit: int, transport=None):
    """The handler the registry serves for `improve`.

    `transport` is the peers transport this agent delegates *through* -- the one
    carrying `code`. Without it the pass can diagnose and cannot fix, which is a
    usable read-only mode, so it is not an error here
    ([tools.make_tools](tools.py)).
    """
    from agent.improve.session import (NothingToImproveOn, build_agent,
                                       check_records)

    workdir = Path(workdir)
    store = IssueStore(workdir)

    def handle(task: Task) -> Task:
        request = task.history[-1].text if task.history else ""
        if not request:
            return task.advance(TaskState.REJECTED, Message.agent(
                "The request was empty; there is nothing to look into."))

        # The probe belongs here rather than at registration, unlike the
        # explorer's: what this agent needs is recorded runs *in the bound
        # workspace*, and a served workspace arrives with the task. A pass with
        # nothing to read can never do its job, so it is refused with the
        # command that would fix it instead of spending a session discovering
        # that ([registry](../protocol/registry.py)).
        try:
            check_records(workdir)
        except NothingToImproveOn as exc:
            return task.advance(TaskState.REJECTED, Message.agent(str(exc)))

        # The before-shot, for the same reason the explorer takes one: without
        # it a second pass reports the first pass's issues as its own findings.
        before = _issues(store)

        agent = build_agent(workdir, model, floor=floor, members=members,
                            transport=transport)
        final = agent.invoke({"messages": [HumanMessage(request)]},
                             {"recursion_limit": recursion_limit})

        after = _issues(store)
        touched = [i for i, mtime in after.items() if before.get(i) != mtime]

        for issue_id in touched:
            issue = store.get(issue_id)
            rel = store.path(issue_id).relative_to(workdir).as_posix()
            task.artifacts.append(Artifact(
                name=issue_id,
                description=(issue.title if issue else issue_id),
                parts=[FilePart(uri=rel, name=f"{issue_id}.json"),
                       DataPart({"status": issue.status if issue else "?",
                                 "severity": issue.severity if issue else "?",
                                 "lever": issue.lever if issue else "",
                                 "runs": len(issue.evidence) if issue else 0})],
                metadata={"status": issue.status if issue else "?"},
            ))

        closing = _closing_text(final, touched)
        message = Message.agent(closing, task_id=task.id,
                                context_id=task.context_id)
        task.history.append(message)
        return task.advance(TaskState.COMPLETED, message)

    return handle


def _closing_text(final, touched: list) -> str:
    """The pass's last message, clipped, with the ledger's own verdict first.

    The count leads because it is the only part a caller can check. A pass that
    moved nothing is the failure mode worth being loud about: it read traces,
    spent quota, and left the project exactly as it found it.
    """
    if not touched:
        head = ("This pass changed no issue in the ledger. Nothing was named, "
                "delegated or closed, so nothing about the harness changed.")
    else:
        head = f"Issues touched: {', '.join(sorted(touched))}."

    messages = (final or {}).get("messages") or []
    text = ""
    if messages:
        content = getattr(messages[-1], "content", "")
        if isinstance(content, list):  # content blocks
            content = " ".join(str(b.get("text", "")) for b in content
                               if isinstance(b, dict))
        text = str(content).strip()
    return f"{head}\n\n{text[:2000]}".strip()
