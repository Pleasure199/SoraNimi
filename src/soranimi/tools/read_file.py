"""Numbered, bounded UTF-8 / UTF-16 reads and image content blocks."""

import base64
import mimetypes

from langchain_core.tools import tool


def decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be"), "utf-16-be"
    return data.decode("utf-8-sig"), "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"


def make_read_file(ctx):
    @tool
    def read_file(path: str, offset: int = 0, limit: int = 2000) -> list[dict]:
        """Read a workspace file with 1-based line numbers. Offset is 0-based.

        Use pages for large files. edit_file requires one complete, untruncated
        read first; increase limit if necessary (maximum 20000). Images return
        image content blocks. PDF/DOCX/PPTX return extracted text, not an editable
        source read. Files outside the workspace are inaccessible.
        """
        if offset < 0 or not 1 <= limit <= 20000:
            raise ValueError("offset must be >= 0 and limit between 1 and 20000")
        target = ctx.workspace.resolve(path)
        from soranimi.documents import DOCUMENT_SUFFIXES, MAX_FILE_BYTES, parse_document

        if target.suffix.lower() in DOCUMENT_SUFFIXES | {".doc", ".ppt"}:
            from soranimi.tools.read_attachment import document_page

            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Document exceeds the 20 MiB read limit")
            parsed = parse_document(target.read_bytes(), target.name)
            warning = "[Extraction truncated at 500000 characters]\n" if parsed["truncated"] else ""
            return [
                {
                    "type": "text",
                    "text": warning
                    + document_page(parsed["text"], offset, limit, ctx.settings.max_output),
                }
            ]
        if target.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("File exceeds the 5 MiB read limit")
        data = target.read_bytes()
        mime = mimetypes.guess_type(target.name)[0]
        if mime in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            return [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"},
                }
            ]
        content, _ = decode_text(data)
        if "\x00" in content:
            raise ValueError("Binary file cannot be read as text")
        lines = content.splitlines()
        selected = lines[offset : offset + limit]
        output = []
        chars = 0
        clipped = False
        for index, line in enumerate(selected, offset + 1):
            rendered = f"{index}: {line[:4000]}"
            if len(line) > 4000:
                rendered += " [line truncated]"
                clipped = True
            if chars + len(rendered) > ctx.settings.max_output:
                clipped = True
                break
            output.append(rendered)
            chars += len(rendered) + 1
        complete = offset == 0 and len(selected) == len(lines) and not clipped
        if complete:
            ctx.snapshots.record_read(target, data)
        text = "\n".join(output)
        text += f"\n[lines {len(output)} of {len(lines)}; complete={str(complete).lower()}]"
        return [{"type": "text", "text": text}]

    return read_file
