# The harness as a long-running container: the pool, both agents, and the HTTP
# binding that makes them addressable (docs/operations/serving.md).
#
# This image is sized and shaped by docs/operations/deployment.md, and two of its
# choices are load-bearing rather than habit:
#
#   * `git` is installed because it is not optional here. The coding agent
#     commits its own work through the jail's allowlist, and the server clones
#     a repository to bind an agent to one.
#   * Three directories are declared as volumes. The usage ledger especially:
#     it lives inside the package tree by default, and a container that starts
#     with an empty ledger believes the whole pool is fresh, hammers accounts
#     that already spent their day, and rediscovers the wall by 429
#     (docs/operations/deployment.md#what-has-to-change-first).
#
# Sizing: 1 vCPU / 2 GiB for the agent, 4 GiB if it runs a heavy test suite in
# the jail. 512 MB is not a candidate -- the imports alone are ~186 MB before
# any conversation (docs/operations/deployment.md#what-the-workload-actually-is).

FROM python:3.12-slim

# git: the agent's own commits and the server's clone.
# ca-certificates: every provider and Tavily are HTTPS.
# No curl -- the healthcheck below uses the interpreter that is already here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first, so an edit to the source does not reinstall the stack.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/
COPY llm_router/ ./llm_router/
# The harness's own tests ship too. `pytest` is installed regardless -- the
# agent runs it against the *target* project inside the jail -- so carrying
# them costs little and buys a smoke check on a built image, before it is
# given a key or a workspace:
#
#   docker run --rm free-coding-agent:latest python -m pytest tests/agent/test_serve.py
#
# That file needs no keys and no network. `tests/` as a whole does not run
# here: two modules import the eval harness, and `evals/` is not in the image
# (31 MB, nearly all of it recorded run output, and nothing serves from it).
COPY tests/ ./tests/

# Not root. The agent runs `python` in the jail, which is arbitrary code
# execution by design -- the container is the boundary the backend's docstring
# says it always was, and a boundary that runs as root is a thinner one.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /workspaces /var/lib/agent/usage /var/lib/agent/records \
    && chown -R agent:agent /workspaces /var/lib/agent /app

# WORKSPACES_DIR      where an agent is bound: mount a host directory here, or
#                     let the server clone into it (docs/operations/serving.md#binding-an-agent-to-a-repository-or-a-filesystem).
# LLM_ROUTER_USAGE_DIR  what the accounts have spent. This one must outlive the
#                     container -- see the header.
# SERVE_RECORD_DIR    task records and run traces, one directory per task.
ENV WORKSPACES_DIR=/workspaces \
    LLM_ROUTER_USAGE_DIR=/var/lib/agent/usage \
    SERVE_RECORD_DIR=/var/lib/agent/records \
    SERVE_HOST=0.0.0.0 \
    SERVE_PORT=8080

VOLUME ["/workspaces", "/var/lib/agent/usage", "/var/lib/agent/records"]

USER agent

# git refuses to operate in a directory owned by another user, which is exactly
# what a bind-mounted host repository looks like from in here. The workspace
# root is declared safe rather than every workspace individually, because the
# names are not known until a caller asks for one.
RUN git config --global --add safe.directory '*' \
    && git config --global user.email "agent@free-coding-agent.local" \
    && git config --global user.name "free_coding_agent"

EXPOSE 8080

# Liveness, not readiness: /health answers while a four-hour task is running and
# reports `running` and `queued` so an operator can tell a busy server from a
# wedged one. Generous timings because start-up builds the pool and the cold
# imports alone take ~6 s.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import os,sys,urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('SERVE_PORT','8080')+'/health', timeout=5).status == 200 else 1)"

CMD ["python", "-m", "agent.serve"]
