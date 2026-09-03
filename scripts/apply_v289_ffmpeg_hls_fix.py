from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# FFmpeg builds differ in how protocol-private options are accepted when the
# top-level input is a local HLS master.  Applying HTTP reconnect options to
# that local file can fail before the nested HTTP playlists are opened.
replace(
    "app/recorder.py",
    '''        cmd += [
            "-fflags", "+genpts+discardcorrupt",
            "-dts_delta_threshold", "1",
            "-thread_queue_size", "8192",
            "-rw_timeout", "15000000",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        ]
        if synchronized_hls:
            cmd += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto,data"]
''',
    '''        cmd += [
            "-fflags", "+genpts+discardcorrupt",
            "-dts_delta_threshold", "1",
            "-thread_queue_size", "8192",
            "-rw_timeout", "15000000",
        ]
        # reconnect* are HTTP protocol options.  Some distro FFmpeg builds
        # reject them when the top-level input is our local synchronized HLS
        # master ("Option reconnect not found") before opening its remote
        # child playlists.  Direct HTTP(S) inputs still keep the reconnect
        # policy; synchronized local HLS relies on FFmpeg's HLS reload logic
        # plus LiveVault's transport guard/restart path.
        if item.url.lower().startswith(("http://", "https://")):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        if synchronized_hls:
            cmd += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto,data"]
''',
)

replace("VERSION", "2.8.8\n", "2.8.9\n")
replace("app/main.py", 'VERSION = "2.8.8"', 'VERSION = "2.8.9"')
replace("app/static/sw.js", "livevault-shell-v2.8.8", "livevault-shell-v2.8.9")
replace("README.md", "# LiveVault v2.8.8", "# LiveVault v2.8.9")
replace("START_HERE.md", "# LiveVault v2.8.8 — START HERE", "# LiveVault v2.8.9 — START HERE")
replace("tests/test_version_consistency.py", 'assert version == "2.8.8"', 'assert version == "2.8.9"')

changelog = ROOT / "CHANGELOG.md"
text = changelog.read_text(encoding="utf-8")
needle = "# Changelog\n\n"
entry = '''# Changelog\n\n## 2.8.9 — FFmpeg HLS compatibility\n\n- Il master HLS sincronizzato locale non riceve più opzioni `reconnect*` HTTP incompatibili con alcune build FFmpeg.\n- Gli input HTTP(S) diretti mantengono reconnect e retry di rete.\n- Il percorso LL-HLS sincronizzato continua a usare il transport guard di LiveVault per riavviare la cattura in caso di sessione/segmenti invalidati.\n\n'''
if needle not in text:
    raise SystemExit("CHANGELOG header not found")
changelog.write_text(text.replace(needle, entry, 1), encoding="utf-8")
