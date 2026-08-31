from __future__ import annotations

import time
import uuid
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests

from .settings_store import runtime


@dataclass
class UploadResult:
    provider: str
    remote_id: str
    remote_url: str
    verified: bool
    remote_size: int | None = None


class UploadError(RuntimeError):
    pass


def _file_chunks(path: Path, callback: Callable[[int, int], None] | None = None, chunk_size: int = 1024 * 1024, digest=None):
    total = path.stat().st_size
    sent = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            sent += len(chunk)
            if digest is not None:
                digest.update(chunk)
            if callback:
                callback(sent, total)
            yield chunk


def _multipart_stream(path: Path, folder_id: str, callback: Callable[[int, int], None] | None, digest=None):
    boundary = "----LiveVault" + uuid.uuid4().hex
    chunks: list[bytes] = []
    if folder_id:
        chunks.append((
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="folderId"\r\n\r\n'
            f"{folder_id}\r\n"
        ).encode())
    safe_filename = path.name.replace('"', '_').replace('\r', '_').replace('\n', '_')
    file_header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    trailer = f"\r\n--{boundary}--\r\n".encode()
    chunks.append(file_header)
    content_length = sum(len(x) for x in chunks) + path.stat().st_size + len(trailer)

    def iterator():
        for chunk in chunks:
            yield chunk
        yield from _file_chunks(path, callback, digest=digest)
        yield trailer

    return boundary, content_length, iterator()


def _response_error(response: requests.Response, provider: str) -> UploadError:
    content_type = (response.headers.get("content-type") or "").lower()
    detail = ""
    if "json" in content_type:
        try:
            payload = response.json()
            detail = str(payload.get("message") or payload.get("status") or payload.get("value") or payload)[:700]
        except Exception:
            pass
    if not detail:
        text = (response.text or "").strip().replace("\n", " ")
        detail = text[:700] or response.reason or "empty response"
    return UploadError(f"{provider} HTTP {response.status_code}: {detail}")


def _json(response: requests.Response, provider: str) -> dict:
    if response.status_code >= 400:
        raise _response_error(response, provider)
    try:
        return response.json()
    except Exception as exc:
        text = (response.text or "").strip().replace("\n", " ")[:500]
        raise UploadError(f"{provider} returned non-JSON HTTP {response.status_code}: {text or 'empty body'}") from exc


def _gofile_endpoints(region: str) -> list[str]:
    mapping = {
        "auto": "https://upload.gofile.io/uploadfile",
        "eu-par": "https://upload-eu-par.gofile.io/uploadfile",
        "na-phx": "https://upload-na-phx.gofile.io/uploadfile",
        "ap-sgp": "https://upload-ap-sgp.gofile.io/uploadfile",
        "ap-hkg": "https://upload-ap-hkg.gofile.io/uploadfile",
        "ap-tyo": "https://upload-ap-tyo.gofile.io/uploadfile",
        "sa-sao": "https://upload-sa-sao.gofile.io/uploadfile",
    }
    first = mapping.get(region, mapping["auto"])
    result = [first]
    if first != mapping["auto"]:
        result.append(mapping["auto"])
    elif region == "auto":
        result.append(mapping["eu-par"])
    return result


def upload_gofile(path: Path, progress: Callable[[int, int], None] | None = None) -> UploadResult:
    cfg = runtime()
    if not cfg.gofile_token:
        raise UploadError("Gofile API token non configurato")
    headers = {"Authorization": f"Bearer {cfg.gofile_token}"}
    last_error: Exception | None = None
    for endpoint in _gofile_endpoints(cfg.gofile_region):
        for attempt in range(2):
            try:
                total = path.stat().st_size
                local_md5 = hashlib.md5()  # nosec B324 - compatibility checksum, not used for security
                boundary, content_length, body = _multipart_stream(path, cfg.gofile_folder_id, progress, digest=local_md5)
                request_headers = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(content_length)}
                response = requests.post(
                    endpoint,
                    headers=request_headers,
                    data=body,
                    timeout=(20, 7200),
                )
                if response.status_code >= 500:
                    raise _response_error(response, "Gofile")
                payload = _json(response, "Gofile")
                status_value = str(payload.get("status", "ok")).lower()
                if status_value not in {"ok", "success", "true"}:
                    raise UploadError(str(payload)[:700])
                item = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                remote_id = str(item.get("id") or item.get("fileId") or item.get("contentId") or "")
                remote_url = str(item.get("downloadPage") or item.get("url") or item.get("directLink") or "")
                server_size = item.get("size")
                remote_md5 = str(item.get("md5") or "").lower().strip()
                if not remote_id and not remote_url:
                    raise UploadError("Gofile non ha restituito un identificatore remoto")
                remote_size = None
                size_verified = False
                if server_size is not None:
                    try:
                        remote_size = int(server_size)
                        size_verified = remote_size == total
                    except (TypeError, ValueError):
                        size_verified = False
                md5_verified = bool(remote_md5) and remote_md5 == local_md5.hexdigest().lower()
                # Gofile normally returns MD5 and current responses may also include size.
                # Never delete the local file unless at least one remote checksum/size check succeeds.
                verified = md5_verified or size_verified
                if not verified:
                    if remote_md5:
                        raise UploadError("Gofile: checksum MD5 remoto diverso dal file locale")
                    if server_size is not None:
                        raise UploadError("Gofile: dimensione remota diversa dal file locale")
                    raise UploadError("Gofile: upload ricevuto ma risposta senza checksum/dimensione verificabile")
                return UploadResult("gofile", remote_id, remote_url, True, remote_size)
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(2)
    raise UploadError(str(last_error or "Gofile upload failed"))


