from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)


# --- DB: keep temporary capture parts out of the user-visible Recording table. ---
db = read("app/db.py")
anchor = "\n\nclass LiveSession(Base):\n"
fragment_model = r'''

class RecordingFragment(Base):
    """Validated local capture part waiting for logical-session stitching."""
    __tablename__ = "recording_fragments"
    __table_args__ = (
        Index("ix_recording_fragments_source_session", "source_id", "session_id"),
        Index("ix_recording_fragments_finalized", "finalized_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    source_name: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    local_path: Mapped[str] = mapped_column(Text, unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    container_format: Mapped[str] = mapped_column(String(16), default="")
    has_video: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_audio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    video_codec: Mapped[str] = mapped_column(String(40), default="")
    audio_codec: Mapped[str] = mapped_column(String(40), default="")
    integrity_status: Mapped[str] = mapped_column(String(30), default="passed", index=True)
    integrity_error: Mapped[str] = mapped_column(Text, default="")
'''
db = replace_once(db, anchor, fragment_model + anchor, "RecordingFragment model")
write("app/db.py", db)


# --- Recorder: reusable logical session id, unique reconnect capture names, concat helper. ---
rec = read("app/recorder.py")
rec = replace_once(rec, "import asyncio\nimport os\n", "import asyncio\nimport json\nimport os\n", "recorder json import")
rec = replace_once(
    rec,
    "LIVE_PREVIEW_MAX_AGE_SECONDS = 90\n",
    "LIVE_PREVIEW_MAX_AGE_SECONDS = 90\nSTITCH_MARKER_NAME = \".livevault-stitch-session.json\"\n",
    "stitch marker constant",
)
old_start = r'''async def start_recorder(source: Source) -> RecorderSession:
    cfg = runtime()
    inputs = await resolve_inputs(source.platform, source.slug, source.quality)
    split_llhls = is_chaturbate_split_llhls(source.platform, inputs)
    if not split_llhls:
        audit = await audit_inputs(inputs)
        if not audit.has_video or not audit.has_audio:
            raise RuntimeError(f"Audio Guard ha bloccato l'avvio: {audit.error}")
    inputs = [item for item in inputs if _llhls_role(item) in {"media", "video", "audio"}]
    local_now = datetime.now(ZoneInfo(settings.timezone))
    source_name = safe_name(source.name)
    session_id = f"{source_name}_{local_now:%Y-%m-%d_%H-%M-%S}"
    directory = settings.recordings_dir / source_name / session_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path: Path | None = None
    if split_llhls:
        inputs, manifest_path = build_chaturbate_synced_master(
            inputs, directory / ".livevault-synced-master.m3u8"
        )
    extension = ".mp4" if cfg.container_format == "mp4" else ".mkv"
    output_pattern = directory / f"{session_id}_part%03d{extension}"
    preview_path = live_preview_path(source.id)
'''
new_start = r'''async def start_recorder(source: Source, *, session_id: str | None = None) -> RecorderSession:
    cfg = runtime()
    inputs = await resolve_inputs(source.platform, source.slug, source.quality)
    split_llhls = is_chaturbate_split_llhls(source.platform, inputs)
    if not split_llhls:
        audit = await audit_inputs(inputs)
        if not audit.has_video or not audit.has_audio:
            raise RuntimeError(f"Audio Guard ha bloccato l'avvio: {audit.error}")
    inputs = [item for item in inputs if _llhls_role(item) in {"media", "video", "audio"}]
    local_now = datetime.now(ZoneInfo(settings.timezone))
    source_name = safe_name(source.name)
    session_id = session_id or f"{source_name}_{local_now:%Y-%m-%d_%H-%M-%S}"
    directory = settings.recordings_dir / source_name / session_id
    directory.mkdir(parents=True, exist_ok=True)
    # Persist enough state for crash recovery before FFmpeg writes the first part.
    (directory / STITCH_MARKER_NAME).write_text(json.dumps({
        "source_id": int(source.id),
        "source_name": source.name,
        "session_id": session_id,
        "started_at": utcnow().isoformat(),
    }, ensure_ascii=False), encoding="utf-8")
    capture_id = local_now.strftime("%Y%m%d_%H%M%S_%f")
    manifest_path: Path | None = None
    if split_llhls:
        inputs, manifest_path = build_chaturbate_synced_master(
            inputs, directory / f".livevault-synced-master-{capture_id}.m3u8"
        )
    extension = ".mp4" if cfg.container_format == "mp4" else ".mkv"
    # A reconnect within the logical 20-minute session must never overwrite part000.
    output_pattern = directory / f"{session_id}_{capture_id}_part%03d{extension}"
    preview_path = live_preview_path(source.id)
'''
rec = replace_once(rec, old_start, new_start, "start_recorder logical session")

