from app.gpt.base import GPT
from app.gpt.prompt_builder import generate_base_prompt
from app.models.gpt_model import GPTSource
import os
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import logging
from app.gpt.prompt import BASE_PROMPT, AI_SUM, SCREENSHOT, LINK, MERGE_PROMPT
from app.gpt.utils import fix_markdown
from app.gpt.request_chunker import RequestChunker
from app.models.transcriber_model import TranscriptSegment
from datetime import timedelta
from typing import List
try:
    from app.utils.logger import get_logger
except ModuleNotFoundError:  # 兼容独立加载 UniversalGPT 的单元测试
    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)
class UniversalGPT(GPT):
    def __init__(self, client, model: str, temperature: float = 0.7):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.screenshot = False
        self.link = False
        self.max_request_bytes = int(os.getenv("OPENAI_MAX_REQUEST_BYTES", str(45 * 1024 * 1024)))
        # 请求体字节数不能代表模型上下文 token。为长视频启用保守的 token 预算，
        # 并预留输出空间；供应商/模型有不同上下文窗口时可通过环境变量覆盖。
        self.context_window_tokens = max(4096, int(os.getenv("OPENAI_CONTEXT_WINDOW_TOKENS", "32768")))
        self.max_output_tokens = max(512, int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "4096")))
        self.context_safety_ratio = min(0.95, max(0.5, float(os.getenv("OPENAI_CONTEXT_SAFETY_RATIO", "0.85"))))
        self.image_token_cost = max(256, int(os.getenv("OPENAI_IMAGE_TOKEN_COST", "1600")))
        self.max_input_tokens = max(
            1024,
            int(self.context_window_tokens * self.context_safety_ratio) - self.max_output_tokens,
        )
        logger.info(
            "[GPT] 上下文预算: model=%s window=%d input=%d output=%d image_cost=%d",
            self.model,
            self.context_window_tokens,
            self.max_input_tokens,
            self.max_output_tokens,
            self.image_token_cost,
        )
        self.checkpoint_dir = Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        # 初始化时缓存重试配置，避免每次请求重复读取环境变量
        self._max_retry_attempts = max(1, int(os.getenv("OPENAI_RETRY_ATTEMPTS", "3")))
        self._retry_base_backoff = float(os.getenv("OPENAI_RETRY_BACKOFF_SECONDS", "1.5"))
    def _format_time(self, seconds: float) -> str:
        return str(timedelta(seconds=int(seconds)))[2:]
    def _build_segment_text(self, segments: List[TranscriptSegment]) -> str:
        return "\n".join(
            f"{self._format_time(seg.start)} - {seg.text.strip()}"
            for seg in segments
        )
    def ensure_segments_type(self, segments) -> List[TranscriptSegment]:
        return [TranscriptSegment(**seg) if isinstance(seg, dict) else seg for seg in segments]
    def create_messages(self, segments: List[TranscriptSegment], **kwargs):
        content_text = generate_base_prompt(
            title=kwargs.get('title'),
            segment_text=self._build_segment_text(segments),
            tags=kwargs.get('tags'),
            _format=kwargs.get('_format'),
            style=kwargs.get('style'),
            extras=kwargs.get('extras'),
        )
        video_img_urls = kwargs.get('video_img_urls', [])
        visual_context = kwargs.get("visual_context")
        if visual_context:
            content_text += (
                "\n\n以下是视觉模型对视频关键帧的分析，请将其作为带时间信息的辅助事实，"
                "与字幕内容交叉理解，不要编造未出现的画面：\n" + visual_context
            )
        content: list[dict] | str
        if video_img_urls:
            # 有截图时走 OpenAI 多模态 content 数组（text + image_url）
            content = [{"type": "text", "text": content_text}]
            for url in video_img_urls:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": url,
                        "detail": "auto"
                    }
                })
        else:
            # 纯文本场景退回 string content：DeepSeek deepseek-chat 等非多模态模型
            # 不识别 [{"type":"text",...}] 数组形态，会返回 invalid_request_error
            # （issue #282）。OpenAI 规范本身也允许 content 为 string。
            content = content_text
        messages = [{
            "role": "user",
            "content": content
        }]
        return messages
    def list_models(self):
        return self.client.models.list()
    def _estimate_messages_bytes(self, messages: list) -> int:
        import json
        return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

    def _estimate_messages_tokens(self, messages: list) -> int:
        return RequestChunker.estimate_tokens(messages, image_token_cost=self.image_token_cost)

    def _message_stats(self, messages: list) -> tuple[int, int, int]:
        """返回请求体字节数、估算 token 数和图片数量（不记录请求内容）。"""
        image_count = 0
        for message in messages or []:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                image_count += sum(
                    1 for item in content
                    if isinstance(item, dict) and item.get("type") == "image_url"
                )
        return (
            self._estimate_messages_bytes(messages),
            self._estimate_messages_tokens(messages),
            image_count,
        )

    @staticmethod
    def _response_output_chars(response) -> int:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return 0
        message = getattr(choices[0], "message", None)
        return len(getattr(message, "content", None) or "")

    def _build_merge_messages(self, partials: list) -> list:
        merge_text = MERGE_PROMPT + "\n\n" + "\n\n---\n\n".join(partials)
        # 合并阶段没有图片，直接用 string content 兼容非多模态模型（issue #282）
        return [{
            "role": "user",
            "content": merge_text
        }]

    def analyze_images(self, image_urls: list, title: str = "", visual_prompt: str = "") -> str:
        """仅执行视觉分析，不生成最终笔记，供分离式视频理解使用。"""
        images = list(image_urls or [])
        if not images:
            return ""
        prompt = visual_prompt or (
            "你是视频关键帧分析器。请按图片中标注的时间戳逐张描述重要视觉信息，"
            "重点关注场景切换、屏幕文字、代码、图表、人物动作和物体变化。"
            "忽略连续且无意义的轻微变化。输出简洁的带时间戳要点，不要生成文章。"
        )
        if title:
            prompt = f"视频标题：{title}\n\n{prompt}"
        content = [{"type": "text", "text": prompt}]
        content.extend(
            {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}
            for url in images
        )
        logger.info(
            "[GPT] 视觉分析开始: model=%s images=%d title_chars=%d",
            self.model,
            len(images),
            len(title or ""),
        )
        response = self._chat_completion_create(
            [{"role": "user", "content": content}],
            phase="visual.analyze",
        )
        logger.info("[GPT] 视觉分析完成: model=%s output_chars=%d", self.model, len(response.choices[0].message.content or ""))
        return (response.choices[0].message.content or "").strip()
    def _checkpoint_path(self, checkpoint_key: str) -> Path:
        safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in checkpoint_key)
        return self.checkpoint_dir / f"{safe_key}.gpt.checkpoint.json"
    def _build_source_signature(self, source: GPTSource) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_request_bytes": self.max_request_bytes,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "image_token_cost": self.image_token_cost,
            "title": source.title,
            "tags": source.tags,
            "format": source._format,
            "style": source.style,
            "extras": source.extras,
            "video_img_urls": source.video_img_urls or [],
            "visual_context": source.visual_context or "",
            "segments": [
                {
                    "start": getattr(seg, "start", None),
                    "end": getattr(seg, "end", None),
                    "text": getattr(seg, "text", "")
                }
                for seg in source.segment
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    def _load_checkpoint(self, checkpoint_key: str, source_signature: str) -> dict | None:
        path = self._checkpoint_path(checkpoint_key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("source_signature") != source_signature:
                path.unlink(missing_ok=True)
                return None
            return data
        except Exception:
            path.unlink(missing_ok=True)
            return None
    def _save_checkpoint(self, checkpoint_key: str, source_signature: str, partials: list, phase: str) -> None:
        path = self._checkpoint_path(checkpoint_key)
        data = {
            "version": 1,
            "source_signature": source_signature,
            "phase": phase,
            "partials": partials,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
    def _clear_checkpoint(self, checkpoint_key: str) -> None:
        self._checkpoint_path(checkpoint_key).unlink(missing_ok=True)
    @staticmethod
    def _is_insufficient_quota_error(exc: Exception) -> bool:
        raw = str(exc)
        return (
            "insufficient_user_quota" in raw
            or "预扣费额度失败" in raw
            or "insufficient quota" in raw.lower()
        )

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        raw = str(exc).lower()
        retryable_tokens = (
            "error code: 524",
            "bad_response_status_code",
            "timed out",
            "timeout",
            "rate limit",
            "error code: 429",
            "error code: 500",
            "error code: 502",
            "error code: 503",
            "error code: 504",
            "apiconnectionerror",
            "connection error",
            "service unavailable",
        )
        if any(token in raw for token in retryable_tokens):
            return True
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        return status in {408, 409, 429, 500, 502, 503, 504, 524}
    @staticmethod
    def _is_temperature_unsupported_error(exc: Exception) -> bool:
        """OpenAI o1/o3/gpt-5 系列等新模型不接受自定义 temperature，
        只允许默认值 1，传 0.7 会报 `'temperature' does not support 0.7 ...`。"""
        raw = str(exc).lower()
        return "temperature" in raw and (
            "does not support" in raw
            or "unsupported_value" in raw
            or "only the default" in raw
        )
    def _do_create(self, messages: list):
        """单次调用。如果模型拒绝自定义 temperature，就地去掉该参数再试一次
        （不消耗外层的重试次数预算），仍失败则把异常抛给外层重试逻辑。"""
        try:
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
            )
        except Exception as exc:
            if self._is_temperature_unsupported_error(exc):
                logger.info("[GPT] 模型不支持自定义 temperature，改用默认值: model=%s", self.model)
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                )
            raise
    def _chat_completion_create(self, messages: list, phase: str = "chat"):
        request_bytes, request_tokens, image_count = self._message_stats(messages)
        last_exc = None
        for attempt in range(self._max_retry_attempts):
            started = time.perf_counter()
            logger.info(
                "[GPT] 请求开始: phase=%s model=%s attempt=%d/%d bytes=%d tokens≈%d images=%d",
                phase,
                self.model,
                attempt + 1,
                self._max_retry_attempts,
                request_bytes,
                request_tokens,
                image_count,
            )
            try:
                response = self._do_create(messages)
                elapsed = time.perf_counter() - started
                output_chars = self._response_output_chars(response)
                logger.info(
                    "[GPT] 请求完成: phase=%s model=%s attempt=%d elapsed=%.2fs output_chars=%d",
                    phase,
                    self.model,
                    attempt + 1,
                    elapsed,
                    output_chars,
                )
                return response
            except Exception as exc:
                last_exc = exc
                elapsed = time.perf_counter() - started
                retryable = self._is_retryable_error(exc)
                logger.warning(
                    "[GPT] 请求失败: phase=%s model=%s attempt=%d/%d elapsed=%.2fs retryable=%s error=%s",
                    phase,
                    self.model,
                    attempt + 1,
                    self._max_retry_attempts,
                    elapsed,
                    retryable,
                    str(exc)[:300],
                )
                if attempt == self._max_retry_attempts - 1 or not retryable:
                    raise
                sleep_seconds = self._retry_base_backoff * (2 ** attempt)
                logger.info("[GPT] 重试等待: phase=%s seconds=%.2f", phase, sleep_seconds)
                time.sleep(sleep_seconds)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("chat completion failed without exception")
    def _merge_partials(self, partials: list, checkpoint_key: str | None, source_signature: str | None) -> str:
        def build_messages(texts, *_args, **_kwargs):
            return self._build_merge_messages(texts)
        merge_chunker = RequestChunker(
            lambda *_args, **_kwargs: [],
            self.max_request_bytes,
            self._estimate_messages_bytes,
            max_tokens=self.max_input_tokens,
            token_estimator=self._estimate_messages_tokens,
        )
        current_partials = list(partials)
        merge_round = 0
        while len(current_partials) > 1:
            merge_round += 1
            groups = merge_chunker.group_texts_by_budget(current_partials, build_messages)
            logger.info(
                "[GPT] 合并轮次开始: model=%s round=%d partials=%d groups=%d",
                self.model,
                merge_round,
                len(current_partials),
                len(groups),
            )
            new_partials = []
            for group_idx, group in enumerate(groups):
                messages = build_messages(group)
                phase = f"merge.round_{merge_round}.group_{group_idx + 1}/{len(groups)}"
                try:
                    response = self._chat_completion_create(messages, phase=phase)
                except Exception as exc:
                    if checkpoint_key and source_signature:
                        self._save_checkpoint(checkpoint_key, source_signature, current_partials, "merge")
                    raise
                new_partials.append(response.choices[0].message.content.strip())
                if checkpoint_key and source_signature:
                    remaining_partials = []
                    for remaining_group in groups[group_idx + 1:]:
                        remaining_partials.extend(remaining_group)
                    resumable_partials = new_partials + remaining_partials
                    self._save_checkpoint(checkpoint_key, source_signature, resumable_partials, "merge")

            current_partials = new_partials
            logger.info(
                "[GPT] 合并轮次完成: model=%s round=%d remaining_partials=%d",
                self.model,
                merge_round,
                len(current_partials),
            )
        return current_partials[0]
    def summarize(self, source: GPTSource) -> str:
        self.screenshot = source.screenshot
        self.link = source.link
        source.segment = self.ensure_segments_type(source.segment)
        checkpoint_key = source.checkpoint_key
        source_signature = self._build_source_signature(source) if checkpoint_key else None
        def message_builder(segments, image_urls, **kwargs):
            return self.create_messages(segments, video_img_urls=image_urls, **kwargs)
        chunker = RequestChunker(
            message_builder,
            self.max_request_bytes,
            self._estimate_messages_bytes,
            max_tokens=self.max_input_tokens,
            token_estimator=self._estimate_messages_tokens,
        )
        try:
            chunks = chunker.chunk(
                source.segment,
                source.video_img_urls or [],
                title=source.title,
                tags=source.tags,
                _format=source._format,
                style=source.style,
                extras=source.extras,
                visual_context=source.visual_context,
            )
        except ValueError:
            chunks = chunker.chunk(
                source.segment,
                [],
                title=source.title,
                tags=source.tags,
                _format=source._format,
                style=source.style,
                extras=source.extras,
                visual_context=source.visual_context,
            )
        logger.info(
            "[GPT] 总结分片准备完成: model=%s chunks=%d transcript_segments=%d images=%d",
            self.model,
            len(chunks),
            len(source.segment),
            len(source.video_img_urls or []),
        )
        partials = []
        if checkpoint_key and source_signature:
            checkpoint = self._load_checkpoint(checkpoint_key, source_signature)
            if checkpoint and isinstance(checkpoint.get("partials"), list):
                partials = checkpoint["partials"]
        if len(partials) > len(chunks):
            partials = []
        total_chunks = len(chunks)
        for chunk_idx, chunk in enumerate(chunks[len(partials):], start=len(partials) + 1):
            logger.info(
                "[GPT] 文本分片开始: model=%s chunk=%d/%d segments=%d images=%d text_chars=%d",
                self.model,
                chunk_idx,
                total_chunks,
                len(chunk.segments),
                len(chunk.image_urls or []),
                sum(len(getattr(segment, "text", "") or "") for segment in chunk.segments),
            )
            messages = self.create_messages(
                chunk.segments,
                title=source.title,
                tags=source.tags,
                video_img_urls=chunk.image_urls,
                _format=source._format,
                style=source.style,
                extras=source.extras,
                visual_context=source.visual_context,
            )
            try:
                response = self._chat_completion_create(messages, phase=f"summarize.chunk_{chunk_idx}/{total_chunks}")
            except Exception as exc:
                if checkpoint_key and source_signature:
                    self._save_checkpoint(checkpoint_key, source_signature, partials, "summarize")
                raise
            partials.append(response.choices[0].message.content.strip())
            logger.info(
                "[GPT] 文本分片完成: model=%s chunk=%d/%d output_chars=%d",
                self.model,
                chunk_idx,
                total_chunks,
                len(partials[-1]),
            )
            if checkpoint_key and source_signature:
                self._save_checkpoint(checkpoint_key, source_signature, partials, "summarize")
        if len(partials) == 1:
            if checkpoint_key:
                self._clear_checkpoint(checkpoint_key)
            return partials[0]
        logger.info("[GPT] 开始合并分片: model=%s partials=%d", self.model, len(partials))
        merged = self._merge_partials(partials, checkpoint_key, source_signature)
        logger.info("[GPT] 分片合并完成: model=%s output_chars=%d", self.model, len(merged))
        if checkpoint_key:
            self._clear_checkpoint(checkpoint_key)
        return merged
