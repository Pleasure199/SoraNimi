"""Deterministic offline model used for a real graph/tool smoke demo."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class DemoModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "soranimi-offline-demo"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            message = AIMessage(
                content="离线演示完成：已通过 LangGraph 调用 list_dir 并读取工具结果。配置模型 API Key 后即可执行真实编程任务。"
            )
        else:
            message = AIMessage(
                content="正在检查工作区。",
                tool_calls=[{"name": "list_dir", "args": {"path": "."}, "id": "demo-list"}],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])
