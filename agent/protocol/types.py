"""The A2A data model, as much of it as two local agents need.

[A2A](https://a2a-protocol.org) (Agent2Agent, Google, now a Linux Foundation
project) is the industry answer to "one agent asks another agent to do a job and
gets a deliverable back". MCP answers a different question -- an agent reaching a
*tool* -- so the split this repo follows is the standard one: MCP for tools, A2A
for agents.

**These names are not ours and must not drift.** Every field here serializes to
the spec's own JSON, camelCase included, so a `Task` written by the local
transport is the same object an HTTP binding would put on the wire
([16. The agent protocol](../../docs/16-agent-protocol.md)). That is the point of
adopting a standard rather than inventing a message format: the transport is
cheap to replace later and the schema is not.

What is deliberately *not* modelled, because nothing local uses it and A2A treats
each as optional:

- streaming (`message/stream`, SSE) and push notifications -- the local
  transport is a blocking call, so there is no gap to stream across;
- `security` and auth on the card -- there is no network to authenticate over;
- `input-required` round trips -- both agents run headless with nobody to ask.

Each is a field the spec already reserves, so adding one later widens this file
rather than reshaping it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    """An RFC 3339 timestamp, which is what the spec's `timestamp` field is."""
    return datetime.now(timezone.utc).isoformat()


