from __future__ import annotations

import importlib.util
import sys
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
