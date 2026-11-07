"""Tags are kept for every kind of content; imports read by the day they were saved."""
import json

import pytest

from test_archive import vault, running_job, image_item, insert_post
from vault import mobile_source as mobile, store, sync


def by_kind(found, kind):
    return [f for f in found if f['kind'] == kind]


def test_a_post_keeps_people_coauthors_and_its_place():
    post = {'usertags': [{'user': {'username': 'anna.b'}, 'x': .4, 'y': .6}],
            'coauthor_producers': [{'username': 'marco_v'}],
            'location': {'pk': 123, 'name': 'Bologna'}}
    found = mobile.story_links(post)
    assert [f['label'] for f in by_kind(found, 'mention')] == ['@anna.b', '@marco_v']
    assert by_kind(found, 'place')[0]['url'] == 'https://www.instagram.com/explore/locations/123/'
    assert all(f['w'] == 0 for f in found)


def test_a_story_keeps_hashtags_places_and_reshared_reels_on_the_image():
    story = {'hashtags': [{'hashtag': {'name': 'tramonto'}, 'x': .5, 'y': .2, 'width': .3, 'height': .1}],
             'locations': [{'location': {'pk': 9, 'name': 'Ravenna'}, 'x': .5, 'y': .4, 'width': .3, 'height': .1}],
             'medias': [{'media_code': 'DfZep-jNG7x', 'product_type': 'clips', 'x': .5, 'y': .5, 'width': .6, 'height': .5}]}
    found = mobile.story_links(story)
    assert by_kind(found, 'hashtag')[0]['label'] == '#tramonto'
    assert by_kind(found, 'place')[0]['y'] == .4
    assert by_kind(found, 'post')[0]['url'] == 'https://www.instagram.com/reel/DfZep-jNG7x/'


def raw_tag(name):
    return {'in': [{'user': {'pk': '2', 'username': name, 'full_name': '', 'profile_pic_url': PICTURE},
                    'position': [.4, .6]}]}


PICTURE = 'https://x.cdninstagram.com/p.jpg'
IMAGE = {'candidates': [{'url': PICTURE, 'width': 10, 'height': 10}]}
RAW_POST = {'pk': '11', 'id': '11_1', 'code': 'Abc', 'taken_at': 1700000000, 'media_type': 1,
            'product_type': 'feed', 'image_versions2': IMAGE, 'like_count': 1, 'comment_count': 0,
            'user': {'pk': '1', 'username': 'owner', 'full_name': '', 'profile_pic_url': PICTURE}}


def test_tags_survive_the_instagram_response_of_a_single_post():
    extract = pytest.importorskip('instagrapi.extractors').extract_media_v1
    raw = RAW_POST | {'usertags': raw_tag('anna.b'), 'location': {'pk': 123, 'name': 'Bologna', 'short_name': '', 'lng': 1.0, 'lat': 2.0}}
    labels = [f['label'] for f in mobile.MediaAdapter(extract(raw)).links]
    assert labels == ['@anna.b', 'Bologna']


def test_tags_on_carousel_slides_are_kept():
    extract = pytest.importorskip('instagrapi.extractors').extract_media_v1
    slides = [{'pk': '12', 'id': '12_1', 'media_type': 1, 'image_versions2': IMAGE, 'usertags': raw_tag('slide.uno')},
              {'pk': '13', 'id': '13_1', 'media_type': 1, 'image_versions2': IMAGE, 'usertags': raw_tag('slide.due')}]
    raw = RAW_POST | {'media_type': 8, 'carousel_media': slides}
    assert [f['label'] for f in mobile.MediaAdapter(extract(raw)).links] == ['@slide.uno', '@slide.due']


def test_unsafe_tags_are_dropped():
    found = mobile.story_links({'mentions': [{'user': {'username': '../admin'}}],
                                'stickers': [{'story_link': {'url': 'javascript:alert(1)'}}],
                                'hashtags': [{'hashtag': {'name': 'a b'}}],
                                'medias': [{'media_code': 'x/../y'}],
                                'location': {'pk': 'abc', 'name': 'Nowhere'}})
    assert found == []


def test_a_listing_fills_in_tags_of_a_post_already_archived(vault):
    job, profile = running_job(vault)
    post = insert_post(vault['profile'], 'Tagged', 1000)
    with store.db() as c:
        c.execute("INSERT INTO media(post_id,position,kind,path,size,sha256) VALUES (?,0,'image','1/Tagged/1.jpg',5,'x')", (post,))
        c.execute("UPDATE posts SET external_key='post:Tagged' WHERE id=?", (post,))
    target = vault['media'] / '1' / 'Tagged'; target.mkdir(parents=True); (target / '1.jpg').write_bytes(b'photo')
    tags = [{'kind': 'mention', 'url': 'https://www.instagram.com/anna/', 'label': '@anna', 'x': 0, 'y': 0, 'w': 0, 'h': 0}]
    item = image_item('Tagged'); item.links = tags
    assert sync.Worker().archive(profile, item, 'post', job) is False
    with store.db() as c:
        assert json.loads(c.execute('SELECT links FROM posts WHERE id=?', (post,)).fetchone()[0]) == tags
    item.links = []
    sync.Worker().archive(profile, item, 'post', job)
    with store.db() as c:
        assert json.loads(c.execute('SELECT links FROM posts WHERE id=?', (post,)).fetchone()[0]) == tags


def imported(vault):
    ids = {}
    for code, published, saved in [('Old', 100, 3000), ('New', 900, 1000), ('Mid', 500, 2000)]:
        ids[code] = insert_post(vault['profile'], code, published)
        with store.db() as c:
            c.execute('UPDATE posts SET archived_at=? WHERE id=?', (saved, ids[code]))
            c.execute("INSERT INTO media(post_id,position,kind,path,size,sha256) VALUES (?,0,'image',?,5,'x')", (ids[code], code + '.jpg'))
    return ids


def test_imports_follow_the_saving_order_not_the_publishing_one(vault):
    ids = imported(vault)
    items = store.list_posts({'sort': 'archived'})['items']
    assert [p['id'] for p in items] == [ids['Old'], ids['Mid'], ids['New']]
    assert [p['id'] for p in store.list_posts({})['items']] == [ids['New'], ids['Mid'], ids['Old']]


def test_imports_page_by_their_own_cursor(vault):
    with store.db() as c:
        for n in range(50):
            c.execute("""INSERT INTO posts(profile_id,external_key,shortcode,kind,published_at,archived_at,complete)
                VALUES (?,?,?,'post',?,?,1)""", (vault['profile'], f'post:P{n}', f'P{n}', 5000 - n, 1000 + n))
    first = store.list_posts({'sort': 'archived'})
    assert len(first['items']) == 48 and first['next'].startswith('1002:')
    second = store.list_posts({'sort': 'archived', 'cursor': first['next']})
    assert [p['archived_at'] for p in second['items']] == [1001, 1000]
