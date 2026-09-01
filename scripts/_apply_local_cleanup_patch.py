from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"Patch target not found: {label}")
    return text.replace(old, new, 1)


# --- New filesystem safety helpers -------------------------------------------------
file_cleanup = r'''from __future__ import annotations

from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mkv"}


def confined_path(path: Path, root: Path) -> Path:
    """Resolve *path* and guarantee it stays inside *root*."""
    root_resolved = root.resolve(strict=False)
    target = path.resolve(strict=False)
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Percorso fuori dall'area LiveVault: {target}") from exc
    return target


def safe_unlink(path: Path, root: Path) -> tuple[int, bool]:
    """Delete one regular file confined to root and return (bytes_freed, removed)."""
    target = confined_path(path, root)
    if not target.exists():
        return 0, False
    if not target.is_file():
        raise ValueError(f"Il percorso non è un file: {target.name}")
    size = target.stat().st_size
    target.unlink()
    return size, True


def cleanup_empty_parents(start: Path, root: Path) -> None:
    root_resolved = root.resolve(strict=False)
    current = start.resolve(strict=False)
    while current != root_resolved:
        try:
            current.relative_to(root_resolved)
        except ValueError:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def cleanup_orphan_videos(root: Path, tracked_paths: list[Path], active_dirs: list[Path]) -> dict:
    """Remove untracked MP4/MKV files while never touching active recorder directories."""
    root_resolved = root.resolve(strict=False)
    tracked: set[Path] = set()
    for path in tracked_paths:
        try:
            tracked.add(confined_path(path, root_resolved))
        except ValueError:
            continue

    active: list[Path] = []
    for directory in active_dirs:
        try:
            active.append(confined_path(directory, root_resolved))
        except ValueError:
            continue

    removed = 0
    freed = 0
    skipped_active = 0
    errors: list[str] = []

    if not root_resolved.exists():
        return {"removed": 0, "freed": 0, "skipped_active": 0, "errors": []}

    for candidate in root_resolved.rglob("*"):
        if candidate.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        try:
            resolved = confined_path(candidate, root_resolved)
            if not resolved.is_file() or resolved in tracked:
                continue
            if any(resolved.is_relative_to(directory) for directory in active):
                skipped_active += 1
                continue
            size, did_remove = safe_unlink(resolved, root_resolved)
            if did_remove:
                removed += 1
                freed += size
                cleanup_empty_parents(resolved.parent, root_resolved)
        except (OSError, ValueError) as exc:
            errors.append(f"{candidate.name}: {exc}")

    return {"removed": removed, "freed": freed, "skipped_active": skipped_active, "errors": errors}
'''
write("app/file_cleanup.py", file_cleanup)


# --- Backend ----------------------------------------------------------------------
main = read("app/main.py")
main = replace_once(
    main,
    "from .db import Recording, Source, db_session, init_db\n",
    "from .db import Recording, Source, db_session, init_db\nfrom .file_cleanup import cleanup_empty_parents, cleanup_orphan_videos, safe_unlink\n",
    "main import",
)
main = replace_once(main, 'VERSION = "2.2.0"', 'VERSION = "2.2.1"', "backend version")
main = replace_once(
    main,
    "class BoolBody(BaseModel):\n    paused: bool\n    stop_active: bool = True\n\n\nclass SourceCreate(BaseModel):",
    "class BoolBody(BaseModel):\n    paused: bool\n    stop_active: bool = True\n\n\nclass CleanupLocalBody(BaseModel):\n    scope: str = \"uploaded\"\n    source_id: int | None = None\n    include_orphans: bool = False\n    delete_thumbnails: bool = False\n    confirm: bool = False\n\n\nclass SourceCreate(BaseModel):",
    "cleanup body",
)

start_marker = '@app.delete("/api/recordings/{recording_id}/local")'
end_marker = '\n\n@app.get("/healthz")'
start = main.find(start_marker)
end = main.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("Patch target not found: recording cleanup endpoints")

