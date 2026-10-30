"""Conservative parsing for explicit sticker reclassification requests."""

from __future__ import annotations

import json
import re
from typing import Any

try:  # Loaded as a package by AstrBot.
    from .sticker_intent import (
        EMOTION_KEYWORDS,
        SNARK_EMOTIONS,
        SNARK_KEYWORDS,
    )
except ImportError:  # Loaded directly by a test harness.
    from sticker_intent import (
        EMOTION_KEYWORDS,
        SNARK_EMOTIONS,
        SNARK_KEYWORDS,
    )

SAFE_CATEGORY_RE = re.compile(r"^[0-9A-Za-z_\-\u4e00-\u9fff]{1,32}$")
KNOWN_EMOTIONS = {emotion for _keywords, emotion in EMOTION_KEYWORDS}
_SUBJECT = r"(?:刚刚|刚才|上一个|上一张|上一条|这个|那个)"
_NOUN = r"(?:表情包|表情|贴纸)"
_VERB = r"(?:是|属于|分类为|改成|改到|移动到|移到|放到|标为|归为|算作)"
_DIRECT_RE = re.compile(
    rf"^\s*(?P<subject>{_SUBJECT})?\s*(?:的)?\s*"
    rf"(?:那个|这个)?\s*{_NOUN}\s*{_VERB}\s*"
    rf"(?P<target>.+?)\s*(?:的?意思|分类)?\s*[。.!！?？]*$"
)
_BA_RE = re.compile(
    rf"^\s*把\s*(?P<subject>{_SUBJECT})?\s*(?:的)?\s*"
    rf"(?:那个|这个)?\s*{_NOUN}\s*"
    rf"(?:改成|改到|移动到|移到|放到|分类为|标为|归为)\s*"
    rf"(?P<target>.+?)\s*(?:分类)?\s*[。.!！?？]*$"
)
_PRONOUN_RE = re.compile(
    rf"^\s*(?P<subject>{_SUBJECT})\s*(?:的)?\s*"
    rf"(?:那个|这个)\s*(?:{_NOUN})?\s*{_VERB}\s*"
    rf"(?P<target>.+?)\s*(?:的?意思|分类)?\s*[。.!！?？]*$"
)
_ADD_RE = re.compile(
    rf"^\s*(?:把\s*)?(?:{_SUBJECT})?\s*(?:的)?\s*"
    rf"(?:那个|这个)?\s*{_NOUN}\s*"
    rf"(?:加入|添加|加上|加进|放到|同时放到|也放到|再加到)\s*"
    rf"(?:分类)?\s*(?P<target>.+?)\s*(?:分类)?\s*[。.!！?？]*$"
)
_REMOVE_RE = re.compile(
    rf"^\s*(?:把\s*)?(?:{_SUBJECT})?\s*(?:的)?\s*"
    rf"(?:那个|这个)?\s*{_NOUN}\s*"
    rf"(?:移出|移除|去掉|从分类中移出|不要)\s*"
    rf"(?:分类)?\s*(?P<target>.+?)\s*(?:分类)?\s*[。.!！?？]*$"
)
_ONLY_RE = re.compile(
    rf"^\s*(?:把\s*)?(?:{_SUBJECT})?\s*(?:的)?\s*"
    rf"(?:那个|这个)?\s*{_NOUN}\s*"
    rf"(?:只放到|只保留|只归到|改为只放到)\s*"
    rf"(?:分类)?\s*(?P<target>.+?)\s*(?:分类)?\s*[。.!！?？]*$"
)


def _clean_target(raw: str) -> str:
    target = str(raw or "").strip()
    target = re.sub(r"(?:的)?意思$", "", target).strip()
    target = re.sub(r"(?:分类|类)$", "", target).strip()
    target = re.sub(r"(?:的|吧|啊|呀|呢|哦)$", "", target).strip()
    return target.strip("，。！？!?,.、:：;；\"'“”‘’ ")


_INCOMPLETE_RE = re.compile(
    rf"^\s*(?:把\s*)?(?:{_SUBJECT})?\s*(?:的)?\s*"
    rf"(?:那个|这个)?\s*{_NOUN}\s*{_VERB}\s*[。.!！?？]*$"
)


def looks_like_incomplete_reclassify_request(text: str) -> bool:
    return _INCOMPLETE_RE.fullmatch(str(text or "").strip()) is not None


