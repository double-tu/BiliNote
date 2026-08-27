import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _Downloader:
    def download_video(self, _url):
        return "video.mp4"


class _VideoReader:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.selected_timestamps = []
        self.instances.append(self)

    def run(self):
        return []


class TestNoteVideoContextIsolation(unittest.TestCase):
    def test_prepare_video_context_uses_task_scoped_frame_and_grid_dirs(self):
        from app.services import note as note_module

        generator = object.__new__(note_module.NoteGenerator)
        generator.video_path = None
        generator.video_img_urls = []
        generator.video_frame_timestamps = []
        _VideoReader.instances = []

        with tempfile.TemporaryDirectory() as root, \
                patch.object(note_module, "get_app_dir", return_value=root), \
                patch.object(note_module, "VideoReader", _VideoReader):
            generator._prepare_video_context(
                downloader=_Downloader(),
                video_url="https://example.com/video",
                grid_size=[2, 2],
                frame_interval=6,
                task_id="task-123",
            )

        self.assertEqual(len(_VideoReader.instances), 1)
        reader_kwargs = _VideoReader.instances[0].kwargs
        self.assertEqual(
            Path(reader_kwargs["frame_dir"]),
            Path(root) / "task-123" / "frames",
        )
        self.assertEqual(
            Path(reader_kwargs["grid_dir"]),
            Path(root) / "task-123" / "grids",
        )


if __name__ == "__main__":
    unittest.main()
