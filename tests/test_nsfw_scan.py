import io
import json
import shutil
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from app.nsfw_scan import LABELS, Moment, Verdict, judge, merge_moments, overall


def test_judge_needs_large_model_to_confirm():
    assert judge(10, (0.9, "FEMALE_BREAST_EXPOSED"), (0.7, "FEMALE_BREAST_EXPOSED"), 0.6).label == "nsfw"
    # Leggings case: small model fairly sure, large model not → only "review".
    assert judge(10, (0.61, "FEMALE_BREAST_EXPOSED"), (0.2, ""), 0.6).label == "review"
    assert judge(10, (0.5, "FEMALE_BREAST_EXPOSED"), (0.2, ""), 0.6).label == ""
    # Without a verification model nothing is ever final.
    assert judge(10, (0.95, "ANUS_EXPOSED"), None, 0.6).label == "review"


def test_merge_moments_joins_nearby_hits_and_keeps_worst_label():
    verdicts = [Verdict(100, "review", 0.62, "BUTTOCKS_EXPOSED"), Verdict(105, "nsfw", 0.8, "FEMALE_BREAST_EXPOSED"),
                Verdict(110, "review", 0.9, "FEMALE_GENITALIA_EXPOSED"), Verdict(300, "review", 0.65, "ANUS_EXPOSED"),
                Verdict(200, "", 0.1, "")]
    moments = merge_moments(verdicts, step=5)
    assert [(m.start, m.end, m.label) for m in moments] == [(100, 115, "nsfw"), (300, 305, "review")]
    assert moments[0].score == 0.8
    # Everything seen in the moment is kept, most explicit first.
    assert moments[0].cls == "FEMALE_GENITALIA_EXPOSED+FEMALE_BREAST_EXPOSED+BUTTOCKS_EXPOSED"
    assert overall(moments) == "nsfw"
    assert overall([Moment(1, 2, "review", 0.7, "A")]) == "review"
    assert overall([]) == "safe"


def test_labels_match_nudenet_34_order():
    assert len(LABELS) == 18
    assert LABELS[3] == "FEMALE_BREAST_EXPOSED" and LABELS[14] == "MALE_GENITALIA_EXPOSED"


def _fake_model(path: Path, size: int) -> None:
    """ONNX stand-in for NudeNet: FEMALE_BREAST_EXPOSED score = mean red of the frame."""
    import numpy as np
    from onnx import TensorProto, helper, numpy_helper, save
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["m"], keepdims=1, axes=[2, 3]),
        helper.make_node("Slice", ["m", "s0", "s1", "ax"], ["r"]),
        helper.make_node("Reshape", ["r", "shp"], ["r3"]),
        helper.make_node("Concat", ["z7", "r3", "z14"], ["output0"], axis=1),
    ]
    inits = [numpy_helper.from_array(np.array([0], np.int64), "s0"), numpy_helper.from_array(np.array([1], np.int64), "s1"),
             numpy_helper.from_array(np.array([1], np.int64), "ax"), numpy_helper.from_array(np.array([1, 1, 1], np.int64), "shp"),
             numpy_helper.from_array(np.zeros((1, 7, 1), np.float32), "z7"),
             numpy_helper.from_array(np.zeros((1, 14, 1), np.float32), "z14")]
    graph = helper.make_graph(nodes, "fake", [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, size, size])],
                              [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 22, 1])], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    save(model, str(path))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_scanner_uses_real_timestamps_across_capture_gaps(tmp_path: Path):
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    from app.nsfw_scan import Scanner
    plain, gap = tmp_path / "plain.mp4", tmp_path / "gap.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=320x180:r=10:d=60", "-f", "lavfi",
                    "-i", "color=red:s=320x180:r=10:d=10", "-filter_complex", "[0][1]overlay=enable='between(t,20,29)'",
                    "-c:v", "libx264", "-g", "10", "-movflags", "+frag_keyframe+empty_moov", str(plain)], check=True)
    # Shift everything after 40 s by 100 s: a capture gap like real reconnects.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(plain), "-vf", "setpts='PTS+if(gte(T,40),100/TB,0)'",
                    "-fps_mode", "passthrough", "-c:v", "libx264", "-g", "10", "-movflags", "+frag_keyframe+empty_moov",
                    str(gap)], check=True)
    _fake_model(tmp_path / "fast.onnx", 320)
    _fake_model(tmp_path / "big.onnx", 640)
    out = io.StringIO()
    with redirect_stdout(out):
        result = Scanner(str(tmp_path / "fast.onnx"), str(tmp_path / "big.onnx"), step=5, threshold=0.5,
                         images_dir=str(tmp_path), image_prefix="42").run(str(gap))
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert result["status"] == "nsfw"
    assert result["frames"] == 12  # 0–40 s and 140–160 s; the gap is not sampled
    assert [(m["start"], m["label"]) for m in result["moments"]] == [(20.0, "nsfw")]
    assert any(e["type"] == "progress" for e in events) and events[-1]["type"] == "done"
    # Preview saved from the analysed frame, one per moment, no re-seek of the file.
    shots = [e["image"] for e in events if e["type"] == "moment" and e.get("image")]
    assert shots == ["42-20.jpg"] and (tmp_path / "42-20.jpg").stat().st_size > 0
    with redirect_stdout(io.StringIO()):
        resumed = Scanner(str(tmp_path / "fast.onnx"), "", step=5, threshold=0.5).run(str(gap), start_at=100)
    assert resumed["frames"] == 4 and resumed["status"] == "safe"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_fully_explicit_video_verifies_only_periodically(tmp_path: Path):
    """A video that is NSFW from start to end must not run the slow model on every frame."""
    pytest.importorskip("onnxruntime")
    pytest.importorskip("onnx")
    from app.nsfw_scan import Scanner
    video = tmp_path / "red.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=320x180:r=10:d=600",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", str(video)], check=True)
    _fake_model(tmp_path / "fast.onnx", 320)
    _fake_model(tmp_path / "big.onnx", 640)
    with redirect_stdout(io.StringIO()):
        result = Scanner(str(tmp_path / "fast.onnx"), str(tmp_path / "big.onnx"), step=5, threshold=0.5,
                         reverify_seconds=60).run(str(video))
    assert result["frames"] == 120
    assert result["verified"] <= 12  # one confirmation per minute, not 120
    assert [(m["start"], m["end"], m["label"]) for m in result["moments"]] == [(0.0, 600.0, "nsfw")]


def test_class_scores_reports_every_clear_class_most_explicit_first():
    import numpy as np
    from app.nsfw_scan import class_scores
    output = np.zeros((1, 22, 3), np.float32)
    output[0, 4 + 3, 0] = 0.85   # FEMALE_BREAST_EXPOSED, the strongest
    output[0, 4 + 4, 1] = 0.55   # FEMALE_GENITALIA_EXPOSED, weaker but clear
    output[0, 4 + 2, 2] = 0.20   # BUTTOCKS_EXPOSED, noise
    score, cls = class_scores(output, [2, 3, 4])
    assert score == pytest.approx(0.85)
    assert cls == "FEMALE_GENITALIA_EXPOSED+FEMALE_BREAST_EXPOSED"
