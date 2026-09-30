"""Stable byte URLs and HLS sequences while capture parts rotate or are removed.

No media is retained here. A token pins the resolved file advertised by a playlist;
if remux/stitch/upload removes it, that token returns unavailable instead of reading
the next active file. Playlist state is bounded and entirely disposable on deploy.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from struct import error as struct_error

from .mp4_index import (
    FragmentIndex, NotFragmented, cached_index, hls_session_playlist, live_playlist_index,
)

MAX_CAPTURE_PART_TOKENS = 2048
MAX_CAPTURE_PLAYLISTS = 16
MAX_CAPTURE_PLAYLIST_PARTS = 256
CAPTURE_TARGET_SECONDS = 2.0
_part_tokens: OrderedDict[str, tuple[int, Path, Path, tuple[int, int]]] = OrderedDict()
_playlists: OrderedDict[tuple[int, str], CapturePlaylist] = OrderedDict()
_cache_lock = threading.Lock()


def capture_part_uri(source_id: int, path: Path, *, expected_identity: tuple[int, int] | None = None) -> str:
    """Register a previously validated, resolved path under a source/session token."""
    stat = path.stat()
    identity = (stat.st_dev, stat.st_ino)
    if expected_identity is not None and identity != expected_identity:
        raise NotFragmented("File della capture sostituito prima della pubblicazione")
    identity = expected_identity if expected_identity is not None else identity
    token = hashlib.sha256(f"{source_id}\0{path}\0{identity}".encode()).hexdigest()[:32]
    with _cache_lock:
        _part_tokens[token] = (int(source_id), path.parent, path, identity)
        _part_tokens.move_to_end(token)
        while len(_part_tokens) > MAX_CAPTURE_PART_TOKENS:
            _part_tokens.popitem(last=False)
    return f"/api/sources/{source_id}/capture?part={token}"


def capture_part_path(source_id: int, token: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    with _cache_lock:
        entry = _part_tokens.get(token)
    if entry is None or entry[0] != int(source_id) or entry[2].parent != entry[1]:
        return None
    try:
        stat = entry[2].stat()
    except OSError:
        return None
    if (stat.st_dev, stat.st_ino) != entry[3]:
        return None
    return entry[2]


def capture_part_identity(source_id: int, token: str) -> tuple[int, int] | None:
    with _cache_lock:
        entry = _part_tokens.get(token)
    return entry[3] if entry is not None and entry[0] == int(source_id) else None


@dataclass
class CapturePart:
    path: Path
    index: FragmentIndex
    identity: tuple[int, int]


class CapturePlaylist:
    def __init__(self) -> None:
        self.parts: list[CapturePart] = []
        self.media_sequence = 0
        self.discontinuity_sequence = 0
        self.target_duration: int | None = None
        self.epoch_key = uuid.uuid4().hex[:12]
        self.epoch = 0
        self.lock = threading.Lock()

    def update(self, source_id: int, active: Path, closed: list[Path]) -> tuple[str, str]:
        with self.lock:
            known = {part.path for part in self.parts}
            missing: set[Path] = set()
            for part in self.parts:
                try:
                    stat = part.path.stat()
                    if (stat.st_dev, stat.st_ino) != part.identity:
                        raise NotFragmented("File della capture sostituito")
                    updated = (live_playlist_index(part.path, CAPTURE_TARGET_SECONDS) if part.path == active
                               else cached_index(part.path, CAPTURE_TARGET_SECONDS))
                    stat = part.path.stat()
                    if (stat.st_dev, stat.st_ino) != part.identity:
                        raise NotFragmented("File della capture sostituito durante l'indicizzazione")
                    prefix = updated.segments[:len(part.index.segments)]
                    if [(s.offset, s.length) for s in prefix] != [(s.offset, s.length) for s in part.index.segments]:
                        raise NotFragmented("Parte della capture cambiata")
                    # Keep the already advertised durations as well as byte ranges;
                    # the final index can measure a provider timestamp gap differently.
                    part.index = FragmentIndex(updated.init_length,
                                               part.index.segments + updated.segments[len(prefix):], updated.complete)
                except (NotFragmented, OSError, ValueError, struct_error):
                    missing.add(part.path)

            # Existing parts retain their sequence positions. Newly observed parts
            # follow the last known file; a late DB index of an earlier file must
            # never insert segments in front of a timeline already advertised.
            candidates = list(dict.fromkeys([*closed, active]))
            last_known = max((i for i, path in enumerate(candidates) if path in known), default=-1)
            for path in candidates[last_known + 1:]:
                if path in known:
                    continue
                try:
                    stat = path.stat()
                    identity = (stat.st_dev, stat.st_ino)
                    index = (live_playlist_index(path, CAPTURE_TARGET_SECONDS) if path == active
                             else cached_index(path, CAPTURE_TARGET_SECONDS))
                    stat = path.stat()
                    if (stat.st_dev, stat.st_ino) != identity:
                        raise NotFragmented("File della capture sostituito durante l'indicizzazione")
                except (NotFragmented, OSError, ValueError, struct_error):
                    continue
                if index.segments:
                    self.parts.append(CapturePart(path, index, identity))

            # A removed prefix advances both sequence counters. If removal leaves
            # a hole, begin after it so every advertised segment remains playable.
            last_missing = max((i for i, part in enumerate(self.parts) if part.path in missing), default=-1)
            last_missing = max(last_missing, len(self.parts) - MAX_CAPTURE_PLAYLIST_PARTS - 1)
            if last_missing >= 0:
                removed = self.parts[:last_missing + 1]
                self.media_sequence += sum(len(part.index.segments) for part in removed)
                self.discontinuity_sequence += len(removed)
                del self.parts[:last_missing + 1]
            if not self.parts:
                raise NotFragmented("Nessun frammento completo")

            required_target = max(3, math.ceil(max(
                segment.duration for part in self.parts for segment in part.index.segments
            )))
            if self.target_duration is None:
                self.target_duration = required_target
            elif required_target > self.target_duration:
                # TARGETDURATION is immutable for a playlist URI. A new epoch
                # gives the larger GOP its own URI without discarding history.
                self.target_duration = required_target
                self.epoch += 1
            body = hls_session_playlist(
                [(part.index, capture_part_uri(source_id, part.path, expected_identity=part.identity)) for part in self.parts],
                live=True, sliding=True, media_sequence=self.media_sequence,
                discontinuity_sequence=self.discontinuity_sequence, target_duration=self.target_duration,
            )
            return body, f"{self.epoch_key}-{self.epoch}"


def capture_playlist_snapshot(source_id: int, active: Path, closed: list[Path]) -> tuple[str, str]:
    key = (int(source_id), str(active.parent))
    with _cache_lock:
        playlist = _playlists.get(key)
        if playlist is None:
            playlist = _playlists[key] = CapturePlaylist()
        _playlists.move_to_end(key)
        while len(_playlists) > MAX_CAPTURE_PLAYLISTS:
            _playlists.popitem(last=False)
    return playlist.update(source_id, active, closed)


def capture_playlist(source_id: int, active: Path, closed: list[Path]) -> str:
    return capture_playlist_snapshot(source_id, active, closed)[0]
