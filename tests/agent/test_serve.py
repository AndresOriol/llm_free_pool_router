"""The HTTP binding, checked without the pool and without the network.

Three things can be wrong here silently, and each is what a section below
watches.

The first is **the boundary**. Over the CLI the workdir is `sys.argv[1]` and the
operator owns it; over HTTP it is a string from a request, and the jail
constrains the agent only *after* it has been pointed somewhere
([serve/workspace.py](../../agent/serve/workspace.py)). So traversal, absolute
paths and cloning over existing work are asserted by outcome, not by inspection.

The second is **an open door**. Every route but `/health` requires the token,
and a route added later that forgets to check is indistinguishable from one that
checks, right up until someone finds it. The check is asserted per route rather
than once.

The third is **submission that blocks**. A run lasts hours and every platform in
front of this kills a request in minutes
([Why request/response platforms cannot host it](../../docs/operations/deployment.md#why-requestresponse-platforms-cannot-host-it)),
so `run` answering 202 immediately is the property that makes the
deployment possible at all -- and the easiest one to lose to a refactor that
"simplifies" the queue away.

No provider is ever called and no key is needed. `Server` takes its runner as an
argument, so the HTTP section drives the real request handler over a stub; the
engine section at the bottom builds a *real* `Runner` -- real queue, real
worker, real lifecycle -- with only the pool check and the agent command replaced,
because the pool is the one part that needs keys.
"""

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent.serve.task import CANCELED, DONE, FAILED, QUEUED, RUNNING, Task
from agent.serve import workspace
from agent.serve.app import Server
from agent.serve.runner import Busy

TOKEN = "0123456789abcdef0123456789abcdef"


# --- the workspace boundary -------------------------------------------------


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "workspaces"
    base.mkdir()
    monkeypatch.setenv("WORKSPACES_DIR", str(base))
    return base


@pytest.mark.parametrize("name", [
    "../etc", "..", ".", "a/b", "a\\b", "/etc/passwd", "C:\\Windows",
    "", "-leading-dash", "name with spaces", "x" * 65, "a\x00b",
])
def test_a_workspace_name_is_never_a_path(root, name):
    """Refused at the name, before any path arithmetic.

    The containment check afterwards would catch most of these too. Both exist:
    normalization is where this class of bug lives, and refusing the separator
    outright means the interesting cases never reach it.
    """
    with pytest.raises(workspace.BadWorkspace):
        workspace.resolve(name)


def test_a_valid_name_resolves_under_the_root(root):
    assert workspace.resolve("closet_ai") == (root / "closet_ai").resolve()


def test_ensure_creates_the_directory_when_no_repo_is_given(root):
    path = workspace.ensure("fresh")
    assert path.is_dir() and path.parent == root.resolve()


def test_ensure_reuses_an_existing_workspace(root):
    (root / "existing").mkdir()
    (root / "existing" / "NOTES.md").write_text("work in progress")
    assert workspace.ensure("existing") == (root / "existing").resolve()
    assert (root / "existing" / "NOTES.md").exists()


def test_cloning_over_existing_work_is_refused(root):
    """The refusal that protects a week of unpushed agent commits.

    From here a non-empty workspace and a previous clone of the same repository
    are indistinguishable, and one of the two guesses destroys work.
    """
    (root / "busy").mkdir()
    (root / "busy" / "committed.py").write_text("print(1)")
    with pytest.raises(workspace.BadWorkspace, match="already exists"):
        workspace.ensure("busy", repo="https://example.invalid/x.git")


@pytest.mark.parametrize("repo", [
    "file:///etc", "/etc/passwd", "--upload-pack=touch /tmp/pwned",
    "ext::sh -c whoami", "git@github.com:x/y.git",
])
def test_only_https_and_ssh_urls_are_cloneable(root, repo):
    """`git clone` takes arguments that read local files and run local
    programs, and this one arrives over the network."""
    with pytest.raises(workspace.BadWorkspace):
        workspace.ensure("w", repo=repo)


