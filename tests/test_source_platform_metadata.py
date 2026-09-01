import asyncio
from datetime import datetime, timezone

from app import source_providers as providers


def test_parse_chaturbate_last_broadcast_iso_utc():
    parsed = providers._parse_last_broadcast("2026-09-01T17:20:31.123456Z")
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-09-01T17:20:31.123456+00:00"
    assert providers._parse_last_broadcast(-1) is None
    assert providers._parse_last_broadcast("-1") is None


def test_parse_chaturbate_naive_last_broadcast_as_pacific():
    parsed = providers._parse_last_broadcast("2026-09-01T17:20:31.123456")
    assert parsed is not None
    assert parsed.isoformat() == "2026-09-02T00:20:31.123456+00:00"


def test_parse_public_profile_relative_last_broadcast():
    now = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
    parsed = providers._extract_last_broadcast_from_profile_html(
        "<div>Last Broadcast:</div><div>20 hours ago</div><div>Languages:</div><div>English</div>",
        now=now,
    )
    assert parsed is not None
    assert parsed.isoformat() == "2026-09-01T00:00:00+00:00"


def test_parse_public_profile_embedded_iso_preferred():
    parsed = providers._extract_last_broadcast_from_profile_html(
        '<script>window.bio={"last_broadcast":"2026-09-01T17:20:31Z","time_since_last_broadcast":"2 hours ago"}</script>'
    )
    assert parsed is not None
    assert parsed.isoformat() == "2026-09-01T17:20:31+00:00"


def test_biocontext_uses_required_profile_headers(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "room_status": "offline",
                "last_broadcast": "2026-09-01T17:20:31",
            }

    def fake_get(url, headers, timeout=15):
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(providers, "_browser_get", fake_get)
    payload = providers._fetch_biocontext("example")

    assert payload["last_broadcast"] == "2026-09-01T17:20:31"
    assert captured["url"] == "https://chaturbate.com/api/biocontext/example/"
    assert captured["headers"]["Referer"] == "https://chaturbate.com/p/example/"
    assert captured["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert "agreeterms=1" in captured["headers"]["Cookie"]


def test_probe_keeps_platform_last_broadcast_while_offline(monkeypatch):
    def offline_extract(*_args, **_kwargs):
        raise RuntimeError("Room is currently offline")

    monkeypatch.setattr(providers, "_extract", offline_extract)
    monkeypatch.setattr(
        providers,
        "_fetch_biocontext",
        lambda _slug: {
            "room_status": "offline",
            "last_broadcast": "2026-09-01T17:20:31Z",
        },
    )

    result = asyncio.run(providers.probe("chaturbate", "example", "best"))
    assert result.live is False
    assert result.status == "offline"
    assert result.last_broadcast is not None
    assert result.last_broadcast.isoformat() == "2026-09-01T17:20:31+00:00"


def test_biocontext_public_room_can_confirm_live(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_extract",
        lambda *_args, **_kwargs: {"is_live": False, "live_status": "", "title": ""},
    )
    monkeypatch.setattr(
        providers,
        "_fetch_biocontext",
        lambda _slug: {
            "room_status": "public",
            "room_title": "Live now",
            "last_broadcast": "2026-09-01T18:00:00+00:00",
        },
    )

    result = asyncio.run(providers.probe("chaturbate", "example", "best"))
    assert result.live is True
    assert result.status == "live"
    assert result.last_broadcast is not None


def test_probe_falls_back_to_public_profile_when_biocontext_is_401(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_extract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Room is currently offline")),
    )

    def gated_context(_slug):
        raise RuntimeError("HTTP 401 from biocontext")

    monkeypatch.setattr(providers, "_fetch_biocontext", gated_context)
    monkeypatch.setattr(
        providers,
        "_fetch_profile_last_broadcast",
        lambda _slug: datetime(2026, 9, 1, 17, 20, 31, tzinfo=timezone.utc),
    )

    result = asyncio.run(providers.probe("chaturbate", "example", "best"))
    assert result.live is False
    assert result.status == "offline"
    assert result.error == ""
    assert result.last_broadcast is not None
    assert result.last_broadcast.isoformat() == "2026-09-01T17:20:31+00:00"


def test_probe_reports_error_only_when_both_metadata_sources_fail(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_extract",
        lambda *_args, **_kwargs: {"is_live": False, "live_status": "offline", "title": ""},
    )

    def broken_context(_slug):
        raise RuntimeError("HTTP 401 from biocontext")

    def broken_profile(_slug):
        raise RuntimeError("HTTP 403 from public profile")

    monkeypatch.setattr(providers, "_fetch_biocontext", broken_context)
    monkeypatch.setattr(providers, "_fetch_profile_last_broadcast", broken_profile)
    result = asyncio.run(providers.probe("chaturbate", "example", "best"))

    assert result.live is False
    assert result.status == "error"
    assert "biocontext" in result.error.lower()
    assert "401" in result.error
    assert "public profile" in result.error.lower()
    assert "403" in result.error
