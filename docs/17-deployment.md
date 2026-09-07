[← Wiki index](README.md)

# 17. Deployment

*Where this harness can actually run, why most hosting platforms cannot run it
at all, and what has to change before any of them can.*

## 17.1 The question

[Phase 2](13-roadmap.md#131-the-two-phases-of-the-project) wants a standing
maintainer that works a project unattended for hours
([long-run-harness](design/long-run-harness.md)). The obvious next thought is to
host it somewhere, so that a run does not depend on one laptop staying awake —
[awake.py](../agent/runtime/awake.py) exists only because it does.

Most of the obvious answers are wrong, and they are all wrong for the same
reason: they host a *web backend*, and this is not one. This page records the
elimination so it is not re-derived.

## 17.2 What the workload actually is

Four properties. Measured 2026-09-02 on this repo, not estimated.

| Property | Evidence |
| --- | --- |
| **A run lasts hours** | [awake.py](../agent/runtime/awake.py) exists because a session "runs for hours with long gaps between provider calls" |
| **Memory floor ~186 MB, before any conversation** | RSS after importing `llm_router` (153 MB) plus `RouterChatModel` and both sessions. Cold imports take 6.2 s. Add the message history: a session only routes to members holding ≥128,000 input tokens |
| **It spawns child processes** | [backend.py](../agent/runtime/backend.py) allows `python`/`pytest`/`git`. They share the container memory limit. A target project test suite can peak far above the agent itself — closet_ai's ONNX segmentation suite measured 440–517 MB |
| **Nothing calls it while it works** | The entry point is `python -m agent.code [workdir] < brief.md`. There is an HTTP server now ([18](18-serving.md)), and it does not change this: a caller submits a task and polls, so the hours in between still carry no inbound traffic |

CPU is the one thing it barely needs. The agent is I/O-bound on provider calls;
it is the `pytest` child, not the loop, that wants a core.

## 17.3 Why request/response platforms cannot host it

Each of the following was checked against vendor documentation, not assumed.

| Platform | Disqualifier |
| --- | --- |
| **Azure Container Apps** (app with ingress) | Ingress request timeout is **240 s**, flat. `minReplicas=0` scales down on *absent inbound traffic*, which is what a working agent looks like — it would be killed mid-run |
| **AWS Lambda** | Function timeout **900 s (15 min)**, listed among the quotas that cannot be changed |
| **Vercel / Netlify functions** | Timeouts in seconds; filesystem read-only except `/tmp`, so no git workspace and no ledger |
| **Render free** | Spins down after **15 minutes without inbound traffic**; 512 MB; **no persistent disks on the free tier**; no background workers or cron there either |
| **Koyeb free** | Same 512 MB ceiling, same traffic-driven model |

The pattern is one mistake repeated: every one of these treats *absence of
inbound requests* as absence of work. For this harness the two are unrelated.

Two corrections worth keeping, because both circulate as folklore:

- Lambda's free tier is **400,000 GB-seconds**, not "3.2 M seconds". The larger
  figure only holds at 128 MB — below this agent's import footprint. At the
  2 GB it actually needs, the free tier is ~55 hours a month.
- Container Apps bills *the resources allocated to a replica for as long as it
  is running*, not the CPU time of requests. Scale-to-zero does not make a long
  run cheap; it makes it fail.

## 17.4 The ceiling is the pool, not the compute

This is why "keep the agents always working" does not follow from "rent a
server that is always on". Summing `rpd` across
[config.yaml](../llm_router/config.yaml) (2026-09-07: 1 Groq account, 6 Gemini):

| Tier | Requests/day |
| --- | --- |
| Reasoning (`gemini-*-flash`, 20 rpd × 6) | **600** |
| Mid (`*-flash-lite`, 500 rpd × 6) | 6,000 |
| Workhorse (`gemma4-*`, 1,500 rpd × 6) | 18,000 |
| Groq (1,000 rpd × 1 account) | 2,000 |
| **Total** | **26,600** |

Aggregate RPM is 600, so the whole pool's day is **~44 minutes of flat-out
routing**. The scarce tier is scarcer still: 600 reasoning requests is a couple
of hours of one agent's architectural steps, after which quality degrades to
gemma for the rest of the day
([4.2.1](04-failover.md#421-skipping-a-member-whose-day-is-spent)).

An always-on host cannot manufacture free-tier quota. It buys idle time, and
[1.1](01-overview.md#11-the-goal) is explicitly about not paying a recurring
bill to keep an agent running.

## 17.5 What fits

Scheduled or triggered execution, on something with a real filesystem.

| Option | Fit | Cost |
| --- | --- | --- |
| **Cheap VPS** (Hetzner CX22, 2 vCPU / 4 GB) | Best fit. Root, `systemd` timer, disk, git, subprocesses — nothing to work around | **~€4.50/mo** |
| **GitHub Actions, `schedule:`** | Strong fit for the daily-maintainer cycle: the workspace *is* a git checkout, the 7 keys are repo secrets, the deliverable is a branch. Capped at **6 h per job**; 2,000 min/mo on the Free plan, and public repos do not consume it | **€0** |
| **Azure Container Apps *Jobs*** (not apps) | Works — `replicaTimeout`, cron or event triggers. But jobs **do not support ingress**, so exposing an agent as an endpoint needs a second, thin app in front | ~$7.6/mo at 4 h/day, 1 vCPU / 2 GiB |
| **Oracle Cloud Always Free** | 4 OCPU ARM / 24 GB, free indefinitely. The one-time VCN setup is the whole cost | **€0** |

For contrast, the shape most people reach for first — one Container App at
1 vCPU / 2 GiB running 24/7 — is **~$72/mo**: (2,592,000 − 180,000) vCPU-s ×
$0.000024 plus (5,184,000 − 360,000) GiB-s × $0.000003, after the monthly free
grant of 180,000 vCPU-s and 360,000 GiB-s per subscription. Ten times the
scheduled-job price for the same work.

Sizing, whichever is chosen: **1 vCPU / 2 GiB** for the agent, **4 GiB** if it
runs a heavy test suite in the jail. 512 MB is not a candidate.

## 17.6 What has to change first

Prerequisites, not polish. Every option above trips on them.

*Where these stand since [18. Serving](18-serving.md): the ledger and the
secrets are handled by the image and its volumes
([18.7](18-serving.md#187-what-has-to-be-a-volume)). Per-process cooldown and
concurrent JSONL appends are **not** solved — they are the reason the server
runs exactly one worker
([18.5](18-serving.md#185-why-there-is-exactly-one-worker)), which contains
them rather than fixing them.*

- **The usage ledger is machine-local and ephemeral.**
  `llm_router/.usage/ledger.jsonl` lives inside the repo tree
  ([14.3](14-quota-panel.md#143-two-files-under-llm_routerusage),
  [2.7](02-repo-map.md#27-state-that-lives-outside-git)). In a container every
  start begins with an empty ledger, so the RPD preflight believes the whole
  pool is fresh, hammers accounts that already spent their day, and
  rediscovers the wall by 429 — the exact behaviour
  [4.2.1](04-failover.md#421-skipping-a-member-whose-day-is-spent) exists to
  avoid. It has to outlive the container before anything else is worth doing.
- **Cooldown is per-process** and unshared
  ([2.7](02-repo-map.md#27-state-that-lives-outside-git),
  [13.4](13-roadmap.md#134-open-questions)). Already a known limitation on one
  machine; running two executions in parallel makes it load-bearing. Keep
  concurrency at 1 until it is solved.
- **Appending JSONL from concurrent replicas is not safe**, least of all over
  SMB. If the ledger moves to shared storage and concurrency ever exceeds 1,
  it needs a store rather than a file.
- **Secrets**: 7 provider keys plus LangSmith and Tavily, today in a gitignored
  `.env`. They become platform secrets; none of them belongs in an image.
- **`keep_awake` is a no-op off Windows** (it guards on `sys.platform`), so it
  ports safely — but the protection it provides is gone, and the call timeout
  in the router becomes the only thing between a dropped socket and a stalled
  run.

## 17.7 Provider terms

[1.4](01-overview.md#14-what-is-deliberately-not-built) commits this project to
not circumventing provider terms of service. Moving six Gemini accounts and one
Groq account from a residential connection to a datacenter IP, all egressing
through one NAT, is a visible change in profile. Read each provider's terms
before deploying. This is the project's own stated constraint, not an external
one.

---

**Previous:** [← 16. The agent protocol](16-agent-protocol.md) · **Next:** [18. Serving the agents →](18-serving.md)
