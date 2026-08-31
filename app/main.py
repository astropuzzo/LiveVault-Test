from __future__ import annotations

import asyncio
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
from .recorder import remux_to_mp4
from .settings_store import public_settings, reload_runtime, runtime, set_values
from .storage import disk_state
from .uploaders import UploadError, test_provider
from .utils import generate_thumbnail, human_bytes, sha256_file, utcnow, verify_media
from .workers import manager

BASE = Path(__file__).parent
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,100}$")
LOGIN_FAILURES: dict[str, deque[float]] = defaultdict(deque)
LOGIN_WINDOW = 10 * 60
LOGIN_MAX_FAILURES = 6
VERSION = "2.0.0"


class LoginBody(BaseModel):
    password: str


class BoolBody(BaseModel):
    paused: bool
    stop_active: bool = True


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=180)
    platform: str = "chaturbate"
    quality: str = "best"
    consent_confirmed: bool


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    slug: str | None = Field(default=None, max_length=180)
    quality: str | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    consent_confirmed: bool | None = None


class SettingsPatch(BaseModel):
    poll_seconds: int | None = Field(default=None, ge=15, le=600)
    max_probe_concurrency: int | None = Field(default=None, ge=1, le=12)
    segment_minutes: int | None = Field(default=None, ge=5, le=120)
    container_format: str | None = None
    integrity_mode: str | None = None
    generate_thumbnails: bool | None = None
    buffer_max_gb: float | None = Field(default=None, ge=0, le=5000)
    buffer_hard_stop: bool | None = None
    min_free_gb: float | None = Field(default=None, ge=0.25, le=1000)
    critical_free_gb: float | None = Field(default=None, ge=0.1, le=1000)
    emergency_free_gb: float | None = Field(default=None, ge=0.05, le=1000)
    delete_after_upload: bool | None = None
    upload_retry_seconds: int | None = Field(default=None, ge=30, le=3600)
    max_upload_attempts: int | None = Field(default=None, ge=1, le=100)
    primary_uploader: str | None = None
    fallback_uploader: str | None = None
    gofile_token: str | None = Field(default=None, max_length=500)
    clear_gofile_token: bool = False
    gofile_folder_id: str | None = Field(default=None, max_length=200)
    gofile_region: str | None = None
    pixeldrain_api_key: str | None = Field(default=None, max_length=500)
    clear_pixeldrain_api_key: bool = False


def _normalize_slug(slug: str) -> str:
    value = slug.strip()
    if "chaturbate.com/" in value.lower():
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
    reload_runtime()
    await manager.start()
    yield
    await manager.stop()


app = FastAPI(title="LiveVault", version=VERSION, lifespan=lifespan)
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
        "media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else response.headers.get("Cache-Control", "")
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
    response.set_cookie(COOKIE_NAME, create_session_token(), max_age=MAX_AGE, httponly=True, samesite="strict", secure=settings.cookie_secure)
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
    cfg = runtime()
    with db_session() as db:
        pending = db.scalar(select(func.count()).select_from(Recording).where(Recording.upload_status.in_(["pending", "uploading", "failed", "waiting_config"]))) or 0
        uploaded = db.scalar(select(func.count()).select_from(Recording).where(Recording.upload_status == "uploaded")) or 0
        failed = db.scalar(select(func.count()).select_from(Recording).where(Recording.upload_status == "failed")) or 0
        integrity_failed = db.scalar(select(func.count()).select_from(Recording).where(Recording.integrity_status == "failed")) or 0
        waiting_config = db.scalar(select(func.count()).select_from(Recording).where(Recording.upload_status == "waiting_config")) or 0
    snapshot = manager.snapshot()
    buffer_bytes = snapshot["buffer_bytes"]
    return {
        "disk": {
            "total": state.total, "used": state.used, "free": state.free,
            "total_human": human_bytes(state.total), "used_human": human_bytes(state.used), "free_human": human_bytes(state.free),
            "free_gb": round(state.free_gb, 2), "pressure": state.pressure,
        },
        "worker": snapshot,
        "queue": {
            "pending": pending, "uploaded": uploaded, "failed": failed, "waiting_config": waiting_config,
            "integrity_failed": integrity_failed,
            "local_bytes": buffer_bytes, "local_human": human_bytes(buffer_bytes),
            "buffer_max_bytes": int(cfg.buffer_max_gb * 1024**3) if cfg.buffer_max_gb else 0,
            "buffer_max_human": human_bytes(cfg.buffer_max_gb * 1024**3) if cfg.buffer_max_gb else "Illimitato",
            "buffer_percent": round(buffer_bytes / (cfg.buffer_max_gb * 1024**3) * 100, 1) if cfg.buffer_max_gb else 0,
        },
        "config": {**public_settings(), "version": VERSION},
    }