stitch_helper = r'''

async def stitch_recording_parts(parts: list[Path], output: Path) -> None:
    """Join public-live capture parts back-to-back, deliberately removing offline gaps.

    Stream-copy is attempted first so normal sessions keep original video quality.  A
    full A/V transcode is only the compatibility fallback when the upstream changed
    codec parameters between reconnects.
    """
    parts = [Path(path) for path in parts if Path(path).is_file()]
    if not parts:
        raise RuntimeError("Nessun frammento valido da unire")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    concat_file = output.with_name(f".{output.stem}.concat.txt")

    def quote(path: Path) -> str:
        value = str(path.resolve()).replace("'", "'\\''")
        return f"file '{value}'"

    concat_file.write_text("\n".join(quote(path) for path in parts) + "\n", encoding="utf-8")

    async def run(args: list[str], timeout: int) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError("Stitching sessione scaduto") from exc
        return proc.returncode, (stderr or b"").decode(errors="replace")[-1800:]

    total_bytes = sum(path.stat().st_size for path in parts)
    timeout = max(180, min(3600, int(total_bytes / (4 * 1024**2))))
    base = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+genpts+discardcorrupt",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-map", "0:v:0", "-map", "0:a:0", "-dn", "-ignore_unknown",
    ]
    trailer = ["-movflags", "+faststart"] if output.suffix.lower() == ".mp4" else []
    try:
        code, detail = await run(base + ["-c", "copy", *trailer, str(output)], timeout)
        if code == 0 and output.is_file() and output.stat().st_size > 0:
            return
        output.unlink(missing_ok=True)
        code, fallback_detail = await run(base + [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-fps_mode", "vfr",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-af", "aresample=async=1",
            *trailer, str(output),
        ], max(timeout, 600))
        if code != 0 or not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError(fallback_detail or detail or "Stitching FFmpeg fallito")
    finally:
        concat_file.unlink(missing_ok=True)
'''
rec = replace_once(rec, "\n\ndef mp4_is_streaming_ready(path: Path) -> bool:\n", stitch_helper + "\n\ndef mp4_is_streaming_ready(path: Path) -> bool:\n", "stitch helper")
write("app/recorder.py", rec)


