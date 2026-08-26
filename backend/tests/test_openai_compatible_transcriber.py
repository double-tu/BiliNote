from types import SimpleNamespace

from app.transcriber.openai_compatible import _parse_response


def test_parse_response_offsets_segments_and_keeps_raw_text():
    response = SimpleNamespace(
        text=" hello world ",
        language="en",
        segments=[
            SimpleNamespace(start=0.5, end=1.5, text=" hello "),
            {"start": 1.5, "end": 2.5, "text": "world"},
        ],
    )

    text, segments, raw = _parse_response(response, offset=10)

    assert text == "hello world"
    assert [(item.start, item.end, item.text) for item in segments] == [
        (10.5, 11.5, "hello"),
        (11.5, 12.5, "world"),
    ]
    assert raw["text"] == " hello world "


def test_parse_response_builds_text_from_segments_when_provider_omits_text():
    response = {"segments": [{"start": 0, "end": 1, "text": "你好"}]}

    text, segments, _ = _parse_response(response)

    assert text == "你好"
    assert len(segments) == 1
