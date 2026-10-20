"""Approval is performed before any side effect in a dedicated graph step."""

from soranimi.permission.classifier import Risk, classify_command
from soranimi.permission.modes import Decision, PermissionMode

READ_TOOLS = {
    "read_file",
    "read_attachment",
    "list_dir",
    "glob",
    "grep",
    "repo_map",
    "git_status",
    "git_diff",
    "recall_memory",
}
LOCAL_TOOLS = {"compact", "update_plan", "spawn_subagent", "parallel_subagents", "plan_and_execute"}


def decide(mode: PermissionMode, name: str, args: dict) -> tuple[Decision, str]:
    if name in READ_TOOLS or name in LOCAL_TOOLS:
        return Decision.ALLOW, "read" if name in READ_TOOLS else "internal"
    if name == "bash":
        risk = classify_command(str(args.get("command", "")))
        if risk == Risk.BLOCKED:
            return Decision.DENY, risk.value
        if mode == PermissionMode.PLAN:
            return (Decision.ALLOW if risk == Risk.READ else Decision.DENY), risk.value
        if mode == PermissionMode.MANUAL or risk == Risk.DANGEROUS:
            return Decision.APPROVE, risk.value
        if risk in {Risk.READ, Risk.BUILD} or mode == PermissionMode.AUTO:
            return Decision.ALLOW, risk.value
        return Decision.APPROVE, risk.value
    if mode == PermissionMode.PLAN:
        return Decision.DENY, "write"
    if name in {"edit_file", "write_file", "remember_memory"}:
        return (Decision.APPROVE if mode == PermissionMode.MANUAL else Decision.ALLOW), "write"
    # Unknown / MCP tools can have effects outside this machine.
    return Decision.APPROVE, "external"
