"""Followers, following and likes, read slowly so no account is overloaded.

A list is walked by one account from start to end (a "pass"): Instagram orders
it differently for each account, so a pass never changes account halfway. A
batch reads up to BATCH_PAGES pages with random pauses, then the account rests
for hours. A pass counts as complete only when it saw most of the people the
profile shows; someone is marked gone only after two complete passes without
them. Instagram truncates the followers of big profiles the account does not
follow: such a list is marked capped and never used to find who left.
"""
from __future__ import annotations

import random
import time

from . import store, sync

LISTS = ('followers', 'following')
PAGE_SIZE = 25
BATCH_PAGES = 40            # about 1000 people
DAILY_PAGES = 120           # per account, all lists and likes together
ACCOUNT_REST = (2 * 3600, 4 * 3600)
LIST_REST = (3600, 3 * 3600)
COMPLETE_SHARE = 0.95
LIKE_POSTS = 5              # posts per batch whose likers are read
LIKE_WINDOW = 30 * 86400
PROBLEM_REST = {'pause': 48 * 3600, 'login': 12 * 3600, None: 6 * 3600}


def initialize(c):
    c.execute('''CREATE TABLE IF NOT EXISTS social_people (
      profile_id INTEGER NOT NULL, list TEXT NOT NULL, pk TEXT NOT NULL, username TEXT NOT NULL,
      full_name TEXT NOT NULL DEFAULT '', first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
      last_pass INTEGER NOT NULL, gone_at INTEGER, PRIMARY KEY(profile_id, list, pk))''')
    c.execute('''CREATE TABLE IF NOT EXISTS social_crawl (
      profile_id INTEGER NOT NULL, list TEXT NOT NULL, account TEXT NOT NULL DEFAULT '', cursor TEXT NOT NULL DEFAULT '',
      pass INTEGER NOT NULL DEFAULT 1, pass_started INTEGER, expected INTEGER NOT NULL DEFAULT 0,
      complete_passes INTEGER NOT NULL DEFAULT 0, last_complete INTEGER NOT NULL DEFAULT 0,
      capped INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(profile_id, list))''')
    c.execute('''CREATE TABLE IF NOT EXISTS social_events (
      id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL, list TEXT NOT NULL, pk TEXT NOT NULL,
      username TEXT NOT NULL, kind TEXT NOT NULL, at INTEGER NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS social_events_profile ON social_events(profile_id, at)')
    c.execute('''CREATE TABLE IF NOT EXISTS likes (
      post_id INTEGER NOT NULL, pk TEXT NOT NULL, username TEXT NOT NULL, full_name TEXT NOT NULL DEFAULT '',
      first_seen INTEGER NOT NULL, PRIMARY KEY(post_id, pk))''')
    columns = {r[1] for r in c.execute('PRAGMA table_info(posts)')}
    if 'likes_at' not in columns:
        c.execute('ALTER TABLE posts ADD COLUMN likes_at INTEGER')
    columns = {r[1] for r in c.execute('PRAGMA table_info(ig_accounts)')}
    for column, kind in [('social_day', "TEXT NOT NULL DEFAULT ''"), ('social_pages', 'INTEGER NOT NULL DEFAULT 0'),
                         ('social_next', 'INTEGER NOT NULL DEFAULT 0')]:
        if column not in columns:
            c.execute(f'ALTER TABLE ig_accounts ADD COLUMN {column} {kind}')
    columns = {r[1] for r in c.execute('PRAGMA table_info(profiles)')}
    if 'social' not in columns:
        c.execute('ALTER TABLE profiles ADD COLUMN social INTEGER NOT NULL DEFAULT 1')
    if 'is_private' not in columns:
        c.execute('ALTER TABLE profiles ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0')


def busy():
    """Downloads come first: a queued job or a due profile ends the batch."""
    with store.db() as c:
        return bool(c.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone()
                    or (store.settings()['automatic'] and c.execute(
                        'SELECT 1 FROM profiles WHERE enabled=1 AND removed=0 AND next_sync<=? LIMIT 1', (store.now(),)).fetchone()))


def pause(low, high):
    sync.STOP.wait(random.uniform(low, high))
    return not sync.STOP.is_set()


def budget_left(account):
    today = time.strftime('%Y-%m-%d', time.gmtime())
    used = account['social_pages'] if account['social_day'] == today else 0
    return DAILY_PAGES - used


def spend(account_id, pages=1):
    today = time.strftime('%Y-%m-%d', time.gmtime())
    with store.db() as c:
        c.execute('''UPDATE ig_accounts SET social_pages=CASE WHEN social_day=? THEN social_pages+? ELSE ? END,
            social_day=? WHERE id=?''', (today, pages, pages, today, account_id))


def candidates(profile):
    """Accounts allowed to read this profile now: usable, with budget, rested.
    A private profile is read only by the account that already sees it."""
    rows = [a for a in sync.accounts() if sync.usable(a) and budget_left(a) > 0 and a['social_next'] <= store.now()]
    if profile.get('is_private'):
        rows = [a for a in rows if a['id'] == profile.get('account_used')]
    return sorted(rows, key=lambda a: (a['social_day'] == time.strftime('%Y-%m-%d', time.gmtime()) and a['social_pages'], random.random()))


def due_crawl():
    with store.db() as c:
        for profile in c.execute("SELECT id FROM profiles WHERE enabled=1 AND removed=0 AND social=1 AND instagram_id<>''"):
            for name in LISTS:
                c.execute('INSERT OR IGNORE INTO social_crawl(profile_id,list,next_at) VALUES (?,?,?)',
                          (profile['id'], name, store.now() + random.randint(0, 1800)))
        return [dict(r) for r in c.execute('''SELECT s.*, p.username, p.instagram_id, p.is_private, p.account_used
            FROM social_crawl s JOIN profiles p ON p.id=s.profile_id
            WHERE p.enabled=1 AND p.removed=0 AND p.social=1 AND s.next_at<=? ORDER BY s.next_at''', (store.now(),))]


def expected_count(profile_id, name):
    with store.db() as c:
        row = c.execute('SELECT followers, following FROM profile_history WHERE profile_id=? ORDER BY at DESC, id DESC LIMIT 1',
                        (profile_id,)).fetchone()
    return int((row[name] if row else 0) or 0)


def save_people(crawl, users):
    now = store.now()
    with store.db() as c:
        for user in users:
            pk, username = str(user.get('pk') or user.get('pk_id') or ''), str(user.get('username') or '')
            if not pk or not username:
                continue
            known = c.execute('SELECT gone_at FROM social_people WHERE profile_id=? AND list=? AND pk=?',
                              (crawl['profile_id'], crawl['list'], pk)).fetchone()
            c.execute('''INSERT INTO social_people(profile_id,list,pk,username,full_name,first_seen,last_seen,last_pass)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(profile_id,list,pk) DO UPDATE SET username=excluded.username,
                full_name=excluded.full_name, last_seen=excluded.last_seen, last_pass=excluded.last_pass, gone_at=NULL''',
                      (crawl['profile_id'], crawl['list'], pk, username, str(user.get('full_name') or ''), now, now, crawl['pass']))
            # Arrivals count only once a whole list is known; before that everyone would be new.
            if crawl['complete_passes'] and (known is None or known['gone_at']):
                c.execute('INSERT INTO social_events(profile_id,list,pk,username,kind,at) VALUES (?,?,?,?,?,?)',
                          (crawl['profile_id'], crawl['list'], pk, username, 'new', now))


def finish_pass(crawl, pages_this_batch, first_page_users):
    """The list ended: decide whether the pass is complete, find who left, start the next one."""
    now = store.now()
    with store.db() as c:
        seen = c.execute('SELECT COUNT(*) FROM social_people WHERE profile_id=? AND list=? AND last_pass=?',
                         (crawl['profile_id'], crawl['list'], crawl['pass'])).fetchone()[0]
        expected = crawl['expected'] or expected_count(crawl['profile_id'], crawl['list'])
        if crawl['pass'] == 1 and not crawl['cursor'] and pages_this_batch == 1 and expected > 200 and first_page_users < expected / 2:
            # One short page for a big list: Instagram hides the rest from this account.
            c.execute('UPDATE social_crawl SET capped=1, cursor=\'\', next_at=? WHERE profile_id=? AND list=?',
                      (now + 7 * 86400, crawl['profile_id'], crawl['list']))
            return
        complete = expected and seen >= expected * COMPLETE_SHARE
        last_complete = crawl['last_complete']
        if complete:
            if last_complete == crawl['pass'] - 1 and last_complete:
                gone = list(c.execute('''SELECT pk, username FROM social_people WHERE profile_id=? AND list=?
                    AND gone_at IS NULL AND last_pass<=?''', (crawl['profile_id'], crawl['list'], crawl['pass'] - 2)))
                for row in gone:
                    c.execute('UPDATE social_people SET gone_at=? WHERE profile_id=? AND list=? AND pk=?',
                              (now, crawl['profile_id'], crawl['list'], row['pk']))
                    c.execute('INSERT INTO social_events(profile_id,list,pk,username,kind,at) VALUES (?,?,?,?,?,?)',
                              (crawl['profile_id'], crawl['list'], row['pk'], row['username'], 'gone', now))
            last_complete = crawl['pass']
        c.execute('''UPDATE social_crawl SET pass=pass+1, cursor='', account='', pass_started=NULL, expected=0, capped=0,
            complete_passes=complete_passes+?, last_complete=?, next_at=? WHERE profile_id=? AND list=?''',
                  (1 if complete else 0, last_complete, now + random.randint(*LIST_REST), crawl['profile_id'], crawl['list']))


def read_likes(source, account_id, profile_id, limit=LIKE_POSTS):
    with store.db() as c:
        posts = list(c.execute('''SELECT id, shortcode FROM posts WHERE profile_id=? AND complete=1 AND kind IN ('post','reel')
            AND published_at>=? AND (likes_at IS NULL OR likes_at<?) ORDER BY published_at DESC LIMIT ?''',
                               (profile_id, store.now() - LIKE_WINDOW, store.now() - 86400, limit)))
    for post in posts:
        if sync.STOP.is_set() or busy():
            return
        users = source.client.private_request(f'media/{source.client.media_pk_from_code(post["shortcode"])}/likers/').get('users', [])
        spend(account_id)
        with store.db() as c:
            for user in users:
                if user.get('pk') and user.get('username'):
                    c.execute('INSERT OR IGNORE INTO likes(post_id,pk,username,full_name,first_seen) VALUES (?,?,?,?,?)',
                              (post['id'], str(user['pk']), user['username'], str(user.get('full_name') or ''), store.now()))
            c.execute('UPDATE posts SET likes_at=? WHERE id=?', (store.now(), post['id']))
        if not pause(4, 12):
            return


def step(loader=None):
    """One batch for the most overdue list, if an account may read it now. True when something ran."""
    loader = loader or sync.loader
    if busy():
        return False
    for crawl in due_crawl():
        if crawl['capped']:
            continue
        profile = {'is_private': crawl['is_private'], 'account_used': crawl['account_used']}
        allowed = candidates(profile)
        if crawl['account']:
            # A pass keeps its account: another one sees the list in a different order.
            allowed = [a for a in allowed if a['id'] == crawl['account']]
            if not allowed and not any(a['id'] == crawl['account'] and sync.usable(a) for a in sync.accounts()):
                with store.db() as c:  # that account is gone or broken: start over with another
                    c.execute("UPDATE social_crawl SET account='', cursor='' WHERE profile_id=? AND list=?",
                              (crawl['profile_id'], crawl['list']))
        if not allowed:
            continue
        account = allowed[0]
        run_batch(crawl, account, loader)
        return True
    return False


def run_batch(crawl, account, loader):
    with store.db() as c:
        if not crawl['account']:
            crawl['expected'] = expected_count(crawl['profile_id'], crawl['list'])
            c.execute('UPDATE social_crawl SET account=?, pass_started=?, expected=? WHERE profile_id=? AND list=?',
                      (account['id'], store.now(), crawl['expected'], crawl['profile_id'], crawl['list']))
            crawl['account'] = account['id']
    source = None
    pages, first_page_users = 0, 0
    try:
        source = loader(account['id'])
        if crawl['list'] == 'followers':
            read_likes(source, account['id'], crawl['profile_id'])
        cursor = crawl['cursor']
        limit = min(BATCH_PAGES, budget_left(next(a for a in sync.accounts() if a['id'] == account['id'])))
        while pages < limit and not sync.STOP.is_set() and not busy():
            params = {'count': PAGE_SIZE}
            if crawl['list'] == 'followers':
                params['search_surface'] = 'follow_list_page'
            if cursor:
                params['max_id'] = cursor
            reply = source.client.private_request(f'friendships/{crawl["instagram_id"]}/{crawl["list"]}/', params=params)
            spend(account['id']); pages += 1
            users = reply.get('users', [])
            if pages == 1:
                first_page_users = len(users)
            save_people(crawl, users)
            cursor = str(reply.get('next_max_id') or '')
            with store.db() as c:
                c.execute('UPDATE social_crawl SET cursor=? WHERE profile_id=? AND list=?', (cursor, crawl['profile_id'], crawl['list']))
            if not cursor:
                finish_pass(crawl, pages, first_page_users)
                break
            crawl['cursor'] = cursor
            # Short random pauses between pages, a longer one every ten.
            if not (pause(25, 75) if pages % 10 == 0 else pause(3, 9)):
                break
        with store.db() as c:
            if cursor:
                c.execute('UPDATE social_crawl SET next_at=? WHERE profile_id=? AND list=?',
                          (store.now() + random.randint(*LIST_REST), crawl['profile_id'], crawl['list']))
            c.execute('UPDATE ig_accounts SET social_next=? WHERE id=?', (store.now() + random.randint(*ACCOUNT_REST), account['id']))
    except Exception as exc:
        # The profile's own downloads are not affected; only this account rests longer.
        kind = sync.note_account_problem(exc, account['id'])
        print(f'InstaVault: social @{crawl["username"]} {crawl["list"]}: {type(exc).__name__}', flush=True)
        with store.db() as c:
            c.execute('UPDATE ig_accounts SET social_next=? WHERE id=?', (store.now() + PROBLEM_REST.get(kind, PROBLEM_REST[None]), account['id']))
            c.execute('UPDATE social_crawl SET next_at=? WHERE profile_id=? AND list=?',
                      (store.now() + random.randint(*LIST_REST), crawl['profile_id'], crawl['list']))
    finally:
        if source:
            source.close()


def overview(profile_id):
    now = store.now()
    accounts = {a['id']: a for a in sync.accounts()}
    with store.db() as c:
        lists = []
        for row in c.execute('SELECT * FROM social_crawl WHERE profile_id=?', (profile_id,)):
            row = dict(row)
            known = c.execute('SELECT COUNT(*) FROM social_people WHERE profile_id=? AND list=? AND gone_at IS NULL',
                              (profile_id, row['list'])).fetchone()[0]
            seen = c.execute('SELECT COUNT(*) FROM social_people WHERE profile_id=? AND list=? AND last_pass=?',
                             (profile_id, row['list'], row['pass'])).fetchone()[0]
            lists.append({'list': row['list'], 'known': known, 'pass_seen': seen, 'expected': row['expected'] or expected_count(profile_id, row['list']),
                          'complete_passes': row['complete_passes'], 'capped': bool(row['capped']), 'next_at': row['next_at'],
                          'account': accounts.get(row['account'], {}).get('username', '')})
        # Interactions: sampled likes plus comments, the profile itself excluded.
        username = c.execute('SELECT username FROM profiles WHERE id=?', (profile_id,)).fetchone()[0]
        ranking = [dict(r) for r in c.execute('''SELECT username, MAX(full_name) AS full_name, SUM(likes) AS likes, SUM(comments) AS comments,
            SUM(likes)+SUM(comments) AS total FROM (
              SELECT l.username, l.full_name, 1 AS likes, 0 AS comments FROM likes l JOIN posts p ON p.id=l.post_id WHERE p.profile_id=?
              UNION ALL
              SELECT k.username, k.full_name, 0, 1 FROM comments k JOIN posts p ON p.id=k.post_id WHERE p.profile_id=?)
            WHERE username<>? GROUP BY username ORDER BY total DESC, likes DESC LIMIT 50''', (profile_id, profile_id, username))]
        events = [dict(r) for r in c.execute('SELECT list, username, kind, at FROM social_events WHERE profile_id=? ORDER BY at DESC, id DESC LIMIT 100',
                                             (profile_id,))]
        likes = [dict(r) for r in c.execute('''SELECT l.username, l.full_name, l.first_seen, p.id AS post_id,
            (SELECT m.id FROM media m WHERE m.post_id=p.id ORDER BY m.position LIMIT 1) AS media
            FROM likes l JOIN posts p ON p.id=l.post_id WHERE p.profile_id=? ORDER BY l.first_seen DESC LIMIT 100''', (profile_id,))]
        liked_posts = c.execute('SELECT COUNT(*) FROM posts WHERE profile_id=? AND likes_at IS NOT NULL', (profile_id,)).fetchone()[0]
    return {'lists': lists, 'ranking': ranking, 'events': events, 'likes': likes, 'liked_posts': liked_posts, 'now': now}


def people(profile_id, name, query='', offset=0):
    term = '%' + str(query).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')[:60] + '%'
    with store.db() as c:
        rows = [dict(r) for r in c.execute('''SELECT username, full_name, first_seen, gone_at FROM social_people
            WHERE profile_id=? AND list=? AND (username LIKE ? ESCAPE '\\' OR full_name LIKE ? ESCAPE '\\')
            ORDER BY gone_at IS NOT NULL, first_seen DESC, username LIMIT 101 OFFSET ?''', (profile_id, name, term, term, int(offset)))]
    return {'items': rows[:100], 'more': len(rows) > 100}
