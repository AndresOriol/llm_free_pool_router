"""The deep-research prompts, ported from LangChain's reference agent.

The text lives in [prompts/](prompts/), one Markdown file per section, so
changing how this agent researches is an edit to prose rather than to
Python. This module is the provenance and the list of what we changed.

Source: `langchain-ai/deepagents-quickstarts`, `deep_research/research_agent/
prompts.py` (MIT). This repo already ports `deepagents-code`'s prompt for the
coding agent and says so ([6.5.2](../../docs/06-agent.md)); this is the same
move for the research side, and for the same reason: the reference is a
maintained artefact that has been tuned against real runs, and our own prose
lost to it on every axis a recorded run could measure
([15.7](../../docs/15-explorer.md#157-measured-against-a-reference-research-agent)).

**Based on the upstream port.** Pool constraints and measured research failures
motivate the adaptations marked `ADAPTED` in those files, so the next person can
diff against the source rather than guess what we invented.

The adaptations, in full:

1. **File paths are under `/research/`.** Upstream writes `/research_request.md`
   and `/final_report.md` at the workdir root. Here the workdir is a *project*
   that a coding agent then works in, and the handoff contract is that research
   lives in `/research/` ([15.1](../../docs/15-explorer.md#151-what-it-is-for));
   a report at the root would land in the diff the coding agent produces.
2. **A search budget in the orchestrator too.** Upstream bounds the sub-agent
   (5 searches) and the delegation rounds (3). Our pool is bounded by *requests
   per day*, not tokens, so the orchestrator is told the same numbers rather
   than left to infer them.
3. **Naming a report that already exists.** Upstream is single-shot. This
   explorer answers repeated delegations into one workdir, so `final_report.md`
   would be overwritten by the next question.
4. **`read_url` is gone.** Upstream's `tavily_search` returns the page itself,
   so there is no second tool to reach for -- which is the whole point of the
   change ([15.7.2](../../docs/15-explorer.md#1572-the-one-difference-not-copied)).
5. **Decision-led briefs and saved findings.** The Machintl reference session
   separates scenarios, checks shared blocking questions and saves workstream
   evidence before synthesizing. Their quality must be measured rather than
   inferred from the instructions.
6. **No `ls`, and a tool list that matches the tools.** The surface here is
   chosen rather than inherited ([tools.py](tools.py)), so upstream's `ls
   /research` becomes `research_status`
   ([research_tools.py](research_tools.py)), and the researcher's "two specific
   research tools" -- which was never true, since it has always been asked to
   save its findings to a file -- names the ones it actually holds.
7. **A review step.** Upstream's workflow ends at the report. Step 6 here reads
   the request back and says, item by item, whether what was asked was answered,
   correcting what the sources do not support
   ([15.5.4](../../docs/15-explorer.md#1554-the-review-at-the-end)).
8. **The reply is a pointer, not a second copy of the findings.** Upstream has
   the researcher return its findings in full and the orchestrator summarize
   the report it just wrote. Both were observed here: a run pays for its
   conclusions twice, once into the file that is the deliverable and once into
   a message whose reader is about to open the file anyway.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS = Path(__file__).parent / "prompts"


def _read(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8").strip()


# The orchestrator's two sections, and the researcher sub-agent's instructions.
# `delegation` and `researcher` carry `{placeholders}` the session fills with the
# budgets it enforces (agent/explore/session.py).
RESEARCH_WORKFLOW_INSTRUCTIONS = _read("workflow.md")
SUBAGENT_DELEGATION_INSTRUCTIONS = _read("delegation.md")
RESEARCHER_INSTRUCTIONS = _read("researcher.md")
