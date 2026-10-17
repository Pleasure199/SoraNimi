"""Permission modes and policy decisions."""

from enum import Enum


class PermissionMode(str, Enum):
    MANUAL = "manual"
    ACCEPT_EDITS = "accept-edits"
    PLAN = "plan"
    AUTO = "auto"


class Decision(str, Enum):
    ALLOW = "allow"
    APPROVE = "approve"
    DENY = "deny"
