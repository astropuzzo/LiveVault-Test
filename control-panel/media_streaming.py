#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
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
_PLAN_CACHE_LOCK = threading.Lock()
_PLAN_CACHE: dict[tuple[str, str, int | None], dict] = {}
_PLAN_CACHE_MAX = 64
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


def _stream_label(stream: dict, fallback: str) -> tuple[str, str]:
    tags = stream.get('tags') or {}
    language = str(tags.get('language') or tags.get('LANGUAGE') or '').strip().lower()
    title = str(tags.get('title') or tags.get('TITLE') or '').strip()
    label = title or (language.upper() if language else fallback)
    return label, language


def audio_tracks(probe_payload: dict) -> list[dict]:
    streams = (probe_payload.get('probe') or {}).get('streams') or []
    tracks = []
    for ordinal, stream in enumerate(s for s in streams if s.get('codec_type') == 'audio'):
        try:
            index = int(stream.get('index'))
        except (TypeError, ValueError):
            continue
        label, language = _stream_label(stream, f'Audio {ordinal + 1}')
        tracks.append({
            'stream_index': index, 'ordinal': ordinal, 'label': label, 'language': language,
            'codec': str(stream.get('codec_name') or '').lower(),
            'channels': int(stream.get('channels') or 0),
            'channel_layout': str(stream.get('channel_layout') or ''),
            'default': bool((stream.get('disposition') or {}).get('default')),
        })
    if tracks and not any(track['default'] for track in tracks):
        tracks[0]['default'] = True
    return tracks


def subtitle_tracks(uuid: str, relative: str, probe_payload: dict | None = None) -> list[dict]:
    info = media_center.file_info(uuid, relative)
    source = info['path']
    tracks = []
    if probe_payload is None:
        try:
            probe_payload = media_center.probe_file(uuid, relative)
        except Exception:
            probe_payload = {}
    streams = (probe_payload.get('probe') or {}).get('streams') or []
    embedded_ordinal = 0
    for stream in streams:
        if stream.get('codec_type') != 'subtitle':
            continue
        try:
            index = int(stream.get('index'))
        except (TypeError, ValueError):
            continue
        label, language = _stream_label(stream, f'Sottotitoli {embedded_ordinal + 1}')
        tracks.append({
            'kind': 'embedded', 'path': relative, 'stream_index': index, 'label': label,
            'language': language, 'format': str(stream.get('codec_name') or '').lower(),
            'forced': bool((stream.get('disposition') or {}).get('forced')),
            'default': bool((stream.get('disposition') or {}).get('default')),
        })
        embedded_ordinal += 1
    try:
        children = list(source.parent.iterdir())
    except OSError:
        return tracks[:12]
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
        tracks.append({'kind': 'sidecar', 'path': rel, 'label': lang.upper() if len(lang) <= 5 else lang, 'language': lang.lower(), 'format': child.suffix.casefold().lstrip('.')})
    return tracks[:12]