@app.get("/api/settings")
def get_settings(request: Request):
    require_auth(request)
    return {"settings": public_settings(), "version": VERSION}


@app.patch("/api/settings")
def patch_settings(body: SettingsPatch, request: Request):
    require_auth(request)
    updates = body.model_dump(exclude_none=True)
    clear_gofile = updates.pop("clear_gofile_token", False)
    clear_pixeldrain = updates.pop("clear_pixeldrain_api_key", False)
    if updates.get("container_format") not in (None, "mp4", "mkv"):
        raise HTTPException(400, "Container deve essere mp4 o mkv")
    if updates.get("integrity_mode") not in (None, "quick", "packet"):
        raise HTTPException(400, "Integrity mode deve essere quick o packet")
    if updates.get("primary_uploader") not in (None, "gofile", "pixeldrain", "none"):
        raise HTTPException(400, "Provider primario non valido")
    if updates.get("fallback_uploader") not in (None, "gofile", "pixeldrain", "none"):
        raise HTTPException(400, "Provider fallback non valido")
    if updates.get("gofile_region") not in (None, "auto", "eu-par", "na-phx", "ap-sgp", "ap-hkg", "ap-tyo", "sa-sao"):
        raise HTTPException(400, "Regione Gofile non valida")
    for secret_key in ("gofile_token", "pixeldrain_api_key"):
        if secret_key in updates:
            updates[secret_key] = updates[secret_key].strip()
    if clear_gofile:
        updates["gofile_token"] = ""
    if clear_pixeldrain:
        updates["pixeldrain_api_key"] = ""
    # Validate disk guard ordering using current values plus this patch.
    current = runtime()
    min_free = float(updates.get("min_free_gb", current.min_free_gb))
    critical_free = float(updates.get("critical_free_gb", current.critical_free_gb))
    emergency_free = float(updates.get("emergency_free_gb", current.emergency_free_gb))
    if not (emergency_free <= critical_free <= min_free):
        raise HTTPException(400, "Le soglie devono rispettare: emergenza ≤ critica ≤ minima")
    set_values(updates)
    manager.wake()
    return {"ok": True, "settings": public_settings()}


@app.post("/api/settings/test/{provider}")
async def test_storage_provider(provider: str, request: Request):
    require_auth(request)
    if provider not in {"gofile", "pixeldrain"}:
        raise HTTPException(404, "Provider non supportato")
    try:
        return await asyncio.to_thread(test_provider, provider)
    except UploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Test provider fallito: {exc}") from exc


@app.post("/api/control/recordings")
async def control_recordings(body: BoolBody, request: Request):
    require_auth(request)
    set_values({"recording_paused": body.paused})
    if body.paused and body.stop_active:
        await manager.stop_all_recordings()
    manager.wake()
    return {"ok": True, "paused": body.paused}


@app.post("/api/control/uploads")
def control_uploads(body: BoolBody, request: Request):
    require_auth(request)
    set_values({"upload_paused": body.paused})
    manager.wake()
    return {"ok": True, "paused": body.paused}


@app.post("/api/uploads/run-now")
def uploads_run_now(request: Request):
    require_auth(request)
    set_values({"upload_paused": False})
    changed = 0
    with db_session() as db:
        rows = list(db.scalars(select(Recording).where(Recording.upload_status.in_(["failed", "waiting_config"]), Recording.integrity_status == "passed")).all())
        for rec in rows:
            if not rec.local_deleted and Path(rec.local_path).exists():
                rec.upload_status = "pending"
                rec.upload_attempts = 0
                changed += 1
    manager.clear_retry_backoff()
    manager.wake()
    return {"ok": True, "changed": changed}


@app.get("/api/sources")
def list_sources(request: Request):
    require_auth(request)
    with db_session() as db:
        rows = list(db.scalars(select(Source).order_by(Source.name.asc())).all())
    active_ids = set(manager.active)
    return [{
        "id": s.id, "name": s.name, "platform": s.platform, "slug": s.slug, "enabled": s.enabled, "quality": s.quality,
        "consent_confirmed": s.consent_confirmed,
        "last_status": "recording" if s.id in active_ids else ("paused" if not s.enabled else s.last_status),
        "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
        "last_live_at": s.last_live_at.isoformat() if s.last_live_at else None,
    } for s in rows]


