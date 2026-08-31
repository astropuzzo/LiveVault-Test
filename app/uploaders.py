from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

from .config import settings


@dataclass
class UploadResult:
    provider: str
    remote_id: str
    remote_url: str
    verified: bool


class UploadError(RuntimeError):
    pass


def _json_or_raise(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except Exception as exc:
        raise UploadError(f"Invalid JSON response ({response.status_code})") from exc
    if response.status_code >= 400:
        message = payload.get("message") or payload.get("status") or response.text[:300]
        raise UploadError(f"HTTP {response.status_code}: {message}")
    return payload


def upload_gofile(path: Path) -> UploadResult:
    if not settings.gofile_token and not settings.allow_gofile_guest:
        raise UploadError("GOFILE_TOKEN is not configured and guest upload is disabled")
    headers = {}
    if settings.gofile_token:
        headers["Authorization"] = f"Bearer {settings.gofile_token}"
    data = {}
    if settings.gofile_folder_id:
        data["folderId"] = settings.gofile_folder_id
    with path.open("rb") as f:
        response = requests.post(
            settings.gofile_upload_endpoint,
            headers=headers,
            data=data,
            files={"file": (path.name, f, "application/octet-stream")},
            timeout=(20, 7200),
        )
    payload = _json_or_raise(response)
    status_value = str(payload.get("status", "ok")).lower()
    if status_value not in {"ok", "success", "true"}:
        raise UploadError(str(payload)[:500])
    item = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    remote_id = str(item.get("id") or item.get("fileId") or item.get("contentId") or "")
    remote_url = str(item.get("downloadPage") or item.get("url") or item.get("directLink") or "")
    server_size = item.get("size")
    verified = bool(remote_id or remote_url)
    if server_size is not None:
        try:
            verified = verified and int(server_size) == path.stat().st_size
        except (TypeError, ValueError):
            pass
    if not remote_id and not remote_url:
        raise UploadError("Gofile upload response did not include a remote identifier")
    return UploadResult("gofile", remote_id, remote_url, verified)


def upload_pixeldrain(path: Path) -> UploadResult:
    if not settings.pixeldrain_api_key:
        raise UploadError("PIXELDRAIN_API_KEY is not configured")
    encoded_name = quote(path.name, safe="")
    with path.open("rb") as f:
        response = requests.put(
            f"https://pixeldrain.com/api/file/{encoded_name}",
            data=f,
            auth=("", settings.pixeldrain_api_key),
            headers={"Content-Type": "application/octet-stream"},
            timeout=(20, 7200),
        )
    payload = _json_or_raise(response)
    if not payload.get("success", True):
        raise UploadError(payload.get("message") or "Pixeldrain upload failed")
    remote_id = str(payload.get("id") or "")
    if not remote_id:
        raise UploadError("Pixeldrain did not return a file id")

    info_resp = requests.get(
        f"https://pixeldrain.com/api/file/{remote_id}/info",
        auth=("", settings.pixeldrain_api_key),
        timeout=30,
    )
    info = _json_or_raise(info_resp)
    verified = bool(info.get("success")) and int(info.get("size", -1)) == path.stat().st_size
    if not verified:
        raise UploadError("Pixeldrain upload could not be size-verified")
    return UploadResult("pixeldrain", remote_id, f"https://pixeldrain.com/u/{remote_id}", True)


def provider_available(provider: str) -> bool:
    provider = provider.lower().strip()
    if provider == "gofile":
        return bool(settings.gofile_token) or settings.allow_gofile_guest
    if provider == "pixeldrain":
        return bool(settings.pixeldrain_api_key)
    if provider in {"none", "local", ""}:
        return False
    return False


def upload(path: Path, provider: str) -> UploadResult:
    provider = provider.lower().strip()
    if provider == "gofile":
        return upload_gofile(path)
    if provider == "pixeldrain":
        return upload_pixeldrain(path)
    raise UploadError(f"Unknown upload provider: {provider}")
