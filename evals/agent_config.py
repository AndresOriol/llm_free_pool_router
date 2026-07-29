"""Resolving an agent configuration to something runnable.

A config is a pinned commit of the agent repo plus overrides. It resolves via
`git worktree add --detach`, so a comparison runs from a clean tree while you
keep editing the branch it came from.

The recorded fingerprint is the resolved SHA plus a hash of the effective
overrides -- `ref: master` today and `ref: master` next month are different
configurations, and the results must say so rather than silently comparing
two different things.
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# What actually launches the agent. Overridable so the pipeline can be tested
# against a stub without spending free-tier quota on the real pool.
DEFAULT_AGENT_CMD = ["python", "-m", "agent.coding_agent", "{workdir}"]


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
    repo: Path
    ref: str
    sha: str
    worktree: Path
    agent_cmd: list
    env: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)
    router_config: str = ""

    @property
    def fingerprint(self) -> str:
        # Secrets are deliberately excluded: which key served a run is not part
        # of what makes two configurations the same, and the fingerprint is
        # written to results that get committed.
        payload = json.dumps({"sha": self.sha, "env": self.env,
                              "router_config": self.router_config,
                              "agent_cmd": self.agent_cmd}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def load(path: Path, worktree_root: Path) -> AgentConfig:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    repo = (path.parent / spec["repo"]).resolve()
    ref = spec.get("ref", "master")
    sha = _git(repo, "rev-parse", ref)

    overrides = spec.get("overrides") or {}
    # Must be absolute: `git -C <repo> worktree add` resolves a relative path
    # against the *agent* repo, which would create the worktree inside the
    # thing under test.
    worktree = (worktree_root / f"{spec['name']}-{sha[:8]}").resolve()
    if not worktree.exists():
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "worktree", "add", "--detach", str(worktree), sha)

    return AgentConfig(
        name=spec["name"],
        repo=repo,
        ref=ref,
        sha=sha,
        worktree=worktree,
        agent_cmd=spec.get("agent_cmd", DEFAULT_AGENT_CMD),
        env={str(k): str(v) for k, v in (overrides.get("env") or {}).items()},
        secrets=(read_env_file((path.parent / spec["env_file"]).resolve())
                 if spec.get("env_file") else {}),
        router_config=overrides.get("router_config") or "",
    )
