import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vault import mobile_source as mobile  # noqa: E402

SESSION = {'verified_via': 'mobile_login', 'username': 'my_account',
           'cookies': {'ds_user_id': '123', 'sessionid': '123%3Aprivate-session'}}


def item(code='Photo1', *, media_type=1, **overrides):
    fields = {'pk': '987', 'code': code, 'media_type': media_type,
              'taken_at': datetime(2026, 11, 2, 12, 30, tzinfo=timezone(timedelta(hours=2))),
              'caption_text': 'Original caption\n#photo', 'product_type': 'feed',
              'thumbnail_url': 'https://s.cdninstagram.com/photo.jpg', 'video_url': None,
              'resources': []}
    return SimpleNamespace(**(fields | overrides))


class Client:
    def __init__(self):
        self.calls = []
        self.pages = {}
        self.last_json = {'items': []}
        self.private = SimpleNamespace(close=lambda: self.calls.append(('close-private',)))
        self.public = SimpleNamespace(close=lambda: self.calls.append(('close-public',)))

    def user_info_by_username_v1(self, username):
        self.calls.append(('profile-v1', username))
        return SimpleNamespace(pk='456', username=username, full_name='Photographer', biography='Bio',
                               profile_pic_url='https://s.cdninstagram.com/avatar.jpg', media_count=4)

    def _page(self, kind, userid, amount, end_cursor):
        self.calls.append((kind, userid, amount, end_cursor))
        value = self.pages[(kind, end_cursor)]
        if isinstance(value, Exception):
            raise value
        self.last_json = {'items': [1] * len(value[0])}
        return value

    def user_medias_paginated_v1(self, userid, amount, end_cursor):
        return self._page('post', userid, amount, end_cursor)

    def user_clips_paginated_v1(self, userid, amount, end_cursor):
        return self._page('reel', userid, amount, end_cursor)

    def user_stories_v1(self, userid):
        self.calls.append(('stories-v1', userid))
        return [item('Story1', media_type=2, video_url='https://s.cdninstagram.com/story.mp4')]

    def user_highlights_v1(self, userid):
        self.calls.append(('highlights-v1', userid))
        return [SimpleNamespace(pk='111', title='Work')]

    def highlight_info_v1(self, pk):
        self.calls.append(('highlight-info', pk))
        return SimpleNamespace(items=[item('Highlight1')])


@pytest.fixture
def setup():
    client = Client()
    source = mobile.MobileSource({}, copy.deepcopy(SESSION), client=client)
    return client, source, source.profile('photographer')


def test_profile_and_photo_mapping_uses_only_authenticated_methods(setup):
    client, source, profile = setup
    assert source.context.is_logged_in is True
    assert client.with_challenge_flow is False
    assert (profile.userid, profile.username, profile.full_name, profile.biography) == ('456', 'photographer', 'Photographer', 'Bio')
    assert profile.profile_pic_url.endswith('avatar.jpg')
    photo = mobile.MediaAdapter(item())
    assert (photo.shortcode, photo.mediaid, photo.typename, photo.is_video) == ('Photo1', 987, 'GraphImage', False)
    assert photo.date_utc == datetime(2026, 11, 2, 10, 30)
    assert photo.caption == 'Original caption\n#photo'
    assert photo.url.endswith('photo.jpg')
    source.close()
    assert client.calls == [('profile-v1', 'photographer'), ('close-private',), ('close-public',)]


def test_carousel_keeps_every_photo_and_video_resource():
    value = mobile.MediaAdapter(item('Carousel1', media_type=8, resources=[
        SimpleNamespace(media_type=1, thumbnail_url='https://s.cdninstagram.com/a.jpg'),
        SimpleNamespace(media_type=2, thumbnail_url='https://s.cdninstagram.com/b.jpg', video_url='https://s.cdninstagram.com/b.mp4')]))
    assert value.typename == 'GraphSidecar'
    nodes = list(value.get_sidecar_nodes())
    assert [(n.is_video, n.display_url, n.video_url) for n in nodes] == [
        (False, 'https://s.cdninstagram.com/a.jpg', ''),
        (True, 'https://s.cdninstagram.com/b.jpg', 'https://s.cdninstagram.com/b.mp4')]


def test_missing_carousel_resources_cannot_publish_an_empty_post():
    with pytest.raises(ValueError, match='contenuti del carosello'):
        mobile.MediaAdapter(item('Incomplete', media_type=8))


