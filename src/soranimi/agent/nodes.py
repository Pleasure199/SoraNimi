"""One tool per committed graph step prevents replay of earlier side effects."""

import json
import time
from pathlib import Path

from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import interrupt

from soranimi.context.auto_compact import compact_history
from soranimi.context.micro_compact import micro_compact
from soranimi.context.token_estimator import estimate_tokens
from soranimi.permission.approval import decide
from soranimi.permission.modes import Decision


def last_call_message(messages):
    return next(m for m in reversed(messages) if isinstance(m, AIMessage) and m.tool_calls)


def create_nodes(model, tools, settings, system_prompt, transcript, summary_model=None):
    by_name = {tool.name: tool for tool in tools}
    try:
        bound = model.bind_tools(tools)
    except NotImplementedError as exc:
        raise ValueError("This model does not support native tool calling") from exc
    audit_path = Path(settings.data_dir) / "tool-events.jsonl"

    def llm_call(state, config):
        transcript.append(state["messages"])
        response = bound.invoke(
            [SystemMessage(content=system_prompt), *state["messages"]], config=config
        )
        return {"messages": [response], "tool_index": 0}

    def tool_node(state, config):
        index = state.get("tool_index", 0)
        call = last_call_message(state["messages"]).tool_calls[index]
        name, args = call["name"], call["args"]
        decision, risk = decide(settings.permission, name, args)
        tool = by_name.get(name)
        error = None
        # Validate before asking a person to approve an invalid operation.
        if tool is None:
            error = f"Unknown tool: {name}"
        else:
            try:
                tool.get_input_schema().model_validate(args)
            except ValueError as exc:
                error = str(exc)
        if error is None and decision == Decision.APPROVE:
            approved = interrupt({"type": "approval", "tool": name, "args": args, "risk": risk})
            if approved is not True:
                error = "Operation cancelled by user."
        elif decision == Decision.DENY:
            error = f"Operation denied by {settings.permission.value} policy ({risk})."
        writer = get_stream_writer()
        writer({"event": "tool_start", "name": name, "id": call["id"]})
        started = time.monotonic()
        updates = {"tool_index": index + 1}
        status = "error" if error else "success"
        if error:
            result = ToolMessage(content=error, name=name, tool_call_id=call["id"], status="error")
        else:
            try:
                result = tool.invoke(call, config=config)
                if not isinstance(result, ToolMessage):
                    result = ToolMessage(
                        content=json.dumps(result, ensure_ascii=False, default=str),
                        name=name,
                        tool_call_id=call["id"],
                    )
                if name == "compact":
                    updates.update(compact_requested=True, compact_focus=args.get("focus", ""))
                if name == "update_plan" and result.status != "error":
                    updates["plan"] = args["steps"]
            except Exception as exc:
                # LangGraph interrupts inherit BaseException and must propagate.
                # Provider/MCP/tool failures become results the model can repair.
                result = ToolMessage(
                    content=f"{type(exc).__name__}: {exc}",
                    name=name,
                    tool_call_id=call["id"],
                    status="error",
                )
            status = result.status
        updates["messages"] = [result]
        writer({"event": "tool_end", "name": name, "id": call["id"], "status": status})
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as log:
            log.write(
                json.dumps(
                    {
                        "tool": name,
                        "id": call["id"],
                        "status": status,
                        "seconds": round(time.monotonic() - started, 3),
                    }
                )
                + "\n"
            )
        return updates

    def context_check(state, config):
        messages = state["messages"]
        transcript.append(messages)
        compacted = micro_compact(messages)
        if estimate_tokens(compacted) > settings.context_tokens or state.get("compact_requested"):
            compacted = compact_history(
                compacted,
                summary_model if summary_model is not None else model,
                state.get("compact_focus", ""),
                config=config,
            )
        if compacted != messages:
            return {
                "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *compacted],
                "compact_requested": False,
                "compact_focus": "",
            }
        return {"compact_requested": False, "compact_focus": ""}

    return llm_call, tool_node, context_check