class TaskState:
    """The lifecycle of one delegated task, verbatim from the spec.

    Terminal states are the four a caller can stop waiting on. `INPUT_REQUIRED`
    is reachable in A2A and not here -- a headless agent has nobody to ask -- but
    it is listed because a delegate that needs a decision should eventually be
    able to say so rather than guess, which is the failure the state exists to
    prevent.
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    TERMINAL = frozenset({COMPLETED, CANCELED, FAILED, REJECTED})


# --- Parts -----------------------------------------------------------------
# A Part is one piece of content inside a Message or an Artifact. The spec has
# three kinds and this uses all three: text for prose, file for a deliverable
# that stays on disk, data for machine-readable structure.


@dataclass
class TextPart:
    text: str

    def to_dict(self) -> dict:
        return {"kind": "text", "text": self.text}


@dataclass
class FilePart:
    """A pointer to a file, never its bytes.

    A2A allows either `uri` or inline base64 `bytes`, and this only ever writes
    the URI. Both agents share a filesystem, so shipping the content through the
    protocol would duplicate a file that is already there and push a research
    note into the caller's context whether or not it wanted to read it. The
    handoff stays what it always was -- files on disk -- and the protocol carries
    the *reference* ([15.1](../../docs/15-explorer.md#151-what-it-is-for)).
    """

    uri: str
    name: Optional[str] = None
    mime_type: str = "text/markdown"

    def to_dict(self) -> dict:
        file: dict[str, Any] = {"uri": self.uri, "mimeType": self.mime_type}
        if self.name:
            file["name"] = self.name
        return {"kind": "file", "file": file}


@dataclass
class DataPart:
    data: dict

    def to_dict(self) -> dict:
        return {"kind": "data", "data": self.data}


Part = Any  # TextPart | FilePart | DataPart


def part_from_dict(raw: dict) -> Part:
    kind = raw.get("kind")
    if kind == "text":
        return TextPart(text=raw.get("text", ""))
    if kind == "file":
        file = raw.get("file") or {}
        return FilePart(uri=file.get("uri", ""), name=file.get("name"),
                        mime_type=file.get("mimeType", "text/markdown"))
    if kind == "data":
        return DataPart(data=raw.get("data") or {})
    raise ValueError(f"unknown part kind: {kind!r}")


def text_of(parts: list) -> str:
    """Every TextPart run together. What an agent actually reads."""
    return "\n".join(p.text for p in parts if isinstance(p, TextPart)).strip()


# --- Message, Artifact, Task ------------------------------------------------


@dataclass
class Message:
    """One turn between two agents. `role` is the spec's, not a chat role.

    `user` means *the party that asked*, which for a delegated task is the
    calling agent rather than any human; `agent` is the one doing the work.
    """

    role: str
    parts: list = field(default_factory=list)
    message_id: str = field(default_factory=_uuid)
    task_id: Optional[str] = None
    context_id: Optional[str] = None

    @classmethod
    def user(cls, text: str, **kw) -> "Message":
        return cls(role="user", parts=[TextPart(text)], **kw)

    @classmethod
    def agent(cls, text: str, **kw) -> "Message":
        return cls(role="agent", parts=[TextPart(text)], **kw)

    @property
    def text(self) -> str:
        return text_of(self.parts)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"kind": "message", "role": self.role,
                               "messageId": self.message_id,
                               "parts": [p.to_dict() for p in self.parts]}
        if self.task_id:
            out["taskId"] = self.task_id
        if self.context_id:
            out["contextId"] = self.context_id
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> "Message":
        return cls(role=raw.get("role", "user"),
                   parts=[part_from_dict(p) for p in raw.get("parts") or []],
                   message_id=raw.get("messageId") or _uuid(),
                   task_id=raw.get("taskId"), context_id=raw.get("contextId"))


@dataclass
class Artifact:
    """A deliverable the task produced. For the explorer, one research note."""

    parts: list = field(default_factory=list)
    artifact_id: str = field(default_factory=_uuid)
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"artifactId": self.artifact_id,
                               "parts": [p.to_dict() for p in self.parts]}
        if self.name:
            out["name"] = self.name
        if self.description:
            out["description"] = self.description
        if self.metadata:
            out["metadata"] = self.metadata
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> "Artifact":
        return cls(parts=[part_from_dict(p) for p in raw.get("parts") or []],
                   artifact_id=raw.get("artifactId") or _uuid(),
                   name=raw.get("name"), description=raw.get("description"),
                   metadata=raw.get("metadata") or {})


@dataclass
class TaskStatus:
    state: str = TaskState.SUBMITTED
    message: Optional[Message] = None
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"state": self.state, "timestamp": self.timestamp}
        if self.message is not None:
            out["message"] = self.message.to_dict()
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskStatus":
        msg = raw.get("message")
        return cls(state=raw.get("state", TaskState.UNKNOWN),
                   message=Message.from_dict(msg) if msg else None,
                   timestamp=raw.get("timestamp") or _now())


@dataclass
class Task:
    """One unit of delegated work, and the only thing a caller waits on.

    `context_id` is what makes this more than a function call: it groups the
    tasks of one conversation, so a second question to the same delegate can
    carry the first one's context instead of restating it. Nothing local reads it
    yet -- each delegation is independent today -- but it is threaded through
    from the start, because retrofitting an identity onto records already written
    is how a protocol acquires a migration.

    `metadata` is the spec's extension point, and it is where anything this
    project needs that A2A has no opinion on goes: the standard has no concept of
    what a call costs, so a per-task call or token budget belongs here rather
    than in a field of our own
    ([16.6](../../docs/16-agent-protocol.md#166-what-a-delegation-costs)).
    """

    id: str = field(default_factory=_uuid)
    context_id: str = field(default_factory=_uuid)
    status: TaskStatus = field(default_factory=TaskStatus)
    artifacts: list = field(default_factory=list)
    history: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def state(self) -> str:
        return self.status.state

    @property
    def done(self) -> bool:
        return self.status.state in TaskState.TERMINAL

    def advance(self, state: str, message: Optional[Message] = None) -> "Task":
        self.status = TaskStatus(state=state, message=message)
        return self

    def to_dict(self) -> dict:
        return {"kind": "task", "id": self.id, "contextId": self.context_id,
                "status": self.status.to_dict(),
                # Iterated over copies, not the live lists. Not schema drift --
                # nothing about the output changes -- but a served task is
                # mutated by the worker thread while an HTTP reader may be
                # serializing it for `tasks/get`, and appending to a list that
                # another thread is iterating raises. The local transport never
                # had two threads and so never needed this
                # ([18.3](../../docs/18-serving.md#183-why-submission-does-not-block)).
                "artifacts": [a.to_dict() for a in list(self.artifacts)],
                "history": [m.to_dict() for m in list(self.history)],
                "metadata": self.metadata}

    @classmethod
    def from_dict(cls, raw: dict) -> "Task":
        return cls(id=raw.get("id") or _uuid(),
                   context_id=raw.get("contextId") or _uuid(),
                   status=TaskStatus.from_dict(raw.get("status") or {}),
                   artifacts=[Artifact.from_dict(a)
                              for a in raw.get("artifacts") or []],
                   history=[Message.from_dict(m)
                            for m in raw.get("history") or []],
                   metadata=raw.get("metadata") or {})


# --- Discovery --------------------------------------------------------------


@dataclass
class AgentSkill:
    """One thing an agent can be asked for. The unit of discovery in A2A."""

    id: str
    name: str
    description: str
    tags: list = field(default_factory=list)
    examples: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "description": self.description, "tags": list(self.tags),
                "examples": list(self.examples)}


@dataclass
class AgentCard:
    """What an agent says it can do. Served at `/.well-known/agent-card.json`
    over HTTP; here it is an object the registry hands out.

    `url` and `preferred_transport` are required by the spec and are honest
    rather than aspirational: a local agent's address is `local:<name>`, which
    says plainly that there is no endpoint to call. An HTTP binding replaces
    those two fields and nothing else.
    """

    name: str
    description: str
    version: str
    skills: list = field(default_factory=list)
    url: str = ""
    preferred_transport: str = "LOCAL"
    protocol_version: str = "0.3.0"
    default_input_modes: list = field(default_factory=lambda: ["text/plain"])
    default_output_modes: list = field(
        default_factory=lambda: ["text/plain", "text/markdown"])
    capabilities: dict = field(
        default_factory=lambda: {"streaming": False,
                                 "pushNotifications": False,
                                 "stateTransitionHistory": True})

    def to_dict(self) -> dict:
        return {"protocolVersion": self.protocol_version, "name": self.name,
                "description": self.description, "version": self.version,
                "url": self.url or f"local:{self.name}",
                "preferredTransport": self.preferred_transport,
                "capabilities": dict(self.capabilities),
                "defaultInputModes": list(self.default_input_modes),
                "defaultOutputModes": list(self.default_output_modes),
                "skills": [s.to_dict() for s in self.skills]}
