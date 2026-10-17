from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from soranimi.agent.graph import build_graph
from soranimi.config import Settings
from soranimi.permission.approval import READ_TOOLS
from soranimi.permission.modes import PermissionMode


def run_worker(task, model, tools, settings, instructions, summary_model=None):
    """Workers cannot spawn workers, invoke MCP, execute shells or edit files."""
    child = Settings(
        workspace=settings.workspace,
        data_dir=settings.data_dir,
        model=settings.model,
        permission=PermissionMode.PLAN,
        context_tokens=settings.context_tokens,
        recursion_limit=40,
    )

    class NoTranscript:
        def append(self, messages):
            pass

    graph = build_graph(
        model,
        [t for t in tools if t.name in READ_TOOLS],
        child,
        InMemorySaver(),
        NoTranscript(),
        instructions,
        summary_model=summary_model,
    )
    from uuid import uuid4

    result = graph.invoke(
        {"messages": [HumanMessage(content=task)]},
        {"configurable": {"thread_id": uuid4().hex}, "recursion_limit": 40, "tags": ["subagent"]},
    )
    return {"task": task, "summary": result["messages"][-1].text[:12000]}
