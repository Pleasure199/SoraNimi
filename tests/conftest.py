from collections.abc import Sequence

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from soranimi.agent.demo import DemoModel
from soranimi.config import Settings
from soranimi.runtime import AgentSession


class ScriptedModel(BaseChatModel):
    responses: Sequence[AIMessage]
    cursor: int = 0
    observed: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "test-scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed.append(list(messages))
        message = self.responses[self.cursor]
        self.cursor += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def calls(*items):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{i}"} for i, (name, args) in enumerate(items)
        ],
    )


@pytest.fixture
def session(tmp_path):
    with AgentSession(Settings(workspace=tmp_path), DemoModel()) as value:
        yield value


@pytest.fixture
def tools(session):
    return {tool.name: tool for tool in session.tools}
