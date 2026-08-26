"""原生千问/豆包 ASR 文件适配。

千问 realtime 与豆包使用 WebSocket；千问 Qwen-Audio 非 realtime 模型使用
DashScope HTTP 同步接口，并按官方 5 分钟/10MB 限制在本地分片后逐段提交。
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from websockets.sync.client import connect
import requests

from app.decorators.timeit import timeit
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.provider import ProviderService
from app.transcriber.base import Transcriber
from app.utils.logger import get_logger

logger = get_logger(__name__)

QWEN_ENDPOINT = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
QWEN_FILE_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DOUBAO_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"


def _ffmpeg_binary() -> str:
    """返回可执行的 FFmpeg 路径，兼容 Windows 的 exe 绝对路径配置。"""
    configured = os.getenv("FFMPEG_BIN_PATH", "").strip().strip('"')
    if configured:
        if os.path.isfile(configured):
            return configured
        if os.path.isdir(configured):
            for name in ("ffmpeg.exe", "ffmpeg"):
                candidate = os.path.join(configured, name)
                if os.path.isfile(candidate):
                    return candidate
    return shutil.which("ffmpeg") or "ffmpeg"


def _provider(provider_id: str) -> dict:
    row = ProviderService.get_provider_by_id(provider_id)
    if not row:
        raise ValueError(f"语音转写供应商不存在: {provider_id}，请先在模型供应商中配置")
    if not row.get("api_key", "").strip():
        raise ValueError(f"{row.get('name', provider_id)} 的 API Key 未配置")
    return row


def _pcm_chunks(path: str, size: int):
    # 16kHz * 16bit * mono = 32,000 bytes/s。实时 ASR 接口虽然允许分块，
    # 但不能把整段文件以本地磁盘读取速度突发发送，否则会触发服务端的
    # "Input traffic exceeds the limit"（尤其是千问 2560KB/s 限流）。
    bytes_per_second = 16000 * 2
    next_deadline = time.monotonic()
    process = subprocess.Popen(
        [_ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-i", path,
         "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        while True:
            chunk = process.stdout.read(size)
            if not chunk:
                break
            now = time.monotonic()
            if now < next_deadline:
                time.sleep(next_deadline - now)
            yield chunk
            next_deadline = max(next_deadline, time.monotonic()) + len(chunk) / bytes_per_second
        error = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        status = process.wait()
        if status:
            raise RuntimeError(f"FFmpeg 音频转换失败: {error[-500:]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _split_wav_chunks(path: str, chunk_seconds: int = 240) -> tuple[tempfile.TemporaryDirectory, list[tuple[str, float]]]:
    """把本地音频切成满足 Qwen-Audio 同步接口限制的 WAV 分片。"""
    temp_dir = tempfile.TemporaryDirectory(prefix="bilinote-qwen-asr-")
    pattern = os.path.join(temp_dir.name, "chunk-%03d.wav")
    process = subprocess.run(
        [_ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-i", path,
         "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
         "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1", pattern],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        temp_dir.cleanup()
        detail = process.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"FFmpeg Qwen ASR 分片失败: {detail}")
    paths = sorted(
        os.path.join(temp_dir.name, name)
        for name in os.listdir(temp_dir.name)
        if name.lower().endswith(".wav")
    )
    if not paths:
        temp_dir.cleanup()
        raise RuntimeError("FFmpeg 没有生成可用于千问 ASR 的 WAV 分片")
    return temp_dir, [(item, index * chunk_seconds) for index, item in enumerate(paths)]


def _event_id() -> str:
    return f"event_{uuid.uuid4()}"


def _qwen_endpoint(base_url: str) -> str:
    value = os.getenv("QWEN_ASR_ENDPOINT", "").strip() or base_url.strip()
    if value.startswith("https://"):
        value = "wss://" + value[len("https://"):]
    if value.startswith("http://"):
        value = "ws://" + value[len("http://"):]
    if not value.startswith(("ws://", "wss://")) or "compatible-mode" in value:
        value = QWEN_ENDPOINT
    return value.rstrip("/")


def _with_model(endpoint: str, model: str) -> str:
    parsed = urlparse(endpoint)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("model", model)
    return urlunparse(parsed._replace(query=urlencode(query)))


class QwenRealtimeTranscriber(Transcriber):
    """百炼 Qwen3-ASR realtime WebSocket 文件适配。"""

    def __init__(self, provider_id: str = "qwen", model: str = "qwen3-asr-flash-realtime"):
        self.provider_id = provider_id
        self.model = model or "qwen3-asr-flash-realtime"

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        provider = _provider(self.provider_id)
        endpoint = _with_model(_qwen_endpoint(provider.get("base_url", "")), self.model)
        completed: list[str] = []
        partial = ""
        events: list[dict] = []
        try:
            with connect(
                endpoint,
                additional_headers={
                    "Authorization": f"Bearer {provider['api_key'].strip()}",
                    "OpenAI-Beta": "realtime=v1",
                },
                open_timeout=10,
                close_timeout=10,
            ) as ws:
                ws.send(json.dumps({
                    "type": "session.update",
                    "event_id": _event_id(),
                    "session": {
                        "modalities": ["text"],
                        "input_audio_format": "pcm",
                        "sample_rate": 16000,
                        "turn_detection": {"type": "server_vad", "silence_duration_ms": 500},
                    },
                }))
                while True:
                    ready = json.loads(ws.recv(timeout=10))
                    if ready.get("type") == "error":
                        raise RuntimeError(ready.get("error", {}).get("message", "千问 ASR 会话初始化失败"))
                    if ready.get("type") == "session.updated":
                        break

                for chunk in _pcm_chunks(file_path, 3200):
                    ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "event_id": _event_id(),
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    }))
                ws.send(json.dumps({"type": "session.finish", "event_id": _event_id()}))

                while True:
                    message = json.loads(ws.recv(timeout=30))
                    events.append(message)
                    event_type = message.get("type", "")
                    if event_type == "conversation.item.input_audio_transcription.text":
                        partial = message.get("text") or message.get("stash") or partial
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        text = str(message.get("transcript", "")).strip()
                        if text:
                            completed.append(text)
                            partial = ""
                    elif event_type in ("conversation.item.input_audio_transcription.failed", "error"):
                        error = message.get("error", {}).get("message") or message.get("message") or "千问 ASR 转写失败"
                        raise RuntimeError(error)
                    elif event_type == "session.finished":
                        break
        except TimeoutError as exc:
            raise RuntimeError("千问 ASR 等待结果超时") from exc

        text = "".join(completed) + (partial.strip() if partial.strip() else "")
        return TranscriptResult(
            language=None,
            full_text=text.strip(),
            segments=[TranscriptSegment(start=0, end=0, text=text.strip())] if text.strip() else [],
            raw={"provider_id": self.provider_id, "model": self.model, "events": events},
        )


class QwenFileTranscriber(Transcriber):
    """百炼 Qwen-Audio 文件 ASR。

    ``qwen-audio-3.0-asr-flash`` 等非 realtime 模型使用 HTTP 文件接口，
    不应连接 ``api-ws/v1/realtime``，也不需要按实时速度发送音频流。
    """

    def __init__(self, provider_id: str = "qwen", model: str = "qwen-audio-3.0-asr-flash"):
        self.provider_id = provider_id
        self.model = model or "qwen-audio-3.0-asr-flash"

    @staticmethod
    def _response_text(payload: dict) -> str:
        output = payload.get("output", payload)
        if isinstance(output, dict):
            nested = output.get("output")
            if isinstance(nested, dict):
                sentence = nested.get("sentence")
                if isinstance(sentence, dict) and sentence.get("text"):
                    return str(sentence["text"]).strip()
            if output.get("text"):
                return str(output["text"]).strip()
        choices = output.get("choices", []) if isinstance(output, dict) else []
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("text"):
                        return str(item["text"]).strip()
            if isinstance(content, str):
                return content.strip()
        for key in ("text", "transcript"):
            value = output.get(key) if isinstance(output, dict) else None
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _response_segments(payload: dict, offset: float) -> list[TranscriptSegment]:
        """兼容文档中的句级时间戳返回；没有时间戳时由调用方生成整段字幕。"""
        output = payload.get("output", payload)
        nested = output.get("output", {}) if isinstance(output, dict) else {}
        sentences = nested.get("sentences", []) if isinstance(nested, dict) else []
        result: list[TranscriptSegment] = []
        for item in sentences or []:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            start = float(item.get("begin_time", 0) or 0) / 1000 + offset
            end = float(item.get("end_time", 0) or 0) / 1000 + offset
            result.append(TranscriptSegment(start=start, end=end, text=text))
        return result

    def _transcribe_chunk(self, provider: dict, path: str) -> tuple[str, list[TranscriptSegment], dict]:
        max_mb = int(os.getenv("QWEN_ASR_MAX_FILE_MB", "10"))
        if os.path.getsize(path) > max_mb * 1024 * 1024:
            raise ValueError(
                f"千问 ASR 分片超过 {max_mb}MB；请降低本地分片时长或压缩音频"
            )
        with open(path, "rb") as audio:
            audio_data_uri = f"data:audio/wav;base64,{base64.b64encode(audio.read()).decode('ascii')}"
        payload = {
            "model": self.model,
            "input": {
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "input_audio",
                        "input_audio": {"data": audio_data_uri},
                    }],
                }],
            },
            "parameters": {"format": "wav", "sample_rate": "16000"},
        }
        from app.services.proxy_config_manager import ProxyConfigManager
        ProxyConfigManager().apply_to_env()
        response = requests.post(
            os.getenv("QWEN_ASR_FILE_ENDPOINT", "").strip() or QWEN_FILE_ENDPOINT,
            headers={
                "Authorization": f"Bearer {provider['api_key'].strip()}",
                "X-DashScope-SSE": "disable",
            },
            json=payload,
            timeout=180,
        )
        if not response.ok:
            detail = response.text[:1000]
            raise RuntimeError(f"千问文件 ASR 请求失败 HTTP {response.status_code}: {detail}")
        result = response.json()
        text = self._response_text(result)
        if not text:
            raise RuntimeError("千问文件 ASR 响应中没有识别文本")
        return text, self._response_segments(result, 0), result

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        provider = _provider(self.provider_id)
        if "filetrans" in self.model.lower():
            raise ValueError(
                "Qwen-Audio-3.0-ASR-Flash-Filetrans 需要公网音频 URL 并通过异步任务轮询；"
                "当前本地部署未配置 OSS/公网文件托管，请使用 qwen-audio-3.0-asr-flash，"
                "由后端本地分片处理长音频"
            )
        # 文档规定同步 Qwen-Audio 单次最多 5 分钟/10MB；使用 4 分钟分片
        # 留出 WAV 头和 Base64 膨胀空间，长音频在本地切片后逐段提交。
        temp_dir, chunks = _split_wav_chunks(file_path, chunk_seconds=240)
        texts: list[str] = []
        segments: list[TranscriptSegment] = []
        raw_parts: list[dict] = []
        try:
            for index, (chunk_path, offset) in enumerate(chunks, start=1):
                logger.info("上传千问文件 ASR 分片 %s/%s: %s", index, len(chunks), chunk_path)
                text, part_segments, raw = self._transcribe_chunk(provider, chunk_path)
                texts.append(text)
                if part_segments:
                    segments.extend(
                        TranscriptSegment(
                            start=item.start + offset,
                            end=item.end + offset,
                            text=item.text,
                        )
                        for item in part_segments
                    )
                raw_parts.append(raw)
        finally:
            temp_dir.cleanup()
        text = "\n".join(texts).strip()
        if not segments and text:
            segments = [TranscriptSegment(start=0, end=0, text=text)]
        return TranscriptResult(
            language=None,
            full_text=text,
            segments=segments,
            raw={"provider_id": self.provider_id, "model": self.model, "parts": raw_parts},
        )


def _frame(message_type: int, flags: int, serialization: int, payload: bytes, sequence: int | None = None) -> bytes:
    header = bytes((0x11, (message_type << 4) | flags, serialization << 4, 0))
    if flags in (1, 3):
        header += int(sequence or 0).to_bytes(4, "big", signed=True)
    return header + len(payload).to_bytes(4, "big") + payload


def _parse_frame(data: bytes) -> tuple[int, int, bytes, int | None, int | None] | None:
    if len(data) < 8:
        return None
    header_size = (data[0] & 0x0F) * 4
    if header_size < 4 or len(data) < header_size + 4:
        return None
    message_type = (data[1] >> 4) & 0x0F
    flags = data[1] & 0x0F
    offset = header_size
    sequence = None
    if flags in (1, 3):
        sequence = int.from_bytes(data[offset:offset + 4], "big", signed=True)
        offset += 4
    if message_type == 0x0F:
        if len(data) < offset + 8:
            return None
        code = int.from_bytes(data[offset:offset + 4], "big")
        size = int.from_bytes(data[offset + 4:offset + 8], "big")
        offset += 8
        return message_type, flags, data[offset:offset + size], sequence, code
    size = int.from_bytes(data[offset:offset + 4], "big")
    offset += 4
    return message_type, flags, data[offset:offset + size], sequence, None


def _doubao_credentials(api_key: str) -> tuple[str, str, str, str]:
    """支持 JSON 或 app_id|access_token 两种存储格式。"""
    try:
        value = json.loads(api_key)
        return (
            str(value.get("app_id", "")),
            str(value.get("access_token", value.get("api_key", ""))),
            str(value.get("resource_id") or os.getenv("VOLCENGINE_RESOURCE_ID", "volc.seedasr.sauc.duration")),
            str(value.get("auth_mode", "app_id_token")),
        )
    except (json.JSONDecodeError, TypeError):
        if "|" in api_key:
            app_id, token = api_key.split("|", 1)
            return app_id.strip(), token.strip(), "volc.seedasr.sauc.duration", "app_id_token"
        return "", api_key.strip(), os.getenv("VOLCENGINE_RESOURCE_ID", "volc.seedasr.sauc.duration"), "api_key"


class DoubaoTranscriber(Transcriber):
    """豆包/火山大模型流式 ASR 的文件适配。"""

    def __init__(self, provider_id: str = "volcengine", model: str = "bigmodel"):
        self.provider_id = provider_id
        self.model = model or "bigmodel"

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        provider = _provider(self.provider_id)
        app_id, access_token, resource_id, auth_mode = _doubao_credentials(provider["api_key"])
        if auth_mode == "app_id_token" and not app_id:
            raise ValueError("豆包/火山 ASR 凭据缺少 app_id；请填写 app_id|access_token 或 JSON 凭据")
        endpoint = (
            os.getenv("DOUBAO_ASR_ENDPOINT", "").strip()
            or provider.get("base_url", "").strip()
            or DOUBAO_ENDPOINT
        )
        headers = {
            "X-Api-Resource-Id": resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        if auth_mode == "api_key":
            headers["X-Api-Key"] = access_token
        else:
            headers["X-Api-App-Key"] = app_id
            headers["X-Api-Access-Key"] = access_token

        text = ""
        segments: list[TranscriptSegment] = []
        raw_frames: list[Any] = []
        sequence = 1
        with connect(endpoint, additional_headers=headers, open_timeout=10, close_timeout=10) as ws:
            payload = {
                "user": {"uid": headers["X-Api-Connect-Id"]},
                "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1, "codec": "raw"},
                "request": {"model_name": self.model, "enable_itn": True, "enable_punc": True, "show_utterances": True},
            }
            ws.send(_frame(1, 1, 1, json.dumps(payload).encode(), sequence))
            sequence += 1
            for chunk in _pcm_chunks(file_path, 6400):
                ws.send(_frame(2, 1, 0, chunk, sequence))
                sequence += 1
            ws.send(_frame(2, 3, 0, b"", -sequence))
            while True:
                parsed = _parse_frame(ws.recv(timeout=30))
                if not parsed:
                    continue
                message_type, flags, body, _seq, error_code = parsed
                if message_type == 0x0F:
                    raise RuntimeError(f"豆包/火山 ASR 错误 {error_code}: {body.decode(errors='replace')}")
                try:
                    value = json.loads(body.decode("utf-8"))
                    raw_frames.append(value)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                result = value.get("result", value)
                if isinstance(result, list):
                    result = result[0] if result else {}
                text = result.get("text", text)
                for utterance in result.get("utterances", []) or []:
                    utterance_text = utterance.get("text", "").strip()
                    if not utterance_text:
                        continue
                    segments.append(TranscriptSegment(
                        start=float(utterance.get("start_time", 0)) / 1000,
                        end=float(utterance.get("end_time", 0)) / 1000,
                        text=utterance_text,
                    ))
                if flags in (2, 3) or (_seq is not None and _seq < 0):
                    break
        if not text and segments:
            text = "".join(segment.text for segment in segments)
        return TranscriptResult(
            language=None,
            full_text=text.strip(),
            segments=segments or ([TranscriptSegment(start=0, end=0, text=text.strip())] if text.strip() else []),
            raw={"provider_id": self.provider_id, "model": self.model, "frames": raw_frames},
        )
