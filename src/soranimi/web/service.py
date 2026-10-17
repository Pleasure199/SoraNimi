"""Thread-owned Agent sessions and replayable browser event streams."""

import json
import sqlite3
import threading
from collections import deque
from dataclasses import replace
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from soranimi.agent.demo import DemoModel
from soranimi.agent.graph import build_graph
from soranimi.agent.model_factory import create_model
from soranimi.context.token_estimator import estimate_tokens
from soranimi.persistence.checkpointer import open_checkpointer
from soranimi.persistence.session import SessionManager
from soranimi.persistence.transcript import Transcript
from soranimi.runtime import AgentSession
from soranimi.tools.read_file import decode_text
from soranimi.workspace import Workspace


class BusyError(ValueError):
    pass


class Job:
    def __init__(self, session_id):
        self.id = uuid4().hex
        self.session_id = session_id
        self.events = deque(maxlen=4000)
        self.sequence = 0
        self.done = False
        self.stop = threading.Event()
        self.condition = threading.Condition()

    def emit(self, event, data):
        with self.condition:
            self.sequence += 1
            self.events.append({"id": self.sequence, "event": event, "data": data})
            self.condition.notify_all()

    def wait(self, after):
        with self.condition:
            if not self.done and self.sequence <= after:
                self.condition.wait(timeout=1)
            return [e for e in self.events if e["id"] > after], self.done

    def finish(self):
        with self.condition:
            self.done = True
            self.condition.notify_all()


def message_view(message):
    """Only display text blocks; never send persisted base64 images to a sidebar."""
    if isinstance(message.content, str):
        text = message.content
    else:
        text = "\n".join(
            block.get("text", "[图片]") if isinstance(block, dict) else str(block)
            for block in message.content
        )
    result = {"id": message.id, "role": message.type, "text": text[:80000]}
    if isinstance(message, AIMessage):
        result["calls"] = message.tool_calls
    if isinstance(message, ToolMessage):
        result.update(name=message.name, call_id=message.tool_call_id, status=message.status)
    return result


