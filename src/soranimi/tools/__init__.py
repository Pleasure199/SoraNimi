"""Construct a session-scoped tool registry with explicit dependencies."""

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel

from soranimi.context.compact_tool import compact
from soranimi.git.integration import make_git_tools
from soranimi.repo_map.parser import parse_python
from soranimi.repo_map.ranker import render_map
from soranimi.tools.bash import make_bash
from soranimi.tools.edit_file import make_edit_file
from soranimi.tools.glob_tool import make_glob
from soranimi.tools.grep_tool import make_grep
from soranimi.tools.list_dir import make_list_dir
from soranimi.tools.read_file import make_read_file
from soranimi.tools.write_file import make_write_file


class PlanStep(BaseModel):
    step: str
    status: Literal["pending", "in_progress", "completed"]


def create_tools(ctx, memory):
    @tool
    def update_plan(steps: list[PlanStep]) -> str:
        """Maintain ordered task steps, using pending/in_progress/completed states."""
        if sum(s.status == "in_progress" for s in steps) > 1:
            raise ValueError("Only one plan step may be in progress")
        return "Plan updated."

    @tool
    def remember_memory(key: str, value: str) -> str:
        """Save a project preference across sessions, when requested by the user."""
        if len(key) > 200 or len(value) > 4000:
            raise ValueError("Memory exceeds length limit")
        memory.put(str(ctx.settings.workspace), key, {"text": value})
        return "Preference saved."

    @tool
    def recall_memory(query: str = "") -> list[dict]:
        """Look up saved project preferences with an optional substring query."""
        return memory.search(str(ctx.settings.workspace), query)

    @tool
    def repo_map(token_budget: int = 2000) -> str:
        """Build a ranked Python symbol/import map, within a 100-10000 token budget."""
        if not 100 <= token_budget <= 10000:
            raise ValueError("token_budget must be 100-10000")
        files = []
        for path in ctx.workspace.files():
            if path.suffix == ".py" and path.stat().st_size < 1024 * 1024:
                try:
                    files.append(
                        parse_python(
                            ctx.workspace.relative(path), path.read_text(encoding="utf-8-sig")
                        )
                    )
                except UnicodeError:
                    continue
        return render_map(files, token_budget) or "No Python symbols found."

    return [
        make_read_file(ctx),
        make_list_dir(ctx),
        make_glob(ctx),
        make_grep(ctx),
        make_edit_file(ctx),
        make_write_file(ctx),
        make_bash(ctx),
        compact,
        update_plan,
        remember_memory,
        recall_memory,
        repo_map,
        *make_git_tools(ctx),
    ]
