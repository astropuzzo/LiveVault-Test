from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, distinct, func, or_, select
from sqlalchemy.exc import IntegrityError

from .auth import COOKIE_NAME, MAX_AGE, create_session_token, password_ok, require_auth
from .config import settings
from .db import (
    Category,
    Collection,
    CollectionProfile,
    LiveSession,
    Profile,
    ProfileCategory,
    Recording,
    Source,
    db_session,
    init_db,
)
from .file_cleanup import cleanup_empty_parents, cleanup_orphan_videos, safe_unlink
from .recorder import (
    LIVE_PREVIEW_MAX_AGE_SECONDS,
    finalize_mp4_for_streaming,
    live_preview_path,
    mp4_is_streaming_ready,
    remux_to_mp4,
)
from .settings_store import public_settings, reload_runtime, runtime, set_values
from .source_providers import audit_inputs, normalize_source, probe, provider_catalog, provider_label, resolve_inputs, source_url
from .statistics import build_activity_statistics
from .storage import disk_state
from .uploaders import UploadError, create_gofile_folder, move_gofile_contents, test_provider
from .utils import generate_thumbnail, human_bytes, sha256_file, utcnow, verify_media
from .workers import manager

BASE = Path(__file__).parent
LOGIN_FAILURES: dict[str, deque[float]] = defaultdict(deque)
LOGIN_WINDOW = 10 * 60
LOGIN_MAX_FAILURES = 6
VERSION = "2.7.0"


class LoginBody(BaseModel):
    password: str


class BoolBody(BaseModel):
    paused: bool
    stop_active: bool = True


class CleanupLocalBody(BaseModel):
    scope: str = "uploaded"
    source_id: int | None = None
    include_orphans: bool = False
    delete_thumbnails: bool = False
    confirm: bool = False


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=1000)
    platform: str = "auto"
    profile_id: int | None = Field(default=None, gt=0)
    quality: str = "best"
    consent_confirmed: bool
    organize_cloud: bool = True
    gofile_folder_id: str = Field(default="", max_length=200)
    gofile_folder_url: str = Field(default="", max_length=500)


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    slug: str | None = Field(default=None, max_length=1000)
    platform: str | None = Field(default=None, max_length=40)
    profile_id: int | None = Field(default=None, gt=0)
    quality: str | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    consent_confirmed: bool | None = None
    organize_cloud: bool | None = None
    gofile_folder_id: str | None = Field(default=None, max_length=200)
    gofile_folder_url: str | None = Field(default=None, max_length=500)


class SourceInspect(BaseModel):
    slug: str = Field(min_length=1, max_length=1000)
    platform: str = Field(default="auto", max_length=40)
    quality: str = Field(default="best", max_length=20)


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#7aa5ff", pattern=r"^#[0-9A-Fa-f]{6}$")


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    color: str = Field(default="#8c78ff", pattern=r"^#[0-9A-Fa-f]{6}$")
    pinned: bool = False


class CollectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    pinned: bool | None = None


class SourceLibraryPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    favorite: bool | None = None
    focus: bool | None = None
    notes: str | None = Field(default=None, max_length=20_000)
    category_ids: list[int] | None = Field(default=None, max_length=200)
    collection_ids: list[int] | None = Field(default=None, max_length=200)


class SourceBulkAction(BaseModel):
    source_ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal[
        "favorite",
        "unfavorite",
        "enable",
        "pause",
        "add_category",
        "remove_category",
        "add_collection",
        "remove_collection",
    ]
    category_id: int | None = Field(default=None, gt=0)
    collection_id: int | None = Field(default=None, gt=0)


class SettingsPatch(BaseModel):
    poll_seconds: int | None = Field(default=None, ge=15, le=600)
    max_probe_concurrency: int | None = Field(default=None, ge=1, le=12)
    segment_minutes: int | None = Field(default=None, ge=5, le=120)
    segment_max_gb: float | None = Field(default=None, ge=0.25, le=2.0)
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


def _normalize_source_or_400(platform: str, value: str) -> tuple[str, str]:
    try:
        return normalize_source(platform, value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _normalize_gofile_folder_id(value: str) -> str:
    value = value.strip()
    if value and not re.fullmatch(r"[A-Za-z0-9_-]{4,200}", value):
        raise HTTPException(400, "Folder ID Gofile non valido")
    return value


def _normalize_gofile_url(value: str) -> str:
    value = value.strip()
    if value and not re.fullmatch(r"https://gofile\.io/d/[A-Za-z0-9_-]+", value):
        raise HTTPException(400, "Link cartella Gofile non valido")
    return value


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_blocked(ip: str) -> bool:
    now = time.time()
    q = LOGIN_FAILURES[ip]
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    return len(q) >= LOGIN_MAX_FAILURES


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_library_name(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned or any(ord(char) < 32 for char in cleaned):
        raise HTTPException(400, f"{label} non valido")
    return cleaned


def _clean_color(value: str) -> str:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value or ""):
        raise HTTPException(400, "Colore non valido: usa #RRGGBB")
    return value.lower()


def _unique_positive_ids(values: list[int] | None, label: str) -> list[int]:
    if values is None:
        return []
    if any(isinstance(value, bool) or value <= 0 for value in values):
        raise HTTPException(400, f"{label} contiene ID non validi")
    return sorted(set(values))


def _source_public_url(source: Source) -> str:
    try:
        return source_url(source.platform, source.slug)
    except ValueError:
        return ""


def _category_json(category: Category, profile_count: int = 0) -> dict:
    return {
        "id": category.id,
        "name": category.name,
        "color": category.color,
        "profile_count": int(profile_count),
        "source_count": int(profile_count),
    }


def _collection_json(collection: Collection, profile_count: int = 0) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "description": collection.description,
        "color": collection.color,
        "pinned": collection.pinned,
        "profile_count": int(profile_count),
        "source_count": int(profile_count),
    }


def _category_rows(db) -> list[dict]:
    counts = dict(db.execute(
        select(ProfileCategory.category_id, func.count(ProfileCategory.profile_id))
        .group_by(ProfileCategory.category_id)
    ).all())
    rows = list(db.scalars(select(Category).order_by(func.lower(Category.name), Category.id)).all())
    return [_category_json(row, counts.get(row.id, 0)) for row in rows]


def _collection_rows(db) -> list[dict]:
    counts = dict(db.execute(
        select(CollectionProfile.collection_id, func.count(CollectionProfile.profile_id))
        .group_by(CollectionProfile.collection_id)
    ).all())
    rows = list(db.scalars(
        select(Collection).order_by(Collection.pinned.desc(), func.lower(Collection.name), Collection.id)
    ).all())
    return [_collection_json(row, counts.get(row.id, 0)) for row in rows]


def _library_maps(db, profile_ids: set[int] | None = None) -> tuple[dict[int, list[dict]], dict[int, list[dict]]]:
    categories: dict[int, list[dict]] = defaultdict(list)
    collections: dict[int, list[dict]] = defaultdict(list)
    category_query = (
        select(ProfileCategory.profile_id, Category)
        .join(Category, Category.id == ProfileCategory.category_id)
        .order_by(func.lower(Category.name), Category.id)
    )
    collection_query = (
        select(CollectionProfile.profile_id, Collection)
        .join(Collection, Collection.id == CollectionProfile.collection_id)
        .order_by(Collection.pinned.desc(), func.lower(Collection.name), Collection.id)
    )
    if profile_ids is not None:
        if not profile_ids:
            return {}, {}
        category_query = category_query.where(ProfileCategory.profile_id.in_(profile_ids))
        collection_query = collection_query.where(CollectionProfile.profile_id.in_(profile_ids))
    for profile_id, category in db.execute(category_query).all():
        categories[int(profile_id)].append(_category_json(category))
    for profile_id, collection in db.execute(collection_query).all():
        collections[int(profile_id)].append(_collection_json(collection))
    return dict(categories), dict(collections)


