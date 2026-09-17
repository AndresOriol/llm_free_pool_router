"""What the framework injects that a harness profile cannot reach.

An agent here fits deepagents' tool surface to its job through a
`HarnessProfileConfig`: `excluded_tools` and `tool_description_overrides`,
applied by the framework to the agent and its sub-agents alike. Two things stop
short of that route, and both are library limits rather than preferences:

- **prompt sections.** The framework appends its own prose per call -- the base
  agent prompt, a section per middleware -- and no field suppresses it.
  `FilesystemMiddleware` and `SubAgentMiddleware` are in `_REQUIRED_MIDDLEWARE`,
  so `excluded_middleware` refuses to drop them, and excluding a middleware
  would take its tools with it anyway. Which sections to cut is each agent's
  choice, so the caller passes them -- imported from deepagents rather than
  quoted, so an upstream rewording fails a test instead of silently leaving the
  text in ([15.5.3](../../docs/15-explorer.md)).
- **`PROFILE_BLIND` descriptions.** See below.

The middleware is passed to every sub-agent spec as well: a caller's
`middleware=` is not installed on the sub-agents deepagents builds, so without
it `task` hands the work to a conversation that reads upstream's text.
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable, Sequence

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import SystemMessage

# Tools `tool_description_overrides` does not reach. deepagents wires that field
# to `FilesystemMiddleware`'s tools and to `SubAgentMiddleware`'s `task`, but
# `write_todos` comes from langchain's `TodoListMiddleware`, which takes a
# `tool_description` deepagents never passes on. Excluding that middleware and
# supplying a configured one does not work either: the profile's exclusion is
# applied to the caller's middleware as well, so both copies are stripped and
# the tool disappears entirely (verified against 0.6.12).
PROFILE_BLIND = ("write_todos",)

NL3 = r"\n{3,}"
NL2 = "\n\n"


class FrameworkSurface(AgentMiddleware):
    """Rewords the `PROFILE_BLIND` tools and cuts framework prose, per call.

    `described` maps a tool name to its description; only `PROFILE_BLIND` names
    are taken from it, because every other tool has the profile. `pruned` is
    the framework sections to remove from the system prompt, verbatim.
    """

    def __init__(self, described: dict, pruned: Sequence[str] = ()) -> None:
        super().__init__()
        self.described = {name: described[name] for name in PROFILE_BLIND
                          if name in described}
        self.pruned = tuple(pruned)

    def _apply(self, request):
        # Copied, not edited: the tool objects are shared with the graph and
        # with any other agent over the same backend.
        tools = [tool.model_copy(update={"description": self.described[tool.name]})
                 if getattr(tool, "name", "") in self.described else tool
                 for tool in request.tools]

        system = request.system_message
        if system is not None and self.pruned:
            text = system.text
            for section in self.pruned:
                text = text.replace(section, "")
            system = SystemMessage(re.sub(NL3, NL2, text).strip())
        return request.override(tools=tools, system_message=system)

    def wrap_model_call(self, request, handler: Callable):
        return handler(self._apply(request))

    async def awrap_model_call(self, request, handler: Callable[..., Awaitable]):
        return await handler(self._apply(request))
