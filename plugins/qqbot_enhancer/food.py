"""Pure helpers for the fixed-host food.dlsz.top /food command."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

FOOD_BASE_URL = "https://food.dlsz.top"
FOOD_HOST = "food.dlsz.top"
FOOD_LOCATIONS_PATH = "/api/locations/search"
FOOD_RESTAURANTS_PATH = "/api/restaurants"
FOOD_ALLOWED_PATHS = {FOOD_LOCATIONS_PATH, FOOD_RESTAURANTS_PATH}
FOOD_LOCATION_SUFFIXES = (
    "区",
    "县",
    "市",
    "镇",
    "乡",
    "街道",
    "路",
    "街",
    "省",
    "州",
)
FOOD_MAX_RESULTS_HARD_LIMIT = 5


class FoodAPIError(ValueError):
    """Safe, non-path-bearing API error for /food fallback messages."""


def sanitize_food_query(raw: Any, *, max_chars: int = 50) -> str | None:
    value = str(raw or "").strip()
    if not value or len(value) > max(1, int(max_chars)):
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    lowered = value.casefold()
    if any(
        token in lowered
        for token in ("://", "http:", "https:", "www.", "file:", "javascript:")
    ):
        return None
    if any(char in value for char in ("/", "\\", "?", "#", "\x00")):
        return None
    if value in {".", ".."}:
        return None
    return value


def split_food_args(raw: Any) -> list[str]:
    return [part for part in re.split(r"\s+", str(raw or "").strip()) if part]


def likely_location_token(token: str) -> bool:
    value = str(token or "").strip()
    return any(value.endswith(suffix) for suffix in FOOD_LOCATION_SUFFIXES)


def build_food_url(path: str, params: dict[str, Any]) -> str:
    if path not in FOOD_ALLOWED_PATHS:
        raise FoodAPIError("unsupported food API path")
    encoded = urllib.parse.urlencode(
        {key: str(value) for key, value in params.items()}
    )
    return f"{FOOD_BASE_URL}{path}?{encoded}"


def validate_food_final_url(final_url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(final_url)
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == FOOD_HOST
        and parsed.path in FOOD_ALLOWED_PATHS
        and parsed.username is None
        and parsed.password is None
    )


def decode_food_payload(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except (TypeError, ValueError) as exc:
        raise FoodAPIError("food API returned non-JSON") from exc


def extract_food_items(payload: Any) -> list[dict[str, Any]]:
    candidates = payload
    if isinstance(payload, dict):
        candidates = None
        for key in ("data", "results", "items", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def select_food_location(
    locations: list[dict[str, Any]],
    query: str,
) -> dict[str, Any] | None:
    needle = str(query or "").strip().casefold()
    if not needle:
        return None
    for item in locations:
        name = str(item.get("name", "") or "").strip()
        if name.casefold() == needle:
            return item
    for item in locations:
        name = str(item.get("name", "") or "").strip().casefold()
        path = str(item.get("path", "") or "").strip().casefold()
        if needle in name or (name and name in needle) or needle in path:
            return item
    return None


def _safe_display(value: Any, *, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = "".join(
        char for char in text if ord(char) >= 32 and ord(char) != 127
    )
    return text[:limit]


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_food_results(
    items: list[dict[str, Any]],
    *,
    keyword: str,
    location_name: str = "",
    max_results: int = 5,
    footer: str = "更多：https://food.dlsz.top",
) -> str:
    limit = max(1, min(FOOD_MAX_RESULTS_HARD_LIMIT, int(max_results or 1)))
    shown = items[:limit]
    location_part = f"（地点：{_safe_display(location_name, limit=40)}）" if location_name else ""
    lines = [
        f"找到 {len(shown)} 家「{_safe_display(keyword, limit=50)}」相关餐厅{location_part}："
    ]
    for index, item in enumerate(shown, 1):
        name = _safe_display(item.get("name"), limit=100) or "未命名餐厅"
        location = _safe_display(item.get("location_name"), limit=60)
        raw_tags = item.get("tags", [])
        tags = (
            "、".join(
                tag
                for tag in (
                    _safe_display(raw_tag, limit=30)
                    for raw_tag in raw_tags
                )
                if tag
            )
            if isinstance(raw_tags, list)
            else ""
        )
        taste = _format_number(item.get("taste_avg"))
        total = _format_number(item.get("total_avg"))
        value = _format_number(item.get("value_avg"))
        try:
            review_count = max(0, int(item.get("review_count", 0) or 0))
        except (TypeError, ValueError):
            review_count = 0
        latest = _safe_display(item.get("latest_review_date"), limit=40) or "无"
        lines.append(f"{index}. {name}")
        if location:
            lines.append(f"   地点：{location}")
        if tags:
            lines.append(f"   标签：{tags}")
        lines.append(
            f"   评分：均分 {total} / 口味 {taste} / 性价比 {value}"
        )
        lines.append(f"   评价：{review_count} 条，最新 {latest}")
    lines.append(footer)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Natural-language review submission (fixed POST endpoint)
# ---------------------------------------------------------------------------
FOOD_SUBMIT_PATH = "/api/upload"
FOOD_SUBMIT_URL = FOOD_BASE_URL + FOOD_SUBMIT_PATH
FOOD_SCORE_MIN = 0.1
FOOD_SCORE_MAX = 5.0
FOOD_SUBMIT_ARG_KEYS = {
    "restaurant",
    "location",
    "taste",
    "value",
    "comment",
    "tags",
}
# (arg key, user-facing label) in the order a clarification should list them.
FOOD_SUBMIT_REQUIRED = (
    ("restaurant", "店名"),
    ("location", "位置"),
    ("taste", "味道分（0.1-5）"),
    ("value", "性价比分（0.1-5）"),
    ("comment", "评价内容"),
)
FOOD_SUBMIT_LABELS = dict(FOOD_SUBMIT_REQUIRED)


def food_submit_missing_labels(keys: list[str]) -> list[str]:
    return [
        FOOD_SUBMIT_LABELS.get(str(key), str(key))
        for key in keys
    ]
FOOD_ADD_VERBS = (
    "添加",
    "加入",
    "加到",
    "加进",
    "加一下",
    "帮我加",
    "记一下",
    "记录",
    "记到",
    "上传",
    "收录",
    "提交",
    "登记",
    "存档",
    "存一下",
    "上报",
    "写入",
)
FOOD_ADD_NOUNS = (
    "food",
    "dlsz",
    "评价",
    "点评",
    "评分",
    "餐厅",
    "饭店",
    "饭馆",
    "馆子",
    "这家店",
    "店铺",
    "美食",
)
FOOD_RELEVANCE_KEYWORDS = (
    "好吃",
    "难吃",
    "好吃到",
    "吃了",
    "吃过",
    "吃饭",
    "爱吃",
    "食物",
    "美食",
    "餐厅",
    "饭店",
    "饭馆",
    "馆子",
    "火锅",
    "烧烤",
    "烤肉",
    "串串",
    "麻辣烫",
    "面条",
    "拉面",
    "拌面",
    "牛肉面",
    "米线",
    "米粉",
    "米饭",
    "盖饭",
    "炒饭",
    "炒菜",
    "菜品",
    "饺子",
    "包子",
    "小笼包",
    "汉堡",
    "披萨",
    "寿司",
    "炸鸡",
    "薯条",
    "面包",
    "甜品",
    "蛋糕",
    "冰淇淋",
    "咖啡",
    "奶茶",
    "饮料",
    "海鲜",
    "牛肉",
    "猪肉",
    "鸡肉",
    "羊肉",
    "烤鸭",
    "小吃",
    "外卖",
    "食堂",
    "零食",
    "水果",
    "蔬菜",
    "沙拉",
    "粥",
    "口味",
    "味道",
    "汤底",
    "性价比",
    "探店",
    "菜单",
    "厨师",
    "异食癖",
    "麻辣",
    "香辣",
)
FOOD_TAG_SPLIT_RE = re.compile(r"[,，、;；|/]+")
_FULLWIDTH_NUMBER = str.maketrans("０１２３４５６７８９．", "0123456789.")


def _clean_food_text(raw: Any, *, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    text = "".join(
        char for char in text if ord(char) >= 32 and ord(char) != 127
    )
    return text[: max(1, int(max_chars))].strip()


def sanitize_food_submit_text(
    raw: Any,
    *,
    max_chars: int,
    forbid_url: bool = True,
) -> str:
    """Return printable single-line text, or "" if it looks like a URL/path."""

    text = _clean_food_text(raw, max_chars=max_chars)
    if not text:
        return ""
    lowered = text.casefold()
    if any(
        token in lowered
        for token in ("://", "http:", "https:", "www.", "file:", "javascript:")
    ):
        return ""
    if forbid_url and any(char in text for char in ("\\", "\x00")):
        return ""
    return text


def parse_food_score(raw: Any) -> float | None:
    """Parse 0.1-5.0 scores; returns None for anything else."""

    text = str(raw or "").strip().translate(_FULLWIDTH_NUMBER)
    text = text.replace("分", "").replace("／", "/").strip()
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if number < FOOD_SCORE_MIN or number > FOOD_SCORE_MAX:
        return None
    return round(number, 2)


def split_food_tags(
    raw: Any,
    *,
    max_count: int = 5,
    max_chars: int = 20,
) -> list[str]:
    if isinstance(raw, (list, tuple)):
        parts = [str(item) for item in raw]
    else:
        parts = FOOD_TAG_SPLIT_RE.split(str(raw or ""))
    tags: list[str] = []
    for part in parts:
        tag = sanitize_food_submit_text(part, max_chars=max_chars)
        if not tag or tag in tags:
            continue
        tags.append(tag)
        if len(tags) >= max(1, int(max_count)):
            break
    return tags


def validate_food_submission(
    args: dict[str, Any],
    *,
    name_max: int = 100,
    location_max: int = 60,
    comment_max: int = 500,
    tag_max: int = 20,
    tag_count: int = 5,
) -> dict[str, Any]:
    """Normalize marker args into (fields, missing labels, invalid labels)."""

    raw = args if isinstance(args, dict) else {}
    fields: dict[str, Any] = {}
    missing: list[str] = []
    invalid: list[str] = []

    restaurant = sanitize_food_submit_text(
        raw.get("restaurant"), max_chars=name_max
    )
    if restaurant:
        fields["restaurant"] = restaurant

    location = sanitize_food_submit_text(
        raw.get("location"), max_chars=location_max
    )
    if location:
        fields["location"] = location

    comment = sanitize_food_submit_text(
        raw.get("comment"), max_chars=comment_max, forbid_url=False
    )
    if comment:
        fields["comment"] = comment

    for key in ("taste", "value"):
        raw_score = str(raw.get(key, "") or "").strip()
        score = parse_food_score(raw_score)
        if score is None:
            if raw_score:
                invalid.append(key)
            continue
        fields[key] = score

    tags = split_food_tags(
        raw.get("tags"), max_count=tag_count, max_chars=tag_max
    )
    if tags:
        fields["tags"] = tags

    for key, _label in FOOD_SUBMIT_REQUIRED:
        if key not in fields:
            missing.append(key)
    return {"fields": fields, "missing": missing, "invalid": invalid}



FOOD_SUBMIT_CONFIRM_RE = re.compile(
    r"^(?:好的?|要|可以|行|嗯+|确认|提交|添加|加上|记吧|就这样|没问题)"
    r"[吧呗呀啊嘛喵!！。.~～\s]{0,3}$"
)


def is_food_submit_confirmation(text: str) -> bool:
    """True for a bare confirmation that answers our "add it?" question."""

    return bool(FOOD_SUBMIT_CONFIRM_RE.match(str(text or "").strip()))


def detect_food_add_intent(text: str) -> bool:
    """True only for explicit "add/record/upload to food" style requests."""

    lowered = str(text or "").casefold()
    if not lowered.strip():
        return False
    return any(verb in lowered for verb in FOOD_ADD_VERBS) and any(
        noun in lowered for noun in FOOD_ADD_NOUNS
    )


def detect_food_relevance(text: str) -> bool:
    lowered = str(text or "").casefold()
    return any(keyword in lowered for keyword in FOOD_RELEVANCE_KEYWORDS)


def validate_food_submit_url(url: str) -> bool:
    """Only the one fixed https://food.dlsz.top/api/upload endpoint is valid."""

    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == FOOD_HOST
        and parsed.port in (None, 443)
        and parsed.path == FOOD_SUBMIT_PATH
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def build_food_submit_payload(
    fields: dict[str, Any],
    *,
    username: str,
    password: str,
    location_id: Any = None,
    new_location_name: str = "",
) -> dict[str, Any]:
    """Build the documented POST body. review_date is left to the site (today)."""

    payload: dict[str, Any] = {
        "name": str(fields.get("restaurant", "")),
        "comment": str(fields.get("comment", "")),
        "taste_score": float(fields.get("taste", 0.0)),
        "value_score": float(fields.get("value", 0.0)),
    }
    tags = fields.get("tags")
    if isinstance(tags, list) and tags:
        payload["tags"] = [str(tag) for tag in tags]
    if username:
        payload["username"] = username
    # Injected from the sender QQ id; never model-visible or logged.
    if password:
        payload["password"] = password
    if location_id is not None:
        try:
            payload["location_id"] = int(location_id)
        except (TypeError, ValueError):
            return {}
    elif new_location_name:
        payload["new_location_name"] = str(new_location_name)
    else:
        return {}
    return payload


