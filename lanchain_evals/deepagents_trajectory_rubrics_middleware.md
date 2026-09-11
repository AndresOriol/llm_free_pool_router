# Deep Agent Evaluation Mechanics: Trajectory Analysis, Rubric Middleware, Probe Datasets & Interception Testing

## Executive Summary
Evaluating deep multi-step reasoning agents—such as autonomous coding assistants, complex scientific search agents, and multi-agent enterprise workflows built on **LangChain**, **LangGraph**, and **deepagents**—requires moving beyond single-turn output scoring. While standard LLM benchmarks score final text quality, multi-step agent reliability depends on **path correctness**, **tool selection precision**, **subagent routing accuracy**, **runtime guardrail compliance**, and **fault recovery resilience**.

This research document investigates the architectural mechanics of evaluating and testing deep agents across four key pillars:
1. **Trajectory Analysis for Deep Multi-Step Agents**: Capture, parsing, step alignment, planning vs execution divergence, backtrack tracking, and loop detection.
2. **Rubric Middleware & Runtime/Offline Constraints**: LLM-as-a-judge middleware (`RubricMiddleware`), step-by-step intermediate compliance, and custom LangSmith evaluators.
3. **Middleware Testing & Interception**: Fault injection, tool call interception, backend mocking, state transition assertions, and multi-agent coordination validation in LangGraph.
4. **Diagnostic Probe Datasets**: Constructing targeted synthetic stress tests to isolate distinct failure modes (context stuffing, lost-in-the-middle, hallucinatory tool calls, cyclic delegation).

---

## 1. Trajectory Analysis for Deep Multi-Step Agents

### 1.1 Capturing and Parsing Agent Execution Paths
In deep agent architectures, a **trajectory** $\mathcal{T}$ is an ordered sequence of execution steps:
$$\mathcal{T} = (s_0, a_0, o_0, s_1, a_1, o_1, \dots, s_T, a_T, o_T)$$
where $s_t$ represents the agent's internal state (including conversation memory, scratchpad, and planning context), $a_t$ represents an action (a thought/reasoning step, a tool call with arguments, or a subagent delegation), and $o_t$ represents the environmental observation returned by the tool or subagent.

In **LangChain** and **LangGraph**, trajectories are logged automatically as DAG-structured run trees via **LangSmith Tracing** or OpenTelemetry spans in platforms like **Arize Phoenix**.

```
Root Agent Run (Graph Execution)
├── Step 1: LLM Reasoning Node (Thought + Plan)
├── Step 2: Tool Call Node (`search_codebase`, args={"query": "auth middleware"})
│   └── Tool Execution Span (Observation: 3 files found)
├── Step 3: Subagent Delegate Node (`refactor_agent`)
│   ├── Subagent LLM Call (Thought)
│   └── Subagent Tool Call (`execute_sandbox_code`)
└── Step 4: Final Response Synthesis
```

Parsing a trajectory requires converting raw execution trees into structured telemetry vectors containing:
- **Node Type Spans**: Categorized as `thought`, `tool_call`, `subagent_delegate`, `human_approval_gate`, or `state_update`.
- **Tool Invocation Hashes**: Unique keys generated from `(tool_name, normalized_args)`.
- **State Transition Records**: Snapshots of key state values (e.g., `messages`, `working_memory`, `active_subagent`) before and after each node execution.

---

### 1.2 Multi-Step Reasoning & Path Metrics

Evaluating multi-step trajectories involves quantifying both structural and semantic efficiency. Key metrics include:

#### A. Tool Selection Correctness (Precision, Recall & F1)
Given a set of expected tool calls $A_{exp}$ in a golden trajectory and actual tool calls $A_{act}$:
$$\text{Precision} = \frac{|A_{act} \cap A_{exp}|}{|A_{act}|}, \quad \text{Recall} = \frac{|A_{act} \cap A_{exp}|}{|A_{exp}|}, \quad F_1 = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$$

#### B. Path Efficiency (Shortest Path Ratio)
Measures the ratio of optimal step length $T_{opt}$ to actual steps taken $T_{act}$:
$$\text{Efficiency Score} = \frac{T_{opt}}{\max(T_{act}, T_{opt})}$$
An efficiency score $< 1.0$ indicates reasoning detours, unnecessary tool invocations, or backtracks.