new_block = r'''def _remove_local_copy(recording_id: int, *, force: bool = False, delete_thumbnail: bool = False) -> dict:
    """Remove the actual local bytes first, then update DB state.

    Non-uploaded files require force=True. Files currently being uploaded/converted are
    never removed. The path is confined to LiveVault storage so a corrupt DB row cannot
    delete an arbitrary server file.
    """
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        previous_status = rec.upload_status
        local_path = Path(rec.local_path)
        thumbnail_path = Path(rec.thumbnail_path) if rec.thumbnail_path else None
        if previous_status in {"uploading", "converting", "deleting"}:
            raise HTTPException(409, "File occupato: attendi la fine dell'operazione in corso")
        if previous_status != "uploaded" and not force:
            raise HTTPException(400, "File non caricato: usa la cancellazione forzata per eliminarlo definitivamente")
        if previous_status != "uploaded":
            rec.upload_status = "deleting"

    try:
        freed, removed = safe_unlink(local_path, settings.recordings_dir)
        cleanup_empty_parents(local_path.parent, settings.recordings_dir)
        thumbnail_removed = False
        if delete_thumbnail and thumbnail_path:
            _, thumbnail_removed = safe_unlink(thumbnail_path, settings.data_dir / "thumbnails")
            cleanup_empty_parents(thumbnail_path.parent, settings.data_dir / "thumbnails")
    except (OSError, ValueError) as exc:
        with db_session() as db:
            rec = db.get(Recording, recording_id)
            if rec and rec.upload_status == "deleting":
                rec.upload_status = previous_status
        raise HTTPException(500, f"Impossibile eliminare il file locale: {exc}") from exc

    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if rec:
            rec.local_deleted = True
            if previous_status != "uploaded":
                rec.upload_status = "discarded"
                rec.last_error = "File locale eliminato manualmente prima dell'upload"
            else:
                rec.upload_status = "uploaded"
                rec.last_error = ""
            if delete_thumbnail and thumbnail_removed:
                rec.thumbnail_path = ""

    manager.clear_retry_backoff()
    manager.wake()
    return {
        "ok": True,
        "removed": removed,
        "thumbnail_removed": thumbnail_removed,
        "freed": freed,
        "freed_human": human_bytes(freed),
    }


@app.delete("/api/recordings/{recording_id}/local")
def delete_local_recording(recording_id: int, request: Request, force: bool = False, delete_thumbnail: bool = False):
    require_auth(request)
    return _remove_local_copy(recording_id, force=force, delete_thumbnail=delete_thumbnail)


@app.delete("/api/recordings/{recording_id}")
def delete_recording(recording_id: int, request: Request, delete_file: bool = True, delete_thumbnail: bool = True):
    """Delete an archive entry. By default the underlying local file is deleted too."""
    require_auth(request)
    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if not rec:
            raise HTTPException(404, "Registrazione non trovata")
        if rec.upload_status in {"uploading", "converting", "deleting"}:
            raise HTTPException(409, "File occupato: attendi la fine dell'operazione in corso")
        thumbnail_path = Path(rec.thumbnail_path) if rec.thumbnail_path else None

    freed = 0
    file_removed = False
    thumbnail_removed = False
    if delete_file:
        result = _remove_local_copy(recording_id, force=True, delete_thumbnail=delete_thumbnail)
        freed = int(result["freed"])
        file_removed = bool(result["removed"])
        thumbnail_removed = bool(result["thumbnail_removed"])
    elif delete_thumbnail and thumbnail_path:
        try:
            _, thumbnail_removed = safe_unlink(thumbnail_path, settings.data_dir / "thumbnails")
            cleanup_empty_parents(thumbnail_path.parent, settings.data_dir / "thumbnails")
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"Impossibile eliminare la miniatura: {exc}") from exc

    with db_session() as db:
        rec = db.get(Recording, recording_id)
        if rec:
            db.delete(rec)

    manager.clear_retry_backoff()
    manager.wake()
    return {
        "ok": True,
        "entry_deleted": True,
        "file_removed": file_removed,
        "thumbnail_removed": thumbnail_removed,
        "freed": freed,
        "freed_human": human_bytes(freed),
    }


@app.post("/api/recordings/retry-failed")
def retry_failed_recordings(request: Request):
    require_auth(request)
    changed = 0
    with db_session() as db:
        rows = list(db.scalars(select(Recording).where(Recording.upload_status.in_(["failed", "waiting_config"]), Recording.integrity_status == "passed")).all())
        for rec in rows:
            if not rec.local_deleted and Path(rec.local_path).exists():
                rec.upload_status = "pending"
                rec.upload_attempts = 0
                rec.last_error = ""
                changed += 1
    manager.clear_retry_backoff()
    manager.wake()
    return {"ok": True, "changed": changed}


def _cleanup_ids(scope: str, source_id: int | None) -> list[int]:
    with db_session() as db:
        query = select(Recording.id).where(~Recording.upload_status.in_(["uploading", "converting", "deleting"]))
        if source_id is not None:
            query = query.where(Recording.source_id == source_id)
        if scope == "uploaded":
            query = query.where(Recording.upload_status == "uploaded")
        elif scope == "failed":
            query = query.where(Recording.upload_status.in_(["failed", "waiting_config", "integrity_failed", "discarded"]))
        elif scope != "all":
            raise HTTPException(400, "Scope pulizia non valido: uploaded, failed o all")
        return list(db.scalars(query).all())


@app.post("/api/recordings/cleanup-local")
def cleanup_local_recordings(body: CleanupLocalBody, request: Request):
    require_auth(request)
    scope = body.scope.strip().lower()
    if scope not in {"uploaded", "failed", "all"}:
        raise HTTPException(400, "Scope pulizia non valido: uploaded, failed o all")
    if scope != "uploaded" and not body.confirm:
        raise HTTPException(400, "La pulizia di file non caricati richiede confirm=true")

    ids = _cleanup_ids(scope, body.source_id)
    removed = 0
    freed = 0
    thumbnails_removed = 0
    errors: list[str] = []
    for recording_id in ids:
        try:
            result = _remove_local_copy(
                recording_id,
                force=scope != "uploaded",
                delete_thumbnail=body.delete_thumbnails,
            )
            removed += int(bool(result["removed"]))
            freed += int(result["freed"])
            thumbnails_removed += int(bool(result["thumbnail_removed"]))
        except HTTPException as exc:
            errors.append(f"#{recording_id}: {exc.detail}")

    orphan_result = {"removed": 0, "freed": 0, "skipped_active": 0, "errors": []}
    if body.include_orphans and body.source_id is None:
        with db_session() as db:
            tracked_paths = [
                Path(path)
                for path in db.scalars(select(Recording.local_path).where(Recording.local_deleted.is_(False))).all()
            ]
        active_dirs = [session.directory for session in manager.active.values()]
        orphan_result = cleanup_orphan_videos(settings.recordings_dir, tracked_paths, active_dirs)
        removed += int(orphan_result["removed"])
        freed += int(orphan_result["freed"])
        errors.extend(orphan_result["errors"])

    manager.clear_retry_backoff()
    manager.wake()
    return {
        "ok": not errors,
        "scope": scope,
        "removed": removed,
        "thumbnails_removed": thumbnails_removed,
        "orphan_removed": orphan_result["removed"],
        "skipped_active": orphan_result["skipped_active"],
        "freed": freed,
        "freed_human": human_bytes(freed),
        "errors": errors[:50],
    }


@app.post("/api/recordings/cleanup-uploaded")
def cleanup_uploaded_recordings(request: Request):
    """Backward-compatible safe cleanup used by older UI versions."""
    require_auth(request)
    ids = _cleanup_ids("uploaded", None)
    removed = 0
    freed = 0
    errors: list[str] = []
    for recording_id in ids:
        try:
            result = _remove_local_copy(recording_id, force=False, delete_thumbnail=False)
            removed += int(bool(result["removed"]))
            freed += int(result["freed"])
        except HTTPException as exc:
            errors.append(f"#{recording_id}: {exc.detail}")
    return {"ok": not errors, "removed": removed, "freed": freed, "freed_human": human_bytes(freed), "errors": errors[:50]}
'''

