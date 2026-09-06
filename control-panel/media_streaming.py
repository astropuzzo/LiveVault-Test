#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request

import media_center

STATE_ROOT = Path('/var/lib/openastro-control')
HLS_ROOT = STATE_ROOT / 'media-hls'
SUB_ROOT = STATE_ROOT / 'media-subs'
TOKEN_RE = re.compile(r'^[a-f0-9]{20}$')
SEGMENT_RE = re.compile(r'^[A-Za-z0-9._-]{1,120}$')
_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
MAX_HLS_JOBS = 2
HLS_IDLE_SECONDS = 600


def _active_recorders() -> int:
    try:
        with urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2) as response:
            payload = json.loads(response.read().decode('utf-8'))
            return int((payload.get('worker') or {}).get('active_recorders') or 0)
    except Exception:
        return 0


def _temperature() -> float | None:
    for path in sorted(Path('/sys/class/thermal').glob('thermal_zone*/temp')):
        try:
            value = float(path.read_text().strip()) / 1000
            if 0 < value < 130:
                return value
        except (OSError, ValueError):
            pass
    return None


def _pressure() -> dict:
    load = os.getloadavg()[0]
    temp = _temperature()
    return {'load1': round(load, 2), 'temperature': temp, 'active_recorders': _active_recorders()}


def _probe_parts(probe_payload: dict) -> tuple[dict, dict, dict]:
    probe = probe_payload.get('probe') or {}
    fmt = probe.get('format') or {}
    streams = probe.get('streams') or []
    video = next((s for s in streams if s.get('codec_type') == 'video'), {})
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), {})
    return fmt, video, audio


def subtitle_tracks(uuid: str, relative: str) -> list[dict]:
    info = media_center.file_info(uuid, relative)
    source = info['path']
    tracks = []
    try:
        children = list(source.parent.iterdir())
    except OSError:
        return []
    stem = source.stem.casefold()
    for child in children:
        if not child.is_file() or child.is_symlink():
            continue
        if child.suffix.casefold() not in {'.srt', '.vtt', '.ass', '.ssa'}:
            continue
        child_stem = child.stem.casefold()
        if child_stem != stem and not child_stem.startswith(stem + '.'):
            continue
        root = Path(media_center._device(uuid)['mountpoint']).resolve(strict=True)
        rel = child.relative_to(root).as_posix()
        lang = child.stem[len(source.stem):].lstrip('._-') or 'Subtitles'
        tracks.append({'path': rel, 'label': lang.upper() if len(lang) <= 5 else lang, 'format': child.suffix.casefold().lstrip('.')})
    return tracks[:12]


def playback_plan(uuid: str, relative: str) -> dict:
    info = media_center.file_info(uuid, relative)
    probe_payload = media_center.probe_file(uuid, relative)
    fmt, video, audio = _probe_parts(probe_payload)
    ext = Path(relative).suffix.casefold()
    vcodec = str(video.get('codec_name') or '').casefold()
    acodec = str(audio.get('codec_name') or '').casefold()
    mode = 'direct'
    reason = 'Browser-compatible direct play'
    if info['category'] == 'video':
        direct_mp4 = ext in {'.mp4', '.m4v', '.mov'} and vcodec in {'h264', 'avc1'} and acodec in {'', 'aac', 'mp3'}
        direct_webm = ext == '.webm' and vcodec in {'vp8', 'vp9', 'av1'} and acodec in {'', 'opus', 'vorbis'}
        if direct_mp4 or direct_webm:
            mode = 'direct'
        elif vcodec == 'h264' and acodec in {'', 'aac'}:
            mode = 'hls_copy'; reason = 'Container remux to HLS; video/audio copied'
        elif vcodec == 'h264':
            mode = 'hls_audio'; reason = 'H.264 video copied; audio converted to AAC'
        else:
            mode = 'hls_transcode'; reason = 'Video converted to browser-safe H.264/AAC'
    elif info['category'] == 'audio':
        if ext not in {'.mp3', '.m4a', '.aac', '.wav', '.ogg', '.opus'}:
            mode = 'unsupported'; reason = 'Audio format needs a future audio transcode path'
    elif info['category'] == 'image':
        if ext not in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.avif'}:
            mode = 'unsupported'; reason = 'Image format not browser-safe'
    else:
        mode = 'download'; reason = 'Not a playable media type'
    pressure = _pressure()
    available = mode not in {'unsupported', 'download'}
    if mode == 'hls_transcode':
        if pressure['active_recorders'] > 0:
            available = False; reason = 'Transcode video sospeso mentre LiveVault registra'
        elif pressure['temperature'] is not None and pressure['temperature'] >= 75:
            available = False; reason = 'Transcode sospeso: temperatura elevata'
        elif pressure['load1'] >= 3.2:
            available = False; reason = 'Transcode sospeso: CPU già sotto carico'
    return {
        'ok': True, 'uuid': uuid, 'path': relative, 'name': info['name'], 'category': info['category'],
        'mode': mode, 'available': available, 'reason': reason, 'direct_url': None,
        'video_codec': vcodec or None, 'audio_codec': acodec or None,
        'width': video.get('width'), 'height': video.get('height'),
        'duration': float(fmt.get('duration') or 0), 'pressure': pressure,
        'subtitles': subtitle_tracks(uuid, relative) if info['category'] == 'video' else [],
        'hls_window_seconds': 360 if mode.startswith('hls_') else None,
        'hardware_encoder': Path('/dev/video11').exists(),
    }


