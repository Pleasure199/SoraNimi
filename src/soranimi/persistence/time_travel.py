from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES


def is_rewindable(state) -> bool:
    messages = state.values.get("messages", [])
    return (
        not state.next
        and bool(messages)
        and isinstance(messages[-1], AIMessage)
        and not messages[-1].tool_calls
    )


def list_checkpoints(graph, config: dict, limit: int = 20) -> list[dict]:
    return [
        {
            "id": state.config["configurable"]["checkpoint_id"],
            "step": (state.metadata or {}).get("step"),
            "next": state.next,
            "rewindable": is_rewindable(state),
        }
        for state in graph.get_state_history(config, limit=limit)
    ]


def rewind(graph, config: dict, checkpoint_id: str) -> dict:
    """Fork graph history only. File snapshots are deliberately separate."""
    for state in graph.get_state_history(config):
        if state.config["configurable"]["checkpoint_id"] == checkpoint_id:
            messages = state.values.get("messages", [])
            if not is_rewindable(state):
                raise ValueError(
                    "Choose a completed-turn checkpoint (rewindable=true); pending tool steps cannot be rewound"
                )
            # Copy the historical state into a new head; never leave a sticky
            # checkpoint_id in CLI config (it would fork every future turn).
            values = dict(state.values, messages=[RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages])
            result = graph.update_state(state.config, values, as_node="llm_call")
            return {
                "configurable": {"thread_id": result["configurable"]["thread_id"]},
                "recursion_limit": config.get("recursion_limit", 100),
            }
    raise ValueError("Checkpoint not found in this session")
