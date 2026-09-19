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
prompt and the tools come from the agent's own `build_agent`, and the graph
is the compiled one; the probe just stops reading after the first tool call. A probe that tested a hand-built copy of the agent
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

# The agents a probe can be posed to; `_build` resolves each lazily, since
# importing the explorer would pull in the web tools to run a coding probe.
# `explore-researcher` is the explorer's research sub-agent, taken compiled out
# of the explorer itself rather than rebuilt beside it.
AGENTS = ("code", "improve", "explore", "explore-researcher")

# How many supersteps a probe may take before it is abandoned. Two, because the
# first is the decision and the second is only ever reached when the first
# produced no tool call at all.
MAX_STEPS = 2

# How many turns a probe will let the agent spend on its `through` tools --
# calls with no effect beyond the conversation, like a reflection -- before the
# decision it is judged on. Bounded, so a probe stays a few calls.
MAX_THROUGH = 3


@dataclass
class Probe:
    """One situation, and what the agent should do first when it sees it."""

    id: str
    agent: str = "code"
    prompt: str = ""
    files: dict = field(default_factory=dict)   # a tiny tree to decide against
    expect: dict = field(default_factory=dict)
    why: str = ""                               # what failure this guards
    # The conversation already had, after `prompt`: the recorded run up to the
    # decision point. A failure that happens mid-run -- a review that signs off
    # what it should have questioned -- cannot be reached from a first message.
    history: list = field(default_factory=list)
    # The explorer's research directory, when the recorded run named one.
    research_dir: str = ""
    # Tools the agent may call and carry on past: the judged decision is its
    # first call outside them. The baseline run of the explorer probes passed
    # three times on `think_tool` without ever reaching the write they test.
    through: list = field(default_factory=list)

    @property
    def problems(self) -> list:
        found = []
        if not self.id:
            found.append("a probe needs an id")
        if self.agent not in AGENTS:
            found.append(f"agent must be one of {', '.join(AGENTS)}")
        if not self.prompt.strip():
            found.append("a probe needs a prompt")
        try:
            messages(self)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            found.append(f"history does not parse: {exc}")
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
                why=str(entry.get("why", "")),
                history=list(entry.get("history") or []),
                research_dir=str(entry.get("research_dir") or ""),
                through=[str(t) for t in (entry.get("through") or [])])
            problems = probe.problems
            if problems:
                raise ValueError(f"{path.name}: probe {probe.id!r} — "
                                 + "; ".join(problems))
            found.append(probe)
    return found


def messages(probe: Probe) -> list:
    """The conversation the probe starts from: its prompt, then its history.

    History entries are `{user: text}` or `{ai: text, calls: [...]}`, where
    each call is `{name, args, result}` and becomes the tool call and the tool
    message answering it -- so a recorded turn is written once, as it read.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    found = [HumanMessage(probe.prompt)]
    for index, entry in enumerate(probe.history):
        if "user" in entry:
            found.append(HumanMessage(str(entry["user"])))
            continue
        if "ai" not in entry:
            raise ValueError(f"history entry {index} is neither user nor ai")
        calls = entry.get("calls") or []
        ids = [f"call_{index}_{n}" for n in range(len(calls))]
        found.append(AIMessage(str(entry["ai"] or ""), tool_calls=[
            {"name": str(c["name"]), "args": dict(c.get("args") or {}), "id": i}
            for c, i in zip(calls, ids)]))
        found += [ToolMessage(str(c.get("result", "")), tool_call_id=i,
                              name=str(c["name"])) for c, i in zip(calls, ids)]
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


# The peers a probed improvement pass is built with. Naming `code` is what puts
# `delegate_fix` in front of the model: the tool exists only when that peer does
# ([agent/improve/tools.py](../agent/improve/tools.py)), and a probe built
# without it -- `does-not-delegate-before-diagnosing` -- was asserting that an
# absent tool went uncalled, which is true of every run and evidence about none.
#
# Nothing is stubbed behind the name. A probe stops at the first decision and
# never launches anything; what matters is that the schema is in front of the
# model, so declining to use it is a choice
# ([20. Probes](../docs/20-probes.md)).
PROBE_PEERS = ("code",)


def _build(probe: Probe, workdir: Path, model, floor: int, members: int):
    """The real compiled agent for this probe's target."""
    if probe.agent in ("explore", "explore-researcher"):
        from agent.explore.agent import build_agent
        agent = build_agent(workdir, model, floor=floor, members=members,
                            **({"research_dir": probe.research_dir}
                               if probe.research_dir else {}))
        return agent if probe.agent == "explore" else _researcher(agent)
    if probe.agent == "improve":
        from agent.improve.agent import build_agent
        return build_agent(workdir, model, floor=floor, members=members,
                           peers=PROBE_PEERS)
    from agent.code.agent import build_agent
    return build_agent(workdir, model, floor=floor, members=members)


