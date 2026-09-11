import struct
from app.mp4_fragments import repair_fragment, repaired_copy


def box(kind, body):
    return struct.pack(">I4s", len(body) + 8, kind) + body


def fragment(entries=((3000, 0), (3000, 4)), timestamp=9000):
    clock = box(b"tfdt", b"\x01\0\0\0" + struct.pack(">Q", timestamp))
    run = box(b"trun", b"\0\0\x03\x01" + struct.pack(">II", len(entries), 100)
              + b"".join(struct.pack(">II", *entry) for entry in entries))
    return box(b"moof", box(b"traf", clock + run)) + box(b"mdat", b"data")


def test_empty_sample_repair_preserves_payload_offsets_and_time(tmp_path):
    original = fragment()
    fixed = repair_fragment(original)
    assert len(fixed) == len(original)
    assert fixed[fixed.index(b"mdat"):] == original[original.index(b"mdat"):]
    assert struct.unpack_from(">Q", fixed, fixed.index(b"tfdt") + 8)[0] == 12000
    run = fixed.index(b"trun")
    assert struct.unpack_from(">II", fixed, run + 8) == (1, 100)
    assert struct.unpack_from(">II", fixed, run + 16) == (3000, 4)
    assert b"free" in fixed
    assert repair_fragment(fixed) == fixed
    source, target = tmp_path / "original.mp4", tmp_path / "fixed.mp4"
    source.write_bytes(original)
    assert repaired_copy(source, target)
    assert source.read_bytes() == original
    assert target.read_bytes() == fixed


def test_unknown_or_nonleading_empty_samples_are_not_guessed(tmp_path):
    for original in (fragment(((3000, 4), (3000, 0))), fragment(((3000, 0),)),
                     fragment(timestamp=2**64 - 1), b"truncated", fragment()[:-1]):
        assert repair_fragment(original) == original
    source, target = tmp_path / "original.mp4", tmp_path / "fixed.mp4"
    source.write_bytes(fragment(((3000, 4),)))
    assert not repaired_copy(source, target)
    assert not target.exists()


def test_mouflon_rejects_dead_variant_and_tries_next_edge(monkeypatch):
    from app import stripchat_capture
    legacy = stripchat_capture._legacy
    monkeypatch.setattr(legacy, "HLS_EDGE_HOSTS", ("dead.test", "healthy.test"))
    monkeypatch.setattr(legacy, "_mouflon_tags", lambda _: [("v2", "key")])
    calls = []
    class Response:
        def __init__(self, text, status=200):
            self.text, self.status_code = text, status
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("404")
    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            if "_auto" in url:
                return Response("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nmedia.m3u8\n")
            return Response("#EXTM3U\n#EXT-X-TARGETDURATION:2\n", 404 if "dead.test" in url else 200)
    selection, _ = legacy.select_master(Session(), "42", "best", {"key": "secret"})
    assert "healthy.test" in selection.media_url
    assert len(calls) == 4

def test_finalization_repairs_separate_input_then_cleans_up(tmp_path, monkeypatch):
    import asyncio
    from app import recorder
    path = tmp_path / "part.capture.mp4"
    original = fragment()
    path.write_bytes(original)
    calls = []
    monkeypatch.setattr(recorder, "mp4_is_streaming_ready", lambda _: False)
    monkeypatch.setattr(recorder.storage_handoff, "checkpoint", lambda: None)
    async def finalize(source, output, size):
        calls.append(source)
        if source == path:
            raise RuntimeError("error reading header")
        assert path.read_bytes() == original
        assert source.read_bytes() == repair_fragment(original)
        output.write_bytes(b"validated output")
    monkeypatch.setattr(recorder, "_finalize_with_av_fallback", finalize)
    assert asyncio.run(recorder.finalize_mp4_for_streaming(path, require_space=False))
    assert path.read_bytes() == b"validated output"
    assert len(calls) == 2
    assert not list(tmp_path.glob(".*"))


def test_failed_repaired_validation_keeps_original(tmp_path, monkeypatch):
    import asyncio
    import pytest
    from app import recorder
    path = tmp_path / "part.capture.mp4"
    original = fragment()
    path.write_bytes(original)
    monkeypatch.setattr(recorder, "mp4_is_streaming_ready", lambda _: False)
    monkeypatch.setattr(recorder.storage_handoff, "checkpoint", lambda: None)
    async def fail(source, output, size):
        output.write_bytes(b"invalid")
        raise RuntimeError("error reading header" if source == path else "invalid A/V")
    monkeypatch.setattr(recorder, "_finalize_with_av_fallback", fail)
    with pytest.raises(RuntimeError, match="invalid A/V"):
        asyncio.run(recorder.finalize_mp4_for_streaming(path, require_space=False))
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".*"))
