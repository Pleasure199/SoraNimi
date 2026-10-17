from pathlib import PurePosixPath

from langchain_core.tools import tool


def matches(path: str, pattern: str) -> bool:
    if pattern.startswith("**/"):
        return PurePosixPath(path).match(pattern) or PurePosixPath(path).match(pattern[3:])
    return PurePosixPath(path).match(pattern)


def make_glob(ctx):
    @tool("glob")
    def glob_files(pattern: str, path: str = ".") -> str:
        """Find workspace files by glob, newest first. Respects nested .gitignore.

        Use **/*.py to find Python files recursively. Returns at most 500 paths.
        """
        base = ctx.workspace.resolve(path)
        files = [
            p for p in ctx.workspace.files(base) if matches(p.relative_to(base).as_posix(), pattern)
        ]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return "\n".join(ctx.workspace.relative(p) for p in files[:500]) + (
            "\n[truncated]" if len(files) > 500 else ""
        )

    return glob_files
