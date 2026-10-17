"""Reject name collisions rather than silently replacing a local tool."""


def merge_tools(local, remote):
    names = set()
    merged = []
    for tool in [*local, *remote]:
        if tool.name in names:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        names.add(tool.name)
        merged.append(tool)
    return merged
