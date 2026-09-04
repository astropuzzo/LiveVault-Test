from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import types
from pathlib import Path
from typing import Any

import requests

from app.stripchat_state import classify_stripchat_cam


# Compatibility facade over the existing single-file provider implementation.
# Keeping the mature adapters untouched lets this package make the id-based
# Stripchat cam state authoritative without duplicating every other provider.
# Python prefers this package over app/source_providers.py, just as the project
# already does for app/stripchat_capture.
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "source_providers.py"
_LEGACY_NAME = "app._source_providers_legacy"
_spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - installation guard
    raise RuntimeError("Cannot load source provider implementation")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules[_LEGACY_NAME] = _legacy
_spec.loader.exec_module(_legacy)

# Public types are aliases, not copies, so recorder/tests keep exact identity.
ProbeResult = _legacy.ProbeResult
ResolvedInput = _legacy.ResolvedInput
InputAudit = _legacy.InputAudit
ProviderSpec = _legacy.ProviderSpec

_NATIVE_STRIPCHAT_PREFIX = "stripchat-native://"
_ORIGINAL_STRIPCHAT_BROADCAST_INFO = _legacy.stripchat_broadcast_info

_EXPECTED_OFFLINE_TEXT = (
    "offline",
    "not live",
    "not currently broadcasting",
    "not broadcasting",
    "no live stream",
    "no active stream",
    "channel is not live",
    "livestream is offline",
    "room is not available",
    "model is offline",
    "stream is unavailable",
)


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_legacy)))


class _ProviderFacade(types.ModuleType):
    """Forward monkeypatches/compat assignments to the legacy module globals."""

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if hasattr(_legacy, name):
            setattr(_legacy, name, value)


# Tests and downstream code historically monkeypatch attributes directly on
# app.source_providers.  Forward those writes so legacy functions see them too.
sys.modules[__name__].__class__ = _ProviderFacade


def stripchat_cam_info(slug: str) -> tuple[int, dict[str, Any]]:
    """Fetch Stripchat's authoritative id-based cam descriptor."""
    from app import stripchat_capture

    session = requests.Session()
    return stripchat_capture.get_cam_state(session, slug.strip("/"))


def _stripchat_broadcast_result(item: dict[str, Any], slug: str) -> ProbeResult:
    """Fallback only when the cam endpoint itself cannot be reached."""
    live = item.get("isLive") is True
    raw_status = str(item.get("status") or "").strip().lower()
    status_key = re.sub(r"[^a-z0-9]", "", raw_status)

    # High-level broadcast descriptors can lag behind the cam endpoint.  Even
    # in fallback mode, explicit offline states must beat a stale isLive=True.
    if status_key in {"off", "idle", "offline"}:
        return ProbeResult(False, "offline", metadata_status="unsupported")

    unavailable = _legacy.inaccessible_status(raw_status) if live else ""
    if not live:
        return ProbeResult(False, "offline", metadata_status="unsupported")
    if unavailable:
        return ProbeResult(True, unavailable, recordable=False, title=slug, metadata_status="unsupported")
    if raw_status and status_key not in {"public", "live"}:
        return ProbeResult(True, "unknown", recordable=False, title=slug, metadata_status="unsupported")
    if not (item.get("streamName") or item.get("modelId")):
        return ProbeResult(
            True,
            "error",
            recordable=False,
            title=slug,
            error="Stripchat public stream identifier is missing",
            metadata_status="unsupported",
        )
    return ProbeResult(True, "live", recordable=True, title=slug, metadata_status="unsupported")


