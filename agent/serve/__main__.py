"""CLI: python -m agent.serve

Serves every agent this pool can offer over HTTP, so a container can run the
harness and a caller can bind an agent to a workspace
([18. Serving](../../docs/18-serving.md)).

The CLI entry points are unchanged and remain the primary way to run one task:

    echo "..." | python -m agent.code    ../my-project
    echo "..." | python -m agent.explore ../my-project
    python -m agent.improve .

This is the same agents, addressable, for when the caller is not a person at a
terminal.

Environment:
  SERVE_HOST         interface to bind; default 0.0.0.0 (a container needs it)
  SERVE_PORT         port to bind; default 8080
  SERVE_TOKEN        the bearer token every route but /health requires. No
                     default, and no server without one unless
                     SERVE_ALLOW_ANONYMOUS=1 says the operator meant it
  WORKSPACES_DIR     the workspace root to bind agents under; default /workspaces
  SERVE_RECORD_DIR   where task records and run traces are written; unset
                     writes none
  ROUTER_CONFIG      pool config to load; unset uses llm_router/config.yaml
  LLM_ROUTER_USAGE_DIR  the usage ledger's directory. **Point this at a volume
                     in a container**, or every restart begins believing the
                     whole pool is fresh
                     (docs/17-deployment.md#176-what-has-to-change-first)
  AGENT_CONTEXT_FLOOR  override the input-token floor (default 128,000)
  AGENT_PEERS        agents the coding agent may run; unset means `explore`,
                     empty means none (docs/16-delegation.md)
  AGENT_DELEGATE_TIMEOUT  ceiling in seconds on one agent run (default 4 hours)
  IMPROVE_RECORDS    extra directories of recorded runs the `improve` agent may
                     read; point it at SERVE_RECORD_DIR to include live runs
                     (docs/19-improvement-agent.md)
  HARNESS_SHELL=1    give the served coding agent an unrestricted shell. Over a
                     network this is remote code execution by design, so it is
                     the operator's deliberate call and never a default
"""

import logging
import os
import sys
from pathlib import Path

from agent.utils.pool import CONTEXT_FLOOR
from agent.serve import app

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("LLMRouter").setLevel(logging.INFO)

# Models emit characters the Windows console codepage cannot encode. Same
# guard the two agent CLIs carry, for the same reason.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a reconfigurable stream
        pass


def _token() -> str:
    """The bearer token, or an exit.

    Refusing to start is the whole point. A server that quietly runs open
    because a variable was unset is exactly how a container with a shell and a
    git clone ends up on the public internet, and defaulting to a generated
    token would be worse -- it would come up, work for the operator who read
    the log line, and be silently regenerated on every restart.
    """
    token = os.environ.get("SERVE_TOKEN") or ""
    if token:
        if len(token) < 16:
            raise SystemExit("SERVE_TOKEN is too short to be a secret; use at "
                             "least 16 characters (`openssl rand -hex 32`).")
        return token
    if os.environ.get("SERVE_ALLOW_ANONYMOUS") == "1":
        logging.warning(
            "SERVE_ALLOW_ANONYMOUS=1: every route is open. Anyone who can "
            "reach this port can run an agent with a shell in your "
            "workspaces. Bind to localhost and nothing else.")
        return ""
    raise SystemExit(
        "SERVE_TOKEN is not set. This server runs agents that execute programs "
        "in a mounted filesystem, so it will not start without one:\n"
        "  SERVE_TOKEN=$(openssl rand -hex 32)\n"
        "Set SERVE_ALLOW_ANONYMOUS=1 only for a server bound to localhost.")


def main() -> None:
    host = os.environ.get("SERVE_HOST") or "0.0.0.0"
    port = int(os.environ.get("SERVE_PORT") or 8080)
    token = _token()

    floor = int(os.environ.get("AGENT_CONTEXT_FLOOR") or CONTEXT_FLOOR)
    record_dir = os.environ.get("SERVE_RECORD_DIR")
    allow_shell = os.environ.get("HARNESS_SHELL") == "1"
    if allow_shell:
        logging.warning("HARNESS_SHELL=1: the served coding agent has an "
                        "unrestricted shell. This is remote code execution to "
                        "anyone holding the token.")

    server = app.build(host, port, floor=floor,
                       allow_shell=allow_shell,
                       record_dir=Path(record_dir) if record_dir else None,
                       token=token)

    logging.info(f"Listening on http://{host}:{port} "
                 f"({'token required' if token else 'ANONYMOUS'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # A task in flight is not interrupted here -- the worker is a daemon
        # thread and the process is going down. That is the honest behaviour:
        # `tasks:cancel` already refuses to stop a running task for the same
        # reason (runner.py), and pretending Ctrl-C can do what the API will
        # not would leave a workspace in a state nobody described.
        logging.info("Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
