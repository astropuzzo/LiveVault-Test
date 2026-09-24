"""Live NSFW analysis while recording, mixed into WorkerManager.

Sampler: round-robin over the active captures, at most ``nsfw_live_fps``
frames per second in total, one keyframe every ``nsfw_step_seconds`` of each
capture. It reads only the fragments appended since the last pass
(``GrowingIndex``) and hands one fragment at a time to a persistent
low-priority helper (``app.nsfw_live``). Suspects become ``NsfwMark`` rows.

Verifier: runs the large model on the saved suspect frames (internal drive
only), inheriting a confirmation for 60 s inside a long explicit stretch.

Storage handoff: sampling pauses while the NVMe is being attached/detached
(``quiesce``), continues on the internal buffer while the NVMe is away and on
the NVMe when it is back; verification never touches recordings, so it keeps
running in every mode.

At stitching time the marks of each capture part are moved onto the uploaded
file's timeline (``nsfw_attach_parts``); a recording the sampler covered well
needs no full scan afterwards.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from . import storage_handoff
from .db import NsfwCoverage, NsfwMark, Recording, db_session
from .mp4_index import GrowingIndex, LiveFragment, NotFragmented
from .nsfw_scan import Verdict, combine_classes, merge_moments, overall
from .nsfw_worker import file_signature, nsfw_dir
from .settings_store import runtime
from .utils import utcnow

LIVE_COVERAGE_OK = 0.85
INHERIT_SECONDS = 60
BACKLOG_JUMP_SECONDS = 60
PREVIEW_GAP_SECONDS = 30  # at most one kept preview per 30 s of a moment
ACTIVE_MARK_STATES = ("pending", "confirmed", "inherited", "review")
NSFW_LABEL = {"confirmed": "nsfw", "inherited": "nsfw", "review": "review", "pending": "review"}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def load_average() -> float:
    with contextlib.suppress(OSError, AttributeError):
        return os.getloadavg()[0]
    return 0.0


class HelperProcess:
    """One persistent ``app.nsfw_live`` child (models loaded once)."""

    def __init__(self, mode: str):
        self.mode = mode
        self.proc: asyncio.subprocess.Process | None = None
        self.signature: tuple | None = None
        self.lock = asyncio.Lock()
        self.counter = 0
        self.error = ""
        self.last_used = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def ensure(self) -> bool:
        cfg = runtime()
        signature = (cfg.nsfw_fast_model, cfg.nsfw_verify_model, int(cfg.nsfw_threads), cfg.nsfw_classes)
        if self.alive and signature == self.signature:
            return True
        await self.close()
        command = [sys.executable, "-m", "app.nsfw_live", "--mode", self.mode,
                   "--fast-model", cfg.nsfw_fast_model, "--verify-model", cfg.nsfw_verify_model,
                   "--threads", str(max(1, int(cfg.nsfw_threads))), "--classes", cfg.nsfw_classes]
        prefix = ["nice", "-n", "19"] if shutil.which("nice") else []
        if shutil.which("ionice"):
            prefix += ["ionice", "-c3"]
        self.proc = await asyncio.create_subprocess_exec(
            *prefix, *command, cwd=str(Path(__file__).resolve().parents[1]),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=90)  # type: ignore[union-attr]
            hello = json.loads(line or b"{}")
        except (asyncio.TimeoutError, ValueError):
            hello = {}
        if not hello.get("ready"):
            self.error = str(hello.get("fatal") or "Avvio analisi live non riuscito")
            await self.close()
            return False
        self.signature, self.error = signature, ""
        return True

    async def request(self, payload: dict, timeout: float) -> dict:
        async with self.lock:
            if not self.alive:
                raise RuntimeError("Processo di analisi non attivo")
            self.counter += 1
            self.last_used = time.monotonic()
            message = {**payload, "id": self.counter}
            self.proc.stdin.write((json.dumps(message) + "\n").encode())  # type: ignore[union-attr]
            try:
                await self.proc.stdin.drain()  # type: ignore[union-attr]
                while True:
                    line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=timeout)  # type: ignore[union-attr]
                    if not line:
                        raise RuntimeError("Processo di analisi terminato")
                    reply = json.loads(line)
                    if reply.get("id") == self.counter:
                        return reply
            except BaseException:
                # A stuck or dead helper is replaced on the next request.
                await self.close()
                raise

    async def close(self) -> None:
        proc, self.proc, self.signature = self.proc, None, None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=10)


@dataclass
class LiveTrack:
    source_id: int
    source_name: str
    session_id: str
    path: Path | None = None
    reader: GrowingIndex | None = None
    unsupported: str = ""
    available: list[LiveFragment] = field(default_factory=list)
    last_time: float = float("-inf")
    last_sample: float = 0.0
    samples: int = 0
    marks: int = 0
    errors: int = 0


def cluster_marks(marks: list, gap_seconds: float = 30.0, step: float = 5.0) -> list[dict]:
    """Group live marks (wall clock) into timeline moments for the Monitor."""
    moments: list[dict] = []
    for mark in sorted(marks, key=lambda m: _aware(m.wall_at)):
        label = NSFW_LABEL.get(mark.state)
        if not label:
            continue
        at = _aware(mark.wall_at)
        state = "pending" if mark.state == "pending" else label
        last = moments[-1] if moments else None
        if last and (at - last["_end"]).total_seconds() <= gap_seconds:
            last["_end"] = at + timedelta(seconds=step)
            last["count"] += 1
            last["cls"] = combine_classes(last["cls"], mark.verified_cls, mark.cls)
            rank = {"pending": 0, "review": 1, "nsfw": 2}
            if rank[state] > rank[last["label"]]:
                last["label"] = state
            if not last["image"] and mark.image:
                last["image"] = mark.image
            continue
        moments.append({
            "_start": at, "_end": at + timedelta(seconds=step), "label": state, "count": 1,
            "cls": combine_classes(mark.verified_cls, mark.cls), "image": mark.image or "",
            "recording_id": mark.recording_id, "file_time": mark.file_time, "mark_id": mark.id,
        })
    out = []
    for moment in moments:
        cls = moment["cls"]
        out.append({
            "started_at": moment["_start"].isoformat(), "ended_at": moment["_end"].isoformat(),
            "label": moment["label"], "class": cls, "count": moment["count"],
            "image_url": f"/api/nsfw/images/{moment['image']}" if moment["image"] else "",
            "recording_id": moment["recording_id"], "file_time": moment["file_time"], "mark_id": moment["mark_id"],
        })
    return out


class LiveNsfwMixin:
    nsfw_live_state: str = "disabled"
    nsfw_live_detail: str = ""

    def _nsfw_live_init(self) -> None:
        if not hasattr(self, "_nsfw_live_tracks"):
            self._nsfw_live_tracks: dict[int, LiveTrack] = {}
            self._nsfw_sampler = HelperProcess("sample")
            self._nsfw_verifier = HelperProcess("verify")
            self.nsfw_verify_state = "idle"

    # ---------- snapshot for the API ----------
    def nsfw_live_snapshot(self) -> dict:
        self._nsfw_live_init()
        with db_session() as db:
            queue = db.scalar(select(func.count()).select_from(NsfwMark).where(NsfwMark.state == "pending")) or 0
        now = time.monotonic()
        tracks = [{
            "source_id": t.source_id, "name": t.source_name, "samples": t.samples, "marks": t.marks,
            "position": None if t.last_time == float("-inf") else round(t.last_time, 1),
            "lag_seconds": round(max(0.0, (t.reader.latest_time if t.reader else 0) - max(t.last_time, 0)), 1) if t.reader else None,
            "seconds_since_sample": round(now - t.last_sample, 1) if t.last_sample else None,
            "unsupported": t.unsupported,
        } for t in self._nsfw_live_tracks.values()]
        return {"state": self.nsfw_live_state, "detail": self.nsfw_live_detail, "tracks": tracks,
                "verify_queue": int(queue), "verify_state": self.nsfw_verify_state,
                "load": round(load_average(), 2)}

    # ---------- sampler ----------
    def _nsfw_live_gate(self) -> str:
        cfg = runtime()
        if not (cfg.nsfw_enabled and cfg.nsfw_live_enabled):
            return "disabled"
        if storage_handoff.state()["mode"] == "quiesce":
            return "storage_switch"
        if not Path(cfg.nsfw_fast_model).is_file():
            self.nsfw_live_detail = f"Modello veloce mancante: {cfg.nsfw_fast_model}"
            return "models_missing"
        if not getattr(self, "active", None):
            return "idle"
        if load_average() > float(cfg.nsfw_live_max_load):
            return "busy"
        return ""

    def _nsfw_live_sync_tracks(self) -> None:
        active = dict(getattr(self, "active", {}) or {})
        for source_id in list(self._nsfw_live_tracks):
            if source_id not in active:
                self._nsfw_live_tracks.pop(source_id, None)
        for source_id, session in active.items():
            track = self._nsfw_live_tracks.get(source_id)
            if track is None or track.session_id != session.session_id:
                track = LiveTrack(int(source_id), session.source_name, session.session_id)
                self._nsfw_live_tracks[source_id] = track
            path = self.active_capture_path(source_id)
            if path is None or path == track.path:
                continue
            # Rollover or reconnect: the recorder moved to a new part.
            track.path, track.available, track.last_time, track.unsupported = path, [], float("-inf"), ""
            track.reader = GrowingIndex(path) if path.suffix.lower() == ".mp4" else None
            if track.reader is None:
                track.unsupported = f"Formato {path.suffix or '?'} non analizzabile dal vivo"

    def _nsfw_live_poll(self, track: LiveTrack) -> None:
        if track.reader is None:
            return
        try:
            track.available.extend(track.reader.poll())
        except NotFragmented as exc:
            track.reader, track.unsupported = None, f"File non frammentato: {exc}"
        except (OSError, ValueError) as exc:
            track.errors += 1
            track.reader = GrowingIndex(track.path) if track.path and track.path.exists() else None
            self.nsfw_live_detail = f"{track.source_name}: {exc}"[-300:]

    def _nsfw_live_pick(self) -> tuple[LiveTrack, LiveFragment] | None:
        step = max(1.0, float(runtime().nsfw_step_seconds))
        for track in sorted(self._nsfw_live_tracks.values(), key=lambda t: t.last_sample):
            self._nsfw_live_poll(track)
            track.available = [f for f in track.available if f.time > track.last_time]
            if not track.available or track.reader is None:
                continue
            due = next((f for f in track.available if f.time >= track.last_time + step), None)
            if due is None:
                continue
            if track.reader.latest_time - due.time > BACKLOG_JUMP_SECONDS:
                due = track.available[-1]  # fell behind: stay live, the gap is left to the full scan
            return track, due
        return None

    async def _nsfw_live_loop(self) -> None:
        self._nsfw_live_init()
        try:
            while not self._stopping:
                try:
                    gate = self._nsfw_live_gate()
                    self.nsfw_live_state = gate or "running"
                    if gate:
                        if gate in ("disabled", "models_missing"):
                            await self._nsfw_sampler.close()
                        if gate != "models_missing":
                            self.nsfw_live_detail = ""
                        if gate in ("disabled", "idle"):
                            self._nsfw_live_tracks.clear()
                        await asyncio.sleep(2.0)
                        continue
                    self._nsfw_live_sync_tracks()
                    job = self._nsfw_live_pick()
                    if job is None:
                        await asyncio.sleep(0.5)
                        continue
                    await self._nsfw_live_sample(*job)
                    await asyncio.sleep(max(0.25, 1.0 / max(0.05, float(runtime().nsfw_live_fps))))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_errors["nsfw-live"] = str(exc)[-800:]
                    await asyncio.sleep(5)
        finally:
            await self._nsfw_sampler.close()

    async def _nsfw_live_sample(self, track: LiveTrack, fragment: LiveFragment) -> None:
        if not await self._nsfw_sampler.ensure():
            self.nsfw_live_state, self.nsfw_live_detail = "error", self._nsfw_sampler.error
            await asyncio.sleep(10)
            return
        if storage_handoff.state()["mode"] == "quiesce" or track.reader is None or track.path is None:
            return
        cfg = runtime()
        lag = max(0.0, track.reader.latest_time - fragment.time)
        wall = utcnow() - timedelta(seconds=lag)
        prefix = f"live-{track.source_id}-{int(wall.timestamp() * 10)}"
        folder = nsfw_dir()
        folder.mkdir(parents=True, exist_ok=True)
        # Counted as a storage job: a detach waits for this one short read.
        self._storage_jobs += 1
        try:
            reply = await self._nsfw_sampler.request({
                "path": str(track.path), "init": track.reader.init_length, "offset": fragment.offset,
                "length": fragment.length, "candidate": float(cfg.nsfw_candidate),
                "images_dir": str(folder), "prefix": prefix,
                # Every suspect keeps a preview until verified; duplicates inside
                # a moment are pruned afterwards (_prune_preview), so a moment
                # never loses its picture when its first frame is rejected.
                "preview": True,
            }, timeout=45)
        finally:
            self._storage_jobs -= 1
        track.last_time, track.last_sample = fragment.time, time.monotonic()
        track.available = [f for f in track.available if f.time > fragment.time]
        if "error" in reply:
            track.errors += 1
            self.nsfw_live_detail = f"{track.source_name}: {reply['error']}"[-300:]
            return
        track.samples += 1
        self._nsfw_live_cover(track, fragment.time, float(cfg.nsfw_step_seconds))
        score = float(reply.get("score") or 0)
        if score < float(cfg.nsfw_candidate):
            return
        verifier = bool(cfg.nsfw_verify_model) and Path(cfg.nsfw_verify_model).is_file()
        state = "pending" if verifier else ("review" if score >= float(cfg.nsfw_threshold) else "rejected")
        if state == "rejected":
            _remove_images(reply.get("image"), reply.get("verify_image"))
            return
        with db_session() as db:
            db.add(NsfwMark(
                source_id=track.source_id, session_id=track.session_id, part_path=str(track.path),
                part_time=fragment.time, wall_at=wall, fast_score=score, cls=str(reply.get("class") or ""),
                state=state, image=str(reply.get("image") or ""),
                verify_image=str(reply.get("verify_image") or "") if verifier else "",
            ))
        if not verifier:
            _remove_images(None, reply.get("verify_image"))
            with db_session() as db:
                latest = db.scalar(select(NsfwMark.id).where(NsfwMark.source_id == track.source_id)
                                   .order_by(NsfwMark.id.desc()).limit(1))
            if latest:
                self._prune_preview(int(latest))
        track.marks += 1

    def _nsfw_live_cover(self, track: LiveTrack, at: float, step: float) -> None:
        with db_session() as db:
            row = db.get(NsfwCoverage, str(track.path))
            if row is None:
                db.add(NsfwCoverage(part_path=str(track.path), source_id=track.source_id, samples=1,
                                    first_time=at, last_time=at, covered_seconds=step, updated_at=utcnow()))
                return
            gap = max(0.0, at - row.last_time)
            row.covered_seconds += min(gap, 1.5 * step)
            row.samples += 1
            row.last_time = max(row.last_time, at)
            row.updated_at = utcnow()

    # ---------- verifier ----------
    async def _nsfw_verify_loop(self) -> None:
        self._nsfw_live_init()
        try:
            while not self._stopping:
                try:
                    await self._nsfw_verify_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_errors["nsfw-verify"] = str(exc)[-800:]
                    await asyncio.sleep(5)
        finally:
            await self._nsfw_verifier.close()

    async def _nsfw_verify_once(self) -> None:
        cfg = runtime()
        has_model = bool(cfg.nsfw_verify_model) and Path(cfg.nsfw_verify_model).is_file()
        if not cfg.nsfw_enabled or not has_model:
            self.nsfw_verify_state = "disabled" if not cfg.nsfw_enabled else "models_missing"
            await self._nsfw_verifier.close()
            await asyncio.sleep(5)
            return
        with db_session() as db:
            mark = db.scalar(select(NsfwMark).where(NsfwMark.state == "pending").order_by(NsfwMark.wall_at).limit(1))
            if mark is not None:
                db.expunge(mark)
        if mark is None:
            self.nsfw_verify_state = "idle"
            if self._nsfw_verifier.alive and time.monotonic() - self._nsfw_verifier.last_used > 120:
                await self._nsfw_verifier.close()  # frees ~300 MB when there is nothing to check
            await asyncio.sleep(3)
            return
        if load_average() > float(cfg.nsfw_live_max_load):
            self.nsfw_verify_state = "busy"
            await asyncio.sleep(5)
            return
        self.nsfw_verify_state = "running"
        threshold = float(cfg.nsfw_threshold)
        wall = _aware(mark.wall_at)
        with db_session() as db:
            anchor = db.scalar(select(NsfwMark).where(
                NsfwMark.source_id == mark.source_id, NsfwMark.state == "confirmed",
                NsfwMark.wall_at <= wall, NsfwMark.wall_at >= wall - timedelta(seconds=INHERIT_SECONDS),
                NsfwMark.id != mark.id,
            ).limit(1))
        score, cls = None, ""
        if anchor is not None:
            state = "inherited"  # inside a confirmed stretch: re-checked once a minute
        elif not mark.verify_image or not Path(mark.verify_image).is_file():
            state = "review" if mark.fast_score >= threshold else "rejected"
        else:
            if not await self._nsfw_verifier.ensure():
                self.nsfw_verify_state = "error"
                self.last_errors["nsfw-verify"] = self._nsfw_verifier.error
                await asyncio.sleep(15)
                return
            reply = await self._nsfw_verifier.request({"image": mark.verify_image}, timeout=180)
            if "error" in reply:
                state = "review" if mark.fast_score >= threshold else "rejected"
            else:
                score, cls = float(reply.get("score") or 0), str(reply.get("class") or "")
                state = "confirmed" if score >= threshold else ("review" if mark.fast_score >= threshold else "rejected")
        with db_session() as db:
            current = db.get(NsfwMark, mark.id)
            if current is None:
                return
            current.state, current.verified_score, current.verified_cls = state, score, cls
            current.verify_image = ""
            recording_id = current.recording_id
        _remove_images(mark.image if state == "rejected" else None, mark.verify_image)
        if state != "rejected":
            self._prune_preview(mark.id)
        if recording_id:
            self.nsfw_finalize_live(int(recording_id))

    def _prune_preview(self, mark_id: int) -> None:
        """Keep one preview per moment: drop this one if an earlier kept mark covers it."""
        with db_session() as db:
            mark = db.get(NsfwMark, mark_id)
            if mark is None or not mark.image:
                return
            # Same capture part, measured on the file's own clock (exact, unlike wall time).
            earlier = db.scalar(select(NsfwMark).where(
                NsfwMark.part_path == mark.part_path, NsfwMark.id != mark.id, NsfwMark.image != "",
                NsfwMark.state.in_(["confirmed", "inherited", "review"]),
                NsfwMark.part_time < mark.part_time,
                NsfwMark.part_time >= mark.part_time - PREVIEW_GAP_SECONDS,
            ).limit(1))
            if earlier is None:
                return
            image, mark.image = mark.image, ""
        _remove_images(image, None)

    # ---------- mapping onto the uploaded file ----------
    def nsfw_attach_parts(self, recording_id: int, parts: list[tuple[str, float, float]]) -> None:
        """parts: (capture part path, offset in the final file, duration) in file order."""
        cfg = runtime()
        paths = [path for path, _offset, _duration in parts]
        with db_session() as db:
            rec = db.get(Recording, recording_id)
            if rec is None:
                return
            for path, offset, _duration in parts:
                for mark in db.scalars(select(NsfwMark).where(NsfwMark.part_path == path)).all():
                    mark.recording_id = recording_id
                    mark.file_time = float(offset) + float(mark.part_time)
            covered = total = 0.0
            coverage = {row.part_path: row for row in db.scalars(
                select(NsfwCoverage).where(NsfwCoverage.part_path.in_(paths))).all()}
            for path, _offset, duration in parts:
                total += max(0.0, float(duration))
                row = coverage.get(path)
                if row is not None:
                    covered += min(float(row.covered_seconds), max(0.0, float(duration)))
                    db.delete(row)
            ratio = covered / total if total > 0 else 0.0
            rec.nsfw_live_coverage = round(ratio, 3)
            if cfg.nsfw_enabled and ratio >= LIVE_COVERAGE_OK and rec.nsfw_status == "pending":
                # Seen live: no full scan needed, only the pending verifications.
                rec.nsfw_source, rec.nsfw_status = "live", "verifying"
        self.nsfw_finalize_live(recording_id)

    def nsfw_finalize_live(self, recording_id: int) -> None:
        cfg = runtime()
        with db_session() as db:
            rec = db.get(Recording, recording_id)
            if rec is None or rec.nsfw_source != "live":
                return
            local_path = Path(rec.local_path)
            marks = list(db.scalars(select(NsfwMark).where(NsfwMark.recording_id == recording_id)
                                    .order_by(NsfwMark.file_time)).all())
            pending = [m for m in marks if m.state == "pending"]
            verdicts = []
            for mark in marks:
                label = NSFW_LABEL.get(mark.state)
                if label and mark.file_time is not None:
                    score = mark.verified_score if mark.verified_score is not None else mark.fast_score
                    verdicts.append(Verdict(float(mark.file_time), label, float(score or 0), combine_classes(mark.verified_cls, mark.cls)))
            step = max(5.0, float(cfg.nsfw_step_seconds)) * 1.5
            moments = merge_moments(verdicts, step)
            payload = []
            for moment in moments:
                image = next((m.image for m in marks if m.image and m.file_time is not None
                              and moment.start - 0.01 <= m.file_time <= moment.end and NSFW_LABEL.get(m.state)), "")
                payload.append(dict(moment.as_dict(), image=image))
            rec.nsfw_moments = json.dumps(payload)
            rec.nsfw_max_score = max((m.score for m in moments), default=0.0)
            rec.nsfw_progress = 1.0 - (len(pending) / len(marks) if marks else 0.0)
            if pending:
                rec.nsfw_status = "verifying"
            else:
                rec.nsfw_status = overall(moments)
                rec.nsfw_scanned_at = utcnow()
                rec.nsfw_error = ""
                rec.nsfw_file_sig = file_signature(local_path)
            done = not pending
        if done:
            with contextlib.suppress(Exception):
                self._delete_uploaded_local_if_ready(recording_id, local_path)


def _remove_images(preview: str | None, verify_path: str | None) -> None:
    if preview:
        with contextlib.suppress(OSError):
            (nsfw_dir() / Path(preview).name).unlink()
    if verify_path:
        with contextlib.suppress(OSError):
            Path(verify_path).unlink()
