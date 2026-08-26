import pytest

from app.utils.url_parser import normalize_video_url
from app.validators.video_url_validator import VideoRequest


def test_watchlater_url():
    url = ("https://www.bilibili.com/list/watchlater/?bvid=BV1CPXpBYEui"
           "&oid=116294762371214&spm_id_from=333.881.0.0&vd_source=abc")
    assert normalize_video_url(url) == "https://www.bilibili.com/video/BV1CPXpBYEui"


def test_favlist_url():
    url = "https://www.bilibili.com/list/ml123456?bvid=BV1xx411c7mD&oid=987"
    assert normalize_video_url(url) == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_tracking_params_stripped():
    url = "https://www.bilibili.com/video/BV1xx411c7mD/?spm_id_from=333.881&vd_source=abc"
    assert normalize_video_url(url) == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_p_number_preserved():
    url = "https://www.bilibili.com/video/BV1xx411c7mD?p=36&spm_id_from=333.881"
    assert normalize_video_url(url) == "https://www.bilibili.com/video/BV1xx411c7mD?p=36"


def test_no_bv_returned_unchanged():
    url = "https://www.bilibili.com/anime/timeline"
    assert normalize_video_url(url) == url


def test_b23_short_url_unchanged():
    url = "https://b23.tv/abc123"
    assert normalize_video_url(url) == url


def test_video_request_accepts_watchlater():
    req = VideoRequest(
        url="https://www.bilibili.com/list/watchlater/?bvid=BV1CPXpBYEui&oid=116294762371214",
        platform="bilibili",
    )
    assert str(req.url) == "https://www.bilibili.com/video/BV1CPXpBYEui"


def test_video_request_rejects_no_bv():
    with pytest.raises(ValueError):
        VideoRequest(url="https://www.bilibili.com/anime/timeline", platform="bilibili")


def test_note_router_request_accepts_watchlater():
    # note.py 里的 VideoRequest 才是 /generate_note 实际使用的请求模型
    from app.routers.note import VideoRequest as NoteVideoRequest

    req = NoteVideoRequest(
        video_url="https://www.bilibili.com/list/watchlater/?bvid=BV1CPXpBYEui&oid=116294762371214&spm_id_from=333.881.0.0",
        platform="bilibili",
        quality="fast",
        model_name="test-model",
        provider_id="test-provider",
    )
    assert req.video_url == "https://www.bilibili.com/video/BV1CPXpBYEui"


def test_video_request_youtube_unaffected():
    req = VideoRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", platform="youtube")
    assert str(req.url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
