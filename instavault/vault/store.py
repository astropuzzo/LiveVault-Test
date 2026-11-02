from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

DATA = Path(os.environ.get('INSTAVAULT_DATA', '/data'))
MEDIA = Path(os.environ.get('INSTAVAULT_MEDIA', '/media'))
SECRETS = Path(os.environ.get('INSTAVAULT_SECRETS', '/run/secrets'))
STATIC = Path(__file__).resolve().parents[1] / 'static'


def now():
    return int(time.time())


def username(value):
    value = str(value).strip()
    if '://' in value:
        parsed = urlparse(value)
        if parsed.scheme != 'https' or parsed.hostname not in {'instagram.com', 'www.instagram.com'}:
            raise ValueError('Inserisci uno username o un URL Instagram valido.')
        value = parsed.path.strip('/')
    value = value.lstrip('@').lower()
    if not re.fullmatch(r'[a-z0-9_](?:[a-z0-9_.]{0,28}[a-z0-9_])?', value) or '..' in value:
        raise ValueError('Username Instagram non valido.')
    if value in {'p', 'reel', 'reels', 'stories', 'explore', 'accounts', 'direct'}:
        raise ValueError('Inserisci il profilo, non il link a un post.')
    return value


@contextlib.contextmanager
def db():
    connection = sqlite3.connect(DATA / 'instavault.sqlite3', timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=15000')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS profiles (
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL DEFAULT '',
          biography TEXT NOT NULL DEFAULT '', instagram_id TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT '',
          enabled INTEGER NOT NULL DEFAULT 1, posts INTEGER NOT NULL DEFAULT 1, reels INTEGER NOT NULL DEFAULT 1,
          stories INTEGER NOT NULL DEFAULT 1, highlights INTEGER NOT NULL DEFAULT 0,
          interval_hours INTEGER NOT NULL DEFAULT 6, status TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL, last_sync INTEGER, next_sync INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS posts (
          id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profiles(id), external_key TEXT NOT NULL,
          shortcode TEXT NOT NULL, kind TEXT NOT NULL, caption TEXT NOT NULL DEFAULT '', published_at INTEGER NOT NULL,
          archived_at INTEGER NOT NULL, permalink TEXT NOT NULL DEFAULT '', collection TEXT NOT NULL DEFAULT '',
          favorite INTEGER NOT NULL DEFAULT 0, complete INTEGER NOT NULL DEFAULT 0,UNIQUE(profile_id,external_key));
        CREATE INDEX IF NOT EXISTS posts_order ON posts(complete,published_at DESC,id DESC);
        CREATE INDEX IF NOT EXISTS posts_profile ON posts(profile_id,complete,published_at DESC);
        CREATE TABLE IF NOT EXISTS media (
          id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES posts(id), position INTEGER NOT NULL,
          kind TEXT NOT NULL, path TEXT NOT NULL, thumb TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL,
          sha256 TEXT NOT NULL, width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
          UNIQUE(post_id,position));
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES profiles(id), state TEXT NOT NULL DEFAULT 'queued',
          created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER, seen INTEGER NOT NULL DEFAULT 0,
          downloaded INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '');
        CREATE UNIQUE INDEX IF NOT EXISTS one_job ON jobs(profile_id) WHERE state IN ('queued','running','cancel_requested');
        CREATE TABLE IF NOT EXISTS cursors (
          profile_id INTEGER NOT NULL REFERENCES profiles(id), kind TEXT NOT NULL, data TEXT,
          complete INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(profile_id,kind));
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        for key, value in {'automatic': True, 'quota_gb': 60, 'min_free_gb': 15}.items():
            c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, json.dumps(value)))


def settings():
    with db() as c:
        return {r['key']: json.loads(r['value']) for r in c.execute('SELECT * FROM settings')}


def storage_ready():
    try:
        return (MEDIA / '.instavault-volume').read_text().strip() == 'instavault-v1'
    except OSError:
        return False


def safe_file(relative):
    if not storage_ready():
        raise FileNotFoundError('Archivio NVMe non disponibile.')
    p = (MEDIA / relative).resolve(strict=True)
    if not p.is_relative_to(MEDIA.resolve()) or not p.is_file() or p.is_symlink():
        raise FileNotFoundError('File non disponibile.')
    return p


def storage_status():
    ready = storage_ready()
    with db() as c:
        used = c.execute('SELECT COALESCE(SUM(size),0) FROM media').fetchone()[0]
    return {'ready': ready, 'used_bytes': used,
            'free_bytes': shutil.disk_usage(MEDIA).free if ready else None,
            'quota_bytes': settings()['quota_gb'] * 1024**3}


def enqueue(profile_id):
    with db() as c:
        profile = c.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()
        if not profile:
            raise ValueError('Profilo non trovato.')
        existing = c.execute("SELECT id FROM jobs WHERE profile_id=? AND state IN ('queued','running','cancel_requested')", (profile_id,)).fetchone()
        if existing:
            return existing['id']
        job_id = c.execute('INSERT INTO jobs(profile_id,created_at) VALUES (?,?)', (profile_id, now())).lastrowid
        c.execute("UPDATE profiles SET status='queued',error='' WHERE id=?", (profile_id,))
        return job_id


def profile_rows():
    with db() as c:
        return [dict(r) for r in c.execute('''SELECT p.*,
          (SELECT COUNT(*) FROM posts WHERE profile_id=p.id AND complete=1) AS post_count,
          (SELECT COUNT(*) FROM media m JOIN posts x ON m.post_id=x.id WHERE x.profile_id=p.id AND x.complete=1) AS media_count
          FROM profiles p ORDER BY p.username''')]


def post_json(c, row, detail=False):
    value = dict(row)
    media = [dict(r) for r in c.execute('SELECT id,kind,position,width,height,size FROM media WHERE post_id=? ORDER BY position', (row['id'],))]
    value['media'] = media
    if not detail:
        value['caption'] = value['caption'][:260]
    return value


def list_posts(query):
    conditions, args = ['p.complete=1'], []
    for key, column in [('profile', 'p.profile_id'), ('kind', 'p.kind')]:
        if query.get(key):
            conditions.append(column + '=?'); args.append(query[key])
    if query.get('favorite') == '1':
        conditions.append('p.favorite=1')
    if query.get('q'):
        conditions.append('(p.caption LIKE ? ESCAPE \'\\\' OR r.username LIKE ? ESCAPE \'\\\')')
        term = str(query['q'])[:150].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        args += ['%' + term + '%'] * 2
    older = query.get('order') == 'oldest'
    if query.get('cursor'):
        stamp, ident = map(int, query['cursor'].split(':'))
        conditions.append('(p.published_at,p.id) ' + ('>' if older else '<') + ' (?,?)'); args += [stamp, ident]
    direction = 'ASC' if older else 'DESC'
    sql = '''SELECT p.*,r.username,r.full_name,r.avatar FROM posts p JOIN profiles r ON p.profile_id=r.id
          WHERE ''' + ' AND '.join(conditions) + f' ORDER BY p.published_at {direction},p.id {direction} LIMIT 49'
    with db() as c:
        rows = list(c.execute(sql, args)); has_more = len(rows) > 48; rows = rows[:48]
        return {'items': [post_json(c, r) for r in rows],
                'next': f"{rows[-1]['published_at']}:{rows[-1]['id']}" if has_more and rows else None}