class Workbench:
    def __init__(self, settings, demo=False, model_factory=None):
        self.settings = settings
        self.demo = demo
        self.custom_model_factory = model_factory is not None
        self.model_factory = model_factory or (
            lambda settings: DemoModel() if self.demo else create_model(settings)
        )
        self.lock = threading.Lock()
        self.jobs = {}
        self.active_job = None
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        manager = SessionManager(self.settings.data_dir / "sessions.db")
        manager.close()
        with self.database() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS web_sessions (id TEXT PRIMARY KEY, title TEXT, permission TEXT, sandbox TEXT)"
            )

    def database(self):
        # Connection lifetime belongs to the request/worker thread using it.
        from contextlib import contextmanager

        @contextmanager
        def connection():
            db = sqlite3.connect(self.settings.data_dir / "sessions.db", timeout=10)
            try:
                with db:
                    yield db
            finally:
                db.close()

        return connection()

    def metadata(self, session_id):
        with self.database() as db:
            row = db.execute(
                "SELECT s.id,s.model,s.updated,w.title,w.permission,w.sandbox FROM sessions s LEFT JOIN web_sessions w ON w.id=s.id WHERE s.id=? AND s.workspace=?",
                (session_id, str(self.settings.workspace)),
            ).fetchone()
        if row is None:
            raise LookupError("找不到此工作区中的会话")
        result = dict(
            zip(("id", "model", "updated", "title", "permission", "sandbox"), row, strict=True)
        )
        # Old CLI metadata does not record permission mode. Import it with a
        # conservative policy so rejecting an old approval cannot bypass it.
        result["permission"] = result["permission"] or "manual"
        return result

    def session_settings(self, session_id):
        meta = self.metadata(session_id)
        return replace(
            self.settings,
            model=meta["model"],
            permission=meta["permission"] or self.settings.permission,
            sandbox=meta["sandbox"] or self.settings.sandbox,
        )

    def sessions(self):
        with self.database() as db:
            rows = db.execute(
                "SELECT s.id,s.model,s.updated,w.title,w.permission,w.sandbox FROM sessions s LEFT JOIN web_sessions w ON w.id=s.id WHERE s.workspace=? ORDER BY s.updated DESC LIMIT 100",
                (str(self.settings.workspace),),
            ).fetchall()
        return [
            dict(
                zip(("id", "model", "updated", "title", "permission", "sandbox"), row, strict=True)
            )
            for row in rows
        ]

    def create(self, model=None, permission=None, sandbox=None):
        selected = replace(
            self.settings,
            model=model or self.settings.model,
            permission=permission or self.settings.permission,
            sandbox=sandbox or self.settings.sandbox,
        )
        manager = SessionManager(selected.data_dir / "sessions.db")
        try:
            session_id = manager.create(selected.workspace, selected.model)
        finally:
            manager.close()
        with self.database() as db:
            db.execute(
                "INSERT INTO web_sessions VALUES (?,?,?,?)",
                (session_id, "新会话", selected.permission.value, selected.sandbox),
            )
        return self.inspect(session_id)

    def inspect(self, session_id):
        meta = self.metadata(session_id)
        settings = self.session_settings(session_id)
        # Compiling a view graph reads checkpoints without creating a model
        # client, touching timestamps, or invoking any tools.
        with open_checkpointer(settings.data_dir / "checkpoints.db") as saver:
            graph = build_graph(
                DemoModel(),
                [],
                settings,
                saver,
                Transcript(settings.data_dir / "transcripts" / f"{session_id}.jsonl"),
            )
            state = graph.get_state({"configurable": {"thread_id": session_id}})
        messages = state.values.get("messages", [])
        interrupts = [
            {"id": item.id, **item.value} for task in state.tasks for item in task.interrupts
        ]
        pending = [item for item in interrupts if item.get("type") == "approval"]
        paused = any(item.get("type") == "pause" for item in interrupts)
        job = self.active_job
        running = job is not None and not job.done and job.session_id == session_id
        return {
            "session": meta,
            "messages": [message_view(m) for m in messages if m.type in {"human", "ai", "tool"}],
            "plan": state.values.get("plan", []),
            "pending": pending,
            "paused": paused,
            "resumable": bool(state.next),
            "running": running,
            "job_id": job.id if running else None,
            "event_cursor": job.sequence if running else 0,
            "tokens": estimate_tokens(messages),
            "changes": self.changes(session_id),
        }

    def changes(self, session_id):
        database = self.settings.data_dir / "files.db"
        if not database.exists():
            return []
        with sqlite3.connect(database) as db:
            rows = db.execute(
                "SELECT path,before FROM edits WHERE session=? AND undone=0 ORDER BY id",
                (session_id,),
            ).fetchall()
        originals = {}
        for path, before in rows:
            originals.setdefault(path, before)
        workspace = Workspace(self.settings.workspace, self.settings.data_dir)
        output = []
        import difflib

        for path, before in originals.items():
            try:
                target = workspace.resolve(path)
                after = target.read_bytes() if target.exists() else b""
                old_text = decode_text(before)[0] if before else ""
                new_text = decode_text(after)[0] if after else ""
                diff = list(
                    difflib.unified_diff(
                        old_text.splitlines(True),
                        new_text.splitlines(True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                    )
                )
                if not diff:
                    continue
                output.append(
                    {
                        "path": path,
                        "status": "added" if before is None else "modified",
                        "added": sum(
                            line.startswith("+") and not line.startswith("+++") for line in diff
                        ),
                        "removed": sum(
                            line.startswith("-") and not line.startswith("---") for line in diff
                        ),
                        "diff": "".join(diff)[:60000],
                    }
                )
            except (OSError, ValueError, UnicodeError):
                output.append(
                    {
                        "path": path,
                        "status": "unavailable",
                        "added": 0,
                        "removed": 0,
                        "diff": "无法读取此文件的差异。",
                    }
                )
        return output

    def start(self, session_id, message=None, approvals=None):
        selected = self.session_settings(session_id)
        if not self.lock.acquire(blocking=False):
            raise BusyError("工作区已有任务运行，请等待完成或暂停后继续")
        try:
            state = self.inspect(session_id)
            if state["pending"]:
                expected = {item["id"] for item in state["pending"]}
                if message or approvals is None or set(approvals) != expected:
                    raise ValueError("请先审批当前操作；审批内容已变化时需要刷新会话")
            elif approvals is not None:
                raise ValueError("当前没有等待审批的操作")
            elif state["resumable"] and message:
                raise ValueError("当前任务已暂停，请先继续执行")
            elif not state["resumable"] and not message:
                raise ValueError("请输入任务内容")
            if message:
                with self.database() as db:
                    db.execute(
                        "INSERT OR IGNORE INTO web_sessions VALUES (?,?,?,?)",
                        (session_id, "新会话", selected.permission.value, selected.sandbox),
                    )
                    db.execute(
                        "UPDATE web_sessions SET title=? WHERE id=? AND title='新会话'",
                        (message.strip().splitlines()[0][:60], session_id),
                    )
            # Retain recent streams so reconnection can replay them.
            for key in list(self.jobs)[:-24]:
                if self.jobs[key].done:
                    del self.jobs[key]
            job = Job(session_id)
            self.jobs[job.id] = job
            self.active_job = job
            thread = threading.Thread(
                target=self._run,
                args=(job, selected, message, approvals),
                daemon=True,
                name=f"soranimi-{job.id[:8]}",
            )
            thread.start()
            return job
        except BaseException:
            self.lock.release()
            raise

    def _run(self, job, settings, message, approvals):
        try:
            model = self.model_factory(settings)
            with AgentSession(
                settings,
                model,
                resume=job.session_id,
                stop_event=job.stop,
                model_factory=self.model_factory
                if self.demo or self.custom_model_factory
                else None,
            ) as session:
                value = (
                    Command(resume=approvals)
                    if approvals is not None
                    else {"messages": [HumanMessage(content=message)]}
                    if message
                    else None
                )
                if value is None:
                    snapshot = session.graph.get_state(session.config)
                    pauses = {
                        item.id: True
                        for task in snapshot.tasks
                        for item in task.interrupts
                        if item.value.get("type") == "pause"
                    }
                    if pauses:
                        value = Command(resume=pauses)
                streamed = False
                events = session.graph.stream(
                    value, session.config, stream_mode=["messages", "custom", "updates"]
                )
                try:
                    for mode, data in events:
                        if mode == "messages":
                            chunk, metadata = data
                            if metadata.get(
                                "langgraph_node"
                            ) == "llm_call" and "subagent" not in metadata.get("tags", []):
                                if chunk.text:
                                    job.emit("token", {"text": chunk.text})
                                    streamed = True
                        elif mode == "custom" and data.get("event") in {"tool_start", "tool_end"}:
                            job.emit("tool", data)
                        elif mode == "updates":
                            for node, update in data.items():
                                if not isinstance(update, dict):
                                    continue
                                if node == "llm_call":
                                    for item in update.get("messages", []):
                                        if isinstance(item, AIMessage):
                                            job.emit(
                                                "assistant",
                                                {**message_view(item), "streamed": streamed},
                                            )
                                    streamed = False
                                elif node == "tools":
                                    for item in update.get("messages", []):
                                        if isinstance(item, ToolMessage):
                                            job.emit("result", message_view(item))
                                if "plan" in update:
                                    job.emit("plan", {"steps": update["plan"]})
                finally:
                    events.close()
                session.manager.touch(session.session_id)
            state = self.inspect(job.session_id)
            state["running"] = False
            state["job_id"] = None
            job.emit("complete", {"state": state, "stopped": state["paused"]})
        except Exception as exc:
            job.emit("error", {"message": str(exc)[:3000]})
        finally:
            self.lock.release()
            job.finish()

    def undo(self, session_id):
        self.metadata(session_id)
        if not self.lock.acquire(blocking=False):
            raise BusyError("任务运行期间不能撤销文件")
        try:
            state = self.inspect(session_id)
            if state["resumable"]:
                raise ValueError("请先完成当前任务或审批，再撤销文件")
            with AgentSession(
                self.session_settings(session_id), DemoModel(), resume=session_id
            ) as session:
                result = session.snapshots.undo()
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
            return {"message": result, "state": self.inspect(session_id)}
        finally:
            self.lock.release()

    def shutdown(self):
        if self.active_job and not self.active_job.done:
            self.active_job.stop.set()


def encode_event(event):
    return f"id: {event['id']}\nevent: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
