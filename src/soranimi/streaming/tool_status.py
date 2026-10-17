from dataclasses import dataclass


@dataclass
class ToolStatus:
    name: str
    call_id: str
    status: str = "running"
