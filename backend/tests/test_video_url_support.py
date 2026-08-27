import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_module(name, relative_path):
    module_path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{name} module spec not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


url_parser = _load_module("url_parser", pathlib.Path("app") / "utils" / "url_parser.py")
video_url_validator = _load_module(
    "video_url_validator",
    pathlib.Path("app") / "validators" / "video_url_validator.py",
)


class TestVideoUrlSupport(unittest.TestCase):
    def test_extract_youtube_video_id_from_supported_url_shapes(self):
        expected_id = "dQw4w9WgXcQ"

        cases = [
            f"https://www.youtube.com/watch?v={expected_id}",
            f"https://youtu.be/{expected_id}",
            f"https://www.youtube.com/shorts/{expected_id}",
        ]

        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(
                    url_parser.extract_video_id(url, "youtube"),
                    expected_id,
                )

    def test_accepts_youtube_shorts_url(self):
        url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"

        self.assertTrue(video_url_validator.is_supported_video_url(url))

    def test_extract_xiaohongshu_note_id_from_supported_url_shapes(self):
        expected_id = "68a1bc2d000000001234abcd"
        cases = [
            f"https://www.xiaohongshu.com/explore/{expected_id}",
            f"https://www.xiaohongshu.com/discovery/item/{expected_id}?xsec_token=token",
            f"https://www.xiaohongshu.com/item/{expected_id}",
        ]

        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(
                    url_parser.extract_video_id(url, "xiaohongshu"),
                    expected_id,
                )

    def test_accepts_xiaohongshu_full_and_short_urls(self):
        self.assertTrue(video_url_validator.is_supported_video_url(
            "https://www.xiaohongshu.com/explore/68a1bc2d000000001234abcd"
        ))
        self.assertTrue(video_url_validator.is_supported_video_url(
            "https://xhslink.com/abc123"
        ))
        self.assertTrue(video_url_validator.is_supported_video_url(
            "https://xhslink.cn/o/3j9OsodA9Nh"
        ))
        self.assertFalse(video_url_validator.is_supported_video_url(
            "https://example.com/path/xiaohongshu.com/explore/68a1bc2d000000001234abcd"
        ))

    def test_xiaohongshu_request_extracts_url_from_share_text(self):
        request = video_url_validator.VideoRequest.model_validate({
            "url": "复制这条内容 https://xhslink.com/abc123 打开小红书查看",
            "platform": "xiaohongshu",
        })

        self.assertEqual(str(request.url), "https://xhslink.com/abc123")


if __name__ == "__main__":
    unittest.main()