#### C. Planning vs. Execution Divergence
Calculates semantic drift between the agent's initial generated plan $P_0 = (p_1, p_2, \dots, p_k)$ and the executed action sequence $A = (a_1, a_2, \dots, a_m)$. High divergence indicates either model hallucination or adaptive replanning forced by unexpected tool observations.

#### D. Loop & Cycle Detection
Pathological loops occur when an agent repeatedly executes the same tool call or subagent delegation without state progress.
- **Exact Loop Score**: Identifies repeating sequence sub-vectors $a_i = a_{i+k}$.
- **Semantic Cycle Index**: Measures cosine distance between successive thoughts $T_i$ and $T_{i+k}$ or tool arguments:
$$\text{Cycle}(i, i+k) = \mathbb{I}\left( \text{CosineSim}(\text{Emb}(a_i), \text{Emb}(a_{i+k})) > 1 - \epsilon \right)$$

```python
# Trajectory Loop & Repetition Detector
import numpy as np
from typing import List, Dict, Any

def detect_trajectory_loops(trajectory: List[Dict[str, Any]], similarity_threshold: float = 0.95) -> Dict[str, Any]:
    tool_sequence = [
        f"{step['tool_name']}:{step.get('args_hash', '')}" 
        for step in trajectory if step['type'] == 'tool_call'
    ]
    
    exact_loops = 0
    seen_calls = {}
    for idx, call in enumerate(tool_sequence):
        if call in seen_calls:
            prev_idx = seen_calls[call]
            if idx - prev_idx <= 3:  # Tight loop within 3 steps
                exact_loops += 1
        seen_calls[call] = idx

    return {
        "total_tool_calls": len(tool_sequence),
        "exact_loop_count": exact_loops,
        "is_pathological": exact_loops >= 2
    }
```

---

### 1.3 Comparing Trajectories Against Reference "Golden" Trajectories

Comparing an agent's execution against a benchmark **Golden Trajectory** requires structural alignment:

1. **Normalized Sequence Edit Distance (Levenshtein on Action Names)**:
   Computes the insertion, deletion, and substitution cost between action string sequences $S_{act}$ and $S_{gold}$.
2. **Dynamic Time Warping (DTW) for State Trajectories**:
   Aligns non-linearly timed action sequences by mapping corresponding reasoning states in vector embedding space.
3. **DAG Step Alignment**:
   In multi-agent systems where parallel subagent branches execute asynchronously, trajectories form directed acyclic graphs. Evaluators perform topological alignment to ensure critical dependency nodes are satisfied before downstream synthesis.

```python
# Levenshtein Distance for Tool Execution Trajectories
def compute_tool_sequence_alignment(actual_tools: List[str], golden_tools: List[str]) -> float:
    m, n = len(actual_tools), len(golden_tools)
    dp = np.zeros((m + 1, n + 1))
    
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
        
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if actual_tools[i-1] == golden_tools[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])
                
    max_len = max(m, n)
    if max_len == 0:
        return 1.0
    edit_dist = dp[m][n]
    return 1.0 - (edit_dist / max_len)
```

---

## 2. Rubric Middleware & Rule-Based / LLM-Guided Constraints

### 2.1 What is Rubric Middleware?
**Rubric Middleware** is a runtime enforcement and evaluation layer in deep agent architectures (such as `deepagents.middleware.rubric.RubricMiddleware`). Unlike offline batch evaluation, rubric middleware acts as an **intermediate judge loop** during agent execution.

When an agent produces an intermediate output or signals completion, the `RubricMiddleware` intercepts the execution transcript and passes it to a dedicated **LLM-as-a-judge grader sub-agent**. The grader evaluates the transcript against a structured rubric containing explicit criteria (e.g., formatting constraints, required safety checks, code execution verification).

```
   +-----------------------------------------------------------+
   |                       Deep Agent                          |
   +-----------------------------------------------------------+
                                 |
                         [Produces Output]
                                 |
                                 v
   +-----------------------------------------------------------+
   |                    Rubric Middleware                      |
   |  - Invokes Grader Sub-Agent (e.g., Claude Haiku / GPT-4o)|
   |  - Evaluates against defined multi-criterion rubric       |
   +-----------------------------------------------------------+
                                 |
            +--------------------+--------------------+
            |                                         |
    [Needs Revision]                             [Satisfied]
            |                                         |
            v                                         v
   Inject Feedback into                      Emit Final Output
   Conversation State &                        & Stream Verdict
   Loop Back to Agent                            Events
```

