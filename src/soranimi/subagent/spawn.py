from langchain_core.tools import tool

from soranimi.subagent.plan_execute import parallel_research
from soranimi.subagent.subgraph import run_worker


def make_subagent_tools(model, tools, settings, instructions):
    def worker(task):
        return run_worker(task, model, tools, settings, instructions)

    @tool
    def spawn_subagent(task: str) -> dict:
        """Delegate read-only code research to one isolated worker.

        Returns a bounded summary. Workers cannot edit, run commands, call MCP
        or create further workers. The main agent performs changes and tests.
        """
        return worker(task)

    @tool
    def parallel_subagents(tasks: list[str]) -> list[dict]:
        """Research up to six independent questions using LangGraph Send fan-out.

        Workers are read-only and return summaries, not their message history.
        """
        if not 1 <= len(tasks) <= settings.max_parallel:
            raise ValueError(f"Supply 1-{settings.max_parallel} tasks")
        return parallel_research(tasks, worker, settings.max_parallel)

    return [spawn_subagent, parallel_subagents]
