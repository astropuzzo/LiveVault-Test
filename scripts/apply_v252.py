from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"release patch anchor missing in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Backend: permanent profile deletion preserves historical media.
main_path = Path("app/main.py")
main_text = main_path.read_text(encoding="utf-8")
route_anchor = '\n\n@app.get("/api/sources/{source_id}/profile")\ndef source_profile(source_id: int, request: Request):\n'
route = '''

@app.delete("/api/library/profiles/{profile_id}")
async def delete_profile(profile_id: int, request: Request):
    """Permanently remove a creator profile and its source configuration.

    Recording rows and their local/cloud files are intentionally preserved so
    deleting a creator from the library cannot destroy captured media.
    """
    require_auth(request)
    with db_session() as db:
        profile = db.get(Profile, profile_id)
        if not profile:
            raise HTTPException(404, "Profilo non trovato")
        linked_sources = list(db.scalars(
            select(Source).where(Source.profile_id == profile_id).order_by(Source.id)
        ).all())
        source_ids = [source.id for source in linked_sources]
        preserved_recordings = int(db.scalar(
            select(func.count()).select_from(Recording).where(Recording.source_id.in_(source_ids))
        ) or 0) if source_ids else 0
        now = utcnow()
        for source in linked_sources:
            source.enabled = False
            source.archived = True
            source.last_status = "archived"
            source.status_changed_at = now

    if source_ids:
        await asyncio.gather(*(manager.stop_source(source_id) for source_id in source_ids))

    with db_session() as db:
        profile = db.get(Profile, profile_id)
        if not profile:
            raise HTTPException(404, "Profilo non trovato")
        # sources.profile_id is a real FK, so source configuration goes first.
        # Recording rows deliberately remain as immutable archive history.
        db.execute(delete(Source).where(Source.profile_id == profile_id))
        db.execute(delete(ProfileCategory).where(ProfileCategory.profile_id == profile_id))
        db.execute(delete(CollectionProfile).where(CollectionProfile.profile_id == profile_id))
        db.delete(profile)

    manager.wake()
    return {
        "ok": True,
        "deleted": True,
        "profile_id": profile_id,
        "source_ids": source_ids,
        "preserved_recordings": preserved_recordings,
    }
'''
if route_anchor not in main_text:
    raise SystemExit("main profile-route anchor missing")
main_text = main_text.replace(route_anchor, route + route_anchor, 1)
main_text = main_text.replace('VERSION = "2.5.1"', 'VERSION = "2.5.2"', 1)
main_path.write_text(main_text, encoding="utf-8")

# Frontend: destructive action on library card and inside profile modal.
appjs = Path("app/static/app.js")
js = appjs.read_text(encoding="utf-8")
old = '''<div class="library-actions"><button class="btn primary" data-lib-action="profile" data-id="${profile.representative_id}" type="button">Apri profilo</button><button class="btn soft" data-lib-action="archive" data-id="${profile.representative_id}" type="button">Archivio</button>${profile.archived ? `<button class="btn soft" data-lib-action="restore" data-id="${profile.representative_id}" type="button">Ripristina</button>` : ''}</div>'''
new = '''<div class="library-actions"><button class="btn primary" data-lib-action="profile" data-id="${profile.representative_id}" type="button">Apri profilo</button><button class="btn soft" data-lib-action="archive" data-id="${profile.representative_id}" type="button">Archivio</button>${profile.archived ? `<button class="btn soft" data-lib-action="restore" data-id="${profile.representative_id}" type="button">Ripristina</button>` : ''}<button class="btn danger" data-lib-action="delete-profile" data-id="${profile.representative_id}" type="button">Elimina definitivamente</button></div>'''
if old not in js:
    raise SystemExit("library actions anchor missing")
js = js.replace(old, new, 1)

old = '''<div class="profile-save"><span id="profileSaveError" class="error-text"></span><button class="btn primary" data-profile-action="save" data-id="${profile.id}" type="button">Salva profilo</button></div>`;'''
new = '''<div class="profile-save"><button class="btn danger" data-profile-action="delete-profile" data-id="${profile.profile_id}" type="button">Elimina creator definitivamente</button><span id="profileSaveError" class="error-text"></span><button class="btn primary" data-profile-action="save" data-id="${profile.id}" type="button">Salva profilo</button></div>`;'''
if old not in js:
    raise SystemExit("profile save anchor missing")