main = main[:start] + new_block + main[end:]
write("app/main.py", main)


# --- UI ---------------------------------------------------------------------------
index = read("app/static/index.html")
index = replace_once(
    index,
    '<option value="converting">Conversione MP4</option><option value="waiting_config">Config mancante</option>',
    '<option value="converting">Conversione MP4</option><option value="waiting_config">Config mancante</option><option value="discarded">Locale eliminato</option>',
    "archive filter",
)
index = replace_once(
    index,
    '<button id="cleanupBtn" class="btn soft" type="button">Libera caricati</button>',
    '<button id="cleanupBtn" class="btn soft" type="button">Libera caricati</button>\n          <button id="purgeLocalBtn" class="btn danger" type="button">Pulisci locali</button>',
    "purge local button",
)
write("app/static/index.html", index)

js = read("app/static/app.js")
js = replace_once(
    js,
    "$('#cleanupBtn').addEventListener('click',async e=>{if(!confirm('Eliminare solo le copie locali già caricate e verificate? Le miniature restano.'))return;setBusy(e.currentTarget,true);try{const r=await api('/api/recordings/cleanup-uploaded',{method:'POST'});toast(`Liberati ${r.freed_human}`);await refresh()}catch(x){toast(x.message,'bad')}finally{setBusy(e.currentTarget,false)}});",
    "$('#cleanupBtn').addEventListener('click',async e=>{if(!confirm('Eliminare solo le copie locali già caricate e verificate? Le miniature restano.'))return;setBusy(e.currentTarget,true);try{const r=await api('/api/recordings/cleanup-uploaded',{method:'POST'});toast(`Liberati ${r.freed_human}${r.errors?.length?` · ${r.errors.length} errori`:''}`,r.errors?.length?'bad':'good');await refresh()}catch(x){toast(x.message,'bad')}finally{setBusy(e.currentTarget,false)}});\n$('#purgeLocalBtn').addEventListener('click',async e=>{const selected=sources.find(s=>s.id===sourceFilterId);const target=selected?` della camera ${selected.name}`:' di TUTTE le camere';if(!confirm(`Eliminare DEFINITIVAMENTE tutti i video locali${target}, anche quelli NON caricati?\\n\\nLe voci archivio e i file cloud restano. I file orfani sul disco verranno rimossi quando pulisci tutte le camere.`))return;setBusy(e.currentTarget,true,'Pulizia…');try{const body={scope:'all',source_id:sourceFilterId||null,include_orphans:!sourceFilterId,delete_thumbnails:false,confirm:true};const r=await api('/api/recordings/cleanup-local',{method:'POST',body:JSON.stringify(body)});toast(`Rimossi ${r.removed} file · liberati ${r.freed_human}${r.orphan_removed?` · ${r.orphan_removed} orfani`:''}${r.skipped_active?` · ${r.skipped_active} live saltati`:''}`,r.errors?.length?'bad':'good');await refresh()}catch(x){toast(x.message,'bad')}finally{setBusy(e.currentTarget,false)}});",
    "cleanup handlers",
)
js = replace_once(
    js,
    "function uploadLabel(v){return ({pending:'In coda',uploading:'Upload',uploaded:'Caricato',failed:'Fallito',waiting_config:'Config mancante',integrity_failed:'Integrità fallita',converting:'Conversione MP4',missing:'Mancante'})[v]||v}",
    "function uploadLabel(v){return ({pending:'In coda',uploading:'Upload',uploaded:'Caricato',failed:'Fallito',waiting_config:'Config mancante',integrity_failed:'Integrità fallita',converting:'Conversione MP4',missing:'Mancante',deleting:'Eliminazione',discarded:'Locale eliminato'})[v]||v}",
    "upload labels",
)
js = replace_once(
    js,
    "${r.upload_status==='uploaded'&&r.local_available?`<button class=\"btn danger\" data-rec-action=\"delete-local\" data-id=\"${r.id}\" type=\"button\">Libera</button>`:''}",
    "${r.local_available?`<button class=\"btn danger\" data-rec-action=\"delete-local\" data-id=\"${r.id}\" type=\"button\">Elimina locale</button>`:''}<button class=\"btn danger\" data-rec-action=\"delete-record\" data-id=\"${r.id}\" type=\"button\">Elimina tutto</button>",
    "recording delete buttons",
)
js = replace_once(
    js,
    "if(action==='delete-local'){if(!confirm('Eliminare la copia video locale? La miniatura resterà disponibile.'))return;await api(`/api/recordings/${id}/local`,{method:'DELETE'});toast('Copia locale rimossa')}",
    "if(action==='delete-local'){const uploaded=r.upload_status==='uploaded';const warning=uploaded?'Eliminare la copia video locale? Il cloud e la miniatura resteranno disponibili.':'Questo file NON risulta caricato. Eliminarlo localmente significa perdere definitivamente il video. Continuare?';if(!confirm(warning))return;const x=await api(`/api/recordings/${id}/local?force=${uploaded?'false':'true'}`,{method:'DELETE'});toast(`File locale eliminato · ${x.freed_human} liberati`)}if(action==='delete-record'){const warning=r.local_available?'Eliminare DEFINITIVAMENTE file locale, miniatura e voce dall’archivio? Il file cloud non verrà cancellato.':'Eliminare definitivamente voce e miniatura dall’archivio? Il file cloud non verrà cancellato.';if(!confirm(warning))return;const x=await api(`/api/recordings/${id}?delete_file=true&delete_thumbnail=true`,{method:'DELETE'});toast(`Registrazione eliminata${x.freed?` · ${x.freed_human} liberati`:''}`)}",
    "recording delete actions",
)
write("app/static/app.js", js)

