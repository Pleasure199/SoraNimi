from langchain_core.tools import tool


def make_list_dir(ctx):
    @tool
    def list_dir(path: str = ".") -> str:
        """List immediate workspace entries with file/directory markers (up to 500)."""
        directory = ctx.workspace.resolve(path)
        output = []
        for entry in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            try:
                ctx.workspace.resolve(str(entry))
            except ValueError:
                continue
            if entry.is_symlink():
                continue
            output.append(f"{'dir ' if entry.is_dir() else 'file'} {entry.name}")
        return "\n".join(output[:500]) + ("\n[truncated]" if len(output) > 500 else "")

    return list_dir
