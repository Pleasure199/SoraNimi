import os
import shutil
import subprocess

import pytest

from soranimi.sandbox.docker_exec import DockerExecutor
from soranimi.sandbox.executor import ExecutionResult


def test_docker_flags_mask_custom_state_and_cleanup(tmp_path, monkeypatch):
    state = tmp_path / "private-state"
    state.mkdir()
    (tmp_path / ".git").mkdir()
    executor = DockerExecutor(tmp_path, data_dir=state)
    captured = []
    cleanup = []

    def run(args, timeout):
        captured.extend(args)
        return ExecutionResult("ok", "", 0)

    monkeypatch.setattr(executor, "run", run)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: cleanup.append(args))
    assert executor.execute("echo ok").exit_code == 0
    assert "--network=none" in captured and "--read-only" in captured
    assert "--cap-drop=ALL" in captured
    assert "/workspace/private-state:ro,nosuid,nodev,noexec" in captured
    assert "/workspace/.git:ro,nosuid,nodev,noexec" in captured
    assert cleanup[0][:3] == ["docker", "rm", "-f"]


def test_git_commit_excludes_unrelated_staged_files(session, tools):
    if not shutil.which("git"):
        pytest.skip("Git is not installed")
    root = session.settings.workspace

    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)

    git("init")
    git("config", "user.name", "SoraNimi Test")
    git("config", "user.email", "soranimi-test@example.invalid")
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")
    git("add", "a.txt", "b.txt")
    git("commit", "-m", "initial")
    (root / "a.txt").write_text("agent")
    (root / "b.txt").write_text("user")
    git("add", "b.txt")
    result = tools["git_commit"].invoke({"message": "agent change", "paths": ["a.txt"]})
    assert result["exit_code"] == 0, result
    assert git("show", "HEAD:b.txt").stdout == "b"
    assert "b.txt" in git("diff", "--cached", "--name-only").stdout


def test_grep_without_ripgrep(session, tools, monkeypatch):
    (session.settings.workspace / "fallback.py").write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert "fallback.py:1:needle" in tools["grep"].invoke({"pattern": "needle"})


def test_subprocess_does_not_inherit_model_credentials(session, tools, monkeypatch):
    monkeypatch.setenv("SORANIMI_API_KEY", "test-secret-soranimi")
    command = (
        "Write-Output $env:SORANIMI_API_KEY"
        if os.name == "nt"
        else "printf '%s' \"$SORANIMI_API_KEY\""
    )
    result = tools["bash"].invoke({"command": command})
    assert "test-secret-soranimi" not in result["stdout"]
