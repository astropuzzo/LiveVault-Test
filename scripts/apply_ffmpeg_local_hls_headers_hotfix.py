from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise SystemExit(f"anchor missing in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# A local synthetic master is a file: input. HTTP AVOptions such as -headers
# and -reconnect must never be bound to it; some FFmpeg builds abort before
# they ever open the remote child playlists (Option headers/reconnect not found).
replace_once(
    "app/recorder.py",
    '''    headers = dict(video.http_headers)\n    for key, value in audio.http_headers.items():\n        headers.setdefault(key, value)\n    manifest_path.parent.mkdir(parents=True, exist_ok=True)\n''',
    '''    manifest_path.parent.mkdir(parents=True, exist_ok=True)\n''',
)
replace_once(
    "app/recorder.py",
    '''    return [ResolvedInput(str(manifest_path.resolve()), headers, "media")], manifest_path\n''',
    '''    # The child URLs are signed. Keep transport headers off the local\n    # synthetic master itself: FFmpeg associates input AVOptions with the\n    # top-level file: protocol and some distro builds reject HTTP-only options.\n    return [ResolvedInput(str(manifest_path.resolve()), {}, "media")], manifest_path\n''',
)
replace_once(
    "app/recorder.py",
    '''        # reconnect* are HTTP protocol options. Some distro FFmpeg builds\n        # reject them when the top-level input is our local synchronized HLS\n        # master ("Option reconnect not found") before opening its remote\n        # child playlists. Direct HTTP(S) inputs still keep the reconnect\n        # policy; synchronized local HLS relies on FFmpeg's HLS reload logic\n        # plus LiveVault's transport guard/restart path.\n        if item.url.lower().startswith(("http://", "https://")):\n            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]\n        if synchronized_hls:\n            cmd += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto,data"]\n        headers = _ffmpeg_headers(item.http_headers)\n        if headers:\n            cmd += ["-headers", headers]\n        cmd += ["-i", item.url]\n''',
    '''        # HTTP AVOptions must only be attached to a top-level HTTP(S)\n        # input. Our synchronized Chaturbate master is a local file containing\n        # signed remote child URLs; binding -headers/-reconnect to that file can\n        # make FFmpeg abort before recording starts (Option ... not found).\n        is_http_input = item.url.lower().startswith(("http://", "https://"))\n        if is_http_input:\n            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]\n        if synchronized_hls:\n            cmd += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto,data"]\n        if is_http_input:\n            headers = _ffmpeg_headers(item.http_headers)\n            if headers:\n                cmd += ["-headers", headers]\n        cmd += ["-i", item.url]\n''',
)

replace_once(
    "tests/test_recorder.py",
    '''from app.recorder import build_ffmpeg_command, max_output_bytes, safe_output_limit_bytes\n''',
    '''from app.recorder import (\n    build_chaturbate_synced_master,\n    build_ffmpeg_command,\n    max_output_bytes,\n    safe_output_limit_bytes,\n)\n''',
)

path = Path("tests/test_recorder.py")
text = path.read_text(encoding="utf-8")
marker = "def test_local_synchronized_hls_never_receives_http_avoptions"
if marker not in text:
    text += '''\n\ndef test_local_synchronized_hls_never_receives_http_avoptions():\n    cmd = build_ffmpeg_command(\n        [ResolvedInput(\n            "/data/recordings/test/.livevault-synced-master.m3u8",\n            {"User-Agent": "LiveVault-Test", "Referer": "https://chaturbate.com/"},\n            "media",\n        )],\n        Path("out_%03d.mp4"),\n        segment_minutes=10,\n        container_format="mp4",\n        synchronized_hls=True,\n    )\n    assert "-headers" not in cmd\n    assert "-reconnect" not in cmd\n    assert "-reconnect_streamed" not in cmd\n    assert "-reconnect_delay_max" not in cmd\n    assert "-protocol_whitelist" in cmd\n\n\ndef test_synced_master_drops_top_level_http_headers(tmp_path):\n    inputs = [\n        ResolvedInput(\n            "https://example.test/llhls/video.m3u8",\n            {"User-Agent": "UA", "Referer": "https://chaturbate.com/"},\n            "video",\n        ),\n        ResolvedInput(\n            "https://example.test/llhls/audio.m3u8",\n            {"User-Agent": "UA", "Referer": "https://chaturbate.com/"},\n            "audio",\n        ),\n    ]\n    synced, manifest = build_chaturbate_synced_master(inputs, tmp_path / "master.m3u8")\n    assert manifest.is_file()\n    assert synced[0].url == str(manifest.resolve())\n    assert synced[0].http_headers == {}\n\n\ndef test_direct_http_input_still_receives_headers_and_reconnect():\n    cmd = build_ffmpeg_command(\n        [ResolvedInput(\n            "https://example.test/master.m3u8",\n            {"User-Agent": "LiveVault-Test", "Referer": "https://example.test/"},\n            "media",\n        )],\n        Path("out_%03d.mp4"),\n        segment_minutes=10,\n        container_format="mp4",\n    )\n    assert "-headers" in cmd\n    assert "-reconnect" in cmd\n'''
    path.write_text(text, encoding="utf-8")

# Restore the normal workflow inside the verified commit and remove staging.
workflow = Path(".github/workflows/ci.yml")
workflow.write_text('''name: CI\n\non:\n  push:\n  pull_request:\n\nconcurrency:\n  group: livevault-${{ github.workflow }}-${{ github.ref }}\n  cancel-in-progress: true\n\njobs:\n  test:\n    name: Core tests\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.13'\n      - uses: actions/setup-node@v4\n        with:\n          node-version: '22'\n      - name: Install FFmpeg\n        run: sudo apt-get update && sudo apt-get install -y ffmpeg\n      - name: Install dependencies\n        run: pip install -r requirements-dev.txt\n      - name: Compile Python\n        run: python -m compileall -q app tests\n      - name: Run tests\n        run: PYTHONPATH=. pytest -q\n      - name: Check JavaScript\n        run: node --check app/static/app.js\n      - name: Check shell scripts\n        run: bash -n scripts/*.sh\n\n  deploy:\n    name: Deploy verified main\n    needs: test\n    if: github.event_name == 'push' && github.ref == 'refs/heads/main'\n    runs-on: ubuntu-latest\n    steps:\n      - name: Ask CapRover to deploy the latest verified main\n        env:\n          CAPROVER_DEPLOY_WEBHOOK: ${{ secrets.CAPROVER_DEPLOY_WEBHOOK }}\n        run: >-\n          curl --fail --show-error --silent\n          --retry 3 --retry-all-errors --max-time 30\n          --request POST "$CAPROVER_DEPLOY_WEBHOOK"\n''', encoding="utf-8")
Path(__file__).unlink()
print("FFmpeg local HLS headers hotfix applied")