def test_cloning_a_real_repository_binds_it(root, tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git_init(origin)
    (origin / "README.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=origin, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=origin, check=True,
                   capture_output=True)

    # A local path is not a URL this server clones, which is the point of the
    # test above -- so the scheme check is bypassed deliberately here to prove
    # the clone itself works, rather than weakening the rule for the tests.
    path = workspace.resolve("cloned")
    path.mkdir()
    workspace._clone(origin.as_uri(), path)
    assert (path / "README.md").read_text() == "hello"
    assert (path / ".git").exists()


def test_listing_reports_what_can_be_bound(root):
    (root / "alpha").mkdir()
    (root / "beta").mkdir()
    (root / "beta" / "f.txt").write_text("x")
    names = {w["name"]: w for w in workspace.listing()}
    assert set(names) == {"alpha", "beta"}
    assert names["alpha"]["empty"] and not names["beta"]["empty"]


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path,
                   check=True, capture_output=True)



# --- the HTTP surface -------------------------------------------------------


class StubRunner:
    """A runner whose tasks stay queued forever.

    Enough of the real one's surface for `app.py` to drive: the point of these
    tests is the binding, and a real runner would need keys to boot.
    """

    def __init__(self, root_dir):
        self.agents = ["code"]
        self.providers = 3
        self.members = 2
        self.running = None
        self.queued = 0
        self.busy = False
        self.tasks = {}
        self.submitted = []

    def submit(self, agent, request, *, workspace_name, repo=None,
               metadata=None):
        if agent not in self.agents:
            raise KeyError(f"no agent named {agent!r} is served here.")
        if self.busy:
            raise Busy("32 tasks are already queued")
        workdir = workspace.ensure(workspace_name, repo)
        task = Task(agent=agent, request=request, workspace=workspace_name,
                    workdir=str(workdir), metadata=dict(metadata or {}))
        self.tasks[task.id] = task
        self.submitted.append((agent, request, workspace_name, repo))
        return task

    def get(self, task_id):
        return self.tasks.get(task_id)

    def cancel(self, task_id):
        task = self.tasks.get(task_id)
        if task and not task.done:
            task.advance(CANCELED)
        return task

    def recent(self, limit=50):
        return list(self.tasks.values())[-limit:]


@pytest.fixture
def server(root):
    srv = Server(("127.0.0.1", 0), StubRunner(root), TOKEN)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(server, method, path, body=None, token=TOKEN):
    """(status, parsed JSON). An error status is a result here, not a raise."""
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_health_needs_no_token_and_says_more_than_alive(server):
    """A healthcheck that only proves the socket is open cannot tell a server
    working a four-hour task from one whose worker thread died."""
    status, body = call(server, "GET", "/health", token=None)
    assert status == 200
    assert body["status"] == "ok"
    assert body["agents"] == ["code"]
    assert body["queued"] == 0 and body["running"] is None
    assert body["providers"] == 3 and body["eligible_providers"] == 2


@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/agents"),
    ("GET", "/v1/tasks"),
    ("GET", "/v1/tasks/anything"),
    ("GET", "/v1/workspaces"),
    ("POST", "/v1/agents/code/run"),
    ("POST", "/v1/tasks/anything:cancel"),
])
def test_every_route_but_health_requires_the_token(server, method, path):
    status, _ = call(server, method, path,
                     body={} if method == "POST" else None, token=None)
    assert status == 401


def test_a_wrong_token_is_refused(server):
    status, _ = call(server, "GET", "/v1/agents", token="x" * 32)
    assert status == 401


def test_an_agent_is_listed_as_the_command_it_is(server):
    """No card: a caller is told the command line, and can run the same thing
    locally rather than take this server's word for what it did."""
    status, body = call(server, "GET", "/v1/agents")
    assert status == 200
    [agent] = body["agents"]
    assert agent["name"] == "code"
    assert agent["command"].startswith("python -m agent.code ")
    assert "--task" in agent["command"]


