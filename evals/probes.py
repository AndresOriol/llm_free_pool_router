"""Small tests: one agent, one situation, one decision.

A scenario run is the only test this project has had, and it is a blunt one. It
materialises a repository, runs an agent for minutes, and returns a single bit
decided by hidden tests. That bit is the right acceptance contract and a poor
instrument: it costs real free-tier quota per sample, it is noisy at the sample
sizes a free tier affords ([6.4.2](../docs/06-agent.md#642-the-pass-column-is-noise)),
and when it says "fail" it does not say where.

A **probe** is the small end of the same idea. It puts the real agent -- the
real system prompt, the real tools, the real jail -- in front of one situation
and stops at its **first decision**. What it asserts is which tool the agent
reached for, and with what. One model call instead of a session.

That is a narrow claim and deliberately so. A probe cannot tell you an agent
solves a problem. It can tell you the agent reads a file before editing it, does
not reach for a shell that is refused, and does not delete a test that
contradicts its task -- and those are exactly the behaviours the failure
taxonomy keeps naming and the pass column cannot isolate.

**Faithfulness is the whole point, so nothing is reconstructed here.** The
prompt comes from the agent's own `prompt.build`, the tools from its own
`build_agent`, and the graph is the compiled one; the probe just stops reading
after the first tool call. A probe that tested a hand-built copy of the agent
would drift from it silently, which is the failure mode this file exists to
avoid rather than to have.

Probes live in `evals/probes/*.yaml` so they are reviewed in git, and are pushed
to a LangSmith dataset from there -- the file is the source of truth and the
dataset is a projection of it ([10.7](../docs/10-metrics.md)).
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("evals.probes")

PROBES_DIR = Path(__file__).parent / "probes"

# The agents a probe can be posed to, and how to build one. Each entry is
# (module path, builder) resolved lazily -- importing all three would pull in
# the web tools and the Tavily pool to run a coding probe.
AGENTS = ("code", "improve")

# How many supersteps a probe may take before it is abandoned. Two, because the
# first is the decision and the second is only ever reached when the first
# produced no tool call at all.
MAX_STEPS = 2


@dataclass
class Probe:
    """One situation, and what the agent should do first when it sees it."""

    id: str
    agent: str = "code"
    prompt: str = ""
    files: dict = field(default_factory=dict)   # a tiny tree to decide against
    expect: dict = field(default_factory=dict)
    why: str = ""                               # what failure this guards

    @property
    def problems(self) -> list:
        found = []
        if not self.id:
            found.append("a probe needs an id")
        if self.agent not in AGENTS:
            found.append(f"agent must be one of {', '.join(AGENTS)}")
        if not self.prompt.strip():
            found.append("a probe needs a prompt")
        if not self.expect:
            found.append("a probe with no expectation asserts nothing")
        unknown = set(self.expect) - set(CHECKS)
        if unknown:
            found.append(f"unknown expectation(s): {', '.join(sorted(unknown))}")
        return found


def load(directory: Path = PROBES_DIR) -> list:
    """Every probe on disk, in file order. Invalid ones raise rather than skip.

    A probe that silently does not run is worse than no probe: the suite still
    reports a number, and the number quietly stops covering what it claims to.
    """
    directory = Path(directory)
    found = []
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in (raw.get("probes") or []):
            probe = Probe(
                id=str(entry.get("id", "")),
                agent=str(entry.get("agent", "code")),
                prompt=str(entry.get("prompt", "")),
                files={str(k): str(v) for k, v in (entry.get("files") or {}).items()},
                expect=dict(entry.get("expect") or {}),
                why=str(entry.get("why", "")))
            problems = probe.problems
            if problems:
                raise ValueError(f"{path.name}: probe {probe.id!r} — "
                                 + "; ".join(problems))
            found.append(probe)
    return found


# -- running one probe -----------------------------------------------------

def _materialize(probe: Probe, root: Path) -> Path:
    """The probe's tiny tree, under a throwaway directory."""
    workdir = root / probe.id
    workdir.mkdir(parents=True, exist_ok=True)
    for name, body in probe.files.items():
        path = workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return workdir


def _stub_transport():
    """A `code` peer that exists and does nothing.

    The improvement agent only gets `delegate_fix` when a peer is reachable
    ([agent/improve/tools.py](../agent/improve/tools.py)), so building it with
    no transport removes the very tool the interesting probes are about --
    `does-not-delegate-before-diagnosing` was asserting that an absent tool went
    uncalled, which is true of every run and evidence about none.

    The handler is a stub because a probe stops at the first decision and never
    reaches a delegate's reply. What matters is that the schema is in front of
    the model, so declining to use it is a choice.
    """
    from agent.code.a2a import CARD
    from agent.protocol.local import LocalTransport
    from agent.protocol.registry import AgentRegistry
    from agent.protocol.types import Message, TaskState

    registry = AgentRegistry()
    registry.register(CARD, lambda task: task.advance(
        TaskState.COMPLETED, Message.agent("(probe stub: nothing was done)")))
    return LocalTransport(registry)


def _build(probe: Probe, workdir: Path, model, floor: int, members: int):
    """The real compiled agent for this probe's target."""
    if probe.agent == "improve":
        from agent.improve.session import build_agent
        return build_agent(workdir, model, floor=floor, members=members,
                           transport=_stub_transport())
    from agent.code.session import build_agent
    return build_agent(workdir, model, floor=floor, members=members)


