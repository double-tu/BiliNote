import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "utils" / "screenshot_marker.py"
spec = importlib.util.spec_from_file_location("screenshot_marker", MODULE_PATH)
if spec is None or spec.loader is None:
    raise ImportError("screenshot_marker module spec not found")
screenshot_marker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot_marker)
extract_screenshot_timestamps = screenshot_marker.extract_screenshot_timestamps
insert_screenshot_markers_by_section = screenshot_marker.insert_screenshot_markers_by_section


class TestScreenshotMarker(unittest.TestCase):
    def test_extract_accepts_star_bracket_format(self):
        markdown = "A\n*Screenshot-[01:02]\nB"
        matches = extract_screenshot_timestamps(markdown)
        self.assertEqual(matches, [("*Screenshot-[01:02]", 62)])

    def test_extract_accepts_legacy_formats(self):
        markdown = "*Screenshot-03:04 and Screenshot-[05:06]"
        matches = extract_screenshot_timestamps(markdown)
        self.assertEqual(
            matches,
            [
                ("*Screenshot-03:04", 184),
                ("Screenshot-[05:06]", 306),
            ],
        )

    def test_extract_accepts_gemini_wrapped_marker(self):
        markdown = "正文\n*Screenshot-[00:10]*\n正文"
        matches = extract_screenshot_timestamps(markdown)
        self.assertEqual(matches, [("*Screenshot-[00:10]*", 10)])

    def test_preserves_existing_model_markers(self):
        markdown = "## 第一节 *Content-[00:00]\n正文\n*Screenshot-[00:10]"
        self.assertEqual(
            insert_screenshot_markers_by_section(markdown, [20, 30], duration=60),
            markdown,
        )

    def test_places_fallback_markers_inside_matching_sections(self):
        markdown = """# 标题

## 第一节 *Content-[00:00]
第一节正文

## 第二节 *Content-[01:00]
第二节正文

## 第三节 *Content-[02:00]
第三节正文

## AI 总结
总结正文"""
        result = insert_screenshot_markers_by_section(markdown, [10, 20, 70, 130], duration=180)

        first_end = result.index("## 第二节")
        second_end = result.index("## 第三节")
        summary_start = result.index("## AI 总结")
        self.assertLess(result.index("*Screenshot-[00:10]"), first_end)
        self.assertLess(result.index("*Screenshot-[00:20]"), first_end)
        self.assertGreater(result.index("*Screenshot-[01:10]"), first_end)
        self.assertLess(result.index("*Screenshot-[01:10]"), second_end)
        self.assertGreater(result.index("*Screenshot-[02:10]"), second_end)
        self.assertLess(result.index("*Screenshot-[02:10]"), summary_start)

    def test_repeated_content_times_use_document_order(self):
        markdown = """# 标题

## 开始 *Content-[00:00]
正文

## 项目列表 *Content-[01:30]
正文

### 项目甲 *Content-[01:30]
正文

### 项目乙 *Content-[01:30]
正文

### 项目丙 *Content-[01:30]
正文

## AI 总结
总结"""
        result = insert_screenshot_markers_by_section(markdown, [90, 120, 150], duration=180)

        self.assertLess(result.index("*Screenshot-[01:30]"), result.index("### 项目甲"))
        self.assertGreater(result.index("*Screenshot-[02:00]"), result.index("### 项目甲"))
        self.assertGreater(result.index("*Screenshot-[02:30]"), result.index("### 项目乙"))
        self.assertLess(result.index("*Screenshot-[02:30]"), result.index("## AI 总结"))

    def test_without_content_times_distributes_across_sections(self):
        markdown = """# 标题
## 第一节
正文
## 第二节
正文
## 第三节
正文"""
        result = insert_screenshot_markers_by_section(markdown, [10, 70, 130], duration=180)

        self.assertLess(result.index("*Screenshot-[00:10]"), result.index("## 第二节"))
        self.assertLess(result.index("*Screenshot-[01:10]"), result.index("## 第三节"))
        self.assertGreater(result.index("*Screenshot-[02:10]"), result.index("## 第三节"))

    def test_skips_empty_parent_heading_and_inherits_its_time(self):
        markdown = """## 章节 *Content-[01:00]
### 子章节甲
甲正文
### 子章节乙
乙正文"""
        result = insert_screenshot_markers_by_section(markdown, [70], duration=120)

        self.assertGreater(result.index("*Screenshot-[01:10]"), result.index("甲正文"))
        self.assertLess(result.index("*Screenshot-[01:10]"), result.index("### 子章节乙"))


if __name__ == "__main__":
    unittest.main()
