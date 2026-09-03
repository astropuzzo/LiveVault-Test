from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    return text.replace(old, new, 1)

# db.py -----------------------------------------------------------------
p = "app/db.py"
t = read(p)
anchor = '''class Recording(Base):
    __tablename__ = "recordings"
'''
cloud = '''class CloudDay(Base):
    __tablename__ = "cloud_days"
    __table_args__ = (
        Index("ux_cloud_days_profile_day_provider", "profile_id", "day_key", "provider", unique=True),
        Index("ix_cloud_days_day_key", "day_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    day_key: Mapped[str] = mapped_column(String(10), index=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    remote_id: Mapped[str] = mapped_column(String(255), default="")
    remote_url: Mapped[str] = mapped_column(Text, default="")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Recording(Base):
    __tablename__ = "recordings"
'''
t = replace_once(t, anchor, cloud, "CloudDay model")
t = replace_once(t,
'''    remote_url: Mapped[str] = mapped_column(Text, default="")
    upload_attempts: Mapped[int] = mapped_column(Integer, default=0)
''',
'''    remote_url: Mapped[str] = mapped_column(Text, default="")
    cloud_day_key: Mapped[str] = mapped_column(String(10), default="", index=True)
    remote_parent_id: Mapped[str] = mapped_column(String(255), default="")
    remote_parent_url: Mapped[str] = mapped_column(Text, default="")
    upload_attempts: Mapped[int] = mapped_column(Integer, default=0)
''', "Recording cloud fields")
t = replace_once(t,
'''        "audio_codec": "VARCHAR(40) NOT NULL DEFAULT ''",
    }
''',
'''        "audio_codec": "VARCHAR(40) NOT NULL DEFAULT ''",
        "cloud_day_key": "VARCHAR(10) NOT NULL DEFAULT ''",
        "remote_parent_id": "VARCHAR(255) NOT NULL DEFAULT ''",
        "remote_parent_url": "TEXT NOT NULL DEFAULT ''",
    }
''', "recording migration fields")
t = replace_once(t,
'''        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recordings_upload_priority ON recordings (upload_priority)"))
''',
'''        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recordings_upload_priority ON recordings (upload_priority)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recordings_cloud_day_key ON recordings (cloud_day_key)"))
''', "cloud day index")
write(p, t)

# uploaders.py -----------------------------------------------------------
p = "app/uploaders.py"
t = read(p)
anchor = '''def provider_available(provider: str) -> bool:
'''
helper = '''def create_pixeldrain_list(title: str, file_ids: list[str]) -> tuple[str, str]:
    """Create one stable album for a completed recording day."""
    cfg = runtime()
    if not cfg.pixeldrain_api_key:
        raise UploadError("Pixeldrain API key non configurata")
    ids = list(dict.fromkeys(str(item).strip() for item in file_ids if str(item).strip()))
    if not ids:
        raise UploadError("Pixeldrain: impossibile creare una lista vuota")
    response = requests.post(
        "https://pixeldrain.com/api/list",
        auth=("", cfg.pixeldrain_api_key),
        headers={"Content-Type": "application/json"},
        json={"title": title[:300], "anonymous": False, "files": [{"id": item} for item in ids[:10000]]},
        timeout=60,
    )
    payload = _json(response, "Pixeldrain")
    if not payload.get("success", True):
        raise UploadError(payload.get("message") or "Pixeldrain: creazione lista fallita")
    remote_id = str(payload.get("id") or "")
    if not remote_id:
        raise UploadError("Pixeldrain non ha restituito un list id")
    return remote_id, f"https://pixeldrain.com/l/{remote_id}"


def provider_available(provider: str) -> bool:
'''
t = replace_once(t, anchor, helper, "pixeldrain list helper")
write(p, t)

