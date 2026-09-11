# LangSmith Evaluation Framework Architecture for Complex Deep & Code Agents

This document details the architectural design, evaluation mechanics, dataset construction strategies, and operational workflows for evaluating multi-step deep agents and code agents using **LangSmith** and the **LangChain** ecosystem.

---

## 1. LangSmith Evaluation Framework Architecture

Evaluating complex, multi-step agentic systems requires moving beyond single-turn input/output scoring. An agent generates a tree of execution steps—planning, calling tools, processing intermediate results, retrying on errors, and managing internal state. LangSmith provides an end-to-end framework to evaluate both end-to-end task completion and intermediate agent trajectories.

```
+-----------------------------------------------------------------------------------+
|                            LANGSMITH EVALUATION HARNESS                           |
|                                                                                   |
|   +-----------------------+                    +------------------------------+   |
|   |   Evaluation Dataset  |                    |      Target System (Agent)   |   |
|   |  (Inputs & Reference) |                    | (LangGraph / LangChain / SDK)|   |
|   +-----------+-----------+                    +--------------+---------------+   |
|               |                                               |                   |
|               +----------------------+------------------------+                   |
|                                      |                                            |
|                                      v                                            |
|                       +-------------------------------+                           |
|                       |   `evaluate()` Execution Engine|                           |
|                       +---------------+---------------+                           |
|                                       |                                           |
|       +-------------------------------+-------------------------------+           |
|       |                               |                               |           |
|       v                               v                               v           |
|  +----------+                +------------------+             +---------------+   |
|  | Code Evals|                | Trajectory Evals |             | LLM-as-a-Judge|   |
|  | (Schema/ |                | (`agentevals` /  |             | (Criteria /   |   |
|  |  Match)  |                |   Match Modes)   |             |   Rubrics)    |   |
|  +----+-----+                +--------+---------+             +-------+-------+   |
|       |                               |                               |           |
|       +-------------------------------+-------------------------------+           |
|                                       |                                           |
|                                       v                                           |
|                       +-------------------------------+                           |
|                       |  LangSmith Run Tree & Scores  |                           |
|                       |  (Online/Offline Experiment)  |                           |
|                       +-------------------------------+                           |
+-----------------------------------------------------------------------------------+
```

### 1.1 Core Evaluation Architecture & SDK Interfaces

