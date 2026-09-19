"""The HTTP binding: `http.server`, and nothing else.

Parsing and formatting only. Every decision about what runs, in which
workspace, is in [runner.py](runner.py); this file's whole job is to turn a
request into a call and a task into JSON.

**Why the standard library and not a framework.** The surface is nine routes
that take JSON and return JSON, fronted by a queue of depth one. `fastapi` plus
`uvicorn` would bring an ASGI stack, a validation library and their transitive
tree to serve that, and the project's standard is the simplest thing that works
with the standard library ahead of a dependency ([CLAUDE.md](../../CLAUDE.md)).
`ThreadingHTTPServer` handles concurrent *polling* fine, which is the only
concurrency there is: the work itself is single-file behind the worker.

**What a request is.** The same command line a person types, over a socket:
which agent, which workspace, and the task as one string. There was a protocol
here -- A2A's `AgentCard`, `Message` and `Task`, with a handler beside every
agent -- and it is gone with the rest of it
([Delegation](../../docs/agents/delegation.md)). An agent is a command; this
queues one and hands back an id to poll, because a run outlives a request.

    GET  /health                    liveness, unauthenticated
    GET  /v1/agents                 which agents this server can run
    POST /v1/agents/<name>/run      submit; 202 + a task
    GET  /v1/tasks                  recent tasks
    GET  /v1/tasks/<id>             one task
    POST /v1/tasks/<id>:cancel      cancel if not started
    GET  /v1/workspaces             what can be bound

**Everything but `/health` requires the bearer token.** This server runs a
shell in a directory it will happily clone a repository into; an open port
serving that is not a deployment, it is an incident
([The token is not optional](../../docs/operations/serving.md#the-token-is-not-optional)). `/health` is
exempt because a container healthcheck should not need a credential to ask
whether the process is alive, and it answers nothing else.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from agent import delegation
from agent.serve import workspace
from agent.serve.runner import Busy, Runner

logger = logging.getLogger("harness.serve")

# A brief is prose, not a payload. A megabyte is far more than any task
# description needs and far less than anything that would matter to hold.
MAX_BODY = 1_000_000
# How much of an oversized body to read and throw away so the sender gets a
# clean 413 rather than a reset. Bounded, because the point of the limit is not
# to be talked out of it.
DISCARD_CEILING = 8 * MAX_BODY

_AGENT_RUN = re.compile(r"^/v1/agents/([^/]+)/run$")
_TASK_GET = re.compile(r"^/v1/tasks/([^/:]+)$")
_TASK_CANCEL = re.compile(r"^/v1/tasks/([^/:]+):cancel$")


class _TooLarge(ValueError):
    """A body too big to drain. The connection is closed rather than read."""


class _Handler(BaseHTTPRequestHandler):
    """One request. `runner` and `token` are set on the server object."""

    server_version = "free_coding_agent"
    protocol_version = "HTTP/1.1"  # so keep-alive polling does not reconnect

    # --- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        """Through the project's logger, so it lands with everything else
        rather than on a bare stderr of its own."""
        logger.info("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": {"code": status, "message": message}})

    def _drain(self) -> bytes:
        """Read the whole request body, whatever this request turns out to be.

        Called before routing and before the auth check, and that ordering is
        the point. On a keep-alive connection an *unread* body stays in the
        socket, and the next request line is then parsed out of those leftover
        bytes -- so a POST that is refused (401 on a bad token, 404 on an
        unknown route) would poison the connection it was refused on. Draining
        first means a refusal costs the caller a status code and nothing else.

        An oversized body is discarded rather than kept, but it is still
        *read* -- up to a ceiling -- before the 413 goes out. Answering and
        closing while the client is still writing gives it a connection reset
        instead of the status code explaining what it did wrong, which is a bad
        way to learn that a brief was too long. Past the ceiling the connection
        closes on principle: at that point the sender is not making a mistake.
        """
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.close_connection = True
            remaining = min(length, DISCARD_CEILING)
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise _TooLarge(f"the request body exceeds {MAX_BODY:,} bytes")
        return self.rfile.read(length) if length > 0 else b""

    @staticmethod
    def _parse(raw: bytes) -> dict:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"the request body is not JSON: {exc}")
        if not isinstance(parsed, dict):
            raise ValueError("the request body must be a JSON object")
        return parsed

    def _authorized(self) -> bool:
        token = self.server.token
        if not token:  # started with --allow-anonymous; the operator was warned
            return True
        header = self.headers.get("Authorization") or ""
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        # Constant-time: a token comparison that returns early leaks the token
        # one character at a time to anyone willing to measure.
        return hmac.compare_digest(header[len(prefix):], token)

    # --- routing -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        path = urlparse(self.path).path
        if path == "/health":
            return self._health()
        if not self._authorized():
            return self._error(401, "a bearer token is required")
        try:
            self._get(path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("GET %s failed", path)
            self._error(500, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        # Before the auth check, so a refusal leaves the connection usable.
        try:
            raw = self._drain()
        except _TooLarge as exc:
            return self._error(413, str(exc))
        if not self._authorized():
            return self._error(401, "a bearer token is required")
        try:
            self._post(path, raw)
        except ValueError as exc:  # a malformed body
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("POST %s failed", path)
            self._error(500, str(exc))

    def _get(self, path: str) -> None:
        runner = self.server.runner

        if path == "/v1/agents":
            # Name, description, and the command it is. The command is the
            # point: a caller can run exactly the same thing locally, and does
            # not have to take this server's word for what it did
            # ([Delegation](../../docs/agents/delegation.md)).
            return self._send(200, {"agents": [
                {"name": name,
                 "description": delegation.AGENTS[name][1],
                 "delivers": delegation.AGENTS[name][2],
                 "command": (f"python -m {delegation.AGENTS[name][0]} "
                             f"<workspace> --task ...")}
                for name in runner.agents]})

        if path == "/v1/workspaces":
            return self._send(200, {"root": str(workspace.root()),
                                    "workspaces": workspace.listing()})

        if path == "/v1/tasks":
            query = parse_qs(urlparse(self.path).query)
            try:
                limit = min(int(query.get("limit", ["50"])[0]), 500)
            except ValueError:
                return self._error(400, "`limit` must be an integer")
            return self._send(200, {"tasks": [t.to_dict()
                                              for t in runner.recent(limit)]})

        match = _TASK_GET.match(path)
        if match:
            task = runner.get(match.group(1))
            if task is None:
                return self._error(404, f"no task {match.group(1)!r}")
            return self._send(200, task.to_dict())

        return self._error(404, f"no route {path!r}")

    def _post(self, path: str, raw: bytes) -> None:
        runner = self.server.runner

        match = _AGENT_RUN.match(path)
        if match:
            return self._run(match.group(1), self._parse(raw))

        match = _TASK_CANCEL.match(path)
        if match:
            task = runner.cancel(match.group(1))
            if task is None:
                return self._error(404, f"no task {match.group(1)!r}")
            return self._send(200, task.to_dict())

        return self._error(404, f"no route {path!r}")

    # --- the one method that does anything ---------------------------------

    def _run(self, agent: str, body: dict) -> None:
        """Queue one run, answering 202 with the task rather than waiting.

        The body is the command line: `task` is what would follow `--task`, and
        `workspace` is what would be the workdir argument.
        """
        text = body.get("task")
        if not isinstance(text, str) or not text.strip():
            return self._error(400, "`task` is required: the string that would "
                                    "follow --task on the command line")

        name = body.get("workspace")
        if not isinstance(name, str) or not name:
            return self._error(400, "`workspace` is required: it names the "
                                    "directory under the server's workspace "
                                    "root that the agent is bound to")

        metadata = body.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            return self._error(400, "`metadata` must be an object")

        try:
            task = self.server.runner.submit(
                agent, text.strip(), workspace_name=name,
                repo=body.get("repo"), metadata=metadata)
        except KeyError as exc:  # unknown agent
            return self._error(404, str(exc).strip("'"))
        except workspace.BadWorkspace as exc:
            return self._error(400, str(exc))
        except Busy as exc:
            return self._error(503, str(exc))

        # 202, not 200: the work has been accepted and has not been done. The
        # Location header is where to poll, so a caller needs to read neither
        # this docstring nor the body to find it.
        body_out = task.to_dict()
        payload = json.dumps(body_out, ensure_ascii=False,
                             default=str).encode("utf-8")
        self.send_response(202)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Location", f"/v1/tasks/{task.id}")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _health(self) -> None:
        """Alive, and enough state to tell a wedged server from a busy one.

        A healthcheck that only says "the socket is open" cannot distinguish a
        server working a four-hour task from one whose worker thread died, and
        those want opposite responses from an operator.
        """
        runner = self.server.runner
        self._send(200, {
            "status": "ok",
            "agents": sorted(runner.agents),
            "providers": runner.providers,
            "eligible_providers": runner.members,
            "running": runner.running,
            "queued": runner.queued,
            "workspace_root": str(workspace.root()),
        })


class Server(ThreadingHTTPServer):
    """`ThreadingHTTPServer` carrying the runner and the token."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, runner: Runner, token: Optional[str]) -> None:
        super().__init__(address, _Handler)
        self.runner = runner
        self.token = token


def build(host: str, port: int, *, floor: int, record_dir,
          token: Optional[str]) -> Server:
    """The server, with its worker already running.

    Everything that can fail at start-up does so here -- no providers, no member
    wide enough, a port already taken -- so a container that comes up is a
    container that can serve.
    """
    runner = Runner(floor=floor, record_dir=record_dir)
    return Server((host, port), runner, token)