---

### 2.2 Rubric Verdict State Machine
The `RubricMiddleware` grader sub-agent operates as a state machine returning five distinct verdicts:

| Verdict Status | Description | Execution Flow |
| :--- | :--- | :--- |
| `satisfied` | All rubric criteria pass successfully. | Agent completes; output delivered to user. |
| `needs_revision` | One or more criteria fail; grader output contains per-criterion feedback. | Feedback injected into conversation; agent loops back for revision. |
| `max_iterations_reached` | Grader requests revisions, but `max_iterations` cap is reached. | Agent terminates with failure status to prevent infinite spending. |
| `failed` | Grader judges the rubric malformed or impossible to satisfy. | Agent terminates immediately with error context. |
| `grader_error` | The grader model API times out or fails schema validation. | Handled gracefully without corrupting main agent state. |

---

### 2.3 Implementing Rubric Middleware in LangChain / DeepAgents

`RubricMiddleware` requires `deepagents>=0.6.5`. It accepts a separate model (often a faster, cheaper LLM like `anthropic:claude-haiku-4-5` or `openai:gpt-4o-mini`), custom tools for verification (e.g., running tests in a sandbox), and callbacks (`on_evaluation`).

```python
# Practical Implementation: Rubric Middleware for Vetted Code Generation
from deepagents import create_deep_agent, RubricMiddleware
from deepagents.middleware.rubric import RubricEvaluation
from langchain.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

def evaluation_logger(ev: RubricEvaluation) -> None:
    print(f"[Rubric Iteration {ev['iteration']}] Verdict: {ev['result']}")
    print(f"Explanation: {ev['explanation']}")
    if ev.get('criteria'):
        for criterion in ev['criteria']:
            print(f"  - {criterion['name']}: {criterion['passed']} ({criterion['feedback']})")

# Create deep agent with RubricMiddleware
vetted_agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    middleware=[
        RubricMiddleware(
            model="anthropic:claude-haiku-4-5",
            max_iterations=3,
            on_evaluation=evaluation_logger,
        ),
    ],
    checkpointer=InMemorySaver(),
)

# Invoke agent with runtime rubric
config = {"configurable": {"thread_id": "vetted-code-session-1"}}
rubric_spec = """
- The code must be valid Python 3.11 with explicit type hints.
- Includes docstrings for all public functions.
- Contains unit tests written in pytest.
- Must not use deprecated or external third-party libraries outside stdlib.
"""

response = vetted_agent.invoke(
    {
        "messages": [HumanMessage("Write a thread-safe LRU Cache in Python.")],
        "rubric": rubric_spec,
    },
    config=config,
)
```

---

### 2.4 Offline Trajectory Evaluators in LangSmith (`@run_evaluator`)

In addition to runtime rubric middleware, **LangSmith** enables offline batch evaluation of agent trajectories against datasets using custom run evaluators.

```python
# Custom LangSmith Trajectory Evaluator
from langsmith import Client, evaluate
from langsmith.schemas import Run, Example
from typing import Dict, Any

def evaluate_agent_trajectory(run: Run, example: Example) -> Dict[str, Any]:
    """
    Parses the child runs of an agent trace to score tool call accuracy,
    subagent routing correctness, and loop occurrence.
    """
    child_runs = run.child_runs or []
    tool_calls = []
    
    for child in child_runs:
        if child.run_type == "tool":
            tool_calls.append(child.name)
            
    expected_tools = example.outputs.get("expected_tools", [])
    
    # Calculate precision & recall
    matched_tools = [t for t in tool_calls if t in expected_tools]
    precision = len(matched_tools) / len(tool_calls) if tool_calls else 0.0
    recall = len(matched_tools) / len(expected_tools) if expected_tools else 1.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return {
        "results": [
            {"key": "tool_precision", "score": precision},
            {"key": "tool_recall", "score": recall},
            {"key": "tool_f1", "score": f1},
            {"key": "total_steps", "score": len(child_runs)}
        ]
    }

# Run batch evaluation in LangSmith
# client = Client()
# evaluate(
#     target=agent_runner_function,
#     data="deep-agent-diagnostic-dataset",
#     evaluators=[evaluate_agent_trajectory],
#     experiment_prefix="claude-sonnet-v2-trajectory-test"
# )
```

