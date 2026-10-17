from conftest import ScriptedModel, calls
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from soranimi.config import Settings
from soranimi.context.auto_compact import compact_history
from soranimi.context.micro_compact import micro_compact
from soranimi.context.token_estimator import estimate_tokens
from soranimi.runtime import AgentSession


def assert_tool_pairs(messages):
    pending = set()
    for message in messages:
        if isinstance(message, AIMessage):
            assert not pending
            pending = {call["id"] for call in message.tool_calls}
        elif isinstance(message, ToolMessage):
            assert message.tool_call_id in pending
            pending.remove(message.tool_call_id)
        elif isinstance(message, HumanMessage):
            assert not pending
    assert not pending


def history():
    result = []
    for index in range(5):
        result += [
            HumanMessage(content=f"Task {index}"),
            AIMessage(
                content="",
                tool_calls=[{"name": "read_file", "args": {"path": "a.py"}, "id": str(index)}],
            ),
            ToolMessage(content="large output " * 100, name="read_file", tool_call_id=str(index)),
            AIMessage(content=f"Done {index}"),
        ]
    return result


def test_micro_keeps_recent_outputs_and_call_pairs():
    original = history()
    compacted = micro_compact(original)
    assert_tool_pairs(compacted)
    assert "archived" in compacted[2].content
    assert compacted[-2].content == original[-2].content
    assert "large output" in original[2].content
    assert estimate_tokens(compacted) < estimate_tokens(original)
    assert estimate_tokens([HumanMessage(content="你好" * 100)]) > 150


def test_summary_keeps_whole_recent_turns():
    model = ScriptedModel(
        responses=[AIMessage(content="Completed tasks 0-2. Preserve a.py. Continue 3-4.")]
    )
    compacted = compact_history(history(), model, focus="a.py")
    assert_tool_pairs(compacted)
    assert compacted[1].content == "Task 3"
    assert "a.py" in compacted[0].content
    assert "Focus: a.py" in model.observed[0][0].content


def test_auto_compaction_replaces_state_and_retains_archive(tmp_path):
    model = ScriptedModel(
        responses=[
            AIMessage(content="User needs a.py fixed; preserve constraints."),
            AIMessage(content="Continuing from summary"),
        ]
    )
    with AgentSession(Settings(workspace=tmp_path, context_tokens=1000), model) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="a.py fix " + "context " * 1000)]}, session.config
        )
        assert len(result["messages"]) == 2
        assert "Session summary" in result["messages"][0].content
        assert "context context" in session.transcript.path.read_text(encoding="utf-8")
        assert len(model.observed[1]) == 2  # system prompt + summary


def test_compact_tool_runs_after_complete_batch(tmp_path):
    model = ScriptedModel(
        responses=[
            calls(("compact", {"focus": "preserve task"}), ("list_dir", {})),
            AIMessage(content="Summary preserves task"),
            AIMessage(content="Done"),
        ]
    )
    with AgentSession(Settings(workspace=tmp_path), model) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Compact and continue")]}, session.config
        )
        assert not result.get("compact_requested")
        assert "Session summary" in result["messages"][0].content
        assert_tool_pairs(result["messages"])


def test_fifty_turns_stay_resumable_without_orphan_results(tmp_path):
    model = ScriptedModel(responses=[AIMessage(content=f"Answer {i}") for i in range(50)])
    with AgentSession(Settings(workspace=tmp_path), model) as session:
        for i in range(50):
            result = session.graph.invoke(
                {"messages": [HumanMessage(content=f"Task {i}")]}, session.config
            )
        assert len(result["messages"]) == 100
        assert_tool_pairs(result["messages"])
        assert all(isinstance(m[0], SystemMessage) for m in model.observed)


def test_separate_summary_model_handles_automatic_and_manual_compaction(tmp_path):
    import io

    from rich.console import Console

    from soranimi.cli.app import handle_slash
    from soranimi.cli.renderer import Renderer

    main = ScriptedModel(responses=[AIMessage(content="Continue coding")])
    summary = ScriptedModel(
        responses=[AIMessage(content="Automatic summary"), AIMessage(content="Manual summary")]
    )
    with AgentSession(
        Settings(workspace=tmp_path, context_tokens=1000), main, summary_model=summary
    ) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="context " * 2000)]}, session.config
        )
        assert "Automatic summary" in result["messages"][0].content
        assert main.cursor == 1 and summary.cursor == 1
        handle_slash("/compact preserve paths", session, Renderer(Console(file=io.StringIO())))
        messages = session.graph.get_state(session.config).values["messages"]
        assert "Manual summary" in messages[0].content
        assert "Focus: preserve paths" in summary.observed[1][0].content
        assert main.cursor == 1 and summary.cursor == 2


def test_summary_client_is_lazy_cached_and_uses_separate_endpoint(tmp_path):
    from soranimi.agent.model_factory import SummaryModel

    main = ScriptedModel(responses=[])
    summary = ScriptedModel(responses=[AIMessage(content="one"), AIMessage(content="two")])
    settings = Settings(
        workspace=tmp_path,
        model="openai:main",
        base_url="http://main/v1",
        summary_model="anthropic:summary",
    )
    seen = []

    def factory(selected):
        seen.append(selected)
        return summary

    lazy = SummaryModel(settings, main, factory)
    assert not seen
    lazy.invoke([HumanMessage(content="one")])
    lazy.invoke([HumanMessage(content="two")])
    assert len(seen) == 1
    assert seen[0].model == "anthropic:summary" and seen[0].base_url is None
    assert settings.base_url == "http://main/v1"
    assert (
        SummaryModel(Settings(workspace=tmp_path, summary_model=None), main, factory).client is main
    )


def test_summary_failure_preserves_checkpoint_history(tmp_path):
    import pytest

    main = ScriptedModel(responses=[calls(("compact", {}))])
    summary = ScriptedModel(responses=[AIMessage(content="")])
    with AgentSession(Settings(workspace=tmp_path), main, summary_model=summary) as session:
        with pytest.raises(ValueError, match="empty summary"):
            session.graph.invoke(
                {"messages": [HumanMessage(content="Original task")]}, session.config
            )
        messages = session.graph.get_state(session.config).values["messages"]
        assert messages[0].content == "Original task"
        assert_tool_pairs(messages)


def test_summary_endpoint_does_not_inherit_main_endpoint_environment(tmp_path, monkeypatch):
    from soranimi.agent.model_factory import SummaryModel

    monkeypatch.setenv("SORANIMI_BASE_URL", "http://main-only.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.delenv("SORANIMI_SUMMARY_BASE_URL", raising=False)
    settings = Settings(workspace=tmp_path, summary_model="openai:summary")
    client = SummaryModel(settings, None).client
    assert "main-only.invalid" not in str(client.root_client.base_url)


def test_subagent_compaction_uses_separate_summary_model(tmp_path):
    from soranimi.subagent.subgraph import run_worker

    main = ScriptedModel(responses=[AIMessage(content="Read-only findings")])
    summary = ScriptedModel(responses=[AIMessage(content="Preserve the research objective")])
    result = run_worker(
        "Inspect project " * 2000,
        main,
        [],
        Settings(workspace=tmp_path, context_tokens=1000),
        "",
        summary_model=summary,
    )
    assert result["summary"] == "Read-only findings"
    assert main.cursor == 1 and summary.cursor == 1
