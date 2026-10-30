"""Small LLM-facing pure helpers.

The actual provider call stays behind the LLMService protocol/adapter in
main.py. These helpers only parse private markers and strict JSON judgments.
"""

from __future__ import annotations

import json
import re
from typing import Any

STICKER_AI_MARKER_RE = re.compile(
    r"\[\[\s*sticker\s*:\s*([^\[\]\r\n]{1,64})\s*\]\]",
    re.IGNORECASE,
)
STICKER_AI_TAG_RE = re.compile(r"^[0-9A-Za-z_\-\u4e00-\u9fff]{1,32}$")


def extract_sticker_ai_marker(
    text: str,
) -> tuple[str, str | None, bool]:
    raw = str(text or "")
    matches = list(STICKER_AI_MARKER_RE.finditer(raw))
    if not matches:
        return raw, None, False
    clean = STICKER_AI_MARKER_RE.sub("", raw)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    if len(matches) != 1:
        return clean, None, True
    return clean, matches[0].group(1).strip(), True


def parse_ai_sticker_judgement(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or "is_sticker" not in payload:
        return None
    value = payload.get("is_sticker")
    if isinstance(value, str):
        is_sticker = value.strip().lower() in {
            "true",
            "yes",
            "1",
            "是",
            "表情包",
            "sticker",
        }
    else:
        is_sticker = bool(value)
    try:
        confidence = float(payload.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    confidence = min(1.0, max(0.0, confidence))
    category = str(payload.get("category", "") or "").strip()
    return {
        "is_sticker": is_sticker,
        "confidence": confidence,
        "category": category,
    }


def sticker_ai_system_instruction(tags: list[str]) -> str:
    if not tags:
        return ""
    visible_tags = ", ".join(tags[:20])
    return (
        f"可选表情标签：{visible_tags}。"
        "仅在确实贴切时，于回复末尾原样附加一个"
        "[[sticker:标签]] 标记；不需要就别输出标记，不要输出多个，不要解释标记。"
    )