---

## 3. Middleware Testing & Interception

### 3.1 Interceptor Layer Architecture
To guarantee that deep agents survive real-world environments, testing harnesses use **interceptor middleware** to introduce failure modes into tool execution and state transitions.

Interceptors hook into LangGraph nodes or tool invocation spans to:
1. Intercept outgoing tool calls before they hit external APIs.
2. Inject synthetic faults (HTTP 500s, network timeouts, malformed JSON, rate limits).
3. Mock backend responses to test deterministic branch execution.
4. Fuzz tool argument parameters to verify schema enforcement.

```
+-------------------+        +----------------------+        +-------------------+
|  LangGraph Agent  | -----> | Interceptor Middleware | -----> | Mocked / Real API |
|   Reasoning Node  |        | - Fault Injector     |        |     Backend       |
+-------------------+        | - Schema Fuzzer      |        +-------------------+
                             +----------------------+
                                        |
                             (Inject 500 / Timeout / Corrupt JSON)
                                        v
                             [Simulated Tool Failure]
                                        |
                                        v
                             Verify Agent Recovery / Retry
```

---

### 3.2 Implementing Fault Injection & Mock Interceptors in Pytest

```python
# Fault Injector Interceptor for Tool Call Resilience
import pytest
import json
from unittest.mock import MagicMock
from langchain_core.tools import tool
from langchain_core.messages import ToolMessage, AIMessage

class FaultInjectionMiddleware:
    def __init__(self, failure_rate: float = 0.5, error_type: str = "timeout"):
        self.failure_rate = failure_rate
        self.error_type = error_type
        self.call_count = 0

    def intercept_tool_call(self, tool_name: str, tool_args: dict):
        self.call_count += 1
        # Inject fault on odd calls
        if self.call_count % 2 == 1:
            if self.error_type == "timeout":
                raise TimeoutError(f"Simulated network timeout calling {tool_name}")
            elif self.error_type == "http_500":
                return json.dumps({"error": "Internal Server Error 500", "code": 500})
            elif self.error_type == "malformed_json":
                return "{ 'error': bad_json_syntax, "
        # Normal fallback mock execution
        return json.dumps({"status": "success", "data": "Mocked tool output"})

# Pytest Test Case validating Agent Resilience under Fault Injection
def test_agent_recovery_from_tool_timeout():
    middleware = FaultInjectionMiddleware(failure_rate=1.0, error_type="http_500")
    
    # Mock tool wrapped with fault injection
    @tool
    def search_database(query: str) -> str:
        """Searches enterprise DB."""
        return middleware.intercept_tool_call("search_database", {"query": query})

    # Assert that tool returns error string, and agent receives structured error
    result = search_database.invoke({"query": "user_id_1234"})
    parsed_result = json.loads(result)
    assert parsed_result["code"] == 500
    assert "Internal Server Error" in parsed_result["error"]
```

---

### 3.3 State Transition & Multi-Agent Routing Assertions

In **LangGraph multi-agent systems** (e.g., Supervisor-Worker architectures), state transitions between nodes must be verified using checkpointing (`InMemorySaver` / `PostgresSaver`).

```python
# Supervisor-Worker State Transition Validation
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict
from typing import Annotated, Sequence
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

class MultiAgentState(TypedDict):
    messages: Sequence[BaseMessage]
    next_step: str
    active_worker: str
    retry_count: int

def supervisor_node(state: MultiAgentState) -> dict:
    last_msg = state["messages"][-1].content
    if "code" in last_msg.lower():
        return {"next_step": "coder_worker", "active_worker": "coder_worker"}
    elif "review" in last_msg.lower():
        return {"next_step": "reviewer_worker", "active_worker": "reviewer_worker"}
    return {"next_step": "FINISH", "active_worker": "none"}

def test_supervisor_routing_logic():
    # Test Coder Routing
    state_1: MultiAgentState = {
        "messages": [HumanMessage(content="Please write a python code snippet.")],
        "next_step": "",
        "active_worker": "",
        "retry_count": 0
    }
    res_1 = supervisor_node(state_1)
    assert res_1["next_step"] == "coder_worker"
    assert res_1["active_worker"] == "coder_worker"

    # Test Reviewer Routing
    state_2: MultiAgentState = {
        "messages": [HumanMessage(content="Please review this pull request.")],
        "next_step": "",
        "active_worker": "",
        "retry_count": 0
    }
    res_2 = supervisor_node(state_2)
    assert res_2["next_step"] == "reviewer_worker"
    assert res_2["active_worker"] == "reviewer_worker"
```

