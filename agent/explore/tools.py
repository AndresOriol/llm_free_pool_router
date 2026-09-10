"""The tool surface this agent is offered, decided rather than inherited.

`create_deep_agent` installs one suite on every agent built with it --
`write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`,
`execute` and `task` -- and the suite is shaped for the job the coding agent
does: find your way around a repository you were dropped into, then change it.
The explorer's job is the other one. It arrives knowing what to find out, and
the thing it must not do is spend a free-tier request rediscovering a project
that another agent already knows
([15.5](../../docs/15-explorer.md#155-what-it-is-allowed-to-do)).

Two edits, and both are about the same scarce thing -- a model call against a
per-day request budget:

1. **Four tools are not offered.** `ls`, `glob` and `grep` are repository
   discovery, and this agent has two sources of paths that cost nothing: the
   listing of its own `/research` directory, restated in the system prompt on
   every call ([notes.py](notes.py)), and the paths the request itself names.
   `execute` is offered by the framework and refused by the backend on every
   command, so it can only ever be a step spent learning what the schema could
   have said -- the failure this project already names, that a description
   advertising a capability the backend does not have is a measured cause of
   failed calls ([agent/runtime/tools.py](../runtime/tools.py)).
2. **Four descriptions are rewritten.** Upstream's are written for a coding
   agent: `read_file` explains itself in terms of *codebase exploration*,
   `write_file` opens by telling the agent to prefer editing an existing file,
   and `write_todos` ends by insisting the deliverable is the final message.
   For this agent the last one is false -- the deliverable is a file on disk and
   the closing message is clipped before its caller ever sees it
   ([a2a.py](a2a.py)) -- and a description contradicting the system prompt is
   worse than a thin one.

**Why a middleware and not a `HarnessProfile`.** Upstream's documented way to
drop a built-in is to register a profile against the model
(`deepagents.profiles`). Profiles are keyed by provider or `provider:model`, and
the model here is one `RouterChatModel` shared by *every* agent in this repo --
so a registration narrow enough to describe the explorer does not exist, and one
that matched would take the coding agent's shell away with it. Filtering the
request is local, applies to exactly the agent it is installed on, and uses only
the middleware API. It is installed on the researcher sub-agent too, which gets
its own copy of the filesystem tools ([session.py](session.py)).

This filters what the *model is offered*. It is not a boundary: the jail is
([agent/runtime/backend.py](../runtime/backend.py)), and `execute` still refuses
everything whether or not this is installed.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger("harness.explore")


# Not offered to the model. See the module docstring for why each one goes; the
# names are the framework's own (`deepagents.middleware.filesystem`).
EXCLUDED = ("ls", "glob", "grep", "execute")


READ_FILE = """Read one file from this workspace, by its exact path.

You cannot browse this workspace. There is no `ls`, no `glob` and no `grep`,
and that is deliberate: exploring a project is the coding agent's job and every
listing here would cost a model call out of a daily budget. Every path you can
read reaches you already:

- **Your own notes.** The system prompt lists everything under `/research/` on
  every turn, with each note's title and size. That listing is current.
- **Project files the request named.** A brief that wants you to read the code
  is expected to give the paths. If you need a file nobody named, say so in your
  findings rather than guessing at a path.

Usage:
- Paths are absolute within this workspace: `/research/pricing.md`,
  `/llm_router/config.yaml`. There is no filesystem above `/`.
- Reads the first 100 lines by default. Pass `offset` and `limit` to page
  through a long file rather than pulling all of it into the conversation.
- Output is `cat -n` style, one line number per source line.
- You must read a file before `edit_file` will change it.
- Re-reading a file you already read costs a request and tells you nothing new.
  Read a note again only when you wrote to it since."""


WRITE_FILE = """Create a file, or replace an existing one whole.

This is how research survives the run. Your closing message is read by nobody
and is clipped before it reaches whoever asked; the files under `/research/` are
the deliverable, because the thing that reads them next -- a coding agent, a
person, you tomorrow -- was not here for the conversation.

Usage:
- Write the first note as soon as you hold evidence worth keeping, not at the
  end. A run that dies with everything in its context leaves nothing behind; one
  that dies having written two notes leaves two notes.
- **This replaces the whole file.** Writing to a path that already exists
  destroys what was there. The system prompt lists what exists; choose an unused
  path for a new topic, and use `edit_file` to extend a note you already wrote.
