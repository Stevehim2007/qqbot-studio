"""Pure image-search relevance helpers.

The network/provider adapter stays in main.py; these functions are independent
of AstrBot and can be tested with fixed HTML/JSON fixtures.
"""

from __future__ import annotations

import html as html_lib
import re
import unicodedata
from typing import Any

IMAGE_SEARCH_QUERY_ALIASES = {
    "千早爱音": ("anon chihaya", "chihaya anon"),
    "初音未来": ("hatsune miku", "miku hatsune"),
    "猫": ("cat", "neko"),
    "猫猫": ("cat", "neko"),
    "大树": ("tree", "big tree"),
}
RELEVANCE_VARIANT_TRANSLATION = str.maketrans(
    {
        "愛": "爱",
        "繪": "绘",
        "優": "优",
        "櫻": "樱",
        "涼": "凉",
        "麗": "丽",
        "亞": "亚",
        "郞": "郎",
        "郷": "乡",
        "鬥": "斗",
        "說": "说",
        "讀": "读",
        "書": "书",
        "寫": "写",
        "學": "学",
        "會": "会",
        "樂": "乐",
        "園": "园",
        "葉": "叶",
        "華": "华",
    }
)


def normalize_relevance_text(value: Any) -> str:
    text = html_lib.unescape(str(value or ""))
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.translate(RELEVANCE_VARIANT_TRANSLATION)
    text = re.sub(
        r"[\ue000-\uf8ff\u200b-\u200f\u202a-\u202e]+",
        " ",
        text,
    )
    text = re.sub(
        r"[^0-9a-z\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def relevance_query_variants(keyword: str) -> list[str]:
    query = normalize_relevance_text(keyword)
    if not query:
        return []
    variants = [query]
    query_compact = query.replace(" ", "")
    for canonical, aliases in IMAGE_SEARCH_QUERY_ALIASES.items():
        canonical_norm = normalize_relevance_text(canonical)
        alias_norms = [normalize_relevance_text(alias) for alias in aliases]
        group = [canonical_norm, *alias_norms]
        group_compacts = {
            variant.replace(" ", "") for variant in group if variant
        }
        if query_compact in group_compacts:
            for variant in group:
                if variant and variant not in variants:
                    variants.append(variant)
    return variants


def relevance_score_single(
    query: str,
    title_norm: str,
    desc_norm: str,
) -> float:
    if not query or (not title_norm and not desc_norm):
        return 0.0
    query_compact = query.replace(" ", "")
    title_compact = title_norm.replace(" ", "")
    desc_compact = desc_norm.replace(" ", "")
    title_tokens = re.findall(r"[a-z0-9]+", title_norm)
    desc_tokens = re.findall(r"[a-z0-9]+", desc_norm)
    query_ascii_tokens = re.findall(r"[a-z0-9]+", query)

    # ASCII queries match whole words, avoiding e.g. cat -> catalog.
    if query_ascii_tokens and not re.search(
        r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]",
        query,
    ):
        if all(token in title_tokens for token in query_ascii_tokens):
            return 1.0 if query_compact in title_compact else 0.9
        if all(token in desc_tokens for token in query_ascii_tokens):
            return 0.9 if query_compact in desc_compact else 0.8
        combined_tokens = set(title_tokens) | set(desc_tokens)
        if all(token in combined_tokens for token in query_ascii_tokens):
            return 0.8
        return 0.0

    if query_compact and query_compact in title_compact:
        return 1.0
    if query_compact and query_compact in desc_compact:
        return 0.9

    cjk_query = "".join(
        ch
        for ch in query_compact
        if "\u3400" <= ch <= "\u4dbf" or "\u4e00" <= ch <= "\u9fff"
    )
    if len(cjk_query) >= 4:
        bigrams = {
            cjk_query[index : index + 2]
            for index in range(len(cjk_query) - 1)
        }
        combined_compact = title_compact + desc_compact
        matched = sum(1 for bigram in bigrams if bigram in combined_compact)
        ratio = matched / max(1, len(bigrams))
        if ratio >= 0.8:
            return 0.75
        if ratio >= 0.6:
            return 0.6
    return 0.0


def relevance_score(keyword: str, title: str, description: str) -> float:
    title_norm = normalize_relevance_text(title)
    desc_norm = normalize_relevance_text(description)
    if not title_norm and not desc_norm:
        return 0.0
    variants = relevance_query_variants(keyword)
    if not variants:
        return 0.0
    return max(
        relevance_score_single(variant, title_norm, desc_norm)
        for variant in variants
    )


def filter_candidates(
    keyword: str,
    candidates: list[dict[str, Any]],
    *,
    min_score: float,
) -> list[dict[str, Any]]:
    relevant: list[dict[str, Any]] = []
    for candidate in candidates:
        score = relevance_score(
            keyword,
            str(candidate.get("title", "") or ""),
            str(candidate.get("description", "") or ""),
        )
        candidate["relevance_score"] = score
        if score >= min_score:
            relevant.append(candidate)
    relevant.sort(
        key=lambda item: float(item.get("relevance_score", 0.0)),
        reverse=True,
    )
    return relevant
