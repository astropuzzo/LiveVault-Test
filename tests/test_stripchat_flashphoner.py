from app import stripchat_capture


def test_flashphoner_candidates_use_cam_view_server_and_stream_name():
    state = {
        "cam": {
            "viewServers": {"flashphoner-hls": "hls-17"},
            "streamName": "213430422",
        }
    }

    candidates = stripchat_capture.flashphoner_candidates(state, "213430422")

    assert candidates[0] == (
        "https://b-hls-17.doppiocdn.com/hls/213430422/"
        "master_213430422.m3u8"
    )
    assert any(url.endswith("/213430422/213430422.m3u8") for url in candidates)


def test_flashphoner_master_resolves_selected_media_playlist():
    state = {"cam": {"viewServers": {"flashphoner-hls": "hls-17"}}}
    master_url = "https://b-hls-17.doppiocdn.com/hls/42/master_42.m3u8"
    child_url = "https://b-hls-17.doppiocdn.com/hls/42/source.m3u8"

    class Response:
        def __init__(self, status_code, text):
            self.status_code = status_code
            self.text = text

    class Session:
        def get(self, url, **_kwargs):
            if url == master_url:
                return Response(
                    200,
                    "#EXTM3U\n"
                    "#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080\n"
                    "source.m3u8\n",
                )
            if url == child_url:
                return Response(200, "#EXTM3U\n#EXT-X-TARGETDURATION:4\nsegment0001.ts\n")
            return Response(404, "")

    resolved = stripchat_capture.resolve_flashphoner_input(
        Session(), state, "42", "best", {"Referer": "https://stripchat.com/example"}
    )

    assert resolved == child_url


def test_flashphoner_ffmpeg_is_stream_copy_only():
    cmd = stripchat_capture.build_flashphoner_ffmpeg_command(
        "https://b-hls-17.doppiocdn.com/hls/42/source.m3u8",
        "/tmp/capture_part%03d.mp4",
        segment_seconds=1200,
        max_bytes=1900 * 1024 * 1024,
        container="mp4",
        headers={"User-Agent": "UA", "Referer": "https://stripchat.com/example"},
    )
    joined = " ".join(cmd)

    assert "-c copy" in joined
    assert "libx264" not in joined
    assert "aac" not in joined
    assert "aresample" not in joined
    assert "-f segment" in joined
    assert "-segment_start_number 1" in joined
    assert "frag_keyframe" in joined
    assert "empty_moov" in joined


def test_package_entrypoint_shadows_old_module_but_reexports_mouflon_helpers():
    assert stripchat_capture.__file__.endswith("app/stripchat_capture/__init__.py")
    assert callable(stripchat_capture.decode_v1_name)
    assert callable(stripchat_capture.decode_v2_url)
