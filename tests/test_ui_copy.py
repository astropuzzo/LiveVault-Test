from pathlib import Path


def test_visible_ui_copy_stays_compact():
    text = (Path("app/static/index.html").read_text(encoding="utf-8") + "\n" + Path("app/static/app.js").read_text(encoding="utf-8"))
    banned = [
        "Registrazione, archivio e distribuzione cloud in un unico spazio privato.",
        "Un profilo per persona, anche quando usa più provider.",
        "Tempo online, copertura registrazioni e andamento delle creator.",
        "Tempo rilevato online e tempo effettivamente acquisito.",
        "In quali ore le creator risultano più spesso online.",
        "Ordinate per tempo online nel periodo selezionato.",
        "Stato operativo e controlli rapidi.",
        "Le creator LIVE salgono automaticamente in primo piano.",
        "Quando una creator diventa LIVE comparirà automaticamente qui, sopra a tutte le offline.",
        "Questa creator è LIVE ma al momento non viene registrata.",
        "Offline, in pausa o senza attività corrente",
        "Online rilevato, registrazioni e copertura.",
        "Non modifica nomi tecnici, cartelle o storico.",
        "Più account della stessa persona resteranno uniti nella Libreria.",
        "Incolla il link: LiveVault sceglie l'adapter disponibile.",
        "Le modifiche sono applicate in tempo reale.",
    ]
    for phrase in banned:
        assert phrase not in text
