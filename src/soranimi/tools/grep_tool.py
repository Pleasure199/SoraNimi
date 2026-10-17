"""Stream batched ripgrep results, with an optional Python fallback."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Literal

from langchain_core.tools import tool

from soranimi.tools.glob_tool import matches
from soranimi.tools.read_file import decode_text


def rg_lines(files, pattern, mode, workspace):
    # Keep explicit paths below Windows' command-line length limit. Passing only
    # enumerated files retains our ignore rules and excludes links/state files.
    batches = []
    batch = []
    size = 0
    for file in files:
        if size + len(str(file)) > 16000 and batch:
            batches.append(batch)
            batch, size = [], 0
        batch.append(file)
        size += len(str(file)) + 3
    if batch:
        batches.append(batch)
    flags = (
        ["--count"]
        if mode == "count"
        else ["--files-with-matches"]
        if mode == "files"
        else ["--json", "--max-count", "501", "--max-columns", "2000", "--max-columns-preview"]
    )
    for batch in batches:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        args = ["rg", "--no-config", "--with-filename", *flags, "--", pattern, *map(str, batch)]
        error_stream = tempfile.TemporaryFile()
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=error_stream,
            text=True,
            encoding="utf-8",
            errors="replace",
            **options,
        )
        try:
            for line in process.stdout:
                line = line.rstrip("\r\n")
                if mode == "files":
                    yield workspace.relative(workspace.resolve(line))
                elif mode == "count":
                    path, _, count = line.rpartition(":")
                    yield f"{workspace.relative(workspace.resolve(path))}:{count}"
                else:
                    event = json.loads(line)
                    if event["type"] == "match":
                        data = event["data"]
                        path = data["path"].get("text")
                        text = data["lines"].get("text")
                        if path is not None and text is not None:
                            key = workspace.relative(workspace.resolve(path))
                            yield f"{key}:{data['line_number']}:{text.rstrip()[:2000]}"
            error_stream.seek(0)
            error = error_stream.read(2000).decode("utf-8", errors="replace")
            code = process.wait(timeout=10)
            if code not in (0, 1):
                raise ValueError(error[:2000] or "ripgrep search failed")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            error_stream.close()


def python_lines(files, pattern, mode, workspace):
    expression = re.compile(pattern)
    for file in files:
        try:
            text, _ = decode_text(file.read_bytes())
        except (UnicodeError, OSError):
            continue
        if "\x00" in text:
            continue
        found = [
            (n, line) for n, line in enumerate(text.splitlines(), 1) if expression.search(line)
        ]
        key = workspace.relative(file)
        if found:
            if mode == "files":
                yield key
            elif mode == "count":
                yield f"{key}:{len(found)}"
            else:
                for n, line in found:
                    yield f"{key}:{n}:{line[:2000]}"


def make_grep(ctx):
    @tool
    def grep(
        pattern: str,
        path: str = ".",
        glob: str = "**/*",
        output_mode: Literal["content", "files", "count"] = "content",
    ) -> str:
        """Search file contents with a regex; include paths and line numbers.

        Respects nested .gitignore and excludes symlinks. output_mode is content,
        files, or count (matching lines per file). Results are capped at 500 lines
        and the configured output limit. Without rg, uses Python regex syntax.
        """
        if len(pattern) > 4000:
            raise ValueError("Search pattern exceeds the 4000 character limit")
        base = ctx.workspace.resolve(path)
        candidates = [base] if base.is_file() else ctx.workspace.files(base)
        files = [
            file
            for file in candidates
            if matches(ctx.workspace.relative(file), glob)
            and file.stat().st_size <= 5 * 1024 * 1024
        ]
        search = rg_lines if shutil.which("rg") else python_lines
        lines = search(files, pattern, output_mode, ctx.workspace)
        output = []
        length = 0
        try:
            for line in lines:
                if len(output) >= 500 or length + len(line) > ctx.settings.max_output:
                    return "\n".join(output) + "\n[truncated]"
                output.append(line)
                length += len(line) + 1
        finally:
            lines.close()
        return "\n".join(output) or "No matches."

    return grep
