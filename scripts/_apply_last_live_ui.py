from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"Patch target not found: {label}")
    return text.replace(old, new, 1)


js = read("app/static/app.js")
js = replace_once(
    js,
    "function dateText(iso){if(!iso)return '—';return new Intl.DateTimeFormat('it-IT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(iso))}\n",
    "function dateText(iso){if(!iso)return '—';return new Intl.DateTimeFormat('it-IT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(iso))}\nfunction dateFull(iso){if(!iso)return '—';return new Intl.DateTimeFormat('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(iso))}\n",
    "dateFull helper",
)
js = replace_once(
    js,
    '<span class="chip" title="${esc(dateText(s.last_live_at))}">Ultimo live ${esc(ago(s.last_live_at))}</span>',
    '<span class="chip" title="Dato last_broadcast restituito da Chaturbate">Ultima live CB ${esc(s.last_live_at?dateFull(s.last_live_at):\'mai\')}${s.last_live_at?` · ${esc(ago(s.last_live_at))}`:\'\'}</span>',
    "visible Chaturbate last live",
)
write("app/static/app.js", js)

sw = read("app/static/sw.js")
sw = replace_once(sw, "livevault-shell-v2.2.1", "livevault-shell-v2.2.2", "service worker cache")
write("app/static/sw.js", sw)

changelog = read("CHANGELOG.md")
needle = "- Aggiunti test offline/live sul parsing ISO UTC e sul metadata `last_broadcast`.\n"
replacement = needle + "- La card sorgente mostra ora la data/ora Chaturbate completa direttamente a schermo, oltre al tempo relativo.\n"
changelog = replace_once(changelog, needle, replacement, "changelog UI bullet")
write("CHANGELOG.md", changelog)

print("Visible Chaturbate last-live UI patch applied")