def test_submission_answers_202_immediately_with_somewhere_to_poll(server):
    """The property the whole deployment rests on: accepted, not done."""
    status, task = call(server, "POST", "/v1/agents/code/run",
                        {"task": "Fix the parser", "workspace": "proj"})
    assert status == 202
    assert task["state"] == QUEUED
    assert task["request"] == "Fix the parser"
    assert task["workspace"] == "proj"
    # And it is readable at the id it was given.
    status, fetched = call(server, "GET", f"/v1/tasks/{task['id']}")
    assert status == 200 and fetched["id"] == task["id"]


def test_the_workspace_binds_the_agent_to_a_directory(server, root):
    call(server, "POST", "/v1/agents/code/run",
         {"task": "go", "workspace": "bound"})
    _, _, name, _ = server.runner.submitted[-1]
    assert name == "bound"
    assert (root / "bound").is_dir()


@pytest.mark.parametrize("body,expected", [
    ({"workspace": "p"}, "task"),                      # nothing to ask for
    ({"task": "   ", "workspace": "p"}, "task"),
    ({"task": "go"}, "workspace"),                     # nothing to bind to
    ({"task": "go", "workspace": "../etc"}, "workspace name"),
    ({"task": "go", "workspace": "p", "metadata": []}, "metadata"),
])
def test_a_bad_request_is_400_and_says_why(server, body, expected):
    status, out = call(server, "POST", "/v1/agents/code/run", body)
    assert status == 400
    assert expected in out["error"]["message"]


def test_an_unknown_agent_is_404(server):
    status, out = call(server, "POST", "/v1/agents/nope/run",
                       {"task": "go", "workspace": "p"})
    assert status == 404 and "nope" in out["error"]["message"]


def test_a_full_queue_is_503_not_a_dropped_task(server):
    server.runner.busy = True
    status, _ = call(server, "POST", "/v1/agents/code/run",
                     {"task": "go", "workspace": "p"})
    assert status == 503


def test_an_unknown_task_is_404(server):
    status, _ = call(server, "GET", "/v1/tasks/does-not-exist")
    assert status == 404


def test_a_queued_task_can_be_cancelled(server):
    _, task = call(server, "POST", "/v1/agents/code/run",
                   {"task": "go", "workspace": "p"})
    status, out = call(server, "POST", f"/v1/tasks/{task['id']}:cancel")
    assert status == 200 and out["state"] == CANCELED


def test_malformed_json_is_400_not_500(server):
    url = f"http://127.0.0.1:{server.server_address[1]}/v1/agents/code/run"
    request = urllib.request.Request(url, data=b"{not json", method="POST")
    request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 400


def test_workspaces_are_listable_over_http(server, root):
    (root / "alpha").mkdir()
    status, body = call(server, "GET", "/v1/workspaces")
    assert status == 200
    assert [w["name"] for w in body["workspaces"]] == ["alpha"]


def test_an_unknown_route_is_404(server):
    assert call(server, "GET", "/v1/nope")[0] == 404
    assert call(server, "POST", "/v1/nope", {})[0] == 404


def test_a_refused_post_still_drains_its_body(server):
    """A POST refused at the door must not poison the connection it arrived on.

    On a keep-alive connection an unread body stays in the socket and the next
    request line is parsed out of those leftover bytes. Caught as an
    intermittent ConnectionAbortedError on an unrelated test, which is exactly
    how this would present in production: not on the refused call, but on the
    *next* one down the same connection.
    """
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1],
                                      timeout=10)
    big = json.dumps({"task": "x" * 5000, "workspace": "p"})

    # Refused for the token, with a body nobody wanted to read...
    conn.request("POST", "/v1/agents/code/run", body=big,
                 headers={"Content-Type": "application/json"})
    first = conn.getresponse()
    # Each response is read to completion, or the *client* is the one holding
    # leftover bytes and this proves nothing about the server.
    first.read()
    assert first.status == 401

    # ...and refused for the route, likewise.
    conn.request("POST", "/v1/nope", body=big,
                 headers={"Content-Type": "application/json",
                          "Authorization": f"Bearer {TOKEN}"})
    second = conn.getresponse()
    second.read()
    assert second.status == 404

    # The connection is still good, which is the whole assertion.
    conn.request("GET", "/health")
    response = conn.getresponse()
    assert response.status == 200
    assert json.loads(response.read())["status"] == "ok"
    conn.close()


