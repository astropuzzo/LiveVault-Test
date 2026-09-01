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


# Worker: last_live_at must come from Chaturbate's own last_broadcast metadata.
workers = read("app/workers.py")
workers = replace_once(
    workers,
    '''                    current.last_error = result.error if result.status == "error" else ""\n                    if result.live:\n                        current.last_live_at = checked_at\n''',
    '''                    current.last_error = result.error if result.status == "error" else ""\n                    if result.last_broadcast is not None:\n                        current.last_live_at = result.last_broadcast\n''',
    "worker probe last_live_at",
)
workers = replace_once(
    workers,
    '''                    source.last_status = new_status\n                    source.last_checked_at = now\n                    source.last_live_at = now\n                    if size_rollover:\n''',
    '''                    source.last_status = new_status\n                    source.last_checked_at = now\n                    if size_rollover:\n''',
    "worker session finalizer last_live_at",
)
write("app/workers.py", workers)


# API: do not replace Chaturbate's timestamp with local `now` while recording.
main = read("app/main.py")
main = replace_once(main, 'VERSION = "2.2.1"', 'VERSION = "2.2.2"', "backend version")
main = replace_once(
    main,
    '            "last_live_at": _iso_utc(now if active else source.last_live_at),\n',
    '            "last_live_at": _iso_utc(source.last_live_at),\n            "last_live_source": "chaturbate",\n',
    "sources API platform last live",
)
# `now` is no longer needed in list_sources after removing the active override.
main = replace_once(
    main,
    '''    active_ids = set(manager.active)\n    now = utcnow()\n    result = []\n''',
    '''    active_ids = set(manager.active)\n    result = []\n''',
    "unused list_sources now",
)
write("app/main.py", main)


# Clean up the public metadata client constant name.
providers = read("app/source_providers.py")
providers = providers.replace("CHaturbate_HEADERS", "CHATURBATE_HEADERS")
write("app/source_providers.py", providers)

write("VERSION", "2.2.2\n")

changelog = read("CHANGELOG.md")
entry = '''# Changelog\n\n## 2.2.2 - 2026-09-01\n\n- `Ultima live` ora usa il `last_broadcast` restituito direttamente dai metadata pubblici Chaturbate (`api/biocontext/{username}/`), non l'ultima live osservata localmente da LiveVault.\n- Il timestamp viene aggiornato anche quando la camera è offline, quindi LiveVault può mostrare una trasmissione precedente avvenuta mentre il server era spento.\n- Rimossi gli override locali che impostavano `Ultima live` a `adesso` solo perché il recorder era attivo o aveva appena terminato una sessione.\n- Aggiunti test offline/live sul parsing ISO UTC e sul metadata `last_broadcast`.\n\n'''
if changelog.startswith("# Changelog\n\n"):
    changelog = entry + changelog[len("# Changelog\n\n"):]
else:
    changelog = entry + changelog
write("CHANGELOG.md", changelog)

print("Chaturbate platform last_broadcast patch applied")
