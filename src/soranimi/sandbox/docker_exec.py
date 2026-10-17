"""Ephemeral offline containers; the only host bind mount is the workspace."""

import os
import subprocess
from uuid import uuid4

from soranimi.sandbox.subprocess_exec import SubprocessExecutor


class DockerExecutor(SubprocessExecutor):
    def __init__(self, workspace, image="soranimi-sandbox:latest", max_output=24000, data_dir=None):
        super().__init__(workspace, max_output)
        self.image = image
        self.data_dir = data_dir or workspace / ".soranimi"

    def execute(self, command: str, timeout: int = 120):
        name = f"soranimi-{uuid4().hex}"
        # Do not expose local session databases or Git metadata inside the mount.
        args = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=256",
            "--memory=1g",
            "--cpus=2",
            "--tmpfs",
            "/tmp:rw,nosuid,size=256m",
            "--mount",
            f"type=bind,source={self.workspace},target=/workspace",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
        ]
        protected = [self.workspace / ".git", self.data_dir]
        for path in protected:
            if not path.exists() or not path.is_relative_to(self.workspace):
                continue
            target = "/workspace/" + path.relative_to(self.workspace).as_posix()
            if path.is_dir():
                args.extend(["--tmpfs", target + ":ro,nosuid,nodev,noexec"])
            else:
                # Git worktrees have a .git file, not a directory.
                self.data_dir.mkdir(parents=True, exist_ok=True)
                empty = self.data_dir / "docker-empty"
                empty.write_bytes(b"")
                args.extend(["--mount", f"type=bind,source={empty},target={target},readonly"])
        if os.name != "nt":
            args.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        args.extend([self.image, "sh", "-c", command])
        try:
            return self.run(args, timeout)
        finally:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15, **options)
