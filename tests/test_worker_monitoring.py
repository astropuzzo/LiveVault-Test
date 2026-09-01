import inspect

from app.workers import WorkerManager


def test_global_recording_pause_does_not_pause_source_monitoring():
    poll_loop = inspect.getsource(WorkerManager._poll_loop)
    source_check = inspect.getsource(WorkerManager._check_source_unlocked)

    assert "recording_paused" not in poll_loop
    assert "cfg.recording_paused" in source_check
