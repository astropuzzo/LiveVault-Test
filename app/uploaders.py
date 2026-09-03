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


class UploadCancelled(UploadError):
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


class _StreamingFile:
    """Length-aware file iterator used for streaming PUT bodies without chunked encoding."""

    def __init__(self, path: Path, callback=None, digest=None):
        self.path = path
        self.callback = callback
        self.digest = digest

    def __len__(self):
        return self.path.stat().st_size

    def __iter__(self):
        yield from _file_chunks(self.path, self.callback, digest=self.digest)


class _StreamingMultipart:
    """Iterable with a real length so requests does not add Transfer-Encoding: chunked."""

    def __init__(self, prefix: list[bytes], path: Path, trailer: bytes, callback, digest):
        self.prefix = prefix
        self.path = path
        self.trailer = trailer
        self.callback = callback
        self.digest = digest
        self.length = sum(len(x) for x in prefix) + path.stat().st_size + len(trailer)

    def __len__(self):
        return self.length

    def __iter__(self):
        yield from self.prefix
        yield from _file_chunks(self.path, self.callback, digest=self.digest)
        yield self.trailer


def _multipart_stream(path: Path, folder_id: str, callback: Callable[[int, int], None] | None, digest=None, token: str = ""):
    boundary = "----LiveVault" + uuid.uuid4().hex
    chunks: list[bytes] = []
    if token:
        chunks.append((
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="token"\r\n\r\n'
            f"{token}\r\n"
        ).encode())
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
    body = _StreamingMultipart(chunks, path, trailer, callback, digest)
    return boundary, len(body), body


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


def _gofile_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "LiveVault/2.2.0",
    }


def _gofile_auth_error(value) -> bool:
    text = str(value).lower()
    return any(x in text for x in ("error-wrongtoken", "error-notauthenticated", "http 401", "invalid token", "wrong token"))


def _gofile_account_root(token: str) -> str:
    headers = _gofile_headers(token)
    identity = _json(requests.get("https://api.gofile.io/accounts/getid", headers=headers, timeout=20), "Gofile")
    account_id = str((identity.get("data") or {}).get("id") or "")
    if not account_id:
        raise UploadError("Gofile: account id mancante")
    account = _json(requests.get(f"https://api.gofile.io/accounts/{account_id}", headers=headers, timeout=20), "Gofile")
    root_id = str((account.get("data") or {}).get("rootFolder") or "")
    if not root_id:
        raise UploadError("Gofile: cartella root non disponibile")
    return root_id


def create_gofile_folder(folder_name: str, parent_folder_id: str = "") -> tuple[str, str]:
    """Create a persistent public folder and return (content id, share URL)."""
    cfg = runtime()
    if not cfg.gofile_token:
        raise UploadError("Gofile API token non configurato")
    parent_id = parent_folder_id.strip() or _gofile_account_root(cfg.gofile_token)
    response = requests.post(
        "https://api.gofile.io/contents/createFolder",
        headers={**_gofile_headers(cfg.gofile_token), "Content-Type": "application/json"},
        json={"parentFolderId": parent_id, "folderName": folder_name[:120], "public": True},
        timeout=30,
    )
    payload = _json(response, "Gofile")
    if str(payload.get("status", "ok")).lower() not in {"ok", "success", "true"}:
        raise UploadError(f"Gofile: creazione cartella fallita ({payload.get('status')})")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    folder_id = str(data.get("id") or data.get("contentId") or "")
    code = str(data.get("code") or "")
    folder_url = str(data.get("downloadPage") or data.get("url") or (f"https://gofile.io/d/{code}" if code else ""))
    if not folder_id:
        raise UploadError("Gofile: identificatore della cartella mancante")
    return folder_id, folder_url


def move_gofile_contents(content_ids: list[str], folder_id: str) -> None:
    ids = [str(item).strip() for item in content_ids if str(item).strip()]
    if not ids:
        return
    cfg = runtime()
    response = requests.put(
        "https://api.gofile.io/contents/move",
        headers={**_gofile_headers(cfg.gofile_token), "Content-Type": "application/json"},
        json={"contentsId": ",".join(ids), "folderId": folder_id},
        timeout=60,
    )
    payload = _json(response, "Gofile")
    if str(payload.get("status", "ok")).lower() not in {"ok", "success", "true"}:
        raise UploadError(f"Gofile: spostamento non riuscito ({payload.get('status')})")


