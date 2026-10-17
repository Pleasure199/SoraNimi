import json
import sys
import threading
from pathlib import Path

import pytest
from conftest import ScriptedModel, calls
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from soranimi.agent.demo import DemoModel
from soranimi.config import Settings
from soranimi.instructions.loader import load_instructions
from soranimi.mcp.client import MCPConnection
from soranimi.mcp.registry import merge_tools
from soranimi.persistence.store import MemoryStore
from soranimi.runtime import AgentSession
from soranimi.subagent.plan_execute import parallel_research


def test_hierarchical_instructions_and_limit(tmp_path):
    root = tmp_path / "project"
    child = root / "src"
    child.mkdir(parents=True)
    global_path = tmp_path / "global.md"
    global_path.write_text("GLOBAL")
    (root / "AGENTS.md").write_text("PROJECT")
    (child / "AGENTS.md").write_text("CHILD")
    text = load_instructions(root, child, global_path)
    assert text.index("GLOBAL") < text.index("PROJECT") < text.index("CHILD")
    assert "limit reached" in load_instructions(root, child, global_path, max_bytes=3)


def test_memory_survives_restart(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.put("project", "style", {"text": "four spaces"})
    store.close()
    store = MemoryStore(tmp_path / "memory.db")
    try:
        assert store.search("project", "spaces")[0]["key"] == "style"
        assert not store.search("other")
    finally:
        store.close()


def test_send_parallelism_limit_and_aggregation():
    lock = threading.Lock()
    active = 0
    peak = 0
    barrier = threading.Barrier(2)

    def worker(task):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return {"task": task, "summary": "result"}

    results = parallel_research(["one", "two", "three", "four"], worker, max_parallel=2)
    assert {r["task"] for r in results} == {"one", "two", "three", "four"}
    assert peak == 2


def test_real_subagent_graphs_have_isolated_readonly_context(tmp_path):
    with AgentSession(Settings(workspace=tmp_path, subagents=True), DemoModel()) as session:
        tools = {t.name: t for t in session.tools}
        results = tools["parallel_subagents"].invoke({"tasks": ["Inspect structure", "List files"]})
        assert len(results) == 2
        assert all("summary" in r for r in results)
        assert not session.graph.get_state(session.config).values


def test_python_repository_map(session, tools):
    root = session.settings.workspace
    (root / "util.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    (root / "main.py").write_text(
        "from util import helper\nclass App:\n    pass\n", encoding="utf-8"
    )
    result = tools["repo_map"].invoke({"token_budget": 100})
    assert "helper" in result and "App" in result
    assert result.index("util.py") < result.index("main.py")


def test_duplicate_tools_rejected(tools):
    with pytest.raises(ValueError, match="Duplicate"):
        merge_tools([tools["list_dir"]], [tools["list_dir"]])


def test_mcp_stdio_discovery_and_invocation(tmp_path):
    pytest.importorskip("langchain_mcp_adapters")
    server = Path(__file__).parent / "fixtures" / "echo_mcp.py"
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {"echo": {"transport": "stdio", "command": sys.executable, "args": [str(server)]}}
        ),
        encoding="utf-8",
    )
    with MCPConnection(config) as connection:
        tools = {t.name: t for t in connection.tools}
        assert "mcp_echo_add" in tools
        result = tools["mcp_echo_add"].invoke({"a": 2, "b": 3})
        assert "5" in str(result)


def test_mcp_tool_requires_approval_and_result_returns_to_model(tmp_path):
    @tool
    def mcp_example(value: str) -> str:
        """Example remote side effect."""
        return f"remote: {value}"

    from soranimi.agent.graph import build_graph
    from soranimi.persistence.checkpointer import open_checkpointer
    from soranimi.persistence.transcript import Transcript

    model = ScriptedModel(
        responses=[calls(("mcp_example", {"value": "hello"})), AIMessage(content="done")]
    )
    settings = Settings(workspace=tmp_path)
    with open_checkpointer() as saver:
        graph = build_graph(
            model, [mcp_example], settings, saver, Transcript(tmp_path / "transcript.jsonl")
        )
        config = {"configurable": {"thread_id": "mcp-test"}}
        result = graph.invoke({"messages": [HumanMessage(content="call MCP")]}, config)
        assert "__interrupt__" in result
        result = graph.invoke(Command(resume=True), config)
        assert any(
            isinstance(m, ToolMessage) and m.content == "remote: hello" for m in result["messages"]
        )
