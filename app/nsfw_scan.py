"""NSFW moment scanner for recorded videos (NudeNet 3.4 ONNX models).

Runs as a separate low-priority process (``python -m app.nsfw_scan``) so a
crash or a kill during an NVMe eject never touches the web/worker process.

Pipeline, no temporary files:
- ffmpeg decodes keyframes only (``-skip_frame nokey``), keeps one every
  ``step`` seconds of *real* presentation time, letterboxes to 640x640 and
  streams raw RGB on stdout; ``showinfo`` on stderr gives each frame's pts, so
  timestamps stay correct across capture gaps.
- Pass 1: the small model (320n) scores every sampled frame.
- Pass 2: frames scoring above ``candidate`` are re-checked with the large
  model (640m) together with their neighbours. Only the large model can mark a
  moment NSFW; a confident small-model hit that the large model does not
  confirm becomes "review" (da controllare).

Progress is emitted as JSON lines on stdout: ``progress``, ``moment``, ``done``.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

LABELS = (
    "FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED", "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED", "ANUS_EXPOSED", "FEET_EXPOSED",
    "BELLY_COVERED", "FEET_COVERED", "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE",
    "BELLY_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED",
    "BUTTOCKS_COVERED",
)
DEFAULT_CLASSES = ("FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED")
FRAME = 640
SEVERITY = {"review": 1, "nsfw": 2}


@dataclass
class Moment:
    start: float
    end: float
    label: str
    score: float
    cls: str

    def as_dict(self) -> dict:
        return {"start": round(self.start, 1), "end": round(self.end, 1), "label": self.label,
                "score": round(self.score, 3), "class": self.cls}


@dataclass
class Verdict:
    t: float
    label: str  # "nsfw" | "review" | ""
    score: float
    cls: str
    fast: float = 0.0
    verified: float | None = None


def class_scores(output, hot_indexes: list[int]) -> tuple[float, str]:
    """Best score among the hot classes of one YOLOv8 output (1, 4+C, N)."""
    import numpy as np
    rows = np.asarray(output)[0]  # (4+C, N)
    scores = rows[4:, :]
    best_score, best_cls = 0.0, ""
    for index in hot_indexes:
        if index < scores.shape[0] and scores.shape[1]:
            value = float(scores[index].max())
            if value > best_score:
                best_score, best_cls = value, LABELS[index]
    return best_score, best_cls


def downsample(frame, size: int):
    """640x640x3 uint8 → size x size float32 CHW batch in [0, 1] (box filter)."""
    import numpy as np
    image = frame.astype(np.float32)
    factor = FRAME // size
    if factor > 1:
        image = image.reshape(size, factor, size, factor, 3).mean(axis=(1, 3))
    return (image / 255.0).transpose(2, 0, 1)[None, ...]


def judge(t: float, fast: tuple[float, str], verified: tuple[float, str] | None, threshold: float) -> Verdict:
    fast_score, fast_cls = fast
    if verified is not None:
        big_score, big_cls = verified
        if big_score >= threshold:
            return Verdict(t, "nsfw", big_score, big_cls, fast_score, big_score)
        if fast_score >= threshold:
            return Verdict(t, "review", fast_score, fast_cls, fast_score, big_score)
        return Verdict(t, "", max(fast_score, big_score), fast_cls, fast_score, big_score)
    if fast_score >= threshold:
        # Without a verification model a single small-model hit is never final.
        return Verdict(t, "review", fast_score, fast_cls, fast_score, None)
    return Verdict(t, "", fast_score, fast_cls, fast_score, None)


def merge_moments(verdicts: list[Verdict], step: float, gap_factor: float = 2.5) -> list[Moment]:
    moments: list[Moment] = []
    for verdict in sorted((v for v in verdicts if v.label), key=lambda v: v.t):
        last = moments[-1] if moments else None
        if last and verdict.t - last.end <= step * gap_factor:
            last.end = verdict.t + step
            if SEVERITY[verdict.label] > SEVERITY[last.label] or (verdict.label == last.label and verdict.score > last.score):
                last.label, last.score, last.cls = verdict.label, verdict.score, verdict.cls
            continue
        moments.append(Moment(verdict.t, verdict.t + step, verdict.label, verdict.score, verdict.cls))
    return moments


def overall(moments: list[Moment]) -> str:
    if any(m.label == "nsfw" for m in moments):
        return "nsfw"
    if moments:
        return "review"
    return "safe"


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _probe(path: str) -> tuple[float, float]:
    return _probe_full(path)[:2]


def _probe_full(path: str) -> tuple[float, float, int, int]:
    """(start_time, duration, width, height) from ffmpeg's input banner; no ffprobe needed."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", path], capture_output=True, text=True, timeout=60).stderr
    duration = start = 0.0
    width = height = 0
    match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    match = re.search(r"start:\s*(-?[\d.]+)", err)
    if match:
        start = float(match.group(1))
    return start, duration, width, height


