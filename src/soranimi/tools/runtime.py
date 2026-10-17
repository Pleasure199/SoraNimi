"""Per-session tool dependencies; no process-wide cwd or mutable tool globals."""

from dataclasses import dataclass

from soranimi.checkpoints.snapshot import SnapshotStore
from soranimi.config import Settings
from soranimi.sandbox.executor import Executor
from soranimi.workspace import Workspace


@dataclass
class ToolContext:
    settings: Settings
    workspace: Workspace
    snapshots: SnapshotStore
    executor: Executor