def upload_gofile(
    path: Path,
    progress: Callable[[int, int], None] | None = None,
    folder_id: str = "",
) -> UploadResult:
    cfg = runtime()
    if not cfg.gofile_token:
        raise UploadError("Gofile API token non configurato")
    headers = _gofile_headers(cfg.gofile_token)
    last_error: Exception | None = None
    for endpoint in _gofile_endpoints(cfg.gofile_region):
        for attempt in range(2):
            try:
                total = path.stat().st_size
                local_md5 = hashlib.md5()  # nosec B324 - compatibility checksum, not used for security
                boundary, content_length, body = _multipart_stream(
                    path, folder_id.strip() or cfg.gofile_folder_id, progress, digest=local_md5, token=cfg.gofile_token
                )
                request_headers = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}
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
                verified = md5_verified or size_verified
                if not verified:
                    if remote_md5:
                        raise UploadError("Gofile: checksum MD5 remoto diverso dal file locale")
                    if server_size is not None:
                        raise UploadError("Gofile: dimensione remota diversa dal file locale")
                    raise UploadError("Gofile: upload ricevuto ma risposta senza checksum/dimensione verificabile")
                return UploadResult("gofile", remote_id, remote_url, True, remote_size)
            except UploadCancelled:
                raise
            except Exception as exc:
                last_error = exc
                if _gofile_auth_error(exc):
                    raise UploadError(str(exc)) from exc
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
        data=_StreamingFile(path, progress, digest=local_sha256),
        auth=("", cfg.pixeldrain_api_key),
        headers={"Content-Type": "application/octet-stream"},
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


def create_pixeldrain_list(title: str, file_ids: list[str]) -> tuple[str, str]:
    """Create one stable album for a completed recording day."""
    cfg = runtime()
    if not cfg.pixeldrain_api_key:
        raise UploadError("Pixeldrain API key non configurata")
    ids = list(dict.fromkeys(str(item).strip() for item in file_ids if str(item).strip()))
    if not ids:
        raise UploadError("Pixeldrain: impossibile creare una lista vuota")
    response = requests.post(
        "https://pixeldrain.com/api/list",
        auth=("", cfg.pixeldrain_api_key),
        headers={"Content-Type": "application/json"},
        json={"title": title[:300], "anonymous": False, "files": [{"id": item} for item in ids[:10000]]},
        timeout=60,
    )
    payload = _json(response, "Pixeldrain")
    if not payload.get("success", True):
        raise UploadError(payload.get("message") or "Pixeldrain: creazione lista fallita")
    remote_id = str(payload.get("id") or "")
    if not remote_id:
        raise UploadError("Pixeldrain non ha restituito un list id")
    return remote_id, f"https://pixeldrain.com/l/{remote_id}"


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
        r = requests.get("https://api.gofile.io/accounts/getid", headers=_gofile_headers(cfg.gofile_token), timeout=20)
        if r.status_code == 401 or _gofile_auth_error((r.text or "")):
            r = requests.get(
                "https://api.gofile.io/accounts/getid",
                params={"token": cfg.gofile_token},
                headers={"Accept": "application/json", "User-Agent": "LiveVault/2.2.0"},
                timeout=20,
            )
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


def _upload_one(
    path: Path,
    provider: str,
    progress: Callable[[int, int], None] | None = None,
    gofile_folder_id: str = "",
) -> UploadResult:
    provider = provider.lower().strip()
    if provider == "gofile":
        return upload_gofile(path, progress, folder_id=gofile_folder_id)
    if provider == "pixeldrain":
        return upload_pixeldrain(path, progress)
    raise UploadError(f"Provider sconosciuto: {provider}")


def upload_with_fallback(
    path: Path,
    providers: list[str],
    progress: Callable[[int, int], None] | None = None,
    provider_started: Callable[[str], None] | None = None,
    uploader=None,
    gofile_folder_id: str = "",
) -> tuple[UploadResult | None, list[str]]:
    uploader = uploader or _upload_one
    errors: list[str] = []
    for provider in providers:
        if provider_started:
            provider_started(provider)
        try:
            if gofile_folder_id and provider == "gofile":
                result = uploader(path, provider, progress, gofile_folder_id=gofile_folder_id)
            else:
                result = uploader(path, provider, progress)
            if result.verified:
                return result, errors
            errors.append(f"{provider}: verifica remota non riuscita")
        except UploadCancelled:
            raise
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
    return None, errors


def upload(
    path: Path,
    provider: str,
    progress: Callable[[int, int], None] | None = None,
    gofile_folder_id: str = "",
) -> UploadResult:
    provider = provider.lower().strip()
    cfg = runtime()
    fallback = cfg.fallback_uploader.lower().strip()
    if provider == cfg.primary_uploader.lower().strip() and fallback not in {"", "none", provider} and provider_available(fallback):
        result, errors = upload_with_fallback(
            path,
            [provider, fallback],
            progress,
            uploader=_upload_one,
            gofile_folder_id=gofile_folder_id,
        )
        if result and result.verified:
            return result
        raise UploadError(" | ".join(errors) or "Upload fallito su primario e fallback")
    return _upload_one(path, provider, progress, gofile_folder_id=gofile_folder_id)
