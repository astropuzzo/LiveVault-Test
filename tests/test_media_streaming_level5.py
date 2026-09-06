from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path('/mnt/livevault-nvme/gpt-harness/work/LiveVault-Test/control-panel')
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('media_streaming_level5_test', ROOT / 'media_streaming.py')
streaming = importlib.util.module_from_spec(spec)
spec.loader.exec_module(streaming)


def _probe(v='h264', a='aac', duration='3600', width=1920, height=1080):
    streams=[]
    if v: streams.append({'codec_type':'video','codec_name':v,'width':width,'height':height})
    if a: streams.append({'codec_type':'audio','codec_name':a})
    return {'probe':{'format':{'duration':duration},'streams':streams}}


@pytest.fixture(autouse=True)
def common(monkeypatch):
    monkeypatch.setattr(streaming, 'subtitle_tracks', lambda *args: [])
    monkeypatch.setattr(streaming, '_pressure', lambda: {'load1':0.3,'temperature':50.0,'active_recorders':0})


def plan(monkeypatch, suffix, probe):
    monkeypatch.setattr(streaming.media_center, 'file_info', lambda u,p: {'path':Path('/tmp/x'+suffix),'name':'x'+suffix,'category':'video'})
    monkeypatch.setattr(streaming.media_center, 'probe_file', lambda u,p: probe)
    return streaming.playback_plan('USB-1','x'+suffix)


def test_browser_safe_mp4_is_direct(monkeypatch):
    result=plan(monkeypatch,'.mp4',_probe('h264','aac'))
    assert result['mode']=='direct' and result['available']


def test_h264_mkv_is_zero_video_transcode_hls(monkeypatch):
    result=plan(monkeypatch,'.mkv',_probe('h264','aac'))
    assert result['mode']=='hls_copy' and result['available']


def test_h264_with_ac3_only_converts_audio(monkeypatch):
    result=plan(monkeypatch,'.mkv',_probe('h264','ac3'))
    assert result['mode']=='hls_audio' and result['available']


def test_hevc_transcode_is_protected_while_livevault_records(monkeypatch):
    monkeypatch.setattr(streaming, '_pressure', lambda: {'load1':0.5,'temperature':52.0,'active_recorders':1})
    result=plan(monkeypatch,'.mkv',_probe('hevc','aac'))
    assert result['mode']=='hls_transcode'
    assert result['available'] is False
    assert 'LiveVault' in result['reason']


def test_hevc_transcode_available_when_node_is_idle(monkeypatch):
    result=plan(monkeypatch,'.mkv',_probe('hevc','aac'))
    assert result['mode']=='hls_transcode' and result['available']
    assert result['hardware_encoder'] == Path('/dev/video11').exists()


def test_hls_manifest_rewrites_segments_through_authenticated_api(tmp_path, monkeypatch):
    token='a'*20
    root=tmp_path/token; root.mkdir()
    manifest=root/'index.m3u8'
    manifest.write_text('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4.0,\nseg-00001.m4s\n')
    class Proc:
        def poll(self): return None
    monkeypatch.setattr(streaming,'HLS_ROOT',tmp_path)
    monkeypatch.setattr(streaming,'_JOBS',{token:{'token':token,'root':root,'manifest':manifest,'process':Proc(),'last_access':0,'started':0,'uuid':'USB-1','path':'x.mkv','name':'x','mode':'hls_copy','client':'','plan':{}}})
    monkeypatch.setattr(streaming,'HLS_IDLE_SECONDS',10**12)
    text=streaming.manifest_text(token)
    assert f'/api/media/hls/segment?token={token}&name=init.mp4' in text
    assert f'/api/media/hls/segment?token={token}&name=seg-00001.m4s' in text
