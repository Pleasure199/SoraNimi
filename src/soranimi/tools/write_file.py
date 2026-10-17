from langchain_core.tools import tool

from soranimi.workspace import digest


def make_write_file(ctx):
    @tool
    def write_file(path: str, content: str) -> str:
        """Create a UTF-8 file and missing directories within the workspace.

        To overwrite an existing file, read it completely first. Prefer edit_file
        for small changes. Writes have persistent undo snapshots.
        """
        target = ctx.workspace.resolve(path)
        current = target.read_bytes() if target.exists() else None
        if current is not None and ctx.workspace.reads.get(
            ctx.workspace.relative(target)
        ) != digest(current):
            raise ValueError("A complete current read_file is required before overwriting")
        ctx.snapshots.write(target, content.replace("\r\n", "\n").encode("utf-8"), current)
        return f"Wrote {ctx.workspace.relative(target)}"

    return write_file
