from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "control-panel"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))
torrent = importlib.import_module("torrent_manager")


class FakeRPC:
    def __init__(self):
        self.calls = []

    def call(self, method, arguments=None, **kwargs):
        arguments = arguments or {}
        self.calls.append((method, arguments))
        if method == "torrent-get" and arguments.get("fields") == ["id", "status"]:
            return {"torrents": [{"id": 7, "status": 0}]}
        return {}


def test_1337x_search_parser_extracts_visible_fields():
    html = """
    <table class="table-list"><tbody>
      <tr>
        <td class="coll-1 name"><a href="/cat/Movies/1/">Movies</a><a href="/torrent/123/Test-Release/">Test Release</a></td>
        <td class="coll-2 seeds">412</td>
        <td class="coll-3 leeches">17</td>
        <td class="coll-4 size mob-uploader">1.45 GB</td>
        <td class="coll-date">2h</td>
        <td class="coll-5 uploader">ExampleUploader</td>
      </tr>
    </tbody></table>
    """
    parser = torrent._SearchParser("https://1337x.to")
    parser.feed(html)
    assert parser.rows == [{
        "detail_url": "https://1337x.to/torrent/123/Test-Release/",
        "name": "Test Release",
        "seeders": 412,
        "leechers": 17,
        "size": "1.45 GB",
        "age": "2h",
        "uploader": "ExampleUploader",
    }]



def test_search_uses_native_1337x_sort(monkeypatch):
    pages = []
    page_html = """
    <table class="table-list"><tbody>
      <tr><td class="coll-1"><a href="/torrent/1/A/">A</a></td><td class="coll-2">4</td><td class="coll-3">1</td><td class="coll-4">1 GB</td></tr>
      <tr><td class="coll-1"><a href="/torrent/2/B/">B</a></td><td class="coll-2">40</td><td class="coll-3">2</td><td class="coll-4">2 GB</td></tr>
    </tbody></table>
    """
    def fake_fetch(url, timeout=12):
        pages.append(url)
        return page_html
    monkeypatch.setattr(torrent, '_fetch_html', fake_fetch)
    result = torrent.search('1337x', 'linux', 2, 'time', 'asc')
    assert pages == ['https://1337x.to/sort-search/linux/time/asc/2/']
    assert result['sort'] == 'time'
    assert result['order'] == 'asc'
    assert [item['name'] for item in result['results']] == ['A', 'B']


def test_search_cache_reuses_same_provider_result(monkeypatch):
    calls = []
    page_html = """
    <table class="table-list"><tbody>
      <tr><td class="coll-1"><a href="/torrent/1/A/">A</a></td><td class="coll-2">4</td><td class="coll-3">1</td><td class="coll-4">1 GB</td></tr>
    </tbody></table>
    """
    torrent._SEARCH_CACHE.clear()
    monkeypatch.setattr(torrent, '_fetch_html', lambda url, timeout=12: calls.append(url) or page_html)
    first = torrent.search('1337x', 'cache-test', 1, 'seeders', 'desc')
    second = torrent.search('1337x', 'cache-test', 1, 'seeders', 'desc')
    assert len(calls) == 1
    assert first['cached'] is False
    assert second['cached'] is True
    assert second['results'][0]['name'] == 'A'
    torrent._SEARCH_CACHE.clear()


def test_search_rejects_unknown_sort(monkeypatch):
    monkeypatch.setattr(torrent, '_fetch_html', lambda url, timeout=12: '')
    try:
        torrent.search('1337x', 'linux', 1, 'name', 'desc')
    except ValueError as exc:
        assert 'Ordinamento' in str(exc)
    else:
        raise AssertionError('unsupported sort must be rejected')

def test_browser_solver_has_no_livevault_recorder_gate(monkeypatch):
    class Headers:
        @staticmethod
        def get_content_charset():
            return "utf-8"

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        @staticmethod
        def read(limit):
            return b"<html>ok</html>"

    class Opener:
        @staticmethod
        def open(request, timeout):
            assert request.full_url.startswith("http://127.0.0.1:9092/fetch?")
            assert timeout == 90
            return Response()

    monkeypatch.setattr(torrent, "build_opener", lambda *args: Opener())
    assert torrent._fetch_html_solver("https://1337x.to/search/ubuntu/1/") == "<html>ok</html>"


