"""FastAPI application: chat API + health + static frontend."""
from __future__ import annotations

import json
import secrets
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.chat import ChatEngine
from app.config import load_settings
from app.database import VectorDB
from app.embedder import Embedder


class HistoryMessage(BaseModel):
    """Only real conversation turns; a client must not inject system/tool roles."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=200)
    mode: Literal["normal", "deep"] = "normal"


class RateLimiter:
    """Per-client sliding window, so one visitor cannot spend the whole quota."""

    def __init__(self, per_minute: int, max_clients: int = 2048):
        self.per_minute = per_minute
        self.max_clients = max_clients
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.time()
        with self._lock:
            if len(self._hits) > self.max_clients:
                for key in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
                    self._hits.pop(key, None)
            hits = self._hits.setdefault(client, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


class ChatResponse(BaseModel):
    reply: str
    mode: str = "normal"
    steps: list[dict] = Field(default_factory=list)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            settings = app.state.settings
            db = VectorDB(settings.db_path, dim=Embedder.dim)
            app.state.db = db
            app.state.db_chunks = db.count()
            app.state.db_ready = True
            embedder = Embedder(cache_dir=settings.model_cache_dir)
            app.state.embedder = embedder
            # The model loads lazily; do it now so /api/health cannot claim
            # "ready" for an embedder that will fail on the first question.
            embedder.ensure_loaded()
            app.state.embedder_ready = True
            app.state.engine = ChatEngine(settings, db, embedder)
        except Exception:
            app.state.engine = None
        yield
        db = getattr(app.state, "db", None)
        if db is not None:
            db.close()

    app = FastAPI(title="Pom-Pom Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = load_settings()
    app.state.engine = None
    app.state.db_chunks = 0
    app.state.db_ready = False
    app.state.embedder_ready = False
    app.state.model_configured = bool(app.state.settings.api_key)
    app.state.debug_enabled = app.state.settings.debug
    app.state.debug_log: deque = deque(maxlen=100)
    app.state.started_at = time.time()
    app.state.rate_limiter = RateLimiter(app.state.settings.rate_limit_per_minute)

    def guard_request(request: Request) -> None:
        """Access token (when configured) plus a per-client rate limit."""
        settings = app.state.settings
        if settings.access_token:
            provided = request.headers.get("x-access-token", "").strip()
            authorization = request.headers.get("authorization", "")
            if not provided and authorization.lower().startswith("bearer "):
                provided = authorization[len("bearer ") :].strip()
            if not secrets.compare_digest(provided, settings.access_token):
                raise HTTPException(status_code=401, detail="access token required")
        client = request.client.host if request.client else "unknown"
        if not app.state.rate_limiter.check(client):
            raise HTTPException(
                status_code=429, detail="too many requests, please slow down"
            )

    def check_payload(req: ChatRequest) -> None:
        """Bound what one request can push into the model."""
        settings = app.state.settings
        if len(req.message) > settings.max_message_chars:
            raise HTTPException(status_code=413, detail="message is too long")
        if len(req.history) > settings.max_history_messages:
            raise HTTPException(status_code=413, detail="history has too many messages")
        total = sum(len(item.content) for item in req.history)
        if total > settings.max_history_chars:
            raise HTTPException(status_code=413, detail="history is too long")

    def record_debug(diag: dict) -> None:
        item = dict(diag)
        item["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        app.state.debug_log.append(item)
        if app.state.debug_enabled:
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
            try:
                print(f"[debug] {json.dumps(item, ensure_ascii=False)}", flush=True)
            except Exception:
                # Debug logging must never break a chat response.
                pass

    app.state.record_debug = record_debug

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, _: None = Depends(guard_request)) -> ChatResponse:
        check_payload(req)
        engine = app.state.engine
        if engine is None:
            raise HTTPException(status_code=503, detail="chat engine not ready")
        reply, diag = engine.reply_with_diagnostics(
            req.message, req.history, mode=req.mode
        )
        record_debug(diag)
        return ChatResponse(
            reply=reply,
            mode=str(diag.get("mode") or req.mode),
            steps=list(diag.get("steps") or []),
        )

    @app.post("/api/chat/stream")
    def chat_stream(req: ChatRequest, _: None = Depends(guard_request)) -> StreamingResponse:
        check_payload(req)
        engine = app.state.engine
        if engine is None:
            raise HTTPException(status_code=503, detail="chat engine not ready")

        def events():
            try:
                for event in engine.stream_reply(
                    req.message,
                    req.history,
                    mode=req.mode,
                    on_diagnostics=record_debug,
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # never leave the stream without a terminator
                payload = {
                    "type": "reply",
                    "reply": "呜……列车广播好像出故障了，开拓者稍等一下再试试哦！",
                    "mode": req.mode,
                    "fallback": True,
                    "insufficient": False,
                    "rounds": 0,
                    "steps": [],
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                record_debug({"question": req.message, "fallback": True,
                              "mode": req.mode, "last_error": f"{type(exc).__name__}: {exc}"})
            else:
                pass  # diagnostics are handed over by the engine itself
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/health")
    def health() -> dict:
        db_ready = bool(app.state.db_ready)
        embedder_ready = bool(app.state.embedder_ready)
        chunks = int(app.state.db_chunks or 0)
        healthy = db_ready and embedder_ready and chunks > 0
        return {
            "status": "ok" if healthy else "degraded",
            "db_chunks": chunks,
            "db_ready": db_ready,
            "embedder_ready": embedder_ready,
            "model_configured": bool(app.state.model_configured),
        }

    @app.get("/api/debug/info")
    def debug_info() -> dict:
        if not app.state.debug_enabled:
            raise HTTPException(status_code=404, detail="debug disabled")
        settings = app.state.settings
        return {
            "model": settings.model,
            "base_url": settings.base_url,
            "api_key_configured": bool(settings.api_key),
            "db_chunks": app.state.db_chunks,
            "db_ready": app.state.db_ready,
            "embedder_ready": app.state.embedder_ready,
            "web_search_enabled": settings.web_search_enabled,
            "web_search_sources": list(settings.web_search_sources),
            "bocha_configured": bool(settings.bocha_api_key),
            "top_k": settings.top_k,
            "deep_think_max_steps": settings.deep_think_max_steps,
            "deep_think_timeout_seconds": settings.deep_think_timeout_seconds,
            "debug_log_size": len(app.state.debug_log),
            "uptime_seconds": round(time.time() - app.state.started_at),
        }

    @app.get("/api/debug/logs")
    def debug_logs() -> list:
        if not app.state.debug_enabled:
            raise HTTPException(status_code=404, detail="debug disabled")
        return list(app.state.debug_log)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