@app.post("/api/sources")
def add_source(body: SourceCreate, request: Request):
    require_auth(request)
    if body.platform != "chaturbate":
        raise HTTPException(400, "Solo l'adapter Chaturbate è abilitato")
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
        source = Source(name=name, platform=body.platform, slug=slug, quality=body.quality, consent_confirmed=True, enabled=True)
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
            if db.scalar(select(Source).where(Source.name == new_name, Source.id != source_id)):
                raise HTTPException(409, "Esiste già una sorgente con questo nome")
            source.name = new_name
        if body.slug is not None:
            new_slug = _normalize_slug(body.slug)
            if db.scalar(select(Source).where(Source.platform == source.platform, Source.slug == new_slug, Source.id != source_id)):
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


def _recording_json(r: Recording) -> dict:
    local_available = (not r.local_deleted) and Path(r.local_path).exists()
    thumb_available = bool(r.thumbnail_path and Path(r.thumbnail_path).exists())
    return {
        "id": r.id, "source_id": r.source_id, "source_name": r.source_name, "session_id": r.session_id,
        "filename": r.filename, "container_format": r.container_format or Path(r.filename).suffix.lstrip("."),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finalized_at": r.finalized_at.isoformat() if r.finalized_at else None,
        "duration_seconds": r.duration_seconds, "size_bytes": r.size_bytes, "size_human": human_bytes(r.size_bytes),
        "sha256": r.sha256, "integrity_status": r.integrity_status, "integrity_error": r.integrity_error,
        "integrity_checked_at": r.integrity_checked_at.isoformat() if r.integrity_checked_at else None,
        "upload_status": r.upload_status, "upload_provider": r.upload_provider, "remote_url": r.remote_url,
        "upload_attempts": r.upload_attempts, "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        "last_error": r.last_error, "local_available": local_available, "thumbnail_available": thumb_available,
        "thumbnail_url": f"/api/recordings/{r.id}/thumbnail" if thumb_available else "",
        "view_url": f"/api/recordings/{r.id}/view" if local_available else "",
    }


@app.get("/api/recordings")
def recordings(request: Request, limit: int = 500):
    require_auth(request)
    limit = max(1, min(limit, 2000))
    with db_session() as db:
        rows = list(db.scalars(select(Recording).order_by(Recording.finalized_at.desc()).limit(limit)).all())
    return [_recording_json(r) for r in rows]