def _food_submit_error_kind(status: int, code: str, error: str) -> str:
    """Stable error kind; prefers v1.1 ``code`` and falls back to v1 text."""

    lowered = error.casefold()
    if code == "DUPLICATE_USERNAME" or (
        "已存在" in error and ("用户" in error or "username" in lowered)
    ):
        return "duplicate_username"
    if code == "LOCATION_EXISTS" or ("位置" in error and "已存在" in error):
        return "location_exists"
    if code == "RESTAURANT_NOT_FOUND" or status == 404 or "餐厅不存在" in error:
        return "restaurant_not_found"
    if code.startswith("MISSING_") or "不能为空" in error:
        return "missing_field"
    if code.startswith("INVALID_") or "格式不正确" in error or "之间" in error:
        return "invalid_score"
    if 300 <= status < 400:
        return "redirect"
    if status >= 500:
        return "server"
    return "rejected"


def parse_food_submit_response(status: Any, payload: Any) -> dict[str, Any]:
    """Interpret the upload response; success requires 200 + success:true."""

    body = payload if isinstance(payload, dict) else {}
    try:
        status_code = int(status)
    except (TypeError, ValueError):
        status_code = 0
    if status_code == 200 and body.get("success") is True:
        tags = body.get("tags")
        return {
            "ok": True,
            "restaurant_id": body.get("restaurant_id"),
            "restaurant_name": _clean_food_text(
                body.get("restaurant_name"), max_chars=100
            ),
            "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
        }
    error = _clean_food_text(body.get("error"), max_chars=200)
    code = str(body.get("code", "") or "").strip().upper()
    return {
        "ok": False,
        "status": status_code,
        "code": code,
        "kind": _food_submit_error_kind(status_code, code, error),
        "error": error,
    }


def food_submit_error_reason(result: dict[str, Any]) -> str:
    """Short safe reason for generic templates; never a URL or traceback."""

    error = str(result.get("error", "") or "").strip()
    if error:
        return error[:80]
    status = result.get("status", 0)
    return f"HTTP {status}" if status else "站点未返回成功"