def test_an_oversized_body_is_413(server):
    from agent.serve import app as app_mod

    status, _ = call(server, "POST", "/v1/agents/code/run",
                     {"task": "x" * (app_mod.MAX_BODY + 10), "workspace": "p"})
    assert status == 413


# --- the engine: the queue and the one worker -------------------------------
#
# The section above drives `app.py` over a stub, so the runner's own queue,
# worker and task lifecycle are untested by it -- and that is the half where a
# lost task or a dead worker would be invisible. These build a real `Runner`
# with only the boot-time pool check and the agent command replaced: the first
# needs keys, and the second would be a real agent session.


class FakeProvider:
    def __init__(self, width):
        self.max_input_tokens = width
        self.name = "fake"


@pytest.fixture
def runner(root, monkeypatch, tmp_path):
    """A real Runner -- real queue, real worker, real lifecycle -- no pool."""
    import llm_router
    from agent import delegation
    from agent.serve.runner import Runner

    monkeypatch.setattr(llm_router, "load_providers_from_config",
                        lambda *a, **k: [FakeProvider(250_000),
                                         FakeProvider(8_000)])
    monkeypatch.setattr(llm_router, "AutonomousLLMRouter",
                        lambda providers: type("R", (), {"providers": providers})())
    monkeypatch.setattr(delegation, "available", lambda peers=None: ["code"])

    yield Runner(floor=128_000, record_dir=tmp_path / "records")


def _command(monkeypatch, fn):
    """Replace the agent command the worker runs with `fn(name, workdir, task)`."""
    from agent import delegation
    monkeypatch.setattr(delegation, "run",
                        lambda name, workdir, task, **kw: fn(name, workdir, task))


def test_the_floor_is_checked_at_boot(runner):
    """Boot-time failure beats a server that answers /health and rejects every
    task it is given."""
    assert runner.providers == 2
    assert runner.members == 1          # only the 250,000-token member clears
    assert runner.agents == ["code"]


def test_a_submitted_task_runs_the_command_and_keeps_what_it_printed(
        runner, root, monkeypatch):
    seen = {}

    def command(name, workdir, task):
        seen.update(name=name, workdir=Path(workdir), task=task)
        return 0, "Fixed the parser.\n\n=== DONE after 12 message(s) ==="

    _command(monkeypatch, command)

    task = runner.submit("code", "do the thing", workspace_name="proj")
    # Accepted, not done. Deliberately *not* asserted as `queued`: the returned
    # object is the live one the worker mutates, and with an empty queue the
    # worker can reach `running` before this line runs. What submit guarantees
    # is that it did not wait for the answer.
    assert not task.done

    done = _await(runner, task.id)
    assert done.state == DONE and done.exit_code == 0
    assert done.output.startswith("Fixed the parser."), "the final message leads"
    assert seen["name"] == "code" and seen["task"] == "do the thing"
    # Bound to the workspace, which was created for it.
    assert seen["workdir"] == (root / "proj").resolve()


def test_a_command_that_fails_fails_its_task_and_not_the_worker(runner,
                                                               monkeypatch):
    """A server that stops running tasks because one failed is the failure this
    whole project exists to avoid."""
    calls = []

    def command(name, workdir, task):
        calls.append(task)
        if len(calls) == 1:
            raise RuntimeError("could not launch")
        if len(calls) == 2:
            return 3, "No providers loaded."
        return 0, "done"

    _command(monkeypatch, command)

    first = runner.submit("code", "a", workspace_name="p")
    assert _await(runner, first.id).state == FAILED

    second = _await(runner, runner.submit("code", "b", workspace_name="p").id)
    assert second.state == FAILED and second.exit_code == 3
    assert "No providers loaded." in second.output

    third = runner.submit("code", "c", workspace_name="p")
    assert _await(runner, third.id).state == DONE