async def _probe_stripchat(slug: str, quality: str) -> ProbeResult:
    del quality  # quality is selected by the native recorder, not by the state endpoint

    # Preserve the long-standing unit-test/downstream monkeypatch surface: when
    # broadcast_info was explicitly replaced, use that injected source.
    if _legacy.stripchat_broadcast_info is not _ORIGINAL_STRIPCHAT_BROADCAST_INFO:
        try:
            item = await asyncio.to_thread(_legacy.stripchat_broadcast_info, slug)
            return _stripchat_broadcast_result(item, slug)
        except Exception as exc:
            return ProbeResult(False, "error", error=str(exc)[-700:], metadata_status="unsupported")

    try:
        user_id, payload = await asyncio.to_thread(stripchat_cam_info, slug)
        state = classify_stripchat_cam(payload, user_id)
    except Exception as cam_exc:
        # Network/API failure is different from a known offline/private state.
        # The older broadcast endpoint remains a best-effort availability
        # fallback, but it is never consulted after a valid cam classification.
        try:
            item = await asyncio.to_thread(_legacy.stripchat_broadcast_info, slug)
            result = _stripchat_broadcast_result(item, slug)
            if result.status != "error":
                return result
            result.error = f"cam endpoint: {cam_exc} | {result.error}"[-900:]
            return result
        except Exception as broadcast_exc:
            return ProbeResult(
                False,
                "error",
                error=f"Stripchat cam endpoint: {cam_exc} | broadcast fallback: {broadcast_exc}"[-1000:],
                metadata_status="unsupported",
            )

    if state.status == "live":
        return ProbeResult(True, "live", recordable=True, title=slug, metadata_status="unsupported")
    if state.status in {"private", "tipjar", "restricted"}:
        return ProbeResult(True, state.status, recordable=False, title=slug, metadata_status="unsupported")
    if state.status == "offline":
        return ProbeResult(False, "offline", recordable=False, title=slug, metadata_status="unsupported")
    return ProbeResult(
        state.live,
        "unknown",
        recordable=False,
        title=slug,
        error="",
        metadata_status="unsupported",
    )


def _normalize_expected_result(result: ProbeResult) -> ProbeResult:
    """Never promote a normal access/offline state to an operational error."""
    if result.status != "error" or not result.error:
        return result
    unavailable = _legacy.inaccessible_status(result.error)
    if unavailable:
        result.live = True
        result.status = unavailable
        result.recordable = False
        result.error = ""
        return result
    lowered = result.error.lower()
    if any(token in lowered for token in _EXPECTED_OFFLINE_TEXT):
        result.live = False
        result.status = "offline"
        result.recordable = False
        result.error = ""
    return result


async def probe(platform: str, slug: str, quality: str = "best") -> ProbeResult:
    if platform == "stripchat":
        return await _probe_stripchat(slug, quality)
    return _normalize_expected_result(await _legacy.probe(platform, slug, quality))


async def resolve_inputs(platform: str, slug: str, quality: str = "best") -> list[ResolvedInput]:
    if platform == "stripchat":
        # Stripchat is consumed by the native Flashphoner/Mouflon recorder, so
        # the generic yt-dlp/ffprobe preflight has no directly consumable URL.
        # A typed sentinel lets /api/sources/inspect report the native A/V path
        # without the obsolete "dedicated WebRTC recorder" error.
        return [ResolvedInput(f"{_NATIVE_STRIPCHAT_PREFIX}{slug.strip('/')}", {}, "media")]
    return await _legacy.resolve_inputs(platform, slug, quality)


async def audit_inputs(inputs: list[ResolvedInput], timeout: float = 18.0) -> InputAudit:
    if inputs and all(str(item.url).startswith(_NATIVE_STRIPCHAT_PREFIX) for item in inputs):
        # Native Stripchat capture validates both tracks on every finalized part;
        # do not launch a second extractor/decoder during a source inspection.
        return InputAudit(True, True, "")
    return await _legacy.audit_inputs(inputs, timeout=timeout)


__all__ = [
    "ProbeResult",
    "ResolvedInput",
    "InputAudit",
    "ProviderSpec",
    "probe",
    "resolve_inputs",
    "audit_inputs",
    "stripchat_cam_info",
]
