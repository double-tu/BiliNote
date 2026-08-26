import os
import pathlib
import subprocess
import sys
import textwrap


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run_isolated(script: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(ROOT), env.get("PYTHONPATH")])
    )
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_note_generator_forwards_configured_whisper_model_size():
    _run_isolated(
        """
        from app.services import note as note_service

        calls = []

        def fake_get_transcriber(**kwargs):
            calls.append(kwargs)
            return object()

        note_service.get_transcriber = fake_get_transcriber

        generator = note_service.NoteGenerator.__new__(note_service.NoteGenerator)
        generator.transcriber_type = "fast-whisper"
        generator.model_size = "large-v3-turbo"

        generator._init_transcriber()

        assert calls == [
            {
                "transcriber_type": "fast-whisper",
                "model_size": "large-v3-turbo",
            }
        ]
        """
    )


def test_whisper_cache_is_rebuilt_when_model_size_changes():
    _run_isolated(
        """
        from app.transcriber import transcriber_provider as provider

        class FakeWhisperTranscriber:
            def __init__(self, model_size, device):
                self.model_size = model_size
                self.device = device

        provider._transcribers = {key: None for key in provider._transcribers}
        provider._transcriber_configs = {
            key: None for key in provider._transcribers
        }
        provider.WhisperTranscriber = FakeWhisperTranscriber

        base = provider.get_whisper_transcriber("base", device="cpu")
        turbo = provider.get_whisper_transcriber("large-v3-turbo", device="cpu")
        turbo_again = provider.get_whisper_transcriber(
            "large-v3-turbo", device="cpu"
        )

        assert base.model_size == "base"
        assert turbo.model_size == "large-v3-turbo"
        assert turbo is not base
        assert turbo_again is turbo
        """
    )


def test_explicit_model_size_wins_over_environment_default():
    _run_isolated(
        """
        import os

        from app.transcriber import transcriber_provider as provider

        class FakeWhisperTranscriber:
            def __init__(self, model_size, device):
                self.model_size = model_size
                self.device = device

        os.environ["WHISPER_MODEL_SIZE"] = "tiny"
        provider._transcribers = {key: None for key in provider._transcribers}
        provider._transcriber_configs = {
            key: None for key in provider._transcribers
        }
        provider.WhisperTranscriber = FakeWhisperTranscriber

        transcriber = provider.get_transcriber(
            transcriber_type="fast-whisper",
            model_size="large-v3-turbo",
            device="cpu",
        )

        assert transcriber.model_size == "large-v3-turbo"

        fallback = provider.get_transcriber(
            transcriber_type="fast-whisper",
            model_size=None,
            device="cpu",
        )

        assert fallback.model_size == "tiny"
        """
    )
