from pathlib import Path

ROOT = Path(__file__).parents[1]
SERVICE = (ROOT / 'control-panel/openastro-control.service').read_text(encoding='utf-8')
UPLOAD = (ROOT / 'control-panel/upload_server.py').read_text(encoding='utf-8')
UI = (ROOT / 'control-panel/static/index.html').read_text(encoding='utf-8')
UPLOAD_JS = (ROOT / 'control-panel/static/media-upload.js').read_text(encoding='utf-8')
INSTALL = (ROOT / 'scripts/install-media-center.sh').read_text(encoding='utf-8')
MANAGER = (ROOT / 'scripts/openastro-media-manager.py').read_text(encoding='utf-8')


def test_control_service_runs_upload_enabled_server():
    assert 'upload_server.py' in SERVICE
    assert "'/api/media/upload'" in UPLOAD or '"/api/media/upload"' in UPLOAD
    assert 'X-CSRF-Token' in UPLOAD
    assert '.openastro-upload-' in UPLOAD
    assert 'os.replace(temporary, target)' in UPLOAD


def test_media_import_ui_is_present_and_uses_web_fallback():
    assert 'id="mediaUploadCard"' in UI
    assert '/media-upload.js' in UI
    assert 'XMLHttpRequest' in UPLOAD_JS
    assert '/api/media/upload?' in UPLOAD_JS
    assert '\\\\OPENASTRO\\Media' in UPLOAD_JS


def test_lan_smb_is_read_only_for_guests_and_writable_only_for_astro():
    assert '[Media]' in INSTALL
    assert 'read only = yes' in INSTALL
    assert 'guest ok = yes' in INSTALL
    assert 'write list = astro' in INSTALL
    assert 'interfaces = 127.0.0.1 $LAN_IP' in INSTALL
    assert 'hosts allow = 127.0.0.1 $LAN_NET' in INSTALL


def test_removable_media_mounts_rw_but_excludes_livevault_disks():
    assert '"rw"' in MANAGER
    assert 'nosuid' in MANAGER and 'nodev' in MANAGER and 'noexec' in MANAGER
    assert '5fe2d0f6-b485-44e9-8e26-31fb0d217db2' in MANAGER
    assert '7EBD-F531' in MANAGER
    assert 'guest/DLNA restano read-only' in MANAGER