js = js.replace(old, new, 1)

old = '''    } else if (action === 'restore') {
      await api(`/api/sources/${sourceId}`, {method: 'PATCH', body: JSON.stringify({enabled: true})});
      toast('Sorgente ripristinata');
    }
    await refresh({includeRecordings: false});'''
new = '''    } else if (action === 'restore') {
      await api(`/api/sources/${sourceId}`, {method: 'PATCH', body: JSON.stringify({enabled: true})});
      toast('Sorgente ripristinata');
    } else if (action === 'delete-profile') {
      const profile = profileForId(source.profile_id);
      const name = profile?.display_name || source.display_name || source.name;
      if (!confirm(`Eliminare definitivamente la creator ${name}? Verranno rimossi il profilo e tutte le sorgenti collegate. Le registrazioni già salvate e i file locali/cloud RESTANO nell'Archivio. Questa operazione non può essere annullata.`)) return;
      const response = await api(`/api/library/profiles/${source.profile_id}`, {method: 'DELETE'});
      selectedProfiles.delete(Number(source.profile_id));
      toast(`Creator eliminata definitivamente${response.preserved_recordings ? ` · ${response.preserved_recordings} registrazioni conservate` : ''}`);
    }
    await refresh({includeRecordings: false});'''
if old not in js:
    raise SystemExit("library handler anchor missing")
js = js.replace(old, new, 1)

old = '''  if (action === 'archive') {
    const sourceId = profileData.source.id;
    closeModal('profileModal');
    return setSourceFilter(sourceId);
  }
  if (action === 'edit-source') {'''
new = '''  if (action === 'archive') {
    const sourceId = profileData.source.id;
    closeModal('profileModal');
    return setSourceFilter(sourceId);
  }
  if (action === 'delete-profile') {
    const profile = profileData.source;
    if (!confirm(`Eliminare definitivamente la creator ${profile.display_name}? Verranno rimossi il profilo e tutte le sorgenti collegate. Le registrazioni già salvate e i file locali/cloud RESTANO nell'Archivio. Questa operazione non può essere annullata.`)) return;
    setBusy(button, true, 'Eliminazione…');
    try {
      const response = await api(`/api/library/profiles/${profile.profile_id}`, {method: 'DELETE'});
      selectedProfiles.delete(Number(profile.profile_id));
      closeModal('profileModal');
      profileData = null;
      toast(`Creator eliminata definitivamente${response.preserved_recordings ? ` · ${response.preserved_recordings} registrazioni conservate` : ''}`);
      await refresh({includeRecordings: false});
    } catch (error) {
      toast(error.message, 'bad');
      setBusy(button, false);
    }
    return;
  }
  if (action === 'edit-source') {'''
if old not in js:
    raise SystemExit("profile handler anchor missing")
js = js.replace(old, new, 1)
appjs.write_text(js, encoding="utf-8")