# workers.py -------------------------------------------------------------
p = "app/workers.py"
t = read(p)
t = replace_once(t, "from pathlib import Path\n", "from pathlib import Path\nfrom zoneinfo import ZoneInfo\n", "ZoneInfo import")
t = replace_once(t,
"from .db import LiveSession, Recording, Source, db_session\n",
"from .db import CloudDay, LiveSession, Profile, Recording, Source, db_session\n", "worker db imports")
t = replace_once(t,
"from .uploaders import UploadCancelled, create_gofile_folder, provider_available, upload\n",
"from .uploaders import UploadCancelled, create_gofile_folder, create_pixeldrain_list, provider_available, upload\n", "worker uploader imports")
t = replace_once(t,
'''RETRYABLE_MEDIA_ERRORS = ("scadut", "timeout", "timed out", "tempor")


class WorkerManager:
''',
'''RETRYABLE_MEDIA_ERRORS = ("scadut", "timeout", "timed out", "tempor")
CLOUD_TIME_ZONE = ZoneInfo("Europe/Berlin")


def cloud_day_key(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CLOUD_TIME_ZONE).date().isoformat()


class WorkerManager:
''', "cloud timezone helper")
t = replace_once(t,
'''    async def _maintenance_backfill(self) -> None:
        while not self._stopping:
            await self._repair_local_mp4s()
            await self._backfill_thumbnails()
            await asyncio.sleep(60)
''',
'''    async def _maintenance_backfill(self) -> None:
        while not self._stopping:
            await self._repair_local_mp4s()
            await self._backfill_thumbnails()
            await self._finalize_closed_pixeldrain_days()
            await asyncio.sleep(60)
''', "maintenance pixel day")
old = '''    async def _gofile_folder_for(self, rec: Recording) -> tuple[str, str]:
        """Resolve or lazily create the stable Gofile folder for one source."""
        with db_session() as db:
            source = db.get(Source, rec.source_id)
            if not source or not source.organize_cloud:
                return "", ""
            existing_id = source.gofile_folder_id
            existing_url = source.gofile_folder_url
            source_name = source.name
        if existing_id:
            return existing_id, existing_url
        parent_id = runtime().gofile_folder_id
        folder_id, folder_url = await asyncio.to_thread(create_gofile_folder, source_name, parent_id)
        with db_session() as db:
            source = db.get(Source, rec.source_id)
            if source:
                if not source.gofile_folder_id:
                    source.gofile_folder_id = folder_id
                if folder_url and not source.gofile_folder_url:
                    source.gofile_folder_url = folder_url
                folder_id = source.gofile_folder_id
                folder_url = source.gofile_folder_url
        return folder_id, folder_url
'''
new = '''    def _cloud_day_spec(self, rec: Recording) -> tuple[int | None, str, str, bool]:
        day_key = cloud_day_key(rec.started_at)
        with db_session() as db:
            source = db.get(Source, rec.source_id)
            if not source:
                return None, day_key, f"{rec.source_name} - {day_key}", False
            profile = db.get(Profile, source.profile_id) if source.profile_id is not None else None
            display_name = profile.display_name if profile else source.name
            return source.profile_id, day_key, f"{display_name} - {day_key}"[:255], bool(source.organize_cloud)

    async def _gofile_folder_for(self, rec: Recording) -> tuple[str, str, str]:
        """Return the Gofile folder dedicated to this creator and Frankfurt calendar day."""
        profile_id, day_key, title, organize = self._cloud_day_spec(rec)
        if not organize or profile_id is None:
            return "", "", day_key
        with db_session() as db:
            existing = db.scalar(select(CloudDay).where(
                CloudDay.profile_id == profile_id,
                CloudDay.day_key == day_key,
                CloudDay.provider == "gofile",
            ))
            if existing and existing.remote_id:
                return existing.remote_id, existing.remote_url, day_key
        folder_id, folder_url = await asyncio.to_thread(
            create_gofile_folder, title, runtime().gofile_folder_id
        )
        with db_session() as db:
            row = db.scalar(select(CloudDay).where(
                CloudDay.profile_id == profile_id,
                CloudDay.day_key == day_key,
                CloudDay.provider == "gofile",
            ))
            if row is None:
                row = CloudDay(profile_id=profile_id, day_key=day_key, provider="gofile")
                db.add(row)
            row.title = title
            row.remote_id = folder_id
            row.remote_url = folder_url
            row.updated_at = utcnow()
        return folder_id, folder_url, day_key

    async def _finalize_closed_pixeldrain_days(self) -> None:
        """Create one Pixeldrain list after a Frankfurt day closes; never churn lists during the day."""
        today_key = datetime.now(CLOUD_TIME_ZONE).date().isoformat()
        with db_session() as db:
            rows = db.execute(
                select(Recording, Source.profile_id)
                .join(Source, Source.id == Recording.source_id)
                .where(
                    Recording.upload_status == "uploaded",
                    Recording.upload_provider == "pixeldrain",
                    Recording.remote_id != "",
                    Source.profile_id.is_not(None),
                )
                .order_by(Recording.started_at.asc(), Recording.id.asc())
            ).all()
            profile_ids = {int(profile_id) for _recording, profile_id in rows if profile_id is not None}
            profiles = {
                int(profile.id): profile.display_name
                for profile in db.scalars(select(Profile).where(Profile.id.in_(profile_ids))).all()
            } if profile_ids else {}
            existing = {
                (int(row.profile_id), row.day_key): (row.remote_id, int(row.file_count or 0))
                for row in db.scalars(select(CloudDay).where(CloudDay.provider == "pixeldrain")).all()
            }
        grouped: dict[tuple[int, str], list[tuple[int, str]]] = {}
        for recording, profile_id in rows:
            if profile_id is None:
                continue
            key = cloud_day_key(recording.started_at)
            if key >= today_key:
                continue
            grouped.setdefault((int(profile_id), key), []).append((int(recording.id), str(recording.remote_id)))
        for (profile_id, day_key), items in grouped.items():
            if self._stopping:
                return
            remote_ids = list(dict.fromkeys(remote_id for _recording_id, remote_id in items if remote_id))
            old_id, old_count = existing.get((profile_id, day_key), ("", 0))
            if old_id and old_count == len(remote_ids):
                continue
            title = f"{profiles.get(profile_id, f'Creator {profile_id}')} - {day_key}"[:300]
            try:
                list_id, list_url = await asyncio.to_thread(create_pixeldrain_list, title, remote_ids)
            except Exception as exc:
                self.last_errors[f"cloud-day:pixeldrain:{profile_id}:{day_key}"] = str(exc)[-900:]
                continue
            recording_ids = [recording_id for recording_id, _remote_id in items]
            with db_session() as db:
                row = db.scalar(select(CloudDay).where(
                    CloudDay.profile_id == profile_id,
                    CloudDay.day_key == day_key,
                    CloudDay.provider == "pixeldrain",
                ))
                if row is None:
                    row = CloudDay(profile_id=profile_id, day_key=day_key, provider="pixeldrain")
                    db.add(row)
                row.title = title
                row.remote_id = list_id
                row.remote_url = list_url
                row.file_count = len(remote_ids)
                row.updated_at = utcnow()
                for recording in db.scalars(select(Recording).where(Recording.id.in_(recording_ids))).all():
                    recording.cloud_day_key = day_key
                    recording.remote_parent_id = list_id
                    recording.remote_parent_url = list_url
            self.last_errors.pop(f"cloud-day:pixeldrain:{profile_id}:{day_key}", None)
'''
t = replace_once(t, old, new, "daily gofile + pixeldrain")
t = replace_once(t,
'''                        gofile_folder_id = ""
                        gofile_folder_url = ""
                        if provider == "gofile":
                            gofile_folder_id, gofile_folder_url = await self._gofile_folder_for(rec)
''',
'''                        gofile_folder_id = ""
                        gofile_folder_url = ""
                        recording_day_key = cloud_day_key(rec.started_at)
                        if provider == "gofile":
                            gofile_folder_id, gofile_folder_url, recording_day_key = await self._gofile_folder_for(rec)
''', "upload daily vars")
t = replace_once(t,
'''                            current.remote_url = result.remote_url
                            current.uploaded_at = utcnow()
                            current.last_error = ""
''',
'''                            current.remote_url = result.remote_url
                            current.cloud_day_key = recording_day_key
                            if result.provider == "gofile" and gofile_folder_id:
                                current.remote_parent_id = gofile_folder_id
                                current.remote_parent_url = gofile_folder_url
                            current.uploaded_at = utcnow()
                            current.last_error = ""
''', "recording daily destination")
t = replace_once(t,
'''                    if result.provider == "gofile":
                        with db_session() as db:
                            source = db.get(Source, rec.source_id)
                            if source and source.organize_cloud and gofile_folder_url and not source.gofile_folder_url:
                                source.gofile_folder_url = gofile_folder_url
''',
'''                    if result.provider == "gofile" and gofile_folder_id:
                        with db_session() as db:
                            profile_id, day_key, _title, _organize = self._cloud_day_spec(rec)
                            if profile_id is not None:
                                day = db.scalar(select(CloudDay).where(
                                    CloudDay.profile_id == profile_id,
                                    CloudDay.day_key == day_key,
                                    CloudDay.provider == "gofile",
                                ))
                                if day:
                                    day.file_count = int(day.file_count or 0) + 1
                                    day.updated_at = utcnow()
''', "gofile file count")
write(p, t)

