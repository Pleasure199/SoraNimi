import io

from conftest import ScriptedModel, calls
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from rich.console import Console
from typer.testing import CliRunner

from soranimi.cli.app import app, handle_slash, run_turn
from soranimi.cli.renderer import Renderer
from soranimi.config import Settings
from soranimi.runtime import AgentSession
from soranimi.streaming.event_consumer import consume_events


def test_help_version_invalid_settings(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert "0.3.0" in runner.invoke(app, ["--version"]).stdout
    result = runner.invoke(app, ["--demo", "--workspace", str(tmp_path), "--sandbox", "invalid"])
    assert result.exit_code == 1
    assert "sandbox must be" in result.stdout


def test_missing_key_exits_with_helpful_error(tmp_path, monkeypatch):
    for key in ["SORANIMI_API_KEY", "ANTHROPIC_API_KEY"]:
        monkeypatch.delenv(key, raising=False)
    result = CliRunner().invoke(
        app, ["task", "--workspace", str(tmp_path), "--model", "anthropic:test-model"]
    )
    assert result.exit_code == 1
    assert "Set ANTHROPIC_API_KEY" in result.stdout


def test_ui_flag_reuses_workspace_without_calling_cli_model(tmp_path, monkeypatch):
    from soranimi.web import launcher

    seen = []
    monkeypatch.setattr(
        launcher, "serve", lambda settings, demo, port: seen.append((settings, demo, port))
    )
    result = CliRunner().invoke(
        app, ["--ui", "--demo", "--port", "8899", "--workspace", str(tmp_path)]
    )
    assert result.exit_code == 0, result.stdout
    assert seen[0][0].workspace == tmp_path.resolve()
    assert seen[0][1:] == (True, 8899)


def test_summary_options_reach_ui_settings(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "soranimi.web.launcher.serve", lambda settings, demo, port: seen.append(settings)
    )
    result = CliRunner().invoke(
        app,
        [
            "--ui",
            "--workspace",
            str(tmp_path),
            "--summary-model",
            "openai:small",
            "--summary-base-url",
            "http://summary/v1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert seen[0].summary_model == "openai:small"
    assert seen[0].summary_base_url == "http://summary/v1"


def test_standalone_ui_accepts_summary_options(tmp_path, monkeypatch):
    from soranimi.web import launcher

    seen = []
    monkeypatch.setattr(launcher, "serve", lambda settings, demo, port: seen.append(settings))
    result = CliRunner().invoke(
        launcher.app,
        [
            "--workspace",
            str(tmp_path),
            "--summary-model",
            "openai:small",
            "--summary-base-url",
            "http://summary/v1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert seen[0].summary_model == "openai:small"
    assert seen[0].summary_base_url == "http://summary/v1"


def test_noninteractive_approval_remains_pending(tmp_path):
    model = ScriptedModel(responses=[calls(("bash", {"command": "echo approval"}))])
    with AgentSession(Settings(workspace=tmp_path), model) as session:
        renderer = Renderer(Console(file=io.StringIO()))
        completed = run_turn(
            session, {"messages": [HumanMessage(content="Execute")]}, renderer, non_interactive=True
        )
        assert not completed
        assert session.graph.get_state(session.config).next == ("tools",)


def test_cli_pending_approval_returns_exit_two(tmp_path, monkeypatch):
    model = ScriptedModel(responses=[calls(("bash", {"command": "echo approval"}))])
    monkeypatch.setattr("soranimi.cli.app.create_model", lambda settings: model)
    result = CliRunner().invoke(
        app, ["Run command", "--workspace", str(tmp_path), "--non-interactive"]
    )
    assert result.exit_code == 2, result.stdout


def test_streaming_does_not_print_tool_json_or_duplicate_text():
    output = io.StringIO()
    renderer = Renderer(Console(file=output))
    events = [
        ("messages", (AIMessageChunk(content="Hello "), {"langgraph_node": "llm_call"})),
        (
            "messages",
            (
                AIMessageChunk(
                    content="world",
                    tool_call_chunks=[
                        {"name": "read_file", "args": '{"path":"a.py"}', "id": "x", "index": 0}
                    ],
                ),
                {"langgraph_node": "llm_call"},
            ),
        ),
        (
            "messages",
            (AIMessageChunk(content="hidden summary"), {"langgraph_node": "context_check"}),
        ),
        ("updates", {"llm_call": {"messages": [AIMessage(content="Hello world")]}}),
    ]
    consume_events(events, renderer)
    text = output.getvalue()
    assert text.count("Hello world") == 1
    assert "path" not in text and "hidden summary" not in text


def test_slash_commands_show_changes_and_undo(session, tools):
    output = io.StringIO()
    renderer = Renderer(Console(file=output))
    tools["write_file"].invoke({"path": "slash.txt", "content": "created\n"})
    assert handle_slash("/diff", session, renderer)
    assert "+created" in output.getvalue()
    assert handle_slash("/undo", session, renderer)
    assert not (session.settings.workspace / "slash.txt").exists()
    assert not handle_slash("/exit", session, renderer)
