from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_required(path: str, old: str, new: str, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    found = text.count(old)
    if found < count:
        raise RuntimeError(f"{path}: expected at least {count} occurrence(s), found {found}: {old[:120]!r}")
    target.write_text(text.replace(old, new, count), encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


# --- Static HTML: remove redundant headings, explanatory subtitles and obvious helper prose.
index_replacements = [
    (
        '      <div class="eyebrow">PRIVATE REMOTE RECORDER</div>\n      <h1>LiveVault</h1>\n      <p>Registrazione, archivio e distribuzione cloud in un unico spazio privato.</p>',
        '      <h1>LiveVault</h1>',
    ),
    (
        '          <div><div class="eyebrow">CONTROLLO</div><h1>Dashboard</h1><p id="lastRefresh">Caricamento…</p></div>',
        '          <div><h1>Dashboard</h1><p id="lastRefresh">Caricamento…</p></div>',
    ),
    (
        '          <div class="section-head"><div><div class="eyebrow">MONITOR</div><h2>Sorgenti</h2><p>Stato operativo e controlli rapidi.</p></div><span id="sourceCount" class="count">0</span></div>',
        '          <div class="section-head"><div><h2>Sorgenti</h2></div><span id="sourceCount" class="count">0</span></div>',
    ),
    (
        '          <div class="section-head"><div><div class="eyebrow danger-text">DIAGNOSTICA</div><h2>Errori recenti</h2></div><span id="diagnosticCount" class="count danger">0</span></div>',
        '          <div class="section-head"><div><h2>Errori recenti</h2></div><span id="diagnosticCount" class="count danger">0</span></div>',
    ),
    (
        '          <div><div class="eyebrow">ORGANIZZAZIONE</div><h1>Libreria</h1><p>Un profilo per persona, anche quando usa più provider.</p></div>',
        '          <div><h1>Libreria</h1></div>',
    ),
    (
        '          <div class="section-head"><div><div class="eyebrow">STRUTTURA</div><h2>Categorie e raccolte libreria</h2><p>Organizzazione editoriale. Le cartelle Gofile restano separate nelle impostazioni sorgente.</p></div><button id="closeLibraryManager" class="btn quiet" type="button">Chiudi</button></div>',
        '          <div class="section-head"><div><h2>Categorie e raccolte</h2></div><button id="closeLibraryManager" class="btn quiet" type="button">Chiudi</button></div>',
    ),
    (
        '          <div><div class="eyebrow">ANALISI</div><h1>Statistiche</h1><p>Tempo online, copertura registrazioni e andamento delle creator.</p></div>',
        '          <div><h1>Statistiche</h1></div>',
    ),
    (
        '<article class="panel stats-chart-card"><div class="section-head"><div><div class="eyebrow">ANDAMENTO</div><h2>Online vs registrato</h2><p>Tempo rilevato online e tempo effettivamente acquisito.</p></div><div class="chart-legend">',
        '<article class="panel stats-chart-card"><div class="section-head"><div><h2>Online vs registrato</h2></div><div class="chart-legend">',
    ),
    (
        '<article class="panel stats-chart-card"><div class="section-head"><div><div class="eyebrow">ORARI</div><h2>Distribuzione giornaliera</h2><p>In quali ore le creator risultano più spesso online.</p></div></div><div id="statisticsHourlyChart"',
        '<article class="panel stats-chart-card"><div class="section-head"><div><h2>Distribuzione giornaliera</h2></div></div><div id="statisticsHourlyChart"',
    ),
    (
        '<section class="panel section"><div class="section-head"><div><div class="eyebrow">CREATOR</div><h2>Confronto</h2><p>Ordinate per tempo online nel periodo selezionato.</p></div></div><div id="statisticsLeaderboard"',
        '<section class="panel section"><div class="section-head"><div><h2>Creator</h2></div></div><div id="statisticsLeaderboard"',
    ),
    (
        '          <div><div class="eyebrow">CONSERVAZIONE</div><h1>Archivio</h1><p>File locali, integrità e copie cloud.</p></div>',
        '          <div><h1>Archivio</h1></div>',
    ),
    (
        '          <div class="section-head"><div><h2>Registrazioni</h2><p>Miniature, anteprima locale, conversione e gestione upload.</p></div><span id="providerRoute" class="route">—</span></div>',
        '          <div class="section-head"><div><h2>Registrazioni</h2></div><span id="providerRoute" class="route">—</span></div>',
    ),
    (
        '      <div class="modal-head"><div><div class="eyebrow">SORGENTE</div><h2 id="sourceModalTitle">Aggiungi sorgente</h2></div><button class="icon-btn"',
        '      <div class="modal-head"><div><h2 id="sourceModalTitle">Aggiungi sorgente</h2></div><button class="icon-btn"',
    ),
    (
        '<label id="sourceProfileField" class="field"><span>Collega a profilo esistente (opzionale)</span><select id="sourceProfile"><option value="">Crea un nuovo profilo</option></select><small>Più account della stessa persona resteranno uniti nella Libreria.</small></label>',
        '<label id="sourceProfileField" class="field"><span>Profilo (opzionale)</span><select id="sourceProfile"><option value="">Nuovo profilo</option></select></label>',
    ),
    (
        '<label class="field"><span id="sourceInputLabel">Username Chaturbate o URL della live</span><input id="sourceSlug" maxlength="1000" placeholder="Username oppure https://..." required><small>Incolla il link: LiveVault sceglie l\'adapter disponibile.</small></label>',
        '<label class="field"><span id="sourceInputLabel">Username Chaturbate o URL della live</span><input id="sourceSlug" maxlength="1000" placeholder="Username oppure https://..." required></label>',
    ),
    (
        '<label class="field"><span>Gofile Folder ID esistente (opzionale)</span><input id="sourceGofileFolder" maxlength="200"><small>Vuoto = LiveVault crea automaticamente la cartella.</small></label>',
        '<label class="field"><span>Gofile Folder ID (opzionale)</span><input id="sourceGofileFolder" maxlength="200"></label>',
    ),
    (
        '      <div class="modal-head sticky"><div><div class="eyebrow">CONFIGURAZIONE</div><h2 id="settingsTitle">Impostazioni</h2><p>Le modifiche sono applicate in tempo reale.</p></div><button class="icon-btn"',
        '      <div class="modal-head sticky"><div><h2 id="settingsTitle">Impostazioni</h2></div><button class="icon-btn"',
    ),
    (
        '<div class="modal-head"><div><div class="eyebrow">ANTEPRIMA</div><h2 id="videoTitle">Video</h2></div><button class="icon-btn"',
        '<div class="modal-head"><div><h2 id="videoTitle">Video</h2></div><button class="icon-btn"',
    ),
]
for old, new in index_replacements:
    replace_required("app/static/index.html", old, new)


# --- JS: compact status copy, remove tutorial-like sentences, keep only data/actions/errors.
js_replacements = [
    ("/* LiveVault Control Room v2.7.0 */", "/* LiveVault Control Room v2.7.1 */"),
    ("return '<div class=\"empty compact\">Nessuna attività nel periodo.</div>';", "return '<div class=\"empty compact\">Nessun dato.</div>';"),
    ("return '<div class=\"empty compact\">Nessun dato orario disponibile.</div>';", "return '<div class=\"empty compact\">Nessun dato.</div>';"),
    ("['Tempo online', duration(summary.online_seconds || 0), `${summary.days_online || 0} giorni con live`],", "['Tempo online', duration(summary.online_seconds || 0), `${summary.days_online || 0} gg`],"),
    ("['Tempo registrato', duration(summary.recorded_seconds || 0), `${summary.recording_sessions || 0} sessioni REC`],", "['Tempo registrato', duration(summary.recorded_seconds || 0), `${summary.recording_sessions || 0} REC`],"),
    ("['Sessioni live', summary.live_sessions || 0, `più lunga ${duration(summary.longest_live_seconds || 0)}`],", "['Sessioni live', summary.live_sessions || 0, `max ${duration(summary.longest_live_seconds || 0)}`],"),
    ("['Copertura', statNumber(summary.coverage_percent || 0, '%'), 'registrato / online'],", "['Copertura', statNumber(summary.coverage_percent || 0, '%'), ''],"),
    ("['Live ora', summary.online_now || 0, compact ? 'stato corrente' : `${summary.creator_count || 0} creator monitorate`],", "['Live ora', summary.online_now || 0, compact ? '' : `${summary.creator_count || 0} creator`],"),
    ("function statisticsHistoryNote(data) {\n  const summary = data?.summary || {};\n  if (Number(summary.estimated_online_seconds) > 0) {\n    const exact = summary.exact_tracking_started_at ? ` Il tracciamento live completo è attivo dal ${dateFull(summary.exact_tracking_started_at)}.` : '';\n    return `Lo storico precedente è ricostruito dalle registrazioni e rappresenta una stima minima del tempo online.${exact}`;\n  }\n  return summary.exact_tracking_started_at ? `Tempo online misurato direttamente dalle rilevazioni LiveVault dal ${dateFull(summary.exact_tracking_started_at)}.` : 'Il tracciamento live inizierà alla prima rilevazione online.';\n}",
     "function statisticsHistoryNote(data) {\n  const summary = data?.summary || {};\n  return Number(summary.estimated_online_seconds) > 0 ? 'Storico pre-2.6: stima.' : '';\n}"),
    ("root.innerHTML = '<div class=\"empty\">Nessun profilo corrisponde ai filtri.</div>';", "root.innerHTML = '<div class=\"empty\">Nessun risultato.</div>';"),
    ("<div class=\"library-recency\">${profile.last_recording_at ? `Ultima registrazione ${esc(ago(profile.last_recording_at))}` : 'Nessuna registrazione'} · live ${esc(ago(profile.last_seen_live_at || profile.last_live_at))}</div>", "<div class=\"library-recency\">REC ${profile.last_recording_at ? esc(ago(profile.last_recording_at)) : '—'} · LIVE ${esc(ago(profile.last_seen_live_at || profile.last_live_at))}</div>"),
    (">Apri profilo</button>", ">Profilo</button>"),
    ("$('#profileProvider').textContent = 'PROFILO LIBRERIA';", "$('#profileProvider').textContent = '';"),
    ("$('#profileContent').innerHTML = '<div class=\"empty\">Caricamento profilo…</div>';", "$('#profileContent').innerHTML = '<div class=\"empty\">Caricamento…</div>';"),
    ("$('#profileReference').textContent = `${profile.linked_sources.length} ${profile.linked_sources.length === 1 ? 'sorgente collegata' : 'sorgenti collegate'} · creato ${dateText(profile.created_at)}`;", "$('#profileReference').textContent = `${profile.linked_sources.length} account · ${dateText(profile.created_at)}`;"),
    (".join('') || '<span class=\"muted\">Crea prima una categoria.</span>';", ".join('') || '<span class=\"muted\">Nessuna categoria.</span>';"),
    (".join('') || '<span class=\"muted\">Crea prima una raccolta.</span>';", ".join('') || '<span class=\"muted\">Nessuna raccolta.</span>';"),
    ("★ ${profile.favorite ? 'Preferita' : 'Aggiungi ai preferiti'}", "★ ${profile.favorite ? 'Preferita' : 'Preferiti'}"),
    ("<section class=\"profile-section profile-statistics\"><div class=\"profile-section-head\"><div><h3>Statistiche attività</h3><span>Online rilevato, registrazioni e copertura.</span></div>", "<section class=\"profile-section profile-statistics\"><div class=\"profile-section-head\"><div><h3>Statistiche</h3></div>"),
    ("<p class=\"stats-note\">${esc(statisticsHistoryNote(activity))}</p>", "${statisticsHistoryNote(activity) ? `<p class=\"stats-note\">${esc(statisticsHistoryNote(activity))}</p>` : ''}"),
    ("<section class=\"profile-section\"><div class=\"profile-section-head\"><h3>Identità e note</h3><span>Non modifica nomi tecnici, cartelle o storico.</span></div>", "<section class=\"profile-section\"><div class=\"profile-section-head\"><h3>Identità e note</h3></div>"),
    ("root.innerHTML = '<div class=\"empty\">Nessuna registrazione corrispondente.</div>';", "root.innerHTML = '<div class=\"empty\">Nessun risultato.</div>';"),
    ("$('#sourceFilterNote').textContent = selected ? `Archivio: ${selected.display_name || selected.name} · sorgente ${selected.name}` : '';", "$('#sourceFilterNote').textContent = selected ? `${selected.display_name || selected.name}` : '';"),
    ("output.textContent = 'Controllo provider, stato, audio e video…';", "output.textContent = 'Controllo…';"),
    ("output.textContent = `${response.provider_label} · ${statusLabel(response.status)} · tracce verificabili quando sarà online`;", "output.textContent = `${response.provider_label} · ${statusLabel(response.status)}`;"),
    ("const title = rows.length === 1 ? 'Creator LIVE non registrata' : `${rows.length} creator LIVE non registrate`;", "const title = rows.length === 1 ? 'LIVE · NON REC' : `${rows.length} LIVE · NON REC`;"),
    ("<small>${globallyPaused ? 'Registrazioni globali in pausa' : 'Registrazione in pausa per queste creator'}</small>", "<small>${globallyPaused ? 'PAUSA GLOBALE' : 'IN PAUSA'}</small>"),
    (">Riprendi registrazioni</button>", ">Riprendi REC</button>"),
    (" : '<div class=\"empty\">Nessun dato creator nel periodo.</div>';", " : '<div class=\"empty\">Nessun dato.</div>';"),
    ("const freshness = updated ? `Preview ${ago(updated)}` : profile.recording ? 'Preview in preparazione…' : 'Preview disponibile durante REC';", "const freshness = updated ? ago(updated) : '';"),
    ("    <span class=\"cr-preview-age\">${esc(freshness)}</span>", "    ${freshness ? `<span class=\"cr-preview-age\">${esc(freshness)}</span>` : ''}"),
    ("if (source.pause_reason === 'global') return 'LIVE · registrazioni globali in pausa';", "if (source.pause_reason === 'global') return 'LIVE · PAUSA GLOBALE';"),
    ("if (source.pause_reason === 'source') return 'LIVE · creator in pausa';", "if (source.pause_reason === 'source') return 'LIVE · IN PAUSA';"),
    ("return 'LIVE · REC non attiva';", "return 'LIVE · NON REC';"),
    ("return active ? `REC ${duration(active.elapsed_seconds)} · ${humanBytes(active.local_bytes || 0)}` : 'LIVE · REC attiva';", "return active ? `REC ${duration(active.elapsed_seconds)} · ${humanBytes(active.local_bytes || 0)}` : 'REC';"),
    ("return profile.live ? 'LIVE rilevata' : `Offline · ultima live ${ago(profile.last_seen_live_at)}`;", "return profile.live ? 'LIVE' : `Offline · ${ago(profile.last_seen_live_at)}`;"),
    ("★ ${profile.focus ? 'Focus' : 'Metti in Focus'}", "★ Focus"),
    ("${profile.blocked ? `<div class=\"cr-blocked-note\">⚠ Questa creator è LIVE ma al momento non viene registrata.${profile.last_error ? ` · ${esc(profile.last_error)}` : ''}</div>` : profile.last_error ? `<div class=\"cr-card-warning\">${esc(profile.last_error)}</div>` : ''}", "${profile.last_error ? `<div class=\"cr-card-warning\">${esc(profile.last_error)}</div>` : ''}"),
    ("${profile.last_error ? '⚠ Da controllare' : profile.source.enabled ? `Offline · ${ago(profile.last_seen_live_at)}` : 'In pausa'}", "${profile.last_error ? '⚠ Errore' : profile.source.enabled ? `Offline · ${ago(profile.last_seen_live_at)}` : 'In pausa'}"),
    ("wall.innerHTML = `<header class=\"cr-wall-header\"><div><div class=\"eyebrow\">CONTROL ROOM</div><h2>Live Wall</h2><span id=\"crWallCount\">0 live</span></div><button", "wall.innerHTML = `<header class=\"cr-wall-header\"><div><h2>Live Wall</h2><span id=\"crWallCount\">0 live</span></div><button"),
    ("'<div class=\"cr-wall-empty\">Nessuna creator live in questo momento.</div>'", "'<div class=\"cr-wall-empty\">Nessuna live.</div>'"),
    ("if (note) note.textContent = 'Le creator LIVE salgono automaticamente in primo piano.';", "if (note) note.remove();"),
    ("root.innerHTML = '<div class=\"empty\">Nessuna sorgente attiva. Quelle archiviate restano disponibili nella Libreria.</div>';", "root.innerHTML = '<div class=\"empty\">Nessuna sorgente.</div>';"),
    ("<div class=\"cr-section-head\"><div><div class=\"eyebrow\">LIVE ADESSO</div><h3>${live.length ? `${live.length} ${live.length === 1 ? 'creator online' : 'creator online'}` : 'Nessuna creator live'}</h3></div>", "<div class=\"cr-section-head\"><div><h3>Live</h3></div>"),
    ("'<div class=\"cr-live-empty\">Quando una creator diventa LIVE comparirà automaticamente qui, sopra a tutte le offline.</div>'", "'<div class=\"cr-live-empty\">Nessuna live.</div>'"),
    ("<section class=\"cr-focus-section\"><div class=\"cr-section-head\"><div><div class=\"eyebrow\">FOCUS</div><h3>Creator fissate</h3></div><span class=\"count\">", "<section class=\"cr-focus-section\"><div class=\"cr-section-head\"><div><h3>Focus</h3></div><span class=\"count\">"),
    ("<summary><span><strong>Altre creator</strong><small>Offline, in pausa o senza attività corrente</small></span><span class=\"count\">", "<summary><span><strong>Altre creator</strong></span><span class=\"count\">"),
    ("'<div class=\"empty compact\">Nessun’altra creator.</div>'", "'<div class=\"empty compact\">Vuoto.</div>'"),
]
for old, new in js_replacements:
    replace_required("app/static/app.js", old, new)

# Make empty dynamic eyebrow labels disappear without leaving spacing.
css = ROOT / "app/static/enhancements.css"
css_text = css.read_text(encoding="utf-8")
rule = "\n.eyebrow:empty{display:none}\n"
if rule.strip() not in css_text:
    css.write_text(css_text.rstrip() + rule, encoding="utf-8")

# Release/version consistency and PWA cache invalidation.
write("VERSION", "2.7.1\n")
replace_required("app/main.py", 'VERSION = "2.7.0"', 'VERSION = "2.7.1"')
replace_required("app/static/sw.js", "livevault-shell-v2.7.0", "livevault-shell-v2.7.1")
replace_required("README.md", "# LiveVault v2.7.0", "# LiveVault v2.7.1")
replace_required("START_HERE.md", "# LiveVault v2.7.0 — START HERE", "# LiveVault v2.7.1 — START HERE")
replace_required("tests/test_version_consistency.py", 'assert version == "2.7.0"', 'assert version == "2.7.1"')
replace_required("tests/test_control_room.py", 'LiveVault Control Room v2.7.0', 'LiveVault Control Room v2.7.1')

changelog = ROOT / "CHANGELOG.md"
changelog_text = changelog.read_text(encoding="utf-8")
entry = """## 2.7.1 — 2026-09-02

- Ripulita l'interfaccia dal copy descrittivo e ridondante: titoli, stati, numeri e azioni restano; spiegazioni ovvie e sottotitoli duplicati vengono rimossi.
- Compattati Control Room, Live Wall, profili, statistiche, Libreria, Archivio, modali e avviso LIVE non REC.
- Mantenuti soltanto avvisi operativi, conferme distruttive e note tecniche necessarie all'uso sicuro delle funzioni.

"""
if "## 2.7.1 — 2026-09-02" not in changelog_text:
    changelog.write_text(changelog_text.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1), encoding="utf-8")

# Regression guard: prose that made the UI read like documentation must not return.
write(
    "tests/test_ui_copy.py",
    '''from pathlib import Path\n\n\ndef test_visible_ui_copy_stays_compact():\n    text = (Path("app/static/index.html").read_text(encoding="utf-8") + "\\n" + Path("app/static/app.js").read_text(encoding="utf-8"))\n    banned = [\n        "Registrazione, archivio e distribuzione cloud in un unico spazio privato.",\n        "Un profilo per persona, anche quando usa più provider.",\n        "Tempo online, copertura registrazioni e andamento delle creator.",\n        "Tempo rilevato online e tempo effettivamente acquisito.",\n        "In quali ore le creator risultano più spesso online.",\n        "Ordinate per tempo online nel periodo selezionato.",\n        "Stato operativo e controlli rapidi.",\n        "Le creator LIVE salgono automaticamente in primo piano.",\n        "Quando una creator diventa LIVE comparirà automaticamente qui, sopra a tutte le offline.",\n        "Questa creator è LIVE ma al momento non viene registrata.",\n        "Offline, in pausa o senza attività corrente",\n        "Online rilevato, registrazioni e copertura.",\n        "Non modifica nomi tecnici, cartelle o storico.",\n        "Più account della stessa persona resteranno uniti nella Libreria.",\n        "Incolla il link: LiveVault sceglie l'adapter disponibile.",\n        "Le modifiche sono applicate in tempo reale.",\n    ]\n    for phrase in banned:\n        assert phrase not in text\n''',
)

print("v2.7.1 compact UI copy patch applied")
