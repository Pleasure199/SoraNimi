"""Workspace boundary, read tracking, ignore rules and atomic file operations."""

import hashlib
import os
import tempfile
from pathlib import Path

from pathspec import GitIgnoreSpec


class WorkspaceError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".soranimi-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Workspace:
    def __init__(self, root: Path, data_dir: Path):
        self.root = root.resolve()
        self.data_dir = data_dir.resolve()
        self.reads: dict[str, str] = {}

    def resolve(self, value: str = ".") -> Path:
        path = Path(value).expanduser()
        path = (self.root / path).resolve()
        if not path.is_relative_to(self.root):
            raise WorkspaceError("Path escapes the workspace")
        rel = path.relative_to(self.root)
        if any(
            part.casefold() in {".git", ".soranimi"} for part in rel.parts
        ) or path.is_relative_to(self.data_dir):
            raise WorkspaceError("Agent state and Git internals are protected")
        return path

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def files(self, start: Path | None = None):
        start = start or self.root
        defaults = [
            ".git/",
            ".soranimi/",
            ".venv/",
            "venv/",
            "node_modules/",
            "__pycache__/",
            ".env",
            ".idea/",
        ]
        # Carry parent ignore rules into a subtree search; nested rules retain their base.
        specs = [(self.root, GitIgnoreSpec.from_lines(defaults))]
        # Walk supplies start's own rule; seed ancestors from root to its parent.
        bases = []
        current = start.parent
        while current.is_relative_to(self.root) and start != self.root:
            bases.append(current)
            if current == self.root:
                break
            current = current.parent
        for base in reversed(bases):
            ignore = base / ".gitignore"
            if ignore.is_file():
                specs.append(
                    (
                        base,
                        GitIgnoreSpec.from_lines(ignore.read_text(encoding="utf-8").splitlines()),
                    )
                )

        def walk(directory, inherited):
            rules = list(inherited)
            ignore = directory / ".gitignore"
            if ignore.is_file():
                rules.append(
                    (
                        directory,
                        GitIgnoreSpec.from_lines(ignore.read_text(encoding="utf-8").splitlines()),
                    )
                )
            for entry in sorted(directory.iterdir(), key=lambda p: p.name):
                if entry.is_symlink() or entry.is_relative_to(self.data_dir):
                    continue
                rel = entry.relative_to(self.root)
                if any(
                    part.casefold() in {".git", ".soranimi", ".venv", "node_modules", "__pycache__"}
                    for part in rel.parts
                ):
                    continue
                ignored = False
                for base, spec in rules:
                    match = spec.check_file(
                        entry.relative_to(base).as_posix() + ("/" if entry.is_dir() else "")
                    )
                    if match.include is not None:
                        ignored = match.include
                if ignored:
                    continue
                if entry.is_dir():
                    yield from walk(entry, rules)
                elif entry.is_file():
                    yield entry

        yield from walk(start, specs)