def _linked_sources_map(db, profile_ids: set[int] | None = None) -> dict[int, list[dict]]:
    linked: dict[int, list[dict]] = defaultdict(list)
    query = select(Source).order_by(Source.name, Source.id)
    if profile_ids is not None:
        if not profile_ids:
            return {}
        query = query.where(Source.profile_id.in_(profile_ids))
    for source in db.scalars(query).all():
        if source.profile_id is None:
            continue
        linked[int(source.profile_id)].append({
            "id": source.id,
            "name": source.name,
            "platform": source.platform,
            "provider_label": provider_label(source.platform),
            "slug": source.slug,
            "source_url": _source_public_url(source),
            "enabled": source.enabled,
            "archived": source.archived,
            "quality": source.quality,
            "last_status": "archived" if source.archived else ("paused" if not source.enabled else source.last_status),
            "last_checked_at": _iso_utc(source.last_checked_at),
            "last_seen_live_at": _iso_utc(source.last_seen_live_at),
        })
    return dict(linked)


def _profile_json(
    profile: Profile,
    categories: dict[int, list[dict]],
    collections: dict[int, list[dict]],
    linked_sources: dict[int, list[dict]],
) -> dict:
    return {
        "id": profile.id,
        "profile_id": profile.id,
        "display_name": profile.display_name,
        "favorite": profile.favorite,
        "focus": profile.focus,
        "notes": profile.notes,
        "created_at": _iso_utc(profile.created_at),
        "categories": categories.get(profile.id, []),
        "collections": collections.get(profile.id, []),
        "linked_sources": linked_sources.get(profile.id, []),
    }


def _safe_thumbnail_url(recording_id: int, thumbnail_path: str) -> str:
    if not thumbnail_path:
        return ""
    try:
        root = (settings.data_dir / "thumbnails").resolve()
        candidate = Path(thumbnail_path).resolve()
        if not candidate.is_file() or not candidate.is_relative_to(root):
            return ""
    except (OSError, RuntimeError, ValueError):
        return ""
    return f"/api/recordings/{recording_id}/thumbnail"


def _profile_cover_map(db, profile_ids: set[int]) -> dict[int, str]:
    covers: dict[int, str] = {}
    if not profile_ids:
        return covers
    ranked = (
        select(
            Recording.id.label("recording_id"),
            Recording.thumbnail_path.label("thumbnail_path"),
            Source.profile_id.label("profile_id"),
            func.row_number().over(
                partition_by=Source.profile_id,
                order_by=(Recording.finalized_at.desc(), Recording.id.desc()),
            ).label("cover_rank"),
        )
        .join(Source, Source.id == Recording.source_id)
        .where(Source.profile_id.in_(profile_ids), Recording.thumbnail_path != "")
        .subquery()
    )
    rows = db.execute(
        select(ranked.c.recording_id, ranked.c.thumbnail_path, ranked.c.profile_id)
        .where(ranked.c.cover_rank <= 5)
        .order_by(ranked.c.cover_rank)
    ).all()
    for recording_id, thumbnail_path, profile_id in rows:
        if profile_id in covers:
            continue
        url = _safe_thumbnail_url(int(recording_id), str(thumbnail_path or ""))
        if url:
            covers[int(profile_id)] = url
    return covers


def _require_categories(db, category_ids: list[int]) -> None:
    if not category_ids:
        return
    found = set(db.scalars(select(Category.id).where(Category.id.in_(category_ids))).all())
    missing = sorted(set(category_ids) - found)
    if missing:
        raise HTTPException(404, f"Categorie non trovate: {', '.join(map(str, missing))}")


def _require_collections(db, collection_ids: list[int]) -> None:
    if not collection_ids:
        return
    found = set(db.scalars(select(Collection.id).where(Collection.id.in_(collection_ids))).all())
    missing = sorted(set(collection_ids) - found)
    if missing:
        raise HTTPException(404, f"Raccolte non trovate: {', '.join(map(str, missing))}")


def _profile_for_source(db, source_id: int) -> tuple[Source, Profile]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "Sorgente non trovata")
    profile = db.get(Profile, source.profile_id) if source.profile_id is not None else None
    if not profile:
        raise HTTPException(409, "Profilo libreria non inizializzato")
    return source, profile


def _replace_profile_categories(db, profile_id: int, category_ids: list[int]) -> None:
    _require_categories(db, category_ids)
    db.execute(delete(ProfileCategory).where(ProfileCategory.profile_id == profile_id))
    db.add_all(ProfileCategory(profile_id=profile_id, category_id=item_id) for item_id in category_ids)


def _replace_profile_collections(db, profile_id: int, collection_ids: list[int]) -> None:
    _require_collections(db, collection_ids)
    db.execute(delete(CollectionProfile).where(CollectionProfile.profile_id == profile_id))
    db.add_all(CollectionProfile(profile_id=profile_id, collection_id=item_id) for item_id in collection_ids)


def _delete_orphan_profile(db, profile_id: int | None) -> None:
    if profile_id is None:
        return
    db.flush()
    if db.scalar(select(func.count()).select_from(Source).where(Source.profile_id == profile_id)):
        return
    db.execute(delete(ProfileCategory).where(ProfileCategory.profile_id == profile_id))
    db.execute(delete(CollectionProfile).where(CollectionProfile.profile_id == profile_id))
    profile = db.get(Profile, profile_id)
    if profile:
        db.delete(profile)


