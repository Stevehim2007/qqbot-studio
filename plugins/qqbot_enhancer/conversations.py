"""Safe helpers for AstrBot ConversationManager commands."""

from __future__ import annotations

import re
from typing import Any

INVALID_TITLE_RE = re.compile(r"[\x00-\x1f\x7f/\\]")


def normalize_title(raw: Any, *, max_chars: int = 32) -> str | None:
    title = str(raw or "").strip()
    title = re.sub(r"\s+", " ", title)
    if not title or len(title) > max(1, int(max_chars)):
        return None
    if INVALID_TITLE_RE.search(title):
        return None
    return title


def conversation_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("cid", "conversation_id", "id"):
            found = value.get(key)
            if found:
                return str(found)
        return ""
    for attr in ("cid", "conversation_id", "id"):
        found = getattr(value, attr, None)
        if found:
            return str(found)
    return ""


def conversation_title(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("title", "name"):
            found = value.get(key)
            if found:
                return str(found)
        return ""
    for attr in ("title", "name"):
        found = getattr(value, attr, None)
        if found:
            return str(found)
    return ""


def short_id(cid: str) -> str:
    safe = "".join(char for char in str(cid or "") if char.isalnum() or char in "-_")
    return safe[-8:] if safe else "?"


def find_by_title(conversations: list[Any], title: str) -> Any | None:
    wanted = str(title or "").strip().casefold()
    for conversation in conversations:
        if conversation_title(conversation).strip().casefold() == wanted:
            return conversation
    return None
