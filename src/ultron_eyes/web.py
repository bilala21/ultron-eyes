"""Web UI and JSON API (FastAPI)."""

from __future__ import annotations

import hmac
from datetime import datetime
from importlib import resources
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from .config import Config
from .query import interpret
from .store import Filter, Store


class Review(BaseModel):
    correct: bool
    note: str | None = None


def allowed_hosts(cfg: Config) -> list[str]:
    """Host header allowlist. Blocks DNS rebinding: a hostile website that points its own
    domain at 127.0.0.1 still sends its own domain in the Host header and gets rejected."""
    hosts = ["127.0.0.1", "localhost", "[::1]"]
    if cfg.web_host not in ("127.0.0.1", "localhost", "0.0.0.0", "::"):
        hosts.append(cfg.web_host)
    hosts += cfg.web_allowed_hosts
    return hosts


def create_app(cfg: Config, store: Store, pipeline=None) -> FastAPI:
    app = FastAPI(title="Ultron Eyes", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts(cfg))

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        """Reject cross-site POSTs (CSRF), even when no token is configured."""
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            site = request.headers.get("sec-fetch-site")
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if site not in (None, "same-origin", "none") or (origin and urlsplit(origin).netloc != host):
                return JSONResponse({"detail": "cross-site request blocked"}, status_code=403)
        return await call_next(request)

    def auth(request: Request) -> None:
        if not cfg.web_token:
            return
        given = request.headers.get("x-ultron-token") or request.query_params.get("key") or request.cookies.get("ultron_key")
        if not given or not hmac.compare_digest(given, cfg.web_token):
            raise HTTPException(401, "missing or wrong key")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        html = resources.files("ultron_eyes").joinpath("static/index.html").read_text()
        resp = HTMLResponse(html)
        if cfg.web_token and request.query_params.get("key") == cfg.web_token:
            resp.set_cookie("ultron_key", cfg.web_token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 365)
        return resp

    @app.get("/api/search", dependencies=[Depends(auth)])
    def search(q: str = "", limit: int = 120):
        parsed = interpret(q, cfg.cameras, cfg.known_plates, cfg.gemini_api_key, gemini_model=cfg.gemini_model)
        parsed.filter.limit = min(limit, 500)
        f = parsed.filter
        return {
            "question": q,
            "understood": parsed.understood,
            "warnings": parsed.warnings,
            "source": parsed.source,
            "window": [datetime.fromtimestamp(t).isoformat(timespec="minutes") if t else None for t in (f.start, f.end)],
            "events": [_public(e) for e in store.search(f)],
        }

    @app.get("/api/events/{event_id}", dependencies=[Depends(auth)])
    def event(event_id: int):
        e = store.get(event_id)
        if not e:
            raise HTTPException(404)
        return _public(e)

    @app.get("/api/events/{event_id}/{kind}", dependencies=[Depends(auth)])
    def image(event_id: int, kind: str):
        e = store.get(event_id)
        if not e or kind not in ("snapshot", "thumb"):
            raise HTTPException(404)
        path = (store.data_dir / e[kind]).resolve()
        if store.data_dir.resolve() not in path.parents or not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/events/{event_id}/review", dependencies=[Depends(auth)])
    def review(event_id: int, body: Review):
        if not store.get(event_id):
            raise HTTPException(404)
        store.review(event_id, body.correct, body.note)
        return {"ok": True}

    @app.get("/api/stats", dependencies=[Depends(auth)])
    def stats():
        return store.stats()

    @app.get("/api/cameras", dependencies=[Depends(auth)])
    def cameras():
        return [{"channel": c.channel, "name": c.name} for c in cfg.cameras]

    @app.post("/api/trigger/{channel}", dependencies=[Depends(auth)])
    def trigger(channel: int):
        if pipeline is None or not cfg.camera(channel):
            raise HTTPException(404)
        return {"queued": pipeline.trigger(channel)}

    return app


def _public(e: dict) -> dict:
    """What the browser needs; file paths stay on the server."""
    return {
        "id": e["id"],
        "time": datetime.fromtimestamp(e["ts"]).isoformat(timespec="seconds"),
        "camera": e["camera"],
        "camera_name": e["camera_name"],
        "labels": e["labels"],
        "objects": e["objects"],
        "plates": e["plates"],
        "latency_ms": e["latency_ms"],
        "review": e["review"],
    }
