"""Execution results are structured and bounded, including on timeout."""

from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False

    def as_dict(self):
        return asdict(self)


class Executor(Protocol):
    def execute(self, command: str, timeout: int = 120) -> ExecutionResult: ...


def create_executor(settings) -> Executor:
    if settings.sandbox == "docker":
        from soranimi.sandbox.docker_exec import DockerExecutor

        return DockerExecutor(
            settings.workspace, settings.docker_image, settings.max_output, settings.data_dir
        )
    from soranimi.sandbox.subprocess_exec import SubprocessExecutor

    return SubprocessExecutor(
        settings.workspace,
        settings.max_output,
        excluded_env=[
            p["api_key_env"] for p in settings.model_profiles.values() if "api_key_env" in p
        ],
    )
