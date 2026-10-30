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
