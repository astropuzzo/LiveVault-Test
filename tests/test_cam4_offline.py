import asyncio
from unittest.mock import Mock

import pytest
import requests

import app.source_providers as providers


def response(status):
    item = Mock(status_code=status)
    if status >= 400:
        item.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    return item


def test_cam4_empty_204_is_offline_without_extraction(monkeypatch):
    http = Mock(return_value=response(204))
    extract = Mock(side_effect=AssertionError("Offline CAM4 must not reach yt-dlp"))
    monkeypatch.setattr(providers.requests, "get", http)
    monkeypatch.setattr(providers, "_extract", extract)
    result = asyncio.run(providers.probe("cam4", "bbsitaaaaa"))
    assert (result.status, result.live, result.recordable, result.error) == ("offline", False, False, "")
    assert http.call_args.args[0] == "https://www.cam4.com/rest/v1.0/profile/bbsitaaaaa/streamInfo"
    extract.assert_not_called()


def test_cam4_public_stream_keeps_quality_and_extractor(monkeypatch):
    monkeypatch.setattr(providers.requests, "get", Mock(return_value=response(200)))
    extract = Mock(return_value={"is_live": True, "title": "Live test"})
    monkeypatch.setattr(providers, "_extract", extract)
    result = asyncio.run(providers.probe("cam4", "testroom", "720p"))
    assert (result.status, result.live, result.recordable) == ("live", True, True)
    extract.assert_called_once_with("https://www.cam4.com/testroom", "720p")


@pytest.mark.parametrize("status", [403, 429, 500])
def test_cam4_http_failure_is_not_offline(monkeypatch, status):
    monkeypatch.setattr(providers.requests, "get", Mock(return_value=response(status)))
    extract = Mock()
    monkeypatch.setattr(providers, "_extract", extract)
    result = asyncio.run(providers.probe("cam4", "testroom"))
    assert result.status == "error"
    assert str(status) in result.error
    extract.assert_not_called()


def test_cam4_network_failure_is_not_offline(monkeypatch):
    monkeypatch.setattr(providers.requests, "get", Mock(side_effect=requests.ConnectionError("connection failed")))
    result = asyncio.run(providers.probe("cam4", "testroom"))
    assert result.status == "error"
    assert "connection failed" in result.error


def test_cam4_malformed_200_stays_an_error(monkeypatch):
    monkeypatch.setattr(providers.requests, "get", Mock(return_value=response(200)))
    monkeypatch.setattr(providers, "_extract", Mock(side_effect=ValueError("Failed to parse JSON")))
    result = asyncio.run(providers.probe("cam4", "testroom"))
    assert result.status == "error"
    assert "Failed to parse JSON" in result.error
