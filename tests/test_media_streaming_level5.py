from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1] / 'control-panel'
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


def _multitrack_probe():
    return {'probe': {'format': {'duration': '6077.312'}, 'streams': [
        {'index': 0, 'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1008},
        {'index': 1, 'codec_type': 'audio', 'codec_name': 'ac3', 'channels': 6, 'channel_layout': '5.1(side)', 'tags': {'title': 'Ita AC3 5.1', 'LANGUAGE': 'ita'}, 'disposition': {'default': 1}},
        {'index': 2, 'codec_type': 'audio', 'codec_name': 'ac3', 'channels': 6, 'channel_layout': '5.1(side)', 'tags': {'title': 'Eng AC3 5.1', 'LANGUAGE': 'eng'}, 'disposition': {'default': 0}},
        {'index': 3, 'codec_type': 'subtitle', 'codec_name': 'ass', 'tags': {'title': 'Forced ita', 'LANGUAGE': 'ita'}, 'disposition': {'forced': 1}},
        {'index': 4, 'codec_type': 'subtitle', 'codec_name': 'ass', 'tags': {'title': 'Ita', 'LANGUAGE': 'ita'}, 'disposition': {}},
        {'index': 5, 'codec_type': 'subtitle', 'codec_name': 'ass', 'tags': {'title': 'Eng', 'LANGUAGE': 'eng'}, 'disposition': {}},
    ]}}


def test_multitrack_mkv_exposes_audio_and_embedded_subtitles(monkeypatch):
    monkeypatch.setattr(streaming.media_center, 'file_info', lambda u,p: {'path':Path('/tmp/longlegs.mkv'),'name':'longlegs.mkv','category':'video'})
    monkeypatch.setattr(streaming.media_center, 'probe_file', lambda u,p: _multitrack_probe())
    result = streaming.playback_plan('USB-1', 'longlegs.mkv')
    assert [track['stream_index'] for track in result['audio_tracks']] == [1, 2]
    assert [track['label'] for track in result['audio_tracks']] == ['Ita AC3 5.1', 'Eng AC3 5.1']
    assert result['selected_audio_stream'] == 1
    assert [track['stream_index'] for track in result['subtitles'] if track['kind'] == 'embedded'] == [3, 4, 5]
    assert result['mode'] == 'hls_audio'


def test_selected_english_audio_is_preserved_in_plan(monkeypatch):
    monkeypatch.setattr(streaming.media_center, 'file_info', lambda u,p: {'path':Path('/tmp/longlegs.mkv'),'name':'longlegs.mkv','category':'video'})
    monkeypatch.setattr(streaming.media_center, 'probe_file', lambda u,p: _multitrack_probe())
    result = streaming.playback_plan('USB-1', 'longlegs.mkv', audio_stream=2)
    assert result['selected_audio_stream'] == 2
    assert result['audio_codec'] == 'ac3'
    assert result['mode'] == 'hls_audio'


def test_invalid_audio_stream_is_rejected(monkeypatch):
    monkeypatch.setattr(streaming.media_center, 'file_info', lambda u,p: {'path':Path('/tmp/longlegs.mkv'),'name':'longlegs.mkv','category':'video'})
    monkeypatch.setattr(streaming.media_center, 'probe_file', lambda u,p: _multitrack_probe())
    with pytest.raises(ValueError, match='Traccia audio non valida'):
        streaming.playback_plan('USB-1', 'longlegs.mkv', audio_stream=99)


def test_ass_parser_keeps_dialogue_text(tmp_path):
    source = tmp_path / 'sample.ass'
    target = tmp_path / 'sample.vtt'
    source.write_text('[Script Info]\nTitle: x\n[Events]\nFormat: Layer, Start, End, Style, Text\nDialogue: 0,0:03:43.38,0:03:44.88,Default,Cucù!\nDialogue: 0,0:04:39.80,0:04:42.68,Default,{\\i1}ho messo le mie gambe lunghe!{\\i0}\nDialogue: 0,0:06:10.75,0:06:14.29,Default,prima riga\\Nseconda riga\n', encoding='utf-8')
    streaming._ass_to_webvtt(source, target)
    text = target.read_text(encoding='utf-8')
    assert '00:03:43.380 --> 00:03:44.880' in text
    assert 'Cucù!' in text
    assert 'ho messo le mie gambe lunghe!' in text
    assert 'prima riga\nseconda riga' in text
