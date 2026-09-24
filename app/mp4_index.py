"""Seekable HLS playlists for fragmented MP4 captures, without remuxing.

Capture files are written as fragmented MP4 (``empty_moov``): the header has no
duration, so a browser discovers the length only while downloading and cannot
seek ahead. This module reads just the box headers (``moov`` and every small
``moof``; ``mdat`` payloads are skipped with a seek), measures each fragment's
duration from the video track and returns an HLS byte-range playlist. The
player then knows the full timeline at once and fetches any point with a Range
request against the existing file endpoint.
"""
from __future__ import annotations

import math
import os
import struct
import threading
from dataclasses import dataclass
from pathlib import Path


class NotFragmented(ValueError):
    """The file is a regular (seekable) MP4 or not an MP4 at all."""


@dataclass(frozen=True)
class Segment:
    offset: int
    length: int
    duration: float


@dataclass(frozen=True)
class FragmentIndex:
    init_length: int
    segments: tuple[Segment, ...]
    complete: bool

    @property
    def duration(self) -> float:
        return sum(segment.duration for segment in self.segments)


def _children(data: bytes, start: int, end: int):
    while start + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, start)
        header = 8
        if size == 1:
            if start + 16 > end:
                return
            size = struct.unpack_from(">Q", data, start + 8)[0]
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            return
        yield kind, start + header, start + size
        start += size


def _child(data: bytes, start: int, end: int, name: bytes):
    for kind, body, stop in _children(data, start, end):
        if kind == name:
            return body, stop
    return None


def _parse_moov(moov: bytes) -> tuple[dict[int, tuple[int, bytes]], dict[int, int], bool]:
    """Return {track_id: (timescale, handler)}, {track_id: default duration}, fragmented."""
    tracks: dict[int, tuple[int, bytes]] = {}
    defaults: dict[int, int] = {}
    fragmented = False
    for kind, body, stop in _children(moov, 8, len(moov)):
        if kind == b"trak":
            tkhd = _child(moov, body, stop, b"tkhd")
            mdia = _child(moov, body, stop, b"mdia")
            if not tkhd or not mdia:
                continue
            version = moov[tkhd[0]]
            track_id = struct.unpack_from(">I", moov, tkhd[0] + (20 if version == 1 else 12))[0]
            mdhd = _child(moov, mdia[0], mdia[1], b"mdhd")
            hdlr = _child(moov, mdia[0], mdia[1], b"hdlr")
            if not mdhd or not hdlr:
                continue
            mversion = moov[mdhd[0]]
            timescale = struct.unpack_from(">I", moov, mdhd[0] + (20 if mversion == 1 else 12))[0]
            handler = bytes(moov[hdlr[0] + 8:hdlr[0] + 12])
            tracks[track_id] = (timescale, handler)
        elif kind == b"mvex":
            fragmented = True
            for sub, sbody, _ in _children(moov, body, stop):
                if sub == b"trex":
                    track_id, _index, duration = struct.unpack_from(">III", moov, sbody + 4)
                    defaults[track_id] = duration
    return tracks, defaults, fragmented


def _fragment_ticks(moof: bytes, track_id: int, default_duration: int) -> tuple[int | None, int]:
    """Return (base decode time, summed sample duration) of one track in a moof."""
    for kind, body, stop in _children(moof, 8, len(moof)):
        if kind != b"traf":
            continue
        tfhd = _child(moof, body, stop, b"tfhd")
        if not tfhd:
            continue
        flags = int.from_bytes(moof[tfhd[0] + 1:tfhd[0] + 4], "big")
        if struct.unpack_from(">I", moof, tfhd[0] + 4)[0] != track_id:
            continue
        cursor = tfhd[0] + 8
        if flags & 0x1:
            cursor += 8
        if flags & 0x2:
            cursor += 4
        sample_default = default_duration
        if flags & 0x8:
            sample_default = struct.unpack_from(">I", moof, cursor)[0]
        base = None
        tfdt = _child(moof, body, stop, b"tfdt")
        if tfdt:
            base = (struct.unpack_from(">Q", moof, tfdt[0] + 4)[0] if moof[tfdt[0]] == 1
                    else struct.unpack_from(">I", moof, tfdt[0] + 4)[0])
        total = 0
        for sub, sbody, _ in _children(moof, body, stop):
            if sub != b"trun":
                continue
            tflags = int.from_bytes(moof[sbody + 1:sbody + 4], "big")
            count = struct.unpack_from(">I", moof, sbody + 4)[0]
            cursor = sbody + 8 + (4 if tflags & 0x1 else 0) + (4 if tflags & 0x4 else 0)
            stride = 4 * sum(1 for bit in (0x100, 0x200, 0x400, 0x800) if tflags & bit)
            if tflags & 0x100:
                for index in range(count):
                    total += struct.unpack_from(">I", moof, cursor + index * stride)[0]
            else:
                total += count * sample_default
        return base, total
    return None, 0


