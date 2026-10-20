import json
import os
import sqlite3
from dataclasses import replace

import pytest
from conftest import ScriptedModel, calls
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from typer.testing import CliRunner

from soranimi.agent.model_factory import SummaryModel, create_model
from soranimi.cli.app import app
from soranimi.config import Settings
from soranimi.runtime import AgentSession
from soranimi.web import launcher
from soranimi.web.service import Workbench

PROFILES = {
    "main": {
        "model": "openai:main",
        "base_url": "http://main.invalid/v1",
        "api_key_env": "MAIN_CREDENTIAL",
    },
    "research": {
        "model": "openai:research",
        "base_url": "http://research.invalid/v1",
        "api_key_env": "RESEARCH_CREDENTIAL",
    },
    "review": {"model": "anthropic:review", "api_key_env": "REVIEW_CREDENTIAL"},
}


def configured(tmp_path, **options):
    return Settings(workspace=tmp_path, model="main", model_profiles=PROFILES, **options)


def test_profiles_load_relative_to_workspace_and_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    path.write_text(json.dumps(PROFILES))
    monkeypatch.setenv("SORANIMI_MODELS_CONFIG", "models.json")
    monkeypatch.setenv("SORANIMI_SUBAGENT_MODEL", "research")
    settings = Settings(workspace=tmp_path, model="main")
    assert settings.models_config == path
    assert settings.model_profiles == PROFILES
    assert settings.subagent_model == "research"
    assert replace(settings, model="review").model_profiles == PROFILES


@pytest.mark.parametrize(
    "profiles",
    [
        [],
        {"bad": {}},
        {"bad": {"model": "unknown:x"}},
        {"bad": {"model": "openai:x", "api_key": "do-not-store"}},
        {"bad": {"model": "openai:x", "api_key_env": 42}},
        {"bad:name": {"model": "openai:x"}},
    ],
)
def test_invalid_profiles_fail_without_exposing_values(tmp_path, profiles):
    with pytest.raises(ValueError) as caught:
        Settings(workspace=tmp_path, model_profiles=profiles)
    assert "do-not-store" not in str(caught.value)


def test_missing_file_and_unknown_alias_fail_early(tmp_path):
    with pytest.raises(ValueError, match="Cannot read"):
        Settings(workspace=tmp_path, models_config=tmp_path / "missing.json")
    with pytest.raises(ValueError, match="Unknown model profile"):
        configured(tmp_path, subagent_model="typo")


@pytest.mark.parametrize(
    "alias,expected,url,key",
    [
        ("main", "main", "main.invalid", "MAIN_CREDENTIAL"),
        ("research", "research", "research.invalid", "RESEARCH_CREDENTIAL"),
        ("review", "review", "api.anthropic.com", "REVIEW_CREDENTIAL"),
    ],
)
def test_profiles_use_own_endpoint_and_credentials(
    tmp_path, monkeypatch, alias, expected, url, key
):
    monkeypatch.setenv(key, "profile-secret")
    monkeypatch.setenv("SORANIMI_API_KEY", "wrong-global-secret")
    monkeypatch.setenv("SORANIMI_BASE_URL", "http://wrong.invalid/v1")
    settings = replace(configured(tmp_path), model=alias)
    client = create_model(settings)
    assert client.model == expected if alias == "review" else client.model_name == expected
    assert (
        client.anthropic_api_key if alias == "review" else client.openai_api_key
    ).get_secret_value() == "profile-secret"
    assert url in str(
        client.anthropic_api_url if alias == "review" else client.root_client.base_url
    )


def test_profile_missing_key_does_not_fallback_to_another_account(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCH_CREDENTIAL", raising=False)
    monkeypatch.setenv("SORANIMI_API_KEY", "wrong-account")
    with pytest.raises(ValueError, match="Set RESEARCH_CREDENTIAL"):
        create_model(replace(configured(tmp_path), model="research"))


def test_summary_can_select_a_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_CREDENTIAL", "research-secret")
    client = SummaryModel(configured(tmp_path, summary_model="research"), None).client
    assert client.model_name == "research"
    assert "research.invalid" in str(client.root_client.base_url)


def test_profile_standard_provider_key_ignores_global_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "provider-secret")
    monkeypatch.setenv("SORANIMI_API_KEY", "wrong-account")
    monkeypatch.setenv("SORANIMI_BASE_URL", "http://wrong.invalid/v1")
    settings = Settings(
        workspace=tmp_path,
        model="default",
        model_profiles={"default": {"model": "deepseek:deepseek-chat"}},
    )
    client = create_model(settings)
    assert client.openai_api_key.get_secret_value() == "provider-secret"
    assert "api.deepseek.com" in str(client.root_client.base_url)