def _researcher(explorer):
    """The research sub-agent as the explorer compiled it.

    deepagents keeps the compiled sub-agents only in the `task` tool's closure.
    Reaching in is fragile against an upstream change -- and loudly so, which is
    the point: rebuilding the researcher here would drift from the real one
    without anything failing (tests/evals/test_probes.py).
    """
    task = explorer.nodes["tools"].bound.tools_by_name["task"]
    function = task.func or task.coroutine
    cells = dict(zip(function.__code__.co_freevars, function.__closure__ or ()))
    return cells["subagent_graphs"].cell_contents["research-agent"]


def first_decision(probe: Probe, model, *, floor: int, members: int,
                   root: Optional[Path] = None) -> dict:
    """Run the agent until it first acts. Returns what it did.

    `{"tools": [{"name", "args"}], "text": str, "error": str}`. Streaming and
    stopping is what keeps this one call rather than a session: the graph is the
    real one and would happily keep going.
    """
    from langchain_core.messages import AIMessage

    with tempfile.TemporaryDirectory(prefix="probe-") as tmp:
        workdir = _materialize(probe, Path(root or tmp))
        agent = _build(probe, workdir, model, floor, members)

        start = messages(probe)
        seen, text = [], ""
        try:
            # Middleware nodes count as steps too, so the passes are counted
            # below rather than inferred from the recursion limit.
            limit = MAX_STEPS + (10 * MAX_THROUGH if probe.through else 0)
            passed = 0
            for state in agent.stream({"messages": start},
                                      {"recursion_limit": limit},
                                      stream_mode="values"):
                # Only what the agent said after the recorded history: a call
                # in the history is the situation, not the decision.
                said = ((state or {}).get("messages") or [])[len(start):]
                passed = 0
                for message in said:
                    if not isinstance(message, AIMessage):
                        continue
                    calls = getattr(message, "tool_calls", None) or []
                    if calls and all(c.get("name") in probe.through
                                     for c in calls):
                        passed += 1  # the decision is still ahead
                        continue
                    if calls:
                        seen = [{"name": c.get("name", "?"),
                                 "args": c.get("args", {})} for c in calls]
                        break
                    text = _text(message) or text
                if seen:
                    break  # it has decided; nothing later is the first decision
                if passed > MAX_THROUGH:
                    break
        except Exception as exc:  # noqa: BLE001 - a probe reports, never raises
            # The recursion limit is a normal end here, not a failure: it means
            # the agent produced no tool call within its two supersteps, which
            # is itself an answer some probes assert on.
            if "recursion" not in repr(exc).lower():
                logger.warning(f"{probe.id}: {exc!r}")
                return {"tools": seen, "text": text, "error": repr(exc)}

        if probe.through and not seen and not text:
            # Out of passes with nothing decided is not a pass: the baseline
            # scored three of these green on negative expectations.
            return {"tools": [], "text": "", "error":
                    f"no decision within {MAX_THROUGH} turns of "
                    f"{', '.join(probe.through)}"}
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


def _check_text_not_matches(want, decision) -> tuple:
    """It answered, and did not say this -- a reply is a decision too."""
    text = decision.get("text") or ""
    return not re.search(str(want), text), f"said {text[:200] or 'nothing'!r}"


def _check_no_tool(want, decision) -> tuple:
    called = bool(decision.get("tools"))
    return (not called) == bool(want), f"first tool was {_first_name(decision) or 'none'}"


CHECKS = {"tool": _check_tool, "tool_in": _check_tool_in,
          "not_tool": _check_not_tool, "args_match": _check_args_match,
          "args_not_match": _check_args_not_match,
          "not_tool_with_args": _check_not_tool_with_args,
          "text_matches": _check_text_matches,
          "text_not_matches": _check_text_not_matches, "no_tool": _check_no_tool}


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
