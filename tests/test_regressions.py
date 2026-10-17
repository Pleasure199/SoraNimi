import shutil
import subprocess

import pytest

from soranimi.persistence.store import MemoryStore
from soranimi.subagent.plan_execute import parallel_research


@pytest.mark.parametrize(
    "original,old,new,expected",
    [
        (b"first\r\nchange\nlast\r\n", "change", "updated", b"first\r\nupdated\nlast\r\n"),
        (b"first\nchange\r\nlast\n", "change", "one\ntwo", b"first\none\r\ntwo\r\nlast\n"),
        (b"first\rchange\rlast\r", "change\nlast", "updated\nend", b"first\rupdated\rend\r"),
    ],
)
def test_local_edit_preserves_unrelated_line_endings(session, tools, original, old, new, expected):
    target = session.settings.workspace / "mixed.txt"
    target.write_bytes(original)
    tools["read_file"].invoke({"path": "mixed.txt"})
    tools["edit_file"].invoke({"path": "mixed.txt", "old_string": old, "new_string": new})
    assert target.read_bytes() == expected
    session.snapshots.undo()
    assert target.read_bytes() == original


@pytest.mark.parametrize("encoding,bom", [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")])
def test_snapshot_diff_decodes_utf16(session, tools, encoding, bom):
    target = session.settings.workspace / "unicode.txt"
    target.write_bytes(bom + "你好\n旧内容\n".encode(encoding))
    tools["read_file"].invoke({"path": "unicode.txt"})
    tools["edit_file"].invoke(
        {"path": "unicode.txt", "old_string": "旧内容", "new_string": "新内容"}
    )
    diff = session.snapshots.diff()
    assert "-旧内容" in diff and "+新内容" in diff
    assert "\x00" not in diff and "\ufffd" not in diff


def test_memory_search_filters_before_applying_result_limit(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    try:
        for index in range(105):
            store.put("project", f"key-{index:03}", {"text": "ordinary"})
        store.put("project", "zzz", {"text": "STRASSE preference"})
        assert store.search("project", "straße") == [
            {"key": "zzz", "value": {"text": "STRASSE preference"}}
        ]
        assert len(store.search("project")) == 100
    finally:
        store.close()


def init_git(root):
    if not shutil.which("git"):
        pytest.skip("Git is not installed")

    def git(*args):
        return subprocess.run(
            ["git", "--literal-pathspecs", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    git("init")
    git("config", "user.name", "Regression Test")
    git("config", "user.email", "test@example.invalid")
    for name in ["note[1].txt", "note1.txt"]:
        (root / name).write_text("original", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "initial")
    for name in ["note[1].txt", "note1.txt"]:
        (root / name).write_text("changed", encoding="utf-8")
    return git


def test_git_commit_treats_selected_filename_literally(session, tools):
    git = init_git(session.settings.workspace)
    git("add", "note1.txt")
    result = tools["git_commit"].invoke({"message": "selected change", "paths": ["note[1].txt"]})
    assert result["exit_code"] == 0, result
    assert git("show", "HEAD:note[1].txt") == "changed"
    assert git("show", "HEAD:note1.txt") == "original"
    assert git("diff", "--cached", "--name-only").strip() == "note1.txt"


def test_git_commit_rejects_directory_selection(session, tools):
    git = init_git(session.settings.workspace)
    head = git("rev-parse", "HEAD")
    with pytest.raises(ValueError, match="file"):
        tools["git_commit"].invoke({"message": "too broad", "paths": ["."]})
    assert git("rev-parse", "HEAD") == head


def test_parallel_worker_failure_preserves_successful_results():
    def worker(task):
        if task == "broken":
            raise KeyError("missing provider response")
        return {"task": task, "summary": "found"}

    results = parallel_research(["good", "broken"], worker, max_parallel=2)
    assert any(result.get("summary") == "found" for result in results)
    assert any("missing provider response" in result.get("error", "") for result in results)


def test_checkpoint_list_agrees_with_rewind_after_manual_compaction(tmp_path):
    import io

    from conftest import ScriptedModel
    from langchain_core.messages import AIMessage, HumanMessage
    from rich.console import Console

    from soranimi.cli.app import handle_slash
    from soranimi.cli.renderer import Renderer
    from soranimi.config import Settings
    from soranimi.persistence.time_travel import list_checkpoints, rewind
    from soranimi.runtime import AgentSession

    model = ScriptedModel(responses=[AIMessage(content="Done"), AIMessage(content="Summary")])
    with AgentSession(Settings(workspace=tmp_path), model) as session:
        session.graph.invoke({"messages": [HumanMessage(content="Inspect")]}, session.config)
        handle_slash("/compact", session, Renderer(Console(file=io.StringIO())))
        checkpoints = list_checkpoints(session.graph, session.config)
        assert not checkpoints[0]["rewindable"]  # Summary-only state is not a completed AI turn.
        completed = next(item for item in checkpoints if item["rewindable"])
        rewind(session.graph, session.config, completed["id"])


@pytest.mark.parametrize("directory", [".GIT", ".SoraNimi"])
def test_file_enumeration_excludes_case_variants_of_protected_directories(
    session, tools, monkeypatch, directory
):
    protected = session.settings.workspace / directory
    protected.mkdir(exist_ok=True)
    (protected / "private.txt").write_text("protected-needle", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert "private.txt" not in tools["glob"].invoke({"pattern": "**/*"})
    assert "protected-needle" not in tools["grep"].invoke({"pattern": "protected-needle"})