# Regression test: delete creator configuration, preserve recordings.
tests = Path("tests/test_library.py")
test_text = tests.read_text(encoding="utf-8")
test_anchor = '\n\ndef test_library_validation_and_thumbnail_containment(tmp_path, monkeypatch):\n'
test_case = '''

def test_permanent_profile_delete_removes_creator_config_but_preserves_recordings(library_db, monkeypatch):
    profile_id, first_id, second_id = _seed_shared_profile(library_db)
    with library_db.begin() as session:
        category = Category(name="Delete category", color="#112233")
        collection = Collection(name="Delete collection", description="", color="#445566", pinned=False)
        session.add_all([category, collection])
        session.flush()
        session.add(ProfileCategory(profile_id=profile_id, category_id=category.id))
        session.add(CollectionProfile(profile_id=profile_id, collection_id=collection.id))
        recording = Recording(
            source_id=first_id,
            source_name="performer_cb",
            session_id="session-preserved",
            local_path="/data/recordings/preserved.mp4",
            filename="preserved.mp4",
            started_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        session.add(recording)
        session.flush()
        recording_id = recording.id

    stopped = []

    async def stop_source(source_id):
        stopped.append(source_id)

    monkeypatch.setattr(main.manager, "stop_source", stop_source)
    monkeypatch.setattr(main.manager, "wake", lambda: None)

    result = asyncio.run(main.delete_profile(profile_id, object()))

    with library_db() as session:
        assert session.get(Profile, profile_id) is None
        assert session.get(Source, first_id) is None
        assert session.get(Source, second_id) is None
        assert session.scalar(select(func.count()).select_from(ProfileCategory).where(ProfileCategory.profile_id == profile_id)) == 0
        assert session.scalar(select(func.count()).select_from(CollectionProfile).where(CollectionProfile.profile_id == profile_id)) == 0
        preserved = session.get(Recording, recording_id)
        assert preserved is not None
        assert preserved.source_id == first_id
        assert preserved.filename == "preserved.mp4"

    assert stopped == [first_id, second_id]
    assert result == {
        "ok": True,
        "deleted": True,
        "profile_id": profile_id,
        "source_ids": [first_id, second_id],
        "preserved_recordings": 1,
    }
'''
if test_anchor not in test_text:
    raise SystemExit("library test anchor missing")
tests.write_text(test_text.replace(test_anchor, test_case + test_anchor, 1), encoding="utf-8")

# Release metadata / cache bust.
Path("VERSION").write_text("2.5.2\n", encoding="utf-8")
replace_once("app/static/sw.js", "const CACHE='livevault-shell-v2.5.1';", "const CACHE='livevault-shell-v2.5.2';")
replace_once("README.md", "# LiveVault v2.5.1", "# LiveVault v2.5.2")
replace_once("START_HERE.md", "# LiveVault v2.5.1 — START HERE", "# LiveVault v2.5.2 — START HERE")
replace_once("tests/test_version_consistency.py", 'assert version == "2.5.1"', 'assert version == "2.5.2"')

changelog = Path("CHANGELOG.md")
changelog_text = changelog.read_text(encoding="utf-8")
header = "# Changelog\n\n"
entry = '''## 2.5.2 — 2026-09-02

- Aggiunto **Elimina definitivamente** per le creator nella Libreria e nel profilo.
- La cancellazione permanente rimuove profilo, categorie/raccolte collegate e tutte le configurazioni sorgente associate, fermando prima eventuali recorder attivi.
- Le registrazioni già acquisite, i file locali e le copie cloud vengono deliberatamente conservati nell'Archivio per evitare perdita accidentale di media.
- La conferma UI distingue chiaramente l'archiviazione reversibile dalla cancellazione definitiva della creator.

'''
if not changelog_text.startswith(header):
    raise SystemExit("changelog header missing")
changelog.write_text(header + entry + changelog_text[len(header):], encoding="utf-8")

# Restore the normal CI workflow in the final feature commit.
normal_ci = '''name: CI

on:
  push:
  pull_request:

concurrency:
  group: livevault-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    name: Core tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - name: Install FFmpeg
        run: sudo apt-get update && sudo apt-get install -y ffmpeg
      - name: Install dependencies
        run: pip install -r requirements-dev.txt
      - name: Compile Python
        run: python -m compileall -q app tests
      - name: Run tests
        run: PYTHONPATH=. pytest -q
      - name: Check JavaScript
        run: node --check app/static/app.js
      - name: Check shell scripts
        run: bash -n scripts/*.sh

  deploy:
    name: Deploy verified main
    needs: test
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - name: Ask CapRover to deploy the latest verified main
        env:
          CAPROVER_DEPLOY_WEBHOOK: ${{ secrets.CAPROVER_DEPLOY_WEBHOOK }}
        run: >-
          curl --fail --show-error --silent
          --retry 3 --retry-all-errors --max-time 30
          --request POST "$CAPROVER_DEPLOY_WEBHOOK"
'''
Path(".github/workflows/ci.yml").write_text(normal_ci, encoding="utf-8")

# The release commit must not retain this one-shot patcher.
Path(__file__).unlink()
