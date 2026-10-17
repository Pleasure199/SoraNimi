from langchain_core.messages import ToolMessage


def micro_compact(messages, keep_recent: int = 3, min_chars: int = 100):
    indices = [i for i, message in enumerate(messages) if isinstance(message, ToolMessage)]
    old = set(indices[:-keep_recent] if keep_recent else indices)
    return [
        message.model_copy(
            update={
                "content": f"[Previous output: {message.name or 'tool'}; archived]",
                "artifact": None,
            }
        )
        if i in old and len(str(message.content)) > min_chars
        else message
        for i, message in enumerate(messages)
    ]