def upload_pixeldrain(path: Path, progress: Callable[[int, int], None] | None = None) -> UploadResult:
    cfg = runtime()
    if not cfg.pixeldrain_api_key:
        raise UploadError("Pixeldrain API key non configurata")
    encoded_name = quote(path.name, safe="")
    total = path.stat().st_size
    local_sha256 = hashlib.sha256()
    response = requests.put(
        f"https://pixeldrain.com/api/file/{encoded_name}",
        data=_file_chunks(path, progress, digest=local_sha256),
        auth=("", cfg.pixeldrain_api_key),
        headers={"Content-Type": "application/octet-stream", "Content-Length": str(total)},
        timeout=(20, 7200),
    )
    payload = _json(response, "Pixeldrain")
    if not payload.get("success", True):
        raise UploadError(payload.get("message") or "Pixeldrain upload failed")
    remote_id = str(payload.get("id") or "")
    if not remote_id:
        raise UploadError("Pixeldrain non ha restituito un file id")
    remote_size = payload.get("size")
    remote_sha256 = str(payload.get("hash_sha256") or "").lower().strip()
    if remote_size is None or not remote_sha256:
        info_resp = requests.get(f"https://pixeldrain.com/api/file/{remote_id}/info", timeout=30)
        info = _json(info_resp, "Pixeldrain")
        remote_size = info.get("size")
        remote_sha256 = str(info.get("hash_sha256") or "").lower().strip()
    try:
        size_verified = int(remote_size) == total
    except Exception:
        size_verified = False
    hash_verified = bool(remote_sha256) and remote_sha256 == local_sha256.hexdigest().lower()
    if not size_verified:
        raise UploadError("Pixeldrain: dimensione remota diversa dal file locale")
    if not hash_verified:
        raise UploadError("Pixeldrain: SHA-256 remoto diverso dal file locale")
    return UploadResult("pixeldrain", remote_id, f"https://pixeldrain.com/u/{remote_id}", True, int(remote_size))


def provider_available(provider: str) -> bool:
    cfg = runtime()
    provider = provider.lower().strip()
    if provider == "gofile":
        return bool(cfg.gofile_token)
    if provider == "pixeldrain":
        return bool(cfg.pixeldrain_api_key)
    return False


def test_provider(provider: str) -> dict:
    cfg = runtime()
    provider = provider.lower().strip()
    if provider == "gofile":
        if not cfg.gofile_token:
            raise UploadError("Gofile API token non configurato")
        r = requests.get("https://api.gofile.io/accounts/getid", headers={"Authorization": f"Bearer {cfg.gofile_token}"}, timeout=20)
        payload = _json(r, "Gofile")
        if str(payload.get("status", "")).lower() not in {"ok", "success"}:
            raise UploadError(f"Gofile: token rifiutato ({payload.get('status') or 'risposta non valida'})")
        account_id = str((payload.get("data") or {}).get("id") or payload.get("id") or "")
        if not account_id:
            raise UploadError("Gofile: token non verificabile, account id mancante")
        return {"ok": True, "provider": "gofile", "message": "Token Gofile valido", "account_id": account_id}
    if provider == "pixeldrain":
        if not cfg.pixeldrain_api_key:
            raise UploadError("Pixeldrain API key non configurata")
        r = requests.get("https://pixeldrain.com/api/user", auth=("", cfg.pixeldrain_api_key), timeout=20)
        payload = _json(r, "Pixeldrain")
        if payload.get("success") is False:
            raise UploadError(f"Pixeldrain: {payload.get('message') or payload.get('value') or 'API key rifiutata'}")
        return {
            "ok": True,
            "provider": "pixeldrain",
            "message": "API key Pixeldrain valida",
            "username": payload.get("username") or "",
            "can_upload": payload.get("can_upload", True),
        }
    raise UploadError(f"Provider sconosciuto: {provider}")


def upload(path: Path, provider: str, progress: Callable[[int, int], None] | None = None) -> UploadResult:
    provider = provider.lower().strip()
    if provider == "gofile":
        return upload_gofile(path, progress)
    if provider == "pixeldrain":
        return upload_pixeldrain(path, progress)
    raise UploadError(f"Provider sconosciuto: {provider}")
