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
([17.3](../../docs/17-deployment.md#173-why-requestresponse-platforms-cannot-host-it)),
so `message:send` answering 202 immediately is the property that makes the
deployment possible at all -- and the easiest one to lose to a refactor that
"simplifies" the queue away.

No provider is ever called and no key is needed. `Server` takes its runner as an
argument, so the HTTP section drives the real request handler over a stub; the
engine section at the bottom builds a *real* `Runner` -- real queue, real
worker, real lifecycle -- with only `load_providers_from_config` replaced,
because the pool is the one part that needs keys.
"""

import json
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from agent.protocol.types import Message, Task, TaskState
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
    """A runner whose tasks complete the moment they are submitted.

    Enough of the real one's surface for `app.py` to drive: the point of these
    tests is the binding, and building a pool would make them require keys.
    """

    def __init__(self, root_dir):
        from agent.code.a2a import CARD as CODE_CARD

        self.cards = {"code": replace(CODE_CARD, url="/v1/agents/code",
                                      preferred_transport="HTTP+JSON")}
        self.router = type("R", (), {"providers": [1, 2, 3]})()
        self.members = 2
        self.running = None
        self.queued = 0
        self.busy = False
        self.tasks = {}
        self.submitted = []

    def submit(self, agent, message, *, workspace_name, repo=None,
               context_id=None, metadata=None):
        if agent not in self.cards:
            raise KeyError(f"no agent named {agent!r} is served here.")
        if self.busy:
            raise Busy("32 tasks are already queued")
        workdir = workspace.ensure(workspace_name, repo)
        task = Task(metadata={**(metadata or {}), "agent": agent,
                              "workspace": workspace_name,
                              "workdir": str(workdir)})
        if context_id:
            task.context_id = context_id
        message.task_id, message.context_id = task.id, task.context_id
        task.history.append(message)
        task.advance(TaskState.SUBMITTED)
        self.tasks[task.id] = task
        self.submitted.append((agent, message.text, workspace_name, repo))
        return task

    def get(self, task_id):
        return self.tasks.get(task_id)

    def cancel(self, task_id):
        task = self.tasks.get(task_id)
        if task and not task.done:
            task.advance(TaskState.CANCELED)
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
    ("GET", "/v1/agents/code/.well-known/agent-card.json"),
    ("GET", "/.well-known/agent-card.json"),
    ("GET", "/v1/tasks"),
    ("GET", "/v1/tasks/anything"),
    ("GET", "/v1/workspaces"),
    ("POST", "/v1/agents/code/message:send"),
    ("POST", "/v1/tasks/anything:cancel"),
])
def test_every_route_but_health_requires_the_token(server, method, path):
    status, _ = call(server, method, path,
                     body={} if method == "POST" else None, token=None)
    assert status == 401


def test_a_wrong_token_is_refused(server):
    status, _ = call(server, "GET", "/v1/agents", token="x" * 32)
    assert status == 401


def test_the_card_is_a2a_shaped_and_addressed(server):
    status, card = call(server, "GET",
                        "/v1/agents/code/.well-known/agent-card.json")
    assert status == 200
    # camelCase, verbatim from the spec. A field renamed to something more
    # Pythonic costs the entire argument for adopting A2A.
    assert card["protocolVersion"] and card["name"] == "code"
    assert card["preferredTransport"] == "HTTP+JSON"
    assert card["url"] == "/v1/agents/code"
    assert card["skills"][0]["id"] == "work_project"


def test_submission_answers_202_immediately_with_somewhere_to_poll(server):
    """The property the whole deployment rests on: accepted, not done."""
    status, task = call(server, "POST", "/v1/agents/code/message:send",
                        {"text": "Fix the parser", "workspace": "proj"})
    assert status == 202
    assert task["kind"] == "task"
    assert task["status"]["state"] == TaskState.SUBMITTED
    assert task["metadata"]["workspace"] == "proj"
    # And it is readable at the id it was given.
    status, fetched = call(server, "GET", f"/v1/tasks/{task['id']}")
    assert status == 200 and fetched["id"] == task["id"]


def test_a_full_a2a_message_is_accepted_as_well_as_the_text_shorthand(server):
    _, task = call(server, "POST", "/v1/agents/code/message:send", {
        "message": {"kind": "message", "role": "user",
                    "parts": [{"kind": "text", "text": "Read NOTES.md"}]},
        "workspace": "proj"})
    assert server.runner.submitted[-1][1] == "Read NOTES.md"
    assert task["history"][0]["parts"][0]["text"] == "Read NOTES.md"


def test_the_workspace_binds_the_agent_to_a_directory(server, root):
    call(server, "POST", "/v1/agents/code/message:send",
         {"text": "go", "workspace": "bound"})
    _, _, name, _ = server.runner.submitted[-1]
    assert name == "bound"
    assert (root / "bound").is_dir()


@pytest.mark.parametrize("body,expected", [
    ({"workspace": "p"}, "text"),                      # nothing to ask for
    ({"text": "   ", "workspace": "p"}, "empty"),
    ({"text": "go"}, "workspace"),                     # nothing to bind to
    ({"text": "go", "workspace": "../etc"}, "workspace name"),
    ({"text": "go", "workspace": "p", "metadata": []}, "metadata"),
])
def test_a_bad_request_is_400_and_says_why(server, body, expected):
    status, out = call(server, "POST", "/v1/agents/code/message:send", body)
    assert status == 400
    assert expected in out["error"]["message"]


def test_an_unknown_agent_is_404(server):
    status, out = call(server, "POST", "/v1/agents/nope/message:send",
                       {"text": "go", "workspace": "p"})
    assert status == 404 and "nope" in out["error"]["message"]


def test_a_full_queue_is_503_not_a_dropped_task(server):
    server.runner.busy = True
    status, _ = call(server, "POST", "/v1/agents/code/message:send",
                     {"text": "go", "workspace": "p"})
    assert status == 503


def test_an_unknown_task_is_404(server):
    status, _ = call(server, "GET", "/v1/tasks/does-not-exist")
    assert status == 404


def test_a_queued_task_can_be_cancelled(server):
    _, task = call(server, "POST", "/v1/agents/code/message:send",
                   {"text": "go", "workspace": "p"})
    status, out = call(server, "POST", f"/v1/tasks/{task['id']}:cancel")
    assert status == 200 and out["status"]["state"] == TaskState.CANCELED


def test_malformed_json_is_400_not_500(server):
    url = f"http://127.0.0.1:{server.server_address[1]}/v1/agents/code/message:send"
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
    big = json.dumps({"text": "x" * 5000, "workspace": "p"})

    # Refused for the token, with a body nobody wanted to read...
    conn.request("POST", "/v1/agents/code/message:send", body=big,
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

    status, _ = call(server, "POST", "/v1/agents/code/message:send",
                     {"text": "x" * (app_mod.MAX_BODY + 10), "workspace": "p"})
    assert status == 413


# --- the coding agent as an addressable agent -------------------------------


def test_the_code_handler_reports_the_commits_it_made(tmp_path, monkeypatch):
    """The deliverable of a coding task is a commit, so the handler has to name
    one. A caller that cannot name the commit cannot review it."""
    from agent.code import a2a

    workdir = tmp_path / "repo"
    workdir.mkdir()
    _git_init(workdir)
    (workdir / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=workdir, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=workdir, check=True,
                   capture_output=True)

    def fake_session(model, task_text, wd, **kw):
        # What the agent would have done: edit and commit, inside the jail.
        (Path(wd) / "a.py").write_text("x = 2\n")
        subprocess.run(["git", "commit", "-am", "change"], cwd=wd, check=True,
                       capture_output=True)
        return {"messages": [type("M", (), {"content": "Done."})()]}, None

    monkeypatch.setattr("agent.code.session.run_session", fake_session)

    handler = a2a.make_handler(None, workdir, floor=128_000, members=1,
                               recursion_limit=10)
    task = Task()
    task.history.append(Message.user("Change a.py"))
    done = handler(task)

    assert done.state == TaskState.COMPLETED
    data = done.artifacts[0].parts[0].data
    assert data["git"] is True
    assert data["commits"] == 1
    assert data["files_changed"] == ["a.py"]
    assert data["head"] != data["head_before"]


def test_a_workspace_that_is_not_a_repository_is_a_supported_case(tmp_path,
                                                                  monkeypatch):
    from agent.code import a2a

    workdir = tmp_path / "plain"
    workdir.mkdir()

    def fake_session(model, task_text, wd, **kw):
        (Path(wd) / "out.txt").write_text("written")
        return {"messages": []}, None

    monkeypatch.setattr("agent.code.session.run_session", fake_session)

    handler = a2a.make_handler(None, workdir, floor=128_000, members=1,
                               recursion_limit=10)
    task = Task()
    task.history.append(Message.user("Write a file"))
    done = handler(task)

    assert done.state == TaskState.COMPLETED
    assert done.artifacts[0].parts[0].data["git"] is False
    assert (workdir / "out.txt").exists()


def test_an_empty_request_is_rejected_rather_than_run(tmp_path):
    from agent.code import a2a

    handler = a2a.make_handler(None, tmp_path, floor=128_000, members=1,
                               recursion_limit=10)
    assert handler(Task()).state == TaskState.REJECTED


# --- the engine: the queue and the one worker -------------------------------
#
# The section above drives `app.py` over a stub, so the runner's own queue,
# worker and task lifecycle are untested by it -- and that is the half where a
# lost task or a dead worker would be invisible. These build a real `Runner`
# with the pool replaced, because the pool is the only part that needs keys.


class FakeProvider:
    def __init__(self, width):
        self.max_input_tokens = width
        self.name = "fake"


@pytest.fixture
def runner(root, monkeypatch, tmp_path):
    """A real Runner -- real queue, real worker, real lifecycle -- no pool."""
    import llm_router
    import agent.runtime.chat_model as chat_model
    from agent.serve.runner import Runner

    monkeypatch.setattr(llm_router, "load_providers_from_config",
                        lambda *a, **k: [FakeProvider(250_000),
                                         FakeProvider(8_000)])
    monkeypatch.setattr(llm_router, "AutonomousLLMRouter",
                        lambda providers: type("R", (), {"providers": providers})())
    monkeypatch.setattr(chat_model, "RouterChatModel",
                        lambda **kw: type("M", (), {"for_context":
                                                    lambda self, *a, **k: self})())

    yield Runner(floor=128_000, recursion_limit=10,
                 record_dir=tmp_path / "records")


def test_the_pool_is_built_once_and_the_floor_is_checked_at_boot(runner):
    """Boot-time failure beats a server that answers /health and rejects every
    task it is given."""
    assert len(runner.router.providers) == 2
    assert runner.members == 1          # only the 250,000-token member clears
    assert "code" in runner.cards


def test_a_submitted_task_runs_and_reaches_a_terminal_state(runner, root,
                                                            monkeypatch):
    seen = {}

    def handler_for(agent, workdir, task):
        def handle(t):
            seen["workdir"] = Path(workdir)
            seen["text"] = t.history[-1].text
            return t.advance(TaskState.COMPLETED, Message.agent("done"))
        return handle

    monkeypatch.setattr(runner, "_handler", handler_for)

    task = runner.submit("code", Message.user("do the thing"),
                         workspace_name="proj")
    # Accepted, not done. Deliberately *not* asserted as `submitted`: the
    # returned object is the live one the worker mutates, and with an empty
    # queue the worker can reach `working` before this line runs. What submit
    # guarantees is that it did not wait for the answer.
    assert not task.done

    done = _await(runner, task.id)
    assert done.state == TaskState.COMPLETED
    assert seen["text"] == "do the thing"
    # Bound to the workspace, which was created for it.
    assert seen["workdir"] == (root / "proj").resolve()


def test_a_handler_that_raises_fails_its_task_and_not_the_worker(runner,
                                                                 monkeypatch):
    """A server that stops running tasks because one raised is the failure this
    whole project exists to avoid."""
    calls = []

    def handler_for(agent, workdir, task):
        def handle(t):
            calls.append(t.id)
            if len(calls) == 1:
                raise RuntimeError("provider exploded")
            return t.advance(TaskState.COMPLETED, Message.agent("done"))
        return handle

    monkeypatch.setattr(runner, "_handler", handler_for)

    first = runner.submit("code", Message.user("a"), workspace_name="p")
    assert _await(runner, first.id).state == TaskState.FAILED

    second = runner.submit("code", Message.user("b"), workspace_name="p")
    assert _await(runner, second.id).state == TaskState.COMPLETED


def test_tasks_are_run_one_at_a_time(runner, monkeypatch):
    """Concurrency is one, and it is not a default to tune: per-process
    cooldown and a JSONL ledger both depend on it (docs/18-serving.md#185)."""
    overlap = []
    inside = threading.Semaphore(1)

    def handler_for(agent, workdir, task):
        def handle(t):
            got = inside.acquire(blocking=False)
            overlap.append(got)
            threading.Event().wait(0.05)
            if got:
                inside.release()
            return t.advance(TaskState.COMPLETED, Message.agent("done"))
        return handle

    monkeypatch.setattr(runner, "_handler", handler_for)

    ids = [runner.submit("code", Message.user(f"t{i}"),
                         workspace_name="p").id for i in range(4)]
    for task_id in ids:
        assert _await(runner, task_id).state == TaskState.COMPLETED
    assert all(overlap), "two tasks were inside the handler at once"


def test_a_task_cancelled_before_it_starts_is_never_run(runner, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    ran = []

    def handler_for(agent, workdir, task):
        def handle(t):
            ran.append(t.id)
            started.set()
            release.wait(10)
            return t.advance(TaskState.COMPLETED, Message.agent("done"))
        return handle

    monkeypatch.setattr(runner, "_handler", handler_for)

    blocker = runner.submit("code", Message.user("first"), workspace_name="p")
    assert started.wait(10)                     # the worker is busy
    queued = runner.submit("code", Message.user("second"), workspace_name="p")

    cancelled = runner.cancel(queued.id)
    assert cancelled.state == TaskState.CANCELED

    # And cancelling the *running* one is refused rather than pretended.
    assert runner.cancel(blocker.id).state == TaskState.WORKING

    release.set()
    _await(runner, blocker.id)
    assert queued.id not in ran


def test_a_full_queue_refuses_rather_than_dropping_a_task(runner, monkeypatch):
    from agent.serve import runner as runner_mod

    release = threading.Event()
    started = threading.Event()

    def handler_for(agent, workdir, task):
        def handle(t):
            started.set()
            release.wait(10)
            return t.advance(TaskState.COMPLETED, Message.agent("done"))
        return handle

    monkeypatch.setattr(runner, "_handler", handler_for)
    monkeypatch.setattr(runner._queue, "maxsize", 2)

    runner.submit("code", Message.user("running"), workspace_name="p")
    assert started.wait(10)
    runner.submit("code", Message.user("q1"), workspace_name="p")
    runner.submit("code", Message.user("q2"), workspace_name="p")

    with pytest.raises(runner_mod.Busy):
        runner.submit("code", Message.user("q3"), workspace_name="p")

    release.set()


def test_an_unknown_agent_is_refused_at_submission(runner):
    with pytest.raises(KeyError):
        runner.submit("nope", Message.user("x"), workspace_name="p")


def test_a_bad_workspace_is_refused_on_the_callers_thread(runner):
    """Discovering it an hour later in a task record nobody is watching would
    be the same mistake as advertising an agent without probing it."""
    with pytest.raises(workspace.BadWorkspace):
        runner.submit("code", Message.user("x"), workspace_name="../etc")


def test_a_terminal_task_is_written_to_the_record_directory(runner, monkeypatch,
                                                            tmp_path):
    monkeypatch.setattr(runner, "_handler", lambda a, w, t: (
        lambda task: task.advance(TaskState.COMPLETED, Message.agent("done"))))
    task = runner.submit("code", Message.user("x"), workspace_name="p")
    _await(runner, task.id)
    assert (tmp_path / "records" / f"{task.id}.json").exists()


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