# main.py ----------------------------------------------------------------
p = "app/main.py"
t = read(p)
t = replace_once(t, "from pathlib import Path\n", "from pathlib import Path\nfrom zoneinfo import ZoneInfo\n", "main ZoneInfo")
t = replace_once(t, "    Category,\n", "    Category,\n    CloudDay,\n", "main CloudDay import")
t = replace_once(t, 'VERSION = "2.8.5"', 'VERSION = "2.8.6"', "main version")
t = replace_once(t,
'''def _recording_json(r: Recording) -> dict:
''',
'''DISPLAY_TIME_ZONE = ZoneInfo("Europe/Berlin")


def _recording_day_key(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIME_ZONE).date().isoformat()


def _recording_json(r: Recording) -> dict:
''', "main day helper")
t = replace_once(t,
'''        "upload_status": r.upload_status, "upload_provider": r.upload_provider, "remote_url": r.remote_url,
        "collection_url": f"/?source={r.source_id}#archive",
''',
'''        "upload_status": r.upload_status, "upload_provider": r.upload_provider, "remote_url": r.remote_url,
        "cloud_day_key": r.cloud_day_key or _recording_day_key(r.started_at),
        "remote_parent_id": r.remote_parent_id, "remote_parent_url": r.remote_parent_url,
        "collection_url": f"/?source={r.source_id}#archive",
''', "recording json daily fields")
helper_anchor = '''@app.get("/api/sources/{source_id}/profile")
def source_profile(source_id: int, request: Request):
'''
helper = '''def _profile_recording_days(recordings: list[Recording], cloud_days: list[CloudDay], limit_days: int = 30) -> tuple[list[dict], int]:
    groups: dict[str, list[Recording]] = defaultdict(list)
    for recording in recordings:
        groups[recording.cloud_day_key or _recording_day_key(recording.started_at)].append(recording)
    day_keys = sorted((key for key in groups if key), reverse=True)
    cloud_map: dict[str, list[dict]] = defaultdict(list)
    for day in cloud_days:
        if day.remote_url:
            cloud_map[day.day_key].append({
                "provider": day.provider,
                "title": day.title,
                "remote_id": day.remote_id,
                "remote_url": day.remote_url,
                "file_count": int(day.file_count or 0),
            })
    payload = []
    for day_key in day_keys[:max(1, limit_days)]:
        rows = groups[day_key]
        payload.append({
            "date": day_key,
            "file_count": len(rows),
            "total_bytes": sum(int(row.size_bytes or 0) for row in rows),
            "total_duration_seconds": sum(float(row.duration_seconds or 0) for row in rows),
            "cloud_links": cloud_map.get(day_key, []),
            "recordings": [_recording_json(row) for row in rows],
        })
    return payload, len(day_keys)


@app.get("/api/sources/{source_id}/profile")
def source_profile(source_id: int, request: Request):
'''
t = replace_once(t, helper_anchor, helper, "profile day helper")
t = replace_once(t,
'''        recent = list(db.scalars(
            select(Recording)
            .where(Recording.source_id.in_(linked_source_ids))
            .order_by(Recording.finalized_at.desc(), Recording.id.desc())
            .limit(20)
        ).all())
        recent_payload = [_recording_json(recording) for recording in recent]
''',
'''        profile_recordings = list(db.scalars(
            select(Recording)
            .where(Recording.source_id.in_(linked_source_ids))
            .order_by(Recording.finalized_at.desc(), Recording.id.desc())
            .limit(2000)
        ).all())
        recent = profile_recordings[:20]
        recent_payload = [_recording_json(recording) for recording in recent]
        cloud_days = list(db.scalars(
            select(CloudDay).where(CloudDay.profile_id == profile.id).order_by(CloudDay.day_key.desc())
        ).all())
        recording_days, recording_day_count = _profile_recording_days(profile_recordings, cloud_days, 30)
''', "profile recording days query")
t = replace_once(t,
'''        return {"source": source_payload, "recent_recordings": recent_payload, "timeline": timeline}
''',
'''        return {
            "source": source_payload,
            "recent_recordings": recent_payload,
            "recording_days": recording_days,
            "recording_day_count": recording_day_count,
            "timeline": timeline,
        }
''', "profile day payload")
write(p, t)