---

## 4. Probe Datasets for Deep Agent Diagnostics

### 4.1 Diagnostic Probe Dataset Architecture
A **Diagnostic Probe Dataset** is a specialized, targeted evaluation suite designed not to measure average accuracy, but to **isolate specific structural failure modes** in deep multi-step agents.

Unlike generic benchmarks (e.g., MMLU, GSM8K), probe datasets test edge-case behavior under synthetic stress conditions.

```
                          Diagnostic Probe Dataset
                                     |
    +-------------------+------------+------------+--------------------+
    |                   |                         |                    |
    v                   v                         v                    v
Probe 1:           Probe 2:                  Probe 3:             Probe 4:
Context Stuffing & Lost-in-the-Middle    Hallucinatory Tool Calls  Cyclic Delegation    Deep Branching
(Needle in 128k Token Scratchpad)        (Phantom Args & Tools)   (Supervisor Ping-Pong) Explosion
```

---

### 4.2 Specific Agent Failure Modes & Synthetic Test Generators

#### Failure Mode 1: Context Stuffing & Lost-in-the-Middle
- **Symptom**: Agent ignores critical constraints or instructions embedded in the middle of long multi-step scratchpads or retrieved tool outputs.
- **Probe Construction**: Inject a critical directive (e.g., "Use currency EUR, not USD") inside 50,000 tokens of irrelevant web search results placed between step 2 and step 10.

#### Failure Mode 2: Hallucinatory Tool Calls
- **Symptom**: Agent attempts to call non-existent tools, invokes real tools with fabricated parameter names, or passes invalid JSON schemas.
- **Probe Construction**: Provide tool definitions with subtle schema constraints (e.g., `date` field must strictly match `YYYY-MM-DD` string format) and evaluate parameter compliance.

#### Failure Mode 3: Cyclic Delegation & Ping-Pong Loops
- **Symptom**: Worker subagents continuously pass control back and forth (e.g., Coder -> Tester -> Coder -> Tester) without advancing state toward termination.
- **Probe Construction**: Give ambiguous tasks requiring conflicting requirements between two specialized worker subagents.

#### Failure Mode 4: Deep Branching Explosion
- **Symptom**: Agent spawns dozens of parallel subagents or recursive search steps, exhausting token budgets without synthesizing results.
- **Probe Construction**: Queries requiring deep recursive exploration (e.g., "Find all dependencies across 10 nested repositories").

```python
# Synthetic Generator for Context-Stuffing & Lost-in-the-Middle Diagnostic Probes
from langsmith import Client
from typing import List, Dict, Any

def generate_lost_in_middle_probes(num_samples: int = 10) -> List[Dict[str, Any]]:
    probes = []
    filler_text = "The quick brown fox jumps over the lazy dog. " * 500  # ~5,000 tokens per block
    
    for i in range(num_samples):
        needle = f"CRITICAL_DIRECTIVE_{i}: Override payment gateway to 'Stripe_EU_v2'."
        # Stuff needle in middle of large context
        context = f"{filler_text}\n\n[SYSTEM NOTE]: {needle}\n\n{filler_text}"
        
        probes.append({
            "inputs": {
                "messages": [
                    {"role": "user", "content": f"Process order #{1000+i}. Here is the audit log context:\n{context}"}
                ]
            },
            "outputs": {
                "expected_gateway": "Stripe_EU_v2",
                "forbidden_gateway": "Stripe_US"
            },
            "metadata": {
                "failure_mode": "lost_in_the_middle",
                "context_token_length": len(context) // 4
            }
        })
    return probes

# Upload probe dataset to LangSmith
# client = Client()
# dataset = client.create_dataset("Probe-Dataset-LostInMiddle-v1")
# for probe in generate_lost_in_middle_probes():
#     client.create_example(inputs=probe["inputs"], outputs=probe["outputs"], dataset_id=dataset.id)
```

---

## 5. End-to-End Implementation Example

The following code demonstrates a complete test harness integrating **LangGraph**, **Rubric Middleware**, **Fault Interception**, and **LangSmith Custom Trajectory Evaluation**.

