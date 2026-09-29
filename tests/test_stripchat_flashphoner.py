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
        "master/213430422_auto.m3u8"
    )
    assert any(url.endswith("/213430422/213430422.m3u8") for url in candidates)
    assert any(url.endswith("/213430422/master_213430422.m3u8") for url in candidates)


def test_provider_edge_hosts_precede_static_fallback(monkeypatch):
    from app import source_providers

    monkeypatch.setattr(source_providers, "_stripchat_snapshot", lambda _slug: {
        "configV3": {"static": {"features": {"hlsFallback": {
            "fallbackDomains": ["doppiocdn.media", "doppiocdn.com"]
        }}}}
    })
    hosts = stripchat_capture._provider_edge_hosts("example")

    assert hosts[0] == "edge-hls.doppiocdn.media"
    assert hosts.count("edge-hls.doppiocdn.com") == 1
    assert "edge-hls.doppiocdn.live" in hosts


def test_capture_passes_provider_hosts_to_mouflon_fallback(monkeypatch):
    from types import SimpleNamespace

    state = {"cam": {"streamName": "42"}}
    monkeypatch.setattr(stripchat_capture, "make_session", lambda: object())
    monkeypatch.setattr(stripchat_capture._legacy, "get_cam_state", lambda *_args: (42, state))
    monkeypatch.setattr(stripchat_capture, "_public_stream_id", lambda *_args: "42")
    monkeypatch.setattr(stripchat_capture, "resolve_flashphoner_input", lambda *_args: None)
    monkeypatch.setattr(stripchat_capture, "_ensure_builtin_mouflon_keys", lambda: None)
    monkeypatch.setattr(stripchat_capture, "_provider_edge_hosts", lambda _slug: ("edge-hls.doppiocdn.media",))
    seen = []
    monkeypatch.setattr(stripchat_capture._legacy, "capture", lambda args: seen.append(args.hls_edge_hosts))

    stripchat_capture.capture(SimpleNamespace(slug="example", quality="best"))

    assert seen == [("edge-hls.doppiocdn.media",)]


def test_edge_403_after_room_turns_private_is_normal_transition(monkeypatch):
    from types import SimpleNamespace
    import pytest
    from app.stripchat_state import StripchatCamState, StripchatExpectedState

    monkeypatch.setattr(stripchat_capture, "make_session", lambda: object())
    monkeypatch.setattr(stripchat_capture._legacy, "get_cam_state", lambda *_args: (42, {"cam": {}}))
    monkeypatch.setattr(stripchat_capture, "_public_stream_id", lambda *_args: "42")
    monkeypatch.setattr(stripchat_capture, "resolve_flashphoner_input", lambda *_args: None)
    monkeypatch.setattr(stripchat_capture, "_ensure_builtin_mouflon_keys", lambda: None)
    monkeypatch.setattr(stripchat_capture, "_provider_edge_hosts", lambda _slug: ())
    monkeypatch.setattr(stripchat_capture._legacy, "capture", lambda _args: (_ for _ in ()).throw(
        RuntimeError("Stripchat HLS master unavailable: HTTP Error 403")))
    monkeypatch.setattr(stripchat_capture, "_current_expected_state", lambda _slug: StripchatExpectedState(
        StripchatCamState("private", "private", True, False, "42")))

    with pytest.raises(StripchatExpectedState):
        stripchat_capture.capture(SimpleNamespace(slug="example", quality="best"))


def test_flashphoner_master_resolves_selected_media_playlist():
    state = {"cam": {"viewServers": {"flashphoner-hls": "hls-17"}}}
    master_url = "https://b-hls-17.doppiocdn.com/hls/42/master/42_auto.m3u8"
    child_url = "https://b-hls-17.doppiocdn.com/hls/42/master/source.m3u8"

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


def test_current_mouflon_master_has_bootstrap_keys():
    advertised = {
        "1Dzcc6OjP73LKbtI",
        "7uUnbD0jMCB9GH32",
        "Fq6m2TO2ZeBkRPm9",
        "GrRncsoByZmsiT6L",
        "N2oLovTIXb0o28Uj",
        "NTK9aqcLmNFMWrpQ",
        "OLzu7QlySkG2fVRn",
        "Ohi7eTRBpkAuML0l",
        "Ook7quaiNgiyuhai",
    }
    assert advertised <= set(stripchat_capture.BUILTIN_MOUFLON_KEYS)
    assert stripchat_capture.BUILTIN_MOUFLON_KEYS["1Dzcc6OjP73LKbtI"] == "Y64UVwX5RrIWnOLp"


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
    assert stripchat_capture.__file__.replace("\\", "/").endswith("app/stripchat_capture/__init__.py")
    assert callable(stripchat_capture.decode_v1_name)
    assert callable(stripchat_capture.decode_v2_url)
