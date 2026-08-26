"""Regression coverage for the retry contract at the yt-dlp boundary."""
import importlib.util
import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "downloaders" / "youtube_downloader.py"


def _stub(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


class _Downloader:
    def __init__(self):
        self.cache_data = "/tmp"


class _AudioDownloadResult:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _load_downloader_module(monkeypatch):
    _stub(monkeypatch, "app")
    _stub(monkeypatch, "app.downloaders")
    _stub(monkeypatch, "app.models")
    _stub(monkeypatch, "app.services")
    _stub(monkeypatch, "app.utils")
    _stub(
        monkeypatch,
        "app.downloaders.base",
        Downloader=_Downloader,
        DownloadQuality=str,
        YDL_RETRY_OPTS={"retries": 3, "fragment_retries": 3, "socket_timeout": 30},
    )
    _stub(monkeypatch, "app.downloaders.youtube_subtitle", YouTubeSubtitleFetcher=object)
    _stub(monkeypatch, "app.models.notes_model", AudioDownloadResult=_AudioDownloadResult)
    _stub(monkeypatch, "app.models.transcriber_model", TranscriptResult=object)
    _stub(
        monkeypatch,
        "app.services.proxy_config_manager",
        ProxyConfigManager=type("ProxyConfigManager", (), {"get_proxy_url": lambda self: None}),
    )
    _stub(monkeypatch, "app.utils.path_helper", get_data_dir=lambda: "/tmp")
    _stub(monkeypatch, "app.utils.url_parser", extract_video_id=lambda url, platform: "video-id")

    spec = importlib.util.spec_from_file_location("youtube_downloader_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _CapturingYoutubeDL:
    options = None

    def __init__(self, options):
        type(self).options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, _url, download=True):
        return {
            "id": "video-id",
            "title": "Example",
            "duration": 1,
            "thumbnail": None,
            "ext": "m4a",
            "tags": [],
        }


def test_audio_download_configures_a_nonzero_retry_budget(tmp_path, monkeypatch):
    """Removing retry options must make transient download errors unprotected."""
    module = _load_downloader_module(monkeypatch)
    original_youtube_dl = module.yt_dlp.YoutubeDL
    module.yt_dlp.YoutubeDL = _CapturingYoutubeDL
    try:
        module.YoutubeDownloader().download("https://youtu.be/example", str(tmp_path))
    finally:
        module.yt_dlp.YoutubeDL = original_youtube_dl

    options = _CapturingYoutubeDL.options
    assert options["retries"] > 0
    assert options["fragment_retries"] > 0
    assert options["socket_timeout"] > 0
