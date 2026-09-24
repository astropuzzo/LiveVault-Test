"""Long-lived helper process for live NSFW analysis.

``python -m app.nsfw_live --mode sample``: small model. Each request names one
complete fragment of a capture that is still being recorded; the helper reads
just the init segment plus that fragment, decodes its first (key)frame with
ffmpeg and scores it. Suspect frames are saved twice on the internal drive: a
cropped preview for the UI and a 640x640 letterboxed copy for verification, so
verification never needs the recording again (it keeps working while the
NVMe is detached).

``python -m app.nsfw_live --mode verify``: large model on those saved copies.

Protocol: one JSON object per line on stdin, one JSON reply per line on
stdout. The helper only opens the recording for the duration of one request.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from .nsfw_scan import DEFAULT_CLASSES, FRAME, LABELS, _session, class_scores, downsample, save_preview

LETTERBOX = (f"scale={FRAME}:{FRAME}:force_original_aspect_ratio=decrease,"
             f"pad={FRAME}:{FRAME}:(ow-iw)/2:(oh-ih)/2:black")


def _decode(data: bytes, input_format: str | None = None):
    import numpy as np
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    if input_format:
        command += ["-f", input_format]
    command += ["-i", "pipe:0", "-map", "0:v:0", "-frames:v", "1", "-vf", LETTERBOX,
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    result = subprocess.run(command, input=data, capture_output=True, timeout=30)
    frame_bytes = FRAME * FRAME * 3
    if len(result.stdout) < frame_bytes:
        raise RuntimeError((result.stderr or b"frame non decodificabile").decode(errors="replace")[-300:])
    return np.frombuffer(result.stdout[:frame_bytes], dtype=np.uint8).reshape(FRAME, FRAME, 3)


def content_bounds(frame) -> tuple[int, int, int, int]:
    """Crop box (w, h, x, y) without the black letterbox padding."""
    import numpy as np
    lit = frame.max(axis=2) > 8
    rows = np.where(lit.any(axis=1))[0]
    cols = np.where(lit.any(axis=0))[0]
    if not len(rows) or not len(cols):
        return FRAME, FRAME, 0, 0
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    return max(2, w), max(2, h), x0, y0


def read_fragment(path: str, init_length: int, offset: int, length: int) -> bytes:
    with open(path, "rb") as handle:
        init = handle.read(init_length)
        handle.seek(offset)
        chunk = handle.read(length)
    if len(init) < init_length or len(chunk) < length:
        raise RuntimeError("Frammento non ancora completo sul disco")
    return init + chunk


class Helper:
    def __init__(self, args):
        self.hot = [LABELS.index(c) for c in args.classes.split(",") if c in LABELS] or \
            [LABELS.index(c) for c in DEFAULT_CLASSES]
        model = args.fast_model if args.mode == "sample" else args.verify_model
        self.session, self.input, self.size = _session(model, args.threads)

    def score(self, frame) -> tuple[float, str]:
        return class_scores(self.session.run(None, {self.input: downsample(frame, self.size)})[0], self.hot)

    def sample(self, request: dict) -> dict:
        data = read_fragment(request["path"], int(request["init"]), int(request["offset"]), int(request["length"]))
        frame = _decode(data, "mp4")
        score, cls = self.score(frame)
        reply = {"score": round(score, 4), "class": cls}
        if score >= float(request.get("candidate", 0.35)) and request.get("images_dir"):
            folder = request["images_dir"]
            name = f"{request['prefix']}.jpg"
            # One preview per moment is enough: the parent asks only at a moment start.
            if request.get("preview", True) and save_preview(frame, os.path.join(folder, name), content_bounds(frame)):
                reply["image"] = name
            verify_dir = os.path.join(folder, "verify")
            os.makedirs(verify_dir, exist_ok=True)
            verify_path = os.path.join(verify_dir, name)
            # Full letterboxed frame, high quality: the large model reads this later.
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{FRAME}x{FRAME}", "-i", "pipe:0", "-frames:v", "1", "-q:v", "2", verify_path],
                           input=frame.tobytes(), capture_output=True, timeout=30, check=True)
            reply["verify_image"] = verify_path
        return reply

    def verify(self, request: dict) -> dict:
        with open(request["image"], "rb") as handle:
            frame = _decode(handle.read(), "image2pipe")
        score, cls = self.score(frame)
        return {"score": round(score, 4), "class": cls}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analisi NSFW live")
    parser.add_argument("--mode", choices=["sample", "verify"], required=True)
    parser.add_argument("--fast-model", default="")
    parser.add_argument("--verify-model", default="")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--classes", default=",".join(DEFAULT_CLASSES))
    args = parser.parse_args(argv)
    try:
        helper = Helper(args)
    except Exception as exc:
        sys.stdout.write(json.dumps({"fatal": str(exc)[-400:]}) + "\n")
        sys.stdout.flush()
        return 1
    sys.stdout.write(json.dumps({"ready": True}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        request: dict = {}
        try:
            request = json.loads(line)
            reply = helper.sample(request) if args.mode == "sample" else helper.verify(request)
        except Exception as exc:  # one bad fragment must not stop the helper
            reply = {"error": str(exc)[-400:]}
        reply["id"] = request.get("id") if isinstance(request, dict) else None
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
