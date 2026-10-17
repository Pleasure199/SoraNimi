"""Snapshots verify current contents before undoing an agent edit."""

import difflib
import sqlite3
import threading
from pathlib import Path

from soranimi.workspace import Workspace, WorkspaceError, atomic_write, digest


class SnapshotStore:
    def __init__(self, database: Path, workspace: Workspace, session_id: str):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.lock = threading.RLock()
        self.workspace = workspace
        self.session_id = session_id
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS edits (id INTEGER PRIMARY KEY, session TEXT, path TEXT, before BLOB, after BLOB, undone INTEGER DEFAULT 0)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS reads (session TEXT, path TEXT, hash TEXT, PRIMARY KEY(session, path))"
        )
        self.db.commit()
        workspace.reads.update(
            dict(self.db.execute("SELECT path, hash FROM reads WHERE session=?", (session_id,)))
        )

    def record_read(self, path: Path, data: bytes):
        key = self.workspace.relative(path)
        self.workspace.reads[key] = digest(data)
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO reads VALUES (?, ?, ?)",
                (self.session_id, key, digest(data)),
            )

    def write(self, path: Path, after: bytes, expected: bytes | None):
        key = self.workspace.relative(path)
        with self.lock:
            current = path.read_bytes() if path.exists() else None
            if current != expected:
                raise WorkspaceError("File changed since read; read_file again before editing")
            # Save first so a process crash never silently loses the previous contents.
            with self.db:
                cursor = self.db.execute(
                    "INSERT INTO edits(session,path,before,after) VALUES (?,?,?,?)",
                    (self.session_id, key, current, after),
                )
            try:
                atomic_write(path, after)
            except BaseException:
                with self.db:
                    self.db.execute("DELETE FROM edits WHERE id=?", (cursor.lastrowid,))
                raise
            self.record_read(path, after)

    def undo(self) -> str:
        with self.lock:
            row = self.db.execute(
                "SELECT id,path,before,after FROM edits WHERE session=? AND undone=0 ORDER BY id DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            if row is None:
                return "No file edits to undo."
            edit_id, key, before, after = row
            path = self.workspace.resolve(key)
            if not path.exists() or path.read_bytes() != after:
                raise WorkspaceError(
                    "File was changed outside this edit; undo refused to avoid losing work"
                )
            if before is None:
                path.unlink()
                self.workspace.reads.pop(key, None)
                with self.db:
                    self.db.execute(
                        "DELETE FROM reads WHERE session=? AND path=?", (self.session_id, key)
                    )
            else:
                atomic_write(path, before)
                self.record_read(path, before)
            with self.db:
                self.db.execute("UPDATE edits SET undone=1 WHERE id=?", (edit_id,))
            return f"Undid {key}"

    def diff(self) -> str:
        rows = self.db.execute(
            "SELECT path,before FROM edits WHERE session=? AND undone=0 ORDER BY id",
            (self.session_id,),
        ).fetchall()
        originals = {}
        for key, before in rows:
            originals.setdefault(key, before)
        output = []
        for key, before in originals.items():
            path = self.workspace.resolve(key)
            after = path.read_bytes() if path.exists() else b""
            output.extend(
                difflib.unified_diff(
                    (before or b"").decode("utf-8", errors="replace").splitlines(True),
                    after.decode("utf-8", errors="replace").splitlines(True),
                    fromfile=f"a/{key}",
                    tofile=f"b/{key}",
                )
            )
        return "".join(output) or "No file changes."

    def close(self):
        self.db.close()