def _smart_library_counts(db) -> dict[str, int]:
    profiles = list(db.scalars(select(Profile)).all())
    source_rows = list(db.scalars(select(Source)).all())
    by_profile: dict[int, list[Source]] = defaultdict(list)
    attention: set[int] = set()
    for source in source_rows:
        if source.profile_id is None:
            continue
        by_profile[int(source.profile_id)].append(source)
        if (
            source.enabled and not source.archived
            and (source.last_status == "error" or bool((source.last_error or "").strip()))
        ):
            attention.add(int(source.profile_id))
    failed_profile_ids = db.scalars(
        select(distinct(Source.profile_id))
        .join(Recording, Recording.source_id == Source.id)
        .where(
            Source.profile_id.is_not(None),
            or_(
                Recording.upload_status.in_(["failed", "integrity_failed"]),
                Recording.integrity_status.in_(["failed", "integrity_failed"]),
            ),
        )
    ).all()
    attention.update(int(item) for item in failed_profile_ids if item is not None)
    categorized = set(db.scalars(select(distinct(ProfileCategory.profile_id))).all())
    return {
        "all": len(profiles),
        "favorites": sum(1 for profile in profiles if profile.favorite),
        "live": sum(
            1 for profile in profiles
            if any(
                source.enabled and not source.archived and source.last_status in {"live", "recording"}
                for source in by_profile.get(profile.id, [])
            )
        ),
        "paused": sum(
            1 for profile in profiles
            if by_profile.get(profile.id) and not any(source.enabled for source in by_profile[profile.id])
        ),
        "uncategorized": sum(1 for profile in profiles if profile.id not in categorized),
        "needs_attention": len(attention),
    }


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
    if request.url.path.startswith("/api/sources/") and request.url.path.endswith("/preview"):
        response.headers["Cache-Control"] = "private, max-age=12"
    elif request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path in {"/", "/sw.js"}:
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        BASE / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache, must-revalidate"},
    )


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
        total_recordings = db.scalar(select(func.count()).select_from(Recording)) or 0
        total_sessions = db.scalar(select(func.count(distinct(Recording.session_id)))) or 0
        total_bytes = db.scalar(select(func.coalesce(func.sum(Recording.size_bytes), 0))) or 0
        total_duration = db.scalar(select(func.coalesce(func.sum(Recording.duration_seconds), 0.0))) or 0.0
        uploaded_bytes = db.scalar(select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.upload_status == "uploaded")) or 0
        audio_missing = db.scalar(select(func.count()).select_from(Recording).where(Recording.has_audio.is_(False))) or 0
        today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_recordings = db.scalar(select(func.count()).select_from(Recording).where(Recording.finalized_at >= today_start)) or 0
        latest_recording = db.scalar(select(func.max(Recording.finalized_at)))
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
        "history": {
            "recordings": total_recordings,
            "sessions": total_sessions,
            "today": today_recordings,
            "total_bytes": total_bytes,
            "total_human": human_bytes(total_bytes),
            "total_duration_seconds": total_duration,
            "uploaded": uploaded,
            "uploaded_bytes": uploaded_bytes,
            "uploaded_human": human_bytes(uploaded_bytes),
            "audio_missing": audio_missing,
            "latest_recording_at": _iso_utc(latest_recording),
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
    if "gofile_folder_id" in updates:
        updates["gofile_folder_id"] = _normalize_gofile_folder_id(updates["gofile_folder_id"])
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


@app.get("/api/providers")
def list_source_providers(request: Request):
    require_auth(request)
    return provider_catalog()


@app.post("/api/sources/inspect")
async def inspect_source(body: SourceInspect, request: Request):
    require_auth(request)
    if body.quality not in {"best", "1080p", "720p", "480p"}:
        raise HTTPException(400, "Qualità non supportata")
    platform, slug = _normalize_source_or_400(body.platform, body.slug)
    result = await probe(platform, slug, body.quality)
    has_video: bool | None = None
    has_audio: bool | None = None
    input_error = ""
    if result.live:
        try:
            inputs = await resolve_inputs(platform, slug, body.quality)
            audit = await audit_inputs(inputs)
            has_video = audit.has_video
            has_audio = audit.has_audio
            input_error = audit.error
        except Exception as exc:
            input_error = str(exc)[-700:]
    return {
        "ok": result.status != "error" and not input_error,
        "platform": platform,
        "provider_label": provider_label(platform),
        "slug": slug,
        "source_url": source_url(platform, slug),
        "live": result.live,
        "status": result.status,
        "title": result.title,
        "has_video": has_video,
        "has_audio": has_audio,
        "metadata_status": result.metadata_status,
        "metadata_error": result.metadata_error,
        "error": input_error or result.error,
    }


@app.get("/api/library/meta")
def library_meta(request: Request):
    require_auth(request)
    with db_session() as db:
        return {
            "categories": _category_rows(db),
            "collections": _collection_rows(db),
            "smart_counts": _smart_library_counts(db),
        }


@app.get("/api/library/categories")
def list_categories(request: Request):
    require_auth(request)
    with db_session() as db:
        return _category_rows(db)


@app.get("/api/library/categories/{category_id}")
def get_category(category_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        category = db.get(Category, category_id)
        if not category:
            raise HTTPException(404, "Categoria non trovata")
        count = db.scalar(
            select(func.count()).select_from(ProfileCategory).where(ProfileCategory.category_id == category_id)
        ) or 0
        return _category_json(category, count)


@app.post("/api/library/categories", status_code=201)
def create_category(body: CategoryCreate, request: Request):
    require_auth(request)
    name = _clean_library_name(body.name, "Nome categoria")
    try:
        with db_session() as db:
            if db.scalar(select(Category.id).where(func.lower(Category.name) == name.lower())):
                raise HTTPException(409, "Esiste già una categoria con questo nome")
            category = Category(name=name, color=_clean_color(body.color))
            db.add(category)
            db.flush()
            return _category_json(category)
    except IntegrityError as exc:
        raise HTTPException(409, "Esiste già una categoria con questo nome") from exc


@app.patch("/api/library/categories/{category_id}")
def patch_category(category_id: int, body: CategoryPatch, request: Request):
    require_auth(request)
    try:
        with db_session() as db:
            category = db.get(Category, category_id)
            if not category:
                raise HTTPException(404, "Categoria non trovata")
            if body.name is not None:
                name = _clean_library_name(body.name, "Nome categoria")
                duplicate = db.scalar(select(Category.id).where(
                    func.lower(Category.name) == name.lower(),
                    Category.id != category_id,
                ))
                if duplicate:
                    raise HTTPException(409, "Esiste già una categoria con questo nome")
                category.name = name
            if body.color is not None:
                category.color = _clean_color(body.color)
            db.flush()
            count = db.scalar(
                select(func.count()).select_from(ProfileCategory).where(ProfileCategory.category_id == category_id)
            ) or 0
            return _category_json(category, count)
    except IntegrityError as exc:
        raise HTTPException(409, "Esiste già una categoria con questo nome") from exc


@app.delete("/api/library/categories/{category_id}")
def delete_category(category_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        category = db.get(Category, category_id)
        if not category:
            raise HTTPException(404, "Categoria non trovata")
        db.execute(delete(ProfileCategory).where(ProfileCategory.category_id == category_id))
        db.delete(category)
    return {"ok": True, "id": category_id}


@app.get("/api/library/collections")
def list_collections(request: Request):
    require_auth(request)
    with db_session() as db:
        return _collection_rows(db)


@app.get("/api/library/collections/{collection_id}")
def get_collection(collection_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        collection = db.get(Collection, collection_id)
        if not collection:
            raise HTTPException(404, "Raccolta non trovata")
        count = db.scalar(
            select(func.count()).select_from(CollectionProfile).where(CollectionProfile.collection_id == collection_id)
        ) or 0
        return _collection_json(collection, count)


@app.post("/api/library/collections", status_code=201)
def create_collection(body: CollectionCreate, request: Request):
    require_auth(request)
    name = _clean_library_name(body.name, "Nome raccolta")
    try:
        with db_session() as db:
            if db.scalar(select(Collection.id).where(func.lower(Collection.name) == name.lower())):
                raise HTTPException(409, "Esiste già una raccolta con questo nome")
            collection = Collection(
                name=name,
                description=body.description.strip(),
                color=_clean_color(body.color),
                pinned=body.pinned,
            )
            db.add(collection)
            db.flush()
            return _collection_json(collection)
    except IntegrityError as exc:
        raise HTTPException(409, "Esiste già una raccolta con questo nome") from exc


@app.patch("/api/library/collections/{collection_id}")
def patch_collection(collection_id: int, body: CollectionPatch, request: Request):
    require_auth(request)
    try:
        with db_session() as db:
            collection = db.get(Collection, collection_id)
            if not collection:
                raise HTTPException(404, "Raccolta non trovata")
            if body.name is not None:
                name = _clean_library_name(body.name, "Nome raccolta")
                duplicate = db.scalar(select(Collection.id).where(
                    func.lower(Collection.name) == name.lower(),
                    Collection.id != collection_id,
                ))
                if duplicate:
                    raise HTTPException(409, "Esiste già una raccolta con questo nome")
                collection.name = name
            if body.description is not None:
                collection.description = body.description.strip()
            if body.color is not None:
                collection.color = _clean_color(body.color)
            if body.pinned is not None:
                collection.pinned = body.pinned
            db.flush()
            count = db.scalar(
                select(func.count()).select_from(CollectionProfile)
                .where(CollectionProfile.collection_id == collection_id)
            ) or 0
            return _collection_json(collection, count)
    except IntegrityError as exc:
        raise HTTPException(409, "Esiste già una raccolta con questo nome") from exc


@app.delete("/api/library/collections/{collection_id}")
def delete_collection(collection_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        collection = db.get(Collection, collection_id)
        if not collection:
            raise HTTPException(404, "Raccolta non trovata")
        db.execute(delete(CollectionProfile).where(CollectionProfile.collection_id == collection_id))
        db.delete(collection)
    return {"ok": True, "id": collection_id}


@app.patch("/api/sources/{source_id}/library")
def patch_source_library(source_id: int, body: SourceLibraryPatch, request: Request):
    require_auth(request)
    category_ids = _unique_positive_ids(body.category_ids, "category_ids")
    collection_ids = _unique_positive_ids(body.collection_ids, "collection_ids")
    with db_session() as db:
        _source, profile = _profile_for_source(db, source_id)
        if body.category_ids is not None:
            _require_categories(db, category_ids)
        if body.collection_ids is not None:
            _require_collections(db, collection_ids)
        if body.display_name is not None:
            profile.display_name = _clean_library_name(body.display_name, "Nome profilo")
        if body.favorite is not None:
            profile.favorite = body.favorite
        if body.focus is not None:
            profile.focus = body.focus
        if body.notes is not None:
            profile.notes = body.notes.strip()
        if body.category_ids is not None:
            _replace_profile_categories(db, profile.id, category_ids)
        if body.collection_ids is not None:
            _replace_profile_collections(db, profile.id, collection_ids)
        db.flush()
        categories, collections = _library_maps(db, {profile.id})
        linked_sources = _linked_sources_map(db, {profile.id})
        payload = _profile_json(profile, categories, collections, linked_sources)
    return {"ok": True, "profile": payload}


@app.post("/api/sources/bulk")
async def bulk_sources(body: SourceBulkAction, request: Request):
    require_auth(request)
    requested_source_ids = _unique_positive_ids(body.source_ids, "source_ids")
    category_actions = {"add_category", "remove_category"}
    collection_actions = {"add_collection", "remove_collection"}
    if body.action in category_actions and body.category_id is None:
        raise HTTPException(400, "category_id richiesto per questa azione")
    if body.action not in category_actions and body.category_id is not None:
        raise HTTPException(400, "category_id non previsto per questa azione")
    if body.action in collection_actions and body.collection_id is None:
        raise HTTPException(400, "collection_id richiesto per questa azione")
    if body.action not in collection_actions and body.collection_id is not None:
        raise HTTPException(400, "collection_id non previsto per questa azione")

    affected_source_ids: list[int] = []
    profile_ids: list[int] = []
    with db_session() as db:
        selected = list(db.scalars(select(Source).where(Source.id.in_(requested_source_ids))).all())
        found_source_ids = {source.id for source in selected}
        missing = sorted(set(requested_source_ids) - found_source_ids)
        if missing:
            raise HTTPException(404, f"Sorgenti non trovate: {', '.join(map(str, missing))}")
        profile_ids = sorted({int(source.profile_id) for source in selected if source.profile_id is not None})
        if len(profile_ids) != len({source.profile_id for source in selected}):
            raise HTTPException(409, "Una sorgente non ha un profilo libreria valido")
        profiles = list(db.scalars(select(Profile).where(Profile.id.in_(profile_ids))).all())
        if len(profiles) != len(profile_ids):
            raise HTTPException(409, "Un profilo libreria collegato non esiste")

        if body.action in category_actions:
            _require_categories(db, [int(body.category_id)])
        if body.action in collection_actions:
            _require_collections(db, [int(body.collection_id)])

        if body.action == "favorite":
            for profile in profiles:
                profile.favorite = True
        elif body.action == "unfavorite":
            for profile in profiles:
                profile.favorite = False
        elif body.action in {"enable", "pause"}:
            linked = list(db.scalars(select(Source).where(
                Source.profile_id.in_(profile_ids),
                Source.archived.is_(False),
            )).all())
            enabled = body.action == "enable"
            for source in linked:
                source.enabled = enabled
                if enabled:
                    source.archived = False
            affected_source_ids = sorted(source.id for source in linked)
        elif body.action == "add_category":
            existing = set(db.scalars(select(ProfileCategory.profile_id).where(
                ProfileCategory.profile_id.in_(profile_ids),
                ProfileCategory.category_id == body.category_id,
            )).all())
            db.add_all(
                ProfileCategory(profile_id=profile_id, category_id=int(body.category_id))
                for profile_id in profile_ids if profile_id not in existing
            )
        elif body.action == "remove_category":
            db.execute(delete(ProfileCategory).where(
                ProfileCategory.profile_id.in_(profile_ids),
                ProfileCategory.category_id == body.category_id,
            ))
        elif body.action == "add_collection":
            existing = set(db.scalars(select(CollectionProfile.profile_id).where(
                CollectionProfile.profile_id.in_(profile_ids),
                CollectionProfile.collection_id == body.collection_id,
            )).all())
            db.add_all(
                CollectionProfile(profile_id=profile_id, collection_id=int(body.collection_id))
                for profile_id in profile_ids if profile_id not in existing
            )
        elif body.action == "remove_collection":
            db.execute(delete(CollectionProfile).where(
                CollectionProfile.profile_id.in_(profile_ids),
                CollectionProfile.collection_id == body.collection_id,
            ))

    if body.action == "pause":
        await asyncio.gather(*(manager.stop_source(source_id) for source_id in affected_source_ids))
    elif body.action == "enable":
        manager.wake()
    return {
        "ok": True,
        "action": body.action,
        "updated": len(profile_ids),
        "profile_ids": profile_ids,
        "source_ids": affected_source_ids or requested_source_ids,
    }


@app.delete("/api/library/profiles/{profile_id}")
async def delete_profile(profile_id: int, request: Request):
    """Permanently remove a creator profile and its source configuration.

    Recording rows and their local/cloud files are intentionally preserved so
    deleting a creator from the library cannot destroy captured media.
    """
    require_auth(request)
    with db_session() as db:
        profile = db.get(Profile, profile_id)
        if not profile:
            raise HTTPException(404, "Profilo non trovato")
        linked_sources = list(db.scalars(
            select(Source).where(Source.profile_id == profile_id).order_by(Source.id)
        ).all())
        source_ids = [source.id for source in linked_sources]
        preserved_recordings = int(db.scalar(
            select(func.count()).select_from(Recording).where(Recording.source_id.in_(source_ids))
        ) or 0) if source_ids else 0
        now = utcnow()
        for source in linked_sources:
            source.enabled = False
            source.archived = True
            source.last_status = "archived"
            source.status_changed_at = now

    if source_ids:
        await asyncio.gather(*(manager.stop_source(source_id) for source_id in source_ids))

    with db_session() as db:
        profile = db.get(Profile, profile_id)
        if not profile:
            raise HTTPException(404, "Profilo non trovato")
        # sources.profile_id is a real FK, so source configuration goes first.
        # Recording rows deliberately remain as immutable archive history.
        if source_ids:
            db.execute(delete(LiveSession).where(LiveSession.source_id.in_(source_ids)))
        db.execute(delete(Source).where(Source.profile_id == profile_id))
        db.execute(delete(ProfileCategory).where(ProfileCategory.profile_id == profile_id))
        db.execute(delete(CollectionProfile).where(CollectionProfile.profile_id == profile_id))
        db.delete(profile)

    manager.wake()
    return {
        "ok": True,
        "deleted": True,
        "profile_id": profile_id,
        "source_ids": source_ids,
        "preserved_recordings": preserved_recordings,
    }


def _activity_statistics(db, days: int, profile_id: int | None = None) -> dict:
    days = max(1, min(int(days), 365))
    now = utcnow()
    window_start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    source_query = select(Source).order_by(Source.id)
    if profile_id is not None:
        if not db.get(Profile, profile_id):
            raise HTTPException(404, "Profilo non trovato")
        source_query = source_query.where(Source.profile_id == profile_id)
    source_rows = list(db.scalars(source_query).all())
    source_ids = [row.id for row in source_rows]
    profile_ids = sorted({int(row.profile_id) for row in source_rows if row.profile_id is not None})
    profile_rows = list(db.scalars(select(Profile).where(Profile.id.in_(profile_ids))).all()) if profile_ids else []
    if source_ids:
        live_rows = list(db.scalars(
            select(LiveSession).where(
                LiveSession.source_id.in_(source_ids),
                LiveSession.started_at <= now,
                or_(LiveSession.ended_at.is_(None), LiveSession.ended_at >= window_start),
            ).order_by(LiveSession.started_at.asc())
        ).all())
        recording_rows = list(db.scalars(
            select(Recording).where(
                Recording.source_id.in_(source_ids),
                Recording.finalized_at >= window_start,
            ).order_by(Recording.started_at.asc())
        ).all())
    else:
        live_rows = []
        recording_rows = []
    return build_activity_statistics(
        sources=source_rows,
        profiles=profile_rows,
        live_sessions=live_rows,
        recordings=recording_rows,
        days=days,
        now=now,
    )


@app.get("/api/statistics")
def global_statistics(request: Request, days: int = 30):
    require_auth(request)
    with db_session() as db:
        return _activity_statistics(db, days)


@app.get("/api/library/profiles/{profile_id}/statistics")
def profile_statistics(profile_id: int, request: Request, days: int = 30):
    require_auth(request)
    with db_session() as db:
        return _activity_statistics(db, days, profile_id=profile_id)


@app.get("/api/sources/{source_id}/profile")
def source_profile(source_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        source, profile = _profile_for_source(db, source_id)
        linked_rows = list(db.scalars(
            select(Source).where(Source.profile_id == profile.id).order_by(Source.name, Source.id)
        ).all())
        linked_source_ids = [row.id for row in linked_rows]
        categories, collections = _library_maps(db, {profile.id})
        linked_sources = _linked_sources_map(db, {profile.id})
        profile_payload = _profile_json(profile, categories, collections, linked_sources)
        cover_url = _profile_cover_map(db, {profile.id}).get(profile.id, "")
        stats = db.execute(select(
            func.count(Recording.id),
            func.count(distinct(Recording.session_id)),
            func.coalesce(func.sum(Recording.size_bytes), 0),
            func.coalesce(func.sum(Recording.duration_seconds), 0.0),
            func.sum(case((Recording.upload_status == "uploaded", 1), else_=0)),
            func.sum(case((Recording.upload_status.in_(["failed", "integrity_failed"]), 1), else_=0)),
            func.sum(case((Recording.has_audio.is_(False), 1), else_=0)),
            func.min(Recording.finalized_at),
            func.max(Recording.finalized_at),
        ).where(Recording.source_id.in_(linked_source_ids))).one()
        recent = list(db.scalars(
            select(Recording)
            .where(Recording.source_id.in_(linked_source_ids))
            .order_by(Recording.finalized_at.desc(), Recording.id.desc())
            .limit(20)
        ).all())
        recent_payload = [_recording_json(recording) for recording in recent]
        source_payload = {
            **profile_payload,
            "id": source.id,
            "name": source.name,
            "platform": source.platform,
            "provider_label": provider_label(source.platform),
            "slug": source.slug,
            "source_url": _source_public_url(source),
            "enabled": source.enabled,
            "archived": source.archived,
            "quality": source.quality,
            "last_status": "archived" if source.archived else ("paused" if not source.enabled else source.last_status),
            "last_checked_at": _iso_utc(source.last_checked_at),
            "last_live_at": _iso_utc(source.last_live_at),
            "last_seen_live_at": _iso_utc(source.last_seen_live_at),
            "status_changed_at": _iso_utc(source.status_changed_at),
            "last_error": source.last_error,
            "cover_thumbnail_url": cover_url,
            "statistics": {
                "recording_count": int(stats[0] or 0),
                "session_count": int(stats[1] or 0),
                "total_bytes": int(stats[2] or 0),
                "total_duration_seconds": float(stats[3] or 0),
                "uploaded_count": int(stats[4] or 0),
                "failed_count": int(stats[5] or 0),
                "audio_missing_count": int(stats[6] or 0),
                "first_recording_at": _iso_utc(stats[7]),
                "last_recording_at": _iso_utc(stats[8]),
            },
        }
        timeline: list[dict] = [{
            "type": "profile_created",
            "at": _iso_utc(profile.created_at),
            "title": "Profilo creato",
        }]
        for linked_source in linked_rows:
            timeline.append({
                "type": "source_added",
                "at": _iso_utc(linked_source.created_at),
                "title": f"Sorgente aggiunta: {linked_source.name}",
                "source_id": linked_source.id,
            })
            if linked_source.last_seen_live_at:
                timeline.append({
                    "type": "live_seen",
                    "at": _iso_utc(linked_source.last_seen_live_at),
                    "title": f"Live rilevata: {linked_source.name}",
                    "source_id": linked_source.id,
                })
        for recording in recent:
            timeline.append({
                "type": "recording",
                "at": _iso_utc(recording.finalized_at),
                "title": f"Registrazione completata: {recording.filename}",
                "source_id": recording.source_id,
                "recording_id": recording.id,
                "upload_status": recording.upload_status,
            })
        timeline = sorted(
            (event for event in timeline if event.get("at")),
            key=lambda event: str(event["at"]),
            reverse=True,
        )[:30]
        return {"source": source_payload, "recent_recordings": recent_payload, "timeline": timeline}


@app.get("/api/sources")
def list_sources(request: Request):
    require_auth(request)
    active_ids = set(manager.active)
    cfg = runtime()
    now = utcnow()
    live_fresh_seconds = max(180, int(cfg.poll_seconds) * 3)
    with db_session() as db:
        rows = list(db.scalars(select(Source).order_by(Source.name.asc())).all())
        aggregate_rows = db.execute(
            select(
                Recording.source_id.label("source_id"),
                func.count(Recording.id).label("recording_count"),
                func.count(distinct(Recording.session_id)).label("session_count"),
                func.coalesce(func.sum(Recording.size_bytes), 0).label("total_bytes"),
                func.coalesce(func.sum(Recording.duration_seconds), 0.0).label("total_duration"),
                func.sum(case((Recording.upload_status == "uploaded", 1), else_=0)).label("uploaded_count"),
                func.sum(case((Recording.upload_status.in_(["failed", "integrity_failed"]), 1), else_=0)).label("failed_count"),
                func.max(Recording.finalized_at).label("last_recording_at"),
            ).group_by(Recording.source_id)
        ).all()
        aggregates = {row.source_id: row for row in aggregate_rows}
        latest_cloud: dict[int, str] = {}
        for source_id, remote_url in db.execute(
            select(Recording.source_id, Recording.remote_url)
            .where(Recording.remote_url != "")
            .order_by(Recording.finalized_at.desc())
        ).all():
            latest_cloud.setdefault(source_id, remote_url)
        profile_ids = {int(source.profile_id) for source in rows if source.profile_id is not None}
        profiles = {
            profile.id: profile
            for profile in db.scalars(select(Profile).where(Profile.id.in_(profile_ids))).all()
        } if profile_ids else {}
        categories, collections = _library_maps(db, profile_ids)
        linked_sources = _linked_sources_map(db, profile_ids)
        covers = _profile_cover_map(db, profile_ids)
        result = []
        for source in rows:
            aggregate = aggregates.get(source.id)
            active = source.id in active_ids
            profile = profiles.get(source.profile_id)
            last_seen = source.last_seen_live_at
            if last_seen is not None and last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            fresh_live = bool(
                last_seen is not None
                and (now - last_seen.astimezone(timezone.utc)).total_seconds() <= live_fresh_seconds
            )
            detected_live = bool(
                not source.archived
                and (active or (source.last_status in {"live", "recording"} and fresh_live))
            )
            blocked_by_pause = bool(
                detected_live and not active and source.consent_confirmed and not source.archived
                and (cfg.recording_paused or not source.enabled)
            )
            preview_url = ""
            preview_updated_at = None
            preview = live_preview_path(source.id)
            try:
                preview_stat = preview.stat()
                preview_time = datetime.fromtimestamp(preview_stat.st_mtime, tz=timezone.utc)
                if preview_stat.st_size > 0 and (now - preview_time).total_seconds() <= LIVE_PREVIEW_MAX_AGE_SECONDS:
                    preview_url = f"/api/sources/{source.id}/preview"
                    preview_updated_at = preview_time
            except OSError:
                pass
            result.append({
                "id": source.id, "name": source.name, "platform": source.platform,
                "provider_label": provider_label(source.platform), "slug": source.slug,
                "source_url": _source_public_url(source),
                "profile_id": profile.id if profile else None,
                "display_name": profile.display_name if profile else source.name,
                "favorite": profile.favorite if profile else False,
                "focus": profile.focus if profile else False,
                "notes": profile.notes if profile else "",
                "categories": categories.get(profile.id, []) if profile else [],
                "collections": collections.get(profile.id, []) if profile else [],
                "linked_sources": linked_sources.get(profile.id, []) if profile else [],
                "cover_thumbnail_url": covers.get(profile.id, "") if profile else "",
                "enabled": source.enabled, "archived": source.archived,
                "quality": source.quality, "consent_confirmed": source.consent_confirmed,
                "detected_live": detected_live,
                "recording_blocked_by_pause": blocked_by_pause,
                "pause_reason": "global" if blocked_by_pause and cfg.recording_paused else ("source" if blocked_by_pause else ""),
                "preview_url": preview_url,
                "preview_updated_at": _iso_utc(preview_updated_at),
                "last_status": "recording" if active else (
                    "archived" if source.archived else ("paused" if not source.enabled else source.last_status)
                ),
                "last_checked_at": _iso_utc(source.last_checked_at),
                "last_live_at": _iso_utc(source.last_live_at),
                "last_seen_live_at": _iso_utc(source.last_seen_live_at),
                "metadata_status": source.metadata_status,
                "metadata_error": source.metadata_error,
                "status_changed_at": _iso_utc(source.status_changed_at),
                "last_error": source.last_error,
                "organize_cloud": source.organize_cloud,
                "gofile_folder_id": source.gofile_folder_id,
                "gofile_folder_url": source.gofile_folder_url,
                "collection_url": f"/?source={source.id}#archive",
                "recording_count": int(aggregate.recording_count if aggregate else 0),
                "session_count": int(aggregate.session_count if aggregate else 0),
                "uploaded_count": int(aggregate.uploaded_count if aggregate else 0),
                "failed_count": int(aggregate.failed_count if aggregate else 0),
                "total_bytes": int(aggregate.total_bytes if aggregate else 0),
                "total_duration_seconds": float(aggregate.total_duration if aggregate else 0),
                "last_recording_at": _iso_utc(aggregate.last_recording_at if aggregate else None),
                "latest_cloud_url": latest_cloud.get(source.id, ""),
            })
        return result


@app.get("/api/sources/{source_id}/preview")
def source_live_preview(source_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        source = db.get(Source, source_id)
        if not source or source.archived:
            raise HTTPException(404, "Preview non disponibile")
    path = live_preview_path(source_id)
    try:
        stat = path.stat()
        updated = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    except OSError as exc:
        raise HTTPException(404, "Preview non ancora disponibile") from exc
    if stat.st_size <= 0 or (utcnow() - updated).total_seconds() > LIVE_PREVIEW_MAX_AGE_SECONDS:
        raise HTTPException(404, "Preview non aggiornata")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=12"})


@app.post("/api/sources")
def add_source(body: SourceCreate, request: Request):
    require_auth(request)
    if not body.consent_confirmed:
        raise HTTPException(400, "È necessaria la conferma di autorizzazione")
    if body.quality not in {"best", "1080p", "720p", "480p"}:
        raise HTTPException(400, "Qualità non supportata")
    platform, slug = _normalize_source_or_400(body.platform, body.slug)
    name = _clean_library_name(body.name, "Nome sorgente")
    with db_session() as db:
        if db.scalar(select(Source).where(Source.name == name)):
            raise HTTPException(409, "Esiste già una sorgente con questo nome")
        if db.scalar(select(Source).where(Source.platform == platform, Source.slug == slug)):
            raise HTTPException(409, "Questa sorgente è già configurata")
        if body.profile_id is not None:
            profile = db.get(Profile, body.profile_id)
            if not profile:
                raise HTTPException(404, "Profilo non trovato")
        else:
            profile = Profile(display_name=name, favorite=False, focus=False, notes="")
            db.add(profile)
            db.flush()
        source = Source(
            profile_id=profile.id,
            name=name,
            platform=platform,
            slug=slug,
            quality=body.quality,
            consent_confirmed=True,
            enabled=True,
            organize_cloud=body.organize_cloud,
            gofile_folder_id=_normalize_gofile_folder_id(body.gofile_folder_id),
            gofile_folder_url=_normalize_gofile_url(body.gofile_folder_url),
        )
        db.add(source)
        db.flush()
        source_id = source.id
    manager.wake()
    return {"ok": True, "id": source_id, "profile_id": profile.id}


@app.patch("/api/sources/{source_id}")
async def patch_source(source_id: int, body: SourcePatch, request: Request):
    require_auth(request)
    reference_changed = False
    with db_session() as db:
        source = db.get(Source, source_id)
        if not source:
            raise HTTPException(404, "Sorgente non trovata")
        old_profile_id = source.profile_id
        if body.profile_id is not None and body.profile_id != source.profile_id:
            if not db.get(Profile, body.profile_id):
                raise HTTPException(404, "Profilo non trovato")
            source.profile_id = body.profile_id
        if body.name is not None:
            new_name = _clean_library_name(body.name, "Nome sorgente")
            if db.scalar(select(Source).where(Source.name == new_name, Source.id != source_id)):
                raise HTTPException(409, "Esiste già una sorgente con questo nome")
            source.name = new_name
        if body.slug is not None or body.platform is not None:
            new_platform, new_slug = _normalize_source_or_400(
                body.platform or source.platform,
                body.slug if body.slug is not None else source.slug,
            )
            if new_platform != source.platform:
                raise HTTPException(400, "Il provider non si cambia su una sorgente esistente: creane una nuova")
            if db.scalar(select(Source).where(Source.platform == new_platform, Source.slug == new_slug, Source.id != source_id)):
                raise HTTPException(409, "Questa sorgente è già configurata")
            if new_slug != source.slug:
                source.slug = new_slug
                reference_changed = True
                source.last_status = "unknown"
                source.last_checked_at = None
                source.last_live_at = None
                source.last_seen_live_at = None
                source.status_changed_at = None
                source.last_error = ""
                source.metadata_status = "unknown"
                source.metadata_error = ""
        if body.quality is not None:
            if body.quality not in {"best", "1080p", "720p", "480p"}:
                raise HTTPException(400, "Qualità non supportata")
            source.quality = body.quality
        if body.enabled is not None:
            source.enabled = body.enabled
            if body.enabled:
                source.archived = False
        if body.consent_confirmed is not None:
            source.consent_confirmed = body.consent_confirmed
        if body.organize_cloud is not None:
            source.organize_cloud = body.organize_cloud
        if body.gofile_folder_id is not None:
            new_folder_id = _normalize_gofile_folder_id(body.gofile_folder_id)
            if new_folder_id != source.gofile_folder_id and body.gofile_folder_url is None:
                source.gofile_folder_url = ""
            source.gofile_folder_id = new_folder_id
        if body.gofile_folder_url is not None:
            source.gofile_folder_url = _normalize_gofile_url(body.gofile_folder_url)
        if source.profile_id != old_profile_id:
            _delete_orphan_profile(db, old_profile_id)
        should_stop = reference_changed or not source.enabled or not source.consent_confirmed
        resulting_profile_id = source.profile_id
    if should_stop:
        await manager.stop_source(source_id)
    manager.wake()
    return {"ok": True, "profile_id": resulting_profile_id}


@app.post("/api/sources/{source_id}/cloud-folder")
async def organize_source_cloud(source_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        source = db.get(Source, source_id)
        if not source:
            raise HTTPException(404, "Sorgente non trovata")
        source_name = source.name
        folder_id = source.gofile_folder_id
        folder_url = source.gofile_folder_url
    try:
        if not folder_id:
            folder_id, folder_url = await asyncio.to_thread(
                create_gofile_folder,
                source_name,
                runtime().gofile_folder_id,
            )
            with db_session() as db:
                source = db.get(Source, source_id)
                if source:
                    source.organize_cloud = True
                    source.gofile_folder_id = folder_id
                    source.gofile_folder_url = folder_url
        with db_session() as db:
            remote_ids = list(db.scalars(
                select(Recording.remote_id).where(
                    Recording.source_id == source_id,
                    Recording.upload_provider == "gofile",
                    Recording.remote_id != "",
                )
            ).all())
        moved = 0
        warning = ""
        if remote_ids:
            try:
                await asyncio.to_thread(move_gofile_contents, remote_ids, folder_id)
                moved = len(remote_ids)
            except Exception as exc:
                warning = f"Cartella creata; i vecchi file restano ai link originali: {exc}"[-800:]
        return {"ok": True, "folder_id": folder_id, "folder_url": folder_url, "moved": moved, "warning": warning}
    except UploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Organizzazione Gofile fallita: {exc}") from exc


@app.delete("/api/sources/{source_id}")
async def remove_source(source_id: int, request: Request):
    require_auth(request)
    with db_session() as db:
        source = db.get(Source, source_id)
        if not source:
            raise HTTPException(404, "Sorgente non trovata")
        # Archiving is deliberately non-destructive: it preserves the profile,
        # recordings, filesystem path and cloud folder for an explicit restore.
        source.enabled = False
        source.archived = True
        source.last_status = "archived"
        source.status_changed_at = utcnow()
    await manager.stop_source(source_id)
    manager.wake()
    return {"ok": True, "archived": True}


@app.post("/api/sources/check-now")
def check_now(request: Request):
    require_auth(request)
    manager.wake()
    return {"ok": True}


@app.post("/api/sources/{source_id}/check-now")
async def check_source_now(source_id: int, request: Request):
    require_auth(request)
    if not await manager.check_source_now(source_id):
        raise HTTPException(404, "Sorgente non trovata")
    return {"ok": True}


def _recording_json(r: Recording) -> dict:
    local_available = (not r.local_deleted) and Path(r.local_path).exists()
    thumbnail_url = _safe_thumbnail_url(r.id, r.thumbnail_path)
    thumb_available = bool(thumbnail_url)
    return {
        "id": r.id, "source_id": r.source_id, "source_name": r.source_name, "session_id": r.session_id,
        "filename": r.filename, "container_format": r.container_format or Path(r.filename).suffix.lstrip("."),
        "started_at": _iso_utc(r.started_at),
        "finalized_at": _iso_utc(r.finalized_at),
        "duration_seconds": r.duration_seconds, "size_bytes": r.size_bytes, "size_human": human_bytes(r.size_bytes),
        "sha256": r.sha256, "integrity_status": r.integrity_status, "integrity_error": r.integrity_error,
        "integrity_checked_at": _iso_utc(r.integrity_checked_at),
        "upload_status": r.upload_status, "upload_provider": r.upload_provider, "remote_url": r.remote_url,
        "collection_url": f"/?source={r.source_id}#archive",
        "upload_attempts": r.upload_attempts, "uploaded_at": _iso_utc(r.uploaded_at),
        "has_video": r.has_video, "has_audio": r.has_audio,
        "video_codec": r.video_codec, "audio_codec": r.audio_codec,
        "last_error": r.last_error, "local_available": local_available, "thumbnail_available": thumb_available,
        "thumbnail_url": thumbnail_url,
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
        path = Path(rec.thumbnail_path).resolve()
        safe_url = _safe_thumbnail_url(rec.id, rec.thumbnail_path)
    if not safe_url:
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
            rec.has_video = result.has_video
            rec.has_audio = result.has_audio
            rec.video_codec = result.codec("video")
            rec.audio_codec = result.codec("audio")
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
        if rec.upload_status in {"uploading", "converting"}:
            raise HTTPException(409, "Attendi la fine dell'elaborazione prima di convertire")
        path = Path(rec.local_path)
        previous_upload_status = rec.upload_status
        already_ready = path.suffix.lower() == ".mp4" and mp4_is_streaming_ready(path)
        if not already_ready:
            # Remove this record from the uploader selection while the file path changes.
            rec.upload_status = "converting"
    if not path.exists():
        with db_session() as db:
            rec = db.get(Recording, recording_id)
            if rec and rec.upload_status == "converting":
                rec.upload_status = previous_upload_status
        raise HTTPException(404, "File locale non disponibile")
    if already_ready:
        return {"ok": True, "already_mp4": True}
    try:
        if path.suffix.lower() == ".mp4":
            await finalize_mp4_for_streaming(path)
            new_path = path
        else:
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
        candidate = settings.data_dir / "thumbnails" / f"{digest[:24]}-sheet-v1.jpg"
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
            rec.has_video = integrity.has_video
            rec.has_audio = integrity.has_audio
            rec.video_codec = integrity.codec("video")
            rec.audio_codec = integrity.codec("audio")
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


def _remove_local_copy(recording_id: int, *, force: bool = False, delete_thumbnail: bool = False) -> dict:
    """Remove the actual local bytes first, then update DB state.

    Non-uploaded files require force=True. Files currently being uploaded/converted are
    never removed. The path is confined to LiveVault storage so a corrupt DB row cannot
    delete an arbitrary server file.
    """
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        previous_status = rec.upload_status
        local_path = Path(rec.local_path)
        thumbnail_path = Path(rec.thumbnail_path) if rec.thumbnail_path else None
        if previous_status in {"uploading", "converting", "deleting"}:
            raise HTTPException(409, "File occupato: attendi la fine dell'operazione in corso")
        if previous_status != "uploaded" and not force:
            raise HTTPException(400, "File non caricato: usa la cancellazione forzata per eliminarlo definitivamente")
        if previous_status != "uploaded":
            rec.upload_status = "deleting"

    try:
        freed, removed = safe_unlink(local_path, settings.recordings_dir)
        cleanup_empty_parents(local_path.parent, settings.recordings_dir)
        thumbnail_removed = False
        if delete_thumbnail and thumbnail_path:
            _, thumbnail_removed = safe_unlink(thumbnail_path, settings.data_dir / "thumbnails")
            cleanup_empty_parents(thumbnail_path.parent, settings.data_dir / "thumbnails")
    except (OSError, ValueError) as exc:
        with db_session() as db:
            rec = db.get(Recording, recording_id)
            if rec and rec.upload_status == "deleting":
                rec.upload_status = previous_status
        raise HTTPException(500, f"Impossibile eliminare il file locale: {exc}") from exc

    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if rec:
            rec.local_deleted = True
            if previous_status != "uploaded":
                rec.upload_status = "discarded"
                rec.last_error = "File locale eliminato manualmente prima dell'upload"
            else:
                rec.upload_status = "uploaded"
                rec.last_error = ""
            if delete_thumbnail and thumbnail_removed:
                rec.thumbnail_path = ""

    manager.clear_retry_backoff()
    manager.wake()
    return {
        "ok": True,
        "removed": removed,
        "thumbnail_removed": thumbnail_removed,
        "freed": freed,
        "freed_human": human_bytes(freed),
    }


@app.delete("/api/recordings/{recording_id}/local")
def delete_local_recording(recording_id: int, request: Request, force: bool = False, delete_thumbnail: bool = False):
    require_auth(request)
    return _remove_local_copy(recording_id, force=force, delete_thumbnail=delete_thumbnail)


@app.delete("/api/recordings/{recording_id}")
def delete_recording(recording_id: int, request: Request, delete_file: bool = True, delete_thumbnail: bool = True):
    """Delete an archive entry. By default the underlying local file is deleted too."""
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        if rec.upload_status in {"uploading", "converting", "deleting"}:
            raise HTTPException(409, "File occupato: attendi la fine dell'operazione in corso")
        thumbnail_path = Path(rec.thumbnail_path) if rec.thumbnail_path else None

    freed = 0
    file_removed = False
    thumbnail_removed = False
    if delete_file:
        result = _remove_local_copy(recording_id, force=True, delete_thumbnail=delete_thumbnail)
        freed = int(result["freed"])
        file_removed = bool(result["removed"])
        thumbnail_removed = bool(result["thumbnail_removed"])
    elif delete_thumbnail and thumbnail_path:
        try:
            _, thumbnail_removed = safe_unlink(thumbnail_path, settings.data_dir / "thumbnails")
            cleanup_empty_parents(thumbnail_path.parent, settings.data_dir / "thumbnails")
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"Impossibile eliminare la miniatura: {exc}") from exc

    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if rec:
            db.delete(rec)

    manager.clear_retry_backoff()
    manager.wake()
    return {
        "ok": True,
        "entry_deleted": True,
        "file_removed": file_removed,
        "thumbnail_removed": thumbnail_removed,
        "freed": freed,
        "freed_human": human_bytes(freed),
    }


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


def _cleanup_ids(scope: str, source_id: int | None) -> list[int]:
    with db_session() as db:
        query = select(Recording.id).where(~Recording.upload_status.in_(["uploading", "converting", "deleting"]))
        if source_id is not None:
            query = query.where(Recording.source_id == source_id)
        if scope == "uploaded":
            query = query.where(Recording.upload_status == "uploaded")
        elif scope == "failed":
            query = query.where(Recording.upload_status.in_(["failed", "waiting_config", "integrity_failed", "discarded"]))
        elif scope != "all":
            raise HTTPException(400, "Scope pulizia non valido: uploaded, failed o all")
        return list(db.scalars(query).all())


@app.post("/api/recordings/cleanup-local")
def cleanup_local_recordings(body: CleanupLocalBody, request: Request):
    require_auth(request)
    scope = body.scope.strip().lower()
    if scope not in {"uploaded", "failed", "all"}:
        raise HTTPException(400, "Scope pulizia non valido: uploaded, failed o all")
    if scope != "uploaded" and not body.confirm:
        raise HTTPException(400, "La pulizia di file non caricati richiede confirm=true")

    ids = _cleanup_ids(scope, body.source_id)
    removed = 0
    freed = 0
    thumbnails_removed = 0
    errors: list[str] = []
    for recording_id in ids:
        try:
            result = _remove_local_copy(
                recording_id,
                force=scope != "uploaded",
                delete_thumbnail=body.delete_thumbnails,
            )
            removed += int(bool(result["removed"]))
            freed += int(result["freed"])
            thumbnails_removed += int(bool(result["thumbnail_removed"]))
        except HTTPException as exc:
            errors.append(f"#{recording_id}: {exc.detail}")

    orphan_result = {"removed": 0, "freed": 0, "skipped_active": 0, "errors": []}
    if body.include_orphans and body.source_id is None:
        with db_session() as db:
            tracked_paths = [
                Path(path)
                for path in db.scalars(select(Recording.local_path).where(Recording.local_deleted.is_(False))).all()
            ]
        active_dirs = [session.directory for session in manager.active.values()]
        orphan_result = cleanup_orphan_videos(settings.recordings_dir, tracked_paths, active_dirs)
        removed += int(orphan_result["removed"])
        freed += int(orphan_result["freed"])
        errors.extend(orphan_result["errors"])

    manager.clear_retry_backoff()
    manager.wake()
    return {
        "ok": not errors,
        "scope": scope,
        "removed": removed,
        "thumbnails_removed": thumbnails_removed,
        "orphan_removed": orphan_result["removed"],
        "skipped_active": orphan_result["skipped_active"],
        "freed": freed,
        "freed_human": human_bytes(freed),
        "errors": errors[:50],
    }


@app.post("/api/recordings/cleanup-uploaded")
def cleanup_uploaded_recordings(request: Request):
    """Backward-compatible safe cleanup used by older UI versions."""
    require_auth(request)
    ids = _cleanup_ids("uploaded", None)
    removed = 0
    freed = 0
    errors: list[str] = []
    for recording_id in ids:
        try:
            result = _remove_local_copy(recording_id, force=False, delete_thumbnail=False)
            removed += int(bool(result["removed"]))
            freed += int(result["freed"])
        except HTTPException as exc:
            errors.append(f"#{recording_id}: {exc.detail}")
    return {"ok": not errors, "removed": removed, "freed": freed, "freed_human": human_bytes(freed), "errors": errors[:50]}


@app.get("/healthz")
def healthz():
    state = disk_state()
    cfg = runtime()
    worker = manager.health()
    workers_ok = worker["started"] and all(worker["tasks"].values())
    healthy = state.free_gb > cfg.emergency_free_gb and workers_ok
    payload = {"ok": healthy, "disk_pressure": state.pressure, "free_gb": round(state.free_gb, 2), "worker": worker, "version": VERSION}
    return JSONResponse(payload, status_code=200 if healthy else 503)
