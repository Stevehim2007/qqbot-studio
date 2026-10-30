"""AstrBot QQ enhancer plugin.

The plugin deliberately uses only public AstrBot v4.29 plugin APIs:

* ``Star`` auto-registration (the legacy ``@register`` decorator is deprecated);
* ``filter.event_message_type`` / ``filter.platform_adapter_type``;
* ``AstrMessageEvent.get_messages()``, ``event.stop_event()`` and
  ``event.should_call_llm()``;
* ``Context.llm_generate()`` for a direct provider call;
* ``Comp.Image.fromFileSystem()`` + ``event.chain_result()`` for local stickers.

No third-party dependency and no API key is stored in this plugin.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import ipaddress
import json
import math
import os
import random
import re
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

PLUGIN_DIR = Path(__file__).resolve().parent
STICKER_DIR = PLUGIN_DIR / "stickers"
STICKER_RULES_PATH = STICKER_DIR / "sticker_rules.json"
STICKER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SONG_DIR = PLUGIN_DIR / "songs"
SONG_SUFFIXES = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
PLUGIN_DISPLAY_NAME = "QQ Bot 增强回复"
PLUGIN_FALLBACK_VERSION = "1.5.2"
COLLECTED_STICKER_DIR_NAME = "collected_stickers"
COLLECTED_INDEX_FILENAME = "index.json"
DEFAULT_COLLECTED_CATEGORY = "未分类"
COLLECTED_CATEGORY_MAX_CHARS = 32
PENDING_USER_MEDIA_MAX_COUNT = 3
PENDING_USER_MEDIA_MAX_SESSIONS = 128
COLLECTED_CATEGORY_RE = re.compile(r"^[0-9A-Za-z_\-\u4e00-\u9fff]{1,32}$")
COLLECTED_FILENAME_RE = re.compile(
    r"^[a-f0-9]{64}\.(?:jpg|png|gif|webp|bmp|avif)$"
)
COLLECT_NATURAL_LANGUAGE_RE = re.compile(
    r"^(?:帮我|给我|请(?:你)?|麻烦(?:你)?|你|能|可以)?\s*"
    r"(?:把)?\s*(?:这个|这张|这条|这|它)?\s*"
    r"(?:表情|图片|贴纸)?\s*"
    r"(?:收藏|保存)(?:一下|起来|了)?\s*"
    r"(?:(?:到|为|分类[:：]?)\s*)?"
    r"(?P<category>[0-9A-Za-z_\-\u4e00-\u9fff]{1,32})?\s*$"
)
COLLECT_NATURAL_LANGUAGE_REVERSED_RE = re.compile(
    r"^(?:帮我|给我|请(?:你)?|麻烦(?:你)?|你|能|可以)?\s*"
    r"(?:收藏|保存)\s*"
    r"(?P<category>[0-9A-Za-z_\-\u4e00-\u9fff]{1,32})\s*"
    r"(?:表情|图片|贴纸)(?:吧|了)?\s*$"
)
STICKER_AI_MARKER_RE = re.compile(
    r"\[\[\s*sticker\s*:\s*([^\[\]\r\n]{1,64})\s*\]\]",
    re.IGNORECASE,
)
STICKER_AI_TAG_RE = re.compile(r"^[0-9A-Za-z_\-\u4e00-\u9fff]{1,32}$")

BING_IMAGES_ENDPOINT = "https://www.bing.com/images/search"
BING_IMAGES_REFERER = "https://www.bing.com/images/search"
BAIDU_IMAGES_ENDPOINT = "https://image.baidu.com/search/acjson"
BAIDU_IMAGES_REFERER = "https://image.baidu.com/"
IMAGE_PATH_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".avif",
)
IMAGE_SEARCH_MAX_CANDIDATES = 200
IMAGE_SEARCH_MAX_CANDIDATE_URLS = 15
IMAGE_SEARCH_MAX_RESULTS_PER_COMMAND = 5
IMAGE_SEARCH_MAX_REDIRECTS = 3
IMAGE_SEARCH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
IMAGE_SEARCH_NL_RE = re.compile(
    r"(?P<prefix>(?:帮(?:我|忙)|给(?:我|一下)|请(?:你)?|麻烦(?:你)?|你|去|来|再|能|可以|一下|\s)*)"
    r"(?P<action>搜索|搜|查找|找|发|来|整|搞|要)"
    r"\s*(?P<quantity>(?:\d+|[一二两三四五六七八九十几]+|一些))?\s*"
    r"(?:张|幅|个)?\s*"
    r"(?P<keyword>.{1,40}?)\s*(?:的)?\s*"
    r"(?P<noun>图片|照片|壁纸|头像|插画|美图|图集|图)"
)
IMAGE_SEARCH_NL_NEGATION_RE = re.compile(
    r"(?:^|[，。！？!?\s])(?:我)?(?:"
    r"找不到|没找到|没有找到|搜不到|没搜到|没有搜到|无法找到|"
    r"别找|不用找|不想找|不要找|不需要找)"
)
IMAGE_SEARCH_NL_REFUSAL_RE = re.compile(
    r"(?:不要|不用|别|不想|不需要|无需|没必要)"
    r".{0,8}(?:找|搜|发|来|整|搞|给|要)"
    r".{0,12}(?:图片|照片|壁纸|头像|插画|美图|图集|图)"
)


class ImageSearchError(RuntimeError):
    """Raised when a safe, bounded image-search request cannot be completed."""


class _LimitedSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Limit redirect chains and reject non-HTTP(S) redirect targets."""

    max_repeats = 2
    max_redirections = IMAGE_SEARCH_MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_request = super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )
        if new_request is not None:
            scheme = urllib.parse.urlsplit(new_request.full_url).scheme.lower()
            if scheme not in {"http", "https"}:
                raise ImageSearchError("网络重定向到非 HTTP(S) 地址")
        return new_request


DEFAULTS: dict[str, Any] = {
    "memory_mode": "direct",
    "image_search_enabled": True,
    "image_search_natural_language_enabled": True,
    "image_search_timeout_seconds": 12,
    "image_search_result_count": 1,
    "image_search_safe_search": "strict",
    "image_search_group_cooldown_seconds": 15,
    "image_search_user_cooldown_seconds": 10,
    "image_search_max_response_bytes": 2_000_000,
    "image_search_max_keyword_chars": 100,
    "image_search_second_source_enabled": True,
    "image_search_download_timeout_seconds": 20,
    "image_search_download_max_bytes": 8_000_000,
    "image_search_download_attempts": 8,
    "image_search_success_prefix": "为你找到这些图片：",
    "image_search_usage_reply": "用法：/找图 关键词（也可以 /搜图 关键词）",
    "image_search_disabled_reply": "图片搜索功能当前未启用。",
    "image_search_empty_reply": "没有找到合适的图片，换个关键词试试吧。",
    "image_search_error_reply": "搜索图片失败了，请稍后再试。",
    "image_search_timeout_reply": "搜索图片超时了，请稍后再试。",
    "image_search_cooldown_reply": "图片搜索冷却中，请约 {seconds} 秒后再试。",
    "song_enabled": True,
    "song_group_cooldown_seconds": 15,
    "song_user_cooldown_seconds": 10,
    "song_max_file_bytes": 20_000_000,
    "song_success_prefix": "🎵 本地歌曲：{name}",
    "song_disabled_reply": "本地点歌功能当前未启用。",
    "song_empty_reply": (
        "本地点歌目录还没有音频文件。请把授权的 mp3/wav 放进 "
        "plugins/qqbot_enhancer/songs/ 后再试。"
    ),
    "song_not_found_reply": "没有找到匹配的本地歌曲，换个关键词试试吧。",
    "song_too_large_reply": "本地音频超过大小限制，管理员可调整 song_max_file_bytes。",
    "song_error_reply": "本地音频构造失败，请检查文件格式或让管理员查看日志。",
    "song_cooldown_reply": "点歌冷却中，请约 {seconds} 秒后再试。",
    "option_include_other_plugins": True,
    "option_max_plugins": 20,
    "option_max_chars": 1800,
    "user_sticker_reaction_enabled": True,
    "user_sticker_reaction_probability": 0.85,
    "user_sticker_reaction_replies": [
        "收到这张表情啦～",
        "这个表情我收下啦！",
        "好可爱，回你一张～",
        "表情包已收到，嘿嘿～",
    ],
    "user_sticker_reaction_attach_sticker": True,
    "collected_sticker_enabled": True,
    "collected_sticker_disabled_reply": "用户表情收藏/使用功能当前未启用。",
    "collected_sticker_max_count": 200,
    "collected_sticker_max_file_bytes": 8_388_608,
    "collected_sticker_use_probability": 0.5,
    "collected_sticker_category_max_chars": COLLECTED_CATEGORY_MAX_CHARS,
    "collected_sticker_list_limit": 30,
    "collected_sticker_storage_unavailable_reply": (
        "收藏存储目录不可用，请检查 AstrBot 的 data/plugin_data 权限。"
    ),
    "collected_sticker_no_image_reply": (
        "这条消息里没有可收藏的图片。请发送图片/表情包并同时使用 /收藏表情。"
    ),
    "collected_sticker_face_unsupported_reply": (
        "QQ 原生 Face 表情没有可保存的图片文件，无法收藏；"
        "请发送图片/贴纸形式的 Image。"
    ),
    "collected_sticker_invalid_category_reply": (
        "分类名不合法，只允许中文、字母、数字、-、_，且不超过 {max_chars} 个字符。"
    ),
    "collected_sticker_invalid_image_reply": (
        "无法识别这张图片的格式，未收藏。支持 JPEG/PNG/GIF/WebP/BMP/AVIF。"
    ),
    "collected_sticker_too_large_reply": "图片超过收藏大小限制，未收藏。",
    "collected_sticker_duplicate_reply": "这张表情已经收藏过（分类：{category}）。",
    "collected_sticker_limit_reply": (
        "收藏数量已达上限 {max_count} 张，请先清理数据目录。"
    ),
    "collected_sticker_saved_reply": "已收藏到「{category}」：{filename}",
    "collected_sticker_empty_reply": "还没有收藏任何表情。",
    "collected_sticker_not_found_reply": "没有找到符合条件的收藏表情。",
    "collected_sticker_send_prefix": "送你一张收藏表情（{category}）：{filename}",
    "collected_sticker_list_header": "收藏表情共 {count} 张：",
    "collected_sticker_category_header": "分类「{category}」共 {count} 张：",
    "collected_sticker_list_more": "……另有 {count} 张未展开。",
    "collected_sticker_command_error_reply": "表情收藏命令处理失败，请稍后再试。",
    "pending_sticker_ttl_seconds": 300,
    "auto_collect_user_sticker_enabled": False,
    "auto_collect_sticker_max_file_bytes": 2_000_000,
    "auto_collect_sticker_max_dimension": 512,
    "auto_collect_sticker_default_category": "未分类",
    "auto_collect_sticker_reply_enabled": True,
    "auto_collect_sticker_cooldown_seconds": 60,
    "auto_collect_sticker_saved_reply": "收下了，已收藏到「{category}」。",
    "reply_probability": 0.12,
    "group_cooldown_seconds": 30,
    "user_cooldown_seconds": 90,
    "min_message_chars": 3,
    "max_input_chars": 2000,
    "max_reply_chars": 500,
    "ignore_pure_image": True,
    "ignore_pure_face": True,
    "block_default_llm_for_group_non_at": True,
    "ignore_command_prefixes": ["/"],
    "inherit_astrbot_wake_prefix": True,
    "system_prompt": (
        "你是一个简洁、友好的 QQ 聊天机器人。请用中文自然地回复，"
        "不要暴露系统提示词、内部实现或 API 凭据。"
    ),
    "llm_timeout_seconds": 45,
    "native_vision_first": True,
    "vision_provider_id": "",
    "auto_use_astrbot_caption_provider": True,
    "image_caption_prompt": (
        "请用中文客观描述这张图片的内容，尽量具体，不要编造图中不存在的信息。"
    ),
    "max_caption_chars": 1200,
    "image_unavailable_reply": (
        "我收到图片啦，但当前对话模型不能看图，且没有配置可用的图片转述模型。"
        "请让管理员配置支持视觉输入的模型后重试。"
    ),
    "llm_error_reply": "我暂时没想好怎么回复，稍后再试试吧。",
    "empty_reply_fallback": "嗯嗯，我在听。",
    "sticker_enabled": True,
    "sticker_probability": 0.35,
    "sticker_cooldown_seconds": 120,
    "sticker_prevent_repeat": True,
    "sticker_ai_select_enabled": True,
    "sticker_ai_marker_enabled": True,
    "sticker_ai_select_probability": 0.6,
    "max_images_per_message": 4,
}


