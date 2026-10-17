import base64
import os
from pathlib import Path

import pytest

from soranimi.tools.edit_pipeline import replace_unique


def test_read_edit_preserves_crlf_bom(session, tools):
    target = session.settings.workspace / "sample.py"
    original = b"\xef\xbb\xbfdef add(a, b):\r\n    return a - b\r\n"
    target.write_bytes(original)
    result = tools["read_file"].invoke({"path": "sample.py"})
    assert "1: def add" in result[0]["text"]
    tools["edit_file"].invoke(
        {"path": "sample.py", "old_string": "return a - b", "new_string": "return a + b"}
    )
    assert target.read_bytes() == original.replace(b"a - b", b"a + b")
    assert "return a + b" in session.snapshots.diff()
    assert session.snapshots.undo() == "Undid sample.py"
    assert target.read_bytes() == original


def test_partial_or_truncated_read_does_not_authorize_edit(session, tools):
    target = session.settings.workspace / "large.txt"
    target.write_text("first\nsecond\n", encoding="utf-8")
    tools["read_file"].invoke({"path": "large.txt", "limit": 1})
    with pytest.raises(ValueError, match="complete current"):
        tools["edit_file"].invoke(
            {"path": "large.txt", "old_string": "first", "new_string": "other"}
        )
    target.write_text("x" * 5000, encoding="utf-8")
    tools["read_file"].invoke({"path": "large.txt"})
    with pytest.raises(ValueError, match="complete current"):
        tools["write_file"].invoke({"path": "large.txt", "content": "lost"})


def test_external_change_rejected(session, tools):
    target = session.settings.workspace / "a.txt"
    target.write_text("original", encoding="utf-8")
    tools["read_file"].invoke({"path": "a.txt"})
    target.write_text("user edit", encoding="utf-8")
    with pytest.raises(ValueError, match="complete current"):
        tools["edit_file"].invoke(
            {"path": "a.txt", "old_string": "original", "new_string": "agent edit"}
        )
    assert target.read_text() == "user edit"


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", ".git/config", ".soranimi/sessions.db", ".GIT/config", ".SoraNimi/private"],
)
def test_workspace_boundary(tools, path):
    with pytest.raises(ValueError):
        tools["write_file"].invoke({"path": path, "content": "bad"})


def test_symlink_cannot_escape(session, tools, tmp_path):
    link = session.settings.workspace / "link"
    try:
        link.symlink_to(Path(tmp_path).parent, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows Developer Mode or administrator")
    with pytest.raises(ValueError, match="escapes"):
        tools["write_file"].invoke({"path": "link/outside.txt", "content": "bad"})


def test_create_undo_external_conflict(session, tools):
    tools["write_file"].invoke({"path": "nested/new.txt", "content": "new"})
    target = session.settings.workspace / "nested/new.txt"
    target.write_text("user", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        session.snapshots.undo()
    target.write_text("new", encoding="utf-8")
    session.snapshots.undo()
    assert not target.exists()


@pytest.mark.parametrize(
    "content,old,new,expected",
    [
        ("a  = 1\n", "a = 1", "a = 2", "a = 2\n"),
        ('value = "a"\n', "value = “a”", 'value = "b"', 'value = "b"\n'),
        ("first\nsecond\n", "second\n", "", "first\n"),
    ],
)
def test_replacement_fallbacks(content, old, new, expected):
    assert replace_unique(content, old, new) == expected


def test_ambiguous_edit_rejected():
    with pytest.raises(ValueError, match="2 times"):
        replace_unique("abc abc", "abc", "xyz")
    with pytest.raises(ValueError, match="0 times"):
        replace_unique("abc", "missing", "xyz")


def test_utf16_round_trip(session, tools):
    target = session.settings.workspace / "utf16.txt"
    target.write_bytes("你好\r\nworld\r\n".encode("utf-16"))
    tools["read_file"].invoke({"path": "utf16.txt"})
    tools["edit_file"].invoke({"path": "utf16.txt", "old_string": "world", "new_string": "世界"})
    assert target.read_bytes().decode("utf-16") == "你好\r\n世界\r\n"


def test_utf16_big_endian_is_preserved(session, tools):
    target = session.settings.workspace / "utf16be.txt"
    target.write_bytes(b"\xfe\xff" + "你好\nworld\n".encode("utf-16-be"))
    tools["read_file"].invoke({"path": "utf16be.txt"})
    tools["edit_file"].invoke({"path": "utf16be.txt", "old_string": "world", "new_string": "世界"})
    assert target.read_bytes() == b"\xfe\xff" + "你好\n世界\n".encode("utf-16-be")


def test_glob_grep_ignore_and_output_modes(session, tools):
    root = session.settings.workspace
    (root / ".gitignore").write_text("ignored/\n*.log\n", encoding="utf-8")
    for relative in ["root.py", "src/keep.py", "ignored/no.py", "src/drop.log"]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("needle\nother\nneedle\n", encoding="utf-8")
    found = tools["glob"].invoke({"pattern": "**/*.py"})
    assert "root.py" in found and "src/keep.py" in found and "no.py" not in found
    result = tools["grep"].invoke({"pattern": "needle"})
    assert "root.py:1:needle" in result
    assert "src/keep.py:3:needle" in result
    assert "no.py" not in result and "drop.log" not in result
    assert "root.py:2" in tools["grep"].invoke({"pattern": "needle", "output_mode": "count"})
    files = tools["grep"].invoke({"pattern": "needle", "output_mode": "files"})
    assert files.count("root.py") == 1
    assert "src/keep.py" in tools["glob"].invoke({"path": "src", "pattern": "*.py"})


def test_nested_ignore_negation(session, tools):
    root = session.settings.workspace
    (root / "src").mkdir()
    (root / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    (root / "src/.gitignore").write_text("!keep.tmp\n", encoding="utf-8")
    (root / "src/keep.tmp").write_text("ok")
    (root / "src/no.tmp").write_text("no")
    found = tools["glob"].invoke({"pattern": "**/*"})
    assert "keep.tmp" in found and "no.tmp" not in found


def test_grep_reports_truncation_and_counts_all_matching_lines(session, tools):
    (session.settings.workspace / "many.txt").write_text("needle\n" * 700, encoding="utf-8")
    result = tools["grep"].invoke({"pattern": "needle"})
    assert "[truncated]" in result
    assert "many.txt:700" in tools["grep"].invoke({"pattern": "needle", "output_mode": "count"})


def test_image_read_returns_content_block(session, tools):
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZYkAAAAASUVORK5CYII="
    )
    (session.settings.workspace / "pixel.png").write_bytes(data)
    result = tools["read_file"].invoke({"path": "pixel.png"})
    assert result[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_shell_output_timeout_and_limit(session, tools):
    command = "Write-Output 'hello'" if os.name == "nt" else "printf hello"
    result = tools["bash"].invoke({"command": command})
    assert result["exit_code"] == 0 and "hello" in result["stdout"]
    command = "Start-Sleep -Seconds 10" if os.name == "nt" else "sleep 10"
    result = tools["bash"].invoke({"command": command, "timeout": 1})
    assert result["exit_code"] == 124 and result["timed_out"]
    command = "Write-Output ('a' * 30000)" if os.name == "nt" else "head -c 30000 /dev/zero"
    result = tools["bash"].invoke({"command": command})
    assert "[truncated]" in result["stdout"]
