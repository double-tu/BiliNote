import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app.downloaders.xiaohongshu_downloader import (
    XiaohongshuDownloader,
    extract_xiaohongshu_note_id,
    extract_xiaohongshu_share_url,
)
from app.routers.note import VideoRequest
from app.services.constant import SUPPORT_PLATFORM_MAP


NOTE_ID = "68a1bc2d000000001234abcd"


class _FakeResponse:
    def __init__(self, *, url="", text="", headers=None):
        self.url = url
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def close(self):
        return None


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested_urls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, url, **_kwargs):
        self.requested_urls.append(url)
        return self.responses.pop(0)


def _new_downloader_without_config():
    downloader = object.__new__(XiaohongshuDownloader)
    downloader._cookie = None
    downloader._info_cache = {}
    return downloader


def _initial_state_html(*, include_video=True):
    video = {
        "duration": 32500,
        "media": {
            "stream": {
                "h264": [{"masterUrl": "https://sns-video.example/video.mp4"}],
                "h265": [{"masterUrl": "https://sns-video.example/video-h265.mp4"}],
            },
        },
    } if include_video else None
    note = {
        "noteId": NOTE_ID,
        "title": "测试小红书视频",
        "desc": "视频简介",
        "user": {"nickname": "测试作者"},
        "imageList": [{"urlDefault": "https://sns-img.example/cover.jpg"}],
        "video": video,
        "optional": None,
    }
    state = {"note": {"noteDetailMap": {NOTE_ID: {"note": note}}}}
    # 还原页面中的 JavaScript undefined，验证兼容替换逻辑。
    state_json = json.dumps(state, ensure_ascii=False).replace('"optional": null', '"optional": undefined')
    return f"<script>window.__INITIAL_STATE__={state_json}</script>"


class TestXiaohongshuDownloader(unittest.TestCase):
    def test_platform_is_registered(self):
        self.assertIsInstance(SUPPORT_PLATFORM_MAP["xiaohongshu"], XiaohongshuDownloader)

    def test_generate_note_request_accepts_share_text(self):
        request = VideoRequest.model_validate({
            "video_url": "分享内容 https://xhslink.com/abc123 打开小红书",
            "platform": "xiaohongshu",
            "quality": "fast",
            "model_name": "test-model",
            "provider_id": "test-provider",
        })

        self.assertEqual(request.video_url, "https://xhslink.com/abc123")

    def test_extract_share_url_preserves_xsec_token(self):
        raw = (
            "复制打开小红书 https://www.xiaohongshu.com/explore/"
            f"{NOTE_ID}?xsec_token=token-value&xsec_source=pc_share。"
        )

        extracted = extract_xiaohongshu_share_url(raw)

        self.assertEqual(
            extracted,
            f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token=token-value&xsec_source=pc_share",
        )

    def test_extract_note_id_and_token(self):
        note_id, token = extract_xiaohongshu_note_id(
            f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}?xsec_token=a%2Bb"
        )

        self.assertEqual(note_id, NOTE_ID)
        self.assertEqual(token, "a+b")

    def test_duration_supports_short_video_milliseconds(self):
        html = _initial_state_html().replace('"duration": 32500', '"duration": 5000')
        url = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
        session = _FakeSession([_FakeResponse(text=html)])
        downloader = _new_downloader_without_config()

        with patch.object(downloader, "_new_session", return_value=session):
            info = downloader.fetch_video_info(url)

        self.assertEqual(info["duration"], 5.0)

    def test_fetch_video_info_resolves_short_link_and_prefers_h264(self):
        resolved_url = (
            f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
            "?xsec_token=secret-token&xsec_source=pc_share"
        )
        session = _FakeSession([
            _FakeResponse(url=resolved_url),
            _FakeResponse(text=_initial_state_html()),
        ])
        downloader = _new_downloader_without_config()

        with patch.object(downloader, "_new_session", return_value=session):
            info = downloader.fetch_video_info("分享内容 https://xhslink.com/abc123 更多内容")

        self.assertEqual(info["note_id"], NOTE_ID)
        self.assertEqual(info["direct_url"], "https://sns-video.example/video.mp4")
        self.assertEqual(info["duration"], 32.5)
        self.assertEqual(info["author"], "测试作者")
        self.assertEqual(
            session.requested_urls[1],
            f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}?xsec_token=secret-token",
        )

    def test_fetch_video_info_resolves_cn_short_link(self):
        resolved_url = (
            f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}"
            "?type=video&xsec_token=cn-secret&xhsshare=CopyLink"
        )
        session = _FakeSession([
            _FakeResponse(url=resolved_url),
            _FakeResponse(text=_initial_state_html()),
        ])
        downloader = _new_downloader_without_config()

        with patch.object(downloader, "_new_session", return_value=session):
            info = downloader.fetch_video_info("https://xhslink.cn/o/3j9OsodA9Nh")

        self.assertEqual(info["note_id"], NOTE_ID)
        self.assertEqual(session.requested_urls[0], "https://xhslink.cn/o/3j9OsodA9Nh")
        self.assertEqual(
            session.requested_urls[1],
            f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}?xsec_token=cn-secret",
        )

    def test_fetch_video_info_rejects_image_only_note(self):
        url = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
        session = _FakeSession([_FakeResponse(text=_initial_state_html(include_video=False))])
        downloader = _new_downloader_without_config()

        with patch.object(downloader, "_new_session", return_value=session):
            with self.assertRaisesRegex(ValueError, "图文笔记"):
                downloader.fetch_video_info(url)

    def test_download_extracts_audio_and_returns_metadata(self):
        downloader = _new_downloader_without_config()
        ffmpeg_commands = []
        info = {
            "note_id": NOTE_ID,
            "title": "测试视频",
            "duration": 12.0,
            "cover_url": "https://sns-img.example/cover.jpg",
            "direct_url": "https://sns-video.example/video.mp4",
            "webpage_url": f"https://www.xiaohongshu.com/explore/{NOTE_ID}",
            "description": "简介",
            "author": "作者",
        }

        def fake_stream(_direct_url, output_path):
            with open(output_path, "wb") as output_file:
                output_file.write(b"fake video")

        def fake_ffmpeg(command, **_kwargs):
            ffmpeg_commands.append(command)
            with open(command[-1], "wb") as output_file:
                output_file.write(b"fake audio")

        with tempfile.TemporaryDirectory() as output_dir:
            ffmpeg_path = os.path.join(output_dir, "ffmpeg.exe")
            with open(ffmpeg_path, "wb") as ffmpeg_file:
                ffmpeg_file.write(b"fake ffmpeg")

            with patch.dict(os.environ, {"FFMPEG_BIN_PATH": ffmpeg_path}), \
                    patch.object(downloader, "fetch_video_info", return_value=info), \
                    patch.object(downloader, "_download_stream", side_effect=fake_stream), \
                    patch("app.downloaders.xiaohongshu_downloader.subprocess.run", side_effect=fake_ffmpeg):
                result = downloader.download(info["webpage_url"], output_dir=output_dir)

            self.assertTrue(os.path.isfile(result.file_path))
            self.assertTrue(os.path.isfile(result.video_path))
            self.assertEqual(result.platform, "xiaohongshu")
            self.assertEqual(result.video_id, NOTE_ID)
            self.assertEqual(result.raw_info["uploader"], "作者")
            self.assertEqual(ffmpeg_commands[0][0], ffmpeg_path)

    def test_download_subtitles_returns_none(self):
        downloader = _new_downloader_without_config()
        self.assertIsNone(downloader.download_subtitles("https://xhslink.com/abc123"))


if __name__ == "__main__":
    unittest.main()
