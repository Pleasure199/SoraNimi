"""Dynamic Send fan-out; each worker owns an isolated conversation."""

import operator
from typing import Annotated

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from typing_extensions import TypedDict


class ResearchState(TypedDict):
    tasks: list[str]
    results: Annotated[list[dict], operator.add]


def parallel_research(tasks, worker, max_parallel=6):
    class WorkerState(TypedDict):
        task: str

    def execute(state: WorkerState):
        try:
            result = worker(state["task"])
        except (ValueError, OSError, RuntimeError) as exc:
            result = {"task": state["task"], "error": str(exc)}
        return {"results": [result]}

    graph = StateGraph(ResearchState)
    graph.add_node("dispatch", lambda s: {})
    graph.add_node("worker", execute)
    graph.add_edge(START, "dispatch")
    graph.add_conditional_edges(
        "dispatch", lambda s: [Send("worker", {"task": task}) for task in s["tasks"]]
    )
    graph.add_edge("worker", END)
    return graph.compile().invoke(
        {"tasks": tasks, "results": []}, {"max_concurrency": max_parallel}
    )["results"]
