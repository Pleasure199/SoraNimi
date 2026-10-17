from langchain_core.tools import tool

from soranimi.tools.edit_pipeline import replace_unique
from soranimi.tools.read_file import decode_text
from soranimi.workspace import digest


def make_edit_file(ctx):
    @tool
    def edit_file(path: str, old_string: str, new_string: str) -> str:
        """Replace a unique string in a file, after reading the complete file.

        old_string must identify exactly one occurrence. Empty new_string deletes
        text. If the file changed externally, read it again before retrying.
        Encoding, newlines and permissions are preserved; edits can be undone.
        """
        target = ctx.workspace.resolve(path)
        data = target.read_bytes()
        if ctx.workspace.reads.get(ctx.workspace.relative(target)) != digest(data):
            raise ValueError("A complete current read_file is required before editing")
        content, encoding = decode_text(data)
        updated = replace_unique(content, old_string, new_string)
        encoded = updated.encode(encoding)
        if encoding in {"utf-16-le", "utf-16-be"}:
            encoded = data[:2] + encoded
        ctx.snapshots.write(target, encoded, expected=data)
        return f"Edited {ctx.workspace.relative(target)}"

    return edit_file