def detect_reclassify_request(text: str) -> dict[str, str] | None:
    value = str(text or "").strip()
    if not value:
        return None
    for pattern, action in (
        (_ADD_RE, "add"),
        (_REMOVE_RE, "remove"),
        (_ONLY_RE, "set"),
        (_BA_RE, "set"),
        (_DIRECT_RE, "set"),
        (_PRONOUN_RE, "set"),
    ):
        match = pattern.fullmatch(value)
        if match is None:
            continue
        target = _clean_target(match.group("target"))
        if target:
            return {"target": target, "action": action}
    return None


def classify_target_local(target: str) -> str | None:
    clean = _clean_target(target)
    if not clean:
        return None
    folded = clean.casefold()
    if folded in KNOWN_EMOTIONS:
        return folded
    for keywords, emotion in EMOTION_KEYWORDS:
        if any(keyword in clean for keyword in keywords):
            return emotion
    if any(keyword in clean for keyword in SNARK_KEYWORDS):
        return SNARK_EMOTIONS[0]
    if SAFE_CATEGORY_RE.fullmatch(clean):
        return clean
    return None


def build_local_semantics(target: str) -> dict[str, Any] | None:
    clean = _clean_target(target)
    if not clean:
        return None
    parts = [
        part.strip()
        for part in re.split(r"[/、,，和及或\s]+", clean)
        if part.strip()
    ]
    parts = [
        re.sub(r"(?:时|场景|时候)?用的?$", "", part).strip()
        for part in parts
    ]
    parts = [part for part in parts if part]
    if not parts:
        return None

    tags: list[str] = []
    category = ""
    meaning_parts: list[str] = []
    tone = ""
    has_semantic_signal = False
    for part in parts:
        mapped = None
        for keywords, emotion in EMOTION_KEYWORDS:
            if any(keyword in part for keyword in keywords):
                mapped = emotion
                meaning_parts.append(part)
                break
        if any(keyword in part for keyword in SNARK_KEYWORDS):
            mapped = "snark"
            meaning_parts.append(part)
        if mapped:
            has_semantic_signal = True
            if not category:
                category = part
            for tag in (part, mapped if mapped != "snark" else "angry"):
                if tag and tag not in tags:
                    tags.append(tag)
        else:
            if SAFE_CATEGORY_RE.fullmatch(part):
                if not category:
                    category = part
                if part not in tags:
                    tags.append(part)
    if any(keyword in clean for keyword in SNARK_KEYWORDS):
        has_semantic_signal = True
        tone = "sarcastic"
        for tag in ("嘲讽", "阴阳怪气", "无语"):
            if tag not in tags:
                tags.append(tag)
        meaning = "讽刺、无语或轻微挖苦"
    elif meaning_parts:
        tone = "expressive"
        meaning = "、".join(dict.fromkeys(meaning_parts))
    else:
        meaning = ""
    category_list = [category] if category else []
    if not has_semantic_signal:
        category_parts = [
            part.strip()
            for part in re.split(r"[/、,，;；|]+", clean)
            if part.strip()
        ]
        if len(category_parts) > 1 and all(
            SAFE_CATEGORY_RE.fullmatch(part) for part in category_parts
        ):
            category = category_parts[0]
            category_list = list(dict.fromkeys(category_parts))
            tags = list(dict.fromkeys(category_parts))
        elif SAFE_CATEGORY_RE.fullmatch(clean):
            category = clean
            category_list = [clean]
            tags = [clean]
        else:
            return None
    if not category:
        if len(parts) == 1 and SAFE_CATEGORY_RE.fullmatch(parts[0]):
            category = parts[0]
        else:
            return None
    if not tags:
        tags.append(category)
    return {
        "category": category,
        "categories": category_list or [category],
        "tags": tags,
        "meaning": meaning,
        "tone": tone,
        "method": "local",
    }


def parse_reclassify_judgement(text: str) -> dict[str, Any] | None:
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
    if not isinstance(payload, dict):
        return None
    category = str(payload.get("category", "") or "").strip()
    if not category:
        return None
    raw_tags = payload.get("tags", [])
    tags = (
        [str(tag).strip() for tag in raw_tags if str(tag).strip()]
        if isinstance(raw_tags, list)
        else []
    )
    meaning = str(payload.get("meaning", "") or "").strip()
    tone = str(payload.get("tone", "") or "").strip()
    try:
        confidence = float(payload.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    confidence = min(1.0, max(0.0, confidence))
    return {
        "category": category,
        "tags": tags,
        "meaning": meaning,
        "tone": tone,
        "confidence": confidence,
    }
