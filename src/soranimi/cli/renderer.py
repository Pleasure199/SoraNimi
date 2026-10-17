from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown


class Renderer:
    def __init__(self, console: Console | None = None, verbose: bool = False):
        self.console = console or Console()
        self.verbose = verbose
        self.buffer = ""
        self.live = None

    def token(self, text: str):
        self.buffer += text
        if self.console.is_terminal:
            if self.live is None:
                self.live = Live(
                    Markdown(self.buffer),
                    console=self.console,
                    refresh_per_second=8,
                    vertical_overflow="visible",
                )
                self.live.start()
            self.live.update(Markdown(self.buffer))

    def finish(self):
        if self.live is not None:
            self.live.update(Markdown(self.buffer))
            self.live.stop()
            self.live = None
        elif self.buffer:
            self.console.print(Markdown(self.buffer))
        self.buffer = ""

    def tool_event(self, event):
        self.finish()
        label = "Running" if event["event"] == "tool_start" else event.get("status", "done")
        self.console.print(f"  {label}: {event['name']}", style="dim", markup=False)

    def tool_result(self, message):
        if self.verbose or message.status == "error":
            text = str(message.content)
            if isinstance(message.content, list):
                text = "\n".join(
                    block.get("text", "[image]") if isinstance(block, dict) else str(block)
                    for block in message.content
                )
            self.console.print(
                text[:6000], style="red" if message.status == "error" else "dim", markup=False
            )