def test_browser_solver_retries_transient_startup(monkeypatch):
    calls = {"count": 0}

    class Headers:
        @staticmethod
        def get_content_charset():
            return "utf-8"

    class Response:
        headers = Headers()
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        @staticmethod
        def read(limit): return b"<html>ready</html>"

    class Opener:
        @staticmethod
        def open(request, timeout):
            calls["count"] += 1
            if calls["count"] < 3:
                raise torrent.URLError(ConnectionRefusedError(111, "refused"))
            return Response()

    monkeypatch.setattr(torrent, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(torrent.time, "sleep", lambda value: None)
    assert torrent._fetch_html_solver("https://1337x.to/search/ubuntu/1/") == "<html>ready</html>"
    assert calls["count"] == 3


def test_solver_endpoint_must_be_loopback(monkeypatch):
    monkeypatch.setattr(torrent, 'SEARCH_SOLVER', 'https://example.org/fetch')
    try:
        torrent._solver_endpoint('https://1337x.to/search/ubuntu/1/')
    except torrent.TorrentError as exc:
        assert 'loopback' in str(exc)
    else:
        raise AssertionError('non-loopback solver must be rejected')


def test_1337x_detail_parser_extracts_magnet():
    parser = torrent._MagnetParser()
    parser.feed('<a href="magnet:?xt=urn:btih:ABCDEF&amp;dn=Example">Magnet</a>')
    assert parser.magnet == "magnet:?xt=urn:btih:ABCDEF&dn=Example"


def test_manual_remove_deletes_only_staging_data(monkeypatch):
    rpc = FakeRPC()
    monkeypatch.setattr(torrent, "RPC", rpc)
    result = torrent.action(42, "remove")
    assert result["ok"] is True
    assert rpc.calls[-1] == ("torrent-remove", {"ids": [42], "delete-local-data": True})


def test_completed_torrent_moves_to_media_then_removes_job_without_deleting_data(tmp_path, monkeypatch):
    share = tmp_path / "share"
    staging = share / ".openastro-torrents" / "complete"
    imports = share / "Media" / "Downloads"
    staging.mkdir(parents=True)
    imports.mkdir(parents=True)
    source = staging / "Example.mkv"
    source.write_bytes(b"media-payload")

    monkeypatch.setattr(torrent, "SHARE_ROOT", share)
    monkeypatch.setattr(torrent, "STAGING_ROOT", share / ".openastro-torrents")
    monkeypatch.setattr(torrent, "COMPLETE_ROOT", staging)
    monkeypatch.setattr(torrent, "INCOMPLETE_ROOT", share / ".openastro-torrents" / "incomplete")
    monkeypatch.setattr(torrent, "MEDIA_ROOT", share / "Media")
    monkeypatch.setattr(torrent, "IMPORT_ROOT", imports)
    monkeypatch.setattr(torrent, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(torrent, "IMPORT_STATE", tmp_path / "state" / "torrent-imports.json")
    monkeypatch.setattr(torrent.os.path, "ismount", lambda path: Path(path) == share)
    monkeypatch.setattr(torrent, "_refresh_media_library", lambda: None)

    rpc = FakeRPC()
    monkeypatch.setattr(torrent, "RPC", rpc)

    item = {
        "id": 7,
        "name": "Example.mkv",
        "hashString": "HASH123",
        "percentDone": 1.0,
        "totalSize": len(b"media-payload"),
    }
    assert torrent._import_one(item) is True
    assert not source.exists()
    target = imports / "Example.mkv"
    assert target.read_bytes() == b"media-payload"
    assert ("torrent-stop", {"ids": [7]}) in rpc.calls
    assert ("torrent-remove", {"ids": [7], "delete-local-data": False}) in rpc.calls
    receipt = torrent._load_receipts()["HASH123"]
    assert receipt["path"] == "Downloads/Example.mkv"


def test_clear_recent_imports_hides_history_but_keeps_dedup_receipts(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(torrent, "STATE_ROOT", state)
    monkeypatch.setattr(torrent, "IMPORT_STATE", state / "torrent-imports.json")
    torrent._save_receipts({
        "HASH1": {"name": "One.mkv", "path": "Downloads/One.mkv", "ts": 10},
        "HASH2": {"name": "Two.mkv", "path": "Downloads/Two.mkv", "ts": 20},
    })
    assert len(torrent._recent_imports()) == 2
    result = torrent.clear_recent_imports()
    assert result == {"ok": True, "hidden": 2}
    assert torrent._recent_imports() == []
    receipts = torrent._load_receipts()
    assert set(receipts) == {"HASH1", "HASH2"}
    assert all(row["history_hidden"] is True for row in receipts.values())


def test_install_and_ui_contracts():
    installer = (ROOT / "scripts" / "install-torrent-manager.sh").read_text(encoding="utf-8")
    handoff = (ROOT / "scripts" / "nvme-handoff.py").read_text(encoding="utf-8")
    upload = (ROOT / "control-panel" / "upload_server.py").read_text(encoding="utf-8")
    ui = (ROOT / "control-panel" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "control-panel" / "static" / "torrent-manager.js").read_text(encoding="utf-8")
    solver = (ROOT / "control-panel" / "torrent_search_service.py").read_text(encoding="utf-8")

    assert "transmission-daemon" in installer
    assert "scrapling[fetchers]==0.4.15" in installer
    assert "openastro-torrent-search.service" in installer
    assert "127.0.0.1" in solver and 'PORT = int(os.environ.get("OPENASTRO_TORRENT_SEARCH_PORT", "9092"))' in solver
    assert 'parsed.scheme != "https" or host not in ALLOWED_HOSTS' in solver
    assert 'parsed.path.startswith("/torrent/")' in solver
    assert "_close_session_locked()" in solver
    assert "IDLE_SECONDS" in solver
    assert 'user_data_dir=PROFILE_DIR' not in solver
    assert 'class SearchHTTPServer(HTTPServer)' in solver
    assert 'ThreadingHTTPServer' not in solver
    assert 'def service_actions(self)' in solver
    assert 'OPENASTRO_TORRENT_SEARCH_IDLE=600' in installer
    assert '"rpc-bind-address": "127.0.0.1"' in installer
    assert "/share/.openastro-torrents/complete" in installer
    assert "/share/Media/Downloads" in installer
    assert "openastro-torrent.service" in handoff
    assert handoff.index("set_service('openastro-torrent.service', 'stop')") < handoff.index("run('umount', '/share')")
    assert "/api/torrents/search" in upload
    assert "torrentSearchSort" in ui and "torrentSearchOrder" in ui
    assert "sort:q('#torrentSearchSort')" in js
    assert "lastGoodStatus" in js and "ultimo stato noto" in js
    assert "Transmission riconnessione" in js and "Superamento protezione 1337x" in js
    assert "/api/torrents/add" in upload
    assert "/api/torrents/action" in upload
    assert 'id="torrentPanel"' in ui
    assert '<option value="1337x" selected>1337x</option>' in ui
    assert "delete-local-data" not in js  # browser never decides storage deletion semantics
