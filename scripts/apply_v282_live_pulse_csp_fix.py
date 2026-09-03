from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path}: {old[:100]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


app = ROOT / "app/static/app.js"
css = ROOT / "app/static/enhancements.css"
main = ROOT / "app/main.py"
sw = ROOT / "app/static/sw.js"
version = ROOT / "VERSION"
readme = ROOT / "README.md"
start = ROOT / "START_HERE.md"
changelog = ROOT / "CHANGELOG.md"
test = ROOT / "tests/test_live_pulse_ui.py"

replace_once(app, "/* LiveVault Live Intelligence v2.8.1 */", "/* LiveVault Live Intelligence v2.8.2 */")

replace_once(
    app,
    """  const labelRatios = compact ? [0, .5, 1] : [0, .25, .5, .75, 1];
  const labels = labelRatios.map(ratio => {
    const value = new Date(windowStart + span * ratio);
    return `<span style=\"left:${ratio * 100}%\">${esc(new Intl.DateTimeFormat('it-IT', {hour:'2-digit', minute:'2-digit'}).format(value))}</span>`;
  }).join('');""",
    """  const labelRatios = [0, .25, .5, .75, 1];
  const labels = labelRatios.map((ratio, index) => {
    const value = new Date(windowStart + span * ratio);
    return `<span class=\"cr-pulse-tick cr-pulse-tick-${index}\">${esc(new Intl.DateTimeFormat('it-IT', {hour:'2-digit', minute:'2-digit'}).format(value))}</span>`;
  }).join('');""",
)

replace_once(
    app,
    """      const left = Math.max(0, Math.min(100, (start - windowStart) / span * 100));
      const width = Math.max(.65, Math.min(100 - left, (end - start) / span * 100));
      const title = `${session.display_name} · ${pulseTimeLabel(session.started_at)}${session.ended_at ? `–${pulseTimeLabel(session.ended_at)}` : '–ora'} · ${Math.round(Number(session.coverage_percent) || 0)}% REC`;
      return `<button class=\"cr-pulse-block ${pulseBlockClass(session)}\" style=\"left:${left.toFixed(3)}%;width:${width.toFixed(3)}%\" data-profile-link=\"${session.representative_source_id || 0}\" type=\"button\" title=\"${esc(title)}\" aria-label=\"${esc(title)}\"></button>`;
    }).join('');
    return `<div class=\"cr-pulse-row\"><button class=\"creator-link cr-pulse-name\" data-profile-link=\"${representative.representative_source_id || 0}\" type=\"button\">${esc(representative.display_name)}</button><div class=\"cr-pulse-track\">${blocks}</div></div>`;""",
    """      const x = Math.max(0, Math.min(1000, (start - windowStart) / span * 1000));
      const rawWidth = Math.max(0, (end - start) / span * 1000);
      const minWidth = compact ? 24 : 6;
      const width = Math.max(0, Math.min(1000 - x, Math.max(minWidth, rawWidth)));
      const title = `${session.display_name} · ${pulseTimeLabel(session.started_at)}${session.ended_at ? `–${pulseTimeLabel(session.ended_at)}` : '–ora'} · ${Math.round(Number(session.coverage_percent) || 0)}% REC`;
      return `<rect class=\"cr-pulse-block ${pulseBlockClass(session)}\" x=\"${x.toFixed(3)}\" y=\"1\" width=\"${width.toFixed(3)}\" height=\"10\" rx=\"5\" ry=\"5\" data-profile-link=\"${session.representative_source_id || 0}\" aria-label=\"${esc(title)}\"><title>${esc(title)}</title></rect>`;
    }).join('');
    return `<div class=\"cr-pulse-row\"><button class=\"creator-link cr-pulse-name\" data-profile-link=\"${representative.representative_source_id || 0}\" type=\"button\">${esc(representative.display_name)}</button><div class=\"cr-pulse-track\"><svg class=\"cr-pulse-svg\" viewBox=\"0 0 1000 12\" preserveAspectRatio=\"none\" aria-hidden=\"true\">${blocks}</svg></div></div>`;""",
)

css_text = css.read_text(encoding="utf-8")
css_text += """

/* LiveVault Live Pulse CSP-safe hotfix v2.8.2 */
.cr-pulse-scale>div{position:relative;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));align-items:end;min-width:0;border-bottom:1px solid rgba(255,255,255,.07)}
.cr-pulse-scale>div .cr-pulse-tick{position:static;display:block;transform:none;min-width:0;padding:0 3px 4px;font-size:.62rem;line-height:1;color:var(--muted);text-align:center;white-space:nowrap}
.cr-pulse-scale>div .cr-pulse-tick:first-child{text-align:left}.cr-pulse-scale>div .cr-pulse-tick:last-child{text-align:right}
.cr-pulse-track{height:12px;position:relative;min-width:0;border-radius:999px;background:rgba(255,255,255,.04);overflow:hidden}
.cr-pulse-svg{display:block;width:100%;height:12px;overflow:hidden}
.cr-pulse-block{position:static;min-width:0;height:auto;padding:0;border:0;border-radius:0;background:none;box-shadow:none;fill:#58615b;stroke:none;cursor:pointer;vector-effect:non-scaling-stroke}
.cr-pulse-block.live{background:none;box-shadow:none;fill:#dc3847;filter:drop-shadow(0 0 2px rgba(220,56,71,.48))}.cr-pulse-block.rec{outline:none;stroke:rgba(255,255,255,.55);stroke-width:1}.cr-pulse-block.saved{background:none;fill:#758b76}.cr-pulse-block.ended{background:none;fill:#707671}.cr-pulse-block.missed{background:none;fill:#c18a35}.cr-pulse-block:hover{opacity:.9}
@media(max-width:620px){
  .cr-pulse-scale>div{grid-template-columns:repeat(3,minmax(0,1fr))}
  .cr-pulse-scale>div .cr-pulse-tick{padding-bottom:6px;font-size:.66rem}
  .cr-pulse-tick-1,.cr-pulse-tick-3{display:none!important}
  .cr-pulse-track,.cr-pulse-svg{height:12px}
}
"""
css.write_text(css_text, encoding="utf-8")

replace_once(main, 'VERSION = "2.8.1"', 'VERSION = "2.8.2"')
replace_once(sw, "livevault-shell-v2.8.1", "livevault-shell-v2.8.2")
version.write_text("2.8.2\n", encoding="utf-8")
replace_once(readme, "# LiveVault v2.8.1", "# LiveVault v2.8.2")
replace_once(start, "# LiveVault v2.8.1 — START HERE", "# LiveVault v2.8.2 — START HERE")

changelog_text = changelog.read_text(encoding="utf-8")
entry = """# Changelog

## 2.8.2 — 2026-09-03

- Corretto **Live Pulse** sotto CSP stretta: rimossi gli inline style bloccati da `style-src 'self'`.
- Tick temporali ora distribuiti con CSS Grid; sessioni renderizzate in SVG con coordinate native `x`/`width`.
- La CSP non viene indebolita; cache PWA aggiornata per forzare il nuovo frontend.

"""
if not changelog_text.startswith("# Changelog\n"):
    raise SystemExit("unexpected changelog header")
changelog.write_text(entry + changelog_text[len("# Changelog\n\n"):], encoding="utf-8")

test.write_text("""from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pulse_function(js: str) -> str:
    start = js.index('function controlRoomPulseMarkup()')
    end = js.index('function controlRoomRecentEnded', start)
    return js[start:end]


def test_live_pulse_is_csp_safe_and_uses_real_geometry():
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    css = (ROOT / 'app/static/enhancements.css').read_text(encoding='utf-8')
    main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
    pulse = pulse_function(js)

    assert 'style=' not in pulse
    assert 'const labelRatios = [0, .25, .5, .75, 1]' in pulse
    assert '<svg class=\"cr-pulse-svg\"' in pulse
    assert 'viewBox=\"0 0 1000 12\"' in pulse
    assert '<rect class=\"cr-pulse-block' in pulse
    assert 'x=\"${x.toFixed(3)}\"' in pulse
    assert 'width=\"${width.toFixed(3)}\"' in pulse
    assert 'const maxProfiles = compact ? 5 : 8' in pulse

    assert 'Live Pulse CSP-safe hotfix v2.8.2' in css
    assert 'grid-template-columns:repeat(5,minmax(0,1fr))' in css
    assert 'grid-template-columns:repeat(3,minmax(0,1fr))' in css
    assert '.cr-pulse-tick-1,.cr-pulse-tick-3{display:none!important}' in css
    assert '.cr-pulse-svg{display:block;width:100%;height:12px' in css

    assert \"style-src 'self'\" in main
    assert \"style-src 'self' 'unsafe-inline'\" not in main
""", encoding="utf-8")

print("v2.8.2 Live Pulse CSP-safe patch applied")
