"""What an agent is pointed at: one directory under one mounted root.

On the command line the workdir is `sys.argv[1]` and the operator is trusted
with it. Over HTTP it is a *string from a request*, and the jail
([backend.py](../runtime/backend.py)) only constrains the agent once it is
running -- it has nothing to say about which root it was handed. So the binding
is the boundary here, and it has exactly one rule:

**Every workspace is a direct child of `WORKSPACES_DIR`, named, never a path.**

Not a path the caller supplies, because then `../..` or `/etc` or a Windows
drive letter is a workspace; not a nested path, because `a/../../b` normalizes
out of the root and no amount of string checking is as reliable as refusing the
separator in the first place. A caller says `closet_ai`; it gets
`$WORKSPACES_DIR/closet_ai` or an error. The containment check afterwards is
belt-and-braces against symlinks, which a name rule cannot see.

The mount is the whole story for "bind to a filesystem": the operator maps a
host directory in, and every child of it is an addressable workspace
([18.4](../../docs/18-serving.md#184-binding-an-agent-to-a-repository-or-a-filesystem)).
`ensure` also clones for the "bind to a repository" half, but only into a
workspace that does not exist yet -- cloning over a directory that already has
work in it is how an unattended run destroys the thing it was asked to improve.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("harness.serve")

_ENV_VAR = "WORKSPACES_DIR"
DEFAULT_DIR = "/workspaces"

# Deliberately narrow: letters, digits, dot, dash, underscore. No separator, no
# drive letter, no colon, no NUL. `..` is excluded by the leading-character
# rule rather than by a special case.
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")

CLONE_TIMEOUT = 600


class BadWorkspace(ValueError):
    """The name or the repository was refused. Always the caller's fault."""


def root() -> Path:
    """Where workspaces live. A container mounts a volume here."""
    return Path(os.environ.get(_ENV_VAR) or DEFAULT_DIR)


def resolve(name: str) -> Path:
    """`$WORKSPACES_DIR/<name>`, or raise. Does not create anything."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise BadWorkspace(
            f"{name!r} is not a workspace name. A name is 1-64 characters of "
            f"letters, digits, '.', '-' and '_', and is never a path: it names "
            f"a directory directly under the server's workspace root.")

    base = root().resolve()
    path = (base / name).resolve()
    # A name cannot escape the root, but a symlink placed inside it can. This
    # is the check that survives one.
    if path != base and base not in path.parents:
        raise BadWorkspace(f"workspace {name!r} resolves outside the "
                           f"workspace root")
    return path


def _is_empty(path: Path) -> bool:
    return not any(path.iterdir())


def ensure(name: str, repo: Optional[str] = None) -> Path:
    """The workspace directory, created or cloned as needed.

    Three cases, and the third is the one that matters:

    - no `repo`: the directory is created if absent and used as it is. This is
      the mounted-filesystem case, and an existing directory is the point.
    - `repo` and the workspace is absent or empty: clone into it.
    - `repo` and the workspace already holds something: **refused**. It is not
      possible to tell from here whether that content is a previous clone of
      the same repository or a week of unpushed agent commits, and one of those
      two guesses destroys work. The caller re-uses the workspace by omitting
      `repo`, or names a new one.
    """
    path = resolve(name)

    if repo is None:
        path.mkdir(parents=True, exist_ok=True)
        return path

    if not _valid_repo_url(repo):
        raise BadWorkspace(
            f"{repo!r} is not a repository URL this server will clone. Use an "
            f"https:// or ssh:// URL, or omit `repo` and mount the repository "
            f"into the workspace root instead.")

    if path.exists() and not _is_empty(path):
        raise BadWorkspace(
            f"workspace {name!r} already exists and is not empty, so cloning "
            f"into it would overwrite work already there. Omit `repo` to use "
            f"it as it stands, or name a new workspace.")

    path.mkdir(parents=True, exist_ok=True)
    _clone(repo, path)
    return path


def _valid_repo_url(repo: str) -> bool:
    """https:// or ssh:// only.

    `file://`, a bare local path and git's `--upload-pack` style options are
    all refused: this argument arrives over the network, and `git clone` takes
    arguments that read local files and run local programs. Scheme-checking is
    the cheap half of not passing attacker-chosen strings to a program with
    those affordances; the other half is `shell=False` in `_clone`.
    """
    return isinstance(repo, str) and repo.startswith(("https://", "ssh://"))


def _clone(repo: str, path: Path) -> None:
    """`git clone <repo> <path>`, with no shell and a timeout.

    The full history, not `--depth 1`: the agent commits on its own branch and
    a human reviews the range afterwards, and a shallow clone makes several of
    the commands the jail allows (`log`, `diff` against a base) answer wrongly
    or not at all.
    """
    logger.info(f"Cloning {repo} into {path}")
    try:
        done = subprocess.run(
            ("git", "clone", "--", repo, str(path)),
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=CLONE_TIMEOUT,
            # The clone must not inherit the pool's keys, on the same principle
            # the jail strips them from the agent's own children.
            env={k: v for k, v in os.environ.items()
                 if not any(m in k.upper()
                            for m in ("KEY", "TOKEN", "SECRET", "PASSWORD"))})
    except subprocess.TimeoutExpired:
        raise BadWorkspace(f"cloning {repo} timed out after {CLONE_TIMEOUT}s")
    except OSError as exc:
        raise BadWorkspace(f"could not run git clone: {exc}")

    if done.returncode != 0:
        # git's own stderr, clipped. It names the actual problem -- no such
        # repository, authentication failed -- and inventing a friendlier
        # message here would just hide it.
        raise BadWorkspace(f"git clone failed: {done.stderr.strip()[:500]}")


def listing() -> list:
    """Every workspace the server can see, with a little state for each.

    An operator binding an agent to a repository needs to know what is already
    bound before choosing a name; this is that list, and it is also what makes
    the "already exists and is not empty" refusal above actionable.
    """
    base = root()
    if not base.is_dir():
        return []

    out = []
    for path in sorted(base.iterdir()):
        if not path.is_dir() or not _NAME.match(path.name):
            continue
        out.append({"name": path.name,
                    "path": str(path),
                    "git": (path / ".git").exists(),
                    "empty": _is_empty(path)})
    return out