- Keep every claim next to the URL it came from. A figure whose source cannot be
  found in the file is the failure this agent exists to avoid.
- Say what you could not establish. An explicit "not found" is a result; silence
  reads as "not looked for" and sends the next reader over ground you covered."""


EDIT_FILE = """Replace an exact string in a file you have already read.

The way to extend a note as evidence arrives, instead of holding findings in
your head and rewriting the whole file at the end.

Usage:
- Read the file first; this is refused otherwise.
- `old_string` must appear exactly once, whitespace included, unless you pass
  `replace_all`. Quote enough surrounding text to make it unique.
- To append a section, anchor on the last heading or line you wrote and put both
  it and the new text in `new_string`.
- Prefer this to `write_file` on a path that already holds work: `write_file`
  replaces the file, and an accidental overwrite of your own note costs the
  searches that produced it."""


WRITE_TODOS = """Plan the research, and keep the plan current as it changes.

Use it on any question that needs more than one workstream: it is what lets a
long run survive its own summarization, because the plan stays in state while
the conversation behind it is compacted away.

## What a todo is here

One workstream: a question whose answer could change the reader's decision, the
evidence that would settle it, and where its findings will be written. Not
"search for competitors" but "who already sells this locally, and at what
price -- vendor pages and a local quote -- to /research/market-vendors.md".

## How to use it

1. Write the plan before the first delegation, once you know the decision the
   reader has to make and the constraints on it.
2. Mark a workstream `in_progress` when you delegate it and `completed` when its
   findings are saved -- not when the sub-agent replies. Keep at least one item
   `in_progress` while work remains.
3. Revise the list as findings come back. A contradiction between two sources,
   or a gap that would change the recommendation, is a new todo; a question that
   turned out not to matter should be deleted rather than quietly dropped.
4. Never mark a workstream complete because its searches ran out. Record what is
   still unanswered -- an unresolved question you can name is worth more to the
   next reader than a confident answer nobody can trace.

## What it is not

It is not the deliverable, and neither is your final message. Ignore any
guidance saying the answer must appear as text in your last turn: here the
answer is the file under `/research/`, and the reply that ends the run is a
sentence naming which files you wrote. Skip this tool entirely for a single
factual question that one sub-agent can answer."""


# Replacing, not appending: upstream's text for these four is written for an
# agent editing a repository, and two of the four contradict this agent's
# deliverable outright. The rest of the suite -- `write_todos`' system-prompt
# section, `task`, and the two research tools in `research_tools.py` -- is left
# as it is, because it already says something true here.
DESCRIPTIONS: Mapping[str, str] = {
    "read_file": READ_FILE,
    "write_file": WRITE_FILE,
    "edit_file": EDIT_FILE,
    "write_todos": WRITE_TODOS,
}


def _name(tool: Any) -> str:
    """A tool's name, whether it is a `BaseTool` or a raw schema dict."""
    if isinstance(tool, dict):
        name = tool.get("name")
    else:
        name = getattr(tool, "name", None)
    return name if isinstance(name, str) else ""


def _tailor(tools: list) -> list:
    """`tools` with the excluded ones dropped and the rewritten ones rewritten.

    Copies rather than mutates. The tool objects are shared with the graph's
    tool node and with every other agent built over the same backend, and
    rewriting one in place would change a description the coding agent relies on.
    """
    result = []
    for tool in tools:
        name = _name(tool)
        if name in EXCLUDED:
            continue
        description = DESCRIPTIONS.get(name)
        if description is None:
            result.append(tool)
        elif isinstance(tool, dict):
            result.append({**tool, "description": description})
        else:
            result.append(tool.model_copy(update={"description": description}))
    return result


class ToolSurfaceMiddleware(AgentMiddleware):
    """Offer the model this agent's tools, described for research."""

    def __init__(self) -> None:
        super().__init__()
        self._logged = False

    def _apply(self, request):
        tailored = _tailor(list(request.tools))
        if not self._logged:
            self._logged = True
            logger.info("Research tool surface: "
                        f"{', '.join(_name(t) for t in tailored)}")
        return request.override(tools=tailored)

    def wrap_model_call(self, request, handler: Callable):
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler: Callable[..., Awaitable]):
        return await handler(self._apply(request))
