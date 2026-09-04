import asyncio
from types import SimpleNamespace

from app import source_providers as providers
from app import stripchat_capture
from app.stripchat_state import StripchatExpectedState, classify_stripchat_cam


def _stripchat_payload(status="public", *, active=True, available=True, online=True, geo=False):
    return {
        "user": {
            "isGeoBanned": geo,
            "user": {
                "id": 42,
                "status": status,
                "isOnline": online,
                "isDeleted": False,
            },
        },
        "cam": {
            "isCamActive": active,
            "isCamAvailable": available,
            "streamName": "42",
        },
    }


def test_stripchat_idle_is_offline_not_error(monkeypatch):
    monkeypatch.setattr(providers, "stripchat_cam_info", lambda _slug: (42, _stripchat_payload("idle")))

    result = asyncio.run(providers.probe("stripchat", "GwenAir", "best"))

    assert result.live is False
    assert result.status == "offline"
    assert result.recordable is False
    assert result.error == ""


def test_stripchat_public_requires_active_available_camera(monkeypatch):
    monkeypatch.setattr(
        providers,
        "stripchat_cam_info",
        lambda _slug: (42, _stripchat_payload("public", active=False, available=True)),
    )

    result = asyncio.run(providers.probe("stripchat", "example", "best"))

    assert result.live is False
    assert result.status == "offline"
    assert result.recordable is False


def test_stripchat_private_paid_and_geo_states_are_nonrecordable():
    private = classify_stripchat_cam(_stripchat_payload("groupShow"), 42)
    away = classify_stripchat_cam(_stripchat_payload("away"), 42)
    restricted = classify_stripchat_cam(_stripchat_payload("public", geo=True), 42)

    assert (private.status, private.live, private.recordable) == ("private", True, False)
    # Away is intentionally normalized into the app's tip-jar/non-public bucket.
    assert (away.status, away.live, away.recordable) == ("tipjar", True, False)
    assert (restricted.status, restricted.live, restricted.recordable) == ("restricted", True, False)


def test_stripchat_unknown_active_state_fails_closed():
    state = classify_stripchat_cam(_stripchat_payload("newPaidMode"), 42)

    assert state.live is True
    assert state.status == "unknown"
    assert state.recordable is False


def test_stripchat_native_inspection_no_longer_returns_webrtc_error():
    inputs = asyncio.run(providers.resolve_inputs("stripchat", "GwenAir", "best"))
    audit = asyncio.run(providers.audit_inputs(inputs))

    assert len(inputs) == 1
    assert inputs[0].url == "stripchat-native://GwenAir"
    assert audit.has_video is True
    assert audit.has_audio is True
    assert audit.error == ""


def test_native_public_stream_id_treats_idle_as_expected_state():
    try:
        stripchat_capture._public_stream_id(_stripchat_payload("idle"), 42)
    except StripchatExpectedState as exc:
        assert exc.state.status == "offline"
        assert exc.state.raw_status == "idle"
    else:
        raise AssertionError("idle must stop capture as a normal state")


def test_empty_expected_stripchat_session_drops_stitch_marker(tmp_path):
    marker = tmp_path / ".livevault-stitch-session.json"
    marker.write_text("{}", encoding="utf-8")
    args = SimpleNamespace(output_pattern=str(tmp_path / "GwenAir_part%03d.mp4"))

    stripchat_capture._cleanup_empty_session(args)

    assert marker.exists() is False


def test_expected_offline_errors_are_normalized_for_other_extractors(monkeypatch):
    async def fake_probe(_platform, _slug, _quality):
        return providers.ProbeResult(False, "error", error="Channel is not live")

    monkeypatch.setattr(providers._legacy, "probe", fake_probe)
    result = asyncio.run(providers.probe("twitch", "example", "best"))

    assert result.live is False
    assert result.status == "offline"
    assert result.recordable is False
    assert result.error == ""