def playback_plan(uuid: str, relative: str, *, audio_stream: int | None = None) -> dict:
    cache_key=(uuid,relative,audio_stream)
    with _PLAN_CACHE_LOCK:
        cached=_PLAN_CACHE.get(cache_key)
    if cached is not None:
        try:
            st=cached['source'].stat()
            plan=cached['plan']
            if int(st.st_mtime)==cached['modified'] and st.st_size==cached['size'] and plan.get('mode')!='hls_transcode':
                return plan
        except OSError:
            pass
    info = media_center.file_info(uuid, relative)
    probe_payload = media_center.probe_file(uuid, relative)
    fmt, video, audio = _probe_parts(probe_payload)
    tracks = audio_tracks(probe_payload)
    selected_audio = None
    if tracks:
        selected_audio = next((track for track in tracks if track['stream_index'] == audio_stream), None) if audio_stream is not None else next((track for track in tracks if track['default']), tracks[0])
        if audio_stream is not None and selected_audio is None:
            raise ValueError('Traccia audio non valida')
    ext = Path(relative).suffix.casefold()
    vcodec = str(video.get('codec_name') or '').casefold()
    acodec = str((selected_audio or {}).get('codec') or audio.get('codec_name') or '').casefold()
    mode = 'direct'
    reason = 'Browser-compatible direct play'
    if info['category'] == 'video':
        direct_mp4 = ext in {'.mp4', '.m4v', '.mov'} and vcodec in {'h264', 'avc1'} and acodec in {'', 'aac', 'mp3'}
        direct_webm = ext == '.webm' and vcodec in {'vp8', 'vp9', 'av1'} and acodec in {'', 'opus', 'vorbis'}
        if (direct_mp4 or direct_webm) and len(tracks) <= 1:
            mode = 'direct'
        elif direct_mp4 or direct_webm:
            mode = 'hls_copy'; reason = 'Più tracce audio: remux HLS per selezione lingua senza ricodifica video'
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
    result={
        'ok': True, 'uuid': uuid, 'path': relative, 'name': info['name'], 'category': info['category'],
        'mode': mode, 'available': available, 'reason': reason, 'direct_url': None,
        'video_codec': vcodec or None, 'audio_codec': acodec or None,
        'width': video.get('width'), 'height': video.get('height'),
        'duration': float(fmt.get('duration') or 0), 'pressure': pressure,
        'audio_tracks': tracks if info['category'] == 'video' else [],
        'selected_audio_stream': selected_audio['stream_index'] if selected_audio else None,
        'subtitles': subtitle_tracks(uuid, relative, probe_payload) if info['category'] == 'video' else [],
        'hls_window_seconds': 360 if mode.startswith('hls_') else None,
        'hardware_encoder': Path('/dev/video11').exists(),
    }
    if mode!='hls_transcode' and info.get('path') is not None and info.get('modified') is not None and info.get('size') is not None:
        entry={'source':info['path'],'modified':int(info['modified']),'size':int(info['size']),'plan':result}
        with _PLAN_CACHE_LOCK:
            _PLAN_CACHE[cache_key]=entry
            if selected_audio is not None:
                _PLAN_CACHE[(uuid,relative,int(selected_audio['stream_index']))]=entry
            while len(_PLAN_CACHE)>_PLAN_CACHE_MAX:
                _PLAN_CACHE.pop(next(iter(_PLAN_CACHE)))
    return result

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
            proc.wait(timeout=.15)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=.15)
            except Exception:
                pass
    shutil.rmtree(job['root'], ignore_errors=True)


def stop_hls(token: str) -> dict:
    if not TOKEN_RE.fullmatch(token or ''):
        raise ValueError('Token HLS non valido')
    with _LOCK:
        _stop_locked(token)
    return {'ok': True}


def start_hls(uuid: str, relative: str, *, client: str = '', position: float = 0.0, audio_stream: int | None = None) -> dict:
    plan = playback_plan(uuid, relative, audio_stream=audio_stream)
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
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin', '-readrate', '1', '-readrate_initial_burst', '12']
    if position > 0:
        args += ['-ss', f'{position:.3f}']
    audio_map = f"0:{plan['selected_audio_stream']}" if plan.get('selected_audio_stream') is not None else '0:a:0?'
    args += ['-i', str(info['path']), '-map', '0:v:0', '-map', audio_map, '-sn', '-dn']
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
        replacement = next((key for key, job in _JOBS.items() if client and job.get('client') == client and job.get('uuid') == uuid and job.get('path') == relative), None)
        if replacement:
            _stop_locked(replacement)
        elif len(_JOBS) >= MAX_HLS_JOBS:
            _stop_locked(min(_JOBS, key=lambda key: _JOBS[key].get('last_access', 0)))
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


def _ass_time_to_vtt(value: str) -> str:
    match = re.fullmatch(r'\s*(\d+):(\d{1,2}):(\d{1,2})(?:[.,](\d+))?\s*', value)
    if not match:
        raise ValueError('Timestamp ASS non valido')
    hours, minutes, seconds = (int(match.group(i)) for i in range(1, 4))
    fraction = (match.group(4) or '0')[:3].ljust(3, '0')
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}.{fraction}'


def _ass_text_to_vtt(value: str) -> str:
    # WebVTT does not need ASS positioning/font commands. Keep only readable text.
    value = re.sub(r'\{[^}]*\}', '', value)
    value = value.replace(r'\N', '\n').replace(r'\n', '\n').replace(r'\h', ' ')
    return html.escape(value.strip(), quote=False)


