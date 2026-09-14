from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'control-panel' / 'media_center.py'
spec = importlib.util.spec_from_file_location('media_center_nvme_share_test', MODULE)
media = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(media)


def test_nvme_share_is_exposed_as_fixed_media_device(monkeypatch):
    monkeypatch.setattr(media, '_DISCOVERY_CACHE', (0.0, []))
    monkeypatch.setattr(
        media,
        '_run',
        lambda args, timeout=8: subprocess.CompletedProcess(args, 0, '[]', ''),
    )
    rows = media._discover(force=True)
    share = next(item for item in rows if item['uuid'] == media.SHARE_MEDIA_UUID)
    assert share['mountpoint'] == '/share/Media'
    assert share['media_role'] == 'nvme-share'
    assert share['persistent'] is True
    assert share['managed'] is False
    assert share['ejectable'] is False
    assert share['smb_path'] == r'\\OPENASTRO\NVMeMedia'


def test_fixed_share_mount_state_follows_parent_share(monkeypatch):
    item = media._share_media_device()
    monkeypatch.setattr(media.os.path, 'ismount', lambda path: str(path) == '/share')
    original_is_dir = Path.is_dir
    monkeypatch.setattr(Path, 'is_dir', lambda self: str(self) == '/share/Media' or original_is_dir(self))
    assert media._item_mounted(item) is True
    monkeypatch.setattr(media.os.path, 'ismount', lambda path: False)
    assert media._item_mounted(item) is False


def test_install_contract_exposes_only_dedicated_share_media_directory():
    install = (ROOT / 'scripts' / 'install-media-center.sh').read_text(encoding='utf-8')
    manager = (ROOT / 'scripts' / 'openastro-media-manager.py').read_text(encoding='utf-8')
    assert 'install -d -o astro -g astro -m 0755 /share/Media' in install
    assert '[NVMeMedia]' in install
    assert 'path = /share/Media' in install
    assert 'media_dir=/share/Media' in install
    assert '7EBD-F531' in manager  # still excluded from removable-media hotplug management


def test_ui_never_offers_independent_eject_for_fixed_nvme_media():
    ui = (ROOT / 'control-panel' / 'static' / 'app.js').read_text(encoding='utf-8')
    upload_ui = (ROOT / 'control-panel' / 'static' / 'media-upload.js').read_text(encoding='utf-8')
    assert 'device.ejectable === false' in ui
    assert 'selected.smb_path || media.smb_path' in ui
    assert "selectedDevice()?.smb_path || SMB_PATH" in upload_ui


def test_nvme_handoff_quiesces_share_media_access_before_unmount():
    handoff = (ROOT / 'scripts' / 'nvme-handoff.py').read_text(encoding='utf-8')
    close = handoff.index("['smbcontrol', 'smbd', 'close-share', 'NVMeMedia']")
    stop_dlna = handoff.index("['systemctl', 'stop', 'minidlna.service']")
    unmount = handoff.index("run('umount', '/share')", stop_dlna)
    assert close < unmount
    assert stop_dlna < unmount
    assert "Path('/share/Media').mkdir(parents=True, exist_ok=True)" in handoff


def test_media_selection_discards_stale_async_responses():
    ui = (ROOT / 'control-panel' / 'static' / 'app.js').read_text(encoding='utf-8')
    assert 'let mediaDirectoryRequest = 0;' in ui
    assert 'let mediaLibraryRequest = 0;' in ui
    assert 'uuid !== mediaUuid || request !== mediaLibraryRequest' in ui
    assert 'uuid !== mediaUuid || request !== mediaDirectoryRequest' in ui
    assert 'mediaThumbUrl(item.path, libraryUuid)' in ui
    assert 'openMediaPlayer(button.dataset.mediaRecent, button.dataset.mediaName, button.dataset.mediaCategory, libraryUuid)' in ui


def test_media_file_cards_are_owned_by_selected_device():
    ui = (ROOT / 'control-panel' / 'static' / 'app.js').read_text(encoding='utf-8')
    assert "let mediaItemsUuid = '';" in ui
    assert 'mediaItemsUuid === mediaUuid ? mediaFilteredItems() : []' in ui
    assert 'mediaItemsUuid = uuid;' in ui
    assert "mediaItems=[]; mediaItemsUuid=mediaUuid; renderMediaLibrary(null);" in ui
