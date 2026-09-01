import asyncio
from datetime import timezone

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


def test_probe_does_not_hide_biocontext_failure_as_never_live(monkeypatch):
    monkeypatch.setattr(
        providers,
        "_extract",
        lambda *_args, **_kwargs: {"is_live": False, "live_status": "offline", "title": ""},
    )

    def broken_context(_slug):
        raise RuntimeError("HTTP 404 from biocontext")

    monkeypatch.setattr(providers, "_fetch_biocontext", broken_context)
    result = asyncio.run(providers.probe("chaturbate", "example", "best"))

    assert result.live is False
    assert result.status == "error"
    assert "biocontext" in result.error.lower()
    assert "404" in result.error
