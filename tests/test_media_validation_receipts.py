import asyncio
import contextlib
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

from app.media_validation import build_validation_receipt, validation_receipt_matches
from app.utils import IntegrityResult
from app import workers


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _integrity() -> IntegrityResult:
    return IntegrityResult(
        True,
        12.5,
        "",
        [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "Gap video rilevato: 0.80s senza frame continui",
    )


def test_receipt_requires_same_digest_size_mode_and_validator(tmp_path: Path):
    media = tmp_path / "final.mp4"
    media.write_bytes(b"same-final-bytes")
    digest = _digest(media)
    receipt = build_validation_receipt(media, digest, "packet", _integrity())

    assert validation_receipt_matches(receipt, digest=digest, size_bytes=media.stat().st_size, mode="packet")
    assert not validation_receipt_matches(receipt, digest="0" * 64, size_bytes=media.stat().st_size, mode="packet")
    assert not validation_receipt_matches(receipt, digest=digest, size_bytes=media.stat().st_size + 1, mode="packet")
    assert not validation_receipt_matches(receipt, digest=digest, size_bytes=media.stat().st_size, mode="quick")


def _exercise_verify(monkeypatch, media: Path, rec):
    manager = object.__new__(workers.WorkerManager)
    manager._retry_after = {}

    async def stable(_path):
        return True

    async def prepare(_path):
        return False

    manager._wait_until_stable = stable
    manager._prepare_mp4 = prepare
    current = SimpleNamespace()

    @contextlib.contextmanager
    def fake_db_session():
        yield SimpleNamespace(get=lambda _model, _id: current)

    monkeypatch.setattr(workers, "db_session", fake_db_session)
    monkeypatch.setattr(workers, "runtime", lambda: SimpleNamespace(integrity_mode="packet"))
    monkeypatch.setattr(workers, "sha256_file", _digest)
    return manager, current


def test_preupload_reuses_receipt_without_second_media_scan(tmp_path: Path, monkeypatch):
    media = tmp_path / "final.mp4"
    media.write_bytes(b"validated-final-bytes")
    digest = _digest(media)
    receipt = build_validation_receipt(media, digest, "packet", _integrity())
    rec = SimpleNamespace(id=7, sha256=digest, validation_receipt=receipt)
    manager, current = _exercise_verify(monkeypatch, media, rec)

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("full media scan must be skipped when the receipt matches")

    monkeypatch.setattr(workers, "verify_media", unexpected_scan)
    assert asyncio.run(manager._verify_before_upload(rec, media)) is True
    assert current.sha256 == digest
    assert current.validation_receipt == receipt
    assert current.integrity_status == "passed"


def test_same_size_same_mtime_byte_change_is_rejected_before_upload(tmp_path: Path, monkeypatch):
    media = tmp_path / "final.mp4"
    media.write_bytes(b"AAAA-final-bytes")
    stat = media.stat()
    digest = _digest(media)
    receipt = build_validation_receipt(media, digest, "packet", _integrity())
    rec = SimpleNamespace(id=8, sha256=digest, validation_receipt=receipt)

    media.write_bytes(b"BBBB-final-bytes")
    os.utime(media, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert media.stat().st_size == stat.st_size
    assert media.stat().st_mtime_ns == stat.st_mtime_ns

    manager, current = _exercise_verify(monkeypatch, media, rec)
    monkeypatch.setattr(workers, "verify_media", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("corrupt bytes must fail at SHA-256")))
    assert asyncio.run(manager._verify_before_upload(rec, media)) is False
    assert current.integrity_status == "failed"
    assert current.validation_receipt == ""
    assert "SHA-256" in current.integrity_error
