"""Which agents this one may run. They are commands; that is the whole design.

`agent/code`, `agent/explore` and `agent/improve` are already three commands
with the same shape -- workdir as an argument, the task as `--task`, a summary
on stdout. The coding agent already has `execute`. So delegation needs no
protocol, no registry, no transport and no tool: it needs the agent to *know*
those commands exist, which is a paragraph of prose, and it needs one caller in
`agent/improve` to be able to launch one, which is `subprocess.run`. That is
this file, and it replaced 700 lines of A2A ([16. Delegation](../docs/16-delegation.md)).

**What the subprocess costs.** A second interpreter builds its own router, so a
delegate does *not* share its caller's cooldown: an account the caller has
already benched is one the delegate rediscovers, at the price of one refused
request. That was the argument for the old in-process transport and it was a
real one. What buys it back is that a delegation blocks -- the caller is idle
while the child runs, so the two never route concurrently and the usage ledger
they both append to stays a single writer at a time.

**Who may call whom is a caller's decision, not a global graph.** The coding
agent is offered `explore` and never `code`; the improvement agent is offered
`code` and never itself. What keeps it acyclic is that a child is launched with
its own name removed from `AGENT_PEERS`, so a delegation cannot come back round
to the agent that made it. Filtering rather than replacing is what keeps
`AGENT_PEERS=` meaning what it says: an operator who turned delegation off must
not get it back through a session someone else delegated.

Availability is a probe, not a declaration: `explore` is offered only if the
search pool can really be reached, because an agent that is advertised and then
fails costs the caller a whole session to discover it
([15.2.1](../docs/15-explorer.md#1521-a-capability-is-a-fact-to-probe-not-to-infer)).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger("harness.delegation")

# This repository, so a child `python -m agent.<name>` resolves even when the
# workspace being worked on is somewhere else entirely.
HARNESS_ROOT = Path(__file__).resolve().parents[1]

# Which peers to offer. Default on, so this stays a configuration that can be
# measured against a baseline rather than a feature nobody exercises
# ([13.7](../docs/13-roadmap.md#137-how-to-propose-a-change)). AGENT_PEERS=
# (empty) runs the agent alone.
PEERS_ENV = "AGENT_PEERS"
DEFAULT_PEERS = ("explore",)

# Where the eval scenarios live. A separate repository on purpose, so they
# survive branch switching in this one
# ([8.3](../docs/08-evaluation-method.md#83-where-things-live)) -- which also
# means a coding agent jailed to this repo cannot reach them, and `scenarios`
# is the second binding that lets an agent here build one.
SCENARIOS_ENV = "EVAL_SCENARIOS"
DEFAULT_SCENARIOS = "agent_evals"

# A delegated session is an agent session: tens of minutes, sometimes hours.
# The backend's ordinary 300s ceiling is sized for `pytest`, and applying it
# here would kill every delegation minutes in, with nothing to show for the
# quota already spent.
TIMEOUT_ENV = "AGENT_DELEGATE_TIMEOUT"
DEFAULT_TIMEOUT = 4 * 60 * 60

# name -> (module, what it is, what it leaves behind). Prose, because prose is
# all a calling model gets: there is no schema here to fill in.
AGENTS = {
    "code": (
        "agent.code",
        "Works a project unattended on its own branch: reads the tree, edits "
        "files, runs `python`/`pytest`, and commits as it goes. It never "
        "merges, pushes, or touches a remote.",
        "commits on a branch — read the diff, not the closing message"),
    "explore": (
        "agent.explore",
        "Researches the open web and writes a cited report to "
        "`/research/final_report.md`. It reads whole pages, not search "
        "snippets; it cannot run or change this project.",
        "markdown notes under `/research` — read them with your file tools"),
    "improve": (
        "agent.improve",
        "Reads the runs this project's agents have recorded, names what keeps "
        "going wrong, has the coding agent fix it, and checks whether it "
        "stopped.",
        "issues in `evals/results/issues/`"),
    # Not a new agent: the same coding agent with its `/` at the scenario
    # repository instead of here.
    "scenarios": (
        "agent.code",
        "The coding agent, bound to the eval scenario repository instead of "
        "this project. Use it to build a scenario that has been drafted.",
        "commits in the scenario repository"),
}


def requested() -> tuple:
    """The peer names asked for, from the environment. Empty means alone."""
    raw = os.environ.get(PEERS_ENV)
    if raw is None:
        return DEFAULT_PEERS
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def scenario_repo() -> Optional[Path]:
    """Where the scenario repository is, if it is there. A probe, not a guess."""
    raw = os.environ.get(SCENARIOS_ENV)
    path = Path(raw) if raw else HARNESS_ROOT.parent / DEFAULT_SCENARIOS
    return path.resolve() if (path / ".git").is_dir() else None


def available(peers: Optional[Sequence[str]] = None) -> list:
    """The names that can actually be reached, probed rather than declared.

    `peers` names who to offer; left unset it is read from `AGENT_PEERS`. The
    improvement agent passes `("code", "scenarios")` because what it delegates
    is a fix or a scenario, and being able to ask for research instead would
    just be a second way to spend the day
    ([19](../docs/19-improvement-agent.md)).
    """
    wanted = tuple(peers) if peers is not None else requested()
    if not wanted:
        logger.info("Peer agents disabled; this agent runs alone.")
        return []

    found = []
    for name in wanted:
        if name not in AGENTS:
            logger.warning(f"Unknown peer: {name!r}")
        elif name == "explore" and not _web_reachable():
            pass  # already logged
        elif name == "scenarios" and scenario_repo() is None:
            logger.warning(
                f"Not offering `scenarios`: no git repository at "
                f"{os.environ.get(SCENARIOS_ENV) or DEFAULT_SCENARIOS}. Set "
                f"{SCENARIOS_ENV} if it lives elsewhere.")
        else:
            found.append(name)
    return found


def _web_reachable() -> bool:
    """Can the explorer actually search? Not fatal when it cannot -- the coding
    agent works fine without a researcher; it just must not be told it has one."""
    try:
        from llm_router import TavilyPoolRouter

        from agent.explore.session import NoSearchPool, check_pool
    except ImportError as exc:
        logger.warning(f"Not offering the `explore` agent: {exc}")
        return False
    try:
        check_pool(TavilyPoolRouter.from_env())
    # NoSearchPool subclasses SystemExit -- a BaseException -- so `except
    # Exception` does not catch it, and letting it through would end the run
    # that was only asking whether an optional peer exists.
    except NoSearchPool as exc:
        logger.warning(f"Not offering the `explore` agent: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Not offering the `explore` agent: {exc!r}")
        return False
    return True


def where(name: str, workdir) -> Path:
    """The directory an agent runs in. Everything but `scenarios` runs in the
    caller's own workspace."""
    if name == "scenarios":
        return scenario_repo() or Path(workdir)
    return Path(workdir)


