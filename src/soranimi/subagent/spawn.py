from dataclasses import replace

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from soranimi.agent.model_factory import SummaryModel, create_model
from soranimi.subagent.plan_execute import parallel_research, plan_research
from soranimi.subagent.subgraph import run_worker


def make_subagent_tools(
    model, tools, settings, instructions, summary_model=None, model_factory=None
):
    factory = model_factory or (lambda selected: create_model(selected, use_env_base_url=False))

    def worker_for(selection=None):
        if selection and selection not in settings.model_profiles:
            raise ValueError("Select a configured model profile for a worker")
        selected = selection or settings.subagent_model
        if not selected or selected == settings.model:
            return lambda task: run_worker(
                task, model, tools, settings, instructions, summary_model
            )
        child_settings = replace(settings, model=selected, base_url=None)

        def worker(task):
            # Independent clients avoid sharing mutable provider state between workers.
            client = factory(child_settings)
            child_summary = SummaryModel(child_settings, client, model_factory)
            return run_worker(task, client, tools, child_settings, instructions, child_summary)

        return worker

    @tool
    def spawn_subagent(task: str, model: str | None = None) -> dict:
        """Delegate read-only code research to one isolated worker.

        Returns a bounded summary. Workers cannot edit, run commands, call MCP
        or create further workers. The main agent performs changes and tests.
        """
        return worker_for(model)(task)

    @tool
    def parallel_subagents(tasks: list[str], model: str | None = None) -> list[dict]:
        """Research up to six independent questions using LangGraph Send fan-out.

        Workers are read-only and return summaries, not their message history.
        """
        if not 1 <= len(tasks) <= settings.max_parallel:
            raise ValueError(f"Supply 1-{settings.max_parallel} tasks")
        return parallel_research(tasks, worker_for(model), settings.max_parallel)

    @tool
    def plan_and_execute(
        objective: str, config: RunnableConfig, max_rounds: int = 3, worker_model: str | None = None
    ) -> dict:
        """Plan complex code research, delegate read-only tasks, and replan from findings.

        Runs at most 1-5 rounds with up to six isolated workers per round. Returns
        findings and remaining questions. Does not implement changes or run tests;
        the main agent must perform implementation and verification afterward.
        """
        return plan_research(
            objective, model, worker_for(worker_model), settings.max_parallel, max_rounds, config
        )

    result = [spawn_subagent, parallel_subagents, plan_and_execute]
    for entry in result:
        entry.description += (
            " Optional model selection uses only configured profile names: "
            + (", ".join(settings.model_profiles) or "none")
            + ". Omit to use the default worker model."
        )
    return result
