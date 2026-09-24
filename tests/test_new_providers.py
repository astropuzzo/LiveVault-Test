import pytest

from app.source_providers import PROVIDER_BY_ID, normalize_source, source_url

yt_dlp = pytest.importorskip("yt_dlp")
from yt_dlp.extractor import gen_extractor_classes  # noqa: E402

EXTRACTORS = {extractor.IE_NAME: extractor for extractor in gen_extractor_classes()}


@pytest.mark.parametrize(("value", "platform", "slug"), [
    ("https://www.tiktok.com/@Some.One/live", "tiktok", "some.one"),
    ("https://www.cammodels.com/cam/abc/", "cammodels", "abc"),
    ("https://play.sooplive.com/abc123", "soop", "abc123"),
    ("https://chzzk.naver.com/live/0123456789abcdef0123456789abcdef", "chzzk", "0123456789abcdef0123456789abcdef"),
    ("https://www.bigo.tv/someone", "bigo", "someone"),
    ("https://picarto.tv/someone", "picarto", "someone"),
    ("https://twitcasting.tv/someone", "twitcasting", "someone"),
    ("https://dlive.tv/someone", "dlive", "someone"),
    ("https://live.vkvideo.ru/someone", "vklive", "someone"),
    ("https://www.huya.com/room", "huya", "room"),
    ("https://www.douyu.com/room", "douyu", "room"),
    ("https://www.younow.com/someone", "younow", "someone"),
    ("https://www.showroom-live.com/r/room1", "showroom", "room1"),
    ("https://17.live/en/live/12345", "17live", "12345"),
    ("https://mixch.tv/u/999/live", "mixch", "999"),
])
def test_new_live_sites_resolve_to_their_yt_dlp_extractor(value, platform, slug):
    detected, normalized = normalize_source("auto", value)
    assert (detected, normalized) == (platform, slug)
    url = source_url(platform, slug)
    assert EXTRACTORS[PROVIDER_BY_ID[platform].extractor].suitable(url)


def test_url_based_live_sites_keep_the_https_url():
    assert normalize_source("auto", "https://rumble.com/v4abcd-x.html") == ("rumble", "https://rumble.com/v4abcd-x.html")
    with pytest.raises(ValueError):
        normalize_source("auto", "http://rumble.com/v4abcd-x.html")
    with pytest.raises(ValueError):
        normalize_source("mixch", "https://mixch.tv/somewhere-else")
