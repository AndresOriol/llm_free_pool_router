# Research Request: LangChain Evaluation of Deep Agents and Code Agents

## Original Request
Research how LangChain evaluates deep agents and code agents (coding agents). Start with an overview of how LangChain and the deepagents framework evaluate deep agents and code agents (eval frameworks, LangSmith evals, benchmarks like SWE-bench, rubric middleware, probe datasets, trajectory analysis, sandbox/backends, middleware testing). From that starting point, identify specific deep topics and questions that arise, and delegate to subagents to investigate those topics deeply. Document all findings and synthesize a final report in lanchain_evals.

## Objectives & Key Themes
1. **LangChain & LangSmith Evaluation Architecture**: Evaluator paradigms (trajectory evaluators, LLM-as-a-judge, step-level vs end-to-end), LangSmith datasets/probe datasets, online vs offline evaluation.
2. **Code Agent Evaluation & Benchmarks**: Benchmarking coding agents on SWE-bench (Verified, Lite) and HumanEval, execution sandbox environments (E2B, Docker, Modal), test harness setups, security and state resets.
3. **Deep Agent Mechanics & Middleware Evaluation**: Rubric middleware, trajectory analysis, middleware testing/interception, probe datasets, multi-agent/subagent delegation tracing and verification, error recovery.
