"""`read_file` returns the whole file by default, instead of the first 100 lines.

Importing this module configures deepagents' `read_file` for every agent in the
process. [backend.py](backend.py) imports it, so anything that can read a file
has already been through it.

## Why the default changed

deepagents defaults `read_file` to 100 lines and its description tells the model
to scan, then page, then page again. That is a default sized for a paid context
window being spent carefully. This pool's scarce resource is a different one: a
session routes only to members holding at least 128,000 input tokens
([pool.py](pool.py)), and what actually runs out is *requests against a daily
quota* ([4.2.1](../../docs/04-failover.md#421-skipping-a-member-whose-day-is-spent)).

Reading a 400-line file in four pages spends four calls to deliver what one call
could, and every one of those calls re-sends the whole conversation. Paginating
to protect a floor we set at 128k pays twice for the same caution.

## How, and what it costs

Two pieces, and the first is the library's own extension point:

- **the description** is a `HarnessProfile`, deepagents' supported way to reword
  a built-in tool. Profiles are keyed by model, and a pre-built instance
  resolves through its *provider* -- which LangChain derives from the class
  name, not from `_llm_type`. For this pool that is `"routerchatmodel"`. It
  reaches the main agent and its sub-agents alike, and nothing private is
  touched. Rename `RouterChatModel` and the override stops applying with
  nothing raised, which is why a test asserts the key against the live class.
- **the default** is not configurable. `DEFAULT_READ_LIMIT` is baked into
  `ReadFileSchema` at import, so the one seam left is setting that field's
  default. The names are public (`ReadFileSchema` is exported from the module,
  `model_fields` is pydantic's own API), which is as narrow as this gets until
  deepagents offers a setting.

`READ_FILE_TRUNCATION_MSG` is rebound for the same reason: upstream's message
advises reformatting the file, which is wrong for a long source file and never
mentions the window that would fetch the rest.

An earlier version of this module wrapped `_create_read_file_tool` and renamed
the arguments to `from_line`/`to_line`. That bought readable windows at the cost
of three private-name seams; the arguments are deepagents' `offset`/`limit`
again ([the deepagents skill](../../.claude/skills/deepagents/SKILL.md)).

## What still bounds a read

The ceiling moved; it was not removed. `FilesystemMiddleware` caps any tool
result at `tool_token_limit_before_evict` (20,000 tokens) and appends the
truncation message, this project's jail refuses files over 10 MB
([backend.py](backend.py)), and `offset`/`limit` are still there for the file
that needs a window.

[tests/agent/test_read_file.py](../../tests/agent/test_read_file.py) asserts
each seam still holds, so an upgrade fails in CI rather than at 3am in a run.
"""

from __future__ import annotations

from deepagents import HarnessProfileConfig, register_harness_profile
from deepagents.middleware import filesystem as _fs

# Lines. Larger than any file this jail will hand back, so it means "all of it"
# while staying the `int` the schema declares.
WHOLE_FILE = 1_000_000

# The key the pool's model lands on. deepagents resolves a pre-built model's
# profile through `_get_ls_params()["ls_provider"]`, which LangChain derives
# from the class name -- so this is `RouterChatModel` lowercased, and *not*
# `_llm_type` ("router"), which is never consulted.
# `test_the_router_key_is_the_models_own_provider_name` holds it to the class.
ROUTER_PROVIDER = "routerchatmodel"

READ_FILE_DESCRIPTION = """Reads a file from the filesystem.

Usage:
- The file_path parameter must be an absolute path, not a relative path.
- Reads the WHOLE file by default, which is what you normally want: one call,
  and you have the file.
- `offset` and `limit` take a window out of a file too long to hold in one
  read. `offset` is 0-indexed, so the line this tool printed as 148 is
  `offset=147`.
- Output is `cat -n` style, one line number per source line.
- You must read a file before `edit_file` will change it.
- Re-reading a file you have already read costs a request and tells you nothing
  new. Read it again only if you have written to it since."""

TRUNCATION_MSG = (
    "\n\n[Output was truncated: this file is too large to return in one read. "
    "Use offset and limit on {file_path} to read the rest -- the line numbers "
    "above are 1-based and offset is 0-based, so the next read starts at "
    "offset = (last line shown).]"
)


def _install() -> None:
    """Applied at import. Idempotent: every step is an assignment."""
    # 1. The description, through the library's own extension point.
    register_harness_profile(
        ROUTER_PROVIDER,
        HarnessProfileConfig(
            tool_description_overrides={"read_file": READ_FILE_DESCRIPTION}
        ),
    )

    # 2. The default, through the only names that reach it.
    limit = _fs.ReadFileSchema.model_fields["limit"]
    limit.default = WHOLE_FILE
    limit.description = (
        "Maximum number of lines to read. Omit it to read the whole file; "
        "set it only to take a window out of a file too large for one read."
    )
    _fs.ReadFileSchema.model_rebuild(force=True)

    # 3. The message a truncated read ends on.
    _fs.READ_FILE_TRUNCATION_MSG = TRUNCATION_MSG


_install()
