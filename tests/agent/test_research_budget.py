from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent.explore import agent as explore
from agent.runtime import web
from agent.runtime.backend import RestrictedShellBackend


def test_search_budget_blocks_overflow_but_allows_notes_and_resets_per_run(
        monkeypatch, tmp_path):
    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    class Pool:
        calls = 0

        def search(self, query, **kwargs):
            self.calls += 1
            return {"results": [{"title": "Source", "url": "https://example.com"}]}

    pool = Pool()
    monkeypatch.setattr(web, "fetch_page", lambda url: "evidence")
    replies = []
    for run in range(2):
        calls = [{"name": "tavily_search", "args": {"query": f"question {i}"},
                  "id": f"search-{run}-{i}"}
                 for i in range(explore.MAX_SEARCHES_PER_SUBAGENT + 1)]
        calls.append({"name": "write_file", "id": f"save-{run}", "args": {
            "file_path": f"/research/note-{run}.md", "content": "partial evidence"}})
        replies.extend([AIMessage(content="", tool_calls=calls),
                        AIMessage(content="Saved the available evidence.")])
    values = explore.template_values()
    described = explore.descriptions(values)
    tools = explore.research_tools(pool, tmp_path, explore.RESEARCH_DIR, described)
    spec = explore.subagents(tools, values, described)[0]
    agent = create_deep_agent(
        model=Model(messages=iter(replies)), tools=spec["tools"],
        system_prompt=spec["system_prompt"], middleware=spec["middleware"],
        backend=RestrictedShellBackend(root_dir=str(tmp_path), allowed_programs=()))
    for run in range(2):
        agent.invoke({"messages": [("user", "Research and save findings")]})
        assert pool.calls == (run + 1) * explore.MAX_SEARCHES_PER_SUBAGENT
        assert (tmp_path / f"research/note-{run}.md").read_text() == "partial evidence"