def content_box(width: int, height: int) -> tuple[int, int, int, int]:
    """Where the picture sits inside the 640x640 letterboxed frame (w, h, x, y)."""
    if width <= 0 or height <= 0:
        return FRAME, FRAME, 0, 0
    factor = FRAME / max(width, height)
    w = min(FRAME, max(2, int(width * factor)))
    h = min(FRAME, max(2, int(height * factor)))
    return w, h, (FRAME - w) // 2, (FRAME - h) // 2


def save_preview(frame, target: str, box: tuple[int, int, int, int]) -> bool:
    """JPEG of the analysed frame (letterbox removed) via ffmpeg, no extra decode."""
    w, h, x, y = box
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{FRAME}x{FRAME}",
                        "-i", "pipe:0", "-vf", f"crop={w}:{h}:{x}:{y},scale=480:-2", "-frames:v", "1", "-q:v", "5", target],
                       input=frame.tobytes(), timeout=30, check=True, capture_output=True)
        return True
    except Exception:
        return False


def _session(model: str, threads: int):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, threads)
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(model, sess_options=options, providers=["CPUExecutionProvider"])
    shape = session.get_inputs()[0].shape
    size = shape[2] if isinstance(shape[2], int) else 320
    return session, session.get_inputs()[0].name, int(size)


