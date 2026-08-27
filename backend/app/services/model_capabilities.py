"""Model capability inference used by video-understanding model selection.

Provider model-list APIs rarely expose a reliable vision flag.  We therefore
return a conservative, inspectable classification for known model families and
mark everything else as ``unknown`` instead of pretending it supports images.
"""
from __future__ import annotations

import re


_VISION_PATTERNS = (
    r"(?:^|[-_/])gpt-4o(?:$|[-_/])",
    r"(?:^|[-_/])gpt-4\.1(?:$|[-_/])",
    r"(?:^|[-_/])gpt-5(?:$|[-_/])",
    r"gpt-4(?:-turbo|-vision)",
    r"(?:^|[-_/])glm-(?:4|4\.1|4\.5|5)?(?:[-_]?5)?v(?:$|[-_/])",
    r"glm.*v",
    r"qwen(?:2?\.5|3)?[-_](?:vl|omni)|qvq",
    r"gemini",
    r"claude-(?:3|4)",
    r"doubao.*(?:vision|1\.6)",
)

_TEXT_ONLY_PATTERNS = (
    r"deepseek",
    r"(?:^|[-_/])gpt-3\.5",
    r"(?:^|[-_/])text-",
)


def infer_model_capabilities(model_name: str, provider_id: str | None = None) -> dict:
    """Return ``supports_text``, ``supports_vision`` and a confidence label.

    ``unknown`` is intentional: custom OpenAI-compatible gateways can expose
    vision models under arbitrary names, so callers should allow an explicit
    override rather than blocking them based on a heuristic.
    """
    name = str(model_name or "").strip().lower()
    provider = str(provider_id or "").strip().lower()
    haystack = f"{provider}/{name}"
    if any(re.search(pattern, haystack) for pattern in _VISION_PATTERNS):
        return {
            "model_role": "vision",
            "supports_text": True,
            "supports_vision": True,
            "confidence": "known",
        }
    if any(re.search(pattern, haystack) for pattern in _TEXT_ONLY_PATTERNS):
        return {
            "model_role": "text",
            "supports_text": True,
            "supports_vision": False,
            "confidence": "known",
        }
    return {
        "model_role": "unknown",
        "supports_text": True,
        "supports_vision": None,
        "confidence": "unknown",
    }


def annotate_model(model: dict, provider_id: str | None = None) -> dict:
    """Attach inferred capability fields without changing existing model keys."""
    result = dict(model)
    model_id = result.get("model_name") or result.get("id") or ""
    result["capabilities"] = infer_model_capabilities(model_id, provider_id or result.get("provider_id"))
    return result
