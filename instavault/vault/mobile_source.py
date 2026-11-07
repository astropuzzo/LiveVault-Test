"""Read-only mobile transport for sessions authenticated by mobile login.

Browser-cookie sessions are deliberately excluded. The adapter never invokes
the SDK's anonymous fallbacks or answers account challenges unattended.
"""
from __future__ import annotations

import copy
import json
import re
import time
from datetime import datetime, timezone
from types import SimpleNamespace

PAGE_SIZE = 12
CURSOR_TRANSPORT = 'instagrapi-mobile-v1'
CURSOR_MAX_AGE = 3600


def _get(item, name, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _url(value):
    return str(value) if value is not None else ''


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if hasattr(value, '__dict__'):
        fields = {k: _plain(v) for k, v in vars(value).items() if not k.startswith('_')}
        # A pydantic URL keeps its text in a private attribute: it would come out as {}.
        if fields:
            return fields
    return str(value)


def _snapshot(value):
    typed = type(value).__name__ == 'Media' and type(value).__module__.startswith('instagrapi.')
    return {'model': 'Media' if typed else 'plain', 'data': _plain(value)}


def _restore(value):
    if not isinstance(value, dict) or value.get('model') not in {'Media', 'plain'} or not isinstance(value.get('data'), dict):
        raise ValueError('Cursore mobile non valido.')
    if value['model'] == 'Media':
        from instagrapi.types import Media
        return Media.model_validate(value['data'])
    return value['data']


def _thumbnail(item):
    versions = _get(item, 'image_versions2')
    candidates = _get(versions, 'candidates', []) or []
    if candidates:
        largest = max(candidates, key=lambda c: int(_get(c, 'width', 0) or 0) * int(_get(c, 'height', 0) or 0))
        if _get(largest, 'url'):
            return _url(_get(largest, 'url'))
    return _url(_get(item, 'thumbnail_url') or _get(item, 'display_url') or _get(item, 'url'))


class MediaAdapter:
    def __init__(self, item, *, reel=False):
        self.item = item
        self._reel = reel
        if int(_get(item, 'media_type', 0)) not in {1, 2, 8}:
            raise ValueError('Tipo di contenuto Instagram non valido.')
        if int(_get(item, 'media_type')) == 8 and not _get(item, 'resources'):
            raise ValueError('Instagram non ha restituito i contenuti del carosello.')

    @property
    def mediaid(self):
        value = str(_get(self.item, 'pk') or _get(self.item, 'id', '')).split('_', 1)[0]
        if not value.isdigit():
            raise ValueError('Identificativo Instagram non valido.')
        return int(value)

    @property
    def shortcode(self):
        return str(_get(self.item, 'code') or self.mediaid)

    @property
    def date_utc(self):
        value = _get(self.item, 'taken_at')
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        elif isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value, timezone.utc)
        if not isinstance(value, datetime):
            raise ValueError('Data del contenuto Instagram non disponibile.')
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

    @property
    def caption(self):
        return str(_get(self.item, 'caption_text', '') or '')

    @property
    def product_type(self):
        return 'clips' if self._reel else str(_get(self.item, 'product_type', '') or '')

    @property
    def typename(self):
        return {1: 'GraphImage', 2: 'GraphVideo', 8: 'GraphSidecar'}[int(_get(self.item, 'media_type'))]

    @property
    def likes(self):
        value = _get(self.item, 'like_count')
        return None if value is None else int(value)

    @property
    def comments(self):
        value = _get(self.item, 'comment_count')
        return None if value is None else int(value)

    @property
    def is_video(self):
        return int(_get(self.item, 'media_type')) == 2

    @property
    def url(self):
        return _thumbnail(self.item)

    @property
    def video_url(self):
        return _url(_get(self.item, 'video_url'))

    @property
    def owner_username(self):
        return str(_get(_get(self.item, 'user'), 'username', '') or '')

    @property
    def links(self):
        return story_links(self.item)

    def get_sidecar_nodes(self):
        for resource in _get(self.item, 'resources', []) or []:
            yield SimpleNamespace(is_video=int(_get(resource, 'media_type', 1)) == 2,
                                  display_url=_thumbnail(resource), video_url=_url(_get(resource, 'video_url')))


