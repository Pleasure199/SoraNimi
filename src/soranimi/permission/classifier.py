"""Conservative shell classification. This is an approval policy, not isolation."""

import re
import shlex
from enum import Enum


class Risk(str, Enum):
    READ = "read"
    BUILD = "build"
    WRITE = "write"
    DANGEROUS = "dangerous"
    BLOCKED = "blocked"


def classify_command(command: str) -> Risk:
    text = command.strip().lower()
    if not text:
        return Risk.BLOCKED
    # Shell composition, expansion and interpreters cannot be proven read-only.
    if re.search(r"\b(sudo|eval|invoke-expression|iex)\b", text):
        return Risk.BLOCKED
    if re.search(r"\b(bash|sh|zsh|cmd|powershell|pwsh)\s+(-c\b|/c\b|-command\b|-enc)", text):
        return Risk.BLOCKED
    if re.search(r"\b(rm|rmdir|del|erase|remove-item|format|diskpart)\b", text):
        return Risk.DANGEROUS
    if re.search(r"\bgit\s+(push|reset|clean|checkout|restore|revert)\b", text):
        return Risk.DANGEROUS
    if any(c in text for c in ";&|><`$\n\r(){}"):
        return Risk.DANGEROUS
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        return Risk.DANGEROUS
    if not parts:
        return Risk.BLOCKED
    executable = parts[0].removesuffix(".exe")
    args = parts[1:]
    if executable == "git":
        if args and args[0] in {"status", "log", "diff", "show", "ls-files", "rev-parse"}:
            if any(
                x in text for x in ("--output", "--ext-diff", "--textconv", "--exec", "--no-index")
            ):
                return Risk.DANGEROUS
            return Risk.READ
        return Risk.WRITE
    if executable in {
        "rg",
        "grep",
        "ls",
        "dir",
        "cat",
        "type",
        "pwd",
        "head",
        "tail",
        "wc",
        "get-childitem",
        "get-content",
        "get-location",
    }:
        # Options such as rg --pre can execute arbitrary commands.
        if any(x in text for x in ("--pre", "--hostname-bin", "--files-from", "-exec")):
            return Risk.DANGEROUS
        return Risk.READ
    if executable in {"pytest", "ruff", "mypy"}:
        return Risk.BUILD
    if executable in {"python", "python3", "py", "node", "ruby", "perl"}:
        if "-c" in args or "-e" in args:
            return Risk.DANGEROUS
        return (
            Risk.BUILD
            if executable.startswith("py") and args[:2] == ["-m", "pytest"]
            else Risk.WRITE
        )
    if executable in {"npm", "pnpm", "yarn", "cargo", "go", "make"}:
        return (
            Risk.BUILD
            if args and args[0] in {"test", "build", "check", "lint", "fmt"}
            else Risk.WRITE
        )
    return Risk.WRITE
