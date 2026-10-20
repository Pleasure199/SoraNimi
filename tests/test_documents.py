import io
import subprocess
import zipfile
from types import SimpleNamespace

import pytest
from conftest import ScriptedModel, document_bytes
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from soranimi import documents
from soranimi.cli.app import app
from soranimi.config import Settings
from soranimi.documents import DocumentError, parse_document
from soranimi.permission.approval import decide
from soranimi.permission.modes import Decision, PermissionMode
from soranimi.persistence.attachments import AttachmentStore
from soranimi.runtime import AgentSession


@pytest.mark.parametrize("suffix", [".txt", ".pdf", ".docx", ".pptx"])
def test_real_document_text_extraction(suffix):
    parsed = parse_document(document_bytes(suffix), "资料" + suffix.upper())
    assert "Budget" in parsed["text"] and "42" in parsed["text"]
    assert not parsed["truncated"]
    if suffix == ".pdf":
        assert "[Page 1]" in parsed["text"] and parsed["pages"] == 1
    if suffix == ".docx":
        assert parsed["text"].index("Budget") < parsed["text"].index("After table")
    if suffix == ".pptx":
        assert "[Slide 1]" in parsed["text"] and "Speaker note" in parsed["text"]


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-be", "gb18030"])
def test_text_encodings(encoding):
    data = "中文资料".encode(encoding)
    if encoding == "utf-16-be":
        data = b"\xfe\xff" + data
    assert parse_document(data, "中文.txt")["text"] == "中文资料"


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "folder\\file.txt",
        "file\x00.txt",
        "file.exe",
        "old.doc",
        "old.ppt",
        "",
        "a" * 201 + ".txt",
    ],
)
def test_invalid_names_and_unsupported_formats(name):
    with pytest.raises(DocumentError):
        parse_document(b"example", name)


@pytest.mark.parametrize(
    "name,data",
    [
        ("empty.txt", b""),
        ("binary.txt", b"a\x00b"),
        ("bad.pdf", b"broken"),
        ("bad.docx", b"broken"),
        ("bad.pptx", b"broken"),
    ],
)
def test_empty_binary_and_corrupt_documents(name, data):
    with pytest.raises(DocumentError):
        parse_document(data, name)


def test_encrypted_and_scanned_pdf_errors():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(DocumentError, match="OCR"):
        parse_document(stream.getvalue(), "scan.pdf")
    writer.encrypt("secret")
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(DocumentError, match="加密"):
        parse_document(stream.getvalue(), "locked.pdf")


def test_office_xml_entities_are_rejected():
    original = document_bytes(".docx")
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(output, "w") as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename == "word/document.xml":
                content = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'.encode(
                    "utf-16"
                )
            target.writestr(item.filename, content)
    with pytest.raises(DocumentError, match="安全解析"):
        parse_document(output.getvalue(), "entity.docx")


def test_archive_and_page_count_limits(monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for i in range(3001):
            output.writestr(str(i), b"")
    with pytest.raises(DocumentError, match="过多条目"):
        documents.check_office_archive(archive.getvalue())
    monkeypatch.setattr(documents, "MAX_PAGES", 0)
    with pytest.raises(DocumentError, match="500 页"):
        documents.extract_binary(document_bytes(".pdf"), ".pdf")


def test_size_output_limits_and_long_line_paging(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "MAX_FILE_BYTES", 10)
    with pytest.raises(DocumentError, match="20 MiB"):
        parse_document(b"x" * 11, "big.txt")
    monkeypatch.setattr(documents, "MAX_FILE_BYTES", 20 * 1024 * 1024)
    parsed = parse_document(b"x" * (documents.MAX_TEXT_CHARS + 10), "large.txt")
    assert parsed["truncated"] and max(map(len, parsed["text"].splitlines())) <= 2000
    store = AttachmentStore(tmp_path, "one")
    item = store.add("long.txt", b"a" * 5000 + b"TAIL")
    with AgentSession(Settings(workspace=tmp_path), ScriptedModel(responses=[])) as session:
        current = AttachmentStore(session.settings.data_dir, session.session_id).add(
            "long.txt", b"a" * 5000 + b"TAIL"
        )
        tool = next(t for t in session.tools if t.name == "read_attachment")
        assert "TAIL" in tool.invoke({"attachment_id": current["id"], "offset": 2})
    assert store.read(item["id"])["text"].endswith("TAIL")


def test_parser_timeout_and_environment_filtering(monkeypatch):
    monkeypatch.setenv("CUSTOM_CREDENTIAL", "not-inherited")

    def run(*args, **kwargs):
        assert "CUSTOM_CREDENTIAL" not in kwargs["env"]
        raise subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(DocumentError, match="超时"):
        parse_document(b"%PDF", "file.pdf")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"invalid")
    )
    with pytest.raises(DocumentError, match="解析失败"):
        parse_document(b"%PDF", "file.pdf")


def test_store_isolation_persistence_quota_and_context(tmp_path):
    store = AttachmentStore(tmp_path, "one")
    item = store.add("same.txt", b"first")
    second = store.add("same.txt", b"second")
    assert second["id"] != item["id"]
    assert len(AttachmentStore(tmp_path, "one").list()) == 2
    assert "untrusted reference data" in store.context([item["id"]])
    with pytest.raises(DocumentError, match="当前会话"):
        AttachmentStore(tmp_path, "two").read(item["id"])
    with pytest.raises(DocumentError, match="8 个不同"):
        store.context([item["id"], item["id"]])
    for _ in range(18):
        store.add("small.txt", b"x")
    with pytest.raises(DocumentError, match="最多 20"):
        store.add("overflow.txt", b"x")


@pytest.mark.parametrize("suffix", [".pdf", ".docx", ".pptx"])
def test_workspace_document_read_does_not_authorize_binary_overwrite(session, tools, suffix):
    path = session.settings.workspace / ("report" + suffix)
    data = document_bytes(suffix)
    path.write_bytes(data)
    assert "Budget" in tools["read_file"].invoke({"path": path.name})[0]["text"]
    with pytest.raises(ValueError, match="complete current"):
        tools["write_file"].invoke({"path": path.name, "content": "destroy"})
    assert path.read_bytes() == data


def test_attachment_tool_paging_and_readonly_permission(session, tools):
    store = AttachmentStore(session.settings.data_dir, session.session_id)
    item = store.add("notes.txt", b"first\nsecond\nthird")
    assert "2: second" in tools["read_attachment"].invoke(
        {"attachment_id": item["id"], "offset": 1, "limit": 1}
    )
    with pytest.raises(ValueError, match="offset"):
        tools["read_attachment"].invoke({"attachment_id": item["id"], "offset": -1})
    for mode in PermissionMode:
        assert decide(mode, "read_attachment", {})[0] == Decision.ALLOW


def test_cli_imports_external_document_and_passes_reference(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = tmp_path / "external.docx"
    path.write_bytes(document_bytes(".docx"))
    model = ScriptedModel(responses=[AIMessage(content="Read attachment")])
    monkeypatch.setattr("soranimi.cli.app.create_model", lambda settings: model)
    response = CliRunner().invoke(
        app,
        ["Summarize", "--workspace", str(workspace), "--attach", str(path), "--non-interactive"],
    )
    assert response.exit_code == 0, response.stdout
    assert "Budget" in model.observed[0][-1].content
    assert "read_attachment" in model.observed[0][-1].content
    assert not (workspace / path.name).exists()
