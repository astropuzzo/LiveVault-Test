from pathlib import Path
from app.recorder import build_ffmpeg_command
from app.source_providers import ResolvedInput

def test_ffmpeg_single_input_mapping():
    cmd=build_ffmpeg_command([ResolvedInput("https://example.test/master.m3u8",{},"media")],Path("out_%03d.mkv")); j=" ".join(cmd)
    assert "-map 0:v:0?" in j and "-map 0:a:0?" in j and "-c copy" in j and "-f segment" in j

def test_ffmpeg_separate_audio_video_mapping():
    inputs=[ResolvedInput("https://example.test/video.m3u8",{},"video"),ResolvedInput("https://example.test/audio.m3u8",{},"audio")]
    j=" ".join(build_ffmpeg_command(inputs,Path("out_%03d.mkv")))
    assert "-map 0:v:0?" in j and "-map 1:a:0?" in j
