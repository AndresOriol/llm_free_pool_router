r"""Path containment in the jail, including the Windows case that broke a run.

`FilesystemBackend._resolve_path` resolves both the target and the root and asks
whether one is under the other. On Windows that comparison is not stable:
`Path.resolve()` normally strips the `\\?\` extended-length prefix and cannot
when another process holds the file open, so the same call returns two different
spellings of one path. The root was resolved once at startup and almost never
carries the prefix, so a write *inside* the jail gets refused as an escape.

That is not hypothetical -- it killed a session after three successful writes to
the same directory. These checks pin the two halves: the prefix is stripped, and
stripping it does not open the jail.
"""

import tempfile
from pathlib import Path

from agent.utils.backend import RestrictedShellBackend, _unprefixed


def test_the_extended_length_prefix_is_stripped():
    assert str(_unprefixed(Path("\\\\?\\C:\\a\\b"))) == "C:\\a\\b"


def test_the_unc_form_maps_back_to_a_network_path():
    # Compared as paths, not strings: pathlib spells a UNC root with a trailing
    # separator and the two forms are the same location.
    assert _unprefixed(Path("\\\\?\\UNC\\srv\\share")) == Path("\\\\srv\\share")


def test_an_ordinary_path_is_returned_unchanged():
    for text in ("C:\\a\\b", "/tmp/x", "relative/path"):
        assert str(_unprefixed(Path(text))) == str(Path(text))


def test_a_write_inside_the_jail_is_allowed_either_spelling():
    root = Path(tempfile.mkdtemp())
    backend = RestrictedShellBackend(root_dir=str(root))
    backend.write("/pipeline/context.py", "x = 1\n")
    assert (root / "pipeline" / "context.py").read_text() == "x = 1\n"


def test_a_prefixed_root_still_contains_its_own_files():
    """The half that matters: if the *root* is the prefixed one, containment
    must still hold rather than refusing every path under it."""
    root = Path(tempfile.mkdtemp())
    backend = RestrictedShellBackend(root_dir=str(root))
    backend.cwd = Path("\\\\?\\" + str(root)) if root.drive else root

    resolved = backend._resolve_path("/notes.md")
    assert _unprefixed(Path(str(resolved))).is_relative_to(root)


def test_traversal_is_still_refused():
    backend = RestrictedShellBackend(root_dir=tempfile.mkdtemp())
    for attempt in ("/../escape.py", "/a/../../escape.py", "../escape.py"):
        try:
            backend._resolve_path(attempt)
        except ValueError as exc:
            assert "traversal" in str(exc).lower()
        else:  # pragma: no cover - a pass here is the bug
            raise AssertionError(f"{attempt} was not refused")


def test_a_tilde_is_a_directory_name_and_stays_inside():
    """Not an escape: pathlib never expands `~`, so it names a literal
    directory under the root. Upstream carries a `startswith("~")` guard that
    a relative key can never trigger, and this pins what actually happens
    rather than what the guard suggests."""
    root = Path(tempfile.mkdtemp())
    backend = RestrictedShellBackend(root_dir=str(root))
    assert backend._resolve_path("~/escape.py").is_relative_to(root)


# --- the git allowlist ------------------------------------------------------
# `allow_git` exists so a long session can commit its own work incrementally.
# Everything it permits has to be additive: the human's gate is the merge, and
# nothing the agent runs may throw committed work away.


def _git_refusal(command):
    backend = RestrictedShellBackend(root_dir=tempfile.mkdtemp(), allow_git=True)
    return backend._refusal(command.split())


def test_the_additive_git_subcommands_are_allowed():
    for command in ("git status", "git add -A", "git commit -m x",
                    "git branch", "git checkout -b topic/x", "git log"):
        assert not _git_refusal(command), command


def test_every_spelling_of_branch_deletion_is_refused():
    """`-d` was missing from the denied list and an agent found it: a recorded
    run created `topic/test-branch`, then ran `git branch -d topic/test-branch`
    and it worked, while `--delete` and `-D` were both refused."""
    for flag in ("-d", "-D", "--delete"):
        assert _git_refusal(f"git branch {flag} topic/x"), flag


def test_the_destructive_subcommands_are_refused():
    for command in ("git merge main", "git push", "git reset --hard",
                    "git clean -fd", "git rebase main"):
        assert _git_refusal(command), command
