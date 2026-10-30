"""Whitelisted tool-intent router.

AstrBot v4.29's tool/function-calling surface is not treated as a stable public
requirement here. Instead the model can only emit a private marker whose name
and arguments are parsed deterministically. No eval/exec/subprocess/tool object
is involved; main.py executes a fixed whitelist against its own services.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

TOOL_MARKER_RE = re.compile(
    r"\[\[\s*tool\s*:\s*([a-z_]+)([^\[\]\r\n]*)\]\]",
    re.IGNORECASE,
)
ARG_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s]+)"
)
SAFE_CATEGORY_RE = re.compile(r"^[0-9A-Za-z_\-\u4e00-\u9fff]{1,32}$")
TOOL_ARG_KEYS = {
    "search_images": {"keyword", "count"},
    "send_sticker": {"category", "emotion", "tag"},
    "collect_sticker": {"category"},
    "list_sticker_categories": set(),
}
ALLOWED_TOOLS = tuple(TOOL_ARG_KEYS)


@dataclass(slots=True)
class ToolIntent:
    name: str
    args: dict[str, str] = field(default_factory=dict)
    valid: bool = True
    error: str = ""


@dataclass(slots=True)
class ToolParseResult:
    clean_text: str
    intents: list[ToolIntent]
    had_marker: bool


def _decode_value(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            value = json.loads(raw)
            return str(value)
        except (TypeError, ValueError):
            return raw[1:-1]
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    return raw


def _parse_args(body: str) -> tuple[dict[str, str], str]:
    args: dict[str, str] = {}
    position = 0
    for match in ARG_RE.finditer(body):
        if body[position : match.start()].strip():
            return {}, "参数格式无效"
        key = match.group(1)
        if key in args:
            return {}, "参数重复"
        args[key] = _decode_value(match.group(2))
        position = match.end()
    if body[position:].strip():
        return {}, "参数格式无效"
    return args, ""


def _contains_unsafe_reference(value: str) -> bool:
    lowered = value.casefold()
    if any(
        token in lowered
        for token in (
            "..",
            "/",
            "\\",
            "://",
            "\x00",
            "\n",
            "\r",
            "file:",
            "http:",
            "https:",
            "command",
            "shell",
            "subprocess",
            "powershell",
        )
    ):
        return True
    return bool(re.search(r"[\x00-\x1f\x7f]", value))


def validate_intent(
    intent: ToolIntent,
    *,
    max_keyword_chars: int = 100,
    max_count: int = 5,
) -> ToolIntent:
    if intent.name not in TOOL_ARG_KEYS:
        intent.valid = False
        intent.error = "工具不在白名单"
        return intent
    allowed = TOOL_ARG_KEYS[intent.name]
    if set(intent.args) - allowed:
        intent.valid = False
        intent.error = "参数不在白名单"
        return intent
    for value in intent.args.values():
        if _contains_unsafe_reference(value):
            intent.valid = False
            intent.error = "禁止路径/URL/命令参数"
            return intent

    if intent.name == "search_images":
        keyword = intent.args.get("keyword", "").strip()
        if not keyword or len(keyword) > max_keyword_chars:
            intent.valid = False
            intent.error = "关键词长度无效"
            return intent
        count_raw = intent.args.get("count", "1")
        try:
            count = int(count_raw)
        except (TypeError, ValueError):
            intent.valid = False
            intent.error = "数量无效"
            return intent
        if count < 1 or count > max_count:
            intent.valid = False
            intent.error = "数量超出范围"
            return intent
    elif intent.name == "send_sticker":
        category = intent.args.get("category", "").strip()
        emotion = intent.args.get("emotion", "").strip()
        tag = intent.args.get("tag", "").strip()
        values = [value for value in (category, emotion, tag) if value]
        if len(values) != 1:
            intent.valid = False
            intent.error = "必须且只能指定 category/emotion/tag 之一"
            return intent
        value = values[0]
        if not SAFE_CATEGORY_RE.fullmatch(value):
            intent.valid = False
            intent.error = "表情分类/情绪/标签非法"
            return intent
    elif intent.name == "collect_sticker":
        category = intent.args.get("category", "").strip()
        if category and not SAFE_CATEGORY_RE.fullmatch(category):
            intent.valid = False
            intent.error = "收藏分类非法"
            return intent
    elif intent.name == "list_sticker_categories":
        if intent.args:
            intent.valid = False
            intent.error = "该工具不接受参数"
            return intent
    return intent


def parse_tool_markers(text: str) -> ToolParseResult:
    raw = str(text or "")
    matches = list(TOOL_MARKER_RE.finditer(raw))
    if not matches:
        return ToolParseResult(raw, [], False)
    clean = TOOL_MARKER_RE.sub("", raw)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    intents: list[ToolIntent] = []
    for match in matches:
        name = match.group(1).strip().lower()
        body = match.group(2).strip()
        args, error = _parse_args(body)
        intent = ToolIntent(name=name, args=args, valid=not error, error=error)
        intents.append(validate_intent(intent))
    return ToolParseResult(clean, intents, True)


def tool_instruction(enabled_tools: list[str]) -> str:
    lines = []
    if "search_images" in enabled_tools:
        lines.append(
            'search_images(keyword="关键词", count=1)'
        )
    if "send_sticker" in enabled_tools:
        lines.append(
            'send_sticker(category="分类") / emotion="happy" / tag="嘲讽"'
        )
    if "collect_sticker" in enabled_tools:
        lines.append('collect_sticker(category="分类")')
    if "list_sticker_categories" in enabled_tools:
        lines.append("list_sticker_categories()")
    if not lines:
        return ""
    return (
        "可选安全工具（仅以下白名单，禁止文件路径/URL/命令/终端/电脑控制）："
        + "；".join(lines)
        + "。需要时在回复末尾单独写一个 "
        + '[[tool:工具名 参数="值"]]'
        + "，最多一个；不需要不写，不要解释标记。"
    )