# app.js ------------------------------------------------------------------
p = "app/static/app.js"
t = read(p)
old_recent = '''  const recent = (profileData.recent_recordings || []).slice(0, 8).map(recording => {
    const remote = safeUrl(recording.remote_url);
    return `<article class="profile-recording"><div><strong>${esc(recording.filename)}</strong><small>${esc(dateText(recording.started_at))} · ${esc(recording.size_human)} · ${esc(duration(recording.duration_seconds))}</small></div>${recording.local_available ? `<button class="btn quiet" data-profile-action="preview" data-id="${recording.id}" type="button">Vedi</button>` : ''}${remote ? `<a class="btn quiet" href="${esc(remote)}" target="_blank" rel="noopener">Cloud ↗</a>` : ''}</article>`;
  }).join('') || '<div class="empty compact">Nessuna registrazione.</div>';
'''
new_recent = '''  const recordingDays = (profileData.recording_days || []).map((day, index) => {
    const cloudLinks = (day.cloud_links || []).map(link => {
      const url = safeUrl(link.remote_url);
      return url ? `<a class="btn quiet" href="${esc(url)}" target="_blank" rel="noopener">${esc(link.provider)} ↗</a>` : '';
    }).join('');
    const videos = (day.recordings || []).map(recording => {
      const remote = safeUrl(recording.remote_url);
      const thumb = recording.thumbnail_available ? safeUrl(recording.thumbnail_url) : '';
      const visual = thumb ? `<img src="${esc(thumb)}" loading="lazy" alt="${esc(recording.filename)}">` : '<span>LV</span>';
      const thumbnail = remote
        ? `<a class="profile-day-thumb ${thumb ? '' : 'empty'}" href="${esc(remote)}" target="_blank" rel="noopener" aria-label="Apri video ${esc(recording.filename)}">${visual}</a>`
        : recording.local_available
          ? `<button class="profile-day-thumb ${thumb ? '' : 'empty'}" data-profile-action="preview" data-id="${recording.id}" type="button">${visual}</button>`
          : `<div class="profile-day-thumb ${thumb ? '' : 'empty'}">${visual}</div>`;
      return `<article class="profile-day-video">${thumbnail}<div class="profile-day-video-body"><strong>${esc(recording.filename)}</strong><small>${esc(dateText(recording.started_at))} · ${esc(recording.size_human)} · ${esc(duration(recording.duration_seconds))}</small><div>${remote ? `<a class="btn quiet" href="${esc(remote)}" target="_blank" rel="noopener">Apri video ↗</a>` : '<span class="muted">Non caricato</span>'}</div></div></article>`;
    }).join('');
    return `<details class="profile-day" ${index === 0 ? 'open' : ''}><summary><div><strong>${esc(day.date)}</strong><small>${day.file_count || 0} file · ${esc(humanBytes(day.total_bytes || 0))} · ${esc(duration(day.total_duration_seconds || 0))}</small></div><div class="profile-day-links">${cloudLinks}</div></summary><div class="profile-day-videos">${videos}</div></details>`;
  }).join('') || '<div class="empty compact">Nessuna registrazione.</div>';
'''
t = replace_once(t, old_recent, new_recent, "profile day renderer")
t = replace_once(t,
'''    <section class="profile-section"><div class="profile-section-head"><h3>Registrazioni recenti</h3><button class="btn quiet" data-profile-action="archive" data-id="${profile.id}" type="button">Apri archivio</button></div><div class="profile-recordings">${recent}</div></section>
''',
'''    <section class="profile-section"><div class="profile-section-head"><h3>Giornate</h3><button class="btn quiet" data-profile-action="archive" data-id="${profile.id}" type="button">Archivio${Number(profileData.recording_day_count || 0) > Number((profileData.recording_days || []).length) ? ' · altre' : ''}</button></div><div class="profile-days">${recordingDays}</div></section>
''', "profile days section")
# Archive thumbnails open the uploaded file when available; local preview remains fallback.
old_thumb = '''    return `<article class="rec-card">
      <button class="thumb ${thumbnail ? '' : 'empty'}" data-rec-action="preview" data-id="${recording.id}" type="button" aria-label="Anteprima ${esc(recording.filename)}">${thumbnail || '<span>LV</span>'}${recording.local_available ? '<span class="play-badge">▶ Anteprima</span>' : ''}</button>
'''
new_thumb = '''    const thumbControl = remote
      ? `<a class="thumb ${thumbnail ? '' : 'empty'}" href="${esc(remote)}" target="_blank" rel="noopener" aria-label="Apri video ${esc(recording.filename)}">${thumbnail || '<span>LV</span>'}<span class="play-badge">↗ Video</span></a>`
      : `<button class="thumb ${thumbnail ? '' : 'empty'}" data-rec-action="preview" data-id="${recording.id}" type="button" aria-label="Anteprima ${esc(recording.filename)}">${thumbnail || '<span>LV</span>'}${recording.local_available ? '<span class="play-badge">▶ Anteprima</span>' : ''}</button>`;
    return `<article class="rec-card">
      ${thumbControl}
'''
t = replace_once(t, old_thumb, new_thumb, "archive thumbnail remote link")
write(p, t)

