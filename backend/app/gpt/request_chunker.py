from dataclasses import dataclass
from typing import Callable, List, Optional
import json
import math
import re


@dataclass
class ChunkPayload:
    segments: list
    image_urls: list


class RequestChunker:
    def __init__(
        self,
        message_builder: Callable,
        max_bytes: int,
        size_estimator: Optional[Callable] = None,
        max_tokens: Optional[int] = None,
        token_estimator: Optional[Callable] = None,
    ):
        self.message_builder = message_builder
        self.max_bytes = max_bytes
        self.size_estimator = size_estimator
        self.max_tokens = max_tokens
        self.token_estimator = token_estimator or self.estimate_tokens

    def estimate(self, messages) -> int:
        if self.size_estimator:
            return self.size_estimator(messages)
        return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

    @staticmethod
    def estimate_tokens(messages, image_token_cost: int = 1600) -> int:
        """保守估算请求 token，兼容没有供应商 tokenizer 的 OpenAI 兼容接口。"""
        def text_tokens(value: str) -> int:
            if not value:
                return 0
            cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", value))
            non_cjk = re.sub(r"[\u3400-\u9fff\uf900-\ufaff]", " ", value)
            words = re.findall(r"\S+", non_cjk)
            ascii_chars = sum(len(word) for word in words)
            return cjk + max(0, math.ceil(ascii_chars / 4)) + len(words)

        def walk(value) -> int:
            if isinstance(value, str):
                return text_tokens(value)
            if isinstance(value, list):
                return sum(walk(item) for item in value)
            if isinstance(value, dict):
                if value.get("type") == "image_url" or "image_url" in value:
                    return image_token_cost
                return sum(walk(item) for item in value.values())
            return 0

        return walk(messages)

    def _within_budget(self, messages) -> bool:
        if self.estimate(messages) > self.max_bytes:
            return False
        if self.max_tokens is not None and self.token_estimator(messages) > self.max_tokens:
            return False
        return True

    def _messages_size(self, segments, image_urls, **kwargs) -> int:
        messages = self.message_builder(segments, image_urls, **kwargs)
        return self.estimate(messages)

    def _get_text(self, segment) -> str:
        if isinstance(segment, dict):
            return segment.get("text", "")
        return getattr(segment, "text", "")

    def _make_segment(self, segment, text: str):
        if isinstance(segment, dict):
            new_seg = dict(segment)
            new_seg["text"] = text
            return new_seg
        if hasattr(segment, "__dict__"):
            data = dict(segment.__dict__)
            data["text"] = text
            return type(segment)(**data)
        return type(segment)(segment.start, segment.end, text)

    def _split_segment_to_fit(self, segment, **kwargs):
        text = self._get_text(segment)
        if not text:
            raise ValueError("empty segment cannot be split")
        lo, hi = 1, len(text)
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = self._make_segment(segment, text[:mid])
            messages = self.message_builder([candidate], [], **kwargs)
            if self._within_budget(messages):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            raise ValueError("single segment too large to fit request")
        head = self._make_segment(segment, text[:best])
        tail = self._make_segment(segment, text[best:])
        return head, tail

    def chunk(self, segments: list, image_urls: list, **kwargs) -> List[ChunkPayload]:
        segments = list(segments or [])
        image_urls = list(image_urls or [])
        if not segments and not image_urls:
            return []

        chunks: List[ChunkPayload] = []
        seg_idx = 0

        while seg_idx < len(segments):
            batch_segments = []
            while seg_idx < len(segments):
                candidate = batch_segments + [segments[seg_idx]]
                messages = self.message_builder(candidate, [], **kwargs)
                if self._within_budget(messages):
                    batch_segments = candidate
                    seg_idx += 1
                    continue
                if not batch_segments:
                    head, tail = self._split_segment_to_fit(segments[seg_idx], **kwargs)
                    segments[seg_idx] = head
                    segments.insert(seg_idx + 1, tail)
                    continue
                break

            if not batch_segments:
                raise ValueError("unable to fit any content into chunk")

            chunks.append(ChunkPayload(segments=batch_segments, image_urls=[]))

        if not image_urls:
            return chunks

        if not chunks:
            chunks = [ChunkPayload(segments=[], image_urls=[])]

        if not segments:
            for image in image_urls:
                appended = False
                for chunk in chunks[-1:]:
                    candidate_images = chunk.image_urls + [image]
                    messages = self.message_builder(chunk.segments, candidate_images, **kwargs)
                    if self._within_budget(messages):
                        chunk.image_urls = candidate_images
                        appended = True
                        break

                if appended:
                    continue

                if not self._within_budget(self.message_builder([], [image], **kwargs)):
                    raise ValueError("single image payload exceeds max_bytes")
                chunks.append(ChunkPayload(segments=[], image_urls=[image]))
            return chunks

        chunk_count = len(chunks)
        total_images = len(image_urls)
        for idx, image in enumerate(image_urls):
            preferred_idx = min(chunk_count - 1, (idx * chunk_count) // total_images)
            placed = False

            for chunk_idx in range(preferred_idx, len(chunks)):
                chunk = chunks[chunk_idx]
                candidate_images = chunk.image_urls + [image]
                messages = self.message_builder(chunk.segments, candidate_images, **kwargs)
                if self._within_budget(messages):
                    chunk.image_urls = candidate_images
                    placed = True
                    break

            if placed:
                continue

            if not self._within_budget(self.message_builder([], [image], **kwargs)):
                raise ValueError("single image payload exceeds max_bytes")
            chunks.append(ChunkPayload(segments=[], image_urls=[image]))

        return chunks

    def group_texts_by_budget(self, texts: List[str], build_messages: Callable, **kwargs) -> List[List[str]]:
        groups: List[List[str]] = []
        idx = 0
        while idx < len(texts):
            group: List[str] = []
            while idx < len(texts):
                candidate = group + [texts[idx]]
                try:
                    messages = build_messages(candidate, [], **kwargs)
                except TypeError:
                    messages = build_messages(candidate, **kwargs)
                if self._within_budget(messages):
                    group = candidate
                    idx += 1
                    continue
                if not group:
                    raise ValueError("single text block exceeds max_bytes")
                break
            groups.append(group)
        return groups