# --- Worker: 20-minute logical session, hidden fragments, delayed consolidation. ---
wrk = read("app/workers.py")
wrk = replace_once(wrk, "import asyncio\nimport contextlib\n", "import asyncio\nimport contextlib\nimport json\nimport shutil\n", "worker imports")
wrk = replace_once(
    wrk,
    "from .db import CloudDay, LiveSession, Profile, Recording, Source, db_session\n",
    "from .db import CloudDay, LiveSession, Profile, Recording, RecordingFragment, Source, db_session\n",
    "worker fragment import",
)
wrk = replace_once(
    wrk,
    "    RecorderSession,\n",
    "    RecorderSession,\n    STITCH_MARKER_NAME,\n",
    "worker marker import",
)
wrk = replace_once(
    wrk,
    "    start_recorder,\n    stop_recorder,\n    stream_transport_fault,\n",
    "    start_recorder,\n    stitch_recording_parts,\n    stop_recorder,\n    stream_transport_fault,\n",
    "worker stitch import",
)
wrk = replace_once(
    wrk,
    "RETRYABLE_MEDIA_ERRORS = (\"scadut\", \"timeout\", \"timed out\", \"tempor\")\nCLOUD_TIME_ZONE = ZoneInfo(\"Europe/Berlin\")\n",
    "RETRYABLE_MEDIA_ERRORS = (\"scadut\", \"timeout\", \"timed out\", \"tempor\")\nCLOUD_TIME_ZONE = ZoneInfo(\"Europe/Berlin\")\nSESSION_STITCH_GAP_SECONDS = 20 * 60\n",
    "20 minute gap constant",
)
helper = r'''


def stitch_gap_open(last_at: datetime, now: datetime, gap_seconds: int = SESSION_STITCH_GAP_SECONDS) -> bool:
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (now.astimezone(timezone.utc) - last_at.astimezone(timezone.utc)).total_seconds()
    return 0 <= delta <= max(0, int(gap_seconds))
'''
wrk = replace_once(wrk, "\n\nclass WorkerManager:\n", helper + "\n\nclass WorkerManager:\n", "gap helper")

old_buffer = r'''    def local_buffer_bytes(self) -> int:
        total = 0
        with db_session() as db:
            total = int(db.scalar(
                select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.local_deleted.is_(False))
            ) or 0)
        known_paths: set[str] = set()
        with db_session() as db:
            known_paths = {str(x) for x in db.scalars(select(Recording.local_path).where(Recording.local_deleted.is_(False))).all()}
        for session in self.active.values():
            with contextlib.suppress(Exception):
                for path in session.directory.glob(f"*{session.extension}"):
                    if str(path) not in known_paths and path.is_file():
                        total += path.stat().st_size
        return total
'''
new_buffer = r'''    def local_buffer_bytes(self) -> int:
        total = 0
        known_paths: set[str] = set()
        with db_session() as db:
            total += int(db.scalar(
                select(func.coalesce(func.sum(Recording.size_bytes), 0)).where(Recording.local_deleted.is_(False))
            ) or 0)
            total += int(db.scalar(select(func.coalesce(func.sum(RecordingFragment.size_bytes), 0))) or 0)
            known_paths.update(str(x) for x in db.scalars(
                select(Recording.local_path).where(Recording.local_deleted.is_(False))
            ).all())
            known_paths.update(str(x) for x in db.scalars(select(RecordingFragment.local_path)).all())
        for session in self.active.values():
            with contextlib.suppress(Exception):
                for path in session.directory.glob(f"*{session.extension}"):
                    if str(path) not in known_paths and path.is_file():
                        total += path.stat().st_size
        return total
'''
wrk = replace_once(wrk, old_buffer, new_buffer, "buffer counts fragments")

wrk = replace_once(
    wrk,
    "        while not self._stopping:\n            await self._repair_local_mp4s()\n            await self._backfill_thumbnails()\n",
    "        while not self._stopping:\n            await self._finalize_closed_stitch_sessions()\n            await self._repair_local_mp4s()\n            await self._backfill_thumbnails()\n",
    "maintenance stitching",
)

