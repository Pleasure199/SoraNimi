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