def _box(item):
    if item is None:
        return {'x': 0, 'y': 0, 'w': 0, 'h': 0}
    try:
        return {k: round(float(_get(item, n, 0) or 0), 4) for k, n in
                [('x', 'x'), ('y', 'y'), ('w', 'width'), ('h', 'height')]}
    except (TypeError, ValueError):
        return {'x': 0, 'y': 0, 'w': 0, 'h': 0}


def story_links(item):
    """What a post or story points to: link stickers, people, hashtags, places
    and reshared posts. x, y, w, h place it on the image as fractions; a tag
    Instagram sends without a place stays at 0 and is listed beside the media."""
    found = []
    def add(kind, url, label, place=None):
        if url.startswith(('https://', 'http://')) and all(f['url'] != url for f in found):
            found.append({'kind': kind, 'url': url, 'label': str(label)} | _box(place))
    def person(name, place=None):
        name = str(name or '')
        if re.fullmatch(r'[A-Za-z0-9._]{1,30}', name):
            add('mention', f'https://www.instagram.com/{name}/', '@' + name, place)
    for sticker in _get(item, 'stickers', []) or []:
        link = _get(sticker, 'story_link')
        url = _url(_get(link, 'url')) if link else ''
        if url:
            add('link', url, _get(link, 'display_url', '') or _get(link, 'link_title', '') or url, sticker)
    for link in _get(item, 'links', []) or []:
        url = _url(_get(link, 'webUri'))
        add('link', url, url, link)
    for mention in _get(item, 'mentions', []) or []:
        person(_get(_get(mention, 'user'), 'username'), mention)
    # In a carousel the people are tagged on each slide, not on the post.
    tagged = list(_get(item, 'usertags', []) or [])
    for slide in _get(item, 'resources', []) or []:
        tagged += _get(slide, 'usertags', []) or []
    for tag in tagged:
        person(_get(_get(tag, 'user'), 'username'))
    for author in _get(item, 'coauthor_producers', []) or []:
        person(_get(author, 'username'))
    for tag in _get(item, 'hashtags', []) or []:
        name = str(_get(_get(tag, 'hashtag'), 'name', '') or '')
        if re.fullmatch(r'[\w.]{1,100}', name):
            add('hashtag', f'https://www.instagram.com/explore/tags/{name}/', '#' + name, tag)
    places = [(_get(entry, 'location'), entry) for entry in _get(item, 'locations', []) or []]
    if _get(item, 'location'):
        places.append((_get(item, 'location'), None))
    for place, box in places:
        if str(_get(place, 'pk', '') or '').isdigit() and _get(place, 'name'):
            add('place', f'https://www.instagram.com/explore/locations/{_get(place, "pk")}/', _get(place, 'name'), box)
    for media in _get(item, 'medias', []) or []:
        code = str(_get(media, 'media_code', '') or '')
        if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', code):
            reel = _get(media, 'product_type') == 'clips'
            add('post', f'https://www.instagram.com/{"reel" if reel else "p"}/{code}/', 'Reel ricondiviso' if reel else 'Post ricondiviso', media)
    return found[:20]