old_orphan = r'''    async def _recover_orphans(self) -> None:
        candidates = sorted(settings.recordings_dir.rglob("*.mkv")) + sorted(settings.recordings_dir.rglob("*.mp4"))
        for path in candidates:
            if path.name.startswith(".") or path.name.endswith(".tmp.mp4"):
                continue
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            with db_session() as db:
                if db.scalar(select(Recording).where(Recording.local_path == str(path))):
                    continue
                sources = list(db.scalars(select(Source)).all())
            source_folder = path.parent.parent.name if path.parent.parent else "recovered"
            source = next((s for s in sources if safe_name(s.name) == source_folder), None)
            source_id = source.id if source else 0
            source_name = source.name if source else source_folder
            await self._index_file(
                source_id=source_id,
                source_name=source_name,
                session_id=path.parent.name,
                path=path,
                started_at=None,
            )
'''
new_orphan = r'''    async def _recover_orphans(self) -> None:
        candidates = sorted(settings.recordings_dir.rglob("*.mkv")) + sorted(settings.recordings_dir.rglob("*.mp4"))
        for path in candidates:
            if path.name.startswith(".") or path.name.endswith(".tmp.mp4") or "_complete" in path.stem:
                continue
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            with db_session() as db:
                if db.scalar(select(Recording).where(Recording.local_path == str(path))):
                    continue
                if db.scalar(select(RecordingFragment).where(RecordingFragment.local_path == str(path))):
                    continue
                sources = list(db.scalars(select(Source)).all())
            marker = path.parent / STITCH_MARKER_NAME
            marker_data = {}
            if marker.is_file():
                with contextlib.suppress(Exception):
                    marker_data = json.loads(marker.read_text(encoding="utf-8"))
            source_folder = path.parent.parent.name if path.parent.parent else "recovered"
            source = next((s for s in sources if int(marker_data.get("source_id") or 0) == s.id), None)
            if source is None:
                source = next((s for s in sources if safe_name(s.name) == source_folder), None)
            source_id = source.id if source else int(marker_data.get("source_id") or 0)
            source_name = source.name if source else str(marker_data.get("source_name") or source_folder)
            session_id = str(marker_data.get("session_id") or path.parent.name)
            if marker.is_file():
                await self._index_fragment(
                    source_id=source_id,
                    source_name=source_name,
                    session_id=session_id,
                    path=path,
                    started_at=None,
                )
            else:
                await self._index_file(
                    source_id=source_id,
                    source_name=source_name,
                    session_id=session_id,
                    path=path,
                    started_at=None,
                )
'''
wrk = replace_once(wrk, old_orphan, new_orphan, "orphan fragment recovery")