def build_index(path: Path, target_seconds: float = 6.0) -> FragmentIndex:
    size = os.path.getsize(path)
    tracks: dict[int, tuple[int, bytes]] = {}
    defaults: dict[int, int] = {}
    init_length = 0
    fragments: list[tuple[int, int, int | None, int]] = []
    pending: tuple[int, int | None, int] | None = None
    complete = True
    with open(path, "rb") as handle:
        position = 0
        while position + 8 <= size:
            handle.seek(position)
            header = handle.read(16)
            box_size, kind = struct.unpack_from(">I4s", header)
            if box_size == 1 and len(header) >= 16:
                box_size = struct.unpack_from(">Q", header, 8)[0]
            elif box_size == 0:
                box_size = size - position
            if box_size < 8 or position + box_size > size:
                complete = False  # growing capture or truncated tail
                break
            if kind == b"moov":
                handle.seek(position)
                tracks, defaults, fragmented = _parse_moov(handle.read(box_size))
                if not fragmented:
                    raise NotFragmented("MP4 already has a complete index")
                init_length = position + box_size
            elif kind == b"moof" and init_length:
                if box_size > 4 * 1024 * 1024:
                    raise NotFragmented("Unexpectedly large moof")
                video = next((tid for tid, (_, handler) in tracks.items() if handler == b"vide"), None)
                track = video if video is not None else next(iter(tracks), None)
                if track is None:
                    raise NotFragmented("No track in moov")
                handle.seek(position)
                base, ticks = _fragment_ticks(handle.read(box_size), track, defaults.get(track, 0))
                pending = (position, base, ticks)
            elif kind == b"mdat" and pending:
                start, base, ticks = pending
                fragments.append((start, position + box_size - start, base, ticks))
                pending = None
            position += box_size
    if not init_length:
        raise NotFragmented("Not a fragmented MP4")
    video = next((tid for tid, (_, handler) in tracks.items() if handler == b"vide"), None)
    timescale = tracks[video if video is not None else next(iter(tracks))][0] or 1

    durations: list[float] = []
    for index, (_, _, base, ticks) in enumerate(fragments):
        following = fragments[index + 1][2] if index + 1 < len(fragments) else None
        if base is not None and following is not None and following > base:
            durations.append((following - base) / timescale)
        else:
            durations.append(ticks / timescale)

    segments: list[Segment] = []
    start = length = 0
    elapsed = 0.0
    for (offset, size_bytes, _, _), seconds in zip(fragments, durations):
        if length and (offset != start + length or elapsed >= target_seconds):
            segments.append(Segment(start, length, elapsed))
            length = 0
            elapsed = 0.0
        if not length:
            start = offset
        length += size_bytes
        elapsed += seconds
    if length:
        segments.append(Segment(start, length, elapsed))
    return FragmentIndex(init_length=init_length, segments=tuple(segments), complete=complete)


_cache: dict[str, tuple[tuple[int, int], FragmentIndex]] = {}
_cache_lock = threading.Lock()


def cached_index(path: Path) -> FragmentIndex:
    stat = os.stat(path)
    key = str(path)
    signature = (stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == signature:
            return hit[1]
    index = build_index(path)
    with _cache_lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[key] = (signature, index)
    return index


def hls_playlist(index: FragmentIndex, media_uri: str, live: bool = False) -> str:
    target = max(1, math.ceil(max((segment.duration for segment in index.segments), default=1)))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        f"#EXT-X-PLAYLIST-TYPE:{'EVENT' if live else 'VOD'}",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        f'#EXT-X-MAP:URI="{media_uri}",BYTERANGE="{index.init_length}@0"',
    ]
    for segment in index.segments:
        lines.append(f"#EXTINF:{segment.duration:.3f},")
        lines.append(f"#EXT-X-BYTERANGE:{segment.length}@{segment.offset}")
        lines.append(media_uri)
    if not live:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"
