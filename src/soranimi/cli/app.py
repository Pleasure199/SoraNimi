"""Interactive and one-shot CLI, including durable approval resumption."""

import json
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from rich.console import Console
from rich.prompt import Confirm

from soranimi import __version__
from soranimi.agent.demo import DemoModel
from soranimi.agent.model_factory import create_model
from soranimi.cli.completer import COMMANDS
from soranimi.cli.input_handler import create_input
from soranimi.cli.renderer import Renderer
from soranimi.config import Settings
from soranimi.context.auto_compact import compact_history
from soranimi.persistence.session import SessionManager
from soranimi.persistence.time_travel import list_checkpoints, rewind
from soranimi.runtime import AgentSession
from soranimi.streaming.event_consumer import consume_events

app = typer.Typer(help="SoraNimi — 本地优先的 LangGraph 编程 Agent。")


def show_version(value: bool):
    if value:
        typer.echo(f"SoraNimi {__version__}")
        raise typer.Exit()


def run_turn(session, value, renderer, non_interactive=False):
    while True:
        try:
            events = session.graph.stream(
                value, session.config, stream_mode=["messages", "custom", "updates"]
            )
            consume_events(events, renderer)
        finally:
            renderer.finish()
        state = session.graph.get_state(session.config)
        session.transcript.append(state.values.get("messages", []))
        session.manager.touch(session.session_id)
        pending = [item for task in state.tasks for item in task.interrupts]
        if not pending:
            return True
        if non_interactive or not sys.stdin.isatty():
            renderer.console.print(
                "操作等待审批。请在交互终端使用 --resume 恢复此会话。", style="yellow"
            )
            return False
        answers = {}
        for item in pending:
            renderer.console.print(
                json.dumps(item.value, ensure_ascii=False, indent=2), markup=False
            )
            answers[item.id] = Confirm.ask(
                "允许此次操作？", default=False, console=renderer.console
            )
        value = Command(resume=answers)


def handle_slash(text, session, renderer):
    command, _, argument = text.partition(" ")
    console = renderer.console
    if command in {"/exit", "/quit"}:
        return False
    if command == "/help":
        console.print(
            "\n".join(f"{name}  {description}" for name, description in COMMANDS.items()),
            markup=False,
        )
    elif command == "/sessions":
        for row in session.manager.list():
            console.print("  ".join(row), markup=False)
    elif command == "/undo":
        result = session.snapshots.undo()
        console.print(result, markup=False)
        if result.startswith("Undid"):
            session.graph.update_state(
                session.config,
                {
                    "messages": [
                        HumanMessage(
                            content=f"[User undid a file edit: {result}. Re-read affected files before continuing.]"
                        )
                    ]
                },
                as_node="llm_call",
            )
    elif command == "/diff":
        console.print(session.snapshots.diff(), markup=False)
    elif command == "/plan":
        console.print(session.graph.get_state(session.config).values.get("plan", []))
    elif command == "/memory":
        console.print(session.memory.search(str(session.settings.workspace)))
    elif command == "/checkpoints":
        console.print(list_checkpoints(session.graph, session.config))
    elif command == "/rewind":
        session.config = rewind(session.graph, session.config, argument.strip())
        console.print("图状态已回溯。工作区文件保持当前内容；需要撤销文件时使用 /undo。")
    elif command == "/compact":
        state = session.graph.get_state(session.config)
        messages = state.values.get("messages", [])
        if not messages:
            console.print("当前没有可压缩的上下文。")
        elif state.next:
            console.print("请先完成或拒绝待执行的工具调用。")
        else:
            from langchain_core.messages import RemoveMessage
            from langgraph.graph.message import REMOVE_ALL_MESSAGES

            session.transcript.append(messages)
            summary = compact_history(messages, session.summary_model, argument)
            session.graph.update_state(
                session.config, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *summary]}
            )
            console.print("上下文已压缩，原始历史保存在本地。")
    else:
        console.print("未知命令。输入 /help 查看帮助。")
    return True