# Add fragment indexing + logical-session selection + consolidation before _index_file.
anchor_index = "    async def _index_file(self, *, source_id: int, source_name: str, session_id: str, path: Path, started_at: datetime | None) -> bool:\n"
fragment_methods = r'''    async def _index_fragment(self, *, source_id: int, source_name: str, session_id: str, path: Path, started_at: datetime | None) -> bool:
        """Validate a capture part but keep it out of Archive/upload until the 20-minute session closes."""
        cfg = runtime()
        if not await self._wait_until_stable(path):
            self.last_errors[f"fragment:{source_id}"] = f"Frammento ancora in scrittura: {path.name}"
            return False
        normalization_error = ""
        try:
            await self._prepare_mp4(path)
        except Exception as exc:
            normalization_error = f"Finalizzazione frammento fallita: {exc}"[-1500:]
        integrity = None if normalization_error else await asyncio.to_thread(verify_media, path, cfg.integrity_mode)
        finalized = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if integrity and integrity.duration:
            start = finalized - timedelta(seconds=integrity.duration)
        elif started_at:
            start = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
            start = start.astimezone(timezone.utc)
        else:
            start = finalized
        with db_session() as db:
            if db.scalar(select(RecordingFragment).where(RecordingFragment.local_path == str(path))):
                return True
            db.add(RecordingFragment(
                source_id=source_id,
                source_name=source_name,
                session_id=session_id,
                local_path=str(path),
                filename=path.name,
                started_at=start,
                finalized_at=finalized,
                duration_seconds=integrity.duration if integrity else None,
                size_bytes=path.stat().st_size,
                container_format=path.suffix.lower().lstrip("."),
                has_video=integrity.has_video if integrity else None,
                has_audio=integrity.has_audio if integrity else None,
                video_codec=integrity.codec("video") if integrity else "",
                audio_codec=integrity.codec("audio") if integrity else "",
                integrity_status="passed" if integrity and integrity.ok else "failed",
                integrity_error=normalization_error or (integrity.error if integrity else "Frammento non valido"),
            ))
        if integrity and integrity.ok:
            self.last_errors.pop(f"fragment:{source_id}", None)
        else:
            self.last_errors[f"fragment:{source_id}"] = normalization_error or (integrity.error if integrity else "Frammento non valido")
        return True

    def _logical_session_id_for(self, source: Source, now: datetime) -> str:
        """Reuse the latest logical session only inside the same Frankfurt day and 20-minute gap."""
        with db_session() as db:
            latest = db.scalar(
                select(RecordingFragment)
                .where(RecordingFragment.source_id == source.id)
                .order_by(RecordingFragment.finalized_at.desc(), RecordingFragment.id.desc())
                .limit(1)
            )
            if not latest:
                return ""
            if cloud_day_key(latest.started_at) != cloud_day_key(now):
                return ""
            return latest.session_id if stitch_gap_open(latest.finalized_at, now) else ""

    async def _finalize_closed_stitch_sessions(self) -> None:
        """Collapse all public-live parts into one user-visible recording after 20 minutes of silence."""
        now = utcnow()
        with db_session() as db:
            rows = list(db.scalars(
                select(RecordingFragment).order_by(
                    RecordingFragment.source_id,
                    RecordingFragment.session_id,
                    RecordingFragment.started_at,
                    RecordingFragment.id,
                )
            ).all())
            for row in rows:
                db.expunge(row)
        groups: dict[tuple[int, str], list[RecordingFragment]] = {}
        for row in rows:
            groups.setdefault((int(row.source_id), row.session_id), []).append(row)
        for (source_id, session_id), items in groups.items():
            if self._stopping or source_id in self.active:
                continue
            latest = max(item.finalized_at for item in items)
            if stitch_gap_open(latest, now):
                continue
            lock = self._source_check_locks.setdefault(source_id, asyncio.Lock())
            async with lock:
                if source_id in self.active:
                    continue
                with db_session() as db:
                    current = list(db.scalars(
                        select(RecordingFragment)
                        .where(
                            RecordingFragment.source_id == source_id,
                            RecordingFragment.session_id == session_id,
                        )
                        .order_by(RecordingFragment.started_at, RecordingFragment.id)
                    ).all())
                    for row in current:
                        db.expunge(row)
                if not current:
                    continue
                latest = max(item.finalized_at for item in current)
                if stitch_gap_open(latest, utcnow()):
                    continue
                try:
                    await self._stitch_fragment_group(current)
                    self.last_errors.pop(f"stitch:{source_id}:{session_id}", None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_errors[f"stitch:{source_id}:{session_id}"] = str(exc)[-1400:]

    async def _stitch_fragment_group(self, fragments: list[RecordingFragment]) -> None:
        fragments = sorted(fragments, key=lambda item: (item.started_at, item.id))
        good = [item for item in fragments if item.integrity_status == "passed" and Path(item.local_path).is_file()]
        if not good:
            raise RuntimeError("Nessun frammento integro nella sessione")
        first = good[0]
        paths = [Path(item.local_path) for item in good]
        total_bytes = sum(path.stat().st_size for path in paths)
        free = shutil.disk_usage(paths[0].parent).free
        if free < total_bytes + 256 * 1024 * 1024:
            raise RuntimeError("Spazio insufficiente per consolidare la sessione")
        suffixes = {path.suffix.lower() for path in paths}
        suffix = next(iter(suffixes)) if len(suffixes) == 1 else ".mp4"
        output = paths[0].parent / f"{first.session_id}_complete{suffix}"
        output.unlink(missing_ok=True)
        await stitch_recording_parts(paths, output)
        if output.suffix.lower() == ".mp4":
            await self._prepare_mp4(output)
        integrity = await asyncio.to_thread(verify_media, output, runtime().integrity_mode)
        if not integrity.ok:
            output.unlink(missing_ok=True)
            raise RuntimeError(f"Sessione consolidata non valida: {integrity.error}")
        digest = await asyncio.to_thread(sha256_file, output)
        thumb_path = ""
        if runtime().generate_thumbnails:
            candidate = settings.data_dir / "thumbnails" / f"{digest[:24]}-sheet-v2.jpg"
            if await asyncio.to_thread(generate_thumbnail, output, candidate, integrity.duration):
                thumb_path = str(candidate)
        started = min(item.started_at for item in good)
        finalized = max(item.finalized_at for item in good)
        with db_session() as db:
            existing = db.scalar(select(Recording).where(
                Recording.source_id == first.source_id,
                Recording.session_id == first.session_id,
            ))
            if existing is None:
                db.add(Recording(
                    source_id=first.source_id,
                    source_name=first.source_name,
                    session_id=first.session_id,
                    local_path=str(output),
                    filename=output.name,
                    started_at=started,
                    finalized_at=finalized,
                    duration_seconds=integrity.duration,
                    size_bytes=output.stat().st_size,
                    sha256=digest,
                    upload_status="pending",
                    thumbnail_path=thumb_path,
                    integrity_status="passed",
                    integrity_error="",
                    integrity_checked_at=utcnow(),
                    container_format=output.suffix.lower().lstrip("."),
                    has_video=integrity.has_video,
                    has_audio=integrity.has_audio,
                    video_codec=integrity.codec("video"),
                    audio_codec=integrity.codec("audio"),
                ))
            for fragment in db.scalars(select(RecordingFragment).where(
                RecordingFragment.source_id == first.source_id,
                RecordingFragment.session_id == first.session_id,
            )).all():
                db.delete(fragment)
        for path in paths:
            if path != output:
                path.unlink(missing_ok=True)
        for fragment in fragments:
            path = Path(fragment.local_path)
            if path not in paths:
                path.unlink(missing_ok=True)
        (output.parent / STITCH_MARKER_NAME).unlink(missing_ok=True)
        for manifest in output.parent.glob(".livevault-synced-master-*.m3u8"):
            manifest.unlink(missing_ok=True)
        self.wake()

'''
wrk = replace_once(wrk, anchor_index, fragment_methods + anchor_index, "fragment lifecycle methods")

