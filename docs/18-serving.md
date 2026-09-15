[← Wiki index](README.md)

# 18. Serving the agents

*How the agents become endpoints, what binds one to a repository, and why
submitting a task does not wait for it.*

## 18.1 What this adds, and what it does not

[17. Deployment](17-deployment.md) ends with a list of prerequisites and no
server. This is the server, plus the packaging that makes the prerequisites
someone's actual configuration rather than a note.

Nothing here changes an agent, and nothing here *is* an agent. The worker runs
`python -m agent.<name> <workdir> --task "..."` — the same command a person
types, in the same container — so a served run and a local run are the same run
([16. Delegation](16-delegation.md)). What is added is an address, a queue, and
a workspace to bind to:

| | CLI | Served |
| --- | --- | --- |
| Which agent | which module you ran | the path you POST to |
| What it works on | `sys.argv[1]`, a path you trust | a workspace *name*, resolved under one mounted root ([18.4](#184-binding-an-agent-to-a-repository-or-a-filesystem)) |
| The task | `--task`, or stdin to EOF | `task` in the request body, passed on as `--task` |
| Waiting | the process runs until it is done | 202 and a task id; you poll ([18.3](#183-why-submission-does-not-block)) |
| Concurrency | one process, one run | one worker, a queue ([18.5](#185-why-there-is-exactly-one-worker)) |

The CLI is not deprecated and remains the shortest way to run one task.

## 18.2 The surface

A request is a command line sent over a socket: which agent, which workspace,
and the task string. There used to be A2A cards and `Message` objects here; they
went with the rest of the protocol ([16.2](16-delegation.md#162-why-a-command-and-not-a-protocol)).

```
GET  /health                    liveness, no token
GET  /v1/agents                 which agents this server can run, as commands
POST /v1/agents/<name>/run      submit; 202 + a task
GET  /v1/tasks                  recent tasks
GET  /v1/tasks/<id>             one task
POST /v1/tasks/<id>:cancel      cancel if not started
GET  /v1/workspaces             what can be bound
```

Submitting work, end to end:

```bash
curl -sS -X POST localhost:8080/v1/agents/code/run \
  -H "Authorization: Bearer $SERVE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"task": "Read NOTES.md and do what the newest feedback asks for",
       "workspace": "closet_ai"}'
```

```json
{"id": "5f2c…", "agent": "code", "state": "queued",
 "workspace": "closet_ai", "workdir": "/workspaces/closet_ai",
 "request": "Read NOTES.md and do what the newest feedback asks for",
 "output": "", "exitCode": null, "created": "2026-09-02T…"}
```

Then poll `GET /v1/tasks/5f2c…` until `state` is `done`, `failed` or
`canceled`. `output` is everything the command printed, and it leads with the
agent's **final message**, unabridged. After it comes what the command reports
about itself: for a coding task, what git says moved — branch, the commits it
made and the files they touched, or **"Nothing changed"** in as many words —
because **the deliverable of a coding task is a commit, and a caller that cannot
name the commit cannot review it**. An exploration lists the notes *this* run
wrote; an improvement pass prints the ledger.

`improve` is served whenever it is asked for. What it needs is recorded runs in
the *bound workspace*, which arrives with the task — so a workspace with nothing
recorded in it comes back as a `failed` task whose output is the command's own
refusal, naming what would fix it.

## 18.3 Why submission does not block

A run lasts hours ([17.2](17-deployment.md#172-what-the-workload-actually-is))
and every platform that might sit in front of this kills a request in seconds
or minutes — 240 s of ingress on Container Apps, 900 s on Lambda
([17.3](17-deployment.md#173-why-requestresponse-platforms-cannot-host-it)). A
synchronous `POST` that returns when the agent is finished is therefore not a
simpler design that could be tightened later; it is a design that cannot be
deployed at all.

So `run` answers **202** with a `queued` task and a `Location` header, and the
caller polls. A local delegation blocks instead
([16.4](16-delegation.md#164-why-a-subprocess-costs-something-real)): an agent
running another is idle until it returns, and there is no gap to poll across.
Over HTTP there is nothing else the gap could be.

**Cancel is honest about what it cannot do.** A queued task cancels properly. A
*running* one does not: stopping it means killing an agent process with a
half-written commit and a half-appended ledger line, and a cancel that leaves a
workspace in a state nobody can describe is worse than one that refuses. The
task comes back still `running`, and the caller reads the state.

## 18.4 Binding an agent to a repository or a filesystem

One rule: **a workspace is a direct child of `WORKSPACES_DIR`, named, never a
path.**

On the CLI the workdir is `sys.argv[1]` and the operator owns it. Over HTTP it
is a string from a request, and the jail ([backend.py](../agent/utils/backend.py))
constrains the agent only *after* it has been pointed somewhere — it has
nothing to say about which root it was handed. So the binding is the boundary,
and it refuses the separator outright rather than normalizing a path and
hoping: a caller says `closet_ai` and gets `$WORKSPACES_DIR/closet_ai`, or an
error. A containment check afterwards catches the symlink a name rule cannot
see.

That gives the two ways to bind:

**A filesystem** — mount a directory of checkouts at the workspace root. Every
direct child becomes addressable, and `GET /v1/workspaces` lists them.

```yaml
volumes:
  - /srv/projects:/workspaces      # /srv/projects/closet_ai -> "closet_ai"
```

**A repository** — pass `repo` and the server clones it into a new workspace.

```bash
-d '{"text": "...", "workspace": "closet_ai",
     "repo": "https://github.com/me/closet_ai.git"}'
```

Cloning into a workspace that already holds something is **refused**. From the
server there is no way to tell a previous clone of the same repository from a
week of unpushed agent commits, and one of those two guesses destroys work. The
caller reuses a workspace by omitting `repo`, or names a new one. Only
`https://` and `ssh://` URLs are cloneable: this argument arrives over the
network, and `git clone` accepts arguments that read local files and run local
programs.

**Nothing is pushed.** The jail permits `add`, `commit` and `branch` and
refuses `merge`, `push`, `rebase`, `reset` and `clean`, unchanged from the CLI
— the human's gate is still the merge
([design/long-run-harness.md](design/long-run-harness.md)). A served run leaves
commits on a branch in a workspace, and someone reviews them.

## 18.5 Why there is exactly one worker

Not a default to tune. Two of [17.6](17-deployment.md#176-what-has-to-change-first)'s
prerequisites are unsolved and both bind here:

- **Cooldown lives on the provider object**, in this process. The pool is built
  once at start-up and every task routes through the same provider instances,
  so an account exhausted by one task is immediately known to the next. Two
  workers in two processes would each rediscover every rate limit by paying a
  wasted request for it.
- **The usage ledger is a JSONL file** appended from this process. Concurrent
  appends from replicas are not safe, least of all over shared storage.

And the ceiling was never compute anyway. Summed across the pool, a day is
about **42 minutes of flat-out routing**
([17.4](17-deployment.md#174-the-ceiling-is-the-pool-not-the-compute)). A
second worker would not buy throughput; it would buy 429s. Tasks queue, thirty‑two
deep, and a full queue is a `503` rather than a dropped task.

## 18.6 The token is not optional

This server runs agents that execute programs in a mounted filesystem and will
clone a repository into it. An open port serving that is not a deployment.

`SERVE_TOKEN` is required and **the server refuses to start without it**. That
refusal is the feature: a server that quietly comes up open because a variable
was unset is how this ends up on the public internet, and generating a token at
boot would be worse — it would work for whoever read the log line and be
silently regenerated on the next restart. `SERVE_ALLOW_ANONYMOUS=1` exists for
a server bound to loopback and says so in a warning.

Everything but `/health` requires the token. `/health` is exempt because a
container healthcheck should not need a credential to ask whether the process
is alive, and it answers nothing else — though it answers enough to tell a
server working a four-hour task from one whose worker died: `running`,
`queued`, and how many pool members cleared the context floor.

`HARNESS_SHELL=1` still drops the jail's allowlist, and over a network that is
remote code execution to anyone holding the token. The compose file publishes
to `127.0.0.1` for that reason; widening it is a decision, and TLS termination
in front is someone's job.

## 18.7 What has to be a volume

Three directories, and the first one is the prerequisite
[17.6](17-deployment.md#176-what-has-to-change-first) leads with:

| Path | Why it outlives the container |
| --- | --- |
| `LLM_ROUTER_USAGE_DIR=/var/lib/agent/usage` | **The usage ledger.** It defaults to a directory inside the package tree, so a fresh container starts believing the whole pool is unspent, hammers accounts that already spent their day, and rediscovers the wall by 429 — the exact behaviour [4.2.1](04-failover.md#421-skipping-a-member-whose-day-is-spent) exists to avoid |
| `WORKSPACES_DIR=/workspaces` | The work. Agent commits live here and a container is not where a branch should be stored |
| `SERVE_RECORD_DIR=/var/lib/agent/records` | One directory per task: the A2A record and the run tree beside it, which is what a human reads afterwards ([7.6](07-observability.md#76-the-record-one-run-tree)) |

The ledger needed no code change — `LLM_ROUTER_USAGE_DIR` already existed
([usage.py](../llm_router/usage.py)). It needed to be *pointed somewhere that
survives*, which the image does.

The remaining [17.6](17-deployment.md#176-what-has-to-change-first) items are
unchanged and still true: per-process cooldown is why [18.5](#185-why-there-is-exactly-one-worker)
exists, concurrent ledger appends are why it stays that way, and `keep_awake`
is a no-op off Windows — so on Linux the router's call timeout is the only
thing between a dropped socket and a stalled run.

## 18.8 Running it

```bash
cp .env.example .env      # keys, and SERVE_TOKEN=$(openssl rand -hex 32)
docker compose up --build
curl -sS localhost:8080/health
```

Sizing is [17.5](17-deployment.md#175-what-fits)'s: 1 vCPU / 2 GiB, 4 GiB if the
agent runs a heavy test suite in the jail, because a target project's suite
shares the container's limit and one measured at 440–517 MB on its own. The
compose file sets 2 GiB. 512 MB is not a candidate — the imports alone are
~186 MB before any conversation.

The image runs as a non-root user, installs `git` because the agent commits and
the server clones, and declares `git safe.directory` for the workspace root:
git refuses to operate in a directory owned by another user, which is exactly
what a bind-mounted host repository looks like from inside.

Where to put it is still [17.5](17-deployment.md#175-what-fits)'s answer and
this page does not change it. A container makes the harness *portable*; it does
not make an always-on host cheaper than a scheduled one, and it cannot
manufacture free-tier quota. [17.7](17-deployment.md#177-provider-terms) still
applies: moving seven accounts onto a datacenter IP behind one NAT is a visible
change in profile, and reading each provider's terms before deploying is this
project's own stated constraint.

## 18.9 Reaching it from outside the house

The intended deployment is a machine at home, reachable by its owner from
elsewhere. That is a normal thing to want and a bad thing to improvise, because
of what this particular service is.

**Start from what the token buys.** A caller holding `SERVE_TOKEN` can ask the
coding agent to write a Python file and run it. That is the agent's whole
purpose ([backend.py](../agent/utils/backend.py) allows `python`, `pytest`
and `git`), and it means the token is not "access to an app" — it is
**execution on that host**, inside a container. With `HARNESS_SHELL=1` it is
execution without even the allowlist. Every decision below follows from that
one sentence, and none of it is generic advice about running a web service.

### Do not forward a port

Forwarding `8080` on the router is the shape everyone reaches for first, and it
is wrong here on three counts at once:

- It publishes the **home IP**, which is also every other device's IP. That is
  the address on the packets, so it is in every log the other end keeps.
- It puts an execution endpoint on the open internet. Scanners find a new open
  port in minutes; they are not looking for this app specifically, and they do
  not have to.
- The only thing between a scanner and the agent is one bearer token in a
  header, sent **in clear** — there is no TLS in [app.py](../agent/serve/app.py)
  and there should not be, because terminating TLS is a job for something that
  does it for a living.

The router's port-forward page is where this gets lost. Nothing below needs it.

### Use an overlay network — the recommended answer

[Tailscale](https://tailscale.com) (WireGuard). The server and the devices that
call it join one private network; each dials **out** to a coordination service,
so there is no inbound port, nothing listening on the home IP, and nothing for a
scanner to find. Traffic between them is encrypted end to end, which is also
what makes sending a bearer token acceptable at all.

Install it on the server and on each device that will call the agents, then
publish the container's port on the overlay address instead of loopback:

```bash
tailscale ip -4                    # on the server: prints 100.x.y.z
echo "SERVE_PUBLISH_ADDR=100.x.y.z" >> .env
docker compose up -d
```

`SERVE_PUBLISH_ADDR` defaults to `127.0.0.1`, so the safe configuration is the
one you get by doing nothing ([docker-compose.yml](../docker-compose.yml)). From
a laptop anywhere:

```bash
curl -sS -H "Authorization: Bearer $SERVE_TOKEN" http://100.x.y.z:8080/health
```

Three properties a forwarded port does not have:

- **The home network gains no exposure.** No router rule changes, no port
  opens, and the WiFi is exactly as reachable as it was before.
- **Access is revoked per device**, from a console, without rotating a secret
  that every caller shares.
- **Reachability is not public.** Tighten it further with a tailnet ACL that
  lets only your own devices reach port 8080 on that machine — worth doing
  before the tailnet ever holds a device you did not set up yourself.

Optionally `tailscale serve` puts it behind a MagicDNS name with a real
certificate, so the URL is `https://…` inside the tailnet. That is polish rather
than protection here: WireGuard has already encrypted the hop.

**Do not use Tailscale Funnel.** It exists to publish a service to the open
internet, which is the one thing this whole section is avoiding.

### If something that cannot join the overlay must call it

A webhook, a CI job, a third-party service — anything that cannot run a VPN
client. Then a **tunnel that dials out**, most simply Cloudflare Tunnel
(`cloudflared`): the connector opens an outbound connection, so again no port is
forwarded and the home IP stays behind the provider. Put an identity layer
(Cloudflare Access, or equivalent) *in front* of the app so requests are
authenticated before they reach it, and keep `SERVE_TOKEN` set underneath —
the tunnel is not a substitute for it, and two independent gates is the point.

This is strictly weaker than the overlay: there is now a public hostname, and
its protection is a policy someone can misconfigure.

### Segment the machine on the LAN

Whatever reaches it, assume the container can be escaped and ask what is next
to it. The agent runs unattended code; the box it runs on should not sit on the
same flat network as a laptop with mounted drives.

- A separate VLAN, or the router's guest network, with no route back to the
  main LAN.
- Outbound is what this workload needs — provider APIs, Tavily, `git clone`.
  Inbound from the LAN it needs nothing.
- Do not run it on the machine holding backups or personal files.

### And on the host itself

- Keep `SERVE_TOKEN` long and per-caller if you can issue more than one; treat
  a leaked token as a compromised host, not a compromised password.
- Leave `HARNESS_SHELL` unset. Over a network it removes the only program
  allowlist there is.
- Keep the container's limits: non-root, `cap_drop: ALL`, `no-new-privileges`,
  `pids_limit`, and memory ([docker-compose.yml](../docker-compose.yml)).
  Never mount the Docker socket into it — a container that can reach the daemon
  can start a privileged one, and every other limit becomes decoration.
- Mount into `/workspaces` only the repositories the agent is meant to work.
  It cannot escape the workspace root ([18.4](#184-binding-an-agent-to-a-repository-or-a-filesystem)),
  so what is not mounted is not reachable.
- Watch the ledger. `python -m llm_router.quota status` against the mounted
  usage volume is also an intrusion signal: consumption nobody asked for is the
  cheapest evidence that someone else is using the token
  ([14. Quota panel](14-quota-panel.md)).

---

**Previous:** [← 17. Deployment](17-deployment.md) · **Next:** [Wiki index →](README.md)