def _ass_to_webvtt(source: Path, target: Path) -> None:
    fields: list[str] = []
    cues: list[tuple[str, str, str]] = []
    in_events = False
    for raw in source.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        line = raw.strip('\ufeff')
        if line.startswith('['):
            in_events = line.strip().casefold() == '[events]'
            continue
        if not in_events:
            continue
        if line.casefold().startswith('format:'):
            fields = [part.strip().casefold() for part in line.split(':', 1)[1].split(',')]
            continue
        if not line.casefold().startswith('dialogue:'):
            continue
        payload = line.split(':', 1)[1].lstrip()
        active_fields = fields or ['layer','start','end','style','name','marginl','marginr','marginv','effect','text']
        parts = payload.split(',', len(active_fields) - 1)
        if len(parts) != len(active_fields):
            continue
        row = dict(zip(active_fields, parts))
        try:
            start = _ass_time_to_vtt(row.get('start', ''))
            end = _ass_time_to_vtt(row.get('end', ''))
        except ValueError:
            continue
        text = _ass_text_to_vtt(row.get('text', ''))
        if text:
            cues.append((start, end, text))
    if not cues:
        raise FileNotFoundError('Sottotitolo ASS senza testo leggibile')
    body = ['WEBVTT', '']
    for start, end, text in cues:
        body.extend([f'{start} --> {end}', text, ''])
    target.write_text('\n'.join(body), encoding='utf-8')


def subtitle_info(uuid: str, subtitle_relative: str, *, stream_index: int | None = None) -> dict:
    info = media_center.file_info(uuid, subtitle_relative)
    source: Path = info['path']
    if stream_index is not None:
        probe_payload = media_center.probe_file(uuid, subtitle_relative)
        embedded = [track for track in subtitle_tracks(uuid, subtitle_relative, probe_payload) if track.get('kind') == 'embedded']
        selected = next((track for track in embedded if track['stream_index'] == stream_index), None)
        if selected is None:
            raise FileNotFoundError('Traccia sottotitoli non valida')
        SUB_ROOT.mkdir(parents=True, exist_ok=True)
        identity = f"{uuid}\0{subtitle_relative}\0{info['modified']}\0{info['size']}\0embedded:{stream_index}".encode()
        cache_key = hashlib.sha256(identity).hexdigest()
        target = SUB_ROOT / (cache_key + '.vtt')
        if not target.exists() or target.stat().st_size < 10:
            tmp = target.with_suffix('.tmp.vtt')
            codec = str(selected.get('format') or '').lower()
            if codec in {'ass', 'ssa'}:
                # Some FFmpeg builds lose ASS dialogue text when converting directly
                # from Matroska to WebVTT. Demux the selected subtitle in stream-copy
                # mode first (cheap, no video/audio decode), then convert the tiny ASS.
                raw = SUB_ROOT / (cache_key + '.ass')
                if not raw.exists() or raw.stat().st_size < 10:
                    raw_tmp = SUB_ROOT / (cache_key + '.tmp.ass')
                    extract = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(source),'-map',f'0:{stream_index}','-c:s','copy',str(raw_tmp)], capture_output=True, timeout=120)
                    if extract.returncode != 0 or not raw_tmp.exists():
                        raw_tmp.unlink(missing_ok=True); raise FileNotFoundError('Estrazione sottotitolo embedded fallita')
                    raw_tmp.replace(raw)
                try:
                    _ass_to_webvtt(raw, tmp)
                except (OSError, ValueError, FileNotFoundError):
                    tmp.unlink(missing_ok=True); raise FileNotFoundError('Conversione sottotitolo ASS fallita')
                result = None
            else:
                result = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(source),'-map',f'0:{stream_index}','-f','webvtt',str(tmp)], capture_output=True, timeout=120)
            if (result is not None and result.returncode != 0) or not tmp.exists() or tmp.stat().st_size < 10:
                tmp.unlink(missing_ok=True); raise FileNotFoundError('Conversione sottotitolo embedded fallita')
            tmp.replace(target)
        return {'path': target, 'name': target.name, 'size': target.stat().st_size, 'mime': 'text/vtt'}
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
