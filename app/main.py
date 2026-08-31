from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .auth import COOKIE_NAME, MAX_AGE, create_session_token, password_ok, require_auth
from .config import settings
from .db import Recording, Source, db_session, init_db
from .storage import disk_state
from .utils import human_bytes
from .workers import manager

BASE = Path(__file__).parent
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,100}$")
LOGIN_FAILURES: dict[str, deque[float]] = defaultdict(deque)
LOGIN_WINDOW = 10 * 60
LOGIN_MAX_FAILURES = 6


class LoginBody(BaseModel):
    password: str


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=180)
    platform: str = "chaturbate"
    quality: str = "best"
    consent_confirmed: bool


class SourcePatch(BaseModel):
    name: str | None = None
    slug: str | None = None
    quality: str | None = None
    enabled: bool | None = None
    consent_confirmed: bool | None = None


def _normalize_slug(slug: str) -> str:
    value = slug.strip()
    if "chaturbate.com/" in value.lower():
        # Keep original casing after the hostname split; usernames are treated verbatim.
        lower = value.lower()
        idx = lower.index("chaturbate.com/") + len("chaturbate.com/")
        value = value[idx:]
    value = value.split("?", 1)[0].split("#", 1)[0].strip("/")
    if "/" in value:
        value = value.split("/", 1)[0]
    if not USERNAME_RE.fullmatch(value):
        raise HTTPException(400, "Username Chaturbate non valido")
    return value


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_blocked(ip: str) -> bool:
    now = time.time()
    q = LOGIN_FAILURES[ip]
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    return len(q) >= LOGIN_MAX_FAILURES


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()
    init_db()
    await manager.start()
    yield
    await manager.stop()


