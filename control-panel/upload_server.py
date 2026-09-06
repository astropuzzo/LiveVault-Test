#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

import media_center
import server as panel


UPLOAD_CHUNK = 1024 * 1024
UPLOAD_RESERVE_BYTES = 256 * 1024 * 1024
UPLOAD_MAX_BYTES = 8 * 1024 ** 4


def _upload_name(value: str, folder: Path) -> str:
    name = value.strip()
    if not name or name in {'.', '..'} or '\x00' in name or '/' in name or '\\' in name:
        raise ValueError('Nome file non valido')
    if Path(name).name != name or name.startswith('.openastro-upload-'):
        raise ValueError('Nome file non valido')
    try:
        name_max = os.pathconf(folder, 'PC_NAME_MAX')
    except (OSError, ValueError):
        name_max = 255
    if len(name.encode('utf-8')) > name_max:
        raise ValueError('Nome file troppo lungo')
    return name


def _remember_uploaded_file(uuid: str, root: Path, target: Path, client: str) -> None:
    st = target.stat()
    mime = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
    row = {
        'name': target.name,
        'path': target.relative_to(root).as_posix(),
        'size': st.st_size,
        'modified': int(st.st_mtime),
        'mime': mime,
        'category': media_center._category(target, mime),
    }
    token = int(time.time_ns() // 1_000_000)
    now = int(time.time())
    with media_center._db() as conn:
        media_center._upsert_item(conn, uuid, row, token)
        totals = conn.execute(
            'SELECT COUNT(*) c,COALESCE(SUM(size),0) b FROM media_items WHERE uuid=?',
            (uuid,),
        ).fetchone()
        conn.execute(
            'UPDATE media_devices SET total_files=?,total_bytes=?,last_seen=? WHERE uuid=?',
            (int(totals['c']), int(totals['b']), now, uuid),
        )
        conn.execute(
            'INSERT INTO media_events(ts,kind,uuid,path,detail) VALUES(?,?,?,?,?)',
            (now, 'upload', uuid, row['path'], json.dumps({'bytes': st.st_size, 'client': client})),
        )
    media_center._LIBRARY_CACHE.pop(uuid, None)


class Handler(panel.Handler):
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != '/api/media/upload':
            return super().do_POST()

        session = self.require_session()
        if not session:
            return
        if self.headers.get('X-CSRF-Token') != session['csrf']:
            self.send_json({'ok': False, 'error': 'Sessione scaduta: ricarica la pagina.'}, 403)
            return

        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if length <= 0:
            self.send_json({'ok': False, 'error': 'File vuoto o Content-Length mancante.'}, 400)
            return
        if length > UPLOAD_MAX_BYTES:
            self.send_json({'ok': False, 'error': 'File troppo grande.'}, 413)
            return

        query = parse_qs(parsed.query)
        uuid = str(query.get('uuid', [''])[0]).strip()
        relative = str(query.get('path', [''])[0]).strip('/')
        requested_name = str(query.get('name', [''])[0])
        temporary: Path | None = None
        try:
            _item, root, folder = media_center._safe_target(uuid, relative, require_file=False)
            name = _upload_name(requested_name, folder)
            if not os.access(folder, os.W_OK):
                raise PermissionError('Il supporto è montato in sola lettura. Rimontalo in modalità scrivibile.')
            usage = shutil.disk_usage(folder)
            available = max(0, usage.free - min(UPLOAD_RESERVE_BYTES, usage.total // 20))
            if length > available:
                self.send_json({'ok': False, 'error': 'Spazio libero insufficiente sul supporto.'}, 507)
                return
            target = folder / name
            if target.exists() or target.is_symlink():
                self.send_json({'ok': False, 'error': 'Esiste già un file con questo nome.'}, 409)
                return

            temporary = folder / f'.openastro-upload-{secrets.token_hex(8)}.part'
            remaining = length
            with temporary.open('xb') as handle:
                while remaining:
                    chunk = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                    if not chunk:
                        raise ConnectionError('Upload interrotto prima del completamento.')
                    handle.write(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
            try:
                target.chmod(0o644)
            except OSError:
                pass
            _remember_uploaded_file(uuid, root, target, self.client_key())
            rel = target.relative_to(root).as_posix()
            self.send_json({
                'ok': True,
                'uuid': uuid,
                'name': target.name,
                'path': rel,
                'size': target.stat().st_size,
                'message': f'{target.name} caricato. MiniDLNA lo rileverà tramite inotify.',
            })
        except PermissionError as exc:
            self.send_json({'ok': False, 'error': str(exc)}, 403)
        except (ValueError, FileNotFoundError, NotADirectoryError) as exc:
            self.send_json({'ok': False, 'error': str(exc)}, 400)
        except (ConnectionError, BrokenPipeError, ConnectionResetError):
            # The browser may cancel a transfer. Never leave a partial file in the library.
            pass
        except OSError as exc:
            self.send_json({'ok': False, 'error': f'Errore scrittura supporto: {exc.strerror or exc}'}, 500)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


if __name__ == '__main__':
    mimetypes.add_type('application/manifest+json', '.webmanifest')
    panel.load_history()
    panel.load_availability()
    panel.threading.Thread(target=panel.history_loop, name='telemetry', daemon=True).start()
    server = panel.ThreadingHTTPServer((panel.HOST, panel.PORT), Handler)
    print(f'OpenAstro Control listening on http://{panel.HOST}:{panel.PORT}', flush=True)
    server.serve_forever()
