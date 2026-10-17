"""Consume LangGraph messages/custom/updates without parsing JSON token fragments."""

from langchain_core.messages import AIMessage, ToolMessage


def consume_events(events, renderer):
    streamed = False
    for mode, payload in events:
        if mode == "messages":
            chunk, metadata = payload
            if metadata.get("langgraph_node") == "llm_call" and "subagent" not in metadata.get(
                "tags", []
            ):
                text = chunk.text
                if text:
                    renderer.token(text)
                    streamed = True
        elif mode == "custom" and payload.get("event") in {"tool_start", "tool_end"}:
            renderer.tool_event(payload)
        elif mode == "updates":
            for node, update in payload.items():
                if node == "__interrupt__" or not isinstance(update, dict):
                    continue
                if node == "llm_call":
                    for message in update.get("messages", []):
                        if isinstance(message, AIMessage) and not streamed and message.text:
                            renderer.token(message.text)
                    renderer.finish()
                    streamed = False
                if node == "tools":
                    for message in update.get("messages", []):
                        if isinstance(message, ToolMessage):
                            renderer.tool_result(message)
