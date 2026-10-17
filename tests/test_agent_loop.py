import os
import subprocess
import sys

from conftest import ScriptedModel, calls
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from soranimi.config import Settings
from soranimi.permission.modes import PermissionMode
from soranimi.persistence.time_travel import list_checkpoints, rewind
from soranimi.runtime import AgentSession


def test_read_edit_verify_loop(tmp_path):
    target = tmp_path / "maths.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_maths.py").write_text(
        "from maths import add\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    # Use this project's installed interpreter to run an actual generated test.
    command = (
        f"& '{sys.executable}' -m pytest -q"
        if os.name == "nt"
        else f"'{sys.executable}' -m pytest -q"
    )
    model = ScriptedModel(
        responses=[
            calls(("read_file", {"path": "maths.py"})),
            calls(
                (
                    "edit_file",
                    {
                        "path": "maths.py",
                        "old_string": "return a - b",
                        "new_string": "return a + b",
                    },
                )
            ),
            calls(("bash", {"command": command})),
            AIMessage(content="Fixed and verified."),
        ]
    )
    with AgentSession(Settings(workspace=tmp_path), model) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Fix add and run tests")]}, session.config
        )
        assert "__interrupt__" in result
        result = session.graph.invoke(Command(resume=True), session.config)
        tool_results = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_results) == 3
        assert '"exit_code": 0' in tool_results[-1].content
        assert "return a + b" in target.read_text()
        assert result["messages"][-1].content == "Fixed and verified."


def test_batch_approval_does_not_replay_prior_tool(tmp_path):
    model = ScriptedModel(
        responses=[
            calls(
                ("write_file", {"path": "a.txt", "content": "once"}),
                ("bash", {"command": "echo approved"}),
            ),
            AIMessage(content="done"),
        ]
    )
    with AgentSession(Settings(workspace=tmp_path), model) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Do both")]}, session.config
        )
        assert "__interrupt__" in result
        assert session.snapshots.db.execute("SELECT COUNT(*) FROM edits").fetchone()[0] == 1
        session.graph.invoke(Command(resume=False), session.config)
        assert session.snapshots.db.execute("SELECT COUNT(*) FROM edits").fetchone()[0] == 1
        assert (tmp_path / "a.txt").read_text() == "once"
        assert any(
            "cancelled" in m.content
            for m in session.graph.get_state(session.config).values["messages"]
            if isinstance(m, ToolMessage)
        )


def test_manual_approval_survives_restart(tmp_path):
    settings = Settings(workspace=tmp_path, permission=PermissionMode.MANUAL)
    model = ScriptedModel(
        responses=[calls(("write_file", {"path": "a.txt", "content": "approved"}))]
    )
    with AgentSession(settings, model) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Write a file")]}, session.config
        )
        assert "__interrupt__" in result
        assert not (tmp_path / "a.txt").exists()
        session_id = session.session_id
    with AgentSession(
        settings, ScriptedModel(responses=[AIMessage(content="done")]), resume=session_id
    ) as session:
        session.graph.invoke(Command(resume=True), session.config)
        assert (tmp_path / "a.txt").read_text() == "approved"
        assert session.snapshots.undo() == "Undid a.txt"


def test_plan_mode_denies_write_and_continues(tmp_path):
    model = ScriptedModel(
        responses=[
            calls(("write_file", {"path": "blocked.txt", "content": "bad"})),
            AIMessage(content="Read-only plan"),
        ]
    )
    with AgentSession(
        Settings(workspace=tmp_path, permission=PermissionMode.PLAN), model
    ) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Plan only")]}, session.config
        )
        assert not (tmp_path / "blocked.txt").exists()
        assert result["messages"][-2].status == "error"
        assert "denied" in result["messages"][-2].content


def test_tool_error_recovery(tmp_path):
    model = ScriptedModel(
        responses=[
            calls(("read_file", {"path": "missing.py"})),
            calls(("write_file", {"path": "new.py", "content": "x = 1\n"})),
            AIMessage(content="Recovered"),
        ]
    )
    with AgentSession(Settings(workspace=tmp_path), model) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Create file")]}, session.config
        )
        assert any(m.status == "error" for m in result["messages"] if isinstance(m, ToolMessage))
        assert (tmp_path / "new.py").exists()


def test_plan_state_and_history_rewind(tmp_path):
    plan = [{"step": "Inspect", "status": "completed"}, {"step": "Fix", "status": "pending"}]
    model = ScriptedModel(
        responses=[calls(("update_plan", {"steps": plan})), AIMessage(content="planned")]
    )
    with AgentSession(Settings(workspace=tmp_path), model) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Plan task")]}, session.config
        )
        assert result["plan"] == plan
        checkpoints = list_checkpoints(session.graph, session.config)
        assert len(checkpoints) >= 4
        config = rewind(session.graph, session.config, checkpoints[0]["id"])
        assert "checkpoint_id" not in config["configurable"]


def test_cli_checkpoint_can_resume_in_fresh_process(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "soranimi", "--demo", "--workspace", str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    import re

    session_id = re.search(r"session: ([0-9a-f]+)", result.stdout).group(1)
    resumed = subprocess.run(
        [
            sys.executable,
            "-m",
            "soranimi",
            "--demo",
            "--workspace",
            str(tmp_path),
            "--resume",
            session_id,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
    )
    assert resumed.returncode == 0, resumed.stderr + resumed.stdout
    assert session_id in resumed.stdout
    assert (tmp_path / ".soranimi/checkpoints.db").exists()


def test_multifile_batch_and_persistent_read_tracking(tmp_path):
    for name in ["a.py", "b.py"]:
        (tmp_path / name).write_text("answer = 0\n")
    settings = Settings(workspace=tmp_path)
    model = ScriptedModel(
        responses=[
            calls(("read_file", {"path": "a.py"}), ("read_file", {"path": "b.py"})),
            AIMessage(content="Read both"),
        ]
    )
    with AgentSession(settings, model) as session:
        session.graph.invoke({"messages": [HumanMessage(content="Inspect files")]}, session.config)
        session_id = session.session_id
    model = ScriptedModel(
        responses=[
            calls(
                *[
                    (
                        "edit_file",
                        {"path": name, "old_string": "answer = 0", "new_string": "answer = 42"},
                    )
                    for name in ["a.py", "b.py"]
                ]
            ),
            AIMessage(content="Updated both"),
        ]
    )
    with AgentSession(settings, model, resume=session_id) as session:
        session.graph.invoke({"messages": [HumanMessage(content="Update both")]}, session.config)
        assert all((tmp_path / name).read_text() == "answer = 42\n" for name in ["a.py", "b.py"])
