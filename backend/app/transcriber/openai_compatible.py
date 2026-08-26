"""批量 OpenAI 兼容语音转写。

很多云厂商把文件转写接口做成了 OpenAI ``audio.transcriptions`` 兼容协议，
例如 OpenAI、Groq、部分 DashScope/百炼兼容端点、硅基流动以及用户自建网关。
本模块统一处理认证、响应解析和长音频本地分片。
"""

import math
import os
import tempfile
from pathlib import Path
from typing import Any

import ffmpeg

from app.decorators.timeit import timeit
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.provider import ProviderService
from app.transcriber.base import Transcriber
from app.utils.logger import get_logger
from app.utils.openai_client import build_openai_client

logger = get_logger(__name__)

DEFAULT_MAX_UPLOAD_MB = 20
DEFAULT_CHUNK_SECONDS = 600


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _raw_response(response: Any) -> dict:
    if isinstance(response, dict):
        return response
    to_dict = getattr(response, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {"text": _value(response, "text", "")}


def _parse_response(response: Any, offset: float = 0.0) -> tuple[str, list[TranscriptSegment], dict]:
    text = str(_value(response, "text", "") or "").strip()
    segments: list[TranscriptSegment] = []
    for item in _value(response, "segments", []) or []:
        segment_text = str(_value(item, "text", "") or "").strip()
        if not segment_text:
            continue
        start = float(_value(item, "start", 0) or 0) + offset
        end = float(_value(item, "end", start - offset) or (start - offset)) + offset
        segments.append(TranscriptSegment(start=start, end=end, text=segment_text))
    if not text:
        text = " ".join(segment.text for segment in segments).strip()
    return text, segments, _raw_response(response)


def _probe_duration(path: str) -> float:
    metadata = ffmpeg.probe(path)
    return float(metadata["format"]["duration"])


def _split_audio(
    path: str,
    chunk_seconds: int,
    max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB,
) -> tuple[tempfile.TemporaryDirectory, list[tuple[str, float]]]:
    """将长音频切成低码率 MP3，返回临时目录和 (文件路径, 时间偏移)。"""
    duration = _probe_duration(path)
    if duration <= 0:
        raise ValueError("无法读取音频时长，无法执行长音频分片")

    temp_dir = tempfile.TemporaryDirectory(prefix="bilinote-asr-")
    chunks: list[tuple[str, float]] = []
    count = max(1, math.ceil(duration / chunk_seconds))
    for index in range(count):
        offset = index * chunk_seconds
        if offset >= duration:
            break
        output = str(Path(temp_dir.name) / f"chunk-{index:04d}.mp3")
        length = min(chunk_seconds, duration - offset)
        try:
            (
                ffmpeg.input(path, ss=offset, t=length)
                .output(output, ac=1, ar=16000, audio_bitrate="64k", format="mp3")
                .overwrite_output()
                .run(quiet=True)
            )
        except ffmpeg.Error as exc:
            temp_dir.cleanup()
            raise RuntimeError(f"长音频分片失败: {exc}") from exc
        if os.path.getsize(output) > max_upload_mb * 1024 * 1024:
            temp_dir.cleanup()
            raise ValueError(
                f"音频分片仍超过 {max_upload_mb}MB，请降低 TRANSCRIBER_CHUNK_SECONDS"
            )
        chunks.append((output, offset))
    return temp_dir, chunks


class OpenAICompatibleTranscriber(Transcriber):
    """使用已配置供应商的 OpenAI 兼容 ``audio.transcriptions`` 接口。"""

    def __init__(
        self,
        provider_id: str = "openai",
        model: str = "whisper-1",
        max_upload_mb: int | None = None,
        chunk_seconds: int | None = None,
    ):
        self.provider_id = provider_id.strip() or "openai"
        self.model = model.strip() or "whisper-1"
        self.max_upload_mb = max_upload_mb or int(os.getenv("TRANSCRIBER_MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB))
        self.chunk_seconds = chunk_seconds or int(os.getenv("TRANSCRIBER_CHUNK_SECONDS", DEFAULT_CHUNK_SECONDS))

    def _transcribe_one(self, client: Any, path: str) -> Any:
        try:
            with open(path, "rb") as audio:
                return client.audio.transcriptions.create(
                    file=(os.path.basename(path), audio.read()),
                    model=self.model,
                    response_format="verbose_json",
                )
        except Exception as exc:
            # OpenAI SDK 会把 DNS、代理拒绝、连接重置等都压缩成
            # "Connection error."；把底层 cause 写入后端日志并透传可读信息，
            # 便于 Windows 用户判断是代理还是供应商权限问题。
            cause = getattr(exc, "__cause__", None)
            detail = str(cause or exc).strip() or exc.__class__.__name__
            logger.error(
                "语音转写请求失败(provider=%s, model=%s, file=%s): %s",
                self.provider_id,
                self.model,
                os.path.basename(path),
                detail,
                exc_info=True,
            )
            raise RuntimeError(
                f"{self.provider_id} 语音转写请求失败：{detail}。"
                "请检查 API Key、网络/代理和转写模型名称"
            ) from exc

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        provider = ProviderService.get_provider_by_id(self.provider_id)
        if not provider:
            raise ValueError(f"语音转写供应商不存在: {self.provider_id}，请先在「设置 → 模型供应商」中配置")

        client = build_openai_client(
            api_key=provider.get("api_key"),
            base_url=provider.get("base_url"),
            key_label=f"{provider.get('name', self.provider_id)} 转写 API Key",
            timeout=600,
        )
        max_bytes = self.max_upload_mb * 1024 * 1024
        temp_dir = None
        try:
            if os.path.getsize(file_path) <= max_bytes:
                chunks = [(file_path, 0.0)]
            else:
                temp_dir, chunks = _split_audio(file_path, self.chunk_seconds, self.max_upload_mb)

            texts: list[str] = []
            segments: list[TranscriptSegment] = []
            raw_parts: list[dict] = []
            language = None
            for index, (chunk_path, offset) in enumerate(chunks, start=1):
                logger.info("上传语音转写分片 %s/%s: %s", index, len(chunks), chunk_path)
                response = self._transcribe_one(client, chunk_path)
                text, part_segments, raw = _parse_response(response, offset)
                if text:
                    texts.append(text)
                segments.extend(part_segments)
                raw_parts.append(raw)
                language = language or _value(response, "language")

            if not segments and texts:
                segments = [TranscriptSegment(start=0, end=0, text=" ".join(texts))]
            return TranscriptResult(
                language=language,
                full_text=" ".join(texts).strip(),
                segments=segments,
                raw={"provider_id": self.provider_id, "model": self.model, "parts": raw_parts},
            )
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()
