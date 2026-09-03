from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path: str, old: str, new: str, expected: int) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} matches, found {count}: {old!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


replace_once("VERSION", "2.8.3\n", "2.8.4\n")
replace_once("app/main.py", 'VERSION = "2.8.3"', 'VERSION = "2.8.4"')
replace_once("app/static/sw.js", "livevault-shell-v2.8.3", "livevault-shell-v2.8.4")
replace_once("README.md", "# LiveVault v2.8.3", "# LiveVault v2.8.4")
replace_once("START_HERE.md", "# LiveVault v2.8.3 — START HERE", "# LiveVault v2.8.4 — START HERE")

utils = ROOT / "app/utils.py"
text = utils.read_text(encoding="utf-8")
start = text.index("def generate_thumbnail(")
end = text.index("\n\ndef human_bytes", start)
new_function = '''def generate_thumbnail(path: Path, output: Path, duration: float | None = None) -> bool:
    """Create a 3x3 storyboard from nine evenly spaced moments.

    Input-side seeks keep this inexpensive for long recordings. A single-frame
    extraction remains as a fallback for unusual media. Writing to a temporary
    file keeps regeneration atomic.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if duration is None or not math.isfinite(duration) or duration <= 0:
        duration = _probe_duration(path)

    seek = 0.5
    seeks: list[float] = []
    if duration and math.isfinite(duration) and duration > 0:
        safe_end = max(0.0, duration - min(1.0, duration * 0.03))
        seeks = [safe_end * fraction for fraction in (0.05, 0.16, 0.27, 0.38, 0.50, 0.62, 0.73, 0.84, 0.95)]
        seek = min(30.0, max(0.05, duration * 0.2))

    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.stem}-",
        suffix=output.suffix or ".jpg",
        delete=False,
    ) as temporary:
        candidate = Path(temporary.name)

    try:
        if seeks:
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
            for position in seeks:
                command.extend(["-ss", f"{position:.3f}", "-i", str(path)])
            cells = [
                f"[{index}:v:0]scale=320:180:force_original_aspect_ratio=decrease,"
                f"pad=320:180:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v{index}]"
                for index in range(9)
            ]
            filters = ";".join(cells + [
                "[v0][v1][v2]hstack=inputs=3[row0]",
                "[v3][v4][v5]hstack=inputs=3[row1]",
                "[v6][v7][v8]hstack=inputs=3[row2]",
                "[row0][row1][row2]vstack=inputs=3[sheet]",
            ])
            command.extend([
                "-filter_complex", filters, "-map", "[sheet]", "-an",
                "-frames:v", "1", "-update", "1", "-pix_fmt", "yuvj420p",
                "-q:v", "4", str(candidate),
            ])
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=120, check=False,
            )
            if result.returncode == 0 and candidate.exists() and candidate.stat().st_size > 0:
                candidate.replace(output)
                return True

        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{seek:.2f}", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=960:540:force_original_aspect_ratio=decrease,"
                "pad=960:540:(ow-iw)/2:(oh-ih)/2:color=black",
                "-an", "-update", "1", "-pix_fmt", "yuvj420p",
                "-q:v", "4", str(candidate),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0 and candidate.exists() and candidate.stat().st_size > 0:
            candidate.replace(output)
            return True
    except Exception:
        pass
    candidate.unlink(missing_ok=True)
    return False
'''
utils.write_text(text[:start] + new_function + text[end:], encoding="utf-8")

replace_all("app/workers.py", "-sheet-v1.jpg", "-sheet-v2.jpg", 2)

old = '''                recording_items = [row[0] for row in overlapping]\n                live_seconds = max(0.0, (ended - started).total_seconds())'''
new = '''                recording_items = [row[0] for row in overlapping]\n                recording_segments = [\n                    {\n                        "id": int(recording.id),\n                        "started_at": _iso_utc(rec_start),\n                        "ended_at": _iso_utc(rec_end),\n                        "filename": str(recording.filename or ""),\n                        "upload_provider": str(recording.upload_provider or ""),\n                        "remote_url": str(recording.remote_url or ""),\n                        "thumbnail_url": _safe_thumbnail_url(int(recording.id), str(recording.thumbnail_path or "")),\n                    }\n                    for recording, rec_start, rec_end in overlapping\n                ]\n                live_seconds = max(0.0, (ended - started).total_seconds())'''
replace_once("app/main.py", old, new)

old = '''                    "recording_intervals": [\n                        {"started_at": _iso_utc(row["started"]), "ended_at": _iso_utc(row["ended"])}\n                        for row in merged_recordings\n                    ],\n                    "coverage_percent": round(coverage, 1),'''
new = '''                    "recording_intervals": [\n                        {"started_at": _iso_utc(row["started"]), "ended_at": _iso_utc(row["ended"])}\n                        for row in merged_recordings\n                    ],\n                    "recordings": recording_segments,\n                    "coverage_percent": round(coverage, 1),'''
replace_once("app/main.py", old, new)

old = '''function pulseRecordingIntervals(session) {\n  return Array.isArray(session?.recording_intervals) ? session.recording_intervals.filter(row => timestamp(row?.started_at) && timestamp(row?.ended_at)) : [];\n}\n'''
new = '''function pulseRecordingIntervals(session) {\n  return Array.isArray(session?.recording_intervals) ? session.recording_intervals.filter(row => timestamp(row?.started_at) && timestamp(row?.ended_at)) : [];\n}\n\nfunction pulseRecordingFiles(session) {\n  const files = Array.isArray(session?.recordings) ? session.recordings : [];\n  if (files.length) return files.filter(row => timestamp(row?.started_at) && timestamp(row?.ended_at));\n  return pulseRecordingIntervals(session);\n}\n\nfunction ensurePulseMediaPreview() {\n  let node = $('#crPulseMediaPreview');\n  if (node) return node;\n  node = document.createElement('div');\n  node.id = 'crPulseMediaPreview';\n  node.className = 'cr-pulse-media-preview';\n  node.setAttribute('aria-hidden', 'true');\n  document.body.append(node);\n  return node;\n}\n\nfunction showPulseMediaPreview(target) {\n  const previewUrl = safeUrl(target?.dataset?.previewUrl || '');\n  if (!previewUrl) return;\n  const node = ensurePulseMediaPreview();\n  const title = target.dataset.previewTitle || 'REC';\n  const meta = target.dataset.previewMeta || '';\n  node.innerHTML = `<img src="${esc(previewUrl)}" alt=""><div><strong>${esc(title)}</strong>${meta ? `<span>${esc(meta)}</span>` : ''}</div>`;\n  node.classList.add('visible');\n  node.setAttribute('aria-hidden', 'false');\n}\n\nfunction hidePulseMediaPreview() {\n  const node = $('#crPulseMediaPreview');\n  if (!node) return;\n  node.classList.remove('visible');\n  node.setAttribute('aria-hidden', 'true');\n}\n\ndocument.addEventListener('pointerover', event => {\n  const target = event.target.closest?.('.cr-pulse-rec-media');\n  if (target) showPulseMediaPreview(target);\n});\n\ndocument.addEventListener('pointerout', event => {\n  const target = event.target.closest?.('.cr-pulse-rec-media');\n  if (!target || target.contains(event.relatedTarget)) return;\n  hidePulseMediaPreview();\n});\n'''
replace_once("app/static/app.js", old, new)

old = '''      const recs = pulseRecordingIntervals(session).map(rec => {\n        const recStart = Math.max(start, timestamp(rec.started_at));\n        const recEnd = Math.min(end, timestamp(rec.ended_at));\n        if (!recStart || recEnd <= recStart) return '';\n        const recX = xFor(recStart);\n        const recWidth = widthFor(recStart, recEnd, compact ? 12 : 4);\n        return `<rect class="cr-pulse-rec-span" x="${recX.toFixed(3)}" y="4" width="${recWidth.toFixed(3)}" height="8" rx="4" ry="4"></rect>`;\n      }).join('');\n      const firstRec = pulseRecordingIntervals(session)[0];\n      const recMarkerX = firstRec ? xFor(Math.max(start, timestamp(firstRec.started_at))) : null;\n      return `<g class="cr-pulse-session" data-profile-link="${session.representative_source_id || 0}"><rect class="cr-pulse-live-span ${session.state === 'live' ? 'current' : ''} ${session.state === 'missed' ? 'missed' : ''}" x="${x.toFixed(3)}" y="2" width="${liveWidth.toFixed(3)}" height="12" rx="6" ry="6"></rect><line class="cr-pulse-live-marker" x1="${x.toFixed(3)}" y1="0" x2="${x.toFixed(3)}" y2="16"></line>${recMarkerX === null ? '' : `<line class="cr-pulse-rec-marker" x1="${recMarkerX.toFixed(3)}" y1="1" x2="${recMarkerX.toFixed(3)}" y2="15"></line>`}${recs}<title>${esc(title)}</title></g>`;'''
new = '''      const recs = pulseRecordingFiles(session).map(rec => {\n        const recStart = Math.max(start, timestamp(rec.started_at));\n        const recEnd = Math.min(end, timestamp(rec.ended_at));\n        if (!recStart || recEnd <= recStart) return '';\n        const recX = xFor(recStart);\n        const recWidth = widthFor(recStart, recEnd, compact ? 12 : 4);\n        const remoteUrl = safeUrl(rec.remote_url || '');\n        const previewUrl = safeUrl(rec.thumbnail_url || '');\n        const provider = String(rec.upload_provider || '').toUpperCase();\n        const filename = String(rec.filename || 'REC');\n        const meta = `${provider || 'REC'} · ${pulseRangeLabel(rec.started_at, rec.ended_at, false)}`;\n        const rect = `<rect class="cr-pulse-rec-span ${remoteUrl ? 'remote' : ''}" x="${recX.toFixed(3)}" y="4" width="${recWidth.toFixed(3)}" height="8" rx="4" ry="4"></rect>`;\n        const attrs = `class="cr-pulse-rec-media" data-preview-url="${esc(previewUrl)}" data-preview-title="${esc(filename)}" data-preview-meta="${esc(meta)}"`;\n        if (remoteUrl) return `<a ${attrs} href="${esc(remoteUrl)}" target="_blank" rel="noopener noreferrer">${rect}<title>${esc(filename)} · ${esc(meta)}</title></a>`;\n        return `<g ${attrs}>${rect}<title>${esc(filename)} · ${esc(meta)}</title></g>`;\n      }).join('');\n      const firstRec = pulseRecordingFiles(session)[0];\n      const recMarkerX = firstRec ? xFor(Math.max(start, timestamp(firstRec.started_at))) : null;\n      return `<g class="cr-pulse-session"><rect class="cr-pulse-live-span ${session.state === 'live' ? 'current' : ''} ${session.state === 'missed' ? 'missed' : ''}" x="${x.toFixed(3)}" y="2" width="${liveWidth.toFixed(3)}" height="12" rx="6" ry="6"></rect><line class="cr-pulse-live-marker" x1="${x.toFixed(3)}" y1="0" x2="${x.toFixed(3)}" y2="16"></line>${recMarkerX === null ? '' : `<line class="cr-pulse-rec-marker" x1="${recMarkerX.toFixed(3)}" y1="1" x2="${recMarkerX.toFixed(3)}" y2="15"></line>`}${recs}<title>${esc(title)}</title></g>`;'''
replace_once("app/static/app.js", old, new)

css = ROOT / "app/static/enhancements.css"
with css.open("a", encoding="utf-8") as fh:
    fh.write('''\n\n/* LiveVault Pulse media preview v2.8.4 */\n.cr-pulse-rec-media{cursor:pointer}.cr-pulse-rec-span.remote{stroke:rgba(255,255,255,.58);stroke-width:1.2}.cr-pulse-rec-media:hover .cr-pulse-rec-span{filter:brightness(1.3)}\n.cr-pulse-media-preview{position:fixed;right:18px;bottom:18px;z-index:4200;width:min(460px,calc(100vw - 36px));border:1px solid rgba(255,255,255,.16);border-radius:14px;background:#0b0e0f;box-shadow:0 22px 60px rgba(0,0,0,.55);overflow:hidden;opacity:0;visibility:hidden;transform:translateY(8px);transition:opacity .12s ease,transform .12s ease,visibility .12s;pointer-events:none}\n.cr-pulse-media-preview.visible{opacity:1;visibility:visible;transform:none}.cr-pulse-media-preview img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;background:#050606}.cr-pulse-media-preview>div{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 11px}.cr-pulse-media-preview strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.76rem}.cr-pulse-media-preview span{flex:0 0 auto;color:var(--muted);font-size:.66rem}\n@media(max-width:620px){.cr-pulse-media-preview{right:10px;bottom:10px;width:calc(100vw - 20px)}}\n''')

test = ROOT / "tests/test_v284_pulse_media.py"
test.write_text('''from pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_storyboard_is_nine_frame_v2():\n    utils = (ROOT / "app/utils.py").read_text(encoding="utf-8")\n    workers = (ROOT / "app/workers.py").read_text(encoding="utf-8")\n    assert "Create a 3x3 storyboard from nine evenly spaced moments" in utils\n    assert "for index in range(9)" in utils\n    assert "[v6][v7][v8]hstack=inputs=3[row2]" in utils\n    assert "[row0][row1][row2]vstack=inputs=3[sheet]" in utils\n    assert workers.count("-sheet-v2.jpg") == 2\n    assert "-sheet-v1.jpg" not in workers\n\n\ndef test_pulse_exposes_exact_recording_media():\n    main = (ROOT / "app/main.py").read_text(encoding="utf-8")\n    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")\n    css = (ROOT / "app/static/enhancements.css").read_text(encoding="utf-8")\n    for token in ('"recordings": recording_segments', '"remote_url": str(recording.remote_url or "")', '"thumbnail_url": _safe_thumbnail_url'):\n        assert token in main\n    for token in ("pulseRecordingFiles", "cr-pulse-rec-media", "data-preview-url", 'target="_blank"', "ensurePulseMediaPreview"):\n        assert token in js\n    assert ".cr-pulse-media-preview" in css\n    assert "position:fixed" in css\n\n\ndef test_release_is_v284():\n    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "2.8.4"\n    assert 'VERSION = "2.8.4"' in (ROOT / "app/main.py").read_text(encoding="utf-8")\n    assert "livevault-shell-v2.8.4" in (ROOT / "app/static/sw.js").read_text(encoding="utf-8")\n''', encoding="utf-8")

changelog = ROOT / "CHANGELOG.md"
text = changelog.read_text(encoding="utf-8")
anchor = "# Changelog\n"
entry = '''# Changelog\n\n## 2.8.4\n- Live Pulse: hover sui segmenti REC con storyboard 3x3 a 9 frame.\n- Ogni segmento REC rappresenta il file reale e apre il relativo link Gofile/PixelDrain.\n- API Pulse espone URL remoto, provider e thumbnail per ciascuna registrazione.\n- Backfill automatico dei vecchi storyboard 2x2 quando il file locale è ancora disponibile.\n'''
if not text.startswith(anchor):
    raise RuntimeError("CHANGELOG.md: unexpected header")
changelog.write_text(entry + text[len(anchor):], encoding="utf-8")

print("v2.8.4 Pulse media patch applied")
