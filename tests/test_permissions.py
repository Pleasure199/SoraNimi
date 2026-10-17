import pytest

from soranimi.permission.approval import decide
from soranimi.permission.classifier import Risk, classify_command
from soranimi.permission.modes import Decision, PermissionMode


@pytest.mark.parametrize(
    "command,risk",
    [
        ("git status", Risk.READ),
        ("rg needle src", Risk.READ),
        ("python -m pytest", Risk.BUILD),
        ("npm test", Risk.BUILD),
        ("git commit -m change", Risk.WRITE),
        ("npm install", Risk.WRITE),
        ("git push --force", Risk.DANGEROUS),
        ("rm -rf /", Risk.DANGEROUS),
        ("ls; rm x", Risk.DANGEROUS),
        ("ls (Set-Content x bad)", Risk.DANGEROUS),
        ("git diff --output=x", Risk.DANGEROUS),
        ("rg --pre evil x", Risk.DANGEROUS),
        ("python -c 'print(1)'", Risk.DANGEROUS),
        ("sudo ls", Risk.BLOCKED),
        ("bash -c 'ls'", Risk.BLOCKED),
    ],
)
def test_classify(command, risk):
    assert classify_command(command) == risk


def test_policy_modes():
    assert decide(PermissionMode.PLAN, "edit_file", {})[0] == Decision.DENY
    assert decide(PermissionMode.PLAN, "bash", {"command": "pytest"})[0] == Decision.DENY
    assert decide(PermissionMode.MANUAL, "write_file", {})[0] == Decision.APPROVE
    assert decide(PermissionMode.ACCEPT_EDITS, "write_file", {})[0] == Decision.ALLOW
    assert decide(PermissionMode.ACCEPT_EDITS, "bash", {"command": "npm test"})[0] == Decision.ALLOW
    assert (
        decide(PermissionMode.AUTO, "bash", {"command": "git push --force"})[0] == Decision.APPROVE
    )
    assert decide(PermissionMode.AUTO, "mcp_external", {})[0] == Decision.APPROVE
    assert decide(PermissionMode.PLAN, "mcp_external", {})[0] == Decision.DENY
