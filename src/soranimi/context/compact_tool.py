from langchain_core.tools import tool


@tool
def compact(focus: str = "") -> str:
    """Request context summarization after this tool-call batch completes.

    focus describes exact paths, decisions or pending work that must be retained.
    Full history remains in the session checkpoints and transcript.
    """
    return "Context compaction requested."
