"""NSFW moment scanner for recorded videos (NudeNet 3.4 ONNX models).

Runs as a separate low-priority process (``python -m app.nsfw_scan``) so a
crash or a kill during an NVMe eject never touches the web/worker process.

Pipeline, no temporary files:
- ffmpeg decodes keyframes only (``-skip_frame nokey``), keeps one every
  ``step`` seconds of *real* presentation time, letterboxes to 640x640 and
  streams raw RGB on stdout; ``showinfo`` on stderr gives each frame's pts, so
  timestamps stay correct across capture gaps.
- The small model (320n) scores every sampled frame. At or above
  ``threshold`` its hit is final. Only frames in the uncertain band
  [``candidate``, ``threshold``) are re-checked with the large model (640m),
  which confirms or discards them; inside an NSFW stretch they simply continue
  it. No "review" (da controllare) verdicts are produced since 3.4.16.

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
# Hits up to this many steps apart belong to one moment (and count as
# continuously watched in the live coverage).
MOMENT_GAP_FACTOR = 2.5


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


# Most explicit first: the timeline icon shows the first one, the lists all.
EXPLICITNESS = {"FEMALE_GENITALIA_EXPOSED": 5, "MALE_GENITALIA_EXPOSED": 5, "ANUS_EXPOSED": 4,
                "FEMALE_BREAST_EXPOSED": 3, "BUTTOCKS_EXPOSED": 2}


def combine_classes(*values: str) -> str:
    """Union of "A+B" class lists, most explicit first."""
    seen = {part for value in values for part in str(value or "").split("+") if part}
    return "+".join(sorted(seen, key=lambda c: (-EXPLICITNESS.get(c, 0), c)))


def class_scores(output, hot_indexes: list[int]) -> tuple[float, str]:
    """Best score among the hot classes of one YOLOv8 output (1, 4+C, N).

    Every hot class clearly present in the frame is reported (score at least
    0.3 and at least half of the best), most explicit first, so breasts and
    genitals seen together are both kept and genitals win the icon.
    """
    import numpy as np
    rows = np.asarray(output)[0]  # (4+C, N)
    scores = rows[4:, :]
    per_class: dict[str, float] = {}
    for index in hot_indexes:
        if index < scores.shape[0] and scores.shape[1]:
            per_class[LABELS[index]] = float(scores[index].max())
    if not per_class:
        return 0.0, ""
    best = max(per_class.values())
    floor = max(0.3, best * 0.5)
    present = [cls for cls, value in per_class.items() if value >= floor]
    if not present:
        present = [max(per_class, key=per_class.get)]
    return best, combine_classes(*present)


def downsample(frame, size: int):
    """640x640x3 uint8 → size x size float32 CHW batch in [0, 1] (box filter)."""
    import numpy as np
    image = frame.astype(np.float32)
    factor = FRAME // size
    if factor > 1:
        image = image.reshape(size, factor, size, factor, 3).mean(axis=(1, 3))
    return (image / 255.0).transpose(2, 0, 1)[None, ...]


def needs_verify(score: float, candidate: float, threshold: float) -> bool:
    """Only the uncertain band goes to the large model (none when candidate >= threshold)."""
    return candidate <= score < threshold


def judge(t: float, fast: tuple[float, str], verified: tuple[float, str] | None, threshold: float) -> Verdict:
    """The small model decides on its own at or above ``threshold``; below it only
    a large-model confirmation counts. No "review" verdicts are produced (3.4.16):
    the small model was usually right and re-checking its sure hits only added
    work and "da controllare" moments."""
    fast_score, fast_cls = fast
    if fast_score >= threshold:
        return Verdict(t, "nsfw", fast_score, fast_cls, fast_score, None)
    if verified is not None:
        big_score, big_cls = verified
        if big_score >= threshold:
            return Verdict(t, "nsfw", big_score, big_cls, fast_score, big_score)
        return Verdict(t, "", max(fast_score, big_score), fast_cls, fast_score, big_score)
    return Verdict(t, "", fast_score, fast_cls, fast_score, None)


def merge_moments(verdicts: list[Verdict], step: float, gap_factor: float = MOMENT_GAP_FACTOR) -> list[Moment]:
    moments: list[Moment] = []
    for verdict in sorted((v for v in verdicts if v.label), key=lambda v: v.t):
        last = moments[-1] if moments else None
        if last and verdict.t - last.end <= step * gap_factor:
            last.end = verdict.t + step
            if SEVERITY[verdict.label] > SEVERITY[last.label] or (verdict.label == last.label and verdict.score > last.score):
                last.label, last.score = verdict.label, verdict.score
            last.cls = combine_classes(last.cls, verdict.cls)
            continue
        moments.append(Moment(verdict.t, verdict.t + step, verdict.label, verdict.score, verdict.cls))
    return moments


def stretches(samples: list[Verdict], step: float, max_gap: float) -> list[Moment]:
    """Moments from every checked frame, labelled or not (3.4.16).

    A moment starts at the first NSFW frame and lasts until the next checked
    frame that is not NSFW. It closes one ``step`` after its last frame when
    nothing was checked for more than ``max_gap`` seconds (capture gap,
    sampler pause) or at the end. Frames without a label are the boundaries,
    so a stretch sampled as NSFW every time is one band, not many dashes.
    """
    moments: list[Moment] = []
    current: Moment | None = None
    last_t: float | None = None
    for sample in sorted(samples, key=lambda v: v.t):
        if current is not None and last_t is not None and sample.t - last_t > max_gap:
            current.end = last_t + step
            moments.append(current)
            current = None
        if sample.label:
            if current is None:
                current = Moment(sample.t, sample.t + step, sample.label, sample.score, sample.cls)
            else:
                current.end = sample.t + step
                if SEVERITY[sample.label] > SEVERITY[current.label] or (
                        sample.label == current.label and sample.score > current.score):
                    current.label, current.score = sample.label, sample.score
                current.cls = combine_classes(current.cls, sample.cls)
        elif current is not None:
            current.end = max(current.start, sample.t)
            moments.append(current)
            current = None
        last_t = sample.t
    if current is not None:
        moments.append(current)
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
        subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{FRAME}x{FRAME}",
                        "-i", "pipe:0", "-vf", f"crop={w}:{h}:{x}:{y},scale=480:-2", "-frames:v", "1", "-q:v", "5", target],
                       input=frame.tobytes(), timeout=30, check=True, capture_output=True)
        return True
    except Exception:
        return False


def helper_env() -> dict:
    """Environment for NSFW helpers: one math thread, no hidden thread pools."""
    import os
    env = dict(os.environ)
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env[key] = "1"
    return env


def _session(model: str, threads: int):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, threads)
    options.inter_op_num_threads = 1
    # Idle worker threads must sleep, not spin: on the 4-core node spinning
    # burned 2+ cores at nice 19 and starved the recorder of CPU time.
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
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
            ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-threads", "1", "-skip_frame", "nokey", "-copyts", *seek, "-i", path,
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
        by_time: dict[float, Verdict] = {}
        last_confirmed = -1.0  # time of the last NSFW frame
        last_emit = 0.0

        def record(verdict: Verdict, frame=None) -> None:
            nonlocal last_labelled, last_confirmed
            previous = by_time[max(by_time)] if by_time else None
            by_time[verdict.t] = verdict
            if not verdict.label and previous is not None and previous.label:
                # First clean frame after a moment: its end, kept with the hits.
                _emit({"type": "moment", "t": round(verdict.t, 1), "label": "", "score": round(verdict.score, 3),
                       "class": ""})
            if verdict.label:
                last_confirmed = verdict.t
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
            score = fast_result[0]
            in_streak = last_confirmed >= 0 and t - last_confirmed < self.reverify_seconds
            uncertain = needs_verify(score, self.candidate, self.threshold)
            big_result = None
            if uncertain and not in_streak and big is not None and verified < self.max_verify:
                # Uncertain band only (~5 s/frame on the node). Inside an NSFW
                # stretch a suspect frame simply continues it.
                session, name, size = big
                big_result = class_scores(session.run(None, {name: downsample(frame, size)})[0], hot)
                verified += 1
            if uncertain and in_streak:
                verdict = Verdict(t, "nsfw", score, fast_result[1], score, None)
            else:
                verdict = judge(t, fast_result, big_result, self.threshold)
            record(verdict, frame)
            now = time.monotonic()
            if now - last_emit >= 1.0:
                last_emit = now
                _emit({"type": "progress", "t": round(t, 1), "duration": round(duration, 1), "frames": frames,
                       "verified": verified, "elapsed": round(now - began, 1),
                       "found": sum(1 for v in by_time.values() if v.label)})
        proc.wait(timeout=30)
        self.verdicts = list(by_time.values())
        moments = stretches(self.verdicts, self.step, MOMENT_GAP_FACTOR * self.step)
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
