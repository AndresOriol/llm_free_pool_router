# Research Plan: LangChain Deep Agents and Code Agents Evaluation

## Context & Decision Focus
The goal is to provide a rigorous, comprehensive, technical analysis of how LangChain (and associated ecosystems such as LangSmith, LangGraph, Deep Agents frameworks) evaluates deep multi-step reasoning agents and coding agents.

## Workstreams

### Workstream 1: LangSmith Evaluation Framework & Architecture for Complex Agents
- **Target File**: `/lanchain_evals/langsmith_eval_architecture.md`
- **Key Questions**:
  - How does LangSmith structure evaluations for multi-step agentic systems?
  - What evaluators exist (trajectory evaluators, criteria scorers, custom run evaluators, LLM-as-a-judge)?
  - How are probe datasets and synthetic test sets constructed and run in LangSmith?
  - How are online tracing and offline regression testing integrated?

### Workstream 2: Code Agents Evaluation, SWE-bench & Execution Sandboxes
- **Target File**: `/lanchain_evals/code_agents_swebench_sandboxes.md`
- **Key Questions**:
  - How are code agents (e.g. LangChain SWE-agent / LangGraph coding agents) evaluated against industry benchmarks like SWE-bench (Verified, Lite)?
  - What execution sandbox backends are used (E2B, Docker, Modal, Daydreams, Daytona)?
  - How are harnesses structured for safe code execution, state reset, patch verification, and reproducibility?
  - What metrics are tracked (resolve rate, patch validity, tool call accuracy, cost, latency)?

### Workstream 3: Deep Agents Trajectory Analysis, Rubrics & Middleware Testing
- **Target File**: `/lanchain_evals/deepagents_trajectory_rubrics_middleware.md`
- **Key Questions**:
  - How does trajectory analysis work in multi-step deep agents (subagent delegation, tool selection, plan adaptation)?
  - What is rubric middleware and how is it used to enforce or evaluate step-by-step agent compliance?
  - How does middleware testing / interceptor evaluation validate intermediate states, guardrails, and error recovery?
  - How do probe datasets target edge cases, failure modes, and long-horizon planning?

## Execution Strategy
- Delegate 3 specialized sub-agents in parallel to investigate the three workstreams with primary documentation, GitHub repos, technical blogs, and benchmarks.
- Synthesize all findings into `/lanchain_evals/final_report.md`.
- Perform full review in `/lanchain_evals/review.md`.
