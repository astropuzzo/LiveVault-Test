#!/usr/bin/env python3
"""Runtime hardening for Media Hub HLS playback.

The original Level 5 implementation uses fragmented MP4 HLS. Some desktop
browser/MSE combinations can stall indefinitely on fMP4 when an MKV H.264
stream is copied while AC3 is converted to AAC. Keep the same governor and
seek model, but emit classic MPEG-TS HLS for the fallback path. It avoids the
separate init fragment and is deliberately boring/compatible.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time


def apply(streaming) -> None:
    """Patch only HLS production/segment MIME on the already imported module."""
    if getattr(streaming, '_openastro_media_patch_v15', False):
        return

    def segment_info(token: str, name: str) -> dict:
        job = streaming._job(token)
        if not streaming.SEGMENT_RE.fullmatch(name or ''):
            raise ValueError('Segmento HLS non valido')
        root: Path = job['root']
        target = (root / name).resolve(strict=True)
        if target.parent != root.resolve():
            raise PermissionError('Segmento fuori sessione')
        st = target.stat()
        if target.suffix == '.ts':
            mime = 'video/mp2t'
        elif target.suffix in {'.mp4', '.m4s'}:
            mime = 'video/mp4'
        else:
            mime = 'application/octet-stream'
        return {'path': target, 'name': name, 'size': st.st_size, 'mime': mime}

    def start_hls(uuid: str, relative: str, *, client: str = '', position: float = 0.0, audio_stream: int | None = None) -> dict:
        plan = streaming.playback_plan(uuid, relative, audio_stream=audio_stream)
        if plan['mode'] == 'direct':
            return {'ok': True, 'mode': 'direct', 'plan': plan}
        if not plan['available'] or not str(plan['mode']).startswith('hls_'):
            raise RuntimeError(plan['reason'])

        info = streaming.media_center.file_info(uuid, relative)
        position = max(0.0, float(position or 0.0))
        if plan.get('duration') and position >= float(plan['duration']) - 10:
            position = 0.0

        streaming.HLS_ROOT.mkdir(parents=True, exist_ok=True)
        token = streaming._job_token(uuid, relative, info['modified'], info['size'])
        root = streaming.HLS_ROOT / token
        root.mkdir(mode=0o750)
        manifest = root / 'index.m3u8'
        log_path = root / 'ffmpeg.log'

        # -re protects the CM4 and LiveVault recorder. A one-second initial HLS
        # target makes the first playable segment appear quickly; subsequent
        # segments use the normal four-second target.
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin', '-re']
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
            args += [
                '-vf', "scale='min(1280,iw)':-2:flags=fast_bilinear,format=yuv420p",
                '-c:v', 'h264_v4l2m2m', '-b:v', '3500k', '-maxrate', '4500k', '-bufsize', '7000k',
                '-c:a', 'aac', '-b:a', '160k', '-ac', '2',
            ]

        args += [
            '-max_muxing_queue_size', '2048',
            '-f', 'hls',
            '-hls_init_time', '1',
            '-hls_time', '4',
            '-hls_list_size', '90',
            '-hls_delete_threshold', '10',
            '-hls_segment_type', 'mpegts',
            '-hls_flags', 'delete_segments+independent_segments+temp_file',
            '-hls_segment_filename', str(root / 'seg-%05d.ts'),
            str(manifest),
        ]

        stderr = log_path.open('wb')
        try:
            with streaming._LOCK:
                streaming._cleanup_locked()
                if len(streaming._JOBS) >= streaming.MAX_HLS_JOBS:
                    oldest = min(streaming._JOBS, key=lambda key: streaming._JOBS[key].get('last_access', 0))
                    streaming._stop_locked(oldest)
                proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
                streaming._JOBS[token] = {
                    'token': token, 'uuid': uuid, 'path': relative, 'name': info['name'], 'mode': mode,
                    'root': root, 'manifest': manifest, 'process': proc, 'started': time.time(),
                    'last_access': time.time(), 'client': client, 'plan': plan, 'offset': position,
                    'transport': 'mpegts',
                }
        finally:
            stderr.close()

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if manifest.exists() and manifest.stat().st_size > 40 and any(root.glob('seg-*.ts')):
                return {
                    'ok': True, 'mode': mode, 'token': token,
                    'manifest': f'/api/media/hls/manifest?token={token}',
                    'plan': plan, 'offset': position, 'transport': 'mpegts',
                }
            if proc.poll() is not None:
                try:
                    detail = log_path.read_text(errors='replace')[-1800:]
                except OSError:
                    detail = ''
                with streaming._LOCK:
                    streaming._stop_locked(token)
                raise RuntimeError('Transcode HLS non avviato. ' + detail.strip())
            time.sleep(.2)

        return {
            'ok': True, 'mode': mode, 'token': token,
            'manifest': f'/api/media/hls/manifest?token={token}',
            'plan': plan, 'offset': position, 'starting': True, 'transport': 'mpegts',
        }

    streaming.segment_info = segment_info
    streaming.start_hls = start_hls
    streaming._openastro_media_patch_v15 = True