@dataclass
class Scanner:
    fast_model: str
    verify_model: str = ""
    step: float = 5.0
    candidate: float = 0.35
    threshold: float = 0.6
    threads: int = 1
    classes: tuple[str, ...] = DEFAULT_CLASSES
    reverify_seconds: float = 60.0
    max_verify: int = 400
    images_dir: str = ""
    image_prefix: str = "moment"
    verdicts: list[Verdict] = field(default_factory=list)

    def run(self, path: str, start_at: float = 0.0) -> dict:
        import numpy as np
        hot = [LABELS.index(c) for c in self.classes if c in LABELS]
        fast, fast_input, fast_size = _session(self.fast_model, self.threads)
        big = _session(self.verify_model, self.threads) if self.verify_model else None
        start, duration, width, height = _probe_full(path)
        box = content_box(width, height)
        last_labelled = -1e9
        seek = ["-ss", f"{start_at:.3f}"] if start_at > 0 else []
        select = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{self.step})',"
                  f"scale={FRAME}:{FRAME}:force_original_aspect_ratio=decrease,"
                  f"pad={FRAME}:{FRAME}:(ow-iw)/2:(oh-ih)/2:black,showinfo")
        proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-skip_frame", "nokey", "-copyts", *seek, "-i", path,
             "-an", "-sn", "-vf", select, "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pts: queue.Queue[float | None] = queue.Queue()

        def read_stderr() -> None:
            pattern = re.compile(rb"pts_time:\s*(-?[0-9.]+)")
            for line in proc.stderr:  # type: ignore[union-attr]
                match = pattern.search(line)
                if match and b"Parsed_showinfo" in line:
                    pts.put(float(match.group(1)))
            pts.put(None)

        threading.Thread(target=read_stderr, daemon=True).start()
        frame_bytes = FRAME * FRAME * 3
        began = time.monotonic()
        frames = verified = 0
        history: deque = deque(maxlen=2)  # (t, frame, fast) of previous frames
        verify_next = False
        verified_at: set[float] = set()
        by_time: dict[float, Verdict] = {}
        last_confirmed = -1.0  # time of the last large-model NSFW confirmation
        last_emit = 0.0

        def record(verdict: Verdict, frame=None) -> None:
            nonlocal last_labelled
            by_time[verdict.t] = verdict
            if verdict.label:
                event = {"type": "moment", "t": round(verdict.t, 1), "label": verdict.label,
                         "score": round(verdict.score, 3), "class": verdict.cls}
                # One preview per moment, taken from the frame already in memory:
                # works after the local file is deleted and costs no extra seek.
                new_moment = verdict.t - last_labelled > self.step * 2.5
                last_labelled = max(last_labelled, verdict.t)
                if new_moment and frame is not None and self.images_dir:
                    name = f"{self.image_prefix}-{int(verdict.t)}.jpg"
                    if save_preview(frame, os.path.join(self.images_dir, name), box):
                        event["image"] = name
                _emit(event)

        def verify(t: float, frame, fast_result) -> None:
            nonlocal verified, last_confirmed
            result = None
            if big is not None and verified < self.max_verify:
                if t in verified_at:
                    return
                session, name, size = big
                result = class_scores(session.run(None, {name: downsample(frame, size)})[0], hot)
                verified += 1
                verified_at.add(t)
            verdict = judge(t, fast_result, result, self.threshold)
            if result is not None:
                last_confirmed = t if verdict.label == "nsfw" else -1.0
            record(verdict, frame)

        while True:
            raw = proc.stdout.read(frame_bytes)  # type: ignore[union-attr]
            if len(raw) < frame_bytes:
                break
            stamp = pts.get(timeout=120)
            if stamp is None:
                break
            t = max(0.0, stamp - start)
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(FRAME, FRAME, 3)
            frames += 1
            fast_result = class_scores(fast.run(None, {fast_input: downsample(frame, fast_size)})[0], hot)
            candidate = fast_result[0] >= self.candidate
            in_streak = last_confirmed >= 0 and t - last_confirmed < self.reverify_seconds
            if candidate and in_streak:
                # Long NSFW stretches: trust the small model inside a confirmed
                # streak and re-check with the large one only every
                # reverify_seconds, so a fully explicit video costs minutes, not hours.
                record(Verdict(t, "nsfw", fast_result[0], fast_result[1], fast_result[0], None), frame)
                flagged, verify_next = True, False
                history.append((t, frame, fast_result))
                continue
            renewing = last_confirmed >= 0  # streak expired: a plain re-check
            if candidate or verify_next or (big is None and fast_result[0] >= self.threshold):
                verify(t, frame, fast_result)
            # The large model costs ~5 s/frame on the node: look at the
            # neighbours only around a newly flagged moment.
            flagged = bool(by_time.get(t) and by_time[t].label)
            if flagged and not renewing:
                for prev_t, prev_frame, prev_fast in history:
                    verify(prev_t, prev_frame, prev_fast)
            verify_next = flagged and not renewing
            history.append((t, frame, fast_result))
            now = time.monotonic()
            if now - last_emit >= 1.0:
                last_emit = now
                _emit({"type": "progress", "t": round(t, 1), "duration": round(duration, 1), "frames": frames,
                       "verified": verified, "elapsed": round(now - began, 1),
                       "found": sum(1 for v in by_time.values() if v.label)})
        proc.wait(timeout=30)
        self.verdicts = list(by_time.values())
        moments = merge_moments(self.verdicts, self.step)
        result = {"type": "done", "status": overall(moments), "moments": [m.as_dict() for m in moments],
                  "frames": frames, "verified": verified, "duration": round(duration, 1),
                  "elapsed": round(time.monotonic() - began, 1), "verify_model": bool(big),
                  "max_score": round(max((v.score for v in self.verdicts if v.label), default=0.0), 3)}
        if frames == 0:
            result = {"type": "error", "error": f"Nessun fotogramma decodificato (ffmpeg exit {proc.returncode})"}
        _emit(result)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scansione NSFW di un video")
    parser.add_argument("path")
    parser.add_argument("--fast-model", required=True)
    parser.add_argument("--verify-model", default="")
    parser.add_argument("--step", type=float, default=5.0)
    parser.add_argument("--candidate", type=float, default=0.35)
    parser.add_argument("--threshold", type=float, default=0.6)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--classes", default=",".join(DEFAULT_CLASSES))
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--reverify", type=float, default=60.0)
    parser.add_argument("--max-verify", type=int, default=400)
    parser.add_argument("--images-dir", default="")
    parser.add_argument("--image-prefix", default="moment")
    args = parser.parse_args(argv)
    classes = tuple(c.strip().upper() for c in args.classes.split(",") if c.strip().upper() in LABELS) or DEFAULT_CLASSES
    try:
        Scanner(args.fast_model, args.verify_model, args.step, args.candidate, args.threshold, args.threads,
                classes, args.reverify, args.max_verify, args.images_dir, args.image_prefix).run(args.path, args.start)
    except Exception as exc:  # reported to the worker as a JSON line
        _emit({"type": "error", "error": str(exc)[-600:]})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
