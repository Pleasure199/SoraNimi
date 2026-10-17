"""Session metadata, separate from LangGraph's opaque checkpoint tables."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class SessionManager:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, workspace TEXT, model TEXT, created TEXT, updated TEXT)"
        )
        self.db.commit()

    def create(self, workspace: Path, model: str) -> str:
        session_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?)",
                (session_id, str(workspace.resolve()), model, now, now),
            )
        return session_id

    def resume(self, session_id: str, workspace: Path) -> str:
        row = self.db.execute("SELECT workspace FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ValueError(f"Session not found: {session_id}")
        if Path(row[0]).resolve() != workspace.resolve():
            raise ValueError("Session belongs to a different workspace")
        self.touch(session_id)
        return session_id

    def touch(self, session_id: str):
        with self.db:
            self.db.execute(
                "UPDATE sessions SET updated=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), session_id),
            )

    def list(self):
        return self.db.execute(
            "SELECT id, model, updated FROM sessions ORDER BY updated DESC"
        ).fetchall()

    def close(self):
        self.db.close()
