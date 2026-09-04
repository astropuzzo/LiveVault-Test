from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Stripchat's current cam endpoint uses these states.  In particular, ``idle``
# is an offline state, not an operational error.  Keep camelCase variants
# normalized so provider changes do not leak raw status strings into the UI.
_PRIVATE_KEYS = {
    "private",
    "groupshow",
    "group",
    "p2p",
    "virtualprivate",
    "p2pvoice",
    "exclusive",
}
_TIPJAR_KEYS = {
    "ticket",
    "ticketshow",
    "hidden",
    "away",
    "tipjar",
}
_OFFLINE_KEYS = {
    "off",
    "idle",
    "offline",
}


def _status_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


@dataclass(frozen=True)
class StripchatCamState:
    status: str
    raw_status: str
    live: bool
    recordable: bool
    stream_id: str
    reason: str = ""


class StripchatExpectedState(RuntimeError):
    """A normal non-public room state observed while capture is starting/running."""

    def __init__(self, state: StripchatCamState) -> None:
        self.state = state
        raw = f" ({state.raw_status})" if state.raw_status else ""
        super().__init__(f"Stripchat state is {state.status}{raw}")


def classify_stripchat_cam(payload: dict[str, Any], user_id: int | str) -> StripchatCamState:
    """Classify Stripchat's id-based /models/{id}/cam payload.

    The cam endpoint is authoritative for capture because it exposes both the
    model status and whether a public camera is actually active/available.  A
    stale high-level ``isLive`` flag must never turn ``idle`` into an error or
    start a recorder against a non-public room.
    """
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    model = user.get("user") if isinstance(user.get("user"), dict) else {}
    cam = payload.get("cam") if isinstance(payload.get("cam"), dict) else {}

    raw_status = str(model.get("status") or "").strip()
    key = _status_key(raw_status)
    active = cam.get("isCamActive") is True
    available = cam.get("isCamAvailable") is True
    online_flag = model.get("isOnline")
    geo_banned = user.get("isGeoBanned") is True
    deleted = model.get("isDeleted") is True
    stream_id = str(cam.get("streamName") or model.get("id") or user_id or "").strip()

    if deleted:
        return StripchatCamState("offline", raw_status, False, False, stream_id, "deleted")

    # Explicit provider states win over possibly stale camera booleans.
    if key in _OFFLINE_KEYS:
        return StripchatCamState("offline", raw_status, False, False, stream_id)
    if key in _PRIVATE_KEYS:
        return StripchatCamState("private", raw_status, True, False, stream_id)
    if key in _TIPJAR_KEYS:
        return StripchatCamState("tipjar", raw_status, True, False, stream_id)

    if key == "public":
        if not active or not available:
            # Stripchat can briefly leave status=public around a transition.
            # No active+available public camera means there is nothing to record.
            return StripchatCamState("offline", raw_status, False, False, stream_id, "camera unavailable")
        if geo_banned:
            return StripchatCamState("restricted", raw_status, True, False, stream_id, "geo restricted")
        if not stream_id:
            return StripchatCamState("unknown", raw_status, True, False, "", "missing stream id")
        return StripchatCamState("live", raw_status, True, True, stream_id)

    if geo_banned and (active or online_flag is True):
        return StripchatCamState("restricted", raw_status, True, False, stream_id, "geo restricted")
    if online_flag is False:
        return StripchatCamState("offline", raw_status, False, False, stream_id)
    if active and available:
        # Fail closed on a new provider status: surface it as online/unknown but
        # do not accidentally record a newly introduced paid/private mode.
        return StripchatCamState("unknown", raw_status, True, False, stream_id, "unrecognized active state")
    return StripchatCamState("unknown", raw_status, False, False, stream_id, "unrecognized state")
