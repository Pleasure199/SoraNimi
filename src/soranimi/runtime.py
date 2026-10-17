"""Own every session resource through one context manager."""

from contextlib import ExitStack

from soranimi.agent.graph import build_graph
from soranimi.agent.model_factory import SummaryModel
from soranimi.checkpoints.snapshot import SnapshotStore
from soranimi.instructions.loader import load_instructions
from soranimi.mcp.registry import merge_tools
from soranimi.persistence.checkpointer import open_checkpointer
from soranimi.persistence.session import SessionManager
from soranimi.persistence.store import MemoryStore
from soranimi.persistence.transcript import Transcript
from soranimi.sandbox.executor import create_executor
from soranimi.tools import create_tools
from soranimi.tools.runtime import ToolContext
from soranimi.workspace import Workspace


class AgentSession:
    def __init__(
        self, settings, model, resume=None, stop_event=None, summary_model=None, model_factory=None
    ):
        self.settings = settings
        self.model = model
        self.summary_model = (
            summary_model
            if summary_model is not None
            else SummaryModel(settings, model, model_factory)
        )
        self.resume = resume
        self.stop_event = stop_event
        self.stack = ExitStack()

    def __enter__(self):
        try:
            self.manager = SessionManager(self.settings.data_dir / "sessions.db")
            self.stack.callback(self.manager.close)
            self.session_id = (
                self.manager.resume(self.resume, self.settings.workspace)
                if self.resume
                else self.manager.create(self.settings.workspace, self.settings.model)
            )
            self.config = {
                "configurable": {"thread_id": self.session_id},
                "recursion_limit": self.settings.recursion_limit,
            }
            self.workspace = Workspace(self.settings.workspace, self.settings.data_dir)
            self.snapshots = SnapshotStore(
                self.settings.data_dir / "files.db", self.workspace, self.session_id
            )
            self.stack.callback(self.snapshots.close)
            self.memory = MemoryStore(self.settings.data_dir / "memory.db")
            self.stack.callback(self.memory.close)
            self.transcript = Transcript(
                self.settings.data_dir / "transcripts" / f"{self.session_id}.jsonl"
            )
            self.tools = create_tools(
                ToolContext(
                    self.settings, self.workspace, self.snapshots, create_executor(self.settings)
                ),
                self.memory,
            )
            instructions = load_instructions(self.settings.workspace)
            if self.settings.subagents:
                from soranimi.subagent.spawn import make_subagent_tools

                self.tools += make_subagent_tools(
                    self.model, self.tools, self.settings, instructions, self.summary_model
                )
            if self.settings.mcp_config:
                from soranimi.mcp.client import MCPConnection

                connection = self.stack.enter_context(MCPConnection(self.settings.mcp_config))
                self.tools = merge_tools(self.tools, connection.tools)
            saver = self.stack.enter_context(
                open_checkpointer(self.settings.data_dir / "checkpoints.db")
            )
            self.graph = build_graph(
                self.model,
                self.tools,
                self.settings,
                saver,
                self.transcript,
                instructions,
                self.stop_event,
                summary_model=self.summary_model,
            )
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        try:
            self.transcript.append(self.graph.get_state(self.config).values.get("messages", []))
        finally:
            self.stack.close()
