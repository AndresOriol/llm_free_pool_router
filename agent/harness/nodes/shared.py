"""The few things more than one node file needs.

Deliberately small. A helper earns a place here by being used by two nodes; a
helper used by one lives in that node's file, where a reader looking at the
node can see it without a second hop. That is the whole point of the folder.
"""

from __future__ import annotations

from agent.harness.protocol import Report, parse_report

_EDIT_TOOLS = {"replace_in_file", "create_file"}


def applied_edits(result) -> list:
    """The edits a node actually landed, as the backend reported them.

    An acting node is judged by this rather than by its own account: a model
    that says it changed a file and did not must not be able to end a session.
    """
    return [out for name, out in result.outputs
            if name in _EDIT_TOOLS and out.startswith("ok:")]


def honour_insufficient(report: Report, result) -> Report:
    """Let a node say it was under-briefed -- unless it demonstrably succeeded.

    A node reporting INSUFFICIENT_CONTEXT while doing nothing is telling the
    orchestrator the one thing it needs to hear, so the text overrides an
    inferred PARTIAL. It never overrides a DONE backed by tool effects.
    """
    if report.status == "DONE":
        return report
    if "INSUFFICIENT_CONTEXT" not in (result.text or "").upper():
        return report
    return Report(status="INSUFFICIENT_CONTEXT",
                  finding=parse_report(result.text).finding,
                  evidence=report.evidence)


def report_from_text(result) -> Report:
    """For a node whose value is what it *says*. Taken at its word."""
    return parse_report(result.text)


def report_from_edits(result) -> Report:
    """For a node whose value is the files it changed (write, document)."""
    applied = applied_edits(result)
    status = "DONE" if applied else "PARTIAL"
    finding = applied[-1] if applied else (result.text or "nothing applied")[:200]
    return honour_insufficient(Report(status=status, finding=finding), result)
