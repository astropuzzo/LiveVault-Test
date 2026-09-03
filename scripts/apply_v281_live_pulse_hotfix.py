from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing expected text in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Version bump also invalidates the service-worker shell cache on phones/PWA.
replace("VERSION", "2.8.0\n", "2.8.1\n")
replace("app/main.py", 'VERSION = "2.8.0"', 'VERSION = "2.8.1"')
replace("app/static/sw.js", "livevault-shell-v2.8.0", "livevault-shell-v2.8.1")
replace("README.md", "# LiveVault v2.8.0", "# LiveVault v2.8.1")
replace("START_HERE.md", "# LiveVault v2.8.0 — START HERE", "# LiveVault v2.8.1 — START HERE")
replace("tests/test_version_consistency.py", 'assert version == "2.8.0"', 'assert version == "2.8.1"')

changelog = ROOT / "CHANGELOG.md"
text = changelog.read_text(encoding="utf-8")
entry = """## 2.8.1 — 2026-09-03\n\n- Hotfix **Live Pulse mobile**: asse temporale leggibile, tre tick su schermi stretti e massimo cinque creator visibili.\n- Righe più alte, nomi separati dalla timeline e segmenti brevi con larghezza minima maggiore.\n- Finestra temporale resa robusta rispetto a timestamp incoerenti e cache PWA invalidata.\n\n"""
if "## 2.8.1 —" not in text:
    text = text.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
    changelog.write_text(text, encoding="utf-8")

app_js = ROOT / "app/static/app.js"
text = app_js.read_text(encoding="utf-8")
old = """function controlRoomPulseMarkup() {\n  const sessions = pulseSessions();\n  const windowStart = timestamp(controlRoomPulseData.window_start) || (Date.now() - 12 * 3600000);\n  const generatedAt = timestamp(controlRoomPulseData.generated_at) || Date.now();\n  const span = Math.max(1, generatedAt - windowStart);\n  const profileOrder = [];\n  const byProfile = new Map();\n  for (const session of [...sessions].reverse()) {\n    const profileId = Number(session.profile_id);\n    if (!byProfile.has(profileId)) { byProfile.set(profileId, []); profileOrder.push(profileId); }\n    byProfile.get(profileId).push(session);\n  }\n  const recentProfiles = profileOrder.slice(-8).reverse();\n  const labels = [0, .25, .5, .75, 1].map(ratio => {\n    const value = new Date(windowStart + span * ratio);\n    return `<span style=\"left:${ratio * 100}%\">${esc(new Intl.DateTimeFormat('it-IT', {hour:'2-digit', minute:'2-digit'}).format(value))}</span>`;\n  }).join('');\n"""
new = """function controlRoomPulseMarkup() {\n  const sessions = pulseSessions();\n  const compact = window.matchMedia('(max-width: 620px)').matches;\n  const hours = Math.max(1, Number(controlRoomPulseData.hours) || 12);\n  const generatedAt = timestamp(controlRoomPulseData.generated_at) || Date.now();\n  const expectedWindowStart = generatedAt - hours * 3600000;\n  const apiWindowStart = timestamp(controlRoomPulseData.window_start);\n  const expectedSpan = hours * 3600000;\n  const apiSpan = apiWindowStart ? generatedAt - apiWindowStart : 0;\n  const windowStart = apiWindowStart && Math.abs(apiSpan - expectedSpan) <= 15 * 60000\n    ? apiWindowStart\n    : expectedWindowStart;\n  const span = Math.max(1, generatedAt - windowStart);\n  const profileOrder = [];\n  const byProfile = new Map();\n  for (const session of [...sessions].reverse()) {\n    const profileId = Number(session.profile_id);\n    if (!byProfile.has(profileId)) { byProfile.set(profileId, []); profileOrder.push(profileId); }\n    byProfile.get(profileId).push(session);\n  }\n  const maxProfiles = compact ? 5 : 8;\n  const recentProfiles = profileOrder.slice(-maxProfiles).reverse();\n  const labelRatios = compact ? [0, .5, 1] : [0, .25, .5, .75, 1];\n  const labels = labelRatios.map(ratio => {\n    const value = new Date(windowStart + span * ratio);\n    return `<span style=\"left:${ratio * 100}%\">${esc(new Intl.DateTimeFormat('it-IT', {hour:'2-digit', minute:'2-digit'}).format(value))}</span>`;\n  }).join('');\n"""
if old not in text:
    raise SystemExit("Live Pulse markup block not found")
text = text.replace(old, new, 1)
text = text.replace("/* LiveVault Live Intelligence v2.8.0 */", "/* LiveVault Live Intelligence v2.8.1 */", 1)
app_js.write_text(text, encoding="utf-8")

css = ROOT / "app/static/enhancements.css"
text = css.read_text(encoding="utf-8")
marker = "/* LiveVault Live Pulse mobile hotfix v2.8.1 */"
if marker not in text:
    text += """\n\n/* LiveVault Live Pulse mobile hotfix v2.8.1 */\n.cr-pulse-scale>div,.cr-pulse-track{min-width:0}\n@media(max-width:620px){\n  .cr-pulse{padding:14px 12px 13px;margin-bottom:20px}\n  .cr-pulse-head{margin-bottom:10px}\n  .cr-pulse-head strong{font-size:.94rem}\n  .cr-pulse-head span{font-size:.74rem}\n  .cr-pulse-scale,.cr-pulse-row{grid-template-columns:96px minmax(0,1fr);gap:10px}\n  .cr-pulse-scale{height:24px;margin-bottom:3px}\n  .cr-pulse-scale>div span{bottom:6px;font-size:.66rem;line-height:1;white-space:nowrap}\n  .cr-pulse-row{min-height:34px;padding:2px 0}\n  .cr-pulse-name{font-size:.72rem;font-weight:700;line-height:1.15;padding:4px 0;max-width:100%}\n  .cr-pulse-track{height:11px;overflow:hidden;background:rgba(255,255,255,.045)}\n  .cr-pulse-track:after{top:-3px;height:17px}\n  .cr-pulse-block{height:11px;min-width:6px}\n}\n@media(max-width:400px){\n  .cr-pulse-scale,.cr-pulse-row{grid-template-columns:84px minmax(0,1fr);gap:9px}\n  .cr-pulse-name{font-size:.68rem}\n  .cr-pulse-scale>div span{font-size:.63rem}\n}\n"""
    css.write_text(text, encoding="utf-8")

ui_test = ROOT / "tests/test_live_pulse_ui.py"
ui_test.write_text("""from pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_live_pulse_mobile_has_compact_axis_and_spacing():\n    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')\n    css = (ROOT / 'app/static/enhancements.css').read_text(encoding='utf-8')\n    assert \"const compact = window.matchMedia('(max-width: 620px)').matches\" in js\n    assert \"const labelRatios = compact ? [0, .5, 1] : [0, .25, .5, .75, 1]\" in js\n    assert \"const maxProfiles = compact ? 5 : 8\" in js\n    assert \"Live Pulse mobile hotfix v2.8.1\" in css\n    assert \"grid-template-columns:96px minmax(0,1fr)\" in css\n    assert \"min-width:6px\" in css\n""", encoding="utf-8")