app = FastAPI(title="LiveVault", version="1.1.2", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(BASE / "static" / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.post("/api/login")
def login(body: LoginBody, response: Response, request: Request):
    ip = _client_ip(request)
    if _login_blocked(ip):
        raise HTTPException(status_code=429, detail="Troppi tentativi. Riprova tra qualche minuto.")
    if not password_ok(body.password):
        LOGIN_FAILURES[ip].append(time.time())
        raise HTTPException(status_code=401, detail="Password non valida")
    LOGIN_FAILURES.pop(ip, None)
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(),
        max_age=MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    require_auth(request)
    return {"authenticated": True}


@app.get("/api/status")
def status(request: Request):
    require_auth(request)
    state = disk_state()
    with db_session() as db:
        pending = db.scalar(
            select(func.count()).select_from(Recording).where(
                Recording.upload_status.in_(["pending", "uploading", "failed", "waiting_config"])
            )
        ) or 0
        uploaded = db.scalar(
            select(func.count()).select_from(Recording).where(Recording.upload_status == "uploaded")
        ) or 0
        failed = db.scalar(
            select(func.count()).select_from(Recording).where(Recording.upload_status == "failed")
        ) or 0
        waiting_config = db.scalar(
            select(func.count()).select_from(Recording).where(Recording.upload_status == "waiting_config")
        ) or 0
        local_bytes = db.scalar(
            select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.local_deleted.is_(False))
        ) or 0
    return {
        "disk": {
            "total": state.total,
            "used": state.used,
            "free": state.free,
            "total_human": human_bytes(state.total),
            "used_human": human_bytes(state.used),
            "free_human": human_bytes(state.free),
            "free_gb": round(state.free_gb, 2),
            "pressure": state.pressure,
        },
        "worker": manager.snapshot(),
        "queue": {
            "pending": pending,
            "uploaded": uploaded,
            "failed": failed,
            "waiting_config": waiting_config,
            "local_bytes": int(local_bytes),
            "local_human": human_bytes(local_bytes),
        },
        "config": {
            "poll_seconds": settings.poll_seconds,
            "segment_minutes": settings.segment_minutes,
            "primary_uploader": settings.primary_uploader,
            "fallback_uploader": settings.fallback_uploader,
            "delete_after_upload": settings.delete_after_upload,
            "version": "1.1.2",
            "gofile_configured": bool(settings.gofile_token) or settings.allow_gofile_guest,
            "pixeldrain_configured": bool(settings.pixeldrain_api_key),
        },
    }


@app.get("/api/sources")
def list_sources(request: Request):
    require_auth(request)
    with db_session() as db:
        rows = list(db.scalars(select(Source).order_by(Source.name.asc())).all())
    active_ids = set(manager.active)
    return [
        {
            "id": s.id,
            "name": s.name,
            "platform": s.platform,
            "slug": s.slug,
            "enabled": s.enabled,
            "quality": s.quality,
            "consent_confirmed": s.consent_confirmed,
            "last_status": "recording" if s.id in active_ids else ("paused" if not s.enabled else s.last_status),
            "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
            "last_live_at": s.last_live_at.isoformat() if s.last_live_at else None,
        }
        for s in rows
    ]


@app.post("/api/sources")
def add_source(body: SourceCreate, request: Request):
    require_auth(request)
    if body.platform != "chaturbate":
        raise HTTPException(400, "Solo l'adapter Chaturbate è abilitato nella v1")
    if not body.consent_confirmed:
        raise HTTPException(400, "È necessaria la conferma di autorizzazione")
    if body.quality not in {"best", "1080p", "720p", "480p"}:
        raise HTTPException(400, "Qualità non supportata")
    slug = _normalize_slug(body.slug)
    name = body.name.strip()
    with db_session() as db:
        if db.scalar(select(Source).where(Source.name == name)):
            raise HTTPException(409, "Esiste già una sorgente con questo nome")
        if db.scalar(select(Source).where(Source.platform == body.platform, Source.slug == slug)):
            raise HTTPException(409, "Questa sorgente è già configurata")
        source = Source(
            name=name,
            platform=body.platform,
            slug=slug,
            quality=body.quality,
            consent_confirmed=True,
            enabled=True,
        )
        db.add(source)
        db.flush()
        source_id = source.id
    manager.wake()
    return {"ok": True, "id": source_id}


@app.patch("/api/sources/{source_id}")
async def patch_source(source_id: int, body: SourcePatch, request: Request):
    require_auth(request)
    with db_session() as db:
        source = db.get(Source, source_id)
        if not source:
            raise HTTPException(404, "Sorgente non trovata")
        if body.name is not None:
            new_name = body.name.strip()
            if not new_name:
                raise HTTPException(400, "Il nome non può essere vuoto")
            duplicate_name = db.scalar(select(Source).where(Source.name == new_name, Source.id != source_id))
            if duplicate_name:
                raise HTTPException(409, "Esiste già una sorgente con questo nome")
            source.name = new_name
        if body.slug is not None:
            new_slug = _normalize_slug(body.slug)
            duplicate = db.scalar(
                select(Source).where(Source.platform == source.platform, Source.slug == new_slug, Source.id != source_id)
            )
            if duplicate:
                raise HTTPException(409, "Questa sorgente è già configurata")
            source.slug = new_slug
        if body.quality is not None:
            if body.quality not in {"best", "1080p", "720p", "480p"}:
                raise HTTPException(400, "Qualità non supportata")
            source.quality = body.quality
        if body.enabled is not None:
            source.enabled = body.enabled
        if body.consent_confirmed is not None:
            source.consent_confirmed = body.consent_confirmed
        should_stop = not source.enabled or not source.consent_confirmed
    if should_stop:
        await manager.stop_source(source_id)
    manager.wake()
    return {"ok": True}


@app.delete("/api/sources/{source_id}")
async def remove_source(source_id: int, request: Request):
    require_auth(request)
    await manager.stop_source(source_id)
    with db_session() as db:
        source = db.get(Source, source_id)
        if not source:
            raise HTTPException(404, "Sorgente non trovata")
        db.delete(source)
    manager.wake()
    return {"ok": True}


@app.post("/api/sources/check-now")
def check_now(request: Request):
    require_auth(request)
    manager.wake()
    return {"ok": True}


@app.get("/api/recordings")
def recordings(request: Request, limit: int = 200):
    require_auth(request)
    limit = max(1, min(limit, 1000))
    with db_session() as db:
        rows = list(db.scalars(select(Recording).order_by(Recording.finalized_at.desc()).limit(limit)).all())
    return [
        {
            "id": r.id,
            "source_id": r.source_id,
            "source_name": r.source_name,
            "session_id": r.session_id,
            "filename": r.filename,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None,
            "duration_seconds": r.duration_seconds,
            "size_bytes": r.size_bytes,
            "size_human": human_bytes(r.size_bytes),
            "sha256": r.sha256,
            "upload_status": r.upload_status,
            "upload_provider": r.upload_provider,
            "remote_url": r.remote_url,
            "upload_attempts": r.upload_attempts,
            "last_error": r.last_error,
            "local_available": (not r.local_deleted) and Path(r.local_path).exists(),
        }
        for r in rows
    ]


@app.get("/api/recordings/{recording_id}/download")
def download_recording(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        path = Path(rec.local_path)
        filename = rec.filename
    if not path.exists():
        raise HTTPException(404, "La copia locale è già stata rimossa")
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


@app.post("/api/recordings/{recording_id}/retry")
def retry_recording(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        if rec.local_deleted or not Path(rec.local_path).exists():
            raise HTTPException(400, "File locale non disponibile")
        rec.upload_status = "pending"
        rec.upload_attempts = 0
        rec.last_error = ""
    return {"ok": True}


@app.delete("/api/recordings/{recording_id}/local")
def delete_local_recording(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        if rec.upload_status != "uploaded":
            raise HTTPException(400, "La copia locale può essere eliminata solo dopo un upload verificato")
        Path(rec.local_path).unlink(missing_ok=True)
        rec.local_deleted = True
    return {"ok": True}


@app.post("/api/recordings/retry-failed")
def retry_failed_recordings(request: Request):
    require_auth(request)
    changed = 0
    with db_session() as db:
        rows = list(db.scalars(select(Recording).where(Recording.upload_status.in_(["failed", "waiting_config"]))).all())
        for rec in rows:
            if not rec.local_deleted and Path(rec.local_path).exists():
                rec.upload_status = "pending"
                rec.upload_attempts = 0
                rec.last_error = ""
                changed += 1
    manager.clear_retry_backoff()
    return {"ok": True, "changed": changed}


@app.post("/api/recordings/cleanup-uploaded")
def cleanup_uploaded_recordings(request: Request):
    require_auth(request)
    removed = 0
    freed = 0
    with db_session() as db:
        rows = list(db.scalars(
            select(Recording).where(Recording.upload_status == "uploaded", Recording.local_deleted.is_(False))
        ).all())
        for rec in rows:
            path = Path(rec.local_path)
            if path.exists():
                freed += path.stat().st_size
                path.unlink(missing_ok=True)
                removed += 1
            rec.local_deleted = True
    return {"ok": True, "removed": removed, "freed": freed, "freed_human": human_bytes(freed)}


@app.get("/healthz")
def healthz():
    state = disk_state()
    healthy = state.free_gb > settings.emergency_free_gb
    return {
        "ok": healthy,
        "disk_pressure": state.pressure,
        "free_gb": round(state.free_gb, 2),
        "worker": manager.health(),
    }