class MediaIterator:
    """One page at a time; persisted state replays the last returned node."""
    def __init__(self, source, profile, kind):
        self.source, self.profile, self.kind = source, profile, kind
        self._items = []
        self._position = 0
        self._started = False
        self._next_cursor = ''
        self._page_cursor = ''
        self._resume_skip = 0

    def __iter__(self):
        return self

    def __next__(self):
        while self._position >= len(self._items):
            if self._started and not self._next_cursor:
                raise StopIteration
            previous_cursor = self._next_cursor
            method = getattr(self.source.client, {'post': 'user_medias_paginated_v1', 'reel': 'user_clips_paginated_v1',
                                                  'tagged': 'usertag_medias_paginated_v1'}[self.kind])
            items, next_cursor = method(str(self.profile.userid), amount=PAGE_SIZE,
                                        end_cursor=previous_cursor)
            # The tagged feed sends its next page id as a number.
            if isinstance(next_cursor, int) and not isinstance(next_cursor, bool):
                next_cursor = str(next_cursor)
            if not isinstance(items, list) or (next_cursor is not None and not isinstance(next_cursor, str)):
                raise ValueError('Instagram ha mandato una pagina incompleta.')
            # The pinned SDK can return an empty page after catching a response
            # schema error. That must not close an archive as successfully empty.
            payload = getattr(self.source.client, 'last_json', None)
            if not items and isinstance(payload, dict) and 'items' not in payload:
                raise ValueError('Instagram ha mandato una pagina incompleta.')
            if not self._started and not items and self.kind == 'post' and self.profile.media_count > 0:
                raise ValueError('Instagram non ha restituito i post del profilo.')
            if next_cursor and (not items or next_cursor == previous_cursor):
                raise ValueError('Instagram non ha avanzato la pagina dell’archivio.')
            self._page_cursor = previous_cursor
            self._items, self._position = items, min(self._resume_skip, len(items))
            self._resume_skip = 0
            self._next_cursor, self._started = next_cursor or '', True
        item = self._items[self._position]
        self._position += 1
        return MediaAdapter(item, reel=self.kind == 'reel')

    def freeze(self):
        return {'version': 1, 'transport': CURSOR_TRANSPORT,
                'account_id': self.source.account_id, 'profile_id': str(self.profile.userid),
                'kind': self.kind, 'created_at': time.time(), 'started': self._started,
                'cursor': self._next_cursor,
                'page_cursor': self._page_cursor, 'page_position': max(0, self._position-1),
                'remaining': [_snapshot(item) for item in self._items[max(0, self._position - 1):]]}

    def restore(self, value):
        if self._started or self._position:
            raise ValueError('Il cursore mobile è già stato usato.')
        if not isinstance(value, dict) or any(value.get(k) != expected for k, expected in {
                'version': 1, 'transport': CURSOR_TRANSPORT, 'account_id': self.source.account_id,
                'profile_id': str(self.profile.userid), 'kind': self.kind}.items()):
            raise ValueError('Il cursore appartiene a un altro archivio o trasporto.')
        stamp = value.get('created_at')
        age = time.time()-stamp if type(stamp) in {int, float} else -1
        if age < 0:
            raise ValueError('Cursore mobile scaduto: aggiorna i collegamenti dei media.')
        remaining, cursor = value.get('remaining'), value.get('cursor')
        if (not isinstance(remaining, list) or len(remaining) > 64 or not isinstance(cursor, str)
                or len(cursor) > 8192 or type(value.get('started')) is not bool):
            raise ValueError('Cursore mobile non valido.')
        if len(json.dumps(value)) > 8 * 1024 * 1024:
            raise ValueError('Cursore mobile troppo grande.')
        if age > CURSOR_MAX_AGE:
            # Refresh the page's signed URLs without restarting the history.
            page, position = value.get('page_cursor'), value.get('page_position')
            if (not value['started'] or not isinstance(page, str) or len(page)>8192
                    or type(position) is not int or not 0 <= position <= 64):
                raise ValueError('Cursore mobile scaduto: aggiorna i collegamenti dei media.')
            self._next_cursor, self._resume_skip = page, position
            return
        self._items = [_restore(item) for item in remaining]
        self._position = 0
        self._next_cursor, self._started = cursor, value['started']


class ProfileAdapter:
    def __init__(self, source, item, latest_reel_media=None):
        self.source = source
        self.latest_reel_media = latest_reel_media if isinstance(latest_reel_media, int) else None
        self.userid = str(_get(item, 'pk', ''))
        if not self.userid.isdigit():
            raise ValueError('Profilo Instagram non valido.')
        self.username = str(_get(item, 'username', ''))
        self.full_name = str(_get(item, 'full_name', '') or '')
        self.biography = str(_get(item, 'biography', '') or '')
        self.profile_pic_url = _url(_get(item, 'profile_pic_url_hd') or _get(item, 'profile_pic_url'))
        self.media_count = int(_get(item, 'media_count', 0) or 0)
        self.is_private = bool(_get(item, 'is_private', False))
        self.followers = int(_get(item, 'follower_count', 0) or 0)
        self.followees = int(_get(item, 'following_count', 0) or 0)

    def get_posts(self):
        return MediaIterator(self.source, self, 'post')

    def get_reels(self):
        return MediaIterator(self.source, self, 'reel')

    def get_tagged(self):
        return MediaIterator(self.source, self, 'tagged')


