import os
import subprocess

from langchain_core.tools import tool


def make_git_tools(ctx):
    def run(args):
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.run(
            ["git", "--no-pager", "-c", "core.hooksPath=", *args],
            cwd=ctx.settings.workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **options,
        )
        return {
            "stdout": process.stdout[: ctx.settings.max_output],
            "stderr": process.stderr[: ctx.settings.max_output],
            "exit_code": process.returncode,
        }

    @tool
    def git_status() -> dict:
        """Show Git working-tree status without changing the index or files."""
        return run(["status", "--short"])

    @tool
    def git_diff() -> dict:
        """Show unstaged Git diff with external diff drivers disabled."""
        return run(["diff", "--no-ext-diff", "--no-textconv"])

    @tool
    def git_commit(message: str, paths: list[str]) -> dict:
        """Commit explicit workspace files after approval. Unrelated staged files are excluded.

        This tool is for already tracked files. Add new files using an approved
        git add command first. No automatic commits or branch changes occur.
        """
        if not paths or not message.strip():
            raise ValueError("Provide a message and explicit paths")
        safe_paths = [ctx.workspace.relative(ctx.workspace.resolve(p)) for p in paths]
        return run(["commit", "--only", "-m", message, "--", *safe_paths])

    return [git_status, git_diff, git_commit]
