"""Validated runtime configuration; secrets stay in environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from soranimi.permission.modes import PermissionMode


@dataclass
class Settings:
    workspace: Path = field(default_factory=Path.cwd)
    model: str = field(
        default_factory=lambda: os.getenv("SORANIMI_MODEL", "deepseek:deepseek-chat")
    )
    permission: PermissionMode = PermissionMode.ACCEPT_EDITS
    sandbox: str = "subprocess"
    data_dir: Path | None = None
    base_url: str | None = None
    recursion_limit: int = 100
    context_tokens: int = 50000
    max_output: int = 24000
    docker_image: str = "soranimi-sandbox:latest"
    subagents: bool = False
    max_parallel: int = 6
    mcp_config: Path | None = None
    summary_model: str | None = field(
        default_factory=lambda: os.getenv("SORANIMI_SUMMARY_MODEL") or None
    )
    summary_base_url: str | None = field(
        default_factory=lambda: os.getenv("SORANIMI_SUMMARY_BASE_URL") or None
    )

    def __post_init__(self):
        self.workspace = self.workspace.expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError(f"Workspace is not a directory: {self.workspace}")
        self.permission = PermissionMode(self.permission)
        self.data_dir = (self.data_dir or self.workspace / ".soranimi").expanduser().resolve()
        if self.workspace.is_relative_to(self.data_dir):
            raise ValueError("data_dir must not be the workspace or an ancestor of it")
        if self.sandbox not in {"subprocess", "docker"}:
            raise ValueError("sandbox must be subprocess or docker")
        if self.recursion_limit < 4 or self.context_tokens < 1000:
            raise ValueError("recursion_limit must be >= 4; context_tokens must be >= 1000")
        if not 1 <= self.max_parallel <= 6:
            raise ValueError("max_parallel must be between 1 and 6")
