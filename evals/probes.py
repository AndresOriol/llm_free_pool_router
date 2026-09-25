"""Small tests: one agent, one situation, one decision.

A scenario run is the only test this project has had, and it is a blunt one. It
materialises a repository, runs an agent for minutes, and returns a single bit
decided by hidden tests. That bit is the right acceptance contract and a poor
instrument: it costs real free-tier quota per sample, it is noisy at the sample
sizes a free tier affords ([The pass column is noise](../docs/agents/code.md#the-pass-column-is-noise)),
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
dataset is a projection of it ([Metrics](../docs/evaluation/metrics.md)).
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("evals.probes")

PROBES_DIR = Path(__file__).parent / "probes"
REPO = Path(__file__).resolve().parents[1]

# What a probe is for. A `failure` guards a decision a recorded run got wrong; a
# `regression` pins a decision the agent already gets right, so a fix for one
# failure cannot quietly break the normal path (docs/evaluation/changing-behaviour.md).
KINDS = ("failure", "regression")

# What an agent's behaviour is made of: its own package, what every agent
# shares that the model reads (prompts, the tool surface, the file tools), and
# which models serve it. A commit touching any of these after a probe's
# `reviewed` date may have moved the agent off the path the probe's situation
# assumes. Router internals -- retries, logging, quota -- are left out: they
# change who answers when, not what the agent is shown.
SHARED = ("agent/utils/prompts", "agent/utils/prompts.py",
          "agent/utils/surface.py", "agent/utils/file_tools.py",
          "llm_router/config.yaml")
SURFACES = {"code": ("agent/code", *SHARED),
            "improve": ("agent/improve", *SHARED),
            "explore": ("agent/explore", *SHARED),
            "explore-researcher": ("agent/explore", *SHARED)}

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

# Passed through on every probe, whatever its `through` says. Neither touches
# the workspace, so neither can be the decision a probe is about -- and a probe
# that forgot to list them passed on a todo list: three NOTES.md probes scored
# green on `write_todos` on 2026-09-25 and failed 3/3 once passed through it.
ALWAYS_THROUGH = ("write_todos", "think_tool")


@dataclass
class Probe:
    """One situation, and what the agent should do first when it sees it."""

    id: str
    agent: str = "code"
    dataset: str = ""                           # from the file: its topic
    kind: str = "failure"
    source: str = ""                            # the run and turn it came from
    reviewed: str = ""                          # when the situation was last checked
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
    # The moves that make sense here, each named and argued. The decision must
    # be one of them: every call it makes matches an option, or its reply
    # matches an `answer` option. A decision that matches none is `unlisted` --
    # not forbidden, not endorsed -- and is the prompt to decide which it is.
    options: list = field(default_factory=list)
    # The moves that must not happen here, each named after the failure it
    # stops recurring. Checked before the options: a forbidden call fails the
    # probe even beside a good one.
    must_not: list = field(default_factory=list)

    @property
    def passes_through(self) -> tuple:
        return tuple(dict.fromkeys([*self.through, *ALWAYS_THROUGH]))

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
        if not (self.expect or self.options or self.must_not):
            found.append("a probe with no expectation asserts nothing")
        found += _move_problems("options", self.options)
        found += _move_problems("must_not", self.must_not)
        if self.kind not in KINDS:
            found.append(f"kind must be one of {', '.join(KINDS)}")
        try:
            datetime.date.fromisoformat(self.reviewed)
        except ValueError:
            found.append("reviewed must be the date the situation was last "
                         "checked, as YYYY-MM-DD")
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
        # One file is one topic is one dataset: the grouping is reviewed in the
        # same diff as the probes it groups.
        dataset = str(raw.get("dataset") or "")
        if not dataset:
            raise ValueError(f"{path.name}: a probe file names its dataset")
        # `<agent>-<topic>`: which agent is under test, then what groups the
        # examples (docs/evaluation/changing-behaviour.md#datasets-one-topic-each).
        if not re.fullmatch(r"[a-z]+(-[a-z0-9]+)+", dataset):
            raise ValueError(f"{path.name}: dataset {dataset!r} is not "
                             f"<agent>-<topic>, lowercase and hyphenated")
        for entry in (raw.get("probes") or []):
            agent = str(entry.get("agent", "code"))
            family = agent.split("-")[0]
            if agent in AGENTS and not dataset.startswith(family + "-"):
                raise ValueError(f"{path.name}: probe {entry.get('id')!r} tests "
                                 f"{family!r}, but dataset {dataset!r} names "
                                 f"another agent")
            probe = Probe(
                id=str(entry.get("id", "")),
                agent=str(entry.get("agent", "code")),
                dataset=dataset,
                kind=str(entry.get("kind", "failure")),
                source=str(entry.get("source") or ""),
                reviewed=str(entry.get("reviewed") or ""),
                prompt=str(entry.get("prompt", "")),
                files={str(k): str(v) for k, v in (entry.get("files") or {}).items()},
                expect=dict(entry.get("expect") or {}),
                why=str(entry.get("why", "")),
                history=list(entry.get("history") or []),
                research_dir=str(entry.get("research_dir") or ""),
                through=[str(t) for t in (entry.get("through") or [])],
                options=list(entry.get("options") or []),
                must_not=list(entry.get("must_not") or []))
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


def stale(probes: list) -> list:
    """`(probe, [commits])` for every probe its agent has changed under.

    A probe freezes a situation: a conversation that reached a decision. Once
    the agent changes, it may never reach that point again, and the probe keeps
    testing a path nobody takes. That is not detectable from the probe, so the
    rule is a review, and this is what says which ones are due.
    """
    due = []
    for probe in probes:
        paths = SURFACES.get(probe.agent, ())
        log = subprocess.run(
            ["git", "log", f"--after={probe.reviewed}T23:59:59",
             "--format=%h %cs %s", "--", *paths],
            cwd=REPO, capture_output=True, text=True, check=False).stdout
        commits = [line for line in log.splitlines() if line.strip()]
        if commits:
            due.append((probe, commits))
    return due


def from_run(record: Path, turn: int, agent: str, probe_id: str = "",
             limit: int = 8000) -> dict:
    """A probe skeleton for the decision at `turn` of a recorded run.

    The run record (`agent/utils/run_tree`) keeps every model call with the
    conversation it was sent. The one at `turn` is the situation: its first
    human message is the prompt, the rest is the history. Pages written by
    `write_file` before that turn become the files. What the probe expects is
    left for a person to write -- that is the part that needs judgement.
    """
    data = json.loads(Path(record).read_text(encoding="utf-8"))
    turns = {t["n"]: t for t in data["turns"]}
    sent = turns[turn]["input"]
    human = [m for m in sent if m.get("role") == "human"]
    if not human:
        raise ValueError(f"turn {turn} carries no human message")

    def cut(text) -> str:
        text = str(text)
        return text if len(text) <= limit else (
            text[:1500] + "\n[... cut to 1,500 characters for the probe; "
            "the run read the whole of it ...]")

    results = {m.get("tool_call_id"): m.get("text", "") for m in sent
               if m.get("role") == "tool"}
    history = []
    for message in sent[sent.index(human[0]) + 1:]:
        if message.get("role") == "human":
            history.append({"user": message.get("text", "")})
        elif message.get("role") == "ai":
            calls = message.get("tool_calls") or []
            history.append({"ai": message.get("text") or "", "calls": [
                {"name": c["name"], "args": c.get("args") or {},
                 "result": cut(results.get(c.get("id"), ""))} for c in calls]})

    files = {}
    for t in data["turns"]:
        if t["n"] >= turn:
            break
        for call in (t.get("output") or {}).get("tool_calls") or []:
            if call.get("name") == "write_file":
                files[call["args"]["file_path"].lstrip("/")] = call["args"]["content"]

    decided = (turns[turn].get("output") or {}).get("tool_calls") or []
    trace = (data.get("meta") or {}).get("trace_id", Path(record).stem)
    probe = {"id": probe_id or f"TODO-name-the-decision-at-turn-{turn}",
             "agent": agent, "kind": "failure",
             "source": f"run {trace}, turn {turn}",
             "reviewed": datetime.date.today().isoformat(),
             "why": (f"TODO: what went wrong at turn {turn}. The run did: "
                     + (", ".join(c["name"] for c in decided) or "no tool")),
             "expect": {"TODO": "what must hold of the next decision"}}
    research_dir = (data.get("meta") or {}).get("research_dir")
    if research_dir:
        probe["research_dir"] = research_dir
    if files:
        probe["files"] = files
    probe.update(prompt=human[0].get("text", ""), history=history)
    return probe


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
# ([Probes](../docs/evaluation/probes.md)).
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
        through = probe.passes_through
        seen, text, before, model_name = [], "", [], ""
        try:
            # Middleware nodes count as steps too, so the passes are counted
            # below rather than inferred from the recursion limit.
            limit = MAX_STEPS + 10 * MAX_THROUGH
            passed = 0
            for state in agent.stream({"messages": start},
                                      {"recursion_limit": limit},
                                      stream_mode="values"):
                # Only what the agent said after the recorded history: a call
                # in the history is the situation, not the decision.
                said = ((state or {}).get("messages") or [])[len(start):]
                passed, before = 0, []
                for message in said:
                    if not isinstance(message, AIMessage):
                        continue
                    calls = getattr(message, "tool_calls", None) or []
                    if calls and all(c.get("name") in through for c in calls):
                        passed += 1  # the decision is still ahead
                        before += [c.get("name", "?") for c in calls]
                        continue
                    model_name = _served_by(message) or model_name
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
                return {"tools": seen, "text": text, "error": repr(exc),
                        "before": before, "model": model_name}

        if not seen and not text:
            # Out of passes with nothing decided is not a pass: the baseline
            # scored three of these green on negative expectations.
            return {"tools": [], "text": "", "before": before,
                    "model": model_name, "error":
                    f"no decision within {MAX_THROUGH} turns of "
                    f"{', '.join(through)}"}
        return {"tools": seen, "text": text, "error": "", "before": before,
                "model": model_name}


def _served_by(message) -> str:
    """The model that made the decision, as its provider reported it.

    A score read without it is half a result: the same probe has failed on the
    full flash models and passed on flash-lite for different reasons
    (evals/probes/code-scope.yaml).
    """
    meta = getattr(message, "response_metadata", None) or {}
    return str(meta.get("model_name") or meta.get("model") or "")


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


# -- options and must_not ----------------------------------------------------
#
# A move is `{name, because, tool?, args?, answer?}`. `tool` and `args` are
# regexes over one call's name and its arguments; `answer` is a regex over a
# reply given instead of a call. Named, because the report says which one the
# agent took; argued, because an option nobody can justify is a preference.

MOVE_KEYS = {"name", "because", "tool", "args", "answer"}


def _move_problems(key: str, moves) -> list:
    if not isinstance(moves, list):
        return [f"{key} must be a list"]
    found, names = [], set()
    for move in moves:
        if not isinstance(move, dict):
            found.append(f"{key}: every entry is a mapping")
            continue
        name = str(move.get("name") or "")
        if not name or name in names:
            found.append(f"{key}: every entry needs a unique name")
        names.add(name)
        if not str(move.get("because") or "").strip():
            found.append(f"{key} {name!r}: says nothing about why")
        unknown = set(move) - MOVE_KEYS
        if unknown:
            found.append(f"{key} {name!r}: unknown key(s) {', '.join(sorted(unknown))}")
        if not ({"tool", "args", "answer"} & set(move)):
            found.append(f"{key} {name!r}: matches nothing; give tool, args or answer")
        if "answer" in move and ({"tool", "args"} & set(move)):
            found.append(f"{key} {name!r}: an answer is a reply, not a call")
        for part in ("tool", "args", "answer"):
            try:
                re.compile(str(move.get(part, "")))
            except re.error as exc:
                found.append(f"{key} {name!r}: {part} is not a regex ({exc})")
    return found


def _call_is(move: dict, call: dict) -> bool:
    if "answer" in move:
        return False
    return bool(re.search(str(move.get("tool", "")), str(call.get("name", "")))
                and re.search(str(move.get("args", "")), str(call.get("args", ""))))


def _reply_is(move: dict, text: str) -> bool:
    return "answer" in move and bool(re.search(str(move["answer"]), text or ""))


def _moves(probe: Probe, decision: dict) -> tuple:
    """(outcome, reasons): forbidden, unlisted, or the options it took."""
    calls, text = decision.get("tools") or [], decision.get("text") or ""
    for move in probe.must_not:
        hit = next((c for c in calls if _call_is(move, c)), None)
        if hit or (not calls and _reply_is(move, text)):
            what = (f"{hit['name']} {str(hit.get('args', ''))[:160]}" if hit
                    else f"said {text[:160]!r}")
            return (f"forbidden:{move['name']}",
                    [f"must not {move['name']}: {what}"])
    if not probe.options:
        return "", []
    if calls:
        taken, stray = [], []
        for call in calls:
            match = next((m["name"] for m in probe.options if _call_is(m, call)), None)
            if match:
                taken.append(match)
            else:
                stray.append(call)
        if not stray:
            return "option:" + "+".join(dict.fromkeys(taken)), []
        return "unlisted", [f"not one of the options: {c['name']} "
                            f"{str(c.get('args', ''))[:160]}" for c in stray]
    match = next((m["name"] for m in probe.options if _reply_is(m, text)), None)
    if match:
        return f"option:{match}", []
    return "unlisted", [f"not one of the options: said {text[:160]!r}"]


def score(probe: Probe, decision: dict) -> dict:
    """Every expectation, checked. All must hold.

    `outcome` says which way it went: `option:<name>` for a move the probe
    endorses, `forbidden:<name>` for one it names as a failure, `unlisted` for
    a move it neither endorses nor forbids -- a fail, and the question of which
    of the two lists that move belongs on.
    """
    base = {"id": probe.id, "agent": probe.agent,
            "decision": _first_name(decision),
            "model": decision.get("model") or "",
            "before": decision.get("before") or []}
    if decision.get("error"):
        return {**base, "passed": False, "outcome": "error",
                "reasons": [f"the run failed: {decision['error'][:200]}"]}

    outcome, reasons = _moves(probe, decision)
    passed = not reasons
    for key, want in probe.expect.items():
        ok, detail = CHECKS[key](want, decision)
        if not ok:
            passed = False
            reasons.append(f"expected {key}={want!r}, but {detail}")
            outcome = outcome or "failed"
    return {**base, "passed": passed, "outcome": outcome or
            ("passed" if passed else "failed"), "reasons": reasons}


def report(results: list) -> str:
    """The table, failures first, because that is what anyone reads it for."""
    if not results:
        return "No probes ran."
    passed = sum(1 for r in results if r["passed"])
    lines = [f"{passed}/{len(results)} probe(s) passed.", ""]
    for result in sorted(results, key=lambda r: r["passed"]):
        mark = "PASS" if result["passed"] else "FAIL"
        path = " > ".join([*result.get("before", []),
                           result["decision"] or "no tool"])
        served = f" [{result['model']}]" if result.get("model") else ""
        lines.append(f"[{mark}] {result['id']} ({result['agent']}) "
                     f"-> {path} = {result.get('outcome', '')}{served}")
        for reason in result["reasons"]:
            lines.append(f"       {reason}")
    return "\n".join(lines)