def _job_token(uuid: str, relative: str, modified: int, size: int) -> str:
    raw = f'{uuid}\0{relative}\0{modified}\0{size}\0{time.time_ns()}'.encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def _cleanup_locked() -> None:
    now = time.time()
    stale = []
    for token, job in _JOBS.items():
        proc = job.get('process')
        if proc is not None and proc.poll() is not None:
            job['ended'] = True
        if now - job.get('last_access', now) > HLS_IDLE_SECONDS:
            stale.append(token)
    for token in stale:
        _stop_locked(token)
    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    for child in HLS_ROOT.iterdir():
        if child.is_dir() and child.name not in _JOBS:
            try:
                if now - child.stat().st_mtime > HLS_IDLE_SECONDS:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass


def _stop_locked(token: str) -> None:
    job = _JOBS.pop(token, None)
    if not job:
        return
    proc = job.get('process')
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        except Exception:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except Exception: pass
    shutil.rmtree(job['root'], ignore_errors=True)


def stop_hls(token: str) -> dict:
    if not TOKEN_RE.fullmatch(token or ''):
        raise ValueError('Token HLS non valido')
    with _LOCK:
        _stop_locked(token)
    return {'ok': True}


def start_hls(uuid: str, relative: str, *, client: str = '', position: float = 0.0) -> dict:
    plan = playback_plan(uuid, relative)
    if plan['mode'] == 'direct':
        return {'ok': True, 'mode': 'direct', 'plan': plan}
    if not plan['available'] or not str(plan['mode']).startswith('hls_'):
        raise RuntimeError(plan['reason'])
    info = media_center.file_info(uuid, relative)
    position = max(0.0, float(position or 0.0))
    if plan.get('duration') and position >= float(plan['duration']) - 10:
        position = 0.0
    HLS_ROOT.mkdir(parents=True, exist_ok=True)
    token = _job_token(uuid, relative, info['modified'], info['size'])
    root = HLS_ROOT / token
    root.mkdir(mode=0o750)
    manifest = root / 'index.m3u8'
    stderr = (root / 'ffmpeg.log').open('wb')
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin', '-re']
    if position > 0:
        args += ['-ss', f'{position:.3f}']
    args += ['-i', str(info['path']), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn']
    mode = plan['mode']
    if mode == 'hls_copy':
        args += ['-c:v', 'copy', '-c:a', 'copy']
    elif mode == 'hls_audio':
        args += ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-ac', '2']
    else:
        args += ['-vf', "scale='min(1280,iw)':-2:flags=fast_bilinear,format=yuv420p",
                 '-c:v', 'h264_v4l2m2m', '-b:v', '3500k', '-maxrate', '4500k', '-bufsize', '7000k',
                 '-c:a', 'aac', '-b:a', '160k', '-ac', '2']
    args += ['-max_muxing_queue_size', '2048', '-f', 'hls', '-hls_time', '4', '-hls_list_size', '90',
             '-hls_delete_threshold', '10', '-hls_segment_type', 'fmp4',
             '-hls_flags', 'delete_segments+independent_segments+temp_file',
             '-hls_fmp4_init_filename', 'init.mp4', '-hls_segment_filename', str(root / 'seg-%05d.m4s'), str(manifest)]
    with _LOCK:
        _cleanup_locked()
        if len(_JOBS) >= MAX_HLS_JOBS:
            oldest = min(_JOBS, key=lambda key: _JOBS[key].get('last_access', 0))
            _stop_locked(oldest)
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
        _JOBS[token] = {'token': token, 'uuid': uuid, 'path': relative, 'name': info['name'], 'mode': mode,
                        'root': root, 'manifest': manifest, 'process': proc, 'started': time.time(),
                        'last_access': time.time(), 'client': client, 'plan': plan, 'offset': position}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if manifest.exists() and manifest.stat().st_size > 40:
            return {'ok': True, 'mode': mode, 'token': token, 'manifest': f'/api/media/hls/manifest?token={token}', 'plan': plan, 'offset': position}
        if proc.poll() is not None:
            try: detail = (root / 'ffmpeg.log').read_text(errors='replace')[-1800:]
            except OSError: detail = ''
            with _LOCK: _stop_locked(token)
            raise RuntimeError('Transcode HLS non avviato. ' + detail.strip())
        time.sleep(.2)
    return {'ok': True, 'mode': mode, 'token': token, 'manifest': f'/api/media/hls/manifest?token={token}', 'plan': plan, 'offset': position, 'starting': True}


def _job(token: str) -> dict:
    if not TOKEN_RE.fullmatch(token or ''):
        raise ValueError('Token HLS non valido')
    with _LOCK:
        _cleanup_locked()
        job = _JOBS.get(token)
        if not job:
            raise FileNotFoundError('Sessione HLS non disponibile')
        job['last_access'] = time.time()
        return dict(job)


def manifest_text(token: str) -> str:
    job = _job(token)
    path: Path = job['manifest']
    if not path.exists():
        raise FileNotFoundError('Playlist HLS in preparazione')
    text = path.read_text(encoding='utf-8', errors='replace')
    out = []
    for line in text.splitlines():
        if line.startswith('#EXT-X-MAP:') and 'URI="' in line:
            name = line.split('URI="', 1)[1].split('"', 1)[0]
            line = line.replace(f'URI="{name}"', f'URI="/api/media/hls/segment?token={token}&name={name}"')
        elif line and not line.startswith('#'):
            name = Path(line).name
            line = f'/api/media/hls/segment?token={token}&name={name}'
        out.append(line)
    return '\n'.join(out) + '\n'


def segment_info(token: str, name: str) -> dict:
    job = _job(token)
    if not SEGMENT_RE.fullmatch(name or ''):
        raise ValueError('Segmento HLS non valido')
    root: Path = job['root']
    target = (root / name).resolve(strict=True)
    if target.parent != root.resolve():
        raise PermissionError('Segmento fuori sessione')
    st = target.stat()
    mime = 'video/mp4' if target.suffix in {'.mp4', '.m4s'} else 'application/octet-stream'
    return {'path': target, 'name': name, 'size': st.st_size, 'mime': mime}


def hls_jobs() -> list[dict]:
    with _LOCK:
        _cleanup_locked()
        now = time.time()
        return [{k: v for k, v in job.items() if k not in {'process','root','manifest','plan'}} | {
            'running': job['process'].poll() is None, 'seconds': round(now-job['started'],1), 'plan': job['plan']
        } for job in _JOBS.values()]


def subtitle_info(uuid: str, subtitle_relative: str) -> dict:
    info = media_center.file_info(uuid, subtitle_relative)
    source: Path = info['path']
    ext = source.suffix.casefold()
    if ext not in {'.srt','.vtt','.ass','.ssa'}:
        raise FileNotFoundError('Sottotitolo non supportato')
    if ext == '.vtt':
        return {'path': source, 'name': source.name, 'size': source.stat().st_size, 'mime': 'text/vtt'}
    SUB_ROOT.mkdir(parents=True, exist_ok=True)
    identity = f"{uuid}\0{subtitle_relative}\0{info['modified']}\0{info['size']}".encode()
    target = SUB_ROOT / (hashlib.sha256(identity).hexdigest() + '.vtt')
    if not target.exists() or target.stat().st_size < 10:
        tmp = target.with_suffix('.tmp.vtt')
        result = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(source),'-f','webvtt',str(tmp)], capture_output=True, timeout=8)
        if result.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True); raise FileNotFoundError('Conversione sottotitolo fallita')
        tmp.replace(target)
    return {'path': target, 'name': target.name, 'size': target.stat().st_size, 'mime': 'text/vtt'}


def diagnostics() -> dict:
    return {'ok': True, 'hls_jobs': hls_jobs(), 'pressure': _pressure(), 'hls_root': str(HLS_ROOT),
            'hardware_h264': Path('/dev/video11').exists(), 'hls_js': '1.7.2'}
