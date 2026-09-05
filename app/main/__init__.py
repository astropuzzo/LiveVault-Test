from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field


# Load the established FastAPI application, then extend it with focused
# processing controls.  Python prefers this package over app/main.py; the
# original module remains the single source of truth for all existing routes.
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "main.py"
_LEGACY_NAME = "app._main_legacy"
_spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise RuntimeError("Cannot load LiveVault application")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules[_LEGACY_NAME] = _legacy
_spec.loader.exec_module(_legacy)

app = _legacy.app


# The legacy Pulse endpoint intentionally clamps the query to 48 hours and the
# response to 120 sessions. The product UI now exposes an explicit seven-day
# history option, so extend those two constants on the already-registered route
# without duplicating its database/query implementation.
def _extend_pulse_history_window() -> None:
    endpoint = getattr(_legacy, "control_room_pulse", None)
    if endpoint is None:  # pragma: no cover
        raise RuntimeError("Live Pulse endpoint not found")
    constants = list(endpoint.__code__.co_consts)
    replacements = {48: 168, 120: 1000}
    for old, new in replacements.items():
        matches = [index for index, value in enumerate(constants) if type(value) is int and value == old]
        if len(matches) != 1:  # pragma: no cover - fail loudly if legacy code changes
            raise RuntimeError(f"Unexpected Live Pulse constant layout for {old}")
        constants[matches[0]] = new
    endpoint.__code__ = endpoint.__code__.replace(co_consts=tuple(constants))


_extend_pulse_history_window()


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_legacy)))


class _MainFacade(types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if hasattr(_legacy, name):
            setattr(_legacy, name, value)


sys.modules[__name__].__class__ = _MainFacade


# Install the physical per-file size policy before wrapping processing errors.
# Logical reconnect sessions can span many files, but no consolidation should
# silently grow past Settings -> segment_max_gb (2 GB by default).
from app.workers import manager as _processing_manager  # noqa: E402
from app.workers.size_policy import install_size_policy as _install_size_policy  # noqa: E402

_install_size_policy(_processing_manager)


# A process kill can leave an interrupted FFmpeg remux as
# .<name>.finalizing.mp4. The original recovery loop correctly waits 30 minutes,
# but an MP4 without a moov/header can never become valid on a later retry and
# used to remain a permanent Dashboard error. Preserve those bytes under a
# hidden quarantine name, write the diagnostic beside them, and stop retrying
# the same irrecoverable temporary file forever.
from app.config import settings as _recovery_settings  # noqa: E402
from app.recovery_policy import (  # noqa: E402
    finalizing_error_is_unrecoverable as _finalizing_error_is_unrecoverable,
    recovery_quarantine_path as _recovery_quarantine_path,
)
from app.utils import verify_media as _recovery_verify_media  # noqa: E402


async def _recover_stale_finalizing_files_safe(self) -> None:
    cutoff = time.time() - 30 * 60
    for temporary in sorted(_recovery_settings.recordings_dir.rglob(".*.finalizing.mp4")):
        if self._stopping:
            return
        if not temporary.is_file():
            continue
        key = f"recovery-temp:{temporary}"
        try:
            if temporary.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        stem = temporary.name[1:-len(".finalizing.mp4")]
        original = temporary.with_name(f"{stem}.mp4")
        try:
            original_ready = original.is_file() and original.stat().st_size > 0
        except OSError:
            original_ready = False
        if original_ready:
            temporary.unlink(missing_ok=True)
            self.last_errors.pop(key, None)
            continue
        integrity = await asyncio.to_thread(_recovery_verify_media, temporary, "quick")
        if integrity.ok:
            temporary.replace(original)
            self.last_errors.pop(key, None)
            continue
        detail = str(integrity.error or temporary.name)
        if _finalizing_error_is_unrecoverable(detail):
            quarantine = _recovery_quarantine_path(temporary)
            try:
                temporary.replace(quarantine)
                note = quarantine.with_name(f"{quarantine.name}.txt")
                note.write_text(
                    "LiveVault recovery quarantine\n"
                    f"temporary={temporary}\n"
                    f"reason={detail}\n",
                    encoding="utf-8",
                )
                self.last_errors.pop(key, None)
            except OSError as exc:
                self.last_errors[key] = f"Recovery: impossibile isolare {temporary.name}: {exc}"[-1400:]
            continue
        self.last_errors[key] = f"Copia temporanea conservata: {detail}"[-1400:]


_processing_manager._recover_stale_finalizing_files = types.MethodType(
    _recover_stale_finalizing_files_safe, _processing_manager
)


# A failed verification/remux must not leave the Dashboard frozen forever at
# (for example) "Verifica audio/video · 84%".  Guard the singleton used by the
# app and keep the error visible briefly before clearing the transient progress.
_processing_original_stitch = _processing_manager._stitch_fragment_group


async def _guarded_processing_stitch(self, fragments, *, allow_transcode: bool = True):
    session_key = str(fragments[0].session_id) if fragments else ""
    try:
        return await _processing_original_stitch(fragments, allow_transcode=allow_transcode)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        current = getattr(self, "processing_current", None)
        if current and (not session_key or str(current.get("session_id") or "") == session_key):
            if isinstance(exc, BaseException) and exc.__class__.__name__ == "CancelledError":
                self.processing_current = None
            else:
                current["stage"] = "Errore"
                current["error"] = str(exc)[-900:]
                current["completed_at"] = _legacy.utcnow().isoformat()
                self.wake()
                if session_key:
                    self._schedule_processing_clear(session_key, delay=8.0)
        raise


_processing_manager._stitch_fragment_group = types.MethodType(
    _guarded_processing_stitch, _processing_manager
)


class SessionProcessingSettings(BaseModel):
    session_stitch_gap_minutes: int = Field(ge=1, le=120)


@app.patch("/api/session-processing/settings")
async def patch_session_processing_settings(body: SessionProcessingSettings, request: Request):
    _legacy.require_auth(request)
    from app.settings_store import public_settings, set_values
    from app.workers import manager

    set_values({"session_stitch_gap_minutes": int(body.session_stitch_gap_minutes)})
    manager.refresh_runtime_constants()
    manager.wake()
    return {"ok": True, "settings": public_settings()}


@app.post("/api/sources/{source_id}/process-now")
async def process_source_now(source_id: int, request: Request):
    _legacy.require_auth(request)
    from app.workers import manager

    with _legacy.db_session() as db:
        source = db.get(_legacy.Source, int(source_id))
        if source is None:
            raise HTTPException(404, "Sorgente non trovata")

    result = await manager.process_source_now(int(source_id))
    if result.get("ok"):
        return result
    reason = result.get("reason")
    if reason == "recording":
        raise HTTPException(409, result.get("message") or "La registrazione è ancora attiva")
    if reason == "standby":
        raise HTTPException(503, "Elaborazione disponibile solo sul worker attivo")
    raise HTTPException(409, result.get("message") or "Impossibile avviare l'elaborazione")


__all__ = ["app", "SessionProcessingSettings"]
