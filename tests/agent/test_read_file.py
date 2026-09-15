"""`read_file` reads the whole file, and the model is told so consistently.

Three things have to agree or the change is worse than not making it: the
schema default, the tool description, and the prompt. A model told to paginate
by one of them will paginate.

These tests also guard the seams. The description rides deepagents' own
`HarnessProfile`, which is supported but keyed on a provider name; the default
rides `ReadFileSchema`'s field, which is public but not a documented setting
([agent/utils/file_tools.py](../../agent/utils/file_tools.py)). An upgrade
that moves either stops the change applying -- silently, at runtime. Here it
fails loudly instead.
"""

import pathlib
import tempfile

from deepagents.middleware import filesystem as fs
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.code.agent import build_agent
from agent.utils import file_tools


class _Router(GenericFakeChatModel):
    """A model that answers like the pool: one `read_file` call, then stops.

    It reports the pool's `ls_provider`, because that -- not `_llm_type` -- is
    what deepagents resolves a pre-built model's profile through. Without it
    the description override would not apply, which is the point of
    `test_the_description_reaches_the_tool`.
    """

    def _get_ls_params(self, **kwargs):
        params = super()._get_ls_params(**kwargs)
        params["ls_provider"] = file_tools.ROUTER_PROVIDER
        return params

    def bind_tools(self, tools, **kwargs):
        return self


def _agent(workdir):
    model = _Router(messages=iter([
        AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": {"file_path": "/big.py"}, "id": "c1"}]),
        AIMessage(content="done"),
    ]))
    return build_agent(workdir, model, peers=[])


def _read(workdir, args) -> str:
    model = _Router(messages=iter([
        AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": args, "id": "c1"}]),
        AIMessage(content="done"),
    ]))
    agent = build_agent(workdir, model, peers=[])
    messages = agent.invoke({"messages": [HumanMessage("hi")]},
                            {"recursion_limit": 10})["messages"]
    return str(next(m for m in messages if isinstance(m, ToolMessage)).content)


def _workspace(name: str, text: str) -> pathlib.Path:
    workdir = pathlib.Path(tempfile.mkdtemp())
    (workdir / name).write_text(text, encoding="utf-8")
    return workdir


def _read_file_tool(agent):
    for node in agent.nodes.values():
        tools = getattr(getattr(node, "bound", None), "tools_by_name", None)
        if tools and "read_file" in tools:
            return tools["read_file"]
    raise AssertionError("the agent has no read_file tool")


# --- the default ---------------------------------------------------------------


def test_a_read_with_no_window_returns_the_whole_file():
    """The point of the change. Upstream would have stopped at line 100."""
    workdir = _workspace("big.py", "\n".join(f"line {i}" for i in range(1, 501)))
    content = _read(workdir, {"file_path": "/big.py"})

    assert "line 1\n" in content
    assert "line 500" in content
    assert len(content.splitlines()) == 500


def test_a_window_still_works():
    """The ceiling moved; it was not removed. `offset` is 0-based, so the line
    the tool printed as 148 is `offset=147` -- which is what the prompt and the
    tool description both say."""
    workdir = _workspace("big.py", "\n".join(f"line {i}" for i in range(1, 501)))
    content = _read(workdir, {"file_path": "/big.py", "offset": 147, "limit": 3})

    assert [line.split("\t")[1] for line in content.splitlines()] == [
        "line 148", "line 149", "line 150"]


def test_the_ceiling_is_moved_not_removed():
    """A whole-file default without a cap would let one read fill the window.
    The middleware still stops at ~20,000 tokens."""
    workdir = _workspace("huge.py",
                         "\n".join(f"line {i} " + "x" * 60 for i in range(1, 4001)))
    content = _read(workdir, {"file_path": "/huge.py"})

    assert len(content) <= fs.NUM_CHARS_PER_TOKEN * 20_000
    assert "truncated" in content.lower()


def test_the_truncation_message_names_the_way_out():
    """Upstream's message says to reformat the file, which is the wrong advice
    for a long source file and does not mention the window that would get the
    rest. Making truncation the normal way to meet a large file is what puts
    the weight on this message."""
    workdir = _workspace("huge.py",
                         "\n".join(f"line {i} " + "x" * 60 for i in range(1, 4001)))
    content = _read(workdir, {"file_path": "/huge.py"})

    assert "offset" in content and "limit" in content
    assert "/huge.py" in content, "it names the file it is talking about"


# --- the seams -----------------------------------------------------------------


def test_the_schema_field_the_default_rides_still_exists():
    """**The upgrade guard for the default.** If deepagents drops `limit` from
    `ReadFileSchema`, or moves the default out of it, the whole-file read goes
    back to 100 lines with nothing raised."""
    assert "limit" in fs.ReadFileSchema.model_fields, (
        "deepagents moved read_file's limit out of ReadFileSchema; update "
        "agent/utils/file_tools.py")
    assert fs.ReadFileSchema.model_fields["limit"].default == file_tools.WHOLE_FILE
    assert fs.ReadFileSchema(file_path="/x").limit == file_tools.WHOLE_FILE


def test_the_description_reaches_the_tool():
    """**The upgrade guard for the description.** It rides a `HarnessProfile`
    resolved through the model's provider, so it applies only while the
    class name and the registered key agree."""
    workdir = _workspace("big.py", "line 1")
    description = _read_file_tool(_agent(workdir)).description

    assert description == file_tools.READ_FILE_DESCRIPTION
    assert "reads up to 100 lines" not in description.lower()


def test_the_router_key_is_the_models_own_provider_name():
    """The profile key is not a guess -- it is what the pool's model reports.

    LangChain derives `ls_provider` from the class name, so renaming
    `RouterChatModel` silently unregisters the override. This is the test that
    turns that into a failure."""
    from agent.utils.chat_model import RouterChatModel

    provider = RouterChatModel(router=None)._get_ls_params()["ls_provider"]
    assert provider == file_tools.ROUTER_PROVIDER


# --- what the model is told ----------------------------------------------------


def test_the_prompt_agrees_with_the_tool():
    from agent.code import agent as code

    prompt = code.system_prompt(code.template_values(128_000, 4, ()))
    assert "read_file` gives you the whole file" in prompt
    assert "limit=100" not in prompt
    assert "from_line" not in prompt, "an argument this tool no longer takes"