class StoryCollection:
    def __init__(self, items, title=''):
        self.items, self.title = items, title

    def get_items(self):
        for item in self.items:
            yield MediaAdapter(item)


class HighlightAdapter:
    def __init__(self, source, item):
        self.source, self.item = source, item
        self.title = str(_get(item, 'title', '') or '')
        self.pk = str(_get(item, 'pk', '')).split(':')[-1]

    def get_items(self):
        # The v1 call only: the generic one falls back to a web query that
        # Instagram refuses for mobile sessions.
        client = self.source.client
        try:
            items = _get(client.highlight_info_v1(self.pk), 'items', []) or []
        except IndexError:
            # instagrapi drops the whole collection when one story arrives
            # without a preview picture; keep the others, skip that one.
            from instagrapi.extractors import extract_story_v1
            raw = ((getattr(client, 'last_json', None) or {}).get('reels') or {}).get('highlight:' + self.pk) or {}
            entries = raw.get('items') or []
            if not entries:
                raise
            items = []
            for entry in entries:
                if (entry.get('image_versions2') or {}).get('candidates'):
                    items.append(extract_story_v1(entry))
                elif entry.get('video_versions'):
                    # A video without a preview picture: the archive draws
                    # the poster from the video's first second instead.
                    first = entry['video_versions'][0]
                    video = {'url': first.get('url'), 'width': first.get('width') or 0, 'height': first.get('height') or 0}
                    story = extract_story_v1(dict(entry, image_versions2={'candidates': [video]}))
                    items.append(story.model_copy(update={'thumbnail_url': None}) if hasattr(story, 'model_copy') else story)
            if len(items) < len(entries):
                print(f'InstaVault: highlight «{self.title}»: {len(entries) - len(items)} story without media skipped', flush=True)
        for item in items:
            yield MediaAdapter(item)


