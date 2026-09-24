import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from app.mp4_index import NotFragmented, build_index, hls_playlist


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def synthetic_fmp4(fragments: int, samples: int = 25, sample_ticks: int = 512, timescale: int = 12800, grow: bool = False) -> bytes:
    tkhd = full(b"tkhd", 0, 3, struct.pack(">III", 0, 0, 1) + bytes(68))
    mdhd = full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, timescale, 0) + bytes(4))
    hdlr = full(b"hdlr", 0, 0, bytes(4) + b"vide" + bytes(12) + b"v\x00")
    trak = box(b"trak", tkhd + box(b"mdia", mdhd + hdlr))
    trex = full(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, sample_ticks, 0, 0))
    moov = box(b"moov", full(b"mvhd", 0, 0, bytes(96)) + trak + box(b"mvex", trex))
    data = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso5") + moov
    for index in range(fragments):
        tfhd = full(b"tfhd", 0, 0x020000, struct.pack(">I", 1))
        tfdt = full(b"tfdt", 1, 0, struct.pack(">Q", index * samples * sample_ticks))
        trun = full(b"trun", 0, 0x000201, struct.pack(">II", samples, 0) + struct.pack(">I", 10) * samples)
        data += box(b"moof", full(b"mfhd", 0, 0, struct.pack(">I", index + 1)) + box(b"traf", tfhd + tfdt + trun))
        data += box(b"mdat", bytes(10 * samples))
    if grow:
        data += struct.pack(">I4s", 5000, b"moof") + bytes(20)
    return data


def test_index_measures_full_duration_and_groups_segments(tmp_path: Path):
    path = tmp_path / "capture.mp4"
    path.write_bytes(synthetic_fmp4(30))  # 30 fragments x 1 s
    index = build_index(path, target_seconds=6)
    assert index.complete
    assert index.duration == pytest.approx(30.0)
    assert [round(s.duration) for s in index.segments] == [6, 6, 6, 6, 6]
    assert index.segments[0].offset == index.init_length
    ends = [s.offset + s.length for s in index.segments]
    assert ends[-1] == path.stat().st_size
    playlist = hls_playlist(index, "/api/recordings/1/view")
    assert f'#EXT-X-MAP:URI="/api/recordings/1/view",BYTERANGE="{index.init_length}@0"' in playlist
    assert playlist.count("#EXT-X-BYTERANGE:") == 5
    assert playlist.rstrip().endswith("#EXT-X-ENDLIST")


def test_growing_capture_becomes_event_playlist(tmp_path: Path):
    path = tmp_path / "growing.mp4"
    path.write_bytes(synthetic_fmp4(8, grow=True))
    index = build_index(path)
    assert not index.complete
    assert index.duration == pytest.approx(8.0)
    playlist = hls_playlist(index, "/media", live=True)
    assert "#EXT-X-PLAYLIST-TYPE:EVENT" in playlist
    assert "#EXT-X-ENDLIST" not in playlist


def test_regular_and_non_mp4_files_are_left_to_direct_playback(tmp_path: Path):
    regular = tmp_path / "regular.mp4"
    moov = box(b"moov", full(b"mvhd", 0, 0, bytes(96)))
    regular.write_bytes(box(b"ftyp", b"isom\x00\x00\x02\x00") + moov + box(b"mdat", bytes(64)))
    with pytest.raises(NotFragmented):
        build_index(regular)
    other = tmp_path / "clip.mkv"
    other.write_bytes(b"\x1aE\xdf\xa3" + bytes(64))
    with pytest.raises(NotFragmented):
        build_index(other)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_real_ffmpeg_fragmented_capture(tmp_path: Path):
    path = tmp_path / "real.mp4"
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20", "-c:v", "libx264", "-g", "25",
        "-c:a", "aac", "-movflags", "+frag_keyframe+empty_moov+default_base_moof", str(path),
    ], check=True)
    index = build_index(path)
    assert index.complete
    assert index.duration == pytest.approx(20.0, abs=0.2)