@app.command()
def main(
    prompt: str | None = typer.Argument(None, help="单次任务；省略则进入交互模式。"),
    model: str | None = typer.Option(
        None, "--model", "-m", help="provider:model，例如 deepseek:deepseek-chat"
    ),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w"),
    resume: str | None = typer.Option(None, "--resume", "-r"),
    sandbox: str = typer.Option("subprocess", "--sandbox", "-s"),
    permission: str = typer.Option(
        "accept-edits", "--permission", help="manual / accept-edits / plan / auto"
    ),
    base_url: str | None = typer.Option(None, "--base-url"),
    summary_model: str | None = typer.Option(
        None, "--summary-model", help="独立上下文摘要模型 provider:model"
    ),
    summary_base_url: str | None = typer.Option(
        None, "--summary-base-url", help="摘要模型的独立 API 地址"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    recursion_limit: int = typer.Option(100, "--recursion-limit"),
    context_tokens: int = typer.Option(50000, "--context-tokens"),
    subagents: bool = typer.Option(False, "--subagents", help="启用只读子 Agent"),
    mcp_config: Path | None = typer.Option(None, "--mcp", help="显式加载 MCP JSON 配置"),
    demo: bool = typer.Option(False, "--demo", help="运行无需 API Key 的离线只读演示"),
    sessions: bool = typer.Option(False, "--sessions", help="列出此工作区的会话后退出"),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="禁止审批提示；待审批时退出码为 2"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    ui: bool = typer.Option(False, "--ui", help="启动本地 Web 工作台；省略时保留 CLI 模式"),
    port: int = typer.Option(8765, "--port", min=1024, max=65535, help="Web 工作台端口"),
    version: bool = typer.Option(False, "--version", callback=show_version, is_eager=True),
):
    console = Console()
    try:
        load_dotenv(workspace.expanduser().resolve() / ".env", override=False)
        kwargs = {"model": model} if model else {}
        if summary_model is not None:
            kwargs["summary_model"] = summary_model
        if summary_base_url is not None:
            kwargs["summary_base_url"] = summary_base_url
        settings = Settings(
            workspace=workspace,
            permission=permission,
            sandbox=sandbox,
            data_dir=data_dir,
            base_url=base_url,
            recursion_limit=recursion_limit,
            context_tokens=context_tokens,
            subagents=subagents,
            mcp_config=mcp_config,
            **kwargs,
        )
        if ui:
            from soranimi.web.launcher import serve

            serve(settings, demo, port)
            return
        if sessions:
            manager = SessionManager(settings.data_dir / "sessions.db")
            try:
                for row in manager.list():
                    console.print("  ".join(row), markup=False)
            finally:
                manager.close()
            return
        if not prompt and not demo and not sys.stdin.isatty():
            raise ValueError("非交互终端需要提供任务参数，或使用 --demo / --sessions")
        chosen_model = DemoModel() if demo else create_model(settings)
        with AgentSession(
            settings, chosen_model, resume=resume, summary_model=chosen_model if demo else None
        ) as session:
            renderer = Renderer(console, verbose)
            console.print(f"SoraNimi {__version__} | session: {session.session_id}", markup=False)
            console.print(
                f"workspace: {settings.workspace} | mode: {settings.permission.value} | sandbox: {settings.sandbox}",
                style="dim",
                markup=False,
            )
            pending = session.graph.get_state(session.config)
            if pending.next:
                if not run_turn(session, None, renderer, non_interactive):
                    raise typer.Exit(2)
            if prompt or demo:
                if not run_turn(
                    session,
                    {"messages": [HumanMessage(content=prompt or "请演示工具调用并列出工作区。")]},
                    renderer,
                    non_interactive,
                ):
                    raise typer.Exit(2)
                return
            input_session = create_input(session.workspace, settings.data_dir / "input-history")
            while True:
                try:
                    text = input_session.prompt("soranimi> ").strip()
                    if not text:
                        continue
                    if text.startswith("/"):
                        if not handle_slash(text, session, renderer):
                            break
                    else:
                        run_turn(
                            session,
                            {"messages": [HumanMessage(content=text)]},
                            renderer,
                            non_interactive,
                        )
                except EOFError:
                    break
                except KeyboardInterrupt:
                    renderer.finish()
                    console.print("已中断。会话已保留，可使用 --resume 继续。", style="yellow")
                    break
                except GraphRecursionError:
                    console.print(
                        "达到循环步数限制。使用更高 --recursion-limit 恢复，或拆分任务。",
                        style="yellow",
                    )
                    break
                except Exception as exc:
                    console.print(str(exc), style="red", markup=False)
    except GraphRecursionError:
        console.print("达到循环步数限制；会话检查点已保存。", style="red")
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        console.print("已中断；使用显示的 session ID 恢复。", style="yellow")
        raise typer.Exit(130) from None
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"Error: {exc}", style="red", markup=False)
        raise typer.Exit(1) from None


def run():
    """Use UTF-8 for redirected Windows output as well as native consoles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    app()


if __name__ == "__main__":
    run()