sw = read("app/static/sw.js")
sw = replace_once(sw, "livevault-shell-v2.2.0", "livevault-shell-v2.2.1", "service worker cache")
write("app/static/sw.js", sw)

write("VERSION", "2.2.1\n")

changelog = read("CHANGELOG.md")
entry = """## 2.2.1 - 2026-09-01\n\n- Aggiunta cancellazione reale del file locale per ogni registrazione, anche se non caricata, con conferma esplicita e protezione da upload/conversioni in corso.\n- `Elimina tutto` rimuove file locale, miniatura e voce DB in un'unica operazione; il file cloud non viene toccato.\n- Nuova pulizia bulk dei video locali, globale o limitata alla camera filtrata, con conteggio file e spazio liberato.\n- Pulizia automatica dei file MP4/MKV orfani lasciati da vecchie cancellazioni DB, senza toccare le cartelle dei recorder attivi.\n- Tutte le cancellazioni sono confinate alle directory LiveVault per impedire rimozioni accidentali fuori dallo storage applicativo.\n\n"""
changelog = replace_once(changelog, "# Changelog\n\n", "# Changelog\n\n" + entry, "changelog header")
write("CHANGELOG.md", changelog)


# --- Tests ------------------------------------------------------------------------
tests = r'''from pathlib import Path

import pytest

from app.file_cleanup import cleanup_orphan_videos, safe_unlink


def test_safe_unlink_removes_file_and_reports_bytes(tmp_path: Path):
    root = tmp_path / "recordings"
    root.mkdir()
    target = root / "clip.mp4"
    target.write_bytes(b"123456")
    freed, removed = safe_unlink(target, root)
    assert removed is True
    assert freed == 6
    assert not target.exists()


def test_safe_unlink_rejects_path_outside_livevault_root(tmp_path: Path):
    root = tmp_path / "recordings"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    with pytest.raises(ValueError):
        safe_unlink(outside, root)
    assert outside.exists()


def test_orphan_cleanup_keeps_tracked_and_active_files(tmp_path: Path):
    root = tmp_path / "recordings"
    tracked = root / "camera" / "old" / "tracked.mp4"
    orphan = root / "camera" / "old" / "orphan.mkv"
    active_dir = root / "camera" / "live"
    active = active_dir / "current.mp4"
    for path in (tracked, orphan, active):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"abcd")

    result = cleanup_orphan_videos(root, [tracked], [active_dir])

    assert tracked.exists()
    assert active.exists()
    assert not orphan.exists()
    assert result["removed"] == 1
    assert result["freed"] == 4
    assert result["skipped_active"] == 1
'''
write("tests/test_file_cleanup.py", tests)

print("LiveVault local cleanup patch applied successfully")