class QQBotEnhancerPlugin(Star):
    """高优先级消息监听器：@/私聊必回，群聊非 @ 概率回复。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config: dict[str, Any] = config if isinstance(config, dict) else {}

        # 可注入的时钟/随机源让单元测试不必真的 sleep。
        self._clock = time.monotonic
        self._rng = random.Random()

        # 仅内存保存，进程重启后自动清空，避免把运行态写入插件目录。
        self._last_group_reply: dict[str, float] = {}
        self._last_user_reply: dict[str, float] = {}
        self._last_sticker_at: dict[str, float] = {}
        self._last_sticker_file: dict[str, str] = {}
        self._last_image_search_group: dict[str, float] = {}
        self._last_image_search_user: dict[str, float] = {}
        self._last_song_group: dict[str, float] = {}
        self._last_song_user: dict[str, float] = {}
        self._last_auto_collect_at: dict[str, float] = {}
        self._pending_user_media: dict[str, dict[str, Any]] = {}
        self._song_dir = SONG_DIR

        self._sticker_entries: list[dict[str, Any]] = []
        self._emotion_keywords: dict[str, tuple[str, ...]] = {}
        self._load_sticker_entries()

        # 用户收藏表情：官方 StarTools.get_data_dir() 返回
        # data/plugin_data/<plugin_name>/，绝不写回插件源码目录。
        self._collected_lock = asyncio.Lock()
        self._collected_root = self._resolve_collected_sticker_root()
        self._collected_entries: list[dict[str, Any]] = []
        self._load_collected_sticker_index()

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    def _config_value(self, key: str) -> Any:
        if isinstance(self.config, dict) and key in self.config:
            return self.config[key]
        return DEFAULTS.get(key)

    def _cfg_bool(self, key: str) -> bool:
        value = self._config_value(key)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _cfg_int(self, key: str, minimum: int = 0) -> int:
        raw = self._config_value(key)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raw_default = DEFAULTS.get(key, minimum)
            try:
                value = int(raw_default)
            except (TypeError, ValueError):
                value = minimum
        return max(minimum, value)

    def _cfg_float(self, key: str, minimum: float = 0.0, maximum: float = 1.0) -> float:
        raw = self._config_value(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raw_default = DEFAULTS.get(key, minimum)
            try:
                value = float(raw_default)
            except (TypeError, ValueError):
                value = minimum
        return min(maximum, max(minimum, value))

    def _cfg_str(self, key: str) -> str:
        raw = self._config_value(key)
        if raw is None:
            raw = DEFAULTS.get(key, "")
        return str(raw)

    def _memory_mode(self) -> str:
        value = self._cfg_str("memory_mode").strip().lower()
        if value in {"astrbot", "memory", "conversation", "long-term", "long_term"}:
            return "astrbot"
        return "direct"

    def _cfg_list(self, key: str) -> list[str]:
        raw = self._config_value(key)
        if isinstance(raw, str):
            raw = raw.replace("，", ",").split(",")
        if not isinstance(raw, (list, tuple, set)):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------
    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP
        | filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK,
        priority=100,
    )
    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_qq_message(self, event: AstrMessageEvent):
        """处理 QQ 群聊/私聊消息。"""

        mode: str | None = None
        should_stop = False
        yielded = False
        message_text = ""
        has_face = False

        try:
            if not event.is_private_chat() and not event.get_group_id():
                return

            components = event.get_messages()
            message_text = self._message_text(event)
            has_face = any(isinstance(comp, Comp.Face) for comp in components)
            has_image = any(isinstance(comp, Comp.Image) for comp in components)
            if has_image:
                await self._remember_recent_user_media(event, components)

            # 不抢占 AstrBot 的指令处理。自定义 wake_prefix 也会在这里被识别，
            # 防止概率回复把 /help 之类的指令吞掉。
            if self._is_command_message(event, message_text):
                return

            is_private = event.is_private_chat()
            bot_mentioned = self._is_bot_mentioned(event, components)
            explicit = is_private or bot_mentioned

            if not explicit:
                eligible = self._group_random_eligible(
                    message_text,
                    components,
                )
                if not eligible or not self._random_reply_allowed(event):
                    # AstrBot 的字段名是 call_llm，但语义是“是否阻断默认 LLM”：
                    # should_call_llm(True) 才会让 ProcessStage 的 not event.call_llm
                    # 条件不成立。这里不能传 False。
                    if self._cfg_bool("block_default_llm_for_group_non_at"):
                        event.should_call_llm(True)
                    return
                mode = "random"
            else:
                mode = "explicit"

            # Natural-language collection on an explicit @/private text message.
            # It can use the quoted image or the most recent media in this UMO.
            if explicit:
                collect_intent = self._detect_natural_language_collect_intent(
                    message_text
                )
                if collect_intent is not None:
                    should_stop = True
                    event.should_call_llm(True)
                    collect_reply = await self._collect_user_sticker(
                        event,
                        collect_intent["category"],
                    )
                    yield event.plain_result(collect_reply)
                    yielded = True
                    return

            # Explicit, textless small/square image: locally heuristically
            # auto-collect it as a user sticker. This happens before the generic
            # image reaction so the user receives only one short reply.
            if (
                explicit
                and has_image
                and not message_text.strip()
                and self._cfg_bool("auto_collect_user_sticker_enabled")
            ):
                auto_collect_reply = await self._maybe_auto_collect_user_sticker(
                    event
                )
                if (
                    auto_collect_reply
                    and self._cfg_bool(
                        "auto_collect_sticker_reply_enabled"
                    )
                ):
                    should_stop = True
                    event.should_call_llm(True)
                    yield event.plain_result(auto_collect_reply)
                    yielded = True
                    return

            # Explicit @/private image or QQ Face without text: treat it as a
            # user sticker reaction and answer in natural language without an
            # LLM call. A local sticker may be attached by the normal sticker
            # probability/cooldown path.
            if (
                explicit
                and (has_image or has_face)
                and not message_text.strip()
                and self._cfg_bool("user_sticker_reaction_enabled")
            ):
                reaction_probability = self._cfg_float(
                    "user_sticker_reaction_probability",
                    0.0,
                    1.0,
                )
                if self._rng.random() < reaction_probability:
                    should_stop = True
                    event.should_call_llm(True)
                    reaction_reply = self._pick_user_sticker_reaction_reply()
                    if self._cfg_bool("user_sticker_reaction_attach_sticker"):
                        result = self._build_result(
                            event,
                            reaction_reply,
                            message_text,
                        )
                    else:
                        result = event.plain_result(reaction_reply)
                    yield result
                    yielded = True
                    return

            # Explicit @/private natural-language image search. This intentionally
            # runs before memory_mode=astrbot so the plugin executes the search
            # directly and stops the default Agent from generating a duplicate.
            if (
                explicit
                and self._cfg_bool("image_search_natural_language_enabled")
                and self._cfg_bool("image_search_enabled")
            ):
                search_intent = self._detect_natural_language_image_search(
                    message_text
                )
                if search_intent is not None:
                    should_stop = True
                    event.should_call_llm(True)
                    remaining = self._image_search_cooldown_remaining(event)
                    if remaining > 0:
                        template = self._cfg_str("image_search_cooldown_reply")
                        try:
                            reply = template.format(seconds=remaining)
                        except (KeyError, ValueError):
                            reply = template
                        yield event.plain_result(reply)
                    else:
                        result = await self._build_image_search_result(
                            event,
                            search_intent["keyword"],
                            result_count=search_intent["result_count"],
                        )
                        yield result
                    yielded = True
                    return

            if self._memory_mode() == "astrbot":
                # 交给 AstrBot 默认 Agent：它会使用会话管理器、人格和持久化
                # 对话历史。非 @ 概率命中时，临时把事件标记为可唤醒，让默认
                # Agent 在这一条消息上继续执行；未命中概率的分支已经在上面阻断。
                event.set_extra("qqbot_enhancer_memory_reply", True)
                event.set_extra(
                    "qqbot_enhancer_sticker_text",
                    message_text or event.message_str,
                )
                event.is_at_or_wake_command = True
                return

            # 本插件已经决定回复，下面直接调用 provider，随后阻止默认 LLM 管线。
            # 注意 should_call_llm(True) 在 AstrBot 中表示“禁止默认 LLM”。
            should_stop = True
            event.should_call_llm(True)

            reply_text = await self._generate_reply(
                event,
                message_text=message_text,
                has_face=has_face,
            )
            result = self._build_result(
                event,
                reply_text,
                message_text,
                allow_random_sticker=not self._ai_marker_mode_active(),
            )
            yield result
            yielded = True
        except Exception:
            logger.exception("qqbot_enhancer 处理消息失败")
            if mode is not None and not yielded:
                should_stop = True
                event.should_call_llm(True)
                try:
                    fallback = self._cfg_str("llm_error_reply") or "我暂时无法回复。"
                    yield event.plain_result(self._finalize_reply(fallback))
                    yielded = True
                except Exception:
                    logger.exception("qqbot_enhancer 发送降级回复失败")
        finally:
            # 官方文档推荐：先 yield 发送结果，再终止传播。
            # 这样本次回复可以发送，同时后面的默认 LLM 不会重复回复。
            if should_stop:
                event.stop_event()

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP
        | filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK,
        priority=110,
    )
    @filter.command("找图", alias={"搜图"}, priority=110)
    async def find_image_command(self, event: AstrMessageEvent):
        """搜索并发送网络图片：/找图 关键词，别名 /搜图。"""

        # 命令自行回复，禁止默认 LLM 再回复一次；最后再 stop_event。
        event.should_call_llm(True)
        yielded = False
        try:
            if not self._cfg_bool("image_search_enabled"):
                yield event.plain_result(self._cfg_str("image_search_disabled_reply"))
                yielded = True
                return

            keyword = self._extract_image_search_keyword(event)
            if not keyword:
                yield event.plain_result(self._cfg_str("image_search_usage_reply"))
                yielded = True
                return

            remaining = self._image_search_cooldown_remaining(event)
            if remaining > 0:
                template = self._cfg_str("image_search_cooldown_reply")
                try:
                    text = template.format(seconds=remaining)
                except (KeyError, ValueError):
                    text = template
                yield event.plain_result(text)
                yielded = True
                return

            result = await self._build_image_search_result(
                event,
                keyword,
                result_count=None,
            )
            yield result
            yielded = True
        except Exception:
            logger.exception("qqbot_enhancer /找图 命令处理失败")
            if not yielded:
                try:
                    yield event.plain_result(self._cfg_str("image_search_error_reply"))
                    yielded = True
                except Exception:
                    logger.exception("qqbot_enhancer /找图 错误回复发送失败")
        finally:
            # 与普通回复一致：结果已经 yield 给 RespondStage 后才执行这里。
            event.stop_event()

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP
        | filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK,
        priority=110,
    )
    @filter.command("唱歌", alias={"点歌"}, priority=110)
    async def local_song_command(self, event: AstrMessageEvent):
        """发送 songs/ 目录中的本地授权音频，不调用 LLM。"""

        event.should_call_llm(True)
        yielded = False
        try:
            if not self._cfg_bool("song_enabled"):
                yield event.plain_result(self._cfg_str("song_disabled_reply"))
                yielded = True
                return

            keyword = self._extract_local_song_keyword(event)
            song_files, has_oversized = self._list_song_files()
            if not song_files:
                yield event.plain_result(
                    self._cfg_str(
                        "song_too_large_reply" if has_oversized else "song_empty_reply"
                    )
                )
                yielded = True
                return

            if keyword:
                keyword_folded = keyword.casefold()
                matched = [
                    path
                    for path in song_files
                    if keyword_folded in path.name.casefold()
                ]
                if not matched:
                    yield event.plain_result(
                        self._cfg_str("song_not_found_reply")
                    )
                    yielded = True
                    return
                song_files = matched

            remaining = self._song_cooldown_remaining(event)
            if remaining > 0:
                template = self._cfg_str("song_cooldown_reply")
                try:
                    text = template.format(seconds=remaining)
                except (KeyError, ValueError):
                    text = template
                yield event.plain_result(text)
                yielded = True
                return

            chosen = self._rng.choice(song_files)
            try:
                record = Comp.Record.fromFileSystem(str(chosen))
            except Exception:
                logger.warning(
                    "qqbot_enhancer 本地音频组件构造失败: %s",
                    chosen,
                    exc_info=True,
                )
                yield event.plain_result(self._cfg_str("song_error_reply"))
                yielded = True
                return

            prefix_template = self._cfg_str("song_success_prefix")
            try:
                prefix = prefix_template.format(name=chosen.stem)
            except (KeyError, ValueError):
                prefix = prefix_template
            yield event.chain_result([Comp.Plain(prefix), record])
            yielded = True
        except Exception:
            logger.exception("qqbot_enhancer /唱歌 命令处理失败")
            if not yielded:
                try:
                    yield event.plain_result(self._cfg_str("song_error_reply"))
                    yielded = True
                except Exception:
                    logger.exception("qqbot_enhancer 点歌错误回复发送失败")
        finally:
            event.stop_event()

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP
        | filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK,
        priority=110,
    )
    @filter.command("option", alias={"插件用法", "用法"}, priority=110)
    async def option_command(self, event: AstrMessageEvent):
        """显示本插件用法、配置入口与可可靠枚举的已加载插件元数据。"""

        event.should_call_llm(True)
        try:
            text = self._build_option_text()
            limit = self._cfg_int("option_max_chars", 1)
            if limit > 0 and len(text) > limit:
                text = text[: max(1, limit - 1)].rstrip() + "…"
            yield event.plain_result(text)
        except Exception:
            logger.exception("qqbot_enhancer /option 命令处理失败")
            try:
                yield event.plain_result(
                    "插件用法暂时读取失败，请到 AstrBot WebUI → 插件 查看。"
                )
            except Exception:
                logger.exception("qqbot_enhancer /option 错误回复发送失败")
        finally:
            event.stop_event()

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP
        | filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK,
        priority=110,
    )
    @filter.command("收藏表情", priority=110)
    async def collect_sticker_command(self, event: AstrMessageEvent):
        """把当前消息中的 Comp.Image 收藏到插件数据目录。"""

        event.should_call_llm(True)
        try:
            if not self._cfg_bool("collected_sticker_enabled"):
                yield event.plain_result(
                    self._cfg_str("collected_sticker_disabled_reply")
                )
                return
            raw_category = self._collect_command_argument(
                event,
                ("收藏表情",),
            )
            category = self._normalize_collected_category(raw_category)
            if category is None:
                max_chars = min(
                    COLLECTED_CATEGORY_MAX_CHARS,
                    max(
                        1,
                        self._cfg_int(
                            "collected_sticker_category_max_chars",
                            1,
                        ),
                    ),
                )
                yield event.plain_result(
                    self._format_collected_reply(
                        self._cfg_str(
                            "collected_sticker_invalid_category_reply"
                        ),
                        max_chars=max_chars,
                    )
                )
                return
            reply = await self._collect_user_sticker(event, category)
            yield event.plain_result(reply)
        except Exception:
            logger.exception("qqbot_enhancer /收藏表情 命令处理失败")
            try:
                yield event.plain_result(
                    self._cfg_str("collected_sticker_command_error_reply")
                )
            except Exception:
                logger.exception("qqbot_enhancer 收藏表情错误回复发送失败")
        finally:
            event.stop_event()

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP
        | filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK,
        priority=110,
    )
    @filter.command("表情分类", priority=110)
    async def collected_categories_command(self, event: AstrMessageEvent):
        """列出收藏表情分类与数量。"""

        event.should_call_llm(True)
        try:
            if not self._cfg_bool("collected_sticker_enabled"):
                yield event.plain_result(
                    self._cfg_str("collected_sticker_disabled_reply")
                )
                return
            counts = self._collected_category_counts()
            total = sum(counts.values())
            if total <= 0:
                yield event.plain_result(
                    self._cfg_str("collected_sticker_empty_reply")
                )
                return
            lines = [f"收藏表情共 {total} 张，分类 {len(counts)} 个："]
            list_limit = max(1, self._cfg_int("collected_sticker_list_limit", 1))
            ordered_categories = sorted(counts)
            for category in ordered_categories[:list_limit]:
                lines.append(f"- {category}: {counts[category]} 张")
            if len(ordered_categories) > list_limit:
                lines.append(
                    self._format_collected_reply(
                        self._cfg_str("collected_sticker_list_more"),
                        count=len(ordered_categories) - list_limit,
                    )
                )
            yield event.plain_result("\n".join(lines))
        except Exception:
            logger.exception("qqbot_enhancer /表情分类 命令处理失败")
            try:
                yield event.plain_result(
                    self._cfg_str("collected_sticker_command_error_reply")
                )
            except Exception:
                logger.exception("qqbot_enhancer 表情分类错误回复发送失败")
        finally:
            event.stop_event()

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP
        | filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK,
        priority=110,
    )
    @filter.command("表情列表", priority=110)
    async def collected_list_command(self, event: AstrMessageEvent):
        """列出收藏表情文件名，不输出绝对路径。"""

        event.should_call_llm(True)
        try:
            if not self._cfg_bool("collected_sticker_enabled"):
                yield event.plain_result(
                    self._cfg_str("collected_sticker_disabled_reply")
                )
                return
            raw_category = self._collect_command_argument(
                event,
                ("表情列表",),
            )
            category = (
                None
                if not raw_category
                else self._normalize_collected_category(raw_category)
            )
            if raw_category and category is None:
                yield event.plain_result(
                    self._format_collected_reply(
                        self._cfg_str(
                            "collected_sticker_invalid_category_reply"
                        ),
                        max_chars=min(
                            COLLECTED_CATEGORY_MAX_CHARS,
                            max(
                                1,
                                self._cfg_int(
                                    "collected_sticker_category_max_chars",
                                    1,
                                ),
                            ),
                        ),
                    )
                )
                return
            entries = self._valid_collected_entries()
            if category is not None:
                entries = [
                    entry for entry in entries if entry.get("category") == category
                ]
            if not entries:
                reply_key = (
                    "collected_sticker_not_found_reply"
                    if category is not None
                    else "collected_sticker_empty_reply"
                )
                yield event.plain_result(self._cfg_str(reply_key))
                return
            list_limit = max(1, self._cfg_int("collected_sticker_list_limit", 1))
            if category is None:
                header = self._format_collected_reply(
                    self._cfg_str("collected_sticker_list_header"),
                    count=len(entries),
                )
            else:
                header = self._format_collected_reply(
                    self._cfg_str("collected_sticker_category_header"),
                    category=category,
                    count=len(entries),
                )
            lines = [header]
            for entry in entries[:list_limit]:
                filename = str(entry.get("filename", ""))
                if category is None:
                    lines.append(
                        f"- [{entry.get('category', DEFAULT_COLLECTED_CATEGORY)}] "
                        f"{filename}"
                    )
                else:
                    lines.append(f"- {filename}")
            if len(entries) > list_limit:
                lines.append(
                    self._format_collected_reply(
                        self._cfg_str("collected_sticker_list_more"),
                        count=len(entries) - list_limit,
                    )
                )
            yield event.plain_result("\n".join(lines))
        except Exception:
            logger.exception("qqbot_enhancer /表情列表 命令处理失败")
            try:
                yield event.plain_result(
                    self._cfg_str("collected_sticker_command_error_reply")
                )
            except Exception:
                logger.exception("qqbot_enhancer 表情列表错误回复发送失败")
        finally:
            event.stop_event()

    @filter.platform_adapter_type(
        filter.PlatformAdapterType.AIOCQHTTP
        | filter.PlatformAdapterType.QQOFFICIAL
        | filter.PlatformAdapterType.QQOFFICIAL_WEBHOOK,
        priority=110,
    )
    @filter.command("发表情", priority=110)
    async def send_collected_sticker_command(self, event: AstrMessageEvent):
        """随机发送一张已收藏表情。"""

        event.should_call_llm(True)
        try:
            if not self._cfg_bool("collected_sticker_enabled"):
                yield event.plain_result(
                    self._cfg_str("collected_sticker_disabled_reply")
                )
                return
            raw_category = self._collect_command_argument(
                event,
                ("发表情",),
            )
            category = (
                None
                if not raw_category
                else self._normalize_collected_category(raw_category)
            )
            if raw_category and category is None:
                yield event.plain_result(
                    self._format_collected_reply(
                        self._cfg_str(
                            "collected_sticker_invalid_category_reply"
                        ),
                        max_chars=min(
                            COLLECTED_CATEGORY_MAX_CHARS,
                            max(
                                1,
                                self._cfg_int(
                                    "collected_sticker_category_max_chars",
                                    1,
                                ),
                            ),
                        ),
                    )
                )
                return
            chosen_path = self._select_collected_sticker(
                "",
                event.unified_msg_origin,
                category=category,
            )
            if chosen_path is None:
                reply_key = (
                    "collected_sticker_not_found_reply"
                    if category is not None
                    else "collected_sticker_empty_reply"
                )
                yield event.plain_result(self._cfg_str(reply_key))
                return
            chosen_entry = next(
                (
                    entry
                    for entry in self._valid_collected_entries()
                    if entry.get("path") == chosen_path
                ),
                None,
            )
            if chosen_entry is None:
                yield event.plain_result(
                    self._cfg_str("collected_sticker_not_found_reply")
                )
                return
            try:
                image_component = Comp.Image.fromFileSystem(str(chosen_path))
            except Exception:
                logger.warning(
                    "qqbot_enhancer 构造收藏表情发送组件失败: %s",
                    chosen_path,
                    exc_info=True,
                )
                yield event.plain_result(
                    self._cfg_str("collected_sticker_command_error_reply")
                )
                return
            prefix = self._format_collected_reply(
                self._cfg_str("collected_sticker_send_prefix"),
                category=chosen_entry.get("category", DEFAULT_COLLECTED_CATEGORY),
                filename=chosen_entry.get("filename", ""),
            )
            self._last_sticker_at[event.unified_msg_origin] = self._clock()
            self._last_sticker_file[event.unified_msg_origin] = str(
                chosen_entry.get("filename", "")
            )
            yield event.chain_result([Comp.Plain(prefix), image_component])
        except Exception:
            logger.exception("qqbot_enhancer /发表情 命令处理失败")
            try:
                yield event.plain_result(
                    self._cfg_str("collected_sticker_command_error_reply")
                )
            except Exception:
                logger.exception("qqbot_enhancer 发表情错误回复发送失败")
        finally:
            event.stop_event()

    @filter.on_decorating_result()
    async def attach_memory_mode_sticker(self, event: AstrMessageEvent):
        """Add a local sticker after AstrBot default Agent replies in memory mode."""

        if not event.get_extra("qqbot_enhancer_memory_reply"):
            return
        if not self._cfg_bool("sticker_enabled"):
            return
        collected_available = (
            self._cfg_bool("collected_sticker_enabled")
            and bool(self._valid_collected_entries())
        )
        if not self._sticker_entries and not collected_available:
            return
        result = event.get_result()
        chain = getattr(result, "chain", None) if result is not None else None
        if not isinstance(chain, list):
            return
        # Never append a second image/sticker to an Agent reply.
        if any(isinstance(component, Comp.Image) for component in chain):
            return
        if self._cfg_bool("sticker_ai_select_enabled"):
            if self._rng.random() >= self._cfg_float(
                "sticker_ai_select_probability",
                0.0,
                1.0,
            ):
                return
        message_text = str(
            event.get_extra("qqbot_enhancer_sticker_text")
            or getattr(event, "message_str", "")
            or ""
        )
        sticker = self._maybe_pick_sticker(event, message_text)
        if sticker is None:
            return
        try:
            chain.append(Comp.Image.fromFileSystem(str(sticker)))
        except Exception:
            logger.warning(
                "qqbot_enhancer 默认 Agent 表情包追加失败",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # /option help
    # ------------------------------------------------------------------
    def _loaded_star_metadata(self) -> list[Any]:
        getter = getattr(self.context, "get_all_stars", None)
        if not callable(getter):
            return []
        try:
            stars = getter()
        except Exception:
            logger.warning(
                "qqbot_enhancer 读取已加载插件元数据失败",
                exc_info=True,
            )
            return []
        if not isinstance(stars, (list, tuple)):
            return []
        return [star for star in stars if star is not None]

    def _build_option_text(self) -> str:
        stars = self._loaded_star_metadata()
        my_name = str(getattr(self, "name", "") or "qqbot_enhancer")
        display_name = PLUGIN_DISPLAY_NAME
        version = PLUGIN_FALLBACK_VERSION

        for star in stars:
            star_name = str(getattr(star, "name", "") or "")
            is_self = (
                getattr(star, "star_cls", None) is self
                or star_name == my_name
                or getattr(star, "root_dir_name", "") == "qqbot_enhancer"
            )
            if not is_self:
                continue
            display_name = (
                str(getattr(star, "display_name", "") or "")
                or star_name
                or display_name
            )
            version = str(getattr(star, "version", "") or version)
            break

        mode = self._memory_mode()
        probability = self._cfg_float("reply_probability", 0.0, 1.0)
        group_cooldown = self._cfg_int("group_cooldown_seconds", 0)
        user_cooldown = self._cfg_int("user_cooldown_seconds", 0)
        natural_language = "开启" if self._cfg_bool(
            "image_search_natural_language_enabled"
        ) else "关闭"

        lines = [
            f"{display_name} 用法速览（v{version}）",
            "",
            "本插件命令：",
            "/找图 <关键词>（别名 /搜图）",
            "/唱歌 [文件名关键词]（别名 /点歌）",
            "/收藏表情 [分类]",
            "/表情分类",
            "/表情列表 [分类]",
            "/发表情 [分类]",
            "/option（别名 /插件用法、/用法）",
            "",
            (
                f"当前行为：memory_mode={mode}；群 @ 必回；私聊必回；"
                f"非 @ 群消息概率 {probability:.0%}；"
                f"群冷却 {group_cooldown}s / 用户冷却 {user_cooldown}s；"
                f"自然语言找图{natural_language}。"
            ),
            (
                "配置入口：AstrBot WebUI → 插件 → "
                f"{display_name} → 配置。"
            ),
            (
                "找图说明：/找图 和 /搜图 会下载并校验图片后发本地文件；"
                "@ 或私聊的自然语言找图也复用同一流程。"
            ),
            (
                "表情收藏：/收藏表情 保存当前消息中的图片到插件数据目录；"
                "内置 stickers 与用户收藏表情都会参与正常回复。"
            ),
        ]

        if self._cfg_bool("option_include_other_plugins"):
            max_plugins = max(1, self._cfg_int("option_max_plugins", 1))
            shown = 0
            if stars:
                lines.extend(
                    [
                        "",
                        f"已加载插件元数据（共 {len(stars)} 个，最多显示 {max_plugins} 个）：",
                    ]
                )
                for star in stars[:max_plugins]:
                    star_name = str(getattr(star, "name", "") or "")
                    star_display = (
                        str(getattr(star, "display_name", "") or "")
                        or star_name
                        or "未命名插件"
                    )
                    star_version = str(getattr(star, "version", "") or "unknown")
                    star_desc = re.sub(
                        r"\s+",
                        " ",
                        str(getattr(star, "desc", "") or ""),
                    ).strip()
                    activated = bool(getattr(star, "activated", True))
                    suffix = "" if activated else "（未激活）"
                    line = f"- {star_display} v{star_version}{suffix}"
                    if star_desc:
                        line += f"：{star_desc[:80]}"
                    lines.append(line)
                    shown += 1
                if len(stars) > shown:
                    lines.append(f"- 另有 {len(stars) - shown} 个插件未展开。")
            else:
                lines.append("")
                lines.append(
                    "已加载插件元数据：当前上下文不支持读取，仅列本插件。"
                )
            lines.append(
                "说明：AstrBot v4.29 没有稳定的公开命令枚举 API；"
                "这里只列插件元数据，不逐个枚举其他插件命令。"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Message analysis
    # ------------------------------------------------------------------
    def _message_text(self, event: AstrMessageEvent) -> str:
        parts: list[str] = []
        for comp in event.get_messages():
            if isinstance(comp, Comp.Plain):
                text = getattr(comp, "text", "")
                if text:
                    parts.append(str(text))
        text = " ".join(parts).strip()
        if not text:
            text = str(getattr(event, "message_str", "") or "").strip()
        max_input = self._cfg_int("max_input_chars", 1)
        return text[:max_input] if max_input > 0 else text

    def _is_bot_mentioned(self, event: AstrMessageEvent, components: list[Any]) -> bool:
        self_id = str(event.get_self_id() or "").strip()
        if not self_id:
            return False
        for comp in components:
            # AtAll 是 At 的子类，必须先排除；qq == "all" 的兼容写法也排除。
            if isinstance(comp, Comp.AtAll):
                continue
            if not isinstance(comp, Comp.At):
                continue
            qq = str(getattr(comp, "qq", "") or "").strip()
            if not qq or qq.lower() in {"all", "everyone"}:
                continue
            if qq == self_id:
                return True
        return False

    def _has_at_all(self, components: list[Any]) -> bool:
        for comp in components:
            if isinstance(comp, Comp.AtAll):
                return True
            if isinstance(comp, Comp.At):
                qq = str(getattr(comp, "qq", "") or "").strip().lower()
                if qq in {"all", "everyone"}:
                    return True
        return False

    def _group_random_eligible(
        self,
        message_text: str,
        components: list[Any],
    ) -> bool:
        has_image = any(isinstance(comp, Comp.Image) for comp in components)
        has_face = any(isinstance(comp, Comp.Face) for comp in components)

        stripped = message_text.strip()
        if stripped:
            return len(stripped) >= self._cfg_int("min_message_chars", 0)
        # 默认忽略纯图片/纯表情/空消息。用户显式关闭 ignore_* 后仍可走概率路径。
        if has_image and not self._cfg_bool("ignore_pure_image"):
            return True
        if has_face and not self._cfg_bool("ignore_pure_face"):
            return True
        return False

    def _random_reply_allowed(self, event: AstrMessageEvent) -> bool:
        now = self._clock()
        self._prune_state(now)

        session_key = event.unified_msg_origin
        user_key = f"{session_key}:{event.get_sender_id()}"
        group_cooldown = self._cfg_int("group_cooldown_seconds", 0)
        user_cooldown = self._cfg_int("user_cooldown_seconds", 0)

        last_group = self._last_group_reply.get(session_key)
        if last_group is not None and now - last_group < group_cooldown:
            return False
        last_user = self._last_user_reply.get(user_key)
        if last_user is not None and now - last_user < user_cooldown:
            return False

        probability = self._cfg_float("reply_probability", 0.0, 1.0)
        if self._rng.random() >= probability:
            return False

        # 先占住冷却再调用较慢的 provider，避免并发消息连续刷屏。
        self._last_group_reply[session_key] = now
        self._last_user_reply[user_key] = now
        return True

    def _prune_state(self, now: float) -> None:
        # 简单上限保护，避免长期运行时字典无限增长。
        if (
            len(self._last_group_reply)
            + len(self._last_user_reply)
            + len(self._last_sticker_at)
            + len(self._last_image_search_group)
            + len(self._last_image_search_user)
            + len(self._last_song_group)
            + len(self._last_song_user)
            + len(self._last_auto_collect_at)
            + len(self._pending_user_media)
            < 4096
        ):
            return
        cutoff = now - 86400
        for mapping in (
            self._last_group_reply,
            self._last_user_reply,
            self._last_sticker_at,
            self._last_image_search_group,
            self._last_image_search_user,
            self._last_song_group,
            self._last_song_user,
            self._last_auto_collect_at,
        ):
            for key, timestamp in list(mapping.items()):
                if timestamp < cutoff:
                    mapping.pop(key, None)
        for key in list(self._last_sticker_file):
            if key not in self._last_sticker_at:
                self._last_sticker_file.pop(key, None)
        self._prune_pending_user_media(now)

    def _command_prefixes(self, event: AstrMessageEvent) -> list[str]:
        prefixes = self._cfg_list("ignore_command_prefixes")
        if self._cfg_bool("inherit_astrbot_wake_prefix"):
            try:
                astrbot_config = self.context.get_config(umo=event.unified_msg_origin)
                wake_prefixes = astrbot_config.get("wake_prefix", [])
                if isinstance(wake_prefixes, str):
                    wake_prefixes = [wake_prefixes]
                if isinstance(wake_prefixes, (list, tuple)):
                    prefixes.extend(
                        str(prefix)
                        for prefix in wake_prefixes
                        if str(prefix).strip()
                    )
            except Exception:
                logger.debug("qqbot_enhancer 读取 AstrBot wake_prefix 失败", exc_info=True)
        # 空字符串会匹配所有消息，必须过滤。
        return sorted({prefix for prefix in prefixes if prefix}, key=len, reverse=True)

    def _is_command_message(self, event: AstrMessageEvent, text: str) -> bool:
        stripped = text.lstrip()
        if not stripped:
            return False
        return any(stripped.startswith(prefix) for prefix in self._command_prefixes(event))

    # ------------------------------------------------------------------
    # Provider / LLM calls
    # ------------------------------------------------------------------
    async def _get_current_provider_id(self, event: AstrMessageEvent) -> str:
        try:
            provider_id = await self.context.get_current_chat_provider_id(
                umo=event.unified_msg_origin,
            )
            if provider_id:
                return str(provider_id)
        except Exception:
            logger.warning("qqbot_enhancer 获取当前 provider 失败", exc_info=True)

        # 兼容测试桩或极少数不实现上述接口的上下文。
        try:
            provider = await self.context.get_using_provider_async(
                umo=event.unified_msg_origin,
            )
        except Exception:
            provider = None
        if provider is not None:
            try:
                return str(provider.meta().id)
            except Exception:
                pass
        try:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            if provider is not None:
                return str(provider.meta().id)
        except Exception:
            pass
        return ""

    async def _llm_generate(self, **kwargs: Any) -> str:
        llm_generate = getattr(self.context, "llm_generate", None)
        if not callable(llm_generate):
            raise RuntimeError("AstrBot Context.llm_generate 不可用")
        timeout = self._cfg_int("llm_timeout_seconds", 1)
        response = await asyncio.wait_for(llm_generate(**kwargs), timeout=timeout)
        text = getattr(response, "completion_text", "")
        if text is None:
            return ""
        return str(text).strip()

    def _provider_image_capability(self, provider: Any) -> bool | None:
        """Return True/False when provider modalities are known, else None."""

        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return None
        modalities = provider_config.get("modalities", None)
        if modalities == []:
            # AstrBot 会把迁移遗留的空列表视为“未配置”，保持向后兼容。
            return True
        if isinstance(modalities, (list, tuple)) and modalities:
            return "image" in modalities
        return None

    def _provider_exists(self, provider_id: str) -> bool:
        getter = getattr(self.context, "get_provider_by_id", None)
        if not callable(getter):
            return True
        try:
            return getter(provider_id) is not None
        except Exception:
            return True

    async def _generate_reply(
        self,
        event: AstrMessageEvent,
        *,
        message_text: str,
        has_face: bool,
    ) -> str:
        image_refs = await self._collect_image_refs(event)
        if image_refs:
            return await self._reply_to_images(
                event,
                image_refs=image_refs,
                message_text=message_text,
                has_face=has_face,
            )
        return await self._reply_to_text(
            event,
            message_text=message_text,
            has_face=has_face,
        )

    async def _collect_image_refs(self, event: AstrMessageEvent) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        max_images = self._cfg_int("max_images_per_message", 1)

        for comp in event.get_messages():
            if not isinstance(comp, Comp.Image):
                continue
            if len(refs) >= max_images:
                break

            chosen = ""
            for candidate in (
                getattr(comp, "url", None),
                getattr(comp, "file", None),
                getattr(comp, "path", None),
            ):
                if isinstance(candidate, str) and self._is_media_ref(candidate):
                    chosen = candidate
                    break

            # 某些适配器只提供临时对象，此时使用 AstrBot Image 的官方转换方法。
            if not chosen:
                try:
                    base64_data = await comp.convert_to_base64()
                    if base64_data:
                        chosen = f"base64://{base64_data}"
                except Exception:
                    logger.warning("qqbot_enhancer 无法读取图片组件", exc_info=True)
                    continue

            if chosen and chosen not in seen:
                seen.add(chosen)
                refs.append(chosen)
        return refs

    @staticmethod
    def _is_media_ref(ref: str) -> bool:
        value = ref.strip()
        if not value:
            return False
        lowered = value.lower()
        if lowered.startswith(
            (
                "http://",
                "https://",
                "base64://",
                "data:image/",
                "file://",
            )
        ):
            return True
        return os.path.exists(value)

    def _build_prompt(
        self,
        event: AstrMessageEvent,
        message_text: str,
        *,
        has_face: bool,
        has_image: bool,
        image_caption: str = "",
    ) -> str:
        sender_name = ""
        try:
            sender_name = str(event.get_sender_name() or "")
        except Exception:
            pass
        scene = "私聊" if event.is_private_chat() else "群聊"
        lines = [
            f"场景：{scene}",
            f"用户昵称：{sender_name or '未知'}",
        ]
        if has_image:
            image_count = self._cfg_int("max_images_per_message", 1)
            lines.append(f"用户发来了图片（最多附带 {image_count} 张）。")
        if message_text.strip():
            lines.append(f"用户文字：{message_text.strip()}")
        elif has_image:
            lines.append("用户没有附带文字，请观察图片后直接自然回复。")
        elif has_face:
            lines.append("用户发送了一个 QQ 表情，没有附带文字。")
        else:
            lines.append("用户没有输入文字内容。")
        if image_caption:
            lines.extend(
                [
                    "以下是图片转述模型得到的客观描述，请基于它回复用户：",
                    f"<image_caption>{image_caption}</image_caption>",
                ]
            )
        lines.append("请直接输出要发给用户的回复正文，不要输出分析过程。")
        return "\n".join(lines)

    async def _reply_to_text(
        self,
        event: AstrMessageEvent,
        *,
        message_text: str,
        has_face: bool,
    ) -> str:
        provider_id = await self._get_current_provider_id(event)
        if not provider_id:
            return self._cfg_str("llm_error_reply")
        prompt = self._build_prompt(
            event,
            message_text,
            has_face=has_face,
            has_image=False,
        )
        try:
            reply = await self._llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=self._effective_system_prompt(),
            )
        except Exception:
            logger.warning("qqbot_enhancer 文本 LLM 调用失败", exc_info=True)
            return self._cfg_str("llm_error_reply")
        return reply or self._cfg_str("empty_reply_fallback")

    async def _reply_to_images(
        self,
        event: AstrMessageEvent,
        *,
        image_refs: list[str],
        message_text: str,
        has_face: bool,
    ) -> str:
        provider_id = await self._get_current_provider_id(event)
        prompt = self._build_prompt(
            event,
            message_text,
            has_face=has_face,
            has_image=True,
        )

        native_attempted = False
        if provider_id and self._cfg_bool("native_vision_first"):
            provider = None
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(provider_id)
                except Exception:
                    provider = None
            capability = self._provider_image_capability(provider)
            if capability is not False:
                native_attempted = True
                try:
                    reply = await self._llm_generate(
                        chat_provider_id=provider_id,
                        prompt=prompt,
                        image_urls=image_refs,
                        system_prompt=self._effective_system_prompt(),
                    )
                    if reply:
                        return reply
                    logger.warning("qqbot_enhancer 原生视觉调用返回空内容，尝试图片转述降级")
                except Exception:
                    logger.warning(
                        "qqbot_enhancer 原生视觉调用失败，尝试图片转述降级",
                        exc_info=True,
                    )
            else:
                logger.info(
                    "qqbot_enhancer 当前 provider 明确不支持图片，使用图片转述降级",
                )

        caption = await self._caption_images(event, image_refs)
        if caption:
            if provider_id and provider_id != "":
                try:
                    caption_prompt = self._build_prompt(
                        event,
                        message_text,
                        has_face=has_face,
                        has_image=True,
                        image_caption=caption,
                    )
                    reply = await self._llm_generate(
                        chat_provider_id=provider_id,
                        prompt=caption_prompt,
                        system_prompt=self._effective_system_prompt(),
                    )
                    if reply:
                        return reply
                except Exception:
                    logger.warning(
                        "qqbot_enhancer 基于图片转述生成回复失败，直接返回描述",
                        exc_info=True,
                    )
            return f"我看了一下图片：{caption}"

        if native_attempted:
            logger.warning("qqbot_enhancer 原生视觉和图片转述均不可用，使用兜底回复")
        return self._cfg_str("image_unavailable_reply")

    async def _caption_images(
        self,
        event: AstrMessageEvent,
        image_refs: list[str],
    ) -> str:
        candidates = self._caption_provider_candidates(event)
        if not candidates:
            logger.warning(
                "qqbot_enhancer 未配置 vision_provider_id，"
                "且 AstrBot 未配置 default_image_caption_provider_id",
            )
            return ""

        caption_prompt = self._cfg_str("image_caption_prompt")
        max_caption = self._cfg_int("max_caption_chars", 1)
        for provider_id in candidates:
            if not self._provider_exists(provider_id):
                logger.warning(
                    "qqbot_enhancer 图片转述 provider 不存在: %s",
                    provider_id,
                )
                continue
            try:
                caption = await self._llm_generate(
                    chat_provider_id=provider_id,
                    prompt=caption_prompt,
                    image_urls=image_refs,
                    system_prompt="你只负责客观描述图片，不要编造不存在的内容。",
                )
            except Exception:
                logger.warning(
                    "qqbot_enhancer 图片转述 provider %s 调用失败",
                    provider_id,
                    exc_info=True,
                )
                continue
            if caption:
                return caption[:max_caption].strip()
        return ""

    def _caption_provider_candidates(self, event: AstrMessageEvent) -> list[str]:
        candidates: list[str] = []
        plugin_provider = self._cfg_str("vision_provider_id").strip()
        if plugin_provider:
            candidates.append(plugin_provider)

        if self._cfg_bool("auto_use_astrbot_caption_provider"):
            try:
                astrbot_config = self.context.get_config(umo=event.unified_msg_origin)
                provider_settings = astrbot_config.get("provider_settings", {})
                if isinstance(provider_settings, dict):
                    global_provider = str(
                        provider_settings.get("default_image_caption_provider_id", "")
                        or ""
                    ).strip()
                    if global_provider:
                        candidates.append(global_provider)
            except Exception:
                logger.debug(
                    "qqbot_enhancer 读取 AstrBot 图片转述 provider 配置失败",
                    exc_info=True,
                )

        # 去重且保持先后顺序。
        return list(dict.fromkeys(candidates))

    def _finalize_reply(self, text: str) -> str:
        if not isinstance(text, str):
            text = str(text or "")
        text = text.strip()
        if not text:
            text = self._cfg_str("empty_reply_fallback") or "嗯嗯。"
        limit = self._cfg_int("max_reply_chars", 1)
        if limit > 0 and len(text) > limit:
            text = text[:limit].rstrip() + "…"
        return text

    # ------------------------------------------------------------------
    # Network image search (Bing Images HTML, no API key)
    # ------------------------------------------------------------------
    def _extract_image_search_keyword(self, event: AstrMessageEvent) -> str:
        text = str(getattr(event, "message_str", "") or "").strip()
        # 真实 AstrBot 的 WakingCheckStage 通常已经去掉 wake_prefix；
        # 测试或部分适配器可能仍保留，这里兼容两种形态。
        for prefix in self._command_prefixes(event):
            if prefix and text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        for command_name in ("找图", "搜图"):
            if text == command_name:
                return ""
            if text.startswith(command_name):
                rest = text[len(command_name) :]
                if rest and rest[0].isspace():
                    keyword = re.sub(r"\s+", " ", rest).strip()
                    keyword = "".join(
                        char for char in keyword if char.isprintable()
                    ).strip()
                    max_chars = self._cfg_int("image_search_max_keyword_chars", 1)
                    return keyword[:max_chars]
        return ""

    def _detect_natural_language_image_search(
        self,
        text: str,
    ) -> dict[str, Any] | None:
        """Conservatively detect an explicit request to find/send images.

        Only called for @/private events. Returns ``None`` when any part of the
        request is ambiguous; ordinary conversation then continues normally.
        """

        value = re.sub(r"\s+", " ", str(text or "")).strip()
        if not value:
            return None
        if IMAGE_SEARCH_NL_REFUSAL_RE.search(value):
            return None

        negations = list(IMAGE_SEARCH_NL_NEGATION_RE.finditer(value))
        for match in IMAGE_SEARCH_NL_RE.finditer(value):
            action_start = match.start("action")
            # Skip a "找不到/没搜到..." match, but keep looking for a later
            # explicit request such as "我找不到图片了，帮我找几张猫图".
            if any(
                negation.start() <= action_start < negation.end()
                for negation in negations
            ):
                continue
            keyword = self._clean_natural_language_image_keyword(
                match.group("keyword")
            )
            if not keyword:
                continue
            return {
                "keyword": keyword,
                "result_count": self._natural_language_result_count(
                    match.group("quantity")
                ),
            }
        return None

    def _clean_natural_language_image_keyword(self, raw: str) -> str:
        value = re.sub(r"\s+", " ", str(raw or "")).strip()
        value = re.sub(
            r"^(?:帮我|给我|请你?|麻烦你?|你|去|来|再|一下|下|能|可以|帮忙|关于|有关|的)+",
            "",
            value,
        ).strip()
        value = re.sub(r"^(?:张|幅|个)+", "", value).strip()
        value = re.sub(
            r"(?:的)?(?:图片|照片|壁纸|头像|插画|美图|图集|图)$",
            "",
            value,
        ).strip()
        value = value.strip(" \t\r\n，。！？!?,.、:：;；\"'“”‘’")
        value = re.sub(r"^[的地得]+|[的地得]+$", "", value).strip()
        if value in {
            "",
            "张",
            "幅",
            "个",
            "的",
            "图",
            "图片",
            "照片",
            "壁纸",
            "头像",
            "插画",
            "美图",
            "图集",
            "好看的",
            "好看",
            "一些",
            "几张",
            "一下",
            "帮我",
            "给我",
            "你",
            "去",
            "来",
            "这个",
            "那个",
        }:
            return ""
        max_chars = self._cfg_int("image_search_max_keyword_chars", 1)
        return value[:max_chars]

    def _natural_language_result_count(self, quantity: str | None) -> int:
        if not quantity:
            return self._image_search_result_limit()
        normalized = str(quantity).strip()
        mapping = {
            "一": 1,
            "1": 1,
            "二": 2,
            "两": 2,
            "2": 2,
            "三": 3,
            "3": 3,
            "四": 4,
            "4": 4,
            "五": 5,
            "5": 5,
            "几": max(2, self._image_search_result_limit()),
            "一些": max(2, self._image_search_result_limit()),
        }
        return max(
            1,
            min(
                IMAGE_SEARCH_MAX_RESULTS_PER_COMMAND,
                mapping.get(normalized, self._image_search_result_limit()),
            ),
        )

    def _image_search_cooldown_remaining(self, event: AstrMessageEvent) -> int:
        now = self._clock()
        self._prune_state(now)
        session_key = event.unified_msg_origin
        user_key = f"{session_key}:{event.get_sender_id()}"

        group_cooldown = self._cfg_int(
            "image_search_group_cooldown_seconds",
            0,
        )
        user_cooldown = self._cfg_int(
            "image_search_user_cooldown_seconds",
            0,
        )
        last_group = self._last_image_search_group.get(session_key)
        last_user = self._last_image_search_user.get(user_key)

        remaining = 0.0
        if last_group is not None:
            remaining = max(remaining, group_cooldown - (now - last_group))
        if last_user is not None:
            remaining = max(remaining, user_cooldown - (now - last_user))
        if remaining > 0:
            return max(1, int(math.ceil(remaining)))

        # 在发起网络请求前占用冷却，防止并发命令刷屏。
        self._last_image_search_group[session_key] = now
        self._last_image_search_user[user_key] = now
        return 0

    def _build_bing_image_search_url(self, keyword: str) -> str:
        result_limit = max(
            1,
            min(5, self._cfg_int("image_search_result_count", 1)),
        )
        fetch_count = min(35, max(10, result_limit * 8))
        safe_mode = self._cfg_str("image_search_safe_search").strip().lower()
        if safe_mode not in {"strict", "moderate", "off"}:
            safe_mode = "strict"
        params = urllib.parse.urlencode(
            {
                "q": keyword,
                "form": "HDRSC2",
                "first": "1",
                "count": str(fetch_count),
                "adlt": safe_mode,
            }
        )
        return f"{BING_IMAGES_ENDPOINT}?{params}"

    async def _fetch_image_search_html(self, search_url: str) -> str:
        timeout = self._cfg_int("image_search_timeout_seconds", 1)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._fetch_image_search_html_sync,
                    search_url,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise

    def _open_bounded_http(self, request: urllib.request.Request, timeout: int):
        """Open one HTTP request with bounded redirects and safe targets."""

        opener = urllib.request.build_opener(_LimitedSafeRedirectHandler())
        return opener.open(request, timeout=timeout)

    def _fetch_image_search_html_sync(self, search_url: str) -> str:
        """Blocking HTTP GET; called through asyncio.to_thread."""

        timeout = self._cfg_int("image_search_timeout_seconds", 1)
        request = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": IMAGE_SEARCH_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        with self._open_bounded_http(request, timeout) as response:
            final_url = response.geturl()
            if not self._sanitize_http_url(
                final_url,
                require_image_extension=False,
            ):
                raise ImageSearchError("图片搜索被重定向到非安全 HTTP(S) 地址")
            return self._read_search_response(response)

    def _read_search_response(self, response: Any, *, expected: str = "html") -> str:
        max_bytes = self._cfg_int(
            "image_search_max_response_bytes",
            1024,
        )
        headers = getattr(response, "headers", None)
        content_type = ""
        content_length = ""
        if headers is not None and hasattr(headers, "get"):
            content_type = str(headers.get("Content-Type", "") or "")
            content_length = str(headers.get("Content-Length", "") or "")

        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise ImageSearchError("图片搜索响应超过大小限制")
            except ValueError:
                pass

        # 多读 1 字节用于判断是否超限；绝不无限 read()。
        data = response.read(max_bytes + 1)
        if data is None:
            data = b""
        if len(data) > max_bytes:
            raise ImageSearchError("图片搜索响应超过大小限制")

        lowered_type = content_type.lower()
        if lowered_type:
            if expected == "json":
                if (
                    "json" not in lowered_type
                    and not lowered_type.startswith("text/")
                ):
                    raise ImageSearchError("百度图片搜索返回的 Content-Type 不是 JSON")
            elif (
                "html" not in lowered_type
                and not lowered_type.startswith("text/")
            ):
                raise ImageSearchError("图片搜索返回的 Content-Type 不是 HTML")

        charset = "utf-8"
        match = re.search(r"charset=([\w.+-]+)", content_type, re.IGNORECASE)
        if match:
            charset = match.group(1)
        else:
            head = data[:2048].decode("ascii", errors="ignore")
            meta_match = re.search(
                r"<meta[^>]+charset=[\"']?([\w.+-]+)",
                head,
                re.IGNORECASE,
            )
            if meta_match:
                charset = meta_match.group(1)
        try:
            return data.decode(charset, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")

    def _image_search_result_limit(self) -> int:
        return max(
            1,
            min(
                IMAGE_SEARCH_MAX_RESULTS_PER_COMMAND,
                self._cfg_int("image_search_result_count", 1),
            ),
        )

    def _image_search_candidate_limit(self) -> int:
        return min(
            IMAGE_SEARCH_MAX_CANDIDATE_URLS,
            max(6, self._image_search_result_limit() * 3),
        )

    def _build_baidu_image_search_url(self, keyword: str) -> str:
        fetch_count = min(
            30,
            max(10, self._image_search_result_limit() * 8),
        )
        params = {
            "tn": "resultjson_com",
            "ipn": "rj",
            "ct": "201326592",
            "is": "",
            "fp": "result",
            "queryWord": keyword,
            "cl": "2",
            "lm": "-1",
            "ie": "utf-8",
            "oe": "utf-8",
            "adpicid": "",
            "st": "-1",
            "z": "",
            "ic": "0",
            "hd": "",
            "latest": "",
            "copyright": "",
            "word": keyword,
            "s": "",
            "se": "",
            "tab": "",
            "width": "",
            "height": "",
            "face": "0",
            "istype": "2",
            "qc": "",
            "nc": "1",
            "fr": "",
            "expermode": "",
            "nojc": "",
            "pn": "0",
            "rn": str(fetch_count),
            "gsm": "1e",
        }
        return f"{BAIDU_IMAGES_ENDPOINT}?{urllib.parse.urlencode(params)}"

    async def _fetch_baidu_image_json(self, search_url: str) -> str:
        timeout = self._cfg_int("image_search_timeout_seconds", 1)
        return await asyncio.wait_for(
            asyncio.to_thread(
                self._fetch_baidu_image_json_sync,
                search_url,
            ),
            timeout=timeout,
        )

    def _fetch_baidu_image_json_sync(self, search_url: str) -> str:
        timeout = self._cfg_int("image_search_timeout_seconds", 1)
        request = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": IMAGE_SEARCH_USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": BAIDU_IMAGES_REFERER,
                "Cache-Control": "no-cache",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        with self._open_bounded_http(request, timeout) as response:
            final_url = response.geturl()
            if not self._sanitize_http_url(
                final_url,
                require_image_extension=False,
            ):
                raise ImageSearchError("百度图片搜索被重定向到非安全 HTTP(S) 地址")
            return self._read_search_response(response, expected="json")

    async def _search_image_candidates(self, keyword: str) -> list[dict[str, str]]:
        result_limit = self._image_search_result_limit()
        candidate_limit = self._image_search_candidate_limit()
        candidates: list[dict[str, str]] = []
        errors: list[BaseException] = []
        bing_ok = False
        baidu_ok = False

        try:
            bing_url = self._build_bing_image_search_url(keyword)
            bing_page = await self._fetch_image_search_html(bing_url)
            bing_urls = self._parse_bing_image_urls(
                bing_page,
                limit=candidate_limit,
            )
            bing_ok = True
            candidates.extend(
                {
                    "url": url,
                    "referer": BING_IMAGES_REFERER,
                    "source": "bing",
                }
                for url in bing_urls
            )
        except Exception as exc:
            errors.append(exc)
            logger.warning(
                "qqbot_enhancer Bing 图片搜索失败，尝试第二搜索源",
                exc_info=True,
            )

        # 第二搜索源：Baidu Images JSON。只在 Bing 结果不足时请求，避免双重流量。
        if (
            len(candidates) < result_limit
            and self._cfg_bool("image_search_second_source_enabled")
        ):
            try:
                baidu_url = self._build_baidu_image_search_url(keyword)
                baidu_payload = await self._fetch_baidu_image_json(baidu_url)
                baidu_urls = self._parse_baidu_image_urls(
                    baidu_payload,
                    limit=candidate_limit,
                )
                baidu_ok = True
                existing = {item["url"] for item in candidates}
                for url in baidu_urls:
                    if url in existing:
                        continue
                    existing.add(url)
                    candidates.append(
                        {
                            "url": url,
                            "referer": BAIDU_IMAGES_REFERER,
                            "source": "baidu",
                        }
                    )
            except Exception as exc:
                errors.append(exc)
                logger.warning(
                    "qqbot_enhancer 百度图片搜索失败",
                    exc_info=True,
                )

        if not candidates and not bing_ok and not baidu_ok:
            if any(isinstance(error, asyncio.TimeoutError) for error in errors):
                raise asyncio.TimeoutError
            raise ImageSearchError("所有图片搜索源均请求失败")
        return candidates[:candidate_limit]

    async def _search_image_urls(self, keyword: str) -> list[str]:
        candidates = await self._search_image_candidates(keyword)
        return [item["url"] for item in candidates]

    async def _build_image_search_result(
        self,
        event: AstrMessageEvent,
        keyword: str,
        *,
        result_count: int | None = None,
    ):
        """Shared search/download/send pipeline for command and NL intents."""

        if result_count is None:
            result_limit = self._image_search_result_limit()
        else:
            result_limit = max(
                1,
                min(
                    IMAGE_SEARCH_MAX_RESULTS_PER_COMMAND,
                    int(result_count),
                ),
            )

        try:
            candidates = await self._search_image_candidates(keyword)
        except asyncio.TimeoutError:
            logger.warning("qqbot_enhancer 图片搜索超时: %s", keyword)
            return event.plain_result(self._cfg_str("image_search_timeout_reply"))
        except Exception:
            logger.warning(
                "qqbot_enhancer 图片搜索失败: %s",
                keyword,
                exc_info=True,
            )
            return event.plain_result(self._cfg_str("image_search_error_reply"))

        if not candidates:
            return event.plain_result(self._cfg_str("image_search_empty_reply"))

        local_paths, remote_urls = await self._download_search_image_files(
            event,
            candidates,
            result_limit=result_limit,
        )
        image_components: list[Any] = []
        for local_path in local_paths:
            try:
                image_components.append(
                    Comp.Image.fromFileSystem(str(local_path))
                )
            except Exception:
                logger.warning(
                    "qqbot_enhancer 本地图片组件构造失败: %s",
                    local_path,
                    exc_info=True,
                )
        if not image_components:
            # 远程 URL 兜底：本地下载全部失败时仍尝试让协议端加载。
            for image_url in remote_urls[:result_limit]:
                try:
                    image_components.append(Comp.Image.fromURL(image_url))
                except Exception:
                    logger.warning(
                        "qqbot_enhancer 跳过无法构造的图片 URL: %s",
                        image_url,
                    )
        if not image_components:
            return event.plain_result(self._cfg_str("image_search_empty_reply"))
        return event.chain_result(
            [
                Comp.Plain(self._cfg_str("image_search_success_prefix")),
                *image_components,
            ]
        )

    def _parse_baidu_image_urls(
        self,
        payload: str | dict[str, Any],
        *,
        limit: int = 1,
    ) -> list[str]:
        """Extract image proxy/direct URLs from Baidu Images JSON."""

        result_limit = max(
            1,
            min(IMAGE_SEARCH_MAX_CANDIDATE_URLS, int(limit or 1)),
        )
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                return []
        if not isinstance(payload, dict):
            return []
        entries = payload.get("data", [])
        if not isinstance(entries, list):
            return []

        results: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            if len(results) >= result_limit:
                break
            if not isinstance(entry, dict):
                continue
            raw_candidates: list[Any] = [
                entry.get("middleURL"),
                entry.get("thumbURL"),
                entry.get("hoverURL"),
            ]
            replace_urls = entry.get("replaceUrl", [])
            if isinstance(replace_urls, list):
                for replacement in replace_urls:
                    if not isinstance(replacement, dict):
                        continue
                    raw_candidates.extend(
                        [
                            replacement.get("ObjURL"),
                            replacement.get("ObjUrl"),
                        ]
                    )
            for raw_candidate in raw_candidates:
                safe_url = self._sanitize_http_url(
                    raw_candidate,
                    require_image_extension=False,
                )
                if not safe_url or safe_url in seen:
                    continue
                seen.add(safe_url)
                results.append(safe_url)
                break

        return results

    async def _download_search_image_files(
        self,
        event: AstrMessageEvent,
        candidates: list[dict[str, str]],
        *,
        result_limit: int,
    ) -> tuple[list[str], list[str]]:
        """Download candidates to bounded temp files; return paths and URL fallbacks."""

        local_paths: list[str] = []
        remote_urls: list[str] = []
        seen_remote: set[str] = set()
        for candidate in candidates:
            url = str(candidate.get("url", "") or "")
            if not url or url in seen_remote:
                continue
            seen_remote.add(url)
            remote_urls.append(url)

        max_attempts = min(
            len(candidates),
            max(1, self._cfg_int("image_search_download_attempts", 1)),
        )
        for candidate in candidates[:max_attempts]:
            if len(local_paths) >= result_limit:
                break
            url = str(candidate.get("url", "") or "")
            referer = str(candidate.get("referer", "") or BING_IMAGES_REFERER)
            if not url:
                continue
            try:
                local_path = await self._download_image(url, referer=referer)
            except Exception:
                logger.warning(
                    "qqbot_enhancer 下载图片失败，尝试下一个候选: %s",
                    url,
                    exc_info=True,
                )
                continue
            local_paths.append(local_path)
            tracker = getattr(event, "track_temporary_local_file", None)
            if callable(tracker):
                try:
                    tracker(local_path)
                except Exception:
                    logger.debug(
                        "qqbot_enhancer 登记临时图片失败: %s",
                        local_path,
                        exc_info=True,
                    )
        return local_paths, remote_urls

    async def _download_image(self, image_url: str, *, referer: str) -> str:
        timeout = self._cfg_int("image_search_download_timeout_seconds", 1)
        return await asyncio.wait_for(
            asyncio.to_thread(
                self._download_image_sync,
                image_url,
                referer,
            ),
            timeout=timeout,
        )

    def _download_image_sync(self, image_url: str, referer: str) -> str:
        safe_url = self._sanitize_http_url(
            image_url,
            require_image_extension=False,
        )
        if not safe_url:
            raise ImageSearchError("图片下载 URL 不是安全的 HTTP(S) 地址")
        timeout = self._cfg_int("image_search_download_timeout_seconds", 1)
        max_bytes = self._cfg_int("image_search_download_max_bytes", 1024)
        request = urllib.request.Request(
            safe_url,
            headers={
                "User-Agent": IMAGE_SEARCH_USER_AGENT,
                "Accept": (
                    "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": referer or BING_IMAGES_REFERER,
                "Cache-Control": "no-cache",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        with self._open_bounded_http(request, timeout) as response:
            final_url = response.geturl()
            if not self._sanitize_http_url(
                final_url,
                require_image_extension=False,
            ):
                raise ImageSearchError("图片下载被重定向到非安全 HTTP(S) 地址")

            headers = getattr(response, "headers", None)
            content_type = ""
            content_length = ""
            if headers is not None and hasattr(headers, "get"):
                content_type = str(headers.get("Content-Type", "") or "")
                content_length = str(headers.get("Content-Length", "") or "")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise ImageSearchError("图片响应超过大小限制")
                except ValueError:
                    pass

            declared_type = content_type.split(";", 1)[0].strip().lower()
            if declared_type and not declared_type.startswith("image/") and (
                declared_type not in {"application/octet-stream", "binary/octet-stream"}
            ):
                raise ImageSearchError("图片响应 Content-Type 不是图片")
            if declared_type.startswith("image/") and declared_type not in {
                "image/jpeg",
                "image/jpg",
                "image/png",
                "image/gif",
                "image/webp",
                "image/bmp",
                "image/x-ms-bmp",
                "image/avif",
            }:
                raise ImageSearchError("不支持的图片 Content-Type")

            data = response.read(max_bytes + 1)
            if data is None:
                data = b""
            if not data:
                raise ImageSearchError("图片响应为空")
            if len(data) > max_bytes:
                raise ImageSearchError("图片响应超过大小限制")
            detected = self._detect_image_magic(data)
            if detected is None:
                raise ImageSearchError("下载内容不是受支持的图片格式")
            _, suffix = detected

        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                prefix="qqbot_enhancer_img_",
                suffix=suffix,
                delete=False,
            ) as temporary:
                temp_path = temporary.name
                temporary.write(data)
            return temp_path
        except Exception:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    @staticmethod
    def _detect_image_magic(data: bytes) -> tuple[str, str] | None:
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", ".jpg"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", ".png"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif", ".gif"
        if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp", ".webp"
        if data.startswith(b"BM"):
            return "image/bmp", ".bmp"
        if len(data) >= 12 and data[4:12] in {b"ftypavif", b"ftypavis"}:
            return "image/avif", ".avif"
        return None

    def _parse_bing_image_urls(self, page: str, *, limit: int = 1) -> list[str]:
        """Extract image URLs from Bing Images HTML without third-party libs.

        Bing renders result links as ``<a class="iusc" m="{...json...}">`` and
        the JSON contains ``murl``. Some responses omit the wrapper, so there
        are two conservative fallbacks. Every result is filtered to a public
        HTTP(S) URL with an image-like path before it can reach a message
        component.
        """

        result_limit = max(
            1,
            min(IMAGE_SEARCH_MAX_CANDIDATE_URLS, int(limit or 1)),
        )
        scan_limit = min(
            IMAGE_SEARCH_MAX_CANDIDATES,
            max(50, result_limit * 20),
        )
        candidates: list[str] = []

        # Primary parser: the m="..." JSON payload.
        for match in re.finditer(r'\bm="([^"]+)"', page, re.IGNORECASE):
            if len(candidates) >= scan_limit:
                break
            raw = html_lib.unescape(match.group(1))
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            murl = payload.get("murl")
            if isinstance(murl, str) and murl.strip():
                candidates.append(murl)

        decoded_page = html_lib.unescape(page)

        # Fallback 1: a JSON "murl":"..." fragment without the anchor wrapper.
        if len(candidates) < scan_limit:
            murl_pattern = re.compile(
                r'"murl"\s*:\s*"((?:\\.|[^"\\])*)"',
                re.IGNORECASE,
            )
            for match in murl_pattern.finditer(decoded_page):
                if len(candidates) >= scan_limit:
                    break
                encoded = match.group(1)
                try:
                    url = json.loads(f'"{encoded}"')
                except (TypeError, ValueError):
                    url = encoded.replace("\\/", "/")
                if isinstance(url, str) and url.strip():
                    candidates.append(url)

        # Fallback 2: image-looking absolute URLs anywhere in the HTML.
        if len(candidates) < scan_limit:
            for match in re.finditer(
                r'https?://[^\s"\'<>\\]+',
                decoded_page,
                re.IGNORECASE,
            ):
                if len(candidates) >= scan_limit:
                    break
                candidate = match.group(0).rstrip(".,);]}\\")
                if self._has_image_path(candidate):
                    candidates.append(candidate)

        results: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            safe_url = self._sanitize_http_url(
                candidate,
                require_image_extension=True,
            )
            if not safe_url or safe_url in seen:
                continue
            seen.add(safe_url)
            results.append(safe_url)
            if len(results) >= result_limit:
                break
        return results

    @staticmethod
    def _has_image_path(url: str) -> bool:
        try:
            path = urllib.parse.urlsplit(url).path.lower()
        except ValueError:
            return False
        return any(path.endswith(extension) for extension in IMAGE_PATH_EXTENSIONS)

    def _sanitize_http_url(
        self,
        value: Any,
        *,
        require_image_extension: bool,
    ) -> str:
        if not isinstance(value, str):
            return ""
        raw = html_lib.unescape(value).strip()
        if not raw:
            return ""
        if raw.startswith("//"):
            raw = "https:" + raw
        if "\\" in raw or any(ord(char) < 32 for char in raw):
            return ""
        try:
            parsed = urllib.parse.urlsplit(raw)
        except ValueError:
            return ""
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            return ""
        if not parsed.netloc or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        try:
            port = parsed.port
        except ValueError:
            return ""
        if port is not None and not (0 < port <= 65535):
            return ""

        host = parsed.hostname.rstrip(".").lower()
        if not host:
            return ""
        if host in {"localhost", "metadata.google.internal"} or host.endswith(
            (".localhost", ".local", ".internal", ".lan", ".home")
        ):
            return ""
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # 要求公网域名至少包含一个点，拒绝裸主机名/内网别名。
            if "." not in host:
                return ""
        else:
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return ""

        if require_image_extension:
            path = parsed.path.lower()
            if not any(path.endswith(extension) for extension in IMAGE_PATH_EXTENSIONS):
                return ""
        return urllib.parse.urlunsplit(
            (scheme, parsed.netloc, parsed.path, parsed.query, "")
        )

    # ------------------------------------------------------------------
    # Local songs / voice notes
    # ------------------------------------------------------------------
    def _extract_local_song_keyword(self, event: AstrMessageEvent) -> str:
        text = str(getattr(event, "message_str", "") or "").strip()
        for prefix in self._command_prefixes(event):
            if prefix and text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        for command_name in ("唱歌", "点歌"):
            if text == command_name:
                return ""
            if text.startswith(command_name):
                rest = text[len(command_name) :]
                if rest and rest[0].isspace():
                    keyword = re.sub(r"\s+", " ", rest).strip()
                    keyword = "".join(
                        char for char in keyword if char.isprintable()
                    ).strip()
                    return keyword
        return ""

    def _song_cooldown_remaining(self, event: AstrMessageEvent) -> int:
        now = self._clock()
        self._prune_state(now)
        session_key = event.unified_msg_origin
        user_key = f"{session_key}:{event.get_sender_id()}"

        group_cooldown = self._cfg_int("song_group_cooldown_seconds", 0)
        user_cooldown = self._cfg_int("song_user_cooldown_seconds", 0)
        remaining = 0.0
        last_group = self._last_song_group.get(session_key)
        last_user = self._last_song_user.get(user_key)
        if last_group is not None:
            remaining = max(remaining, group_cooldown - (now - last_group))
        if last_user is not None:
            remaining = max(remaining, user_cooldown - (now - last_user))
        if remaining > 0:
            return max(1, int(math.ceil(remaining)))

        self._last_song_group[session_key] = now
        self._last_song_user[user_key] = now
        return 0

    def _resolve_song_file(self, filename: str) -> Path | None:
        if not filename:
            return None
        try:
            base = self._song_dir.resolve()
            raw_candidate = base / filename
            if raw_candidate.is_symlink():
                return None
            candidate = raw_candidate.resolve()
            if candidate.parent != base:
                return None
            if candidate.suffix.lower() not in SONG_SUFFIXES:
                return None
            if not candidate.is_file():
                return None
            return candidate
        except (OSError, ValueError):
            return None

    def _list_song_files(self) -> tuple[list[Path], bool]:
        try:
            base = self._song_dir.resolve()
            entries = sorted(base.iterdir(), key=lambda path: path.name.casefold())
        except (OSError, ValueError):
            return [], False

        max_bytes = self._cfg_int("song_max_file_bytes", 1)
        song_files: list[Path] = []
        has_oversized = False
        for entry in entries:
            song_file = self._resolve_song_file(entry.name)
            if song_file is None:
                continue
            try:
                size = song_file.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue
            if size > max_bytes:
                has_oversized = True
                logger.warning(
                    "qqbot_enhancer 本地音频超过大小限制: %s (%s > %s)",
                    song_file,
                    size,
                    max_bytes,
                )
                continue
            song_files.append(song_file)
        return song_files, has_oversized

    # ------------------------------------------------------------------
    # Stickers
    # ------------------------------------------------------------------
    def _load_sticker_entries(self) -> None:
        """Load sticker_rules.json and fall back to directory scan on errors."""

        files = self._all_sticker_files()
        try:
            payload = json.loads(STICKER_RULES_PATH.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "qqbot_enhancer 未找到或无法解析 stickers/sticker_rules.json，"
                "将仅按随机方式使用目录中的图片",
                exc_info=True,
            )
            self._sticker_entries = [
                {"file": path.name, "path": path, "keywords": [], "emotion": ""}
                for path in files
            ]
            return

        stickers = payload.get("stickers", []) if isinstance(payload, dict) else []
        raw_emotions = (
            payload.get("emotion_keywords", {}) if isinstance(payload, dict) else {}
        )
        if isinstance(raw_emotions, dict):
            for emotion, words in raw_emotions.items():
                if isinstance(words, list):
                    normalized = tuple(
                        str(word).casefold() for word in words if str(word).strip()
                    )
                    if normalized:
                        self._emotion_keywords[str(emotion)] = normalized

        entries: list[dict[str, Any]] = []
        known_names: set[str] = set()
        if isinstance(stickers, list):
            for item in stickers:
                if not isinstance(item, dict):
                    continue
                path = self._resolve_sticker_file(str(item.get("file", "")))
                if path is None:
                    continue
                keywords = item.get("keywords", [])
                if isinstance(keywords, str):
                    keywords = [keywords]
                if not isinstance(keywords, list):
                    keywords = []
                entries.append(
                    {
                        "file": path.name,
                        "path": path,
                        "keywords": [
                            str(keyword).casefold()
                            for keyword in keywords
                            if str(keyword).strip()
                        ],
                        "emotion": str(item.get("emotion", "") or "").strip(),
                    }
                )
                known_names.add(path.name)

        # 目录中存在但 manifest 未列出的图片也参与随机兜底选择。
        for path in files:
            if path.name not in known_names:
                entries.append(
                    {
                        "file": path.name,
                        "path": path,
                        "keywords": [],
                        "emotion": "",
                    }
                )
        self._sticker_entries = entries

    def _all_sticker_files(self) -> list[Path]:
        try:
            return sorted(
                path
                for path in STICKER_DIR.iterdir()
                if path.is_file() and path.suffix.lower() in STICKER_SUFFIXES
            )
        except OSError:
            return []

    @staticmethod
    def _resolve_sticker_file(filename: str) -> Path | None:
        if not filename:
            return None
        try:
            base = STICKER_DIR.resolve()
            candidate = (base / filename).resolve()
            if candidate.parent != base:
                return None
            if candidate.suffix.lower() not in STICKER_SUFFIXES:
                return None
            if not candidate.is_file():
                return None
            return candidate
        except (OSError, ValueError):
            return None

    def _sticker_ai_available_tags(self) -> dict[str, list[Path]]:
        tags: dict[str, list[Path]] = {}
        if not self._cfg_bool("sticker_enabled"):
            return tags
        for entry in self._sticker_entries:
            path = entry.get("path")
            emotion = str(entry.get("emotion", "") or "").strip().casefold()
            if (
                isinstance(path, Path)
                and path.is_file()
                and STICKER_AI_TAG_RE.fullmatch(emotion)
            ):
                tags.setdefault(emotion, []).append(path)
        if self._cfg_bool("collected_sticker_enabled"):
            for entry in self._valid_collected_entries():
                path = entry.get("path")
                category = str(
                    entry.get("category", DEFAULT_COLLECTED_CATEGORY)
                ).strip().casefold()
                if (
                    isinstance(path, Path)
                    and path.is_file()
                    and STICKER_AI_TAG_RE.fullmatch(category)
                ):
                    tags.setdefault(category, []).append(path)
        return tags

    def _sticker_ai_system_instruction(self) -> str:
        if self._memory_mode() != "direct":
            return ""
        if not (
            self._cfg_bool("sticker_ai_select_enabled")
            and self._cfg_bool("sticker_ai_marker_enabled")
        ):
            return ""
        tags = sorted(self._sticker_ai_available_tags())
        if not tags:
            return ""
        # Keep the injected prompt short and only expose safe tags, never paths.
        visible_tags = ", ".join(tags[:20])
        return (
            f"可选表情标签：{visible_tags}。"
            "仅在确实贴切时，于回复末尾原样附加一个"
            "[[sticker:标签]] 标记；不需要就别输出标记，不要输出多个，不要解释标记。"
        )

    def _effective_system_prompt(self) -> str:
        prompt = self._cfg_str("system_prompt")
        instruction = self._sticker_ai_system_instruction()
        return f"{prompt}\n{instruction}" if instruction else prompt

    def _ai_marker_mode_active(self) -> bool:
        return bool(
            self._memory_mode() == "direct"
            and self._cfg_bool("sticker_ai_select_enabled")
            and self._cfg_bool("sticker_ai_marker_enabled")
            and self._sticker_ai_available_tags()
        )

    def _extract_sticker_ai_marker(
        self,
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

    def _resolve_sticker_ai_tag(
        self,
        tag: str | None,
        session_key: str,
    ) -> Path | None:
        if not tag:
            return None
        normalized = tag.strip().casefold()
        if not STICKER_AI_TAG_RE.fullmatch(normalized):
            return None
        candidates = self._sticker_ai_available_tags().get(normalized, [])
        if not candidates:
            return None
        if self._rng.random() >= self._cfg_float(
            "sticker_ai_select_probability",
            0.0,
            1.0,
        ):
            return None

        now = self._clock()
        cooldown = self._cfg_int("sticker_cooldown_seconds", 0)
        last_sent = self._last_sticker_at.get(session_key)
        if last_sent is not None and now - last_sent < cooldown:
            return None
        if self._cfg_bool("sticker_prevent_repeat"):
            last_file = self._last_sticker_file.get(session_key)
            if last_file and len(candidates) > 1:
                non_repeating = [
                    candidate
                    for candidate in candidates
                    if candidate.name != last_file
                ]
                if non_repeating:
                    candidates = non_repeating
        chosen = self._rng.choice(candidates)
        self._last_sticker_at[session_key] = now
        self._last_sticker_file[session_key] = chosen.name
        return chosen

    def _pick_user_sticker_reaction_reply(self) -> str:
        replies = self._cfg_list("user_sticker_reaction_replies")
        if not replies:
            replies = list(DEFAULTS["user_sticker_reaction_replies"])
        return str(self._rng.choice(replies))

    def _build_result(
        self,
        event: AstrMessageEvent,
        reply_text: str,
        message_text: str,
        *,
        allow_random_sticker: bool = True,
    ):
        clean_text, ai_tag, had_marker = self._extract_sticker_ai_marker(
            reply_text
        )
        reply_text = self._finalize_reply(clean_text)

        if had_marker:
            # A model-chosen/private marker must never leak into user-visible
            # text. Only honor it when explicitly enabled; otherwise return the
            # cleaned text without a random fallback sticker.
            sticker_path = None
            if (
                self._cfg_bool("sticker_ai_select_enabled")
                and self._cfg_bool("sticker_ai_marker_enabled")
                and self._memory_mode() == "direct"
            ):
                sticker_path = self._resolve_sticker_ai_tag(
                    ai_tag,
                    event.unified_msg_origin,
                )
            if sticker_path is not None:
                try:
                    return event.chain_result(
                        [
                            Comp.Plain(reply_text),
                            Comp.Image.fromFileSystem(str(sticker_path)),
                        ]
                    )
                except Exception:
                    logger.warning(
                        "qqbot_enhancer AI 标签表情组装失败，仅返回文本",
                        exc_info=True,
                    )
            return event.plain_result(reply_text)

        if not allow_random_sticker:
            # In direct marker mode the model decides whether to emit a tag.
            # No tag means it chose not to send a sticker; do not add a random
            # fallback that would override that decision.
            return event.plain_result(reply_text)

        sticker_path = self._maybe_pick_sticker(event, message_text)
        if sticker_path is not None:
            try:
                chain = [
                    Comp.Plain(reply_text),
                    Comp.Image.fromFileSystem(str(sticker_path)),
                ]
                return event.chain_result(chain)
            except Exception:
                logger.warning(
                    "qqbot_enhancer 组装本地表情包失败，降级为纯文本",
                    exc_info=True,
                )
        return event.plain_result(reply_text)

    def _maybe_pick_sticker(self, event: AstrMessageEvent, message_text: str) -> Path | None:
        if not self._cfg_bool("sticker_enabled"):
            return None

        collected_available = (
            self._cfg_bool("collected_sticker_enabled")
            and bool(self._valid_collected_entries())
        )
        if not self._sticker_entries and not collected_available:
            return None

        session_key = event.unified_msg_origin
        now = self._clock()
        cooldown = self._cfg_int("sticker_cooldown_seconds", 0)
        last_sent = self._last_sticker_at.get(session_key)
        if last_sent is not None and now - last_sent < cooldown:
            return None
        if self._rng.random() >= self._cfg_float("sticker_probability", 0.0, 1.0):
            return None

        try:
            chosen: Path | None = None
            use_collected = collected_available and (
                self._rng.random()
                < self._cfg_float(
                    "collected_sticker_use_probability",
                    0.0,
                    1.0,
                )
            )
            if use_collected:
                chosen = self._select_collected_sticker(
                    message_text,
                    session_key,
                )
            if chosen is None:
                chosen = self._select_sticker(message_text, session_key)
            if chosen is None and collected_available:
                chosen = self._select_collected_sticker(
                    message_text,
                    session_key,
                )
        except Exception:
            logger.warning("qqbot_enhancer 选择本地表情包失败", exc_info=True)
            return None
        if chosen is None:
            return None

        # 选中后再占用冷却，图片缺失时不会误占冷却。
        self._last_sticker_at[session_key] = now
        self._last_sticker_file[session_key] = chosen.name
        return chosen

    def _select_sticker(self, message_text: str, session_key: str) -> Path | None:
        valid_entries = [
            entry
            for entry in self._sticker_entries
            if isinstance(entry.get("path"), Path) and entry["path"].is_file()
        ]
        if not valid_entries:
            return None

        text = message_text.casefold()
        keyword_matches: list[dict[str, Any]] = []
        for entry in valid_entries:
            if any(keyword and keyword in text for keyword in entry.get("keywords", [])):
                keyword_matches.append(entry)

        if keyword_matches:
            candidates = keyword_matches
        else:
            emotion_matches: list[dict[str, Any]] = []
            for emotion, keywords in self._emotion_keywords.items():
                if any(keyword in text for keyword in keywords):
                    emotion_matches.extend(
                        entry
                        for entry in valid_entries
                        if entry.get("emotion") == emotion
                    )
            # 按文件去重，保持 manifest 顺序。
            candidates = list(
                {str(entry["path"]): entry for entry in emotion_matches}.values()
            )
            if not candidates:
                candidates = valid_entries

        # 同一会话尽量不连续发送同一张图。
        if self._cfg_bool("sticker_prevent_repeat"):
            last_file = self._last_sticker_file.get(session_key)
            if last_file and len(candidates) > 1:
                non_repeating = [
                    entry for entry in candidates if entry.get("file") != last_file
                ]
                if non_repeating:
                    candidates = non_repeating

        if not candidates:
            return None
        return self._rng.choice(candidates)["path"]

    # ------------------------------------------------------------------
    # Collected user stickers
    # ------------------------------------------------------------------
    def _resolve_collected_sticker_root(self) -> Path | None:
        try:
            plugin_name = str(getattr(self, "name", "") or "qqbot_enhancer")
            data_dir = Path(StarTools.get_data_dir(plugin_name=plugin_name)).resolve()
            raw_root = data_dir / COLLECTED_STICKER_DIR_NAME
            if raw_root.is_symlink():
                logger.warning("qqbot_enhancer 收藏目录不能是符号链接")
                return None
            raw_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            root = raw_root.resolve()
            if root.parent != data_dir:
                logger.warning("qqbot_enhancer 收藏目录越出插件数据目录")
                return None
            return root
        except Exception:
            logger.warning(
                "qqbot_enhancer 无法创建用户收藏表情数据目录",
                exc_info=True,
            )
            return None

    def _collected_index_path(self) -> Path | None:
        if self._collected_root is None:
            return None
        return self._collected_root / COLLECTED_INDEX_FILENAME

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".tmp-",
                dir=str(path.parent),
                delete=False,
            ) as temporary:
                temp_path = temporary.name
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temp_path, path)
        except Exception:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def _save_collected_sticker_index(self) -> None:
        index_path = self._collected_index_path()
        if index_path is None:
            raise RuntimeError("收藏数据目录不可用")
        serializable = []
        for entry in self._collected_entries:
            serializable.append(
                {
                    "sha256": str(entry.get("sha256", "")),
                    "filename": str(entry.get("filename", "")),
                    "category": str(entry.get("category", DEFAULT_COLLECTED_CATEGORY)),
                    "size": int(entry.get("size", 0)),
                    "created_at": int(entry.get("created_at", 0)),
                    "source": str(entry.get("source", "")),
                }
            )
        payload = {
            "version": 1,
            "saved_at": int(time.time()),
            "stickers": serializable,
        }
        self._atomic_write_bytes(
            index_path,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def _normalize_collected_category(self, raw: Any) -> str | None:
        value = str(raw or "").strip()
        if not value:
            return DEFAULT_COLLECTED_CATEGORY
        max_chars = min(
            COLLECTED_CATEGORY_MAX_CHARS,
            max(1, self._cfg_int("collected_sticker_category_max_chars", 1)),
        )
        if len(value) > max_chars:
            return None
        if value in {".", ".."}:
            return None
        if not COLLECTED_CATEGORY_RE.fullmatch(value):
            return None
        return value

    def _collected_category_dir(
        self,
        category: str,
        *,
        create: bool = False,
    ) -> Path | None:
        if self._collected_root is None or not category:
            return None
        try:
            base = self._collected_root.resolve()
            raw_dir = base / category
            if raw_dir.is_symlink():
                return None
            if create:
                raw_dir.mkdir(parents=False, exist_ok=True, mode=0o700)
            if not raw_dir.is_dir():
                return None
            resolved = raw_dir.resolve()
            if resolved.parent != base:
                return None
            return resolved
        except (OSError, ValueError):
            return None

    def _collected_file_path_for(
        self,
        category: str,
        filename: str,
    ) -> Path | None:
        if not COLLECTED_FILENAME_RE.fullmatch(filename):
            return None
        category_dir = self._collected_category_dir(category)
        if category_dir is None:
            return None
        candidate = category_dir / filename
        if candidate.is_symlink():
            return None
        try:
            resolved = candidate.resolve()
            if resolved.parent != category_dir or not resolved.is_file():
                return None
            return resolved
        except (OSError, ValueError):
            return None

    def _valid_collected_entries(self) -> list[dict[str, Any]]:
        max_bytes = self._cfg_int("collected_sticker_max_file_bytes", 1)
        valid: list[dict[str, Any]] = []
        for entry in self._collected_entries:
            category = self._normalize_collected_category(entry.get("category"))
            filename = str(entry.get("filename", ""))
            if category is None or category != entry.get("category"):
                continue
            path = self._collected_file_path_for(category, filename)
            if path is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > max_bytes:
                continue
            normalized = dict(entry)
            normalized["path"] = path
            normalized["size"] = size
            valid.append(normalized)
        return valid

    def _load_collected_sticker_index(self) -> None:
        self._collected_entries = []
        index_path = self._collected_index_path()
        if index_path is None or not index_path.is_file():
            return
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(
                "qqbot_enhancer 收藏表情 index.json 无法解析，本次从空列表启动",
                exc_info=True,
            )
            return
        raw_entries = payload.get("stickers", []) if isinstance(payload, dict) else []
        if not isinstance(raw_entries, list):
            return
        max_bytes = self._cfg_int("collected_sticker_max_file_bytes", 1)
        loaded: list[dict[str, Any]] = []
        seen_sha: set[str] = set()
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            sha256 = str(item.get("sha256", "") or "").lower()
            filename = str(item.get("filename", "") or "")
            category = self._normalize_collected_category(item.get("category"))
            if (
                not re.fullmatch(r"[a-f0-9]{64}", sha256)
                or category is None
                or not COLLECTED_FILENAME_RE.fullmatch(filename)
                or not filename.startswith(sha256 + ".")
                or sha256 in seen_sha
            ):
                continue
            path = self._collected_file_path_for(category, filename)
            if path is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > max_bytes:
                continue
            seen_sha.add(sha256)
            try:
                created_at = int(item.get("created_at", 0) or 0)
            except (TypeError, ValueError):
                created_at = 0
            loaded.append(
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": category,
                    "size": size,
                    "created_at": created_at,
                    "source": str(item.get("source", "") or ""),
                    "path": path,
                }
            )
        self._collected_entries = loaded

    def _format_collected_reply(self, template: str, **kwargs: Any) -> str:
        try:
            return str(template).format(**kwargs)
        except (KeyError, ValueError):
            return str(template)

    def _local_image_ref_path(self, ref: Any) -> Path | None:
        if not isinstance(ref, str) or not ref.strip():
            return None
        value = html_lib.unescape(ref).strip()
        if value.startswith("file://"):
            try:
                parsed = urllib.parse.urlsplit(value)
            except ValueError:
                return None
            if parsed.scheme != "file":
                return None
            return Path(urllib.parse.unquote(parsed.path))
        try:
            scheme = urllib.parse.urlsplit(value).scheme.lower()
        except ValueError:
            return None
        if scheme in {"http", "https", "data", "base64"}:
            return None
        return Path(value)

    def _prune_pending_user_media(self, now: float) -> None:
        ttl = self._cfg_int("pending_sticker_ttl_seconds", 1)
        for key, items in list(self._pending_user_media.items()):
            kept: list[dict[str, Any]] = []
            for item in items:
                try:
                    timestamp = float(item.get("timestamp", 0))
                except (TypeError, ValueError):
                    timestamp = 0
                if now - timestamp <= ttl:
                    kept.append(item)
            if kept:
                self._pending_user_media[key] = kept[
                    :PENDING_USER_MEDIA_MAX_COUNT
                ]
            else:
                self._pending_user_media.pop(key, None)
        if len(self._pending_user_media) > PENDING_USER_MEDIA_MAX_SESSIONS:
            oldest = sorted(
                self._pending_user_media.items(),
                key=lambda pair: max(
                    (
                        float(item.get("timestamp", 0))
                        for item in pair[1]
                    ),
                    default=0,
                ),
            )
            overflow = len(self._pending_user_media) - PENDING_USER_MEDIA_MAX_SESSIONS
            for key, _ in oldest[:overflow]:
                self._pending_user_media.pop(key, None)

    async def _remember_recent_user_media(
        self,
        event: AstrMessageEvent,
        components: list[Any],
    ) -> None:
        if not self._cfg_bool("collected_sticker_enabled"):
            return
        images = [comp for comp in components if isinstance(comp, Comp.Image)]
        if not images:
            return
        existing = list(
            self._pending_user_media.get(event.unified_msg_origin, [])
        )
        now = self._clock()
        max_bytes = self._cfg_int("collected_sticker_max_file_bytes", 1)
        for image in images[-PENDING_USER_MEDIA_MAX_COUNT:]:
            status, payload = await self._decode_image_component(
                image,
                max_bytes=max_bytes,
            )
            if status != "ok" or payload is None:
                continue
            cached = dict(payload)
            cached["timestamp"] = now
            existing = [
                item
                for item in existing
                if item.get("sha256") != cached.get("sha256")
            ]
            existing.insert(0, cached)
            existing = existing[:PENDING_USER_MEDIA_MAX_COUNT]
        # 仅内存保存每个会话最近最多 3 张图片，不写盘、不记录用户文字/用户 ID。
        if existing:
            self._pending_user_media[event.unified_msg_origin] = existing
        self._prune_pending_user_media(now)

    def _get_recent_user_media(
        self,
        unified_msg_origin: str,
    ) -> dict[str, Any] | None:
        now = self._clock()
        self._prune_pending_user_media(now)
        cached_items = self._pending_user_media.get(unified_msg_origin, [])
        return dict(cached_items[0]) if cached_items else None

    def _get_recent_user_media_list(
        self,
        unified_msg_origin: str,
    ) -> list[dict[str, Any]]:
        now = self._clock()
        self._prune_pending_user_media(now)
        return [
            dict(item)
            for item in self._pending_user_media.get(
                unified_msg_origin,
                [],
            )
        ]

    @staticmethod
    def _quoted_image_components(components: list[Any]) -> list[Any]:
        images: list[Any] = []
        for comp in components:
            if not isinstance(comp, Comp.Reply):
                continue
            chain = getattr(comp, "chain", None)
            if not isinstance(chain, (list, tuple)):
                continue
            images.extend(
                item for item in chain if isinstance(item, Comp.Image)
            )
        return images

    async def _decode_image_component(
        self,
        image: Any,
        *,
        max_bytes: int,
    ) -> tuple[str, dict[str, Any] | None]:
        for ref in (
            getattr(image, "path", None),
            getattr(image, "file", None),
            getattr(image, "url", None),
        ):
            local_path = self._local_image_ref_path(ref)
            if local_path is None:
                continue
            try:
                if local_path.is_file() and local_path.stat().st_size > max_bytes:
                    return "too_large", None
            except OSError:
                pass

        try:
            raw_base64 = await image.convert_to_base64()
        except Exception:
            logger.warning("qqbot_enhancer 收藏图片读取失败", exc_info=True)
            return "invalid_image", None
        if isinstance(raw_base64, bytes):
            data = raw_base64
        elif isinstance(raw_base64, str) and raw_base64.strip():
            encoded = raw_base64.strip()
            if encoded.startswith("data:"):
                encoded = encoded.split(",", 1)[-1]
            encoded = encoded.removeprefix("base64://")
            try:
                data = base64.b64decode(encoded, validate=True)
            except Exception:
                try:
                    data = base64.b64decode(encoded)
                except Exception:
                    return "invalid_image", None
        else:
            return "invalid_image", None

        if not data:
            return "invalid_image", None
        if len(data) > max_bytes:
            return "too_large", None
        detected = self._detect_image_magic(data)
        if detected is None:
            return "invalid_image", None
        mime, extension = detected
        return (
            "ok",
            {
                "data": data,
                "mime": mime,
                "extension": extension,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            },
        )

    async def _extract_collectible_image_payload(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str, dict[str, Any] | None]:
        components = event.get_messages()
        current_images = [
            comp for comp in components if isinstance(comp, Comp.Image)
        ]
        quoted_images = self._quoted_image_components(components)
        max_bytes = self._cfg_int("collected_sticker_max_file_bytes", 1)
        last_status = "no_image"
        for image in current_images + quoted_images:
            status, payload = await self._decode_image_component(
                image,
                max_bytes=max_bytes,
            )
            if status == "ok" and payload is not None:
                return "ok", payload
            last_status = status
        if current_images or quoted_images:
            return last_status, None

        cached = self._get_recent_user_media(event.unified_msg_origin)
        if cached is not None:
            return "ok", cached
        has_face = any(
            isinstance(comp, Comp.Face) for comp in components
        )
        return ("face" if has_face else "no_image"), None

    @staticmethod
    def _image_dimensions(data: bytes, mime: str) -> tuple[int, int] | None:
        try:
            if mime == "image/png" and len(data) >= 24:
                if data[12:16] != b"IHDR":
                    return None
                width = int.from_bytes(data[16:20], "big")
                height = int.from_bytes(data[20:24], "big")
                return width, height
            if mime == "image/gif" and len(data) >= 10:
                return (
                    int.from_bytes(data[6:8], "little"),
                    int.from_bytes(data[8:10], "little"),
                )
            if mime == "image/bmp" and len(data) >= 26:
                width = int.from_bytes(data[18:22], "little", signed=True)
                height = int.from_bytes(data[22:26], "little", signed=True)
                return abs(width), abs(height)
            if mime == "image/jpeg":
                offset = 2
                sof_markers = {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }
                while offset + 9 < len(data):
                    if data[offset] != 0xFF:
                        offset += 1
                        continue
                    marker = data[offset + 1]
                    offset += 2
                    if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                        continue
                    if offset + 2 > len(data):
                        return None
                    segment_length = int.from_bytes(
                        data[offset : offset + 2],
                        "big",
                    )
                    if segment_length < 2:
                        return None
                    if marker in sof_markers and offset + 7 <= len(data):
                        height = int.from_bytes(
                            data[offset + 3 : offset + 5],
                            "big",
                        )
                        width = int.from_bytes(
                            data[offset + 5 : offset + 7],
                            "big",
                        )
                        return width, height
                    offset += segment_length
                return None
            if mime == "image/webp" and len(data) >= 30:
                chunk_type = data[12:16]
                if chunk_type == b"VP8X":
                    width = 1 + int.from_bytes(data[24:27], "little")
                    height = 1 + int.from_bytes(data[27:30], "little")
                    return width, height
                if chunk_type == b"VP8 " and len(data) >= 30:
                    width = int.from_bytes(data[26:28], "little") & 0x3FFF
                    height = int.from_bytes(data[28:30], "little") & 0x3FFF
                    return width, height
            return None
        except (IndexError, ValueError):
            return None

    def _is_probable_sticker_payload(self, payload: dict[str, Any]) -> bool:
        data = payload.get("data")
        mime = str(payload.get("mime", ""))
        if not isinstance(data, bytes):
            return False
        max_file_bytes = self._cfg_int(
            "auto_collect_sticker_max_file_bytes",
            1,
        )
        if len(data) > max_file_bytes:
            return False
        dimensions = self._image_dimensions(data, mime)
        if dimensions is None:
            return False
        width, height = dimensions
        if width <= 0 or height <= 0:
            return False
        max_dimension = max(
            32,
            self._cfg_int("auto_collect_sticker_max_dimension", 32),
        )
        if max(width, height) > max_dimension:
            return False
        if max(width, height) / max(1, min(width, height)) > 2.5:
            return False
        # Small square-ish GIF/WebP are especially sticker-like; still bounded
        # by the same dimensions and file-size limits above.
        return True

    def _detect_natural_language_collect_intent(
        self,
        text: str,
    ) -> dict[str, Any] | None:
        value = re.sub(r"\s+", " ", str(text or "")).strip()
        value = value.strip("，。！？!?,.、:：;；\"'“”‘’")
        if not value:
            return None
        match = re.search(r"(?:收藏|保存)", value)
        if match is None:
            return None

        prefix = value[: match.start()].strip()
        prefix = re.sub(
            r"^(?:帮我|给我|请(?:你)?|麻烦(?:你)?|你|能|可以|(?:我)?(?:想|要))\s*",
            "",
            prefix,
        ).strip()
        prefix = re.sub(r"^(?:把)?\s*", "", prefix).strip()
        prefix = re.sub(
            r"^(?:这个|这张|这条|这|它)\s*",
            "",
            prefix,
        ).strip()
        # Anything left before the action means this is likely ordinary chat,
        # e.g. “我收藏了很多图片” or “这只很可爱收藏一下”.
        if prefix:
            return None

        rest = value[match.end() :].strip()
        rest = re.sub(
            r"(?:一下|起来|了|吧|啦|呀|哦|嘛|行吗|可以吗)+\s*$",
            "",
            rest,
        ).strip()
        rest = re.sub(r"^(?:来|去|到|为|分类[:：]?)\s*", "", rest).strip()
        rest = re.sub(
            r"^(?:了|一下|起来|这个|这张|这条|这|它)\s*",
            "",
            rest,
        ).strip()
        rest = re.sub(r"^(?:表情|图片|贴纸)\s*", "", rest).strip()
        rest = re.sub(r"(?:表情|图片|贴纸)\s*$", "", rest).strip()
        rest = rest.strip("，。！？!?,.、:：;；\"'“”‘’ ")

        category = self._normalize_collected_category(rest)
        if category is None:
            return None
        return {"category": category}

    async def _save_collected_payload(
        self,
        event: AstrMessageEvent,
        category: str,
        payload: dict[str, Any],
    ) -> tuple[str, str]:
        if self._collected_root is None:
            return (
                "storage",
                self._cfg_str("collected_sticker_storage_unavailable_reply"),
            )
        max_count = self._cfg_int("collected_sticker_max_count", 1)
        async with self._collected_lock:
            self._collected_entries = self._valid_collected_entries()
            duplicate = next(
                (
                    entry
                    for entry in self._collected_entries
                    if entry.get("sha256") == payload.get("sha256")
                ),
                None,
            )
            if duplicate is not None:
                return (
                    "duplicate",
                    self._format_collected_reply(
                        self._cfg_str("collected_sticker_duplicate_reply"),
                        category=duplicate.get(
                            "category",
                            DEFAULT_COLLECTED_CATEGORY,
                        ),
                    ),
                )
            if len(self._collected_entries) >= max_count:
                return (
                    "limit",
                    self._format_collected_reply(
                        self._cfg_str("collected_sticker_limit_reply"),
                        max_count=max_count,
                    ),
                )

            category_dir = self._collected_category_dir(category, create=True)
            if category_dir is None:
                return (
                    "storage",
                    self._cfg_str(
                        "collected_sticker_storage_unavailable_reply"
                    ),
                )
            filename = f"{payload['sha256']}{payload['extension']}"
            final_path = category_dir / filename
            if final_path.is_symlink():
                return (
                    "storage",
                    self._cfg_str(
                        "collected_sticker_storage_unavailable_reply"
                    ),
                )
            if final_path.exists():
                return (
                    "duplicate",
                    self._format_collected_reply(
                        self._cfg_str("collected_sticker_duplicate_reply"),
                        category=category,
                    ),
                )

            source = ""
            try:
                get_platform_name = getattr(event, "get_platform_name", None)
                if callable(get_platform_name):
                    source = str(get_platform_name() or "")
            except Exception:
                source = ""
            entry = {
                "sha256": payload["sha256"],
                "filename": filename,
                "category": category,
                "size": int(payload.get("size", len(payload["data"]))),
                "created_at": int(time.time()),
                "source": source,
                "path": final_path,
            }
            old_entries = list(self._collected_entries)
            self._atomic_write_bytes(final_path, payload["data"])
            self._collected_entries = old_entries + [entry]
            try:
                self._save_collected_sticker_index()
            except Exception:
                self._collected_entries = old_entries
                try:
                    final_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            return (
                "saved",
                self._format_collected_reply(
                    self._cfg_str("collected_sticker_saved_reply"),
                    category=category,
                    filename=filename,
                ),
            )

    async def _collect_user_sticker(
        self,
        event: AstrMessageEvent,
        category: str,
    ) -> str:
        if self._collected_root is None:
            return self._cfg_str("collected_sticker_storage_unavailable_reply")
        if not self._cfg_bool("collected_sticker_enabled"):
            return self._cfg_str("collected_sticker_disabled_reply")

        status, payload = await self._extract_collectible_image_payload(event)
        if status == "no_image":
            return self._cfg_str("collected_sticker_no_image_reply")
        if status == "face":
            return self._cfg_str("collected_sticker_face_unsupported_reply")
        if status == "too_large":
            return self._cfg_str("collected_sticker_too_large_reply")
        if status != "ok" or payload is None:
            return self._cfg_str("collected_sticker_invalid_image_reply")
        _status, message = await self._save_collected_payload(
            event,
            category,
            payload,
        )
        return message

    async def _maybe_auto_collect_user_sticker(
        self,
        event: AstrMessageEvent,
    ) -> str | None:
        if not self._cfg_bool("auto_collect_user_sticker_enabled"):
            return None
        if not self._cfg_bool("collected_sticker_enabled"):
            return None
        now = self._clock()
        cooldown = self._cfg_int(
            "auto_collect_sticker_cooldown_seconds",
            0,
        )
        last = self._last_auto_collect_at.get(event.unified_msg_origin)
        if last is not None and now - last < cooldown:
            return None
        status, payload = await self._extract_collectible_image_payload(event)
        if status != "ok" or payload is None:
            return None
        if not self._is_probable_sticker_payload(payload):
            return None
        category = self._normalize_collected_category(
            self._cfg_str("auto_collect_sticker_default_category")
        )
        if category is None:
            category = DEFAULT_COLLECTED_CATEGORY
        self._last_auto_collect_at[event.unified_msg_origin] = now
        save_status, message = await self._save_collected_payload(
            event,
            category,
            payload,
        )
        if save_status != "saved":
            logger.info(
                "qqbot_enhancer 自动收藏未保存: %s",
                save_status,
            )
            return None
        return self._format_collected_reply(
            self._cfg_str("auto_collect_sticker_saved_reply"),
            category=category,
        )

    def _collected_category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._valid_collected_entries():
            category = str(entry.get("category", DEFAULT_COLLECTED_CATEGORY))
            counts[category] = counts.get(category, 0) + 1
        return counts

    def _select_collected_sticker(
        self,
        message_text: str,
        session_key: str,
        *,
        category: str | None = None,
    ) -> Path | None:
        entries = self._valid_collected_entries()
        if category is not None:
            entries = [entry for entry in entries if entry.get("category") == category]
        if not entries:
            return None

        if category is None and message_text.strip():
            text = message_text.casefold()
            category_matches = [
                entry
                for entry in entries
                if str(entry.get("category", "")).casefold() in text
            ]
            if category_matches:
                entries = category_matches

        if self._cfg_bool("sticker_prevent_repeat"):
            last_file = self._last_sticker_file.get(session_key)
            if last_file and len(entries) > 1:
                non_repeating = [
                    entry for entry in entries if entry.get("filename") != last_file
                ]
                if non_repeating:
                    entries = non_repeating
        if not entries:
            return None
        chosen = self._rng.choice(entries)
        path = chosen.get("path")
        return path if isinstance(path, Path) else None

    def _collect_command_argument(
        self,
        event: AstrMessageEvent,
        command_names: tuple[str, ...],
    ) -> str:
        text = str(getattr(event, "message_str", "") or "").strip()
        for prefix in self._command_prefixes(event):
            if prefix and text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        for command_name in command_names:
            if text == command_name:
                return ""
            if text.startswith(command_name):
                rest = text[len(command_name) :]
                if rest and rest[0].isspace():
                    argument = re.sub(r"\s+", " ", rest).strip()
                    return "".join(
                        char for char in argument if char.isprintable()
                    ).strip()
        return ""

    async def terminate(self) -> None:
        """插件被停用/卸载时调用；此插件没有后台任务需要清理。"""