# New reconnects use the same logical session id when eligible.
wrk = replace_once(
    wrk,
    "                session = await start_recorder(current)\n",
    "                logical_session_id = self._logical_session_id_for(current, checked_at)\n                session = await start_recorder(current, session_id=logical_session_id or None)\n",
    "reconnect logical session",
)

# Force a clean daily boundary in Frankfurt while preserving the logical session gap inside a day.
wrk = replace_once(
    wrk,
    "                if session.restart_requested:\n                    await stop_recorder(session)\n                    continue\n                files = sorted(session.directory.glob(f\"*{session.extension}\"), key=lambda p: p.stat().st_mtime)\n",
    "                if session.restart_requested:\n                    await stop_recorder(session)\n                    continue\n                if cloud_day_key(session.started_at) != cloud_day_key(utcnow()):\n                    session.rollover_requested = True\n                    await stop_recorder(session)\n                    continue\n                files = sorted(session.directory.glob(f\"*{session.extension}\"), key=lambda p: p.stat().st_mtime)\n",
    "Frankfurt midnight rollover",
)

# Capture parts are now hidden fragments, not immediately uploadable recordings.
old_finalize = r'''        indexed = await self._index_file(
            source_id=session.source_id,
            source_name=session.source_name,
            session_id=session.session_id,
            path=path,
            started_at=started,
        )
'''
new_finalize = r'''        indexed = await self._index_fragment(
            source_id=session.source_id,
            source_name=session.source_name,
            session_id=session.session_id,
            path=path,
            started_at=started,
        )
'''
wrk = replace_once(wrk, old_finalize, new_finalize, "finalize as fragment")
write("app/workers.py", wrk)


# --- Release bump. ---
write("VERSION", "2.8.8\n")
main = read("app/main.py")
main = main.replace('VERSION = "2.8.7"', 'VERSION = "2.8.8"')
write("app/main.py", main)
sw = read("app/static/sw.js").replace("livevault-shell-v2.8.7", "livevault-shell-v2.8.8")
write("app/static/sw.js", sw)
for path in ("README.md", "START_HERE.md"):
    text = read(path).replace("v2.8.7", "v2.8.8", 1)
    write(path, text)
