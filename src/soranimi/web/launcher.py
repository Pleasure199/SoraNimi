"""A separate UI launcher keeps existing CLI commands fully compatible."""

from pathlib import Path

import typer
from dotenv import load_dotenv

from soranimi.config import Settings

app = typer.Typer(help="SoraNimi 本地 Web 工作台")


def serve(settings, demo=False, port=8765):
    try:
        import uvicorn

        from soranimi.web.app import create_app
    except ImportError as exc:
        raise ValueError(f"安装 UI 依赖后重试：pip install -e '.[ui]' ({exc})") from exc
    typer.echo(f"SoraNimi Workbench → http://127.0.0.1:{port}")
    typer.echo("演示模式 · 不调用付费模型" if demo else f"工作区：{settings.workspace}")
    uvicorn.run(
        create_app(settings, demo),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )


@app.command()
def main(
    workspace: Path = typer.Option(Path("."), "--workspace", "-w"),
    model: str | None = typer.Option(None, "--model", "-m"),
    permission: str = typer.Option("accept-edits", "--permission"),
    sandbox: str = typer.Option("subprocess", "--sandbox"),
    port: int = typer.Option(8765, "--port", min=1024, max=65535),
    demo: bool = typer.Option(False, "--demo"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    base_url: str | None = typer.Option(None, "--base-url"),
    summary_model: str | None = typer.Option(None, "--summary-model"),
    summary_base_url: str | None = typer.Option(None, "--summary-base-url"),
    subagents: bool = typer.Option(False, "--subagents"),
    mcp_config: Path | None = typer.Option(None, "--mcp"),
):
    load_dotenv(workspace.resolve() / ".env", override=False)
    settings = Settings(
        workspace=workspace,
        permission=permission,
        sandbox=sandbox,
        data_dir=data_dir,
        base_url=base_url,
        subagents=subagents,
        mcp_config=mcp_config,
        **({"model": model} if model else {}),
        **({"summary_model": summary_model} if summary_model is not None else {}),
        **({"summary_base_url": summary_base_url} if summary_base_url is not None else {}),
    )
    serve(settings, demo, port)


def run():
    import sys

    # Reuse the CLI's UTF-8 convention without changing its command parser.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    app()


if __name__ == "__main__":
    run()
