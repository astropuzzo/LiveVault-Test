"""Background NSFW scan queue, mixed into WorkerManager.

The scan itself runs in ``python -m app.nsfw_scan`` at the lowest CPU and I/O
priority. This loop only picks recordings, streams progress into memory for
the UI, checkpoints the position into the database and stops the child at once
when the NVMe is being ejected (storage quiesce), when a recorder starts (if
"only when idle" is on) or when the feature is switched off. A stopped scan
resumes from its last position.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import case, select

from . import storage_handoff
from .config import settings
from .db import Recording, db_session
from .nsfw_scan import MOMENT_GAP_FACTOR, Verdict, helper_env, overall, stretches
from .settings_store import runtime
from .utils import utcnow

OPEN_STATES = ("pending", "scanning", "paused")
# After a (re)start the poller needs a moment to resume the live captures; a
# full scan started in that gap competes with them and is stopped at once.
STARTUP_GRACE_SECONDS = 90


def file_signature(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_size}-{stat.st_mtime_ns}"


def nsfw_dir() -> Path:
    return settings.data_dir / "nsfw"


def models_ready(cfg=None) -> tuple[bool, str]:
    cfg = cfg or runtime()
    if not Path(cfg.nsfw_fast_model).is_file():
        return False, f"Modello veloce mancante: {cfg.nsfw_fast_model}"
    if cfg.nsfw_verify_model and not Path(cfg.nsfw_verify_model).is_file():
        return False, f"Modello di verifica mancante: {cfg.nsfw_verify_model}"
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False, "onnxruntime non installato nell'immagine"
    return True, ""


class NsfwWorkerMixin:
    nsfw_current: dict | None = None
    nsfw_state: str = "disabled"
    nsfw_detail: str = ""
    _nsfw_stop_reason: str = ""
    _nsfw_ready_at: float = 0.0

    def nsfw_snapshot(self) -> dict:
        return {"state": self.nsfw_state, "detail": self.nsfw_detail, "current": self.nsfw_current}

    def _recover_interrupted_nsfw(self) -> None:
        with db_session() as db:
            for rec in db.scalars(select(Recording).where(Recording.nsfw_status == "scanning")).all():
                rec.nsfw_status = "paused"

    def _next_nsfw_job(self) -> Recording | None:
        with db_session() as db:
            candidates = db.scalars(
                select(Recording)
                .where(Recording.local_deleted.is_(False), Recording.integrity_status == "passed",
                       Recording.nsfw_status.in_(["pending", "paused"]))
                # Already uploaded files first: they only wait for the scan to be deleted.
                .order_by(case((Recording.upload_status == "uploaded", 0), else_=1), Recording.started_at.asc())
                .limit(50)
            ).all()
            # NVMe detached: only files written to the internal buffer are reachable.
            buffering = storage_handoff.state()["mode"] == "buffer"
            rec = next((row for row in candidates if not buffering or Path(row.local_path).is_file()), None)
            if rec is None:
                # Moments must match the file that goes to the cloud: if a
                # scanned local file was converted or repaired, scan it again.
                done = db.scalars(
                    select(Recording)
                    .where(Recording.local_deleted.is_(False), Recording.nsfw_file_sig != "",
                           Recording.nsfw_status.in_(["safe", "review", "nsfw"]))
                    .order_by(Recording.id.desc()).limit(200)
                ).all()
                for row in done:
                    signature = file_signature(Path(row.local_path))
                    if signature and signature != row.nsfw_file_sig:
                        row.nsfw_status, row.nsfw_resume_at, row.nsfw_progress = "pending", 0.0, 0.0
                        row.nsfw_hits, row.nsfw_error = "", "File modificato dopo l'analisi: rianalisi"
                        rec = row
                        break
            if rec:
                db.flush()
                db.expunge(rec)
            return rec

    def _nsfw_waits_for_recorders(self, cfg) -> bool:
        """Full scans yield to active captures.

        With live analysis on, captures are analysed by the sampler: a full
        scan running beside them took 2+ cores, pushed the load over
        ``nsfw_live_max_load`` so the sampler stopped, and left every file
        uncovered, i.e. queued for yet another full scan.
        """
        yields = bool(cfg.nsfw_only_when_idle or cfg.nsfw_live_enabled)
        if yields and time.monotonic() < self._nsfw_ready_at:
            return True
        if not getattr(self, "active", None):
            return False
        return yields

    def _nsfw_threads(self, cfg) -> int:
        # The "Core CPU" setting is for idle time; next to a capture one core only.
        return 1 if getattr(self, "active", None) else max(1, int(cfg.nsfw_threads))

    def _nsfw_gate(self) -> str:
        """Why the queue must wait right now ('' = may run)."""
        cfg = runtime()
        if not cfg.nsfw_enabled:
            return "disabled"
        if not storage_handoff.media_online() and storage_handoff.state()["mode"] != "buffer":
            return "waiting_storage"
        if self._nsfw_waits_for_recorders(cfg):
            return "waiting_idle"
        ready, detail = models_ready(cfg)
        if not ready:
            self.nsfw_detail = detail
            return "models_missing"
        return ""

    async def _nsfw_loop(self) -> None:
        self._nsfw_ready_at = time.monotonic() + STARTUP_GRACE_SECONDS
        while not self._stopping:
            try:
                gate = self._nsfw_gate()
                if gate:
                    self.nsfw_state = gate
                    if gate != "models_missing":
                        self.nsfw_detail = ""
                    await self._sleep_or_wake(5.0)
                    continue
                rec = self._next_nsfw_job()
                if rec is None:
                    self.nsfw_state, self.nsfw_detail = "idle", ""
                    await self._sleep_or_wake(10.0)
                    continue
                self.nsfw_state = "running"
                await self._run_nsfw_job(rec)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_errors["nsfw"] = str(exc)[-1000:]
                await asyncio.sleep(5)

    def _nsfw_command(self, rec: Recording) -> list[str]:
        cfg = runtime()
        command = [sys.executable, "-m", "app.nsfw_scan", rec.local_path,
                   "--fast-model", cfg.nsfw_fast_model, "--verify-model", cfg.nsfw_verify_model,
                   "--step", str(cfg.nsfw_step_seconds), "--candidate", str(cfg.nsfw_candidate),
                   "--threshold", str(cfg.nsfw_threshold), "--threads", str(self._nsfw_threads(cfg)),
                   "--classes", cfg.nsfw_classes, "--start", f"{float(rec.nsfw_resume_at or 0):.1f}",
                   "--images-dir", str(nsfw_dir()), "--image-prefix", str(rec.id)]
        prefix = ["nice", "-n", "19"] if shutil.which("nice") else []
        if shutil.which("ionice"):
            prefix += ["ionice", "-c3"]
        return prefix + command

    def _nsfw_should_stop(self, threads: int = 1) -> str:
        cfg = runtime()
        if self._stopping:
            return "shutdown"
        # Attaching/detaching the NVMe: close the file at once, resume later.
        if storage_handoff.state()["mode"] not in ("nvme", "legacy", "buffer"):
            return "storage"
        if not cfg.nsfw_enabled:
            return "disabled"
        # A capture started: pause (resumes from the checkpoint), or restart
        # the helper on a single core when full scans may run beside captures.
        if self._nsfw_waits_for_recorders(cfg) or threads > self._nsfw_threads(cfg):
            return "recording"
        return self._nsfw_stop_reason

    def request_nsfw_stop(self, reason: str = "user") -> None:
        self._nsfw_stop_reason = reason

    @storage_handoff.media_job(buffering=True)
    async def _run_nsfw_job(self, rec: Recording) -> None:
        path = Path(rec.local_path)
        if not path.is_file():
            if storage_handoff.state()["mode"] == "buffer":
                return  # on the detached NVMe: scanned when it is back
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current:
                    current.nsfw_status, current.nsfw_error = "skipped", "File locale non disponibile"
            return
        hits = _load(rec.nsfw_hits)
        resume_at = float(rec.nsfw_resume_at or 0)
        if (resume_at <= 0 and not hits and rec.nsfw_source != "live" and not rec.nsfw_live_coverage
                and hasattr(self, "nsfw_attach_parts")):
            # Queued before its live marks were matched (e.g. raw .capture name):
            # attach them now and skip the full scan when coverage is enough.
            # Never after a stitch already measured the coverage: matching the
            # final name again finds nothing and would reset it to 0.
            self.nsfw_attach_parts(rec.id, [(str(path), 0.0, float(rec.duration_seconds or 0))])
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current is None or current.nsfw_source == "live":
                    return
        nsfw_dir().mkdir(parents=True, exist_ok=True)
        if resume_at <= 0 and not hits:
            for old in nsfw_dir().glob(f"{rec.id}-*.jpg"):
                old.unlink(missing_ok=True)
        signature = file_signature(path)
        with db_session() as db:
            current = db.get(Recording, rec.id)
            if not current or current.nsfw_status not in ("pending", "paused"):
                return
            current.nsfw_status, current.nsfw_error = "scanning", ""
        self._nsfw_stop_reason = ""
        began = time.monotonic()
        self.nsfw_current = {"recording_id": rec.id, "name": rec.source_name, "filename": rec.filename,
                             "t": resume_at, "duration": float(rec.duration_seconds or 0), "progress": 0.0,
                             "frames": 0, "verified": 0, "found": sum(1 for h in hits if h.get("label")), "eta_seconds": None,
                             "speed": None, "started_at": utcnow().isoformat(), "resumed_from": resume_at}
        threads = self._nsfw_threads(runtime())
        proc = await asyncio.create_subprocess_exec(
            *self._nsfw_command(rec), cwd=str(Path(__file__).resolve().parents[1]),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=helper_env())
        result: dict | None = None
        stopped = ""
        last_saved = 0.0
        try:
            while True:
                stopped = self._nsfw_should_stop(threads)
                if stopped:
                    break
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=2.0)  # type: ignore[union-attr]
                except asyncio.TimeoutError:
                    continue
                if not line:
                    break
                with contextlib.suppress(ValueError):
                    event = json.loads(line)
                    kind = event.get("type")
                    if kind == "progress":
                        self._nsfw_progress(rec.id, event, resume_at, began)
                        if time.monotonic() - last_saved > 15:
                            last_saved = time.monotonic()
                            self._nsfw_checkpoint(rec.id, hits)
                    elif kind == "moment":
                        hits.append({k: event[k] for k in ("t", "label", "score", "class", "image") if k in event})
                        if self.nsfw_current:
                            self.nsfw_current["found"] = sum(1 for h in hits if h.get("label"))
                    elif kind in ("done", "error"):
                        result = event
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=10)
            self.nsfw_current = None
        if stopped:
            self._nsfw_checkpoint(rec.id, hits, status="paused")
            if stopped == "user":
                with db_session() as db:
                    current = db.get(Recording, rec.id)
                    if current:
                        current.nsfw_status, current.nsfw_error = "skipped", "Analisi annullata"
            if stopped == "storage":
                raise storage_handoff.StorageQuiesced()
            return
        if not result or result.get("type") == "error":
            with db_session() as db:
                current = db.get(Recording, rec.id)
                if current:
                    current.nsfw_status = "error"
                    current.nsfw_error = str((result or {}).get("error") or f"Scanner terminato (codice {proc.returncode})")[-600:]
            return
        cfg = runtime()
        verdicts = [Verdict(float(h["t"]), h["label"], float(h["score"]), h.get("class", "")) for h in hits]
        step = float(cfg.nsfw_step_seconds)
        # Hits also hold the first clean frame after each moment (its end).
        moments = stretches(verdicts, step, MOMENT_GAP_FACTOR * step)
        images = moment_images(moments, hits)
        payload = [dict(m.as_dict(), image=images[i]) for i, m in enumerate(moments)]
        with db_session() as db:
            current = db.get(Recording, rec.id)
            if current:
                current.nsfw_status = overall(moments)
                current.nsfw_source = "scan"
                current.nsfw_moments = json.dumps(payload)
                current.nsfw_hits = json.dumps(hits)
                current.nsfw_max_score = max((m.score for m in moments), default=0.0)
                current.nsfw_progress = 1.0
                current.nsfw_resume_at = 0.0
                current.nsfw_scanned_at = utcnow()
                current.nsfw_error = ""
                # A file that changed during the scan is picked up again next pass.
                current.nsfw_file_sig = signature if file_signature(path) == signature else "changed"
        self.last_errors.pop("nsfw", None)
        self._delete_uploaded_local_if_ready(rec.id, path)

    def _nsfw_progress(self, recording_id: int, event: dict, resume_at: float, began: float) -> None:
        current = self.nsfw_current
        if not current or current.get("recording_id") != recording_id:
            return
        duration = float(event.get("duration") or current.get("duration") or 0)
        position = float(event.get("t") or 0)
        elapsed = max(0.001, time.monotonic() - began)
        done = max(0.0, position - resume_at)
        speed = done / elapsed if done > 0 else None
        remaining = max(0.0, duration - position)
        current.update({
            "t": position, "duration": duration, "frames": int(event.get("frames") or 0),
            "verified": int(event.get("verified") or 0),
            "progress": min(1.0, position / duration) if duration else 0.0,
            "speed": round(speed, 2) if speed else None,
            "eta_seconds": int(remaining / speed) if speed else None,
        })

    def _nsfw_checkpoint(self, recording_id: int, hits: list[dict], status: str | None = None) -> None:
        current_state = self.nsfw_current or {}
        with db_session() as db:
            current = db.get(Recording, recording_id)
            if not current:
                return
            if current_state.get("recording_id") == recording_id:
                current.nsfw_resume_at = float(current_state.get("t") or 0)
                current.nsfw_progress = float(current_state.get("progress") or 0)
            current.nsfw_hits = json.dumps(hits)
            if status:
                current.nsfw_status = status

    def nsfw_hold_blocks_delete(self, rec: Recording) -> bool:
        """True while an uploaded file must stay local to be scanned."""
        cfg = runtime()
        if getattr(rec, "nsfw_status", "") not in OPEN_STATES:
            return False
        if not (getattr(cfg, "nsfw_enabled", False) and cfg.nsfw_hold_delete) or not models_ready(cfg)[0]:
            return False
        uploaded = rec.uploaded_at
        if uploaded is not None:
            if uploaded.tzinfo is None:
                from datetime import timezone
                uploaded = uploaded.replace(tzinfo=timezone.utc)
            if utcnow() - uploaded > timedelta(hours=max(0, int(cfg.nsfw_max_hold_hours))):
                return False
        from .storage import disk_state
        with contextlib.suppress(Exception):
            if disk_state().pressure != "ok":
                return False
        return True


def _load(raw: str) -> list[dict]:
    with contextlib.suppress(ValueError, TypeError):
        value = json.loads(raw or "[]")
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict) and "t" in v and "label" in v]
    return []


def moment_images(moments, hits: list[dict]) -> list[str]:
    """Preview per merged moment: the first hit inside it that saved a frame."""
    images = []
    for moment in moments:
        inside = sorted((h for h in hits if h.get("image") and moment.start - 0.01 <= float(h["t"]) <= moment.end),
                        key=lambda h: float(h["t"]))
        images.append(inside[0]["image"] if inside else "")
    return images
