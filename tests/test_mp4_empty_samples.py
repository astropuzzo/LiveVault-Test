import struct
import pytest
from app.mp4_fragments import repair_fragment, repaired_copy


def box(kind, body):
    return struct.pack(">I4s", len(body) + 8, kind) + body


def fragment(entries=((3000, 0), (3000, 4)), timestamp=9000, flags=0x301):
    clock = box(b"tfdt", b"\x01\0\0\0" + struct.pack(">Q", timestamp))
    run = box(b"trun", flags.to_bytes(4, "big") + struct.pack(">II", len(entries), 100)
              + (struct.pack(">I", 0x02000000) if flags & 4 else b"")
              + b"".join(struct.pack(">" + "I" * len(entry), *entry) for entry in entries))
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


def test_unknown_or_unrepresentable_empty_samples_are_not_guessed(tmp_path):
    for original in (fragment(((3000, 0),)),
                     fragment(flags=0x302),
                     fragment(((2**32 - 1, 4), (1, 0))),
                     fragment(timestamp=2**64 - 1), b"truncated", fragment()[:-1]):
        assert repair_fragment(original) == original
    source, target = tmp_path / "original.mp4", tmp_path / "fixed.mp4"
    source.write_bytes(fragment(((3000, 4),)))
    assert not repaired_copy(source, target)
    assert not target.exists()


@pytest.mark.parametrize("flags", [0x301, 0x305, 0x701])
@pytest.mark.parametrize("sizes", [(4, 0, 4), (4, 0, 0), (0, 0, 4, 0, 4, 0)])
def test_internal_and_trailing_empty_samples_keep_packet_timestamps_and_flags(flags, sizes, tmp_path):
    entries = [(2970 + i * 90, size) + ((0x02000000 + i,) if flags == 0x701 else ())
               for i, size in enumerate(sizes)]
    original = fragment(entries, flags=flags)
    fixed = repair_fragment(original)
    assert len(fixed) == len(original)
    assert fixed[fixed.index(b"mdat"):] == original[original.index(b"mdat"):]
    run = fixed.index(b"trun") - 4
    count, offset = struct.unpack_from(">II", fixed, run + 12)
    assert count == sum(size > 0 for size in sizes)
    assert offset == 100
    first_flags_retained = flags == 0x305 and sizes[0] != 0
    header = 24 if first_flags_retained else 20
    stride = 12 if flags == 0x701 else 8
    assert int.from_bytes(fixed[run + 9:run + 12], "big") == (flags if sizes[0] else flags & ~4)
    if first_flags_retained:
        assert struct.unpack_from(">I", fixed, run + 20)[0] == 0x02000000
    expected = []
    time = 9000
    for entry in entries:
        if entry[1]:
            expected.append((time, *entry[1:]))
        time += entry[0]
    actual = []
    timestamp = struct.unpack_from(">Q", fixed, fixed.index(b"tfdt") + 8)[0]
    for i in range(count):
        entry = struct.unpack_from(">" + "I" * (stride // 4), fixed, run + header + i * stride)
        actual.append((timestamp, *entry[1:]))
        timestamp += entry[0]
    assert actual == expected
    assert timestamp == time  # Including the trailing empty duration.
    assert repair_fragment(fixed) == fixed
    source, target = tmp_path / "original.mp4", tmp_path / "fixed.mp4"
    source.write_bytes(original)
    assert repaired_copy(source, target)
    assert source.read_bytes() == original
    assert target.read_bytes() == fixed


def test_auxiliary_sample_tables_are_not_silently_reindexed():
    original = fragment()
    moof_size = struct.unpack_from(">I", original)[0]
    traf_body = original[16:moof_size] + box(b"senc", b"\0" * 8)
    original = box(b"moof", box(b"traf", traf_body)) + original[moof_size:]
    assert repair_fragment(original) == original


@pytest.mark.parametrize("flags", [0x301, 0x305, 0x701])
def test_capture_normalizes_download_before_writing(tmp_path, monkeypatch, flags):
    from types import SimpleNamespace
    from app.stripchat_capture import _legacy as capture
    entries = [(3000, size) + ((0x01010000,) if flags == 0x701 else ()) for size in (4, 0, 4, 0)]
    original = fragment(entries, flags=flags)
    init = box(b"ftyp", b"isom")
    playlist = capture.MediaPlaylist("init", (capture.MediaSegment("media", "one"),), 2, True)
    selection = capture.MasterSelection("playlist", "", "", "")
    session = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=200, text="#EXTM3U"))
    monkeypatch.setattr(capture, "STOP_REQUESTED", False)
    monkeypatch.setattr(capture, "make_session", lambda: session)
    monkeypatch.setattr(capture, "get_cam_state", lambda *a, **k: ("42", {}))
    monkeypatch.setattr(capture, "_public_stream_id", lambda *a: "42")
    monkeypatch.setattr(capture, "_load_key_file", lambda: {})
    monkeypatch.setattr(capture, "select_master", lambda *a: (selection, {}))
    monkeypatch.setattr(capture, "parse_media_playlist", lambda *a: playlist)
    monkeypatch.setattr(capture, "_download_bytes", lambda _, url, __: init if url == "init" else original)
    captured = []
    monkeypatch.setattr(capture, "_remux", lambda raw, *a: captured.append(raw.read_bytes()))
    capture.capture(SimpleNamespace(slug="example", quality="best", output_pattern=str(tmp_path / "part%03d.mp4"),
                                    video_preview_base="", max_bytes=1000000, segment_seconds=3600, container="mp4"))
    assert captured == [init + repair_fragment(original)]
    assert captured[0] != init + original


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
