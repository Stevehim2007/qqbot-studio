"""Conservative natural-language sticker request parsing.

Only explicit @/private requests are passed here by main.py. The parser never
returns file paths; it resolves against the safe built-in emotion names and the
collected category names supplied by the caller.
"""

from __future__ import annotations

import re
from typing import Iterable

STICKER_REQUEST_RE = re.compile(
    r"^(?:(?:请|麻烦|可以|能不能|能|帮我|给我|你|我想|我要)\s*)*"
    r"(?:发|来|要|整|给)\s*"
    r"(?:一?个|一?张|几?个|一点|点)?\s*"
    r"(?P<target>.*?)\s*"
    r"(?:的)?\s*(?:表情包|表情|贴纸|图)"
    r"(?:吧|啊|呀|呗|嘛|哦|谢谢|行吗|可以吗)*"
    r"[!！。.？?~～\s]*$",
    re.IGNORECASE,
)

EMOTION_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("开心", "高兴", "快乐", "愉快"), "happy"),
    (("难过", "伤心", "悲伤", "不开心"), "sad"),
    (("生气", "愤怒", "火大"), "angry"),
    (("思考", "想想", "沉思"), "thinking"),
    (("喜欢", "爱你", "爱心"), "love"),
    (("困", "睡觉", "睡", "累了"), "sleepy"),
    (("惊讶", "震惊", "吃惊"), "surprised"),
    (("酷", "帅", "帅气"), "cool"),
)
SNARK_KEYWORDS = ("嘲讽", "阴阳", "无语", "鄙视", "挖苦", "阴阳怪气")
SNARK_EMOTIONS = ("angry", "cool")
GENERIC_TARGETS = {"表情", "表情包", "贴纸", "图"}


def _clean_target(raw: str) -> str:
    target = str(raw or "").strip()
    target = re.sub(r"^(?:一?个|一?张|几?个)\s*", "", target).strip()
    target = re.sub(r"(?:的|啊|呀|吧|呗|嘛|哦)\s*$", "", target).strip()
    return target


def resolve_sticker_target(
    target: str,
    *,
    available_emotions: Iterable[str],
    available_categories: Iterable[str],
) -> dict[str, str]:
    clean = _clean_target(target)
    emotion_names = {
        str(item).strip().casefold()
        for item in available_emotions
        if str(item).strip()
    }
    category_names = {
        str(item).strip().casefold(): str(item).strip()
        for item in available_categories
        if str(item).strip()
    }
    clean_folded = clean.casefold()
    if not clean:
        return {"kind": "generic", "target": ""}
    # Exact collected category first.
    if clean_folded in category_names:
        return {
            "kind": "category",
            "value": category_names[clean_folded],
            "target": clean,
        }
    # Exact built-in emotion tag.
    if clean_folded in emotion_names:
        return {"kind": "emotion", "value": clean_folded, "target": clean}
    for keywords, emotion in EMOTION_KEYWORDS:
        if any(keyword in clean for keyword in keywords):
            if emotion in emotion_names:
                return {
                    "kind": "emotion",
                    "value": emotion,
                    "target": clean,
                }
            return {"kind": "not_found", "target": clean}
    if any(keyword in clean for keyword in SNARK_KEYWORDS):
        for emotion in SNARK_EMOTIONS:
            if emotion in emotion_names:
                return {
                    "kind": "emotion",
                    "value": emotion,
                    "target": clean,
                }
        return {"kind": "not_found", "target": clean}
    if clean in GENERIC_TARGETS:
        return {"kind": "generic", "target": ""}
    return {"kind": "not_found", "target": clean}


def detect_sticker_request(
    text: str,
    *,
    available_emotions: Iterable[str],
    available_categories: Iterable[str],
) -> dict[str, str] | None:
    value = str(text or "").strip()
    if not value:
        return None
    match = STICKER_REQUEST_RE.fullmatch(value)
    if match is None:
        return None
    return resolve_sticker_target(
        match.group("target"),
        available_emotions=available_emotions,
        available_categories=available_categories,
    )
