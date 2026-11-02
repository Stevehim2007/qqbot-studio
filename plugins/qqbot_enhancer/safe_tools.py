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

try:  # Loaded as a package by AstrBot.
    from . import food as food_engine
    from . import web_fetch as web_fetch_engine
except ImportError:  # Loaded directly by a test harness.
    import food as food_engine
    import web_fetch as web_fetch_engine

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
    "fetch_webpage": {"url", "max_chars"},
    "submit_food_review": {
        "restaurant",
        "location",
        "taste",
        "value",
        "comment",
        "tags",
    },
    "search_food_restaurants": {
        "name",
        "tag",
        "location",
        "sort",
        "order",
        "limit",
    },
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
    if intent.name == "search_food_restaurants":
        for key, limit in (
            ("name", 50),
            ("tag", 120),
            ("location", 30),
            ("sort", 12),
            ("order", 5),
            ("limit", 4),
        ):
            value = str(intent.args.get(key, "") or "")
            if len(value) > limit:
                intent.valid = False
                intent.error = "字段过长"
                return intent
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                intent.valid = False
                intent.error = "字段含控制字符"
                return intent
            if (
                value
                and key in ("name", "tag", "location")
                and not food_engine.sanitize_food_submit_text(
                    value, max_chars=limit
                )
            ):
                intent.valid = False
                intent.error = "字段含链接/路径"
                return intent
        return intent
    if intent.name == "submit_food_review":
        # Free-text fields legitimately contain Chinese, spaces and punctuation,
        # so the generic URL/path rejection loop is skipped; dangerous values
        # (links/paths/control chars/oversize) are rejected per field instead.
        for key, limit in (
            ("restaurant", 200),
            ("location", 60),
            ("comment", 500),
            ("tags", 120),
            ("taste", 8),
            ("value", 8),
        ):
            value = str(intent.args.get(key, "") or "")
            if len(value) > limit:
                intent.valid = False
                intent.error = "字段过长"
                return intent
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                intent.valid = False
                intent.error = "字段含控制字符"
                return intent
            if (
                value
                and key not in ("taste", "value", "tags")
                and not food_engine.sanitize_food_submit_text(
                    value, max_chars=limit
                )
            ):
                intent.valid = False
                intent.error = "字段含链接/路径"
                return intent
        return intent
    if intent.name == "fetch_webpage":
        # The URL is the one argument allowed to contain "://" and "/"; it is
        # still restricted to public HTTP(S) targets by web_fetch.
        try:
            web_fetch_engine.validate_fetch_url(intent.args.get("url", ""))
        except web_fetch_engine.WebFetchError:
            intent.valid = False
            intent.error = "网页地址不安全"
            return intent
        raw_max_chars = intent.args.get("max_chars", "").strip()
        if raw_max_chars:
            try:
                max_chars = int(raw_max_chars)
            except (TypeError, ValueError):
                intent.valid = False
                intent.error = "max_chars 无效"
                return intent
            if (
                max_chars < 1
                or max_chars > web_fetch_engine.WEB_FETCH_MAX_CHARS_HARD_LIMIT
            ):
                intent.valid = False
                intent.error = "max_chars 超出范围"
                return intent
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
    if "fetch_webpage" in enabled_tools:
        lines.append(
            'fetch_webpage(url="https://food.dlsz.top", max_chars=3000)'
        )
    if "submit_food_review" in enabled_tools:
        lines.append(
            'submit_food_review(restaurant="店名", location="位置", '
            'taste="4.5", value="3.5", comment="评价", tags="火锅,夜宵")'
        )
    if "search_food_restaurants" in enabled_tools:
        lines.append(
            'search_food_restaurants(name="店名关键词", tag="火锅,烤肉", '
            'location="南关区", sort="total", order="desc", limit=5)'
        )
    if not lines:
        return ""
    if "fetch_webpage" in enabled_tools:
        preamble = (
            "可选安全工具（仅以下白名单，禁止文件路径/命令/终端/电脑控制；"
            "仅 fetch_webpage 允许公开网页 URL，且不登录、不带 Cookie）："
        )
    else:
        preamble = (
            "可选安全工具（仅以下白名单，禁止文件路径/URL/命令/终端/电脑控制）："
        )
    if "submit_food_review" in enabled_tools:
        lines.append(
            "submit_food_review 只在用户明确要求把餐厅/评价添加、记录或上传到 "
            "food.dlsz.top 时使用；必填店名/位置/味道分/性价比分/评价，"
            "缺哪个就留空并照常调用一次（插件会追问缺少项），"
            "location 可以直接写完整地址（如 中国/吉林省/长春市/南关区 或 中国长春市南关区），"
            "插件会自动拆分并创建缺失的上级/子级位置，用户不用手动拆；"
            "不要编造分数，不要传密码/QQ/日期"
        )
    if "search_food_restaurants" in enabled_tools:
        lines.append(
            "search_food_restaurants 只在用户想找/推荐餐厅时使用："
            "name/tag/location 至少给一个，tag 支持逗号分隔多个，"
            'sort 可选 total/taste/value/reviews/name/latest，order 可选 asc/desc，'
            "limit 1-5；不带 URL/命令参数，查询结果由插件排序并只读返回"
        )
    return (
        preamble
        + "；".join(lines)
        + "。需要时在回复末尾单独写一个 "
        + '[[tool:工具名 参数="值"]]'
        + "，最多一个；不需要不写，不要解释标记。"
    )
