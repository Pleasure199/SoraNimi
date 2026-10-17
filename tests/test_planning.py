import json

import pytest
from conftest import ScriptedModel, calls
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from soranimi.config import Settings
from soranimi.permission.approval import decide
from soranimi.permission.modes import Decision, PermissionMode
from soranimi.runtime import AgentSession
from soranimi.subagent.plan_execute import plan_research


def plan(tasks=(), summary="", complete=False):
    return AIMessage(
        content=json.dumps({"tasks": list(tasks), "summary": summary, "complete": complete})
    )


def test_replanner_uses_findings_to_dispatch_followup():
    model = ScriptedModel(
        responses=[
            plan(["Locate entry point"]),
            plan(["Inspect the missing validation"]),
            plan(summary="app.py lacks validation", complete=True),
        ]
    )
    observed = []

    def worker(task):
        observed.append(task)
        return {"summary": "Found app.py"}

    result = plan_research("Find incomplete validation", model, worker, max_parallel=1)
    assert result["complete"] and result["rounds"] == 2
    assert [r["task"] for r in result["results"]] == [
        "Locate entry point",
        "Inspect the missing validation",
    ]
    assert "Found app.py" in observed[1]
    assert "Find incomplete validation" in observed[0]
    assert "Found app.py" in model.observed[1][-1].content


def test_round_budget_preserves_pending_tasks_and_partial_results():
    model = ScriptedModel(
        responses=[plan(["First"]), plan(["Still needed"], summary="Partial finding")]
    )
    result = plan_research("Investigate", model, lambda task: {"summary": "partial"}, max_rounds=1)
    assert not result["complete"]
    assert result["rounds"] == 1 and result["tasks"] == ["Still needed"]
    assert result["summary"] == "Partial finding" and len(result["results"]) == 1


def test_completion_is_possible_on_last_budgeted_round():
    model = ScriptedModel(
        responses=[plan(["Find entry point"]), plan(summary="main.py", complete=True)]
    )
    result = plan_research(
        "Find entry point", model, lambda task: {"summary": "main.py"}, max_rounds=1
    )
    assert result["complete"] and result["rounds"] == 1


def test_worker_failure_is_reported_without_false_completion():
    def broken(task):
        raise RuntimeError("worker failed")

    model = ScriptedModel(responses=[plan(["Inspect"]), plan(summary="Unavailable", complete=True)])
    result = plan_research("Investigate", model, broken)
    assert not result["complete"] and result["results"][0]["error"] == "worker failed"


@pytest.mark.parametrize(
    "response",
    [
        "invalid JSON",
        '{"tasks": []}',
        '{"tasks": ["same", "same"]}',
        '{"tasks": ["   "]}',
        '{"tasks": ["work"], "complete": true}',
        '{"tasks": ["one", "two"]}',
    ],
)
def test_invalid_plan_never_starts_workers(response):
    model = ScriptedModel(responses=[AIMessage(content=response)])
    with pytest.raises(ValueError):
        plan_research(
            "Inspect", model, lambda task: pytest.fail("worker must not run"), max_parallel=1
        )


@pytest.mark.parametrize(
    "options", [{"max_rounds": 0}, {"max_rounds": 6}, {"max_parallel": 0}, {"max_parallel": 7}]
)
def test_invalid_budgets_are_rejected(options):
    with pytest.raises(ValueError):
        plan_research("Inspect", None, None, **options)


def test_planning_tool_runs_through_main_graph_and_keeps_workers_readonly(tmp_path):
    model = ScriptedModel(
        responses=[
            calls(("plan_and_execute", {"objective": "Inspect project", "max_rounds": 1})),
            plan(["Find source files"]),
            calls(("write_file", {"path": "forbidden.txt", "content": "bad"})),
            AIMessage(content="Worker could not modify files; read-only research only"),
            plan(summary="Implementation remains for the main agent", complete=True),
            AIMessage(content="Main agent received research"),
        ]
    )
    with AgentSession(
        Settings(workspace=tmp_path, subagents=True, max_parallel=1), model
    ) as session:
        result = session.graph.invoke(
            {"messages": [HumanMessage(content="Research")]}, session.config
        )
        assert result["messages"][-1].content == "Main agent received research"
        report = json.loads(
            next(m.content for m in result["messages"] if isinstance(m, ToolMessage))
        )
        assert report["rounds"] == 1 and len(report["results"]) == 1
        assert not (tmp_path / "forbidden.txt").exists()
        # Worker conversation is not merged into the main message history.
        assert len(result["messages"]) == 4
        for mode in PermissionMode:
            assert decide(mode, "plan_and_execute", {})[0] == Decision.ALLOW


def test_planning_tool_is_opt_in(session):
    assert "plan_and_execute" not in {t.name for t in session.tools}
