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



def test_search_falls_back_when_sorted_search_is_empty(monkeypatch):
    pages = []
    sorted_html = '<table class="table-list"><tbody></tbody></table>'
    fallback_html = """
    <table class="table-list"><tbody>
      <tr><td class="coll-1"><a href="/torrent/1/A/">A</a></td><td class="coll-2">4</td><td class="coll-3">1</td><td class="coll-4">1 GB</td></tr>
      <tr><td class="coll-1"><a href="/torrent/2/B/">B</a></td><td class="coll-2">40</td><td class="coll-3">2</td><td class="coll-4">2 GB</td></tr>
    </tbody></table>
    """
    def fake_fetch(url, timeout=12):
        pages.append(url)
        return sorted_html if '/sort-search/' in url else fallback_html
    monkeypatch.setattr(torrent, '_fetch_html', fake_fetch)
    result = torrent.search('1337x', 'linux', 1)
    assert any('/sort-search/' in url for url in pages)
    assert any('/search/linux/1/' in url for url in pages)
    assert [item['name'] for item in result['results']] == ['B', 'A']

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


def test_install_and_ui_contracts():
    installer = (ROOT / "scripts" / "install-torrent-manager.sh").read_text(encoding="utf-8")
    handoff = (ROOT / "scripts" / "nvme-handoff.py").read_text(encoding="utf-8")
    upload = (ROOT / "control-panel" / "upload_server.py").read_text(encoding="utf-8")
    ui = (ROOT / "control-panel" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "control-panel" / "static" / "torrent-manager.js").read_text(encoding="utf-8")

    assert "transmission-daemon" in installer
    assert '"rpc-bind-address": "127.0.0.1"' in installer
    assert "/share/.openastro-torrents/complete" in installer
    assert "/share/Media/Downloads" in installer
    assert "openastro-torrent.service" in handoff
    assert handoff.index("set_service('openastro-torrent.service', 'stop')") < handoff.index("run('umount', '/share')")
    assert "/api/torrents/search" in upload
    assert "/api/torrents/add" in upload
    assert "/api/torrents/action" in upload
    assert 'id="torrentPanel"' in ui
    assert '<option value="1337x" selected>1337x</option>' in ui
    assert "delete-local-data" not in js  # browser never decides storage deletion semantics
