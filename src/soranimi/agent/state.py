from langgraph.graph import MessagesState


class AgentState(MessagesState):
    tool_index: int
    plan: list[dict[str, str]]
    compact_requested: bool
    compact_focus: str
