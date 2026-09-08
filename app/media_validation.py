from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALIDATOR_VERSION = "media-integrity-v1"


def build_validation_receipt(path: Path, digest: str, mode: str, integrity: Any) -> str:
    """Serialize proof that a specific byte sequence passed the current validator."""
    payload = {
        "version": VALIDATOR_VERSION,
        "mode": str(mode or "packet"),
        "sha256": str(digest or ""),
        "size_bytes": int(path.stat().st_size),
        "ok": bool(integrity.ok),
        "duration": integrity.duration,
        "warning": str(getattr(integrity, "warning", "") or ""),
        "streams": list(getattr(integrity, "streams", None) or []),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def parse_validation_receipt(raw: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def validation_receipt_matches(raw: str, *, digest: str, size_bytes: int, mode: str) -> bool:
    payload = parse_validation_receipt(raw)
    return bool(
        payload
        and payload.get("version") == VALIDATOR_VERSION
        and payload.get("mode") == str(mode or "packet")
        and payload.get("sha256") == digest
        and payload.get("size_bytes") == int(size_bytes)
        and payload.get("ok") is True
    )


def integrity_from_validation_receipt(raw: str):
    payload = parse_validation_receipt(raw) or {}
    from .utils import IntegrityResult

    return IntegrityResult(
        ok=bool(payload.get("ok")),
        duration=payload.get("duration"),
        error="",
        streams=list(payload.get("streams") or []),
        warning=str(payload.get("warning") or ""),
    )