# CSS --------------------------------------------------------------------
p = "app/static/enhancements.css"
t = read(p)
css = '''

/* v2.8.6 creator recording days */
.profile-days{display:grid;gap:10px}.profile-day{border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.018);overflow:hidden}.profile-day>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;cursor:pointer;list-style:none}.profile-day>summary::-webkit-details-marker{display:none}.profile-day>summary>div:first-child{display:grid;gap:2px}.profile-day>summary small{color:var(--muted)}.profile-day-links{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}.profile-day-videos{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px;padding:0 12px 12px}.profile-day-video{display:grid;grid-template-columns:112px minmax(0,1fr);gap:10px;border-top:1px solid var(--line);padding-top:10px}.profile-day-thumb{display:flex;aspect-ratio:16/9;border-radius:10px;overflow:hidden;background:var(--surface-2);border:0;padding:0;color:inherit;text-decoration:none;align-items:center;justify-content:center}.profile-day-thumb img{width:100%;height:100%;object-fit:cover}.profile-day-video-body{min-width:0;display:grid;align-content:start;gap:4px}.profile-day-video-body strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.profile-day-video-body small{color:var(--muted)}@media(max-width:680px){.profile-day>summary{align-items:flex-start}.profile-day-videos{grid-template-columns:1fr}.profile-day-video{grid-template-columns:104px minmax(0,1fr)}.profile-day-links .btn{padding:6px 8px}}
'''
if "/* v2.8.6 creator recording days */" not in t:
    t += css
