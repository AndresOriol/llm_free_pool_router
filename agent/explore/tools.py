"""Which tools this agent is offered, and which words describe them.

`create_deep_agent` installs one suite on every agent built with it --
`write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`,
`execute`, `task` -- shaped for the coding agent's job: find your way around a
repository you were dropped into, then change it. This one is given its question
and told which files matter, so it does three things to that suite, all of them
declarative:

- `EXCLUDED` names the tools it is not offered;
- `descriptions/` holds one Markdown file per tool, and that file *is* the
  description the model reads;
- `PRUNED` names the framework's own prompt sections that describe the tools it
  no longer has, so the prompt says only what is true.

**Everything this module decides is a list or a file.** The argument for each
entry is in the wiki rather than here
([15.5](../../docs/15-explorer.md#155-what-it-is-allowed-to-do)), and changing
what the agent is told means editing Markdown, not Python.

The one thing that has to be code is *when*: the framework injects its tools and
its prompt sections per model call, so the edit is a middleware rather than an
argument to a constructor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

from deepagents.graph import BASE_AGENT_PROMPT
from deepagents.middleware.filesystem import (EXECUTION_SYSTEM_PROMPT,
                                              FILESYSTEM_SYSTEM_PROMPT)
from deepagents.middleware.subagents import TASK_SYSTEM_PROMPT
from langchain.agents.middleware.todo import WRITE_TODOS_SYSTEM_PROMPT
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import SystemMessage

logger = logging.getLogger("harness.explore")

# The default research directory, and the one every prompt is written against.
DEFAULT_DIR = "research"

# Not offered to the model: repository discovery this agent does not do, and an
# `execute` the backend refuses on every command.
EXCLUDED = ("ls", "glob", "grep", "execute")

# One file per tool, named for the tool. A tool that has no file here keeps the
# framework's own description.
DESCRIPTIONS = {
    path.stem: path.read_text(encoding="utf-8").strip()
    for path in sorted((Path(__file__).parent / "descriptions").glob("*.md"))
}

# Framework prompt sections that stop being true once the tools above are gone,
# or that were never true here: the filesystem section lists `ls`, `glob` and
# `grep` as available; the execution section documents the refused `execute`;
# the task section re-answers what the research workflow already covers; the
# todo section ends "write your final answer in the message AFTER your last
# `write_todos` call"; and the SDK preamble opens "The user can see your
# responses and tool outputs in real time". The last two are the expensive ones
# -- this agent's answer is a file, and its closing message only points at it.
#
# Imported rather than pasted, so an upstream rewording is caught by a test
# instead of leaving half an edit nobody notices (tests/agent/test_explore.py).
PRUNED = (BASE_AGENT_PROMPT, FILESYSTEM_SYSTEM_PROMPT, EXECUTION_SYSTEM_PROMPT,
          TASK_SYSTEM_PROMPT, WRITE_TODOS_SYSTEM_PROMPT)


def retarget(text: str, research_dir: str) -> str:
    """`text` with every `/research/` pointed at this run's directory.

    A substitution over assembled prose, so prompts and descriptions can go on
    saying `/research/` and mean whichever directory the run was given. Apply it
    once per string: twice gives `/research/x/x/final_report.md`.
    """
    target = research_dir.strip("/") or DEFAULT_DIR
    if target == DEFAULT_DIR:
        return text
    return text.replace(f"/{DEFAULT_DIR}/", f"/{target}/")


class ToolSurfaceMiddleware(AgentMiddleware):
    """Drop the excluded tools, describe the rest in our words, prune the prompt.

    Installed on the orchestrator and on both sub-agents, because the framework
    gives each of them its own copy of the filesystem tools -- and a tailoring
    that only holds for the agent in front is not a tailoring.
    """

    def __init__(self, research_dir: str = DEFAULT_DIR) -> None:
        super().__init__()
        self.research_dir = research_dir

    def _describe(self, tool):
        """`tool` with our description, if we wrote one.

        Copies rather than mutates: the tool objects are shared with the graph
        and with every other agent built over the same backend.
        """
        text = DESCRIPTIONS.get(getattr(tool, "name", ""))
        if text is None:
            return tool
        return tool.model_copy(
            update={"description": retarget(text, self.research_dir)})

    def _apply(self, request):
        tools = [self._describe(t) for t in request.tools
                 if getattr(t, "name", "") not in EXCLUDED]
        system = request.system_message
        if system is not None:
            text = system.text
            for section in PRUNED:
                text = text.replace(section, "")
            while "\n\n\n" in text:
                text = text.replace("\n\n\n", "\n\n")
            system = SystemMessage(text.strip())
        return request.override(tools=tools, system_message=system)

    def wrap_model_call(self, request, handler: Callable):
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler: Callable[..., Awaitable]):
        return await handler(self._apply(request))
