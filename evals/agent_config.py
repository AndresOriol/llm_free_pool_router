"""Resolving an agent configuration to something runnable.

A config is a pinned commit of the agent repo plus overrides. It resolves via
`git worktree add --detach`, so a comparison runs from a clean tree while you
keep editing the branch it came from.

**The checkout is throwaway and lives only as long as the batch.** What makes a
configuration reproducible is the resolved SHA in every `run.json` plus the
config file copied beside it -- `git worktree add --detach <path> <sha>`
rebuilds the tree from those two at any point later. Keeping the checkouts
instead bought nothing and cost a directory of thirty-odd stale copies of this
repo, one per (config, SHA) pair ever run.

The recorded fingerprint is the resolved SHA plus a hash of the effective
overrides -- `ref: master` today and `ref: master` next month are different
configurations, and the results must say so rather than silently comparing
two different things.
"""

import contextlib
import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# What actually launches the agent. Overridable because there is more than one
# thing to launch: `evals/fake_agent.py` exercises the runner's own paths
# without spending quota, and `agent.explore` takes the same arguments as
# `agent.code` on purpose, so an arm that researches instead of codes is a
# config line rather than a second runner.
DEFAULT_AGENT_CMD = ["python", "-m", "agent.code", "{workdir}"]


def read_env_file(path: Path) -> dict:
    """Parse a KEY=VALUE file.

    Configurations resolve to throwaway worktrees and the agent's `.env` is
    gitignored, so it is never present in one. Keys therefore come from the
    evaluator's side and are injected into the subprocess environment -- which
    also means no secret is ever copied into a worktree.
    """
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class AgentConfig:
    name: str
    path: Path          # the yaml this was read from; copied into every run dir
    repo: Path
    ref: str
    sha: str
    agent_cmd: list
    env: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)
    router_config: str = ""
    # Set only while `checkout()` is open. Nothing outside a batch has a tree.
    worktree: Optional[Path] = None

    @contextlib.contextmanager
    def checkout(self):
        """Materialize the pinned commit, and take it away again afterwards.

        `--force` on removal: the agent runs with cwd set here, so the tree is
        never clean by the end -- `__pycache__` at least, and whatever else it
        wrote outside the scenario workdir. There is nothing here to save; the
        commit is in the repo and the run's evidence is in the results dir.
        """
        # mkdtemp gives an absolute path, which matters: `git -C <repo>
        # worktree add` resolves a relative one against the *agent* repo, and
        # would put the worktree inside the thing under test.
        path = Path(tempfile.mkdtemp(prefix=f"eval-{self.name}-"))
        _git(self.repo, "worktree", "add", "--detach", str(path), self.sha)
        self.worktree = path
        try:
            yield path
        finally:
            self.worktree = None
            subprocess.run(["git", "-C", str(self.repo), "worktree", "remove",
                            "--force", str(path)], capture_output=True)
            shutil.rmtree(path, ignore_errors=True)

    @property
    def fingerprint(self) -> str:
        # Secrets are deliberately excluded: which key served a run is not part
        # of what makes two configurations the same, and the fingerprint is
        # written into every run's `run.json` and quoted from there into
        # reports, which do get committed.
        payload = json.dumps({"sha": self.sha, "env": self.env,
                              "router_config": self.router_config,
                              "agent_cmd": self.agent_cmd}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def load(path: Path) -> AgentConfig:
    """Read a config and resolve its ref. No tree is created here -- `ref` is
    resolved to a SHA now so a batch cannot silently straddle a commit that
    moved under it, and `checkout()` builds the tree when the batch starts."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    repo = (path.parent / spec["repo"]).resolve()
    ref = spec.get("ref", "master")
    sha = _git(repo, "rev-parse", ref)

    overrides = spec.get("overrides") or {}
    return AgentConfig(
        name=spec["name"],
        path=path.resolve(),
        repo=repo,
        ref=ref,
        sha=sha,
        agent_cmd=spec.get("agent_cmd", DEFAULT_AGENT_CMD),
        env={str(k): str(v) for k, v in (overrides.get("env") or {}).items()},
        secrets=(read_env_file((path.parent / spec["env_file"]).resolve())
                 if spec.get("env_file") else {}),
        router_config=overrides.get("router_config") or "",
    )