def test_reels_use_12_item_pages_without_feed_or_anonymous_fallback(setup):
    client, _, profile = setup
    client.pages['reel', ''] = ([item('Reel1', media_type=2, video_url='https://s.cdninstagram.com/reel.mp4')], '')
    reel = next(profile.get_reels())
    assert (reel.product_type, reel.is_video, reel.video_url) == ('clips', True, 'https://s.cdninstagram.com/reel.mp4')
    assert client.calls[-1] == ('reel', '456', 12, '')


def test_stories_and_highlights_preserve_original_items(setup):
    client, source, profile = setup
    story = next(next(source.get_stories([profile.userid])).get_items())
    assert story.mediaid == 987 and story.is_video and story.video_url.endswith('story.mp4')
    highlight = next(source.get_highlights(profile))
    assert highlight.title == 'Work'
    assert next(highlight.get_items()).shortcode == 'Highlight1'
    assert client.calls[1:] == [('stories-v1', '456'), ('highlights-v1', '456'), ('highlight-info', '111')]


def test_cursor_is_json_only_and_replays_last_node_before_continuing(setup):
    client, _, profile = setup
    client.pages['post', ''] = ([item('One'), item('Two'), item('Three')], 'next-page')
    client.pages['post', 'next-page'] = ([item('Four')], '')
    first = profile.get_posts()
    assert next(first).shortcode == 'One'
    saved = json.loads(json.dumps(first.freeze()))
    assert 'private-session' not in json.dumps(saved)
    resumed = profile.get_posts(); resumed.restore(saved)
    assert [post.shortcode for post in resumed] == ['One', 'Two', 'Three', 'Four']
    assert client.calls[1:] == [('post', '456', 12, ''), ('post', '456', 12, 'next-page')]


def test_failed_post_is_not_skipped_by_the_preceding_checkpoint(setup):
    client, _, profile = setup
    client.pages['post', ''] = ([item('Archived'), item('Failed')], '')
    original = profile.get_posts(); next(original)
    checkpoint = json.loads(json.dumps(original.freeze()))
    assert next(original).shortcode == 'Failed'  # download fails before another checkpoint is committed
    retried = profile.get_posts(); retried.restore(checkpoint)
    assert [post.shortcode for post in retried] == ['Archived', 'Failed']


def test_sdk_media_checkpoint_uses_model_validation_on_restore(setup, monkeypatch):
    client, _, profile = setup
    validations = []
    class Media(SimpleNamespace):
        def model_dump(self, *, mode):
            assert mode == 'json'
            return dict(vars(self)) | {'taken_at': self.taken_at.isoformat()}

        @classmethod
        def model_validate(cls, data):
            validations.append(data['code'])
            return cls(**data)

    Media.__module__ = 'instagrapi.types'
    module = ModuleType('instagrapi.types'); module.Media = Media
    monkeypatch.setitem(sys.modules, 'instagrapi.types', module)
    typed = Media(**vars(item('Typed')))
    client.pages['post', ''] = ([typed], '')
    iterator = profile.get_posts(); next(iterator)
    saved = json.loads(json.dumps(iterator.freeze()))
    assert saved['remaining'][0]['model'] == 'Media'
    restored = profile.get_posts(); restored.restore(saved)
    assert next(restored).shortcode == 'Typed'
    assert validations == ['Typed']


@pytest.mark.parametrize(('key', 'value'), [('transport', 'browser'), ('account_id', '999'),
                                         ('profile_id', '888'), ('kind', 'reel')])
def test_cursor_cannot_be_reused_for_another_account_profile_or_transport(setup, key, value):
    _, _, profile = setup
    cursor = profile.get_posts().freeze(); cursor[key] = value
    with pytest.raises(ValueError):
        profile.get_posts().restore(cursor)


def test_expired_cursor_restarts_with_fresh_media_urls(setup):
    _, _, profile = setup
    cursor = profile.get_posts().freeze(); cursor['created_at'] -= mobile.CURSOR_MAX_AGE + 1
    with pytest.raises(ValueError):
        profile.get_posts().restore(cursor)


def test_private_errors_propagate_without_faking_an_empty_profile(setup):
    client, _, profile = setup
    class PrivateError(Exception):
        pass
    error = PrivateError('private request failed')
    client.pages['post', ''] = error
    with pytest.raises(PrivateError) as captured:
        next(profile.get_posts())
    assert captured.value is error


def test_empty_feed_is_not_success_when_profile_has_posts(setup):
    client, _, profile = setup
    client.pages['post', ''] = ([], '')
    with pytest.raises(ValueError, match='non ha restituito i post'):
        next(profile.get_posts())


