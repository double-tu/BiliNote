import json

from app.transcriber.native_asr import (
    DOUBAO_ENDPOINT,
    QWEN_ENDPOINT,
    _doubao_credentials,
    _frame,
    _parse_frame,
    _qwen_endpoint,
    _with_model,
)


def test_qwen_endpoint_normalizes_dashscope_compatible_url():
    assert _qwen_endpoint("https://dashscope.aliyuncs.com/compatible-mode/v1") == QWEN_ENDPOINT


def test_qwen_endpoint_appends_model_without_duplicate_query():
    value = _with_model(QWEN_ENDPOINT, "qwen3-asr-flash-realtime")
    assert "model=qwen3-asr-flash-realtime" in value
    assert _with_model(value, "other").count("model=") == 1


def test_doubao_credentials_support_pipe_and_json_formats():
    assert _doubao_credentials("app|token")[:2] == ("app", "token")
    parsed = _doubao_credentials(json.dumps({"app_id": "a", "access_token": "t", "resource_id": "r"}))
    assert parsed[:3] == ("a", "t", "r")


def test_doubao_frame_round_trip():
    parsed = _parse_frame(_frame(1, 1, 1, b"{}", 3))
    assert parsed is not None
    assert parsed[0] == 1
    assert parsed[3] == 3
    assert parsed[2] == b"{}"
    assert DOUBAO_ENDPOINT.startswith("wss://")
