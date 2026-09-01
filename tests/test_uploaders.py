import hashlib
from pathlib import Path

import requests

import app.uploaders as uploaders
from app.uploaders import UploadResult, _StreamingFile, _multipart_stream, _response_error, upload_with_fallback


class FakeResponse:
    def __init__(self, status_code=500, text="upstream exploded", content_type="text/html", payload=None):
        self.status_code = status_code
        self.text = text
        self.reason = "Server Error"
        self.headers = {"content-type": content_type}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_gofile_non_json_500_keeps_useful_diagnostic():
    err = _response_error(FakeResponse(), "Gofile")
    msg = str(err)
    assert "Gofile HTTP 500" in msg
    assert "upstream exploded" in msg
    assert "Invalid JSON" not in msg


def test_multipart_stream_has_exact_length_and_hashes_only_file(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    payload = b"abcdef" * 1000
    path.write_bytes(payload)
    digest = hashlib.md5()  # nosec B324 - test of provider compatibility checksum
    boundary, content_length, body = _multipart_stream(path, "folder-1", None, digest=digest, token="guest-token")
    wire = b"".join(body)
    assert len(wire) == content_length
    assert f"--{boundary}".encode() in wire
    assert b'name="token"' in wire
    assert b"guest-token" in wire
    assert b'name="folderId"' in wire
    assert b"folder-1" in wire
    assert b'name="file"' in wire
    assert digest.hexdigest() == hashlib.md5(payload).hexdigest()  # nosec B324


def test_gofile_streaming_body_is_not_chunked(tmp_path: Path):
    """Regression: a raw generator made requests emit both Content-Length and chunked."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x" * 4096)
    boundary, _, body = _multipart_stream(path, "", None, token="token")
    prepared = requests.Request(
        "POST",
        "https://upload.gofile.io/uploadfile",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    ).prepare()
    assert prepared.headers.get("Content-Length") == str(len(body))
    assert "Transfer-Encoding" not in prepared.headers


def test_pixeldrain_streaming_body_is_not_chunked(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x" * 4096)
    body = _StreamingFile(path)
    prepared = requests.Request(
        "PUT",
        "https://pixeldrain.com/api/file/clip.mp4",
        data=body,
        headers={"Content-Type": "application/octet-stream"},
    ).prepare()
    assert prepared.headers.get("Content-Length") == str(path.stat().st_size)
    assert "Transfer-Encoding" not in prepared.headers


def test_primary_failure_immediately_uses_fallback(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    calls = []

    def fake_upload(_path, provider, _progress):
        calls.append(provider)
        if provider == "gofile":
            raise RuntimeError("Gofile HTTP 401: error-wrongToken")
        return UploadResult("pixeldrain", "abc", "https://pixeldrain.com/u/abc", True, 5)

    result, errors = upload_with_fallback(path, ["gofile", "pixeldrain"], uploader=fake_upload)
    assert calls == ["gofile", "pixeldrain"]
    assert result is not None
    assert result.provider == "pixeldrain"
    assert result.verified is True
    assert errors == ["gofile: Gofile HTTP 401: error-wrongToken"]


def test_public_upload_function_wires_primary_to_fallback(monkeypatch, tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    calls = []

    class Cfg:
        primary_uploader = "gofile"
        fallback_uploader = "pixeldrain"

    monkeypatch.setattr(uploaders, "runtime", lambda: Cfg())
    monkeypatch.setattr(uploaders, "provider_available", lambda provider: provider == "pixeldrain")

    def fake_upload_one(_path, provider, _progress):
        calls.append(provider)
        if provider == "gofile":
            raise RuntimeError("bad primary")
        return UploadResult("pixeldrain", "abc", "https://pixeldrain.com/u/abc", True, 5)

    monkeypatch.setattr(uploaders, "_upload_one", fake_upload_one)
    result = uploaders.upload(path, "gofile")
    assert result.provider == "pixeldrain"
    assert calls == ["gofile", "pixeldrain"]


def test_gofile_folder_is_forwarded_only_to_gofile(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video")
    calls = []

    def fake_upload(_path, provider, _progress, **kwargs):
        calls.append((provider, kwargs))
        if provider == "gofile":
            raise RuntimeError("primary unavailable")
        return UploadResult("pixeldrain", "abc", "https://pixeldrain.com/u/abc", True, 5)

    result, errors = upload_with_fallback(
        path,
        ["gofile", "pixeldrain"],
        uploader=fake_upload,
        gofile_folder_id="camera-folder",
    )
    assert result is not None and result.provider == "pixeldrain"
    assert calls == [
        ("gofile", {"gofile_folder_id": "camera-folder"}),
        ("pixeldrain", {}),
    ]
    assert errors == ["gofile: primary unavailable"]


def test_create_gofile_folder_uses_requested_parent(monkeypatch):
    class Cfg:
        gofile_token = "secret-token"

    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse(
            status_code=200,
            content_type="application/json",
            payload={"status": "ok", "data": {"id": "folder-id", "code": "share-code"}},
        )

    monkeypatch.setattr(uploaders, "runtime", lambda: Cfg())
    monkeypatch.setattr(uploaders.requests, "post", fake_post)
    folder_id, folder_url = uploaders.create_gofile_folder("Camera", "parent-id")
    assert folder_id == "folder-id"
    assert folder_url == "https://gofile.io/d/share-code"
    assert captured["url"].endswith("/contents/createFolder")
    assert captured["json"] == {"parentFolderId": "parent-id", "folderName": "Camera", "public": True}