In LangSmith SDK v0.2+, the evaluation API is unified into the `evaluate()` (sync) and `aevaluate()` (async) methods ([Easier evaluations with LangSmith SDK v0.2](https://www.langchain.com/blog/easier-evaluations-with-langsmith-sdk-v0-2)). The method unifies three primary modes:

1. **Agent Invocation & Scoring**: Executing a target agent function or `LangGraph` graph on an evaluation dataset and scoring the resulting run traces.
2. **Existing Experiment Scoring**: Running new evaluators over existing experiment results without re-invoking the agent.
3. **Pairwise / Comparative Scoring**: Comparing outputs across two or more experiment runs to judge preference or relative performance.

#### Key SDK Features:
- **Direct Agent Support**: LangGraph graphs (`create_react_agent`, `StateGraph`) or LangChain runnables can be passed directly as the target callable.
- **Local Execution**: Setting `upload_results=False` enables local evaluation execution (useful for quick iteration in CI/CD without writing traces to the remote platform).
- **Flexible Evaluator Signature**: Evaluators can accept simplified dict parameters (`inputs`, `outputs`, `reference_outputs`) or inspect the full `Run` and `Example` objects to access intermediate execution trees.

```python
from langsmith import evaluate
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

# 1. Define Agent & Tools
def get_weather(city: str) -> str:
    """Return weather forecast for a city."""
    return f"It's 72°F and sunny in {city}."

model = ChatOpenAI(model="gpt-4o-mini")
graph = create_react_agent(model, tools=[get_weather])

# 2. Define Custom Code Evaluator
def exact_tool_called(inputs: dict, outputs: dict, reference_outputs: dict) -> bool:
    """Verify whether the final answer or trajectory meets exact criteria."""
    messages = outputs.get("messages", [])
    has_tool_call = any(
        getattr(msg, "tool_calls", None) for msg in messages
    )
    return has_tool_call

# 3. Execute Evaluation Run
results = evaluate(
    graph,
    data="agent-weather-probe-v1",
    evaluators=[exact_tool_called],
    experiment_prefix="react-agent-test",
    max_concurrency=2,
    upload_results=True
)
```

---

### 1.2 Core Evaluator Types

LangSmith supports several evaluator paradigms targeting different nodes of an agent's execution graph.

#### A. Trajectory Evaluators (`agentevals`)
Agent trajectories are evaluated using deterministic structural matching or LLM judges via the official [`agentevals`](https://docs.langchain.com/oss/python/langchain/test/evals) library ([Agent Evals - Docs by LangChain](https://docs.langchain.com/oss/python/langchain/test/evals)). Trajectory matching verifies tool selection, step order, and argument precision:

| Trajectory Match Mode | Description | Practical Use Case |
| :--- | :--- | :--- |
| `strict` | Exact sequence match of message types and tool calls in order. | High-security or policy-bound workflows (e.g., authentication required before database write). |
| `unordered` | Required tool calls present, but order is flexible. | Independent retrieval operations (e.g., fetching weather and event data in parallel). |
| `subset` | Agent calls *only* tools present in reference (no unexpected extras). | Scope enforcement (preventing hallucinated tool invocations). |
| `superset` | Agent calls *at least* the tools in reference trajectory (extra tools permitted). | Baseline requirement verification (ensuring critical safety checks ran). |

```python
from agentevals.trajectory.match import create_trajectory_match_evaluator
from agentevals.trajectory.llm import create_trajectory_llm_as_judge, TRAJECTORY_ACCURACY_PROMPT
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

# 1. Deterministic Trajectory Matcher
strict_match_evaluator = create_trajectory_match_evaluator(
    trajectory_match_mode="strict"
)

# 2. LLM-as-a-Judge Trajectory Evaluator
llm_trajectory_judge = create_trajectory_llm_as_judge(
    model="openai:gpt-4o",
    prompt=TRAJECTORY_ACCURACY_PROMPT
)

# Example Trajectory Scoring
reference_trajectory = [
    HumanMessage(content="Check weather in SF"),
    AIMessage(content="", tool_calls=[{"id": "call_1", "name": "get_weather", "args": {"city": "SF"}}]),
    ToolMessage(content="72 degrees", tool_call_id="call_1"),
    AIMessage(content="It is 72 degrees in SF.")
]

def eval_trajectory(run, example):
    agent_messages = run.outputs.get("messages", [])
    ref_messages = example.outputs.get("messages", [])
    
    match_result = strict_match_evaluator(
        outputs=agent_messages,
        reference_outputs=ref_messages
    )
    return match_result
```

#### B. LLM-as-a-Judge Evaluators
LLM judges evaluate semantic qualities where deterministic checks are insufficient (e.g., answer correctness, reasoning quality, instruction adherence). They use a reference model (e.g. GPT-4o, Claude 3.5 Sonnet) with structured scoring prompts returning boolean or numeric scores along with qualitative reasoning explanations ([Agent Trajectory Testing](https://genai.qa/ai-agent-trajectory-testing-2026)).

#### C. Criteria & Rubric-Based Evaluators
Criteria evaluators assess outputs against explicit standards (e.g., correctness, conciseness, helpfulness, or custom organizational safety rubrics).

```python
def rubric_compliance_evaluator(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """Evaluates agent adherence to a multi-point rubric."""
    final_response = outputs.get("messages", [])[-1].content
    
    # Example scoring against custom criteria
    has_disclaimer = "Not financial advice" in final_response
    is_concise = len(final_response.split()) < 150
    
    score = 1.0 if (has_disclaimer and is_concise) else 0.0
    return {
        "key": "rubric_compliance",
        "score": score,
        "comment": f"Disclaimer present: {has_disclaimer}, Concise: {is_concise}"
    }
```

#### D. Custom Code Evaluators
Custom Python/TypeScript functions allow users to execute arbitrary assertion logic, schema verification, JSON parsing, regex validation, or AST static analysis on code generated by code agents.

---

### 1.3 Tracing, Run Trees & Intermediate State Verification

Tracing provides the foundation for agent evaluation in LangSmith ([Evaluating LLMs with LangSmith](https://www.analyticsvidhya.com/blog/2025/11/evaluating-llms-with-langsmith)). Each multi-step agent run is recorded as a hierarchical **Run Tree**:

- **Root Run**: Represents the overall task execution (captures top-level input query, final agent output, total latency, total token cost).
- **Child Spans**:
  - **LLM Calls**: Individual prompts sent to the LLM and raw generations returned.
  - **Tool Executions**: External API invocations, code sandboxes execution steps, or database queries.
  - **Subagent Delegations**: Nested agent runs in multi-agent architectures (e.g., LangGraph supervisor delegating to worker nodes).

#### Intermediate State & Tool Call Extraction
Evaluators can access intermediate run steps by inspecting the `Run` object's tree structure (`run.child_runs`). This allows evaluators to verify:
1. **Tool Argument Validation**: Checking if tool arguments match JSON schemas or expected parameter types before execution.
2. **Loop & Retry Detection**: Detecting repeating tool invocation loops or excessive retry steps.
3. **Intermediate State Corruption**: Ensuring internal state variables (e.g., memory dicts, message histories) maintain correct invariants across turns.

```
Root Run: Agent Execution ("Solve issue #42")
 ├── Child Span: LLM Plan Generation (LLM Call)
 ├── Child Span: Tool Call -> `repo_search` (Input: {"query": "auth_bug"})
 ├── Child Span: Tool Output -> `repo_search` (Result: 3 files found)
 ├── Child Span: Subagent Call -> `code_fixer_agent` (Child Run Tree)
 │    ├── Child Span: Sandbox Code Execution (`pytest tests/`)
 │    └── Child Span: Git Patch Generation
 └── Child Span: Final Summary Generation
```

---

## 2. Probe Datasets & Test Set Construction

Evaluating complex deep agents requires specialized probe datasets capable of isolating specific agent behaviors, failure recovery modes, and long-horizon reasoning limits.

### 2.1 Probe Datasets vs. Golden Datasets

| Dataset Type | Purpose | Composition | Example Probe Target |
| :--- | :--- | :--- | :--- |
| **Golden Dataset** | Overall regression testing & task accuracy. | Representative end-to-end task examples with ground truth outputs. | "Resolve standard customer refund request." |
| **Probe Dataset** | Targeted capability probing & stress testing. | Adversarial inputs, edge cases, partial tool failures, or long contexts. | "Handle API rate limits gracefully without looping." |

#### Probing Specific Capabilities:
- **Tool Selection Accuracy**: Presenting queries where multiple tools appear relevant but only one is optimal.
- **Failure Recovery & Retry Resilience**: Simulating tool failure responses (e.g., mock 500 error from a database tool) to test whether the agent degrades gracefully or retries with modified parameters.
- **Long-Context Reasoning & Goal Drift**: Multi-turn probes that inject distracting conversation turns to check if the agent retains its original objective.
- **Permission & Safety Boundaries**: Probing if the agent refuses unpermitted actions (e.g., invoking a write/delete tool without required user clearance).

---

### 2.2 Synthetic Dataset Generation & Trace Pipeline

Building high-coverage probe sets manually is time-consuming. LangChain and LangSmith support synthetic evaluation dataset creation through automated generation and production trace extraction ([Generate synthetic data for evaluating RAG systems](https://aws.amazon.com/blogs/machine-learning/generate-synthetic-data-for-evaluating-rag-systems-using-amazon-bedrock)).

```
+-----------------------------------------------------------------------------------+
|                        SYNTHETIC PROBE DATASET GENERATION                         |
|                                                                                   |
|  +---------------------+        +--------------------+        +----------------+  |
|  | Unstructured Docs / | -----> | LLM Probe Generator| -----> | Query Evolution|  |
|  | Agent Tool Schemas  |        | (Question/Task Gen)|        | (Evolver Agent)|  |
|  +---------------------+        +--------------------+        +-------+--------+  |
|                                                                       |           |
|                                                                       v           |
|  +---------------------+        +--------------------+        +-------+--------+  |
|  | Saved to LangSmith  | <----- |  Critique Filter   | <----- | Simulated Tool |  |
|  |  Dataset Registry   |        | (Deduplication/Val)|        | Response Sync  |  |
|  +---------------------+        +--------------------+        +----------------+  |
+-----------------------------------------------------------------------------------+
```

#### A. Synthetic Generation Pipeline:
1. **Tool Schema & Context Parsing**: Input tool definitions and domain context documents into an LLM generator.
2. **Query Seed Generation**: Generate baseline queries targeting specific tools or multi-step tasks.
3. **Query Evolution**: Apply evolution rules to seeds to create complex variants (e.g., adding constraints, introducing ambiguous phrasing, or simulating multi-intent user requests).
4. **Critique & Filtering**: Filter generated examples using an LLM critic to discard invalid or unanswerable tasks.

#### B. Production Trace Extraction Pipeline:
1. **Production Trace Monitoring**: Filter live production traces in LangSmith for specific tags or low user feedback scores (e.g., thumbs down).
2. **Trace-to-Example Conversion**: Extract the initial inputs and expected outputs (with human expert correction) using the LangSmith SDK.
3. **Dataset Registration**: Push the converted trace directly into a target probe dataset.

```python
from langsmith import Client

client = Client()

# Convert a failed production trace into a permanent regression probe
def convert_trace_to_probe(run_id: str, probe_dataset_name: str, corrected_output: dict):
    # Fetch original trace
    run = client.read_run(run_id)
    
    # Get or create probe dataset
    if not client.has_dataset(dataset_name=probe_dataset_name):
        dataset = client.create_dataset(dataset_name=probe_dataset_name, description="Production edge-case probe dataset")
    else:
        dataset = client.read_dataset(dataset_name=probe_dataset_name)
    
    # Add example with original inputs and ground truth corrected outputs
    client.create_example(
        inputs=run.inputs,
        outputs=corrected_output,
        dataset_id=dataset.id,
        metadata={"source_run_id": run_id, "probe_type": "production_failure_regression"}
    )
```

---

## 3. Online vs. Offline Evaluation Workflows

A complete agent evaluation strategy combines **offline pre-deployment regression testing** with **continuous online production evaluation**.

```
+-----------------------------------------------------------------------------------+
|                        ONLINE & OFFLINE EVALUATION LOOPS                          |
|                                                                                   |
|    OFFLINE EVALUATION (CI/CD)                     ONLINE EVALUATION (PRODUCTION)  |
|  +---------------------------+                 +-------------------------------+  |
|  | Golden & Probe Datasets   |                 | Production Live Agent Traffic |  |
|  +-------------+-------------+                 +---------------+---------------+  |
|                |                                               |                  |
|                v                                               v                  |
|  +-------------+-------------+                 +---------------+---------------+  |
|  | `evaluate()` Local Test   |                 | LangSmith Tracing & Sampling  |  |
|  |  (PR Gate / Block Merge)  |                 +---------------+---------------+  |
|  +-------------+-------------+                                 |                  |
|                |                                               v                  |
|                v                               +---------------+---------------+  |
|  +-------------+-------------+                 | Online Rules / Auto-Evaluators|  |
|  | Deploy Agent to Production|                +---------------+---------------+  |
|  +---------------------------+                                 |                  |
|                                                                v                  |
|                                                +---------------+---------------+  |
|                                                | Annotation Queues (Human Review|  |
|                                                +---------------+---------------+  |
|                                                                |                  |
|  +-------------------------------------------------------------+                  |
|  | (Add low-scoring traces to offline probe datasets for continuous learning)    |
|  v                                                                                |
+-----------------------------------------------------------------------------------+
```

### 3.1 Offline Regression Testing

Offline evaluation executes before shipping agent, prompt, tool, or model changes. It runs against curated golden and probe datasets to catch regressions early in the development lifecycle.

- **CI/CD Pipeline Integration**: Offline evals run as part of pull request checks using `pytest` and `langsmith.testing` ([Agent Evals - Docs by LangChain](https://docs.langchain.com/oss/python/langchain/test/evals)).
- **Cost & Concurrency Control**: Execution limits (`max_concurrency`) and local execution mode (`upload_results=False`) prevent high cloud LLM costs during rapid code iteration.

```python
import pytest
from langsmith import testing as t
from agentevals.trajectory.match import create_trajectory_match_evaluator

trajectory_evaluator = create_trajectory_match_evaluator(trajectory_match_mode="strict")

@pytest.mark.langsmith
def test_agent_tool_sequence_ci():
    # Invoke local agent graph
    outputs = my_agent_graph.invoke({"messages": [("user", "Run security audit on repo")]})
    
    # Log inputs/outputs to LangSmith testing context
    t.log_inputs({"query": "Run security audit on repo"})
    t.log_outputs({"messages": outputs["messages"]})
    
    # Run trajectory assertion
    evaluation = trajectory_evaluator(outputs=outputs["messages"], reference_outputs=expected_trajectory)
    assert evaluation["score"] is True, f"Trajectory mismatch: {evaluation.get('comment')}"
```

---

### 3.2 Continuous Online Monitoring & Shadow Evaluation

Once deployed, agents encounter real-world inputs and unpredicted tool behaviors that offline test suites may miss.

- **Production Trace Sampling**: LangSmith samples a configurable percentage of production traffic (e.g., 5-10% of live runs) to apply automated evaluators without incurring excessive latency or cost.
- **Online Rules / Auto-Evaluators**: Background evaluation rules configured in LangSmith run automatically on incoming live traces. Rules evaluate properties like output toxicity, hallucination rates, tool error codes, or cost threshold breaches.
- **Shadow Evaluation**: Deploying a candidate agent version in shadow mode (executing side-by-side with production traffic without returning the shadow output to the user) allows real-world performance comparisons before full rollout.

---

### 3.3 Human-in-the-Loop (HITL) Feedback & Annotation Queues

Automated evaluators require ongoing calibration against expert human judgment ([AI Agent Evaluation: Trajectory and Tool Calls](https://langfuse.com/resources/engineering/ai-agent-evaluation)).

#### Human Feedback Loops:
1. **Annotation Queues**: Low-confidence runs, auto-evaluator failures, or sampled production traces are routed automatically to LangSmith Annotation Queues for human review.
2. **Expert Scoring**: Subject Matter Experts (SMEs) inspect intermediate tool calls, reasoning steps, and final outputs, assigning criteria scores and qualitative feedback.
3. **Evaluator Calibration**: Disagreements between human scores and LLM-as-a-judge scores trigger judge prompt tuning to realign automated judges with human standards.
4. **Dataset Enrichment**: High-value failure cases identified in annotation queues are added back into offline probe datasets, closing the continuous improvement loop.

---

### Key Operational Metrics Summary for Agent Evaluation

| Metric Category | Specific Metric | Evaluation Level | Primary Evaluator Type |
| :--- | :--- | :--- | :--- |
| **Tool Calling** | Tool Selection Accuracy | Step / Component | Deterministic Code / Match |
| | Tool Argument Validity | Step / Component | Schema Validation / Code |
| **Trajectory** | Step Efficiency & Path Length | Trajectory | Code / LLM Judge |
| | Loop / Retry Rate | Trajectory | Run Tree Code Evaluator |
| **Task Completion** | Goal Completion Score | End-to-End | LLM Judge / Rubric |
| | Safety & Guardrail Compliance| End-to-End & Step | Criteria / Code Guardrail |

---

### Sources

- **Easier evaluations with LangSmith SDK v0.2** (Dec 2024), LangChain Official Blog  
  URL: https://www.langchain.com/blog/easier-evaluations-with-langsmith-sdk-v0-2  
  *Establishes*: SDK v0.2 unified `evaluate()` API, simplified evaluator signatures, direct `langgraph` object evaluation, local execution mode (`upload_results=False`).

- **Agent Evals - Docs by LangChain** (2026), Official Documentation  
  URL: https://docs.langchain.com/oss/python/langchain/test/evals  
  *Establishes*: `agentevals` package, `create_trajectory_match_evaluator` modes (`strict`, `unordered`, `subset`, `superset`), `create_trajectory_llm_as_judge`, pytest integration with LangSmith.

- **LLM Agent Evaluation Metrics in 2026: Tool Calling, Task Completion, Reasoning, and Trace-Based Evals** (Jun 2026), Confident AI  
  URL: https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide  
  *Establishes*: Agent evaluation dimensions (tool calling, argument correctness, step efficiency, plan adherence, task completion), deterministic vs LLM-as-a-judge metrics.

- **AI Agent Evaluation: Trajectory and Tool Calls** (2026), Langfuse Engineering Resources  
  URL: https://langfuse.com/resources/engineering/ai-agent-evaluation  
  *Establishes*: Trajectory vs end-to-end evaluation, structured tool call extraction, online vs offline evaluation loops, session multi-turn scoring, human annotation queues.

- **LangSmith vs Braintrust vs Galileo: Agent Trajectory Testing** (Apr/Jul 2026), genai.qa  
  URL: https://genai.qa/ai-agent-trajectory-testing-2026  
  *Establishes*: Golden trajectory datasets, tool-call precision/recall, path efficiency, replay testing from production traces, CBUAE / regulatory compliance requirement for trajectory evidence.

- **Evaluating LLMs with LangSmith** (Nov 2025), Analytics Vidhya  
  URL: https://www.analyticsvidhya.com/blog/2025/11/evaluating-llms-with-langsmith  
  *Establishes*: LangSmith dataset setup (`Client.create_dataset`, `create_examples`), `@traceable` instrumentation, run trees, custom evaluator functions (`Run`, `Example`).

- **Generate synthetic data for evaluating RAG systems using Amazon Bedrock** (Sep 2024), AWS ML Blog  
  URL: https://aws.amazon.com/blogs/machine-learning/generate-synthetic-data-for-evaluating-rag-systems-using-amazon-bedrock  
  *Establishes*: Synthetic evaluation dataset generation workflow, query evolution, critique filtering pipelines.
