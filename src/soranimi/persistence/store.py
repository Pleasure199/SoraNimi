"""Durable namespaced preferences without a required embedding service."""

import json
import sqlite3
from pathlib import Path


class MemoryStore:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS memories (namespace TEXT, key TEXT, value TEXT, PRIMARY KEY(namespace,key))"
        )
        self.db.commit()

    def put(self, namespace: str, key: str, value: dict):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO memories VALUES (?,?,?)",
                (namespace, key, json.dumps(value, ensure_ascii=False)),
            )

    def search(self, namespace: str, query: str = "") -> list[dict]:
        rows = self.db.execute(
            "SELECT key,value FROM memories WHERE namespace=? ORDER BY key LIMIT 100", (namespace,)
        ).fetchall()
        return [
            {"key": key, "value": json.loads(value)}
            for key, value in rows
            if not query or query.casefold() in (key + value).casefold()
        ]

    def close(self):
        self.db.close()