def prompt_section(names: Sequence[str], workdir) -> str:
    """The commands as a prompt section, or '' when nothing is reachable.

    **This is the whole client half of delegation.** No tool and no schema: the
    agent already has `execute`, and what it was missing was the knowledge that
    these commands exist. That is tokens and not only tidiness -- tool schemas
    are 91% of what a step spends
    ([6.4](../docs/06-agent.md#64-why-it-is-shaped-this-way)), and a `delegate`
    tool was charged on *every* step of *every* run to describe a directory
    that changes once at startup.
    """
    if not names:
        return ""

    lines = ["### Other agents you can run", "",
             "Each is a command. Running one starts a full agent session that "
             "spends the same free-tier pool your own turns do and can take an "
             "hour, so ask once, ask specifically, and read the files it "
             "leaves behind rather than asking again. The command blocks until "
             "that session ends and then prints a summary; the deliverable is "
             "on disk, not in the summary.", "",
             "Write the brief for someone who cannot see your conversation: "
             "state the question, what the answer is for, and what would make "
             "it useless.", ""]
    for name in names:
        module, description, delivers = AGENTS[name]
        at = where(name, workdir) if name == "scenarios" else "."
        lines += [f"**{name}** — {description}",
                  f"- delivers: {delivers}",
                  f"- run it: `python -m {module} {at} --task \"<your brief>\"`",
                  ""]
    return "\n".join(lines).rstrip()


def child_env(name: str) -> dict:
    """The environment a delegated agent runs in. Two edits and no more.

    `AGENT_PEERS` loses the child's own name, which is what keeps the graph
    acyclic. `PYTHONPATH` gains this repository, so `agent.code` imports even
    when the workspace is somewhere else.

    `EVAL_TRACE_FILE` is deliberately left alone: it is the flat JSONL every
    metric is summed over, it is appended to rather than replaced, and a
    delegation's cost belongs inside the totals of the run that asked for it
    ([10. Metrics](../docs/10-metrics.md)).
    """
    env = dict(os.environ)
    env[PEERS_ENV] = ",".join(p for p in requested() if p != name)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (f"{HARNESS_ROOT}{os.pathsep}{existing}" if existing
                         else str(HARNESS_ROOT))
    # The run tree is one JSON file per run. A child writing the parent's path
    # would overwrite the record of the run that launched it.
    env.pop("AGENT_TRACE_FILE", None)
    return env


def run(name: str, workdir, task: str, *,
        timeout: Optional[int] = None) -> tuple:
    """Run one agent to completion. Returns (exit_code, output).

    `exit_code` is None when the timeout stopped it -- which is not a crash:
    the session did real work and whatever it committed is still committed, so
    saying so beats reporting an empty failure.

    Only `agent/improve` calls this. Every other caller is a model with a shell,
    running the same command itself.
    """
    at = where(name, workdir)
    seconds = int(timeout or os.environ.get(TIMEOUT_ENV) or DEFAULT_TIMEOUT)
    command = [sys.executable, "-m", AGENTS[name][0], str(at), "--task", task]

    logger.info(f"Running {name} in {at}: {task[:120]!r}")
    try:
        done = subprocess.run(command, cwd=str(HARNESS_ROOT),
                              env=child_env(name), capture_output=True,
                              encoding="utf-8", errors="replace",
                              timeout=seconds)
    except subprocess.TimeoutExpired as expired:
        return None, _text(expired.stdout) + _text(expired.stderr)
    except OSError as exc:
        return 1, f"could not launch {name}: {exc}"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _text(stream) -> str:
    if not stream:
        return ""
    return stream if isinstance(stream, str) else stream.decode("utf-8", "replace")


def tail(text: str, lines: int = 40) -> str:
    """The last few lines of a child's output. Its summary is at the end."""
    return "\n".join((text or "").strip().splitlines()[-lines:])
