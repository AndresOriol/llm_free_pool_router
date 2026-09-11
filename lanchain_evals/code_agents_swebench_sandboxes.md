# Code Agent Benchmarking, Evaluation Pipelines, and Sandboxes in LangChain and LangGraph

## 1. Benchmarking Code Agents on SWE-bench and Coding Benchmarks

### Benchmark Suites Overview
Evaluation of AI coding agents built with **LangChain**, **LangGraph**, and modern agent scaffolds relies on standardized benchmark suites designed to test autonomous code navigation, patch generation, and test execution across real-world software repositories:

*   **SWE-bench Full** (2,294 instances): Real-world GitHub issues paired with pull requests across 12 popular Python repositories. Agents are supplied with problem descriptions, repository states at base commits, and must generate unified `.patch` files that pass hidden unit tests ([SWE-bench Paper](https://arxiv.org/abs/2310.06770)).
*   **SWE-bench Lite** (300 instances): A functional, cost-optimized subset of SWE-bench selected to reduce evaluation compute costs while maintaining representative difficulty across repository types ([SWE-bench Lite Docs](https://www.swebench.com/lite.html)).
*   **SWE-bench Verified** (500 instances): Human-validated subset developed with OpenAI. Evaluators reviewed issue descriptions and test setups to remove impossible or underspecified tasks, establishing it as the standard benchmark for coding agents ([SWE-bench Verified Report](https://www.swebench.com/verified.html)).
*   **SWE-Bench-CL** (273 tasks across 8 sequences): A continual learning adaptation of SWE-bench Verified that organizes GitHub issues chronologically within repositories (e.g. `django/django`, `sympy/sympy`). It evaluates an agent's ability to retain knowledge across sequential issues, mitigate catastrophic forgetting, and utilize long-term memory systems ([SWE-Bench-CL Paper](https://arxiv.org/html/2507.00014v1)).
*   **Claw-SWE-Bench** (350 instances) & **Claw-SWE-Bench Lite** (80 instances): A benchmark and adapter framework that standardizes general-purpose tool-using agents (such as OpenClaw and LangGraph custom agents) into scorable SWE-bench evaluation harnesses ([Claw-SWE-Bench Paper](https://arxiv.org/html/2606.12344v1)).
*   **HumanEval & RepoQA**: Synthetically constructed or function-level coding tasks (HumanEval) and long-context repository retrieval benchmarks (RepoQA) used to measure basic code synthesis and context retrieval capabilities prior to full repository-level agent deployment.

### SWE-agent & LangChain / LangGraph Integration
Integration between **SWE-agent** scaffolding concepts and **LangChain / LangGraph** occurs through agent-computer interfaces (ACIs) and stateful graph architectures:

1.  **ReAct & Tool Scaffolding**: LangGraph agents wrap terminal execution and file operations into custom tools or sandbox backends (`DaytonaSandbox`, `E2BSandbox`, `LangSmithSandbox`), replicating the ACI design of SWE-agent where file viewing, line editing, and test running occur via explicit tool calls.
2.  **Memory & Trajectory Tracking**: In continual evaluation setups like SWE-Bench-CL, LangGraph state graphs manage episodic memory (e.g. via FAISS or vector stores) alongside state channels (e.g. `messages`, `working_directory`, `todo_list`) to maintain task trajectories across multiple turns.
3.  **Mini-SWE-agent Scaffolding**: Minimalist ReAct loop execution in bash environments used for standardized model comparisons ([mini-SWE-agent Repository](https://github.com/SWE-agent/mini-swe-agent)).

```python
# Conceptual Architecture: LangGraph SWE-Agent Loop
from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], lambda x, y: x + y]
    working_dir: str
    issue_description: str

def call_model(state: AgentState):
    # LLM decides next tool invocation or final diff output
    pass

def should_continue(state: AgentState) -> str:
    last_message = state["messages"][-1]
    if "FINAL_PATCH" in last_message.content or not last_message.tool_calls:
        return END
    return "tools"

workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode(tools=[]))
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")
app = workflow.compile()
```

### Metrics Tracked in Evaluation
*   **Issue Resolution Rate (Pass@1, Pass@3)**: Percentage of benchmark issues resolved where generated git diffs pass all ground-truth tests. `Pass@1` reflects single-attempt accuracy; `Pass@3` reflects accuracy within a 3-attempt retry budget ([Verdent Report](https://www.verdent.ai/blog/swe-bench-verified-technical-report)).
*   **Patch Generation Validity & Syntax Correctness**: Ratio of trajectories that output syntactically valid `.patch` files that apply cleanly via `git apply` without merge conflicts or formatting syntax errors.
*   **Unit Test Pass Rate (`FAIL_TO_PASS` vs `PASS_TO_PASS`)**:
    *   `FAIL_TO_PASS`: Specific test cases that failed prior to the patch but MUST pass after applying the agent's patch.
    *   `PASS_TO_PASS`: Regression test suite that passed prior to the patch and MUST continue passing without side-effect failures.
*   **Cost per Resolved Issue & Token Consumption**: Total API dollars spent per resolved issue, combining prompt tokens, output tokens, and reasoning/thinking tokens (e.g., Claude Sonnet 4.5 or GPT-5 thinking steps).
*   **Turns / Trajectory Length**: The number of agent-environment interaction steps required to complete the task before hitting max step limits (e.g., 30 steps).

---

## 2. Sandbox & Execution Backends

### Sandbox Environments Comparison
Agents running untrusted code or executing full repository tests require isolated execution backends ([LangChain Sandboxes Documentation](https://docs.langchain.com/oss/python/deepagents/sandboxes)).

| Provider | Isolation Technology | Persistence & State | Cold-Start Latency | Key Use Case / Integration | Primary Docs |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **LangSmith Sandbox** | Managed Docker / MicroVM | Managed cloud session | ~1–2s | First-party LangChain/LangSmith evaluation & agent trace debugging | [LangChain Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes) |
| **E2B** | Isolated Linux MicroVMs | Pause, resume, snapshot, persistent volumes | ~1s resume | Python/JS Code Interpreter & DeepAgents backends | [E2B Docs](https://e2b.dev/docs) |
| **Daytona** | Dedicated Kernel / Containers | Stateful snapshots, volume mounts, unlimited retention | Sub-90ms creation | Full dev environments, Docker-in-Docker support, BYOC enterprise | [Daytona Docs](https://www.daytona.io/docs) |
| **Modal** | gVisor (user-space kernel) | Distributed volumes, filesystem/memory snapshots (7–30 day retention) | Sub-second scheduling | Large-scale parallel batch evaluation runs (up to 100k concurrent) | [Modal Sandbox Docs](https://modal.com/docs/guide/sandbox) |
| **Vercel Sandbox** | Firecracker MicroVMs | Auto-snapshot on stop, persistent volumes (30 day TTL) | Milliseconds | Web-focused agent runtimes & Vercel AI SDK execution | [Vercel Sandbox Docs](https://vercel.com/docs/sandbox) |
| **Cloudflare Sandbox** | Ubuntu Containers inside VMs | Ephemeral by default, S3/R2 volume mounts | Ephemeral connection | Edge-native agent execution with credential proxying | [Cloudflare Sandbox Docs](https://developers.cloudflare.com/sandbox/) |
| **Docker (Local/Self-Hosted)** | Linux Containers (cgroups/namespaces) | Volatile container layer, bind mounts | ~2–5s container start | Standard SWE-bench dockerized evaluation harness | [SWE-bench GitHub](https://github.com/SWE-bench/SWE-bench) |

### Tool Interfaces in LangChain / DeepAgents Backends
LangChain and DeepAgents standardize sandbox interactions by presenting them as unified execution backends. When a sandbox backend is configured, the agent receives two categories of tools:
1.  **Filesystem Tools**: `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`.
2.  **Execution Tool**: `execute` (runs bash/shell commands inside the sandbox).

```python
# Example: Integrating E2B, Daytona, and LangSmith Sandboxes in DeepAgents
from deepagents import create_deep_agent
from deepagents.backends import LangSmithSandbox
from langchain_daytona import DaytonaSandbox
from langchain_e2b import E2BSandbox
from daytona import Daytona
from e2b import Sandbox as E2BSandboxClient
from langsmith.sandbox import SandboxClient
from langchain_anthropic import ChatAnthropic

# 1. LangSmith Sandbox Setup
ls_client = SandboxClient()
ls_box = ls_client.create_sandbox()
ls_backend = LangSmithSandbox(sandbox=ls_box)

# 2. Daytona Sandbox Setup
daytona_box = Daytona().create()
daytona_backend = DaytonaSandbox(sandbox=daytona_box)

# 3. E2B Sandbox Setup
e2b_box = E2BSandboxClient.create()
e2b_backend = E2BSandbox(sandbox=e2b_box)

# Instantiate DeepAgent with selected sandbox backend
agent = create_deep_agent(
    model=ChatAnthropic(model="claude-3-7-sonnet-20250219"),
    system_prompt="You are an autonomous software engineering assistant operating in a secure sandbox.",
    backend=daytona_backend,
)

try:
    result = agent.invoke({
        "messages": [{"role": "user", "content": "Clone the repo, run pytest, and fix failing tests."}]
    })
finally:
    daytona_box.stop()
```

### Security Isolation, Teardown, and Persistence
*   **Security Boundaries**: MicroVMs (Firecracker, E2B) and gVisor (Modal) prevent container breakout by intercepting system calls or running full virtualized kernels per tenant. Host API credentials are proxy-injected at the network perimeter rather than exposed in sandbox environments ([Cloudflare Security Docs](https://developers.cloudflare.com/sandbox/concepts/security/)).
*   **Lifecycle & Scoping**:
    *   *Thread-scoped (Default)*: Fresh sandbox instance created per conversation or benchmark task evaluation run; torn down upon evaluation completion.
    *   *Assistant-scoped*: Persistent sandbox retained across multiple interactions or tasks, supporting stateful continual learning workflows.
*   **Teardown & Determinism**: Every evaluation harness enforces deterministic teardown in python `finally` blocks (e.g. `sandbox.stop()`, `sandbox.kill()`, `modal_sandbox.terminate()`) or ephemeral container auto-destruction to ensure test environments start from pristine base commits without leftover artifacts.

---

## 3. Test Harness & Evaluation Pipelines

### Harness Architecture at Scale
Evaluating coding agents across hundreds of SWE-bench instances requires an orchestrated pipeline:

```
+-----------------------------------------------------------------------------------+
|                            Evaluation Orchestrator                                |
|  1. Load SWE-bench Instance Metadata (Repo, Base Commit, Issue Text, Test Spec)   |
+----------------------------------------+------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                             Isolated Sandbox Pool                                 |
|  - Launch MicroVM / Docker Container                                              |
|  - Checkout repository to base commit                                             |
|  - Apply environment setup patch & dependencies                                   |
+----------------------------------------+------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                         LangGraph Agent Execution Loop                            |
|  - Inject Issue Description & Tool Definitions                                    |
|  - Iterative ReAct Loop: Execute Shell, Edit Files, Run Tests                     |
|  - Enforce Step Timeouts, Token Budgets, & Max Turn Limits                        |
|  - Generate output diff (`git diff > patch.patch`)                                |
+----------------------------------------+------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                             SWE-bench Test Evaluator                              |
|  1. Reset repository state in clean container                                     |
|  2. Apply generated patch (`git apply patch.patch`)                               |
|  3. Run `FAIL_TO_PASS` test cases (Verify issue fix)                              |
|  4. Run `PASS_TO_PASS` test cases (Verify no regressions)                         |
|  5. Output Final Metric (Resolved: PASS / FAIL)                                   |
+-----------------------------------------------------------------------------------+
```

### Handling Failures, Timeouts, and Runaway Tool Calls
Robust evaluation pipelines implement strict defensive constraints to handle agent execution edge cases:

1.  **Command-Level Timeouts**: Terminal executions via the `execute` tool are bounded by step-level timeouts (e.g. 30–60 seconds per bash command) to prevent infinite loops (e.g. waiting for interactive user prompt input or endless server processes).
2.  **Trajectory Step & Turn Limits**: Agents are restricted to a maximum number of environment turns (typically 30 steps). Reaching the turn limit forces immediate termination and diff extraction.
3.  **Runaway Tool Call & Recursion Limits**: LangGraph native recursion limits (`config={"recursion_limit": 50}`) stop infinite loops between model output and tool nodes.
4.  **Cost and Token Caps**: Hard API usage caps prevent unbounded billing when models enter reasoning loops.

### Comparative Performance Data on SWE-bench
Benchmark performance varies across models, agent scaffolds, and thinking/reasoning modes on **SWE-bench Verified** ([Verdent Report](https://www.verdent.ai/blog/swe-bench-verified-technical-report), [Claw-SWE-Bench Paper](https://arxiv.org/html/2606.12344v1)):

| System / Agent Scaffold | LLM Backbone | Pass@1 (Resolved) | Pass@3 (Resolved) | Special Features / Setup |
| :--- | :--- | :--- | :--- | :--- |
| **Verdent Agent** | Claude Sonnet 4.5 (w/ Thinking) | **82.0%** (Subset) | **88.0%** (Subset) | Multi-agent plan-code-verify loop + code review subagent |
| **Verdent Agent** | Claude Sonnet 4.5 | **76.1%** | **81.2%** | Production agent scaffold, no test-time scaling |
| **Claude Code** | Claude Sonnet 4.5 (w/ Thinking) | 78.0% (Subset) | 86.0% (Subset) | Anthropic native CLI agent scaffold |
| **OpenClaw (Full Adapter)** | GLM 5.1 | 73.4% | - | Claw-SWE-Bench full adapter architecture |
| **OpenClaw (Direct Diff)** | GLM 5.1 | 19.1% | - | Minimal adapter without structured tools / scaffolding |
| **mini-SWE-agent** | Frontier LMs | Baseline ReAct | - | Standard bash-only reference evaluation harness |

---

## Sources

### Primary Benchmark & Evaluation Papers
*   **SWE-bench Paper**: Jimenez et al., *SWE-bench: Can Language Models Resolve Real-world Github Issues?*, ICLR 2024. [https://arxiv.org/abs/2310.06770](https://arxiv.org/abs/2310.06770) (Primary Benchmark Source)
*   **SWE-Bench-CL Paper**: Joshi et al., *SWE-Bench-CL: Continual Learning for Coding Agents*, arXiv:2507.00014, June 2025. [https://arxiv.org/html/2507.00014v1](https://arxiv.org/html/2507.00014v1) (Primary Benchmark Source)
*   **Claw-SWE-Bench Paper**: Zheng et al., *Claw-SWE-Bench: A Benchmark for Evaluating OpenClaw-style Agent Harnesses on Coding Tasks*, arXiv:2606.12344, June 2026. [https://arxiv.org/html/2606.12344v1](https://arxiv.org/html/2606.12344v1) (Primary Benchmark Source)

### Vendor Technical Reports & Documentation
*   **SWE-bench Verified Announcement & Leaderboards**: OpenAI & Princeton NLP, *SWE-bench Verified*. [https://www.swebench.com/verified.html](https://www.swebench.com/verified.html) (Primary Vendor/Official Source)
*   **LangChain Sandboxes Documentation**: LangChain, *Execution environment: Sandboxes*. [https://docs.langchain.com/oss/python/deepagents/sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes) (Primary Documentation Source)
*   **Verdent Technical Report**: Verdent AI, *SWE-bench Verified Technical Report*, November 2025. [https://www.verdent.ai/blog/swe-bench-verified-technical-report](https://www.verdent.ai/blog/swe-bench-verified-technical-report) (Vendor Evaluation Report)
*   **Developer Digest AI Agent Code Sandbox Comparison**: *Where Should Your AI Agent Run Code: E2B vs Daytona vs Modal vs Cloudflare vs Vercel Sandbox*, July 2026. [https://www.developersdigest.tech/blog/ai-agent-code-sandbox-comparison-2026](https://www.developersdigest.tech/blog/ai-agent-code-sandbox-comparison-2026) (Secondary Reporting)
