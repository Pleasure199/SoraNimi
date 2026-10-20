"""Local trusted execution with bounded spooled output and process-tree cleanup."""

import os
import signal
import subprocess
import tempfile
from pathlib import Path

from soranimi.sandbox.executor import ExecutionResult


class SubprocessExecutor:
    def __init__(self, workspace: Path, max_output: int = 24000, excluded_env=()):
        self.workspace = workspace
        self.max_output = max_output
        self.excluded_env = {name.upper() for name in excluded_env}

    @staticmethod
    def kill_tree(process):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()

    def run(self, args: list[str], timeout: int, env: dict | None = None) -> ExecutionResult:
        options = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        timed_out = False
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                args,
                cwd=self.workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                **options,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self.kill_tree(process)
            except BaseException:
                self.kill_tree(process)
                raise

            def read(stream):
                stream.seek(0)
                value = stream.read(self.max_output + 1)
                return value[: self.max_output].decode("utf-8", errors="replace") + (
                    "\n[truncated]" if len(value) > self.max_output else ""
                )

            return ExecutionResult(
                read(stdout), read(stderr), 124 if timed_out else process.returncode, timed_out
            )

    def execute(self, command: str, timeout: int = 120) -> ExecutionResult:
        args = (
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
            if os.name == "nt"
            else ["/bin/sh", "-c", command]
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in self.excluded_env
            and not any(word in key.upper() for word in ("API_KEY", "SECRET", "TOKEN", "PASSWORD"))
        }
        env["PYTHONIOENCODING"] = "utf-8"
        env["GIT_TERMINAL_PROMPT"] = "0"
        return self.run(args, timeout, env)
