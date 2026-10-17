"""Real HTTP, SSE and checkpoint workflows for the local browser workbench."""

import json
import threading
import time

import pytest
from conftest import ScriptedModel, calls
from langchain_core.messages import AIMessage

from soranimi.config import Settings

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from soranimi.web.app import create_app
from soranimi.web.service import BusyError, Workbench

HEADERS = {"X-SoraNimi-Request": "1"}


def client_for(tmp_path, model=None):
    app = create_app(
        Settings(workspace=tmp_path),
        demo=True,
        model_factory=(lambda settings: model) if model else None,
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/").status_code == 200
    return client


def new_session(client, **options):
    response = client.post("/api/sessions", headers=HEADERS, json=options)
    assert response.status_code == 201, response.text
    return response.json()["session"]["id"]


def run_http(client, session_id, **body):
    response = client.post(f"/api/sessions/{session_id}/run", headers=HEADERS, json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    events = client.get(f"/api/jobs/{job_id}/events", headers=HEADERS)
    assert events.status_code == 200
    packets = []
    for packet in events.text.split("\n\n"):
        lines = packet.splitlines()
        if len(lines) >= 3 and lines[1].startswith("event:"):
            packets.append(
                {"id": int(lines[0][4:]), "event": lines[1][7:], "data": json.loads(lines[2][6:])}
            )
    return job_id, packets


def wait_job(job):
    deadline = time.monotonic() + 15
    cursor = 0
    while not job.done and time.monotonic() < deadline:
        events, _ = job.wait(cursor)
        if events:
            cursor = events[-1]["id"]
    assert job.done
    return list(job.events)


def test_static_assets_and_local_access_boundary(tmp_path):
    app = create_app(Settings(workspace=tmp_path), demo=True)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/bootstrap", headers=HEADERS).status_code == 403
    response = client.get("/")
    assert "把想法" in response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/api/bootstrap").status_code == 403
    assert (
        client.get(
            "/api/bootstrap", headers={**HEADERS, "Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert client.get("/", headers={"Host": "rebind.example"}).status_code == 400
    for name in [
        "app.js",
        "app.css",
        "markdown.js",
        "logo.svg",
        "typography.css",
        "nimi-avatar.jpg",
    ]:
        assert client.get(f"/assets/{name}").status_code == 200


def test_demo_chat_sse_history_and_replay(tmp_path):
    with client_for(tmp_path) as client:
        session_id = new_session(client)
        job_id, events = run_http(client, session_id, message="探索这个项目")
        assert not [event for event in events if event["event"] == "error"], events
        assert any(
            event["event"] == "tool" and event["data"]["name"] == "list_dir" for event in events
        )
        assert any(event["event"] == "assistant" for event in events)
        final = next(event["data"]["state"] for event in events if event["event"] == "complete")
        assert final["messages"][0]["text"] == "探索这个项目"
        assert not final["running"] and not final["pending"]
        assert (
            client.get(f"/api/sessions/{session_id}", headers=HEADERS).json()["messages"]
            == final["messages"]
        )
        last = events[-2]["id"]
        replay = client.get(f"/api/jobs/{job_id}/events?after={last}", headers=HEADERS)
        assert "event: complete" in replay.text and "event: token" not in replay.text
        assert client.get("/api/sessions", headers=HEADERS).json()[0]["title"] == "探索这个项目"


def test_web_worker_uses_configured_summary_model(tmp_path):
    main = ScriptedModel(responses=[AIMessage(content="Continue from summary")])
    summary = ScriptedModel(responses=[AIMessage(content="Preserve original task and paths")])
    seen = []

    def factory(settings):
        seen.append(settings.model)
        return summary if settings.model == "openai:summary" else main

    service = Workbench(
        Settings(
            workspace=tmp_path,
            model="openai:main",
            summary_model="openai:summary",
            context_tokens=1000,
        ),
        model_factory=factory,
    )
    session_id = service.create()["session"]["id"]
    assert not seen  # Browsing sessions does not create either model client.
    packets = wait_job(service.start(session_id, "Original task " * 2000))
    assert packets[-1]["event"] == "complete", packets
    assert seen == ["openai:main", "openai:summary"]
    assert "Preserve original task" in packets[-1]["data"]["state"]["messages"][0]["text"]
    assert main.cursor == 1 and summary.cursor == 1


@pytest.mark.parametrize("approved", [True, False])
def test_browser_approval_before_side_effect(tmp_path, approved):
    model = ScriptedModel(
        responses=[
            calls(("write_file", {"path": "approved.txt", "content": "allowed"})),
            AIMessage(content="Done"),
        ]
    )
    with client_for(tmp_path, model) as client:
        session_id = new_session(client, permission="manual")
        _, packets = run_http(client, session_id, message="创建文件")
        view = packets[-1]["data"]["state"]
        assert len(view["pending"]) == 1
        assert not (tmp_path / "approved.txt").exists()
        bad = client.post(
            f"/api/sessions/{session_id}/run",
            headers=HEADERS,
            json={"approvals": {"stale-id": True}},
        )
        assert bad.status_code == 400
        _, packets = run_http(client, session_id, approvals={view["pending"][0]["id"]: approved})
        assert packets[-1]["event"] == "complete", packets
        assert (tmp_path / "approved.txt").exists() == approved
        assert not packets[-1]["data"]["state"]["pending"]


def test_changes_preview_and_undo(tmp_path):
    model = ScriptedModel(
        responses=[
            calls(("write_file", {"path": "new.py", "content": "answer = 42\n"})),
            AIMessage(content="Created"),
        ]
    )
    with client_for(tmp_path, model) as client:
        session_id = new_session(client)
        _, packets = run_http(client, session_id, message="创建新文件")
        view = packets[-1]["data"]["state"]
        assert view["changes"][0]["path"] == "new.py"
        assert view["changes"][0]["added"] == 1
        assert "+answer = 42" in view["changes"][0]["diff"]
        assert (
            client.get("/api/file?path=new.py", headers=HEADERS).json()["content"]
            == "answer = 42\n"
        )
        undone = client.post(f"/api/sessions/{session_id}/undo", headers=HEADERS)
        assert undone.status_code == 200, undone.text
        assert not (tmp_path / "new.py").exists()
        assert not undone.json()["state"]["changes"]


def test_file_scope_and_ignore_rules(tmp_path):
    (tmp_path / "visible.py").write_text("x = 1\n")
    (tmp_path / ".env").write_text("SECRET=not-for-listing")
    (tmp_path / ".gitignore").write_text("skip.txt\n")
    (tmp_path / "skip.txt").write_text("hidden")
    with client_for(tmp_path) as client:
        files = client.get("/api/files", headers=HEADERS).json()["files"]
        paths = [item["path"] for item in files]
        assert "visible.py" in paths and ".env" not in paths and "skip.txt" not in paths
        assert not any(".soranimi" in path for path in paths)
        for path in ["../outside", ".git/config", ".soranimi/sessions.db"]:
            assert (
                client.get("/api/file", params={"path": path}, headers=HEADERS).status_code == 400
            )


def test_parallel_runs_are_rejected_and_stop_is_cooperative(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def factory(settings):
        started.set()
        release.wait(timeout=10)
        from soranimi.agent.demo import DemoModel

        return DemoModel()

    service = Workbench(Settings(workspace=tmp_path), demo=True, model_factory=factory)
    session_id = service.create()["session"]["id"]
    job = service.start(session_id, "inspect")
    assert started.wait(timeout=5)
    with pytest.raises(BusyError):
        service.start(session_id, "another")
    job.stop.set()
    release.set()
    events = wait_job(job)
    assert events[-1]["event"] == "complete", events
    assert events[-1]["data"]["stopped"]
    state = events[-1]["data"]["state"]
    assert state["resumable"]
    continued = service.start(session_id)
    events = wait_job(continued)
    assert events[-1]["event"] == "complete", events
    assert not events[-1]["data"]["state"]["resumable"]


def test_session_policy_survives_workbench_restart(tmp_path):
    settings = Settings(workspace=tmp_path)
    first = Workbench(settings, demo=True)
    session_id = first.create(model="openai:test", permission="plan", sandbox="docker")["session"][
        "id"
    ]
    second = Workbench(settings, demo=True)
    actual = second.session_settings(session_id)
    assert (
        actual.model == "openai:test"
        and actual.permission.value == "plan"
        and actual.sandbox == "docker"
    )


def test_pausing_before_tool_never_approves_the_tool_on_resume(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingModel(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.cursor == 0:
                started.set()
                release.wait(timeout=10)
            return super()._generate(messages, stop, run_manager, **kwargs)

    model = BlockingModel(
        responses=[
            calls(("write_file", {"path": "must-approve.txt", "content": "protected"})),
            AIMessage(content="Done"),
        ]
    )
    settings = Settings(workspace=tmp_path)
    first = Workbench(settings, model_factory=lambda settings: model)
    session_id = first.create(permission="manual")["session"]["id"]
    job = first.start(session_id, "write")
    assert started.wait(timeout=5)
    job.stop.set()
    release.set()
    state = wait_job(job)[-1]["data"]["state"]
    assert state["paused"] and not state["pending"]
    assert not (tmp_path / "must-approve.txt").exists()
    # A fresh service simulates restarting the server before continuing.
    second = Workbench(settings, model_factory=lambda settings: model)
    resumed = second.start(session_id)
    state = wait_job(resumed)[-1]["data"]["state"]
    assert not state["paused"] and state["pending"]
    assert not (tmp_path / "must-approve.txt").exists()
    denied = second.start(session_id, approvals={state["pending"][0]["id"]: False})
    assert wait_job(denied)[-1]["event"] == "complete"
    assert not (tmp_path / "must-approve.txt").exists()


def test_model_failure_becomes_replayable_error_without_stuck_lock(tmp_path):
    def unavailable(settings):
        raise ValueError("Set provider API key")

    service = Workbench(Settings(workspace=tmp_path), model_factory=unavailable)
    session_id = service.create()["session"]["id"]
    events = wait_job(service.start(session_id, "task"))
    assert events[-1]["event"] == "error"
    assert "API key" in events[-1]["data"]["message"]
    assert service.lock.acquire(blocking=False)
    service.lock.release()


def test_imported_cli_approval_cannot_be_silently_allowed_by_ui(tmp_path):
    from langchain_core.messages import HumanMessage

    from soranimi.runtime import AgentSession

    model = ScriptedModel(
        responses=[
            calls(("write_file", {"path": "cli.txt", "content": "must confirm"})),
            AIMessage(content="Denied"),
        ]
    )
    with AgentSession(Settings(workspace=tmp_path, permission="manual"), model) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Write file")]}, session.config
        )
        assert "__interrupt__" in result
        session_id = session.session_id
    service = Workbench(Settings(workspace=tmp_path), model_factory=lambda settings: model)
    view = service.inspect(session_id)
    assert view["session"]["permission"] == "manual"
    job = service.start(session_id, approvals={view["pending"][0]["id"]: False})
    assert wait_job(job)[-1]["event"] == "complete"
    assert not (tmp_path / "cli.txt").exists()
