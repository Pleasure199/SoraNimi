from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver


@contextmanager
def open_checkpointer(database: Path | str | None = None):
    """Own the saver lifetime so SQLite worker threads close cleanly."""
    if database is None:
        yield InMemorySaver()
    elif str(database).startswith(("postgres://", "postgresql://")):
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(str(database)) as saver:
            saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(path)) as saver:
            saver.setup()
            yield saver