def test_subagent_default_override_parallel_and_readonly(tmp_path):
    seen = []

    def factory(selected):
        seen.append(selected)
        return ScriptedModel(
            responses=[
                calls(("write_file", {"path": "forbidden", "content": "bad"})),
                AIMessage(content=selected.model),
            ]
        )

    main = ScriptedModel(responses=[])
    with AgentSession(
        configured(tmp_path, subagents=True, subagent_model="research"), main, model_factory=factory
    ) as session:
        tools = {t.name: t for t in session.tools}
        assert not seen
        assert tools["spawn_subagent"].invoke({"task": "Inspect"})["summary"] == "research"
        assert (
            tools["spawn_subagent"].invoke({"task": "Review", "model": "review"})["summary"]
            == "review"
        )
        results = tools["parallel_subagents"].invoke({"tasks": ["A", "B"], "model": "review"})
        assert [r["summary"] for r in results] == ["review", "review"]
        assert not (tmp_path / "forbidden").exists()
        assert main.cursor == 0
        assert all(s.base_url is None for s in seen)
        assert [s.model for s in seen] == ["research", "review", "review", "review"]
        with pytest.raises(ValueError, match="configured model profile"):
            tools["spawn_subagent"].invoke({"task": "Inspect", "model": "openai:not-configured"})


def test_research_planner_keeps_main_model_and_selects_worker_profile(tmp_path):
    main = ScriptedModel(
        responses=[
            calls(("plan_and_execute", {"objective": "Inspect", "worker_model": "review"})),
            AIMessage(
                content=json.dumps({"tasks": ["Find entry"], "summary": "", "complete": False})
            ),
            AIMessage(content=json.dumps({"tasks": [], "summary": "Found", "complete": True})),
            AIMessage(content="Main done"),
        ]
    )
    seen = []

    def factory(settings):
        seen.append(settings.model)
        return ScriptedModel(responses=[AIMessage(content="Worker found main.py")])

    with AgentSession(
        configured(tmp_path, subagents=True, subagent_model="research"), main, model_factory=factory
    ) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Inspect")]}, session.config
        )
        report = json.loads(
            next(m.content for m in result["messages"] if isinstance(m, ToolMessage))
        )
        assert report["complete"] and seen == ["review"]
        assert result["messages"][-1].content == "Main done"


@pytest.mark.parametrize("entry,extra", [(app, ["--ui"]), (launcher.app, [])])
def test_cli_model_options_reach_both_launchers(tmp_path, monkeypatch, entry, extra):
    config = tmp_path / "models.json"
    config.write_text(json.dumps(PROFILES))
    seen = []
    monkeypatch.setattr(launcher, "serve", lambda settings, demo, port: seen.append(settings))
    result = CliRunner().invoke(
        entry,
        extra
        + [
            "--workspace",
            str(tmp_path),
            "--models-config",
            "models.json",
            "--model",
            "main",
            "--subagents",
            "--subagent-model",
            "research",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert seen[0].subagent_model == "research" and seen[0].model_profiles == PROFILES


def test_web_session_choices_survive_restart_and_can_be_cleared(tmp_path):
    settings = configured(tmp_path, subagents=True, subagent_model="research")
    service = Workbench(settings, demo=True)
    selected = service.create(model="review", subagent_model="main", subagents=True)["session"]
    session_id = selected["id"]
    assert selected["subagent_model"] == "main" and selected["subagents"]
    service.shutdown()
    restarted = Workbench(settings, demo=True)
    restored = restarted.session_settings(session_id)
    assert restored.model == "review" and restored.subagent_model == "main" and restored.subagents
    blank = restarted.create(subagent_model="", subagents=False)["session"]["id"]
    assert restarted.session_settings(blank).subagent_model is None
    assert not restarted.session_settings(blank).subagents
    restarted.shutdown()


def test_web_migrates_old_metadata(tmp_path):
    data_dir = tmp_path / ".soranimi"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "sessions.db") as db:
        db.execute(
            "CREATE TABLE web_sessions (id TEXT PRIMARY KEY, title TEXT, permission TEXT, sandbox TEXT)"
        )
    service = Workbench(configured(tmp_path), demo=True)
    assert service.create(subagent_model="research")["session"]["subagent_model"] == "research"
    service.shutdown()


def test_custom_credential_environment_not_inherited_by_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIN_CREDENTIAL", "must-not-leak")
    with AgentSession(configured(tmp_path), ScriptedModel(responses=[])) as session:
        tool = next(t for t in session.tools if t.name == "bash")
        command = (
            "Write-Output $env:MAIN_CREDENTIAL"
            if os.name == "nt"
            else "printf '%s' \"$MAIN_CREDENTIAL\""
        )
        assert "must-not-leak" not in tool.invoke({"command": command})["stdout"]


def test_custom_credentials_not_implicitly_inherited_by_mcp(tmp_path, monkeypatch):
    adapters = pytest.importorskip("langchain_mcp_adapters.client")
    from soranimi.mcp.client import MCPConnection

    observed = []

    class Client:
        def __init__(self, connections, **kwargs):
            observed.append(connections)

        async def get_tools(self):
            return []

    monkeypatch.setattr(adapters, "MultiServerMCPClient", Client)
    monkeypatch.setenv("MAIN_CREDENTIAL", "must-not-leak")
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"local": {"transport": "stdio", "command": "unused"}}))
    with MCPConnection(config, excluded_env=["MAIN_CREDENTIAL"]):
        assert "MAIN_CREDENTIAL" not in observed[0]["local"]["env"]
