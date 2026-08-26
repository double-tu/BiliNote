"""Model-selection contract tests without loading the Whisper runtime."""
import importlib.util
import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "transcriber" / "transcriber_provider.py"


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class _Whisper:
    def __init__(self, model_size, device):
        self.model_size = model_size
        self.device = device


def _stub(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def _load_provider(monkeypatch):
    _stub(monkeypatch, "app")
    _stub(monkeypatch, "app.transcriber")
    _stub(monkeypatch, "app.utils")
    _stub(monkeypatch, "app.transcriber.groq", GroqTranscriber=object)
    _stub(monkeypatch, "app.transcriber.whisper", WhisperTranscriber=_Whisper)
    _stub(monkeypatch, "app.transcriber.bcut", BcutTranscriber=object)
    _stub(monkeypatch, "app.transcriber.kuaishou", KuaishouTranscriber=object)
    _stub(monkeypatch, "app.utils.logger", get_logger=lambda _name: _Logger())

    spec = importlib.util.spec_from_file_location("transcriber_provider_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_explicit_whisper_model_overrides_docker_default(monkeypatch):
    """A saved UI choice must not be replaced by WHISPER_MODEL_SIZE."""
    provider = _load_provider(monkeypatch)
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "tiny")

    transcriber = provider.get_transcriber(
        transcriber_type="fast-whisper",
        model_size="large-v3-turbo",
        device="cpu",
    )

    assert transcriber.model_size == "large-v3-turbo"


def test_switching_whisper_models_rebuilds_the_cached_instance(monkeypatch):
    """Caching only by transcriber type would keep returning the first model."""
    provider = _load_provider(monkeypatch)
    monkeypatch.delenv("WHISPER_MODEL_SIZE", raising=False)

    base = provider.get_transcriber("fast-whisper", model_size="base", device="cpu")
    turbo = provider.get_transcriber("fast-whisper", model_size="large-v3-turbo", device="cpu")

    assert turbo is not base
    assert turbo.model_size == "large-v3-turbo"
