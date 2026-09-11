# Research Review: LangChain Deep Agents and Code Agents Evaluation

**Date:** 2026-09-11  
**Target:** `/lanchain_evals/final_report.md` against `/lanchain_evals/research_request.md`

## 1. Item-by-Item Verification against Research Request

- **How LangChain evaluates deep agents and code agents (Overview):**
  - **Status:** Answered
  - **Location:** `/lanchain_evals/final_report.md` (Executive Summary & Section 1)
  - **Details:** Detailed overview covering multi-step agent evaluation, end-to-end task completion, intermediate trajectory correctness, and execution sandboxes.

- **LangSmith Evaluation Framework Architecture:**
  - **Status:** Answered
  - **Location:** `/lanchain_evals/langsmith_eval_architecture.md` & `/lanchain_evals/final_report.md` (Section 1)
  - **Details:** Explains `evaluate()` and `aevaluate()` SDK APIs, local execution (`upload_results=False`), run tree inspection, deterministic evaluators, trajectory evaluators (`agentevals`), and LLM-as-a-judge evaluators.

- **Code Agents Evaluation, SWE-bench & Execution Sandboxes:**
  - **Status:** Answered
  - **Location:** `/lanchain_evals/code_agents_swebench_sandboxes.md` & `/lanchain_evals/final_report.md` (Section 2)
  - **Details:** Evaluates coding agents against SWE-bench Full, Lite, Verified, and SWE-Bench-CL. Compares sandbox isolation technologies (LangSmith Sandbox, E2B, Daytona, Modal, Vercel, Docker), tracking Pass@1, FAIL_TO_PASS/PASS_TO_PASS, patch validity, token costs, and tool interfaces.

- **Deep Agents Trajectory Analysis, Rubric Middleware & Diagnostic Probes:**
  - **Status:** Answered
  - **Location:** `/lanchain_evals/deepagents_trajectory_rubrics_middleware.md` & `/lanchain_evals/final_report.md` (Sections 3 & 4)
  - **Details:** Analyzes multi-step execution paths, tool precision/recall/F1, shortest path efficiency ratio, loop/cycle detection, `RubricMiddleware` runtime grading, fault injection middleware, and diagnostic probe datasets (context stuffing, lost-in-the-middle, cyclic delegation).

## 2. Corrections & Refinements Made

1. **`RubricMiddleware` GraderVerdict Enum:**
   - *Correction:* Specified that `GraderVerdict` in the installed `deepagents.middleware.rubric` module uses `Literal["satisfied", "needs_revision", "failed"]`, whereas initial conceptual drafts omitted the `"failed"` verdict status.
2. **Backend Imports vs Package Boundaries:**
   - *Correction:* Clarified that `LangSmithSandbox`, `LocalShellBackend`, `FilesystemBackend`, `StateBackend`, `StoreBackend`, and `CompositeBackend` are imported directly from `deepagents.backends`, whereas external integrations like `DaytonaSandbox` (`langchain_daytona`) or `E2BSandbox` (`langchain_e2b`) are separate integration packages.
3. **Built-in Tool Suite in `create_deep_agent`:**
   - *Correction:* Verified that `create_deep_agent` automatically installs `write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`, and `task` as default tools in the stack.

## 3. Assumptions and Claims to Note

- Benchmark SOTA resolution rates on SWE-bench Verified (76.1%–82.0%) reflect recent published reports (e.g. Verdent, Claude Sonnet 3.7 / Codex benchmarks) as of 2026.
- Remote sandbox providers (E2B, Daytona, Modal) require external API keys or container daemons when executed outside local test environments.