def test_a_timeout_is_reported_as_a_stop_and_not_a_crash(runner, monkeypatch):
    """The session did real work and was stopped; what it committed is still
    committed, and the task has to say so."""
    _command(monkeypatch, lambda name, workdir, task: (None, "got this far"))

    done = _await(runner, runner.submit("code", "x", workspace_name="p").id)
    assert done.state == FAILED
    assert "timeout" in done.error and "committed" in done.error
    assert done.output == "got this far"


def test_tasks_are_run_one_at_a_time(runner, monkeypatch):
    """Concurrency is one, and it is not a default to tune: per-process
    cooldown and a JSONL ledger both depend on it (docs/operations/serving.md#why-there-is-exactly-one-worker)."""
    overlap = []
    inside = threading.Semaphore(1)

    def command(name, workdir, task):
        got = inside.acquire(blocking=False)
        overlap.append(got)
        threading.Event().wait(0.05)
        if got:
            inside.release()
        return 0, "done"

    _command(monkeypatch, command)

    ids = [runner.submit("code", f"t{i}", workspace_name="p").id
           for i in range(4)]
    for task_id in ids:
        assert _await(runner, task_id).state == DONE
    assert all(overlap), "two commands were running at once"


def test_a_task_cancelled_before_it_starts_is_never_run(runner, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    ran = []

    def command(name, workdir, task):
        ran.append(task)
        started.set()
        release.wait(10)
        return 0, "done"

    _command(monkeypatch, command)

    blocker = runner.submit("code", "first", workspace_name="p")
    assert started.wait(10)                     # the worker is busy
    queued = runner.submit("code", "second", workspace_name="p")

    cancelled = runner.cancel(queued.id)
    assert cancelled.state == CANCELED

    # And cancelling the *running* one is refused rather than pretended.
    assert runner.cancel(blocker.id).state == RUNNING

    release.set()
    _await(runner, blocker.id)
    assert "second" not in ran


def test_a_full_queue_refuses_rather_than_dropping_a_task(runner, monkeypatch):
    from agent.serve import runner as runner_mod

    release = threading.Event()
    started = threading.Event()

    def command(name, workdir, task):
        started.set()
        release.wait(10)
        return 0, "done"

    _command(monkeypatch, command)
    monkeypatch.setattr(runner._queue, "maxsize", 2)

    runner.submit("code", "running", workspace_name="p")
    assert started.wait(10)
    runner.submit("code", "q1", workspace_name="p")
    runner.submit("code", "q2", workspace_name="p")

    with pytest.raises(runner_mod.Busy):
        runner.submit("code", "q3", workspace_name="p")

    release.set()


def test_an_unknown_agent_is_refused_at_submission(runner):
    with pytest.raises(KeyError):
        runner.submit("nope", "x", workspace_name="p")


def test_a_bad_workspace_is_refused_on_the_callers_thread(runner):
    """Discovering it an hour later in a task record nobody is watching would
    be the same mistake as advertising an agent without probing it."""
    with pytest.raises(workspace.BadWorkspace):
        runner.submit("code", "x", workspace_name="../etc")


def test_a_terminal_task_is_written_to_the_record_directory(runner, monkeypatch,
                                                            tmp_path):
    _command(monkeypatch, lambda name, workdir, task: (0, "done"))
    task = runner.submit("code", "x", workspace_name="p")
    _await(runner, task.id)
    record = json.loads((tmp_path / "records" / f"{task.id}.json")
                        .read_text(encoding="utf-8"))
    assert record["state"] == DONE and record["output"] == "done"


def _await(runner, task_id, timeout=15):
    """Poll until terminal, which is how a real caller reads this API too."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        task = runner.get(task_id)
        if task is not None and task.done:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} never reached a terminal state")