def test_empty_reels_is_a_valid_terminal_page(setup):
    client, _, profile = setup
    client.pages['reel', ''] = ([], '')
    assert list(profile.get_reels()) == []


def test_sdk_schema_error_cannot_look_like_an_empty_reel_collection(setup):
    client, _, profile = setup
    def broken(*args, **kwargs):
        client.last_json = {'status': 'fail'}
        return [], None
    client.user_clips_paginated_v1 = broken
    with pytest.raises(ValueError, match='pagina incompleta'):
        next(profile.get_reels())


def test_page_is_fetched_only_when_consumed_and_never_prefetches_history(setup):
    client, _, profile = setup
    client.pages['post', ''] = ([item('One'), item('Two')], 'next-page')
    iterator = profile.get_posts()
    assert len(client.calls) == 1
    next(iterator); next(iterator)
    assert len(client.calls) == 2


def test_mobile_factory_preserves_device_identity_and_binds_verified_account():
    original = {'uuids': {'uuid': 'saved-device'}, 'cookies': {'mid': 'device-cookie'}}
    captured = {}
    def factory(**kwargs):
        captured.update(kwargs)
        return Client()
    source = mobile.MobileSource(original, copy.deepcopy(SESSION), client_factory=factory)
    assert captured['settings']['uuids'] == original['uuids']
    assert captured['settings']['authorization_data']['ds_user_id'] == '123'
    assert captured['settings']['authorization_data']['should_use_header_over_cookies'] is True
    assert 'sessionid' not in original['cookies']
    assert source.client.with_challenge_flow is False


@pytest.mark.parametrize('marker', [None, 'browser_cookie', 'legacy_graphql'])
def test_browser_sessions_never_become_mobile_sessions(marker):
    session = copy.deepcopy(SESSION); session['verified_via'] = marker
    with pytest.raises(ValueError, match='non proviene'):
        mobile.MobileSource({}, session, client=Client())


def test_story_links_keep_stickers_and_mentions_with_their_place():
    story = {'stickers': [{'x': .5, 'y': .8, 'width': .4, 'height': .1,
                           'story_link': {'url': 'https://shop.example/a', 'link_title': 'Shop'}}],
             'links': [{'webUri': 'https://shop.example/a', 'x': .5, 'y': .8, 'width': .4, 'height': .1}],
             'mentions': [{'user': {'username': 'virginia.c'}, 'x': .2, 'y': .3, 'width': .3, 'height': .05}]}
    found = mobile.story_links(story)
    assert [(f['label'], f['url']) for f in found] == [('Shop', 'https://shop.example/a'),
                                                        ('@virginia.c', 'https://www.instagram.com/virginia.c/')]
    assert (found[0]['y'], found[1]['x'], found[1]['w']) == (0.8, 0.2, 0.3)


def test_tagged_feed_numeric_page_id_continues_to_the_next_page(setup):
    client, _, profile = setup
    client.usertag_medias_paginated_v1 = lambda userid, amount, end_cursor: client._page('tagged', userid, amount, end_cursor)
    client.pages['tagged', ''] = ([item('T1')], 3674965766175925989)
    client.pages['tagged', '3674965766175925989'] = ([item('T2')], '')
    assert [m.shortcode for m in profile.get_tagged()] == ['T1', 'T2']


def test_a_highlight_story_without_preview_does_not_drop_the_collection(setup):
    client, source, profile = setup
    highlight = next(source.get_highlights(profile))
    good = {'pk': '1', 'id': '1_456', 'code': 'Good', 'media_type': 1, 'taken_at': 1767225600,
            'image_versions2': {'candidates': [{'url': 'https://scontent.cdninstagram.com/a.jpg', 'width': 10, 'height': 10}]},
            'user': {'pk': '456', 'username': 'my_account'}}
    bare = dict(good, pk='2', id='2_456', code='Bare', media_type=2, image_versions2={'candidates': []},
                video_versions=[{'url': 'https://scontent.cdninstagram.com/v.mp4', 'width': 720, 'height': 1280}])
    def broken(pk):
        client.last_json = {'reels': {'highlight:' + pk: {'items': [good, bare]}}}
        raise IndexError('list index out of range')
    client.highlight_info_v1 = broken
    items = list(highlight.get_items())
    # The video without a preview stays, with no picture: the archive draws one from the video.
    assert [m.mediaid for m in items] == [1, 2] and items[1].is_video and items[1].url == ''
    assert items[1].video_url.endswith('v.mp4')
