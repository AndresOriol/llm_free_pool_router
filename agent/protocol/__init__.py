"""How one agent asks another for work: the A2A data model, bound locally.

Read [16. The agent protocol](../../docs/16-agent-protocol.md) before changing
anything here. The short version: the vocabulary is
[A2A](https://a2a-protocol.org)'s and must not drift from it, the transport is a
Python call and is meant to be replaceable, and the deliverable stays a file on
disk that the protocol only points at.
"""

from agent.protocol.local import LocalTransport, TaskStore, render
from agent.protocol.registry import AgentRegistry, directory_section
from agent.protocol.tools import make_delegate_tool
from agent.protocol.types import (AgentCard, AgentSkill, Artifact, DataPart,
                                  FilePart, Message, Task, TaskState,
                                  TaskStatus, TextPart)

__all__ = ["AgentCard", "AgentRegistry", "AgentSkill", "Artifact", "DataPart",
           "FilePart", "LocalTransport", "Message", "Task", "TaskState",
           "TaskStatus", "TaskStore", "TextPart", "directory_section",
           "make_delegate_tool", "render"]
