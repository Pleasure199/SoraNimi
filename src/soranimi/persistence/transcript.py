"""Append-only local history retained even when active context is compacted."""

import json
from pathlib import Path


class Transcript:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.seen = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    self.seen.add(json.loads(line)["fingerprint"])
                except (ValueError, KeyError):
                    continue

    def append(self, messages):
        import hashlib

        with self.path.open("a", encoding="utf-8") as stream:
            for message in messages:
                payload = message.model_dump(mode="json")
                serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
                if fingerprint not in self.seen:
                    stream.write(
                        json.dumps(
                            {"fingerprint": fingerprint, "message": payload}, ensure_ascii=False
                        )
                        + "\n"
                    )
                    self.seen.add(fingerprint)
