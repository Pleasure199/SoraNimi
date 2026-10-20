"""Loopback-only authenticated API and packaged, buildless browser assets."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from soranimi import __version__
from soranimi.documents import MAX_FILE_BYTES, SUPPORTED_SUFFIXES, validate_filename
from soranimi.permission.modes import PermissionMode
from soranimi.persistence.attachments import AttachmentStore
from soranimi.tools.read_file import decode_text
from soranimi.web.service import BusyError, Workbench, encode_event
from soranimi.workspace import Workspace


class SessionInput(BaseModel):
    model: str | None = Field(default=None, max_length=200)
    permission: PermissionMode | None = None
    sandbox: str | None = None
    subagent_model: str | None = Field(default=None, max_length=200)
    subagents: StrictBool | None = None


class RunInput(BaseModel):
    message: str | None = Field(default=None, max_length=30000)
    approvals: dict[str, StrictBool] | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)


def create_app(settings, demo=False, model_factory=None):
    service = Workbench(settings, demo, model_factory)
    token = secrets.token_urlsafe(32)
    upload_gate = asyncio.Semaphore(2)
    assets = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.shutdown()

    app = FastAPI(
        title="SoraNimi Workbench",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.workbench = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin and origin != expected:
                return JSONResponse({"detail": "只允许同源本地请求"}, status_code=403)
            if (
                request.cookies.get("soranimi_local") != token
                or request.headers.get("x-soranimi-request") != "1"
            ):
                return JSONResponse({"detail": "请从本地工作台打开此接口"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        if request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def bad_request(request, exc):
        return JSONResponse(
            {"detail": str(exc)}, status_code=409 if isinstance(exc, BusyError) else 400
        )

    @app.exception_handler(LookupError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/")
    def index():
        response = FileResponse(assets / "index.html")
        response.set_cookie("soranimi_local", token, httponly=True, samesite="strict")
        return response

    @app.get("/api/bootstrap")
    def bootstrap():
        active = service.active_job
        return {
            "version": __version__,
            "workspace": {"name": settings.workspace.name, "path": str(settings.workspace)},
            "defaults": {
                "model": settings.model,
                "permission": settings.permission.value,
                "sandbox": settings.sandbox,
                "subagent_model": settings.subagent_model,
                "subagents": settings.subagents,
            },
            "models": [
                {"id": alias, "model": profile["model"]}
                for alias, profile in settings.model_profiles.items()
            ],
            "demo": demo,
            "uploads": {"max_bytes": MAX_FILE_BYTES, "extensions": sorted(SUPPORTED_SUFFIXES)},
            "sessions": service.sessions(),
            "active": {"id": active.id, "session_id": active.session_id}
            if active and not active.done
            else None,
        }

    @app.get("/api/sessions")
    def sessions():
        return service.sessions()

    @app.post("/api/sessions", status_code=201)
    def new_session(body: SessionInput):
        return service.create(
            body.model, body.permission, body.sandbox, body.subagent_model, body.subagents
        )

    @app.get("/api/sessions/{session_id}")
    def inspect(session_id: str):
        return service.inspect(session_id)

    @app.post("/api/sessions/{session_id}/run", status_code=202)
    def run(session_id: str, body: RunInput):
        message = body.message.strip() if body.message else None
        job = service.start(session_id, message, body.approvals, body.attachment_ids)
        return {"job_id": job.id, "session_id": session_id}

    @app.post("/api/sessions/{session_id}/attachments", status_code=201)
    async def upload(session_id: str, request: Request, filename: str):
        # Stream the raw file, not a multipart body buffered by the web framework.
        service.metadata(session_id)
        validate_filename(filename)
        if request.headers.get("content-length"):
            try:
                length = int(request.headers["content-length"])
            except ValueError as exc:
                raise ValueError("文件长度无效") from exc
            if length > MAX_FILE_BYTES:
                raise HTTPException(413, "文件超过 20 MiB 限制")
        async with upload_gate:
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_FILE_BYTES:
                    raise HTTPException(413, "文件超过 20 MiB 限制")
                data.extend(chunk)
            return await asyncio.to_thread(
                AttachmentStore(settings.data_dir, session_id).add, filename, bytes(data)
            )

    @app.get("/api/sessions/{session_id}/attachments/{attachment_id}")
    def attachment(session_id: str, attachment_id: str, offset: int = 0, limit: int = 200):
        from soranimi.tools.read_attachment import document_page

        service.metadata(session_id)
        item = AttachmentStore(settings.data_dir, session_id).read(attachment_id)
        text = item.pop("text")
        return {**item, "content": document_page(text, offset, limit, 24000)}

    @app.post("/api/sessions/{session_id}/undo")
    def undo(session_id: str):
        return service.undo(session_id)

    @app.post("/api/jobs/{job_id}/stop")
    def stop(job_id: str):
        job = service.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        job.stop.set()
        return {"message": "将在当前步骤完成后暂停"}

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request, after: int = 0):
        job = service.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在，请重新加载会话")

        async def stream():
            cursor = max(0, after)
            while True:
                if await request.is_disconnected():
                    break
                batch, done = await asyncio.to_thread(job.wait, cursor)
                for event in batch:
                    yield encode_event(event)
                    cursor = event["id"]
                if done:
                    break
                if not batch:
                    yield ": keepalive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    @app.get("/api/files")
    def files():
        workspace = Workspace(settings.workspace, settings.data_dir)
        output = []
        for path in workspace.files():
            output.append({"path": workspace.relative(path), "size": path.stat().st_size})
            if len(output) >= 1500:
                break
        return {"files": output, "truncated": len(output) == 1500}

    @app.get("/api/file")
    def file(path: str):
        target = Workspace(settings.workspace, settings.data_dir).resolve(path)
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        from soranimi.documents import DOCUMENT_SUFFIXES, parse_document
        from soranimi.tools.read_attachment import document_page

        if target.suffix.lower() in DOCUMENT_SUFFIXES | {".doc", ".ppt"}:
            if target.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("文档超过 20 MiB 限制")
            parsed = parse_document(target.read_bytes(), target.name)
            return {
                "path": path,
                "content": document_page(parsed["text"], 0, 200, 24000),
                "truncated": parsed["truncated"],
            }
        if target.stat().st_size > 1024 * 1024:
            raise ValueError("预览只支持 1 MiB 以内的文本文件")
        try:
            content, _ = decode_text(target.read_bytes())
            if "\x00" in content:
                raise ValueError("二进制文件无法以文本方式预览")
        except UnicodeError as exc:
            raise ValueError("此文件不是支持的文本编码") from exc
        return {"path": path, "content": content}

    app.mount("/assets", StaticFiles(directory=assets), name="assets")
    return app