def first_decision(probe: Probe, model, *, floor: int, members: int,
                   root: Optional[Path] = None) -> dict:
    """Run the agent until it first acts. Returns what it did.

    `{"tools": [{"name", "args"}], "text": str, "error": str}`. Streaming and
    stopping is what keeps this one call rather than a session: the graph is the
    real one and would happily keep going.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    with tempfile.TemporaryDirectory(prefix="probe-") as tmp:
        workdir = _materialize(probe, Path(root or tmp))
        agent = _build(probe, workdir, model, floor, members)

        seen, text = [], ""
        try:
            for state in agent.stream({"messages": [HumanMessage(probe.prompt)]},
                                      {"recursion_limit": MAX_STEPS},
                                      stream_mode="values"):
                messages = (state or {}).get("messages") or []
                for message in messages:
                    if not isinstance(message, AIMessage):
                        continue
                    calls = getattr(message, "tool_calls", None) or []
                    if calls:
                        seen = [{"name": c.get("name", "?"),
                                 "args": c.get("args", {})} for c in calls]
                    elif not seen:
                        text = _text(message) or text
                if seen:
                    break  # it has decided; nothing later is the first decision
        except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
            # The recursion limit is a normal end here, not a failure: it means
            # the agent produced no tool call within its two supersteps, which
            # is itself an answer some probes assert on.
            if "recursion" not in repr(exc).lower():
                logger.warning(f"{probe.id}: {exc!r}")
                return {"tools": seen, "text": text, "error": repr(exc)}

        return {"tools": seen, "text": text, "error": ""}


def _text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):  # content blocks
        content = " ".join(str(b.get("text", "")) for b in content
                           if isinstance(b, dict))
    return str(content).strip()


# -- scoring ---------------------------------------------------------------

def _first_name(decision: dict) -> str:
    tools = decision.get("tools") or []
    return str(tools[0]["name"]) if tools else ""


def _all_args(decision: dict) -> str:
    return " ".join(str((t or {}).get("args", "")) for t in
                    (decision.get("tools") or []))


def _check_tool(want, decision) -> tuple:
    got = _first_name(decision)
    return got == str(want), f"first tool was {got or 'none'}"


def _check_tool_in(want, decision) -> tuple:
    got = _first_name(decision)
    return got in [str(w) for w in want], f"first tool was {got or 'none'}"


def _check_not_tool(want, decision) -> tuple:
    names = [t["name"] for t in (decision.get("tools") or [])]
    return str(want) not in names, f"tools called: {', '.join(names) or 'none'}"


def _check_args_match(want, decision) -> tuple:
    args = _all_args(decision)
    return bool(re.search(str(want), args)), f"args were {args[:200] or 'none'}"


def _check_not_tool_with_args(want, decision) -> tuple:
    """No call matches *both* this tool pattern and this argument pattern.

    The check most probes actually want, and the reason it exists rather than
    `args_not_match` being reused: the first live run of the suite failed a
    probe meant to catch an agent *editing* a protected test, because the agent
    had *read* it -- which is the correct move and the one the other probes ask
    for. An argument pattern with no tool attached cannot tell those apart, and
    a suite that punishes the right behaviour is worse than one that misses the
    wrong one.

    Written as a pair, `[tool regex, args regex]`.
    """
    try:
        tool_pattern, args_pattern = want
    except (TypeError, ValueError):
        return False, f"expected a [tool, args] pair, got {want!r}"
    for call in decision.get("tools") or []:
        name, args = str(call.get("name", "")), str(call.get("args", ""))
        if re.search(str(tool_pattern), name) and re.search(str(args_pattern), args):
            return False, f"{name} was called with {args[:160]}"
    return True, "no such call"


def _check_args_not_match(want, decision) -> tuple:
    """The negative case, which is most of what a probe wants to say.

    "It did not reach for the test file", "it did not put a shell operator in
    the command" -- the failures this project keeps recording are things the
    agent should not have done, and a suite that can only assert the positive
    would have to guess the one right answer instead.
    """
    args = _all_args(decision)
    return not re.search(str(want), args), f"args were {args[:200] or 'none'}"


def _check_text_matches(want, decision) -> tuple:
    text = decision.get("text") or ""
    return bool(re.search(str(want), text, re.I)), f"said {text[:200] or 'nothing'!r}"


def _check_no_tool(want, decision) -> tuple:
    called = bool(decision.get("tools"))
    return (not called) == bool(want), f"first tool was {_first_name(decision) or 'none'}"


CHECKS = {"tool": _check_tool, "tool_in": _check_tool_in,
          "not_tool": _check_not_tool, "args_match": _check_args_match,
          "args_not_match": _check_args_not_match,
          "not_tool_with_args": _check_not_tool_with_args,
          "text_matches": _check_text_matches, "no_tool": _check_no_tool}


def score(probe: Probe, decision: dict) -> dict:
    """Every expectation, checked. All must hold."""
    if decision.get("error"):
        return {"id": probe.id, "passed": False, "agent": probe.agent,
                "reasons": [f"the run failed: {decision['error'][:200]}"],
                "decision": _first_name(decision)}

    reasons, passed = [], True
    for key, want in probe.expect.items():
        ok, detail = CHECKS[key](want, decision)
        if not ok:
            passed = False
            reasons.append(f"expected {key}={want!r}, but {detail}")
    return {"id": probe.id, "passed": passed, "agent": probe.agent,
            "reasons": reasons, "decision": _first_name(decision)}


def report(results: list) -> str:
    """The table, failures first, because that is what anyone reads it for."""
    if not results:
        return "No probes ran."
    passed = sum(1 for r in results if r["passed"])
    lines = [f"{passed}/{len(results)} probe(s) passed.", ""]
    for result in sorted(results, key=lambda r: r["passed"]):
        mark = "PASS" if result["passed"] else "FAIL"
        lines.append(f"[{mark}] {result['id']} ({result['agent']}) "
                     f"-> {result['decision'] or 'no tool'}")
        for reason in result["reasons"]:
            lines.append(f"       {reason}")
    return "\n".join(lines)
