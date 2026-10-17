from pathlib import Path


def load_instructions(
    root: Path, cwd: Path | None = None, global_path: Path | None = None, max_bytes: int = 32768
) -> str:
    root = root.resolve()
    cwd = (cwd or root).resolve()
    if not cwd.is_relative_to(root):
        raise ValueError("Instruction cwd must be inside workspace")
    candidates = [global_path or Path.home() / ".soranimi" / "AGENTS.md", root / "AGENTS.md"]
    current = root
    for part in cwd.relative_to(root).parts:
        current = current / part
        candidates.append(current / "AGENTS.md")
    output = []
    remaining = max_bytes
    for path in dict.fromkeys(candidates):
        if not path.is_file():
            continue
        if path.is_symlink():
            continue
        data = path.read_bytes()
        clipped = len(data) > remaining
        text = data[:remaining].decode("utf-8", errors="replace")
        output.append(f"Instructions from {path}:\n{text}")
        remaining -= min(len(data), remaining)
        if clipped or remaining == 0:
            output.append("[Instruction byte limit reached]")
            break
    return "\n\n".join(output)