```python
# End-to-End Suite: Deep Agent Middleware, Interception & Evaluation
import pytest
import json
from typing import TypedDict, Sequence, Dict, Any
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from deepagents import create_deep_agent, RubricMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langsmith.schemas import Run, Example

# 1. Interceptor for Fault Testing
class MockToolInterceptor:
    def __init__(self, simulate_failure: bool = False):
        self.simulate_failure = simulate_failure

    def __call__(self, query: str) -> str:
        if self.simulate_failure:
            return json.dumps({"status": "error", "message": "Database Connection Timeout 504"})
        return json.dumps({"status": "success", "data": f"Results for query: {query}"})

# 2. Custom Trajectory Evaluator for LangSmith
def evaluate_deep_agent_trajectory(run: Run, example: Example) -> Dict[str, Any]:
    child_runs = run.child_runs or []
    tool_spans = [r for r in child_runs if r.run_type == "tool"]
    
    # Check for duplicate consecutive tool calls
    duplicate_calls = 0
    prev_tool = None
    for span in tool_spans:
        current_tool = f"{span.name}:{span.inputs}"
        if current_tool == prev_tool:
            duplicate_calls += 1
        prev_tool = current_tool

    has_loop = duplicate_calls > 0
    return {
        "results": [
            {"key": "trajectory_loop_detected", "score": float(has_loop)},
            {"key": "total_tool_calls", "score": len(tool_spans)}
        ]
    }

# 3. Pytest Integration Test
def test_deep_agent_with_rubric_and_interceptor():
    interceptor = MockToolInterceptor(simulate_failure=False)
    
    # Create Agent with Rubric
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        middleware=[
            RubricMiddleware(
                model="anthropic:claude-haiku-4-5",
                max_iterations=2
            )
        ],
        checkpointer=InMemorySaver()
    )
    
    config = {"configurable": {"thread_id": "integration-test-run"}}
    
    # Execute agent
    result = agent.invoke(
        {
            "messages": [HumanMessage("Summarize user logs.")],
            "rubric": "- Summary must be under 50 words.\n- Must state status clearly."
        },
        config=config
    )
    
    assert result is not None
    assert "messages" in result
    assert len(result["messages"]) > 0
```

---

## 6. Sources & References

### Primary Documentation & Framework Specs
- **LangChain DeepAgents Documentation**: [https://docs.langchain.com/oss/python/deepagents/overview](https://docs.langchain.com/oss/python/deepagents/overview) *(Official docs covering Deep Agents architecture)*
- **LangChain Grading Rubrics (`RubricMiddleware`)**: [https://docs.langchain.com/oss/python/deepagents/rubric](https://docs.langchain.com/oss/python/deepagents/rubric) *(Official documentation on RubricMiddleware, LLM-as-a-judge grading loops, and verdicts)*
- **LangSmith Evaluation Platform**: [https://www.langchain.com/langsmith/evaluation](https://www.langchain.com/langsmith/evaluation) *(Official LangSmith evaluation suite, trace monitoring, and dataset management)*
- **LangSmith Tracing & Evaluation Concepts**: [https://docs.langchain.com/langsmith/evaluation-concepts](https://docs.langchain.com/langsmith/evaluation-concepts) *(Concepts on run trees, LLM-as-judge calibrators, and custom evaluators)*

### Industry Technical Analyses & Benchmarks
- **GenAI QA: Agent Trajectory Testing (2026 Comparison)**: [https://genai.qa/ai-agent-trajectory-testing-2026](https://genai.qa/ai-agent-trajectory-testing-2026) *(In-depth analysis of golden trajectories, tool precision/recall, path efficiency, and trajectory tools)*
- **CircleCI Engineering: Validating LangGraph Tool Use & API Responses**: [https://circleci.com/blog/building-llm-agents-to-validate-tool-use-and-structured-api](https://circleci.com/blog/building-llm-agents-to-validate-tool-use-and-structured-api) *(Comprehensive guide on testing LangGraph agents, Pydantic schemas, Pytest async harnesses, and CI/CD automation)*
- **Analytics Vidhya: Evaluating LLMs with LangSmith**: [https://www.analyticsvidhya.com/blog/2025/11/evaluating-llms-with-langsmith](https://www.analyticsvidhya.com/blog/2025/11/evaluating-llms-with-langsmith) *(Detailed practical guide on datasets, tracing, `@traceable`, and custom evaluator functions)*
