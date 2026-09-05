from pathlib import Path

from app.recovery_policy import finalizing_error_is_unrecoverable, recovery_quarantine_path


def test_irrecoverable_finalizing_mp4_is_classified_and_quarantined(tmp_path: Path):
    assert finalizing_error_is_unrecoverable('[mov,mp4] moov atom not found')
    assert finalizing_error_is_unrecoverable('Invalid data found when processing input')
    assert finalizing_error_is_unrecoverable('error reading header')
    assert not finalizing_error_is_unrecoverable('temporaneo: timeout durante ffprobe')

    temporary = tmp_path / '.clip.finalizing.mp4'
    temporary.write_bytes(b'incomplete')
    first = recovery_quarantine_path(temporary)
    assert first.name == '.clip.recovery-failed.mp4'
    first.write_bytes(b'older copy')
    second = recovery_quarantine_path(temporary)
    assert second.name == '.clip.recovery-failed-2.mp4'


def test_main_installs_non_repeating_finalizing_recovery_wrapper():
    facade = (Path(__file__).resolve().parents[1] / 'app/main/__init__.py').read_text(encoding='utf-8')
    assert '_recover_stale_finalizing_files_safe' in facade
    assert 'temporary.replace(quarantine)' in facade
    assert 'self.last_errors.pop(key, None)' in facade
    assert 'recovery-failed.mp4' not in facade  # naming policy stays centralized