write(p, t)

# Version/docs ------------------------------------------------------------
write("VERSION", "2.8.6\n")
for p in ("README.md", "START_HERE.md"):
    t = read(p).replace("v2.8.5", "v2.8.6", 1)
    write(p, t)
p = "app/static/sw.js"
t = read(p).replace("livevault-shell-v2.8.5", "livevault-shell-v2.8.6")
write(p, t)
p = "CHANGELOG.md"
t = read(p)
entry = '''## 2.8.6\n\n- Cartelle Gofile giornaliere per creator: `NOME CREATOR - YYYY-MM-DD`.\n- Giornata calcolata in `Europe/Berlin` per allinearsi agli orari Frankfurt della UI.\n- PixelDrain crea una lista giornaliera unica quando la giornata si chiude.\n- Profilo creator organizzato per giornate con video e link cloud dedicati.\n- Click sulla miniatura apre il singolo video remoto quando disponibile.\n\n'''
if "## 2.8.6" not in t:
    idx = t.find("## ")
    t = (t[:idx] + entry + t[idx:]) if idx >= 0 else entry + t
write(p, t)

# Tests ------------------------------------------------------------------
test = '''from datetime import datetime, timezone\nfrom pathlib import Path\n\nfrom app.db import CloudDay, Recording\nfrom app.workers import cloud_day_key\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_cloud_day_uses_frankfurt_calendar_boundary():\n    assert cloud_day_key(datetime(2026, 9, 3, 21, 59, tzinfo=timezone.utc)) == "2026-09-03"\n    assert cloud_day_key(datetime(2026, 9, 3, 22, 1, tzinfo=timezone.utc)) == "2026-09-04"\n\n\ndef test_daily_cloud_models_keep_file_and_parent_links_separate():\n    assert hasattr(Recording, "remote_url")\n    assert hasattr(Recording, "remote_parent_url")\n    assert hasattr(Recording, "cloud_day_key")\n    assert hasattr(CloudDay, "day_key")\n    assert hasattr(CloudDay, "provider")\n\n\ndef test_profile_days_and_thumbnail_remote_link_are_present():\n    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")\n    main = (ROOT / "app/main.py").read_text(encoding="utf-8")\n    assert "profileData.recording_days" in js\n    assert "profile-day-thumb" in js\n    assert 'href="${esc(remote)}"' in js\n    assert '"recording_days": recording_days' in main\n    assert '"remote_parent_url": r.remote_parent_url' in main\n\n\ndef test_pixeldrain_closed_day_album_and_gofile_daily_folder_code_present():\n    uploaders = (ROOT / "app/uploaders.py").read_text(encoding="utf-8")\n    workers = (ROOT / "app/workers.py").read_text(encoding="utf-8")\n    assert "def create_pixeldrain_list" in uploaders\n    assert "https://pixeldrain.com/api/list" in uploaders\n    assert "https://pixeldrain.com/l/{remote_id}" in uploaders\n    assert "async def _finalize_closed_pixeldrain_days" in workers\n    assert 'CloudDay.provider == "gofile"' in workers\n\n\ndef test_v286_version_and_cache():\n    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "2.8.6"\n    assert 'VERSION = "2.8.6"' in (ROOT / "app/main.py").read_text(encoding="utf-8")\n    assert "livevault-shell-v2.8.6" in (ROOT / "app/static/sw.js").read_text(encoding="utf-8")\n'''
write("tests/test_v286_daily_cloud.py", test)

print("v2.8.6 daily cloud patch applied")
