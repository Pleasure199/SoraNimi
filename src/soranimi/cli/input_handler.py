from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from soranimi.cli.completer import AgentCompleter


def create_input(workspace, history_path):
    bindings = KeyBindings()

    @bindings.add("enter")
    def submit(event):
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(
        history=FileHistory(str(history_path)),
        completer=AgentCompleter(workspace),
        key_bindings=bindings,
        multiline=True,
    )