changelog = read("CHANGELOG.md")
entry = """# Changelog\n\n## 2.8.8 — Session stitching\n\n- Una registrazione logica resta aperta per 20 minuti dopo una temporanea uscita dalla live pubblica.\n- I reconnect entro 20 minuti vengono uniti in un solo video, senza riempire i gap privati/offline.\n- I frammenti intermedi restano locali e invisibili ad Archivio/upload finché la sessione non viene consolidata.\n- Stream-copy per lo stitching quando possibile; transcode A/V solo come fallback di compatibilità.\n- Rollover automatico a mezzanotte Europe/Berlin per mantenere la separazione cloud giornaliera.\n- Recovery dopo riavvio tramite marker persistente della sessione logica.\n\n"""
if changelog.startswith("# Changelog\n"):
    changelog = entry + changelog[len("# Changelog\n\n"):]
else:
    changelog = entry + changelog
write("CHANGELOG.md", changelog)

# Update only assertions that explicitly track the current runtime/cache version.
for test_path in (ROOT / "tests").glob("test_*.py"):
    text = test_path.read_text(encoding="utf-8")
    if 'VERSION = "2.8.7"' in text or 'livevault-shell-v2.8.7' in text or '.strip() == "2.8.7"' in text:
        text = text.replace('VERSION = "2.8.7"', 'VERSION = "2.8.8"')
        text = text.replace('livevault-shell-v2.8.7', 'livevault-shell-v2.8.8')
        text = text.replace('.strip() == "2.8.7"', '.strip() == "2.8.8"')
        test_path.write_text(text, encoding="utf-8")

# New regression tests, including an actual FFmpeg concat with an artificial wall-clock gap.
test = r'''from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.recorder import STITCH_MARKER_NAME, stitch_recording_parts
from app.workers import SESSION_STITCH_GAP_SECONDS, stitch_gap_open
from app.utils import probe_media


def test_session_gap_is_exactly_twenty_minutes():
    now = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
    assert SESSION_STITCH_GAP_SECONDS == 20 * 60
    assert stitch_gap_open(now - timedelta(minutes=19, seconds=59), now)
    assert stitch_gap_open(now - timedelta(minutes=20), now)
    assert not stitch_gap_open(now - timedelta(minutes=20, seconds=1), now)


def test_stitch_marker_and_fragment_table_are_persistent():
    recorder = Path("app/recorder.py").read_text(encoding="utf-8")
    db = Path("app/db.py").read_text(encoding="utf-8")
    workers = Path("app/workers.py").read_text(encoding="utf-8")
    assert STITCH_MARKER_NAME == ".livevault-stitch-session.json"
    assert "class RecordingFragment" in db
    assert "await self._index_fragment(" in workers
    assert "await self._finalize_closed_stitch_sessions()" in workers
    assert "session_id=logical_session_id or None" in workers
    assert "cloud_day_key(session.started_at) != cloud_day_key(utcnow())" in workers
    assert "capture_id = local_now.strftime" in recorder


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg required")
def test_real_ffmpeg_stitch_removes_offline_gap(tmp_path):
    async def run():
        parts = []
        for index, frequency in enumerate((440, 660)):
            path = tmp_path / f"part{index}.mp4"
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=1.2",
                "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=1.2",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(path),
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            parts.append(path)
        # The second public interval could have happened 19 minutes later in wall-clock
        # time; stitching intentionally joins media end-to-start and does not encode that gap.
        output = tmp_path / "complete.mp4"
        await stitch_recording_parts(parts, output)
        media = probe_media(output, require_audio=True)
        assert media.ok, media.error
        assert media.duration is not None
        assert 2.0 <= media.duration <= 3.0

    asyncio.run(run())
'''
write("tests/test_v288_session_stitching.py", test)

print("v2.8.8 session stitching patch applied")