class MobileSource:
    def __init__(self, settings, session, *, client=None, client_factory=None):
        # A browser cookie session works on the mobile API too; the old web
        # endpoints answer it with a redirect to the login page.
        if not isinstance(session, dict) or session.get('verified_via') not in {'mobile_login', 'web_session'}:
            raise ValueError('Questa sessione non proviene da un accesso mobile verificato.')
        cookies = session.get('cookies')
        if not isinstance(cookies, dict) or not cookies.get('sessionid'):
            raise ValueError('Sessione mobile non disponibile.')
        owner = str(cookies.get('ds_user_id') or '')
        if not owner:
            match = re.match(r'^\d+', str(cookies['sessionid']))
            owner = match[0] if match else ''
        if not owner.isdigit():
            raise ValueError('Identità della sessione mobile non disponibile.')
        self.account_id = owner
        if client is None:
            if client_factory is None:
                from instagrapi import Client
                client_factory = Client
            config = copy.deepcopy(settings or {})
            config['cookies'] = dict(config.get('cookies') or {}) | dict(cookies)
            config['authorization_data'] = {'ds_user_id': owner, 'sessionid': cookies['sessionid'],
                                            'should_use_header_over_cookies': True}
            client = client_factory(settings=config, session_retry_total=0, public_request_retries_count=1)
        self.client = client
        self.client.with_challenge_flow = False
        self.client.username = str(session.get('username', ''))
        self.context = SimpleNamespace(is_logged_in=True, username=self.client.username)
        self.request_lane = 'fresh'
        sender = getattr(self.client, '_send_private_request', None)
        if sender:
            # Every SDK attempt passes here, including its outer retry path.
            # Transport-level retries on 429 must remain disabled after restore.
            configure = getattr(self.client, 'set_retry_config', None)
            if configure:
                configure(session_retry_total=0, public_request_retries_count=1)
            def guarded(endpoint, *args, **kwargs):
                from . import request_policy, sync
                from instagrapi.exceptions import PrivateError
                class TransportDeferred(request_policy.Deferred, PrivateError):
                    pass
                lane = self.request_lane
                if any(part in endpoint for part in ['/comments/', '/likers/', '/followers/', '/following/', 'highlights/']):
                    lane = 'maintenance'
                try:
                    request_policy.wait(self.account_id, lane, endpoint.split('/')[0], sync.STOP)
                except request_policy.Deferred as exc:
                    # Paginated SDK helpers swallow ordinary Exceptions and
                    # return an empty page. PrivateError is propagated intact.
                    raise TransportDeferred(exc.until, exc.reason) from None
                try:
                    return sender(endpoint, *args, **kwargs)
                except Exception as exc:
                    request_policy.note_failure(self.account_id, exc)
                    # The SDK retries these after sleeping, hiding the extra
                    # work from callers. Defer instead, without losing cursors.
                    if type(exc).__name__ in {'ClientRequestTimeout', 'ClientIncompleteReadError'}:
                        raise TransportDeferred(__import__('time').time()+900, 'network') from None
                    raise
            self.client._send_private_request = guarded

    def _card(self, item):
        # The SDK's User model drops the story marker; the raw reply still has it.
        reply = getattr(self.client, 'last_json', None)
        user = reply.get('user') if isinstance(reply, dict) else None
        return ProfileAdapter(self, item, _get(user or {}, 'latest_reel_media'))

    def profile(self, username):
        return self._card(self.client.user_info_by_username_v1(username))

    def profile_by_id(self, user_id):
        return self._card(self.client.user_info_v1(str(user_id)))

    def access(self, profile):
        if not profile.is_private or profile.userid == self.account_id:
            return ''
        # Raw call: the SDK's Relationship model rejects today's reply.
        status = self.client.private_request(f'friendships/show/{profile.userid}/')
        if status.get('following'):
            return ''
        return 'requested' if status.get('outgoing_request') else 'not_followed'

    def likers(self, shortcode):
        # About the latest 100 people who liked it: all Instagram returns.
        pk = self.client.media_pk_from_code(shortcode)
        users = self.client.private_request(f'media/{pk}/likers/').get('users', [])
        return [{'pk': str(u['pk']), 'username': u['username'], 'full_name': str(u.get('full_name') or '')[:150]}
                for u in users if u.get('pk') and u.get('username')]

    def comments(self, shortcode, pages=1, wait=None):
        # Newest first, about 15 a page; a few pages with a short pause for recent posts.
        pk = str(self.client.media_pk_from_code(shortcode))
        items, min_id, max_id = self.client.media_comments_v1_chunk(pk)
        items = list(items or [])
        for _ in range(pages - 1):
            if not (min_id or max_id) or (wait and not wait()):
                break
            more, next_min, next_max = self.client.media_comments_v1_chunk(pk, min_id, max_id)
            if not more or (next_min, next_max) == (min_id, max_id):
                break
            items += more; min_id, max_id = next_min, next_max
        found = []
        for item in items:
            user = _get(item, 'user')
            name = str(_get(user, 'username', '') or '')
            if not re.fullmatch(r'[A-Za-z0-9._]{1,30}', name):
                continue
            stamp = _get(item, 'created_at_utc')
            if isinstance(stamp, datetime):
                stamp = int(stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp())
            found.append({'id': str(_get(item, 'pk', '')), 'username': name,
                          'full_name': str(_get(user, 'full_name', '') or '')[:150],
                          'text': str(_get(item, 'text', '') or '')[:2200],
                          'created_at': int(stamp or 0), 'likes': int(_get(item, 'like_count', 0) or 0)})
        return found

    def follow(self, userid):
        self.client.user_follow(str(userid))

    def get_stories(self, userids):
        for userid in userids:
            # The explicit v1 method has no anonymous fallback.
            yield StoryCollection(self.client.user_stories_v1(str(userid)))

    def get_highlights(self, profile):
        for item in self.client.user_highlights_v1(str(profile.userid)):
            yield HighlightAdapter(self, item)

    def close(self):
        closed = set()
        for name in ['private', 'public']:
            session = getattr(self.client, name, None)
            if session is not None and id(session) not in closed:
                closed.add(id(session))
                try:
                    session.close()
                except Exception:
                    pass
