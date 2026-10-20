"""Validated runtime configuration; secrets stay in environment variables."""

import json
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
    models_config: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["SORANIMI_MODELS_CONFIG"])
            if os.getenv("SORANIMI_MODELS_CONFIG")
            else None
        )
    )
    subagent_model: str | None = field(
        default_factory=lambda: os.getenv("SORANIMI_SUBAGENT_MODEL") or None
    )
    model_profiles: dict = field(default_factory=dict, repr=False)

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
        if self.models_config:
            path = self.models_config.expanduser()
            self.models_config = (self.workspace / path).resolve()
            if not self.model_profiles:
                try:
                    self.model_profiles = json.loads(self.models_config.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise ValueError("Cannot read models config JSON") from exc
        if not isinstance(self.model_profiles, dict):
            raise ValueError("Models config must be an object mapping names to model profiles")
        for alias, profile in self.model_profiles.items():
            if (
                not isinstance(alias, str)
                or not alias.strip()
                or alias != alias.strip()
                or ":" in alias
                or len(alias) > 100
            ):
                raise ValueError("Model profile names must contain 1-100 characters without ':'")
            if not isinstance(profile, dict) or set(profile) - {"model", "base_url", "api_key_env"}:
                raise ValueError(f"Invalid model profile: {alias}")
            if any(not isinstance(value, str) or not value.strip() for value in profile.values()):
                raise ValueError(f"Model profile fields must be non-empty strings: {alias}")
            name = profile.get("model", "")
            if (
                ":" not in name
                or name.split(":", 1)[0] not in {"openai", "anthropic", "deepseek"}
                or not name.split(":", 1)[1].strip()
            ):
                raise ValueError(f"Model profile requires provider:model: {alias}")
        for selection in (self.model, self.subagent_model, self.summary_model):
            if selection:
                self.validate_model(selection)

    def validate_model(self, name):
        if name not in self.model_profiles and self.model_profiles and ":" not in name:
            raise ValueError(f"Unknown model profile: {name}")