@app.get("/api/recordings/{recording_id}/thumbnail")
def recording_thumbnail(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec or not rec.thumbnail_path:
            raise HTTPException(404, "Miniatura non disponibile")
        path = Path(rec.thumbnail_path)
    if not path.exists():
        raise HTTPException(404, "Miniatura non disponibile")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/recordings/{recording_id}/view")
def view_recording(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        path = Path(rec.local_path)
    if not path.exists():
        raise HTTPException(404, "Copia locale non disponibile")
    media_type = "video/mp4" if path.suffix.lower() == ".mp4" else "video/x-matroska"
    return FileResponse(path, media_type=media_type)


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


@app.post("/api/recordings/{recording_id}/upload-now")
def upload_now(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        if rec.local_deleted or not Path(rec.local_path).exists():
            raise HTTPException(400, "File locale non disponibile")
        if rec.integrity_status != "passed":
            raise HTTPException(400, "Il file deve superare il controllo integrità prima dell'upload")
        rec.upload_status = "pending"
        rec.upload_attempts = 0
        rec.upload_priority = 100
        rec.last_error = ""
    set_values({"upload_paused": False})
    manager.clear_retry_backoff()
    manager.wake()
    return {"ok": True}


@app.post("/api/recordings/{recording_id}/retry")
def retry_recording(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        if rec.local_deleted or not Path(rec.local_path).exists():
            raise HTTPException(400, "File locale non disponibile")
        if rec.integrity_status != "passed":
            raise HTTPException(400, "Ricontrolla prima l'integrità del file")
        rec.upload_status = "pending"
        rec.upload_attempts = 0
        rec.last_error = ""
    manager.clear_retry_backoff()
    manager.wake()
    return {"ok": True}


@app.post("/api/recordings/{recording_id}/integrity")
async def recheck_integrity(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        path = Path(rec.local_path)
        old_sha = rec.sha256
    if not path.exists():
        raise HTTPException(404, "File locale non disponibile")
    result = await asyncio.to_thread(verify_media, path, runtime().integrity_mode)
    digest = await asyncio.to_thread(sha256_file, path) if result.ok else old_sha
    if result.ok and old_sha and digest != old_sha:
        result.ok = False
        result.error = "SHA-256 cambiato rispetto alla finalizzazione"
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if rec:
            rec.integrity_status = "passed" if result.ok else "failed"
            rec.integrity_error = result.error
            rec.integrity_checked_at = utcnow()
            if result.ok:
                rec.sha256 = digest
                if rec.upload_status == "integrity_failed":
                    rec.upload_status = "pending"
                    rec.last_error = ""
            else:
                rec.upload_status = "integrity_failed"
                rec.last_error = f"Integrità fallita: {result.error}"[-1600:]
    manager.wake()
    return {"ok": result.ok, "status": "passed" if result.ok else "failed", "error": result.error}


@app.post("/api/recordings/{recording_id}/convert-mp4")
async def convert_mp4(recording_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        if rec.upload_status == "uploading":
            raise HTTPException(409, "Attendi la fine dell'upload prima di convertire")
        path = Path(rec.local_path)
        previous_upload_status = rec.upload_status
        if path.suffix.lower() != ".mp4":
            # Remove this record from the uploader selection while the file path changes.
            rec.upload_status = "converting"
    if not path.exists():
        with db_session() as db:
            rec = db.get(Recording, recording_id)
            if rec and rec.upload_status == "converting":
                rec.upload_status = previous_upload_status
        raise HTTPException(404, "File locale non disponibile")
    if path.suffix.lower() == ".mp4":
        return {"ok": True, "already_mp4": True}
    try:
        new_path = await remux_to_mp4(path)
    except Exception as exc:
        with db_session() as db:
            rec = db.get(Recording, recording_id)
            if rec and rec.upload_status == "converting":
                rec.upload_status = previous_upload_status
        raise HTTPException(500, f"Conversione MP4 fallita: {exc}") from exc
    integrity = await asyncio.to_thread(verify_media, new_path, runtime().integrity_mode)
    digest = await asyncio.to_thread(sha256_file, new_path)
    thumb_path = ""
    if runtime().generate_thumbnails and integrity.ok:
        candidate = settings.data_dir / "thumbnails" / f"{digest[:24]}.jpg"
        if await asyncio.to_thread(generate_thumbnail, new_path, candidate, integrity.duration):
            thumb_path = str(candidate)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if rec:
            was_uploaded = previous_upload_status == "uploaded"
            rec.local_path = str(new_path)
            rec.filename = new_path.name
            rec.container_format = "mp4"
            rec.size_bytes = new_path.stat().st_size
            rec.sha256 = digest
            rec.duration_seconds = integrity.duration
            rec.integrity_status = "passed" if integrity.ok else "failed"
            rec.integrity_error = integrity.error
            rec.integrity_checked_at = utcnow()
            if thumb_path:
                rec.thumbnail_path = thumb_path
            # The bytes changed, so the new MP4 always needs a fresh upload verification.
            rec.upload_status = "pending" if integrity.ok else "integrity_failed"
            rec.upload_attempts = 0
            rec.last_error = "" if integrity.ok else f"Integrità fallita dopo remux: {integrity.error}"[-1600:]
            if was_uploaded:
                rec.upload_provider = ""
                rec.remote_id = ""
                rec.remote_url = ""
                rec.last_error = "MP4 creato: il nuovo file deve essere caricato nuovamente"
    manager.wake()
    return {"ok": True, "filename": new_path.name, "integrity": "passed" if integrity.ok else "failed"}


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
        rows = list(db.scalars(select(Recording).where(Recording.upload_status.in_(["failed", "waiting_config"]), Recording.integrity_status == "passed")).all())
        for rec in rows:
            if not rec.local_deleted and Path(rec.local_path).exists():
                rec.upload_status = "pending"
                rec.upload_attempts = 0
                rec.last_error = ""
                changed += 1
    manager.clear_retry_backoff()
    manager.wake()
    return {"ok": True, "changed": changed}


@app.post("/api/recordings/cleanup-uploaded")
def cleanup_uploaded_recordings(request: Request):
    require_auth(request)
    removed = 0
    freed = 0
    with db_session() as db:
        rows = list(db.scalars(select(Recording).where(Recording.upload_status == "uploaded", Recording.local_deleted.is_(False))).all())
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
    cfg = runtime()
    healthy = state.free_gb > cfg.emergency_free_gb
    return {"ok": healthy, "disk_pressure": state.pressure, "free_gb": round(state.free_gb, 2), "worker": manager.health(), "version": VERSION}
