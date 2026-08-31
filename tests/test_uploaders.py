import hashlib
from pathlib import Path

from app.uploaders import _multipart_stream, _response_error


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
    boundary, content_length, body = _multipart_stream(path, "folder-1", None, digest=digest)
    wire = b"".join(body)
    assert len(wire) == content_length
    assert f"--{boundary}".encode() in wire
    assert b'name="folderId"' in wire
    assert b'name="file"' in wire
    assert digest.hexdigest() == hashlib.md5(payload).hexdigest()  # nosec B324
