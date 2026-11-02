"""Exercise the host lifecycle without Docker, root access, or removable disks."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest


FIRST = 'a' * 64
SECOND = 'b' * 64
REMOVED = 'c' * 64
HELPER = Path(__file__).resolve().parents[2] / 'scripts' / 'instavault-storage.py'


@pytest.fixture
def helper():
    spec = importlib.util.spec_from_file_location('instavault_storage_lifecycle_test', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDocker:
    def __init__(self, module):
        self.module = module
        self.containers = {}
        self.recorded = []
        self.events = []
        self.failures = {}
        self.mount_uuid = module.DATA_UUID
        self.refresh_marker = True

    def add(self, container, *, running=True, marker=True, label=None):
        self.containers[container] = {
            'running': running,
            'marker': marker,
            'label': self.module.MODULE_NAME if label is None else label,
        }

    def save(self, ids):
        self.recorded = sorted(set(ids))
        self.events.append(('save', tuple(self.recorded)))

    def command(self, *args, **kwargs):
        self.events.append(args)
        result = SimpleNamespace(returncode=0, stdout='', stderr='')
        if args[0] == 'findmnt':
            result.stdout = json.dumps({'filesystems': [{
                'uuid': self.mount_uuid, 'target': str(self.module.NVME),
            }]})
        elif args[:2] == ('docker', 'ps'):
            assert args[2:] == (
                '--no-trunc', '-q', '--filter',
                f'label={self.module.MODULE_LABEL}={self.module.MODULE_NAME}',
            )
            result.stdout = '\n'.join(sorted(
                container for container, item in self.containers.items()
                if item['running'] and item['label'] == self.module.MODULE_NAME
            ))
        elif args[:2] == ('docker', 'inspect'):
            container = args[2]
            if container not in self.containers:
                result.returncode = 1
                result.stderr = f'Error: No such object: {container}'
            else:
                item = self.containers[container]
                result.stdout = json.dumps([{
                    'Id': container, 'State': {'Running': item['running']},
                    'Config': {'Labels': {self.module.MODULE_LABEL: item['label']}},
                }])
        elif args[:2] == ('docker', 'exec'):
            assert args[3:5] == ('python', '-c')
            result.returncode = 0 if self.containers[args[2]]['marker'] else 1
        elif args[0] == 'docker' and args[1] in {'stop', 'start', 'restart'}:
            operation, container = args[1], args[-1]
            failure = self.failures.get((operation, container))
            if failure:
                raise failure
            self.containers[container]['running'] = operation != 'stop'
            if operation in {'start', 'restart'}:
                self.containers[container]['marker'] = self.refresh_marker
        else:
            raise AssertionError(f'Unexpected command: {args}')
        return result

    def mutations(self):
        return [event for event in self.events if event[:2] in {
            ('docker', 'start'), ('docker', 'stop'), ('docker', 'restart'),
        }]


@pytest.fixture
def lifecycle(helper, monkeypatch, tmp_path):
    media = tmp_path / 'media'
    media.mkdir()
    (media / helper.MARKER).write_text(helper.MARKER_VALUE)
    monkeypatch.setattr(helper, 'NVME', tmp_path / 'mounted-nvme')
    monkeypatch.setattr(helper, 'MEDIA', media)
    fake = FakeDocker(helper)
    monkeypatch.setattr(helper, 'command', fake.command)
    monkeypatch.setattr(helper, 'read_state', lambda: list(fake.recorded))
    monkeypatch.setattr(helper, 'save_state', fake.save)
    return helper, fake


def test_pause_persists_every_exact_running_id_before_first_stop(lifecycle):
    module, docker = lifecycle
    docker.add(FIRST)
    docker.add(SECOND)
    module.pause()
    first_stop = next(i for i, event in enumerate(docker.events) if event[:2] == ('docker', 'stop'))
    assert ('save', (FIRST, SECOND)) in docker.events[:first_stop]
    assert docker.recorded == [FIRST, SECOND]
    assert all(not item['running'] for item in docker.containers.values())


def test_resume_starts_recorded_container_but_not_user_stopped_container(lifecycle):
    module, docker = lifecycle
    docker.add(FIRST, running=False)
    docker.add(SECOND, running=False)
    docker.recorded = [FIRST]
    module.resume()
    assert docker.mutations() == [('docker', 'start', FIRST)]
    assert docker.containers[FIRST]['running']
    assert not docker.containers[SECOND]['running']
    assert docker.recorded == []


def test_wrong_uuid_blocks_resume_before_any_docker_command(lifecycle):
    module, docker = lifecycle
    docker.add(FIRST, running=False)
    docker.recorded = [FIRST]
    docker.mount_uuid = 'unrelated-filesystem'
    with pytest.raises(RuntimeError, match='UUID'):
        module.resume()
    assert not any(event[0] == 'docker' for event in docker.events)
    assert docker.recorded == [FIRST]


def test_invalid_media_marker_blocks_resume(lifecycle):
    module, docker = lifecycle
    docker.add(FIRST, running=False)
    docker.recorded = [FIRST]
    (module.MEDIA / module.MARKER).write_text('another-volume')
    with pytest.raises(RuntimeError, match='marker'):
        module.resume()
    assert docker.mutations() == []
    assert docker.recorded == [FIRST]


def test_running_missing_marker_view_is_recorded_before_restart(lifecycle):
    module, docker = lifecycle
    docker.add(FIRST, marker=False)
    module.resume()
    restart = ('docker', 'restart', '--time', '40', FIRST)
    assert docker.mutations() == [restart]
    assert docker.events.index(('save', (FIRST,))) < docker.events.index(restart)
    assert docker.containers[FIRST]['marker']
    assert docker.recorded == []


def test_removed_recorded_container_is_ignored_without_starting_other_containers(lifecycle):
    module, docker = lifecycle
    docker.recorded = [REMOVED]
    docker.add(SECOND, running=False)
    module.resume()
    assert docker.mutations() == []
    assert docker.recorded == []


@pytest.mark.parametrize('operation', ['stop', 'start', 'restart'])
def test_failed_lifecycle_operation_preserves_recorded_id(lifecycle, operation):
    module, docker = lifecycle
    docker.add(FIRST, running=operation != 'start', marker=operation != 'restart')
    if operation == 'start':
        docker.recorded = [FIRST]
    docker.failures[operation, FIRST] = subprocess.TimeoutExpired(['docker', operation], 55)
    action = module.pause if operation == 'stop' else module.resume
    with pytest.raises(RuntimeError):
        action()
    assert docker.recorded == [FIRST]


def test_healthy_running_container_is_not_restarted(lifecycle):
    module, docker = lifecycle
    docker.add(FIRST)
    module.resume()
    assert docker.mutations() == []
    assert docker.containers[FIRST]['running']


def test_recorded_container_with_changed_label_is_never_controlled(lifecycle):
    module, docker = lifecycle
    docker.add(FIRST, running=False, label='unrelated-application')
    docker.recorded = [FIRST]
    with pytest.raises(RuntimeError, match='no longer an InstaVault'):
        module.resume()
    assert docker.mutations() == []
    assert docker.recorded == [FIRST]


def test_failed_bind_repair_stops_container_and_preserves_retry_state(lifecycle):
    module, docker = lifecycle
    docker.add(FIRST, marker=False)
    docker.refresh_marker = False
    with pytest.raises(RuntimeError, match='no media marker'):
        module.resume()
    assert docker.mutations() == [
        ('docker', 'restart', '--time', '40', FIRST),
        ('docker', 'stop', '--time', '40', FIRST),
    ]
    assert not docker.containers[FIRST]['running']
    assert docker.recorded == [FIRST]


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory fsync and permissions')
def test_state_publish_is_private_atomic_and_deduplicated(helper, monkeypatch, tmp_path):
    monkeypatch.setattr(helper, 'STATE_PATH', tmp_path / 'containers.json')
    # CI need not be root: exercise the actual atomic writer in a private temp dir.
    monkeypatch.setattr(helper, 'state_directory', lambda: tmp_path)
    helper.save_state([SECOND, FIRST, FIRST])
    assert json.loads(helper.STATE_PATH.read_text()) == {
        'version': 1, 'containers': [FIRST, SECOND],
    }
    assert stat.S_IMODE(helper.STATE_PATH.stat().st_mode) == 0o600
    assert not list(tmp_path.glob('.containers-*'))
