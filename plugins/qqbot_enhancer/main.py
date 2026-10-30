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
import json
import math
import random
import re
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

try:  # Loaded as a package by AstrBot.
    from .config import COLLECTED_CATEGORY_MAX_CHARS, DEFAULTS, ConfigReader
    from . import image_search as image_search_engine
    from .media import (
        IMAGE_PATH_EXTENSIONS,
        decode_base64_payload,
        detect_image_magic,
        image_dimensions,
        is_media_ref as media_is_media_ref,
        local_image_ref_path as media_local_image_ref_path,
        sanitize_http_url as media_sanitize_http_url,
    )
    from .songs import SONG_SUFFIXES, list_song_files, resolve_song_file
    from . import food as food_engine
    from . import personas as persona_engine
    from . import safe_tools as safe_tools_engine
    from . import sticker_intent as sticker_intent_engine
    from . import sticker_reclassify as sticker_reclassify_engine
    from .llm import (
        STICKER_AI_MARKER_RE,
        STICKER_AI_TAG_RE,
        extract_sticker_ai_marker,
        parse_ai_sticker_judgement,
        sticker_ai_system_instruction,
    )
    from .sticker_store import (
        COLLECTED_CATEGORY_RE,
        COLLECTED_FILENAME_RE,
        COLLECTED_INDEX_FILENAME,
        COLLECTED_STICKER_DIR_NAME,
        CollectedStickerStore,
    )
except ImportError:  # Loaded directly by a test harness.
    import sys as _sys

    _plugin_dir = str(Path(__file__).resolve().parent)
    if _plugin_dir not in _sys.path:
        _sys.path.insert(0, _plugin_dir)
    from config import COLLECTED_CATEGORY_MAX_CHARS, DEFAULTS, ConfigReader
    import image_search as image_search_engine
    from media import (
        IMAGE_PATH_EXTENSIONS,
        decode_base64_payload,
        detect_image_magic,
        image_dimensions,
        is_media_ref as media_is_media_ref,
        local_image_ref_path as media_local_image_ref_path,
        sanitize_http_url as media_sanitize_http_url,
    )
    from songs import SONG_SUFFIXES, list_song_files, resolve_song_file
    import food as food_engine
    import personas as persona_engine
    import safe_tools as safe_tools_engine
    import sticker_intent as sticker_intent_engine
    import sticker_reclassify as sticker_reclassify_engine
    from llm import (
        STICKER_AI_MARKER_RE,
        STICKER_AI_TAG_RE,
        extract_sticker_ai_marker,
        parse_ai_sticker_judgement,
        sticker_ai_system_instruction,
    )
    from sticker_store import (
        COLLECTED_CATEGORY_RE,
        COLLECTED_FILENAME_RE,
        COLLECTED_INDEX_FILENAME,
        COLLECTED_STICKER_DIR_NAME,
        CollectedStickerStore,
    )

PLUGIN_DIR = Path(__file__).resolve().parent
STICKER_DIR = PLUGIN_DIR / "stickers"
STICKER_RULES_PATH = STICKER_DIR / "sticker_rules.json"
STICKER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SONG_DIR = PLUGIN_DIR / "songs"
PLUGIN_DISPLAY_NAME = "QQ Bot 增强回复"
PLUGIN_FALLBACK_VERSION = "1.15.0"
DEFAULT_COLLECTED_CATEGORY = "未分类"
PENDING_USER_MEDIA_MAX_COUNT = 3
PENDING_USER_MEDIA_MAX_SESSIONS = 128
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
BING_IMAGES_ENDPOINT = "https://www.bing.com/images/search"
BING_IMAGES_REFERER = "https://www.bing.com/images/search"
BAIDU_IMAGES_ENDPOINT = "https://image.baidu.com/search/acjson"
BAIDU_IMAGES_REFERER = "https://image.baidu.com/"
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





class QQBotEnhancerPlugin(Star):
    """高优先级消息监听器：@/私聊必回，群聊非 @ 概率回复。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config: dict[str, Any] = config if isinstance(config, dict) else {}
        self._config_reader = ConfigReader(self.config)

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
        self._last_safe_tool_collect_at: dict[str, float] = {}
        self._last_food_at: dict[str, float] = {}
        self._last_collected_by_session: dict[str, dict[str, Any]] = {}
        self._sticker_ai_auto_collect_times: dict[str, list[float]] = {}
        self._pending_user_media: dict[str, dict[str, Any]] = {}
        self._song_dir = SONG_DIR

        self._sticker_entries: list[dict[str, Any]] = []
        self._emotion_keywords: dict[str, tuple[str, ...]] = {}
        self._collected_permissions_checked = False
        self._load_sticker_entries()

        # 用户收藏表情：官方 StarTools.get_data_dir() 返回
        # data/plugin_data/<plugin_name>/，绝不写回插件源码目录。
        self._collected_lock = asyncio.Lock()
        self._collected_root = self._resolve_collected_sticker_root()
        self._collected_entries: list[dict[str, Any]] = []
        self._load_collected_sticker_index()
        try:
            if self._memory_mode() == "astrbot":
                parsed_personas = self._parse_group_personas_config()
                if parsed_personas.has_group_overrides:
                    logger.warning(
                        "qqbot_enhancer 已配置 group_personas，但 "
                        "memory_mode=astrbot 没有稳定的会话级 persona "
                        "注入 API；已安全降级，不会修改全局人格。"
                        "如需按群人格请使用 memory_mode=direct。"
                    )
        except Exception:
            logger.warning(
                "qqbot_enhancer 群人格配置无法解析，已安全回退旧配置"
            )

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    def _config_value(self, key: str) -> Any:
        return self._config_reader.value(key)

    def _cfg_bool(self, key: str) -> bool:
        return self._config_reader.bool(key)

    def _cfg_int(self, key: str, minimum: int = 0) -> int:
        return self._config_reader.int(key, minimum=minimum)

    def _cfg_float(
        self,
        key: str,
        minimum: float = 0.0,
        maximum: float = 1.0,
    ) -> float:
        return self._config_reader.float(
            key,
            minimum=minimum,
            maximum=maximum,
        )

    def _cfg_str(self, key: str) -> str:
        return self._config_reader.string(key)

    def _memory_mode(self) -> str:
        value = self._cfg_str("memory_mode").strip().lower()
        if value in {"astrbot", "memory", "conversation", "long-term", "long_term"}:
            return "astrbot"
        return "direct"

    def _cfg_list(self, key: str) -> list[str]:
        return self._config_reader.string_list(key)

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

            # Optional AI auto-collect for any group member's textless image.
            # Disabled by default; when enabled it only saves after local safety
            # checks and a vision-provider/local-heuristic decision.
            if (
                has_image
                and not message_text.strip()
                and self._cfg_bool("sticker_ai_auto_collect_enabled")
            ):
                ai_auto_collect_reply = (
                    await self._maybe_ai_auto_collect_user_sticker(event)
                )
                if ai_auto_collect_reply:
                    should_stop = True
                    event.should_call_llm(True)
                    yield event.plain_result(ai_auto_collect_reply)
                    yielded = True
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

            # Explicit @/private reclassification of the most recent saved
            # sticker. The local parser is deliberately narrow; AI is only
            # consulted for an unclear target and never touches files/tools.
            if (
                explicit
                and message_text.strip()
                and self._cfg_bool(
                    "sticker_natural_language_reclassify_enabled"
                )
                and not safe_tools_engine.TOOL_MARKER_RE.search(message_text)
                and not STICKER_AI_MARKER_RE.search(message_text)
            ):
                reclassify_intent = (
                    sticker_reclassify_engine.detect_reclassify_request(
                        message_text
                    )
                )
                if reclassify_intent is not None:
                    should_stop = True
                    event.should_call_llm(True)
                    reclassify_reply = (
                        await self._handle_natural_language_reclassify(
                            event,
                            reclassify_intent,
                        )
                    )
                    yield event.plain_result(reclassify_reply)
                    yielded = True
                    return
                if (
                    sticker_reclassify_engine
                    .looks_like_incomplete_reclassify_request(message_text)
                ):
                    should_stop = True
                    event.should_call_llm(True)
                    yield event.plain_result(
                        self._cfg_str(
                            "sticker_natural_language_reclassify_unclear_reply"
                        )
                    )
                    yielded = True
                    return

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

            # Explicit @/private natural-language sticker request. Commands
            # have already been filtered above; private marker text is not
            # treated as a natural-language request. This branch deliberately
            # skips the LLM and sends at most one sticker.
            if (
                explicit
                and message_text.strip()
                and self._cfg_bool("sticker_natural_language_enabled")
                and not safe_tools_engine.TOOL_MARKER_RE.search(message_text)
                and not STICKER_AI_MARKER_RE.search(message_text)
            ):
                sticker_intent = (
                    self._detect_natural_language_sticker_intent(message_text)
                )
                if sticker_intent is not None:
                    should_stop = True
                    event.should_call_llm(True)
                    yield self._build_natural_language_sticker_result(
                        event,
                        sticker_intent,
                    )
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
            (
                reply_text,
                tool_result,
                had_safe_tool_marker,
            ) = await self._route_safe_tool(event, reply_text)
            if had_safe_tool_marker:
                should_stop = True
                event.should_call_llm(True)
                yield (
                    tool_result
                    if tool_result is not None
                    else event.plain_result(
                        self._finalize_reply(reply_text)
                    )
                )
                yielded = True
                return
            result = self._build_result(
                event,
                reply_text,
                message_text,
                allow_random_sticker=(
                    self._cfg_bool("sticker_random_fallback_enabled")
                    and not self._ai_tool_protocol_active()
                ),
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
    @filter.command("food", alias={"吃什么", "饭店"}, priority=110)
    async def food_command(self, event: AstrMessageEvent):
        """查询 food.dlsz.top 的餐厅信息，不调用 LLM。"""

        event.should_call_llm(True)
        try:
            if not self._cfg_bool("food_enabled"):
                yield event.plain_result(
                    self._cfg_str("food_disabled_reply")
                )
                return
            raw_args = self._collect_command_argument(
                event,
                ("food", "吃什么", "饭店"),
            )
            if not raw_args.strip():
                yield event.plain_result(
                    self._cfg_str("food_usage_reply")
                )
                return
            remaining = self._food_cooldown_remaining(event)
            if remaining > 0:
                yield event.plain_result(
                    self._format_collected_reply(
                        self._cfg_str("food_cooldown_reply"),
                        seconds=remaining,
                    )
                )
                return
            yield await self._build_food_result(event, raw_args)
        except asyncio.TimeoutError:
            yield event.plain_result(
                self._cfg_str("food_timeout_reply")
            )
        except Exception:
            logger.info("qqbot_enhancer /food 查询失败")
            try:
                yield event.plain_result(
                    self._cfg_str("food_error_reply")
                )
            except Exception:
                logger.info("qqbot_enhancer /food 错误回复发送失败")
        finally:
            event.stop_event()

    def _food_cooldown_remaining(self, event: AstrMessageEvent) -> int:
        now = self._clock()
        self._prune_state(now)
        key = (
            f"{event.unified_msg_origin}:"
            f"{event.get_sender_id()}"
        )
        cooldown = self._cfg_int("food_cooldown_seconds", 0)
        last = self._last_food_at.get(key)
        remaining = 0.0
        if last is not None:
            remaining = cooldown - (now - last)
        if remaining > 0:
            return max(1, int(math.ceil(remaining)))
        self._last_food_at[key] = now
        return 0

    def _read_food_response(self, response: Any) -> str:
        max_bytes = self._cfg_int("food_max_response_bytes", 1)
        headers = getattr(response, "headers", None)
        content_length = ""
        content_type = ""
        if headers is not None and hasattr(headers, "get"):
            content_length = str(headers.get("Content-Length", "") or "")
            content_type = str(headers.get("Content-Type", "") or "")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise food_engine.FoodAPIError("food response too large")
            except ValueError:
                pass
        data = response.read(max_bytes + 1)
        if data is None:
            data = b""
        if len(data) > max_bytes:
            raise food_engine.FoodAPIError("food response too large")
        if content_type:
            lowered = content_type.lower()
            if "json" not in lowered and not lowered.startswith("text/"):
                raise food_engine.FoodAPIError(
                    "food response content-type is not JSON"
                )
        charset = "utf-8"
        match = re.search(
            r"charset=([\w.+-]+)",
            content_type,
            re.IGNORECASE,
        )
        if match:
            charset = match.group(1)
        try:
            return data.decode(charset, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")

    def _fetch_food_json_sync(self, url: str) -> Any:
        if not food_engine.validate_food_final_url(url):
            raise food_engine.FoodAPIError("invalid food API URL")
        timeout = self._cfg_int("food_timeout_seconds", 1)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": IMAGE_SEARCH_USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        with self._open_bounded_http(request, timeout) as response:
            final_url = response.geturl()
            if not food_engine.validate_food_final_url(final_url):
                raise food_engine.FoodAPIError(
                    "food API redirect left fixed host"
                )
            text = self._read_food_response(response)
        return food_engine.decode_food_payload(text.encode("utf-8"))

    async def _fetch_food_json(self, url: str) -> Any:
        timeout = self._cfg_int("food_timeout_seconds", 1)
        return await asyncio.wait_for(
            asyncio.to_thread(self._fetch_food_json_sync, url),
            timeout=timeout,
        )

    async def _search_food_location(
        self,
        query: str,
    ) -> dict[str, Any] | None:
        url = food_engine.build_food_url(
            food_engine.FOOD_LOCATIONS_PATH,
            {"q": query},
        )
        payload = await self._fetch_food_json(url)
        items = food_engine.extract_food_items(payload)
        return food_engine.select_food_location(items, query)

    async def _resolve_food_args(
        self,
        raw_args: str,
    ) -> dict[str, Any]:
        tokens = food_engine.split_food_args(raw_args)
        keyword_max = max(
            1,
            self._cfg_int("food_keyword_max_chars", 1),
        )
        location_max = max(
            1,
            self._cfg_int("food_location_max_chars", 1),
        )
        sanitized = [
            food_engine.sanitize_food_query(token, max_chars=keyword_max)
            for token in tokens
        ]
        if not sanitized or any(token is None for token in sanitized):
            return {"error": "invalid"}
        tokens = [str(token) for token in sanitized]
        if len(tokens) == 1:
            return {"keyword": tokens[0], "location": None}

        candidate_indexes = []
        for index in (0, len(tokens) - 1):
            if index not in candidate_indexes:
                candidate_indexes.append(index)
        for index in candidate_indexes:
            candidate = tokens[index]
            location_query = food_engine.sanitize_food_query(
                candidate,
                max_chars=location_max,
            )
            if location_query is None:
                continue
            location = await self._search_food_location(location_query)
            if location is not None:
                keyword_tokens = [
                    token
                    for token_index, token in enumerate(tokens)
                    if token_index != index
                ]
                keyword = " ".join(keyword_tokens).strip()
                if not keyword or len(keyword) > keyword_max:
                    return {"error": "invalid"}
                return {"keyword": keyword, "location": location}

        for token in tokens:
            if food_engine.likely_location_token(token):
                return {
                    "error": "location_not_found",
                    "location": token,
                }
        keyword = " ".join(tokens).strip()
        if not keyword or len(keyword) > keyword_max:
            return {"error": "invalid"}
        return {"keyword": keyword, "location": None}

    async def _build_food_result(
        self,
        event: AstrMessageEvent,
        raw_args: str,
    ):
        try:
            resolved = await self._resolve_food_args(raw_args)
            if resolved.get("error") == "invalid":
                return event.plain_result(
                    self._cfg_str("food_invalid_query_reply")
                )
            if resolved.get("error") == "location_not_found":
                return event.plain_result(
                    self._format_collected_reply(
                        self._cfg_str(
                            "food_location_not_found_reply"
                        ),
                        location=str(resolved.get("location", "")),
                    )
                )
            keyword = str(resolved.get("keyword", "")).strip()
            location = resolved.get("location")
            params = {"q": keyword}
            location_name = ""
            if isinstance(location, dict):
                location_id = location.get("id")
                if location_id is not None:
                    params["location_id"] = str(location_id)
                location_name = str(location.get("name", "") or "")
            url = food_engine.build_food_url(
                food_engine.FOOD_RESTAURANTS_PATH,
                params,
            )
            payload = await self._fetch_food_json(url)
            items = food_engine.extract_food_items(payload)
            if not items:
                return event.plain_result(
                    self._cfg_str("food_empty_reply")
                )
            max_results = max(
                1,
                min(
                    food_engine.FOOD_MAX_RESULTS_HARD_LIMIT,
                    self._cfg_int("food_max_results", 1),
                ),
            )
            text = food_engine.format_food_results(
                items,
                keyword=keyword,
                location_name=location_name,
                max_results=max_results,
                footer=f"更多：{food_engine.FOOD_BASE_URL}",
            )
            return event.plain_result(text)
        except asyncio.TimeoutError:
            return event.plain_result(
                self._cfg_str("food_timeout_reply")
            )
        except Exception:
            logger.info("qqbot_enhancer /food 查询失败")
            return event.plain_result(
                self._cfg_str("food_error_reply")
            )

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
            categories = self._parse_collected_categories(raw_category)
            if categories is None:
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
            reply = await self._collect_user_sticker(
                event,
                categories[0],
                categories=categories,
            )
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
            valid_entries = self._valid_collected_entries()
            unique_total = len(
                {
                    str(entry.get("sha256", ""))
                    for entry in valid_entries
                    if entry.get("sha256")
                }
            )
            membership_total = sum(counts.values())
            if unique_total <= 0:
                yield event.plain_result(
                    self._cfg_str("collected_sticker_empty_reply")
                )
                return
            lines = [
                f"收藏表情共 {unique_total} 张"
                f"（分类归属 {membership_total} 次），"
                f"分类 {len(counts)} 个："
            ]
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
                    entry
                    for entry in entries
                    if category in self._entry_categories(entry)
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
        result = event.get_result()
        chain = getattr(result, "chain", None) if result is not None else None
        if not isinstance(chain, list):
            return

        # A memory-mode Agent can still emit the same private marker; parse it
        # before sticker selection so tool failures cleanly fall back to text.
        plain_text = "".join(
            str(component.text or "")
            for component in chain
            if isinstance(component, Comp.Plain)
        )
        if plain_text:
            (
                clean_text,
                tool_result,
                had_safe_tool_marker,
            ) = await self._route_safe_tool(event, plain_text)
            if had_safe_tool_marker:
                replacement_chain = getattr(tool_result, "chain", None)
                if isinstance(replacement_chain, list) and replacement_chain:
                    chain[:] = replacement_chain
                else:
                    chain[:] = [
                        Comp.Plain(self._finalize_reply(clean_text))
                    ]
                return

        if not self._cfg_bool("sticker_enabled"):
            return
        collected_available = (
            self._cfg_bool("collected_sticker_enabled")
            and bool(self._valid_collected_entries())
        )
        if not self._sticker_entries and not collected_available:
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
        sticker = self._maybe_pick_sticker(
            event,
            message_text,
            require_semantic_match=True,
        )
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

    def _persona_option_line(self) -> str:
        try:
            parsed = self._parse_group_personas_config()
        except Exception:
            return "群人格：配置解析失败，已回退旧 system_prompt。"
        if self._memory_mode() == "astrbot" and parsed.has_group_overrides:
            return (
                "群人格：已配置映射，但 memory_mode=astrbot 无稳定"
                "会话级 persona API；已安全降级，不会串改其它群。"
                "建议 memory_mode=direct。"
            )
        return persona_engine.persona_summary(parsed)

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
            "/food <关键词> [地点]（别名 /吃什么、/饭店）",
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
            self._persona_option_line(),
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
            + len(self._last_safe_tool_collect_at)
            + len(self._last_food_at)
            + len(self._sticker_ai_auto_collect_times)
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
            self._last_safe_tool_collect_at,
            self._last_food_at,
        ):
            for key, timestamp in list(mapping.items()):
                if timestamp < cutoff:
                    mapping.pop(key, None)
        for key in list(self._last_sticker_file):
            if key not in self._last_sticker_at:
                self._last_sticker_file.pop(key, None)
        self._prune_pending_user_media(now)
        self._prune_sticker_ai_auto_collect_times(now)

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
        return media_is_media_ref(ref)

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
                system_prompt=self._effective_system_prompt(event),
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
                        system_prompt=self._effective_system_prompt(event),
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
                        system_prompt=self._effective_system_prompt(event),
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
        has_cjk = bool(
            re.search(
                r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]",
                keyword,
            )
        )
        params = urllib.parse.urlencode(
            {
                "q": keyword,
                "form": "HDRSC2",
                "first": "1",
                "count": str(fetch_count),
                "adlt": safe_mode,
                "setlang": "zh-hans" if has_cjk else "en-US",
                "qs": "n",
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

    @staticmethod
    def _normalize_relevance_text(value: Any) -> str:
        return image_search_engine.normalize_relevance_text(value)

    def _relevance_query_variants(self, keyword: str) -> list[str]:
        return image_search_engine.relevance_query_variants(keyword)

    def _image_relevance_score_single(
        self,
        query: str,
        title_norm: str,
        desc_norm: str,
    ) -> float:
        return image_search_engine.relevance_score_single(
            query,
            title_norm,
            desc_norm,
        )

    def _image_relevance_score(
        self,
        keyword: str,
        title: str,
        description: str,
    ) -> float:
        return image_search_engine.relevance_score(
            keyword,
            title,
            description,
        )

    def _filter_image_candidates_by_relevance(
        self,
        keyword: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not self._cfg_bool("image_search_relevance_enabled"):
            for candidate in candidates:
                candidate["relevance_score"] = 1.0
            return candidates
        min_score = self._cfg_float(
            "image_search_min_relevance_score",
            0.0,
            1.0,
        )
        relevant = image_search_engine.filter_candidates(
            keyword,
            candidates,
            min_score=min_score,
        )
        if len(relevant) < len(candidates):
            logger.info(
                "qqbot_enhancer 图片相关性过滤: 保留 %s/%s（关键词=%s，阈值=%.2f）",
                len(relevant),
                len(candidates),
                keyword,
                min_score,
            )
        return relevant

    async def _search_image_candidates(self, keyword: str) -> list[dict[str, Any]]:
        result_limit = self._image_search_result_limit()
        candidate_limit = self._image_search_candidate_limit()
        raw_candidate_limit = min(
            IMAGE_SEARCH_MAX_CANDIDATE_URLS,
            max(20, candidate_limit * 3),
        )
        candidates: list[dict[str, Any]] = []
        errors: list[BaseException] = []
        bing_ok = False
        baidu_ok = False

        try:
            bing_url = self._build_bing_image_search_url(keyword)
            bing_page = await self._fetch_image_search_html(bing_url)
            bing_candidates = self._filter_image_candidates_by_relevance(
                keyword,
                self._parse_bing_image_candidates(
                    bing_page,
                    limit=raw_candidate_limit,
                ),
            )
            bing_ok = True
            for candidate in bing_candidates:
                candidate["referer"] = BING_IMAGES_REFERER
                candidate["source"] = "bing"
                candidates.append(candidate)
        except Exception as exc:
            errors.append(exc)
            logger.warning(
                "qqbot_enhancer Bing 图片搜索失败",
                exc_info=True,
            )

        # 百度作为旧可选源保留，但默认关闭，且默认绝不自动回退。
        if (
            len(candidates) < result_limit
            and self._cfg_bool("image_search_second_source_enabled")
        ):
            try:
                baidu_url = self._build_baidu_image_search_url(keyword)
                baidu_payload = await self._fetch_baidu_image_json(baidu_url)
                baidu_candidates = self._filter_image_candidates_by_relevance(
                    keyword,
                    self._parse_baidu_image_candidates(
                        baidu_payload,
                        limit=raw_candidate_limit,
                    ),
                )
                baidu_ok = True
                existing = {item["url"] for item in candidates}
                for candidate in baidu_candidates:
                    url = str(candidate.get("url", "") or "")
                    if not url or url in existing:
                        continue
                    existing.add(url)
                    candidate["referer"] = BAIDU_IMAGES_REFERER
                    candidate["source"] = "baidu"
                    candidates.append(candidate)
            except Exception as exc:
                errors.append(exc)
                logger.warning(
                    "qqbot_enhancer 百度图片可选搜索失败",
                    exc_info=True,
                )

        if not candidates and not bing_ok and not baidu_ok:
            if any(isinstance(error, asyncio.TimeoutError) for error in errors):
                raise asyncio.TimeoutError
            raise ImageSearchError("所有图片搜索源均请求失败")
        if self._cfg_bool("image_search_relevance_enabled"):
            candidates.sort(
                key=lambda item: float(item.get("relevance_score", 0.0)),
                reverse=True,
            )
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

    def _parse_baidu_image_candidates(
        self,
        payload: str | dict[str, Any],
        *,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Extract safe URLs plus title/description metadata from Baidu JSON."""

        result_limit = max(
            1,
            min(IMAGE_SEARCH_MAX_CANDIDATES, int(limit or 1)),
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

        results: list[dict[str, Any]] = []
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
            page_url = ""
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
                    if not page_url:
                        page_url = str(
                            replacement.get("FromURL")
                            or replacement.get("FromUrl")
                            or ""
                        )
            chosen_url = ""
            for raw_candidate in raw_candidates:
                safe_url = self._sanitize_http_url(
                    raw_candidate,
                    require_image_extension=False,
                )
                if safe_url:
                    chosen_url = safe_url
                    break
            if not chosen_url or chosen_url in seen:
                continue
            title = ""
            for title_key in (
                "fromPageTitleEnc",
                "fromPageTitle",
                "bdSourceName",
                "bdFromPageTitlePrefix",
            ):
                title = str(entry.get(title_key) or "").strip()
                if title:
                    break
            description_parts: list[str] = []
            for description_key in (
                "fromPageTitleEnc",
                "fromPageTitle",
                "bdSourceName",
                "bdFromPageTitlePrefix",
                "fromURLHost",
            ):
                value = str(entry.get(description_key) or "").strip()
                if value and value not in description_parts:
                    description_parts.append(value)
            if not page_url:
                page_url = str(
                    entry.get("fromURL")
                    or entry.get("fromJumpUrl")
                    or ""
                )
            seen.add(chosen_url)
            results.append(
                {
                    "url": chosen_url,
                    "title": title,
                    "description": " ".join(description_parts),
                    "page_url": page_url,
                }
            )
        return results

    def _parse_baidu_image_urls(
        self,
        payload: str | dict[str, Any],
        *,
        limit: int = 1,
    ) -> list[str]:
        """Compatibility wrapper returning only Baidu candidate URLs."""

        return [
            str(candidate["url"])
            for candidate in self._parse_baidu_image_candidates(
                payload,
                limit=limit,
            )
        ]

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
        return detect_image_magic(data)

    def _parse_bing_image_candidates(
        self,
        page: str,
        *,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Extract safe image URLs with Bing title/description metadata."""

        result_limit = max(
            1,
            min(IMAGE_SEARCH_MAX_CANDIDATES, int(limit or 1)),
        )
        scan_limit = min(
            IMAGE_SEARCH_MAX_CANDIDATES,
            max(50, result_limit * 20),
        )
        raw_candidates: list[dict[str, str]] = []

        # Primary parser: the m="..." JSON payload contains murl + t/desc/purl.
        for match in re.finditer(r'\bm="([^"]+)"', page, re.IGNORECASE):
            if len(raw_candidates) >= scan_limit:
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
                raw_candidates.append(
                    {
                        "url": murl.strip(),
                        "title": str(payload.get("t") or "").strip(),
                        "description": str(payload.get("desc") or "").strip(),
                        "page_url": str(payload.get("purl") or "").strip(),
                    }
                )

        decoded_page = html_lib.unescape(page)

        # Fallback 1: a JSON "murl":"..." fragment without the anchor wrapper.
        if len(raw_candidates) < scan_limit:
            murl_pattern = re.compile(
                r'"murl"\s*:\s*"((?:\\.|[^"\\])*)"',
                re.IGNORECASE,
            )
            for match in murl_pattern.finditer(decoded_page):
                if len(raw_candidates) >= scan_limit:
                    break
                encoded = match.group(1)
                try:
                    url = json.loads(f'"{encoded}"')
                except (TypeError, ValueError):
                    url = encoded.replace("\\/", "/")
                if isinstance(url, str) and url.strip():
                    raw_candidates.append(
                        {
                            "url": url.strip(),
                            "title": "",
                            "description": "",
                            "page_url": "",
                        }
                    )

        # Fallback 2: image-looking absolute URLs anywhere in the HTML.
        if len(raw_candidates) < scan_limit:
            for match in re.finditer(
                r'https?://[^\s"\'<>\\]+',
                decoded_page,
                re.IGNORECASE,
            ):
                if len(raw_candidates) >= scan_limit:
                    break
                candidate = match.group(0).rstrip(".,);]}\\")
                if self._has_image_path(candidate):
                    raw_candidates.append(
                        {
                            "url": candidate,
                            "title": "",
                            "description": "",
                            "page_url": "",
                        }
                    )

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in raw_candidates:
            safe_url = self._sanitize_http_url(
                candidate.get("url"),
                require_image_extension=True,
            )
            if not safe_url or safe_url in seen:
                continue
            seen.add(safe_url)
            results.append(
                {
                    "url": safe_url,
                    "title": candidate.get("title", ""),
                    "description": candidate.get("description", ""),
                    "page_url": candidate.get("page_url", ""),
                }
            )
            if len(results) >= result_limit:
                break
        return results

    def _parse_bing_image_urls(self, page: str, *, limit: int = 1) -> list[str]:
        """Compatibility wrapper returning only Bing candidate URLs."""

        return [
            str(candidate["url"])
            for candidate in self._parse_bing_image_candidates(
                page,
                limit=limit,
            )
        ]

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
        return media_sanitize_http_url(
            value,
            require_image_extension=require_image_extension,
            extensions=IMAGE_PATH_EXTENSIONS,
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
        return resolve_song_file(
            self._song_dir,
            filename,
            suffixes=SONG_SUFFIXES,
        )

    def _list_song_files(self) -> tuple[list[Path], bool]:
        max_bytes = self._cfg_int("song_max_file_bytes", 1)
        song_files, oversized = list_song_files(
            self._song_dir,
            max_bytes=max_bytes,
            suffixes=SONG_SUFFIXES,
        )
        for song_file in oversized:
            try:
                size = song_file.stat().st_size
            except OSError:
                continue
            logger.warning(
                "qqbot_enhancer 本地音频超过大小限制: %s (%s > %s)",
                song_file,
                size,
                max_bytes,
            )
        return song_files, bool(oversized)

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
                if isinstance(path, Path) and path.is_file():
                    for raw_category in self._entry_categories(entry):
                        safe_category = raw_category.strip()
                        if STICKER_AI_TAG_RE.fullmatch(safe_category):
                            tags.setdefault(safe_category, []).append(path)
                if isinstance(path, Path) and path.is_file():
                    for raw_tag in entry.get("tags", []):
                        tag = str(raw_tag or "").strip()
                        if STICKER_AI_TAG_RE.fullmatch(tag):
                            tags.setdefault(tag, []).append(path)
        return tags

    def _sticker_ai_system_instruction(self) -> str:
        if self._memory_mode() != "direct":
            return ""
        if not (
            self._cfg_bool("sticker_ai_select_enabled")
            and self._cfg_bool("sticker_ai_marker_enabled")
        ):
            return ""
        # In unified mode send_sticker is the single AI sticker protocol.
        # Keep parsing the legacy marker for compatibility, but do not inject
        # two competing instructions into one system prompt.
        if (
            self._cfg_bool("ai_unified_tool_protocol_enabled")
            and self._cfg_bool("safe_tools_enabled")
            and self._cfg_bool("safe_tools_marker_enabled")
            and self._cfg_bool("safe_tools_allow_send_sticker")
        ):
            return ""
        return sticker_ai_system_instruction(
            sorted(self._sticker_ai_available_tags())
        )

    def _enabled_safe_tool_names(self) -> list[str]:
        if not self._cfg_bool("safe_tools_enabled"):
            return []
        names: list[str] = []
        if self._cfg_bool("safe_tools_allow_search_images"):
            names.append("search_images")
        if self._cfg_bool("safe_tools_allow_send_sticker"):
            names.append("send_sticker")
        if self._cfg_bool("safe_tools_allow_collect_sticker"):
            names.append("collect_sticker")
        if self._cfg_bool("safe_tools_allow_list_categories"):
            names.append("list_sticker_categories")
        return names

    def _safe_tool_system_instruction(self) -> str:
        if self._memory_mode() != "direct":
            return ""
        if not self._cfg_bool("safe_tools_marker_enabled"):
            return ""
        return safe_tools_engine.tool_instruction(
            self._enabled_safe_tool_names()
        )

    def _parse_group_personas_config(self):
        raw = self._config_value("group_personas")
        return persona_engine.parse_group_personas(
            raw,
            max_groups=self._cfg_int("group_persona_max_groups", 0),
            max_prompt_chars=self._cfg_int(
                "group_persona_max_prompt_chars",
                1,
            ),
            max_name_chars=self._cfg_int(
                "group_persona_max_name_chars",
                1,
            ),
        )

    def _resolve_event_persona(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str, str, str]:
        legacy_prompt = self._cfg_str("system_prompt")
        if not self._cfg_bool("group_persona_enabled"):
            return legacy_prompt, "", "legacy"
        try:
            parsed = self._parse_group_personas_config()
            group_id = (
                ""
                if event.is_private_chat()
                else str(event.get_group_id() or "")
            )
            return persona_engine.resolve_persona(
                parsed,
                group_id=group_id,
                is_private=event.is_private_chat(),
                legacy_prompt=legacy_prompt,
            )
        except Exception:
            logger.warning(
                "qqbot_enhancer 群人格配置解析失败，安全回退旧 system_prompt"
            )
            return legacy_prompt, "", "legacy"

    def _effective_system_prompt(
        self,
        event: AstrMessageEvent | None = None,
    ) -> str:
        if event is None:
            prompt = self._cfg_str("system_prompt")
        else:
            prompt, _persona_name, _source = self._resolve_event_persona(event)
        instructions = [
            instruction
            for instruction in (
                self._sticker_ai_system_instruction(),
                self._safe_tool_system_instruction(),
            )
            if instruction
        ]
        if not instructions:
            return prompt
        return prompt + "\n" + "\n".join(instructions)

    def _ai_marker_mode_active(self) -> bool:
        return bool(
            self._memory_mode() == "direct"
            and self._cfg_bool("sticker_ai_select_enabled")
            and self._cfg_bool("sticker_ai_marker_enabled")
            and self._sticker_ai_available_tags()
        )

    def _ai_tool_protocol_active(self) -> bool:
        if not self._cfg_bool("ai_unified_tool_protocol_enabled"):
            return self._ai_marker_mode_active()
        return bool(
            self._safe_tool_system_instruction()
            or self._sticker_ai_system_instruction()
        )

    def _extract_sticker_ai_marker(
        self,
        text: str,
    ) -> tuple[str, str | None, bool]:
        return extract_sticker_ai_marker(text)

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

    def _collected_entry_matches_term(
        self,
        entry: dict[str, Any],
        term: str,
    ) -> bool:
        normalized = str(term or "").strip().casefold()
        if not normalized:
            return False
        if any(
            str(category).strip().casefold() == normalized
            for category in self._entry_categories(entry)
        ):
            return True
        if any(
            str(tag or "").strip().casefold() == normalized
            for tag in entry.get("tags", [])
        ):
            return True
        meaning = str(entry.get("meaning", "") or "").casefold()
        return bool(meaning and normalized in meaning)

    def _select_sticker_from_pool(
        self,
        event: AstrMessageEvent,
        *,
        category: str | None = None,
        emotion: str | None = None,
        tag: str | None = None,
    ) -> Path | None:
        if not self._cfg_bool("sticker_enabled"):
            return None
        session_key = event.unified_msg_origin
        candidates: list[Path] = []
        if emotion:
            normalized_emotion = str(emotion).strip().casefold()
            for entry in self._sticker_entries:
                path = entry.get("path")
                if (
                    isinstance(path, Path)
                    and path.is_file()
                    and str(entry.get("emotion", "") or "").strip().casefold()
                    == normalized_emotion
                ):
                    candidates.append(path)
        elif category:
            if not self._cfg_bool("collected_sticker_enabled"):
                return None
            normalized_category = self._normalize_collected_category(category)
            if normalized_category is None:
                return None
            for entry in self._valid_collected_entries():
                if normalized_category not in self._entry_categories(entry):
                    continue
                path = entry.get("path")
                if isinstance(path, Path) and path.is_file():
                    candidates.append(path)
        elif tag:
            if not self._cfg_bool("collected_sticker_enabled"):
                return None
            for entry in self._valid_collected_entries():
                if not self._collected_entry_matches_term(entry, tag):
                    continue
                path = entry.get("path")
                if isinstance(path, Path) and path.is_file():
                    candidates.append(path)
        else:
            for entry in self._sticker_entries:
                path = entry.get("path")
                if isinstance(path, Path) and path.is_file():
                    candidates.append(path)
            if self._cfg_bool("collected_sticker_enabled"):
                for entry in self._valid_collected_entries():
                    path = entry.get("path")
                    if isinstance(path, Path) and path.is_file():
                        candidates.append(path)
        if not candidates:
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

    def _detect_natural_language_sticker_intent(
        self,
        text: str,
    ) -> dict[str, str] | None:
        if not self._cfg_bool("sticker_natural_language_enabled"):
            return None
        emotions: set[str] = set()
        for entry in self._sticker_entries:
            path = entry.get("path")
            emotion = str(entry.get("emotion", "") or "").strip()
            if isinstance(path, Path) and path.is_file() and emotion:
                emotions.add(emotion)
        categories = (
            set(self._collected_category_counts().keys())
            if self._cfg_bool("collected_sticker_enabled")
            else set()
        )
        intent = sticker_intent_engine.detect_sticker_request(
            text,
            available_emotions=emotions,
            available_categories=categories,
        )
        if intent is not None:
            target = str(intent.get("target", "") or "").strip()
            if target and self._cfg_bool("collected_sticker_enabled"):
                if any(
                    self._collected_entry_matches_term(entry, target)
                    for entry in self._valid_collected_entries()
                ):
                    return {
                        "kind": "tag",
                        "value": target,
                        "target": target,
                    }
        return intent

    def _normalize_semantic_metadata(
        self,
        raw: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        category = self._normalize_collected_category(raw.get("category"))
        if category is None:
            return None
        raw_categories = raw.get("categories")
        if not isinstance(raw_categories, (list, tuple, set)):
            raw_categories = [category]
        normalized_categories = self._normalize_collected_categories_list(
            list(raw_categories),
            primary=category,
        )
        if normalized_categories is None:
            return None
        category = normalized_categories[0]
        if not self._cfg_bool("sticker_semantic_tags_enabled"):
            return {
                "category": category,
                "categories": normalized_categories,
                "tags": [],
                "meaning": "",
                "tone": "",
            }
        max_tags = max(0, self._cfg_int("collected_sticker_max_tags", 0))
        max_tag_chars = max(
            1,
            self._cfg_int("collected_sticker_max_tag_chars", 1),
        )
        raw_tags = raw.get("tags", [])
        if raw_tags is None:
            raw_tags = []
        if not isinstance(raw_tags, list):
            return None
        tags: list[str] = []
        for raw_tag in raw_tags:
            tag = str(raw_tag or "").strip()
            if not tag or len(tag) > max_tag_chars:
                return None
            if not re.fullmatch(r"[0-9A-Za-z_\-\u4e00-\u9fff]+", tag):
                return None
            if tag not in tags:
                tags.append(tag)
        if len(tags) > max_tags:
            return None
        meaning = str(raw.get("meaning", "") or "").strip()
        if len(meaning) > self._cfg_int(
            "collected_sticker_max_meaning_chars",
            1,
        ):
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in meaning):
            return None
        tone = str(raw.get("tone", "") or "").strip()
        if len(tone) > self._cfg_int("collected_sticker_max_tone_chars", 1):
            return None
        if tone and not re.fullmatch(
            r"[0-9A-Za-z_\-\u4e00-\u9fff]+",
            tone,
        ):
            return None
        return {
            "category": category,
            "categories": normalized_categories,
            "tags": tags,
            "meaning": meaning,
            "tone": tone,
        }

    async def _ai_classify_reclassify_target(
        self,
        event: AstrMessageEvent,
        target: str,
    ) -> dict[str, Any] | None:
        if not self._cfg_bool(
            "sticker_natural_language_reclassify_ai_enabled"
        ):
            return None
        provider_id = self._cfg_str(
            "sticker_natural_language_reclassify_provider_id"
        ).strip()
        if not provider_id:
            provider_id = await self._get_current_provider_id(event)
        if not provider_id:
            return None
        prompt = (
            "把用户对聊天表情包含义/用途的自然语言说明结构化为安全元数据。"
            "只输出 JSON 对象，不要 Markdown、不要解释："
            '{"category":"安全短分类名（中文/字母/数字/-/_，最多32字）",'
            '"tags":["安全短标签"],"meaning":"不超过200字的用途说明",'
            '"tone":"可选语气标签","confidence":0.0 到 1.0}。'
            f"用户说明：{target}"
        )
        try:
            response_text = await self._llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=(
                    "你只负责输出表情含义的结构化 JSON。"
                    "不得调用工具、读取文件、执行命令或输出路径、"
                    "用户 ID、凭据。标签和分类必须是安全短词语。"
                ),
            )
        except Exception:
            logger.warning(
                "qqbot_enhancer 语义分类 AI 失败，回退本地/纯文本",
                exc_info=True,
            )
            return None
        judgement = sticker_reclassify_engine.parse_reclassify_judgement(
            response_text
        )
        if judgement is None:
            return None
        if float(judgement["confidence"]) < self._cfg_float(
            "sticker_natural_language_reclassify_min_confidence",
            0.0,
            1.0,
        ):
            return None
        return self._normalize_semantic_metadata(judgement)

    async def _reclassify_collected_by_sha(
        self,
        event: AstrMessageEvent,
        sha256: str,
        semantics: dict[str, Any],
    ) -> str | None:
        store = self._collected_store()
        if store is None:
            return self._cfg_str(
                "sticker_natural_language_reclassify_failed_reply"
            )
        category = str(semantics.get("category", ""))
        async with self._collected_lock:
            self._collected_entries = self._valid_collected_entries()
            old_entry = next(
                (
                    entry
                    for entry in self._collected_entries
                    if entry.get("sha256") == sha256
                ),
                None,
            )
            old_category = (
                old_entry.get("category") if old_entry is not None else None
            )
            try:
                status, new_entry = store.update_metadata(
                    self._collected_entries,
                    sha256,
                    category=category,
                    categories=semantics.get("categories", [category]),
                    tags=semantics.get("tags", []),
                    meaning=semantics.get("meaning", ""),
                    tone=semantics.get("tone", ""),
                )
            except Exception:
                logger.warning(
                    "qqbot_enhancer 收藏语义更新失败，旧文件/index 保留",
                    exc_info=True,
                )
                return self._cfg_str(
                    "sticker_natural_language_reclassify_failed_reply"
                )
            if status == "not_found":
                return None
            if status == "same":
                if new_entry is not None:
                    self._last_collected_by_session[
                        event.unified_msg_origin
                    ] = {
                        "sha256": sha256,
                        "category": category,
                        "filename": str(new_entry.get("filename", "")),
                        "saved_at": int(time.time()),
                    }
                return self._format_collected_reply(
                    self._cfg_str(
                        "sticker_natural_language_reclassify_same_reply"
                    ),
                    category=category,
                )
            if status != "updated" or new_entry is None:
                return self._cfg_str(
                    "sticker_natural_language_reclassify_failed_reply"
                )
            self._load_collected_sticker_index()
            self._last_collected_by_session[event.unified_msg_origin] = {
                "sha256": sha256,
                "category": category,
                "filename": str(new_entry.get("filename", "")),
                "saved_at": int(time.time()),
            }
            meaning = str(semantics.get("meaning", "") or "").strip()
            if meaning:
                return self._format_collected_reply(
                    self._cfg_str(
                        "sticker_natural_language_reclassify_memorized_reply"
                    ),
                    meaning=meaning,
                    category=category,
                )
            if old_category != category:
                return self._format_collected_reply(
                    self._cfg_str(
                        "sticker_natural_language_reclassify_moved_reply"
                    ),
                    category=category,
                )
            return self._format_collected_reply(
                self._cfg_str(
                    "sticker_natural_language_reclassify_same_reply"
                ),
                category=category,
            )

    async def _update_collected_categories_by_sha(
        self,
        event: AstrMessageEvent,
        sha256: str,
        categories: list[str],
        action: str,
    ) -> str | None:
        store = self._collected_store()
        if store is None:
            return self._cfg_str(
                "sticker_natural_language_reclassify_failed_reply"
            )
        async with self._collected_lock:
            self._collected_entries = self._valid_collected_entries()
            old_entry = next(
                (
                    entry
                    for entry in self._collected_entries
                    if entry.get("sha256") == sha256
                ),
                None,
            )
            if old_entry is None:
                return None
            try:
                if action == "add":
                    status, new_entry = store.add_categories(
                        self._collected_entries,
                        sha256,
                        categories,
                    )
                elif action == "remove":
                    status, new_entry = store.remove_categories(
                        self._collected_entries,
                        sha256,
                        categories,
                    )
                else:
                    return None
            except Exception:
                logger.warning(
                    "qqbot_enhancer 收藏多分类更新失败，旧文件/index 保留",
                    exc_info=True,
                )
                return self._cfg_str(
                    "sticker_natural_language_reclassify_failed_reply"
                )
            if status == "not_found":
                return None
            if status == "same":
                return self._cfg_str(
                    "sticker_natural_language_reclassify_categories_same_reply"
                )
            if status != "updated" or new_entry is None:
                return self._cfg_str(
                    "sticker_natural_language_reclassify_failed_reply"
                )
            self._load_collected_sticker_index()
            primary = str(new_entry.get("category", ""))
            self._last_collected_by_session[event.unified_msg_origin] = {
                "sha256": sha256,
                "category": primary,
                "filename": str(new_entry.get("filename", "")),
                "saved_at": int(time.time()),
            }
            template_key = (
                "sticker_natural_language_reclassify_added_reply"
                if action == "add"
                else "sticker_natural_language_reclassify_removed_reply"
            )
            return self._format_collected_reply(
                self._cfg_str(template_key),
                categories=", ".join(categories),
            )

    async def _update_recent_categories(
        self,
        event: AstrMessageEvent,
        categories: list[str],
        action: str,
    ) -> str:
        last = self._last_collected_by_session.get(
            event.unified_msg_origin
        )
        if last and last.get("sha256"):
            reply = await self._update_collected_categories_by_sha(
                event,
                str(last["sha256"]),
                categories,
                action,
            )
            if reply is not None:
                return reply
        status, payload = await self._extract_collectible_image_payload(
            event
        )
        if status == "ok" and payload is not None:
            sha256 = str(payload.get("sha256", ""))
            reply = await self._update_collected_categories_by_sha(
                event,
                sha256,
                categories,
                action,
            )
            if reply is not None:
                return reply
            if action == "add":
                save_status, _ = await self._save_collected_payload(
                    event,
                    categories[0],
                    payload,
                    categories=categories,
                )
                if save_status == "saved":
                    return self._format_collected_reply(
                        self._cfg_str(
                            "sticker_natural_language_reclassify_added_reply"
                        ),
                        categories=", ".join(categories),
                    )
        return self._cfg_str(
            "sticker_natural_language_reclassify_not_found_reply"
        )

    async def _reclassify_recent_collected(
        self,
        event: AstrMessageEvent,
        semantics: dict[str, Any],
    ) -> str:
        category = str(semantics.get("category", ""))
        last = self._last_collected_by_session.get(
            event.unified_msg_origin
        )
        if last and last.get("sha256"):
            reply = await self._reclassify_collected_by_sha(
                event,
                str(last["sha256"]),
                semantics,
            )
            if reply is not None:
                return reply

        status, payload = await self._extract_collectible_image_payload(
            event
        )
        if status != "ok" or payload is None:
            return self._cfg_str(
                "sticker_natural_language_reclassify_not_found_reply"
            )
        sha256 = str(payload.get("sha256", ""))
        reply = await self._reclassify_collected_by_sha(
            event,
            sha256,
            semantics,
        )
        if reply is not None:
            return reply

        save_status, _ = await self._save_collected_payload(
            event,
            category,
            payload,
            categories=semantics.get("categories", [category]),
            tags=semantics.get("tags", []),
            meaning=str(semantics.get("meaning", "")),
            tone=str(semantics.get("tone", "")),
        )
        if save_status == "saved":
            meaning = str(semantics.get("meaning", "") or "").strip()
            if meaning:
                return self._format_collected_reply(
                    self._cfg_str(
                        "sticker_natural_language_reclassify_memorized_reply"
                    ),
                    meaning=meaning,
                    category=category,
                )
            return self._format_collected_reply(
                self._cfg_str(
                    "sticker_natural_language_reclassify_cached_reply"
                ),
                category=category,
            )
        if save_status == "duplicate":
            reply = await self._reclassify_collected_by_sha(
                event,
                sha256,
                semantics,
            )
            if reply is not None:
                return reply
        return self._cfg_str(
            "sticker_natural_language_reclassify_failed_reply"
        )

    async def _handle_natural_language_reclassify(
        self,
        event: AstrMessageEvent,
        intent: dict[str, str],
    ) -> str:
        target = str(intent.get("target", "") or "").strip()
        action = str(intent.get("action", "set") or "set")
        if action in {"add", "remove"}:
            target_categories = self._parse_collected_categories(target)
            if target_categories is None:
                return self._format_collected_reply(
                    self._cfg_str(
                        "sticker_natural_language_reclassify_unclear_reply"
                    ),
                    target=target,
                )
            return await self._update_recent_categories(
                event,
                target_categories,
                action,
            )
        semantics = sticker_reclassify_engine.build_local_semantics(target)
        if semantics is None:
            semantics = await self._ai_classify_reclassify_target(
                event,
                target,
            )
        if isinstance(semantics, dict) and not semantics.get("categories"):
            parsed_categories = self._parse_collected_categories(target)
            if parsed_categories:
                semantics["categories"] = parsed_categories
                semantics["category"] = parsed_categories[0]
        normalized = self._normalize_semantic_metadata(semantics)
        if normalized is None:
            return self._format_collected_reply(
                self._cfg_str(
                    "sticker_natural_language_reclassify_unclear_reply"
                ),
                target=target,
            )
        return await self._reclassify_recent_collected(
            event,
            normalized,
        )

    def _build_natural_language_sticker_result(
        self,
        event: AstrMessageEvent,
        intent: dict[str, str],
    ):
        kind = intent.get("kind")
        if kind == "not_found":
            return event.plain_result(
                self._format_collected_reply(
                    self._cfg_str(
                        "sticker_natural_language_not_found_reply"
                    ),
                    target=str(intent.get("target", "")),
                )
            )
        if kind == "emotion":
            chosen = self._select_sticker_from_pool(
                event,
                emotion=intent.get("value", ""),
            )
        elif kind == "category":
            chosen = self._select_sticker_from_pool(
                event,
                category=intent.get("value", ""),
            )
        elif kind == "tag":
            chosen = self._select_sticker_from_pool(
                event,
                tag=intent.get("value", ""),
            )
        else:
            chosen = self._select_sticker_from_pool(event)

        if chosen is None:
            now = self._clock()
            cooldown = self._cfg_int("sticker_cooldown_seconds", 0)
            last_sent = self._last_sticker_at.get(event.unified_msg_origin)
            if last_sent is not None and now - last_sent < cooldown:
                return event.plain_result(
                    self._cfg_str(
                        "sticker_natural_language_cooldown_reply"
                    )
                )
            return event.plain_result(
                self._cfg_str("sticker_natural_language_empty_reply")
            )
        try:
            image_component = Comp.Image.fromFileSystem(str(chosen))
        except Exception:
            logger.warning(
                "qqbot_enhancer 自然语言发表情组件构造失败",
                exc_info=True,
            )
            return event.plain_result(
                self._cfg_str("sticker_natural_language_empty_reply")
            )
        return event.chain_result(
            [
                Comp.Plain(
                    self._cfg_str("sticker_natural_language_send_prefix")
                ),
                image_component,
            ]
        )

    def _select_safe_tool_sticker(
        self,
        event: AstrMessageEvent,
        intent: Any,
    ) -> Path | None:
        emotion = str(intent.args.get("emotion", "") or "").strip()
        category = str(intent.args.get("category", "") or "").strip()
        tag = str(intent.args.get("tag", "") or "").strip()
        return self._select_sticker_from_pool(
            event,
            emotion=emotion or None,
            category=category or None,
            tag=tag or None,
        )

    async def _execute_safe_tool(
        self,
        event: AstrMessageEvent,
        intent: Any,
        clean_text: str,
    ):
        name = intent.name
        if name == "search_images":
            if not self._cfg_bool("safe_tools_allow_search_images"):
                return None
            keyword = str(intent.args.get("keyword", "") or "").strip()
            if not keyword:
                return None
            # The tool contract accepts count for compatibility, but a single
            # reply must never contain more than one attachment.
            try:
                requested_count = int(intent.args.get("count", "1"))
            except (TypeError, ValueError):
                return None
            max_safe_count = max(
                1,
                self._cfg_int("safe_tools_search_max_count", 1),
            )
            if requested_count < 1 or max_safe_count < 1:
                return None
            # Hard attachment cap: the tool never sends more than one image.
            requested_count = min(requested_count, max_safe_count, 1)
            if self._image_search_cooldown_remaining(event) > 0:
                return None
            candidates = await self._search_image_candidates(keyword)
            if not candidates:
                return None
            local_paths, remote_urls = await self._download_search_image_files(
                event,
                candidates,
                result_limit=1,
            )
            image_component = None
            if local_paths:
                try:
                    image_component = Comp.Image.fromFileSystem(
                        str(local_paths[0])
                    )
                except Exception:
                    image_component = None
            if image_component is None and remote_urls:
                try:
                    image_component = Comp.Image.fromURL(remote_urls[0])
                except Exception:
                    image_component = None
            if image_component is None:
                return None
            return event.chain_result(
                [
                    Comp.Plain(self._cfg_str("image_search_success_prefix")),
                    image_component,
                ]
            )

        if name == "send_sticker":
            if not self._cfg_bool("safe_tools_allow_send_sticker"):
                return None
            sticker_path = self._select_safe_tool_sticker(event, intent)
            if sticker_path is None:
                return None
            try:
                image_component = Comp.Image.fromFileSystem(str(sticker_path))
            except Exception:
                return None
            return event.chain_result(
                [
                    Comp.Plain(self._cfg_str("safe_tools_sticker_prefix")),
                    image_component,
                ]
            )

        if name == "collect_sticker":
            if not self._cfg_bool("safe_tools_allow_collect_sticker"):
                return None
            now = self._clock()
            cooldown = self._cfg_int(
                "safe_tools_collect_cooldown_seconds",
                0,
            )
            last = self._last_safe_tool_collect_at.get(
                event.unified_msg_origin
            )
            if last is not None and now - last < cooldown:
                return None
            self._last_safe_tool_collect_at[event.unified_msg_origin] = now
            raw_category = intent.args.get("category", "").strip()
            category = self._normalize_collected_category(
                raw_category or DEFAULT_COLLECTED_CATEGORY
            )
            if category is None:
                return None
            status, payload = await self._extract_collectible_image_payload(
                event
            )
            if status != "ok" or payload is None:
                return None
            save_status, _ = await self._save_collected_payload(
                event,
                category,
                payload,
            )
            if save_status != "saved":
                return None
            return event.plain_result(
                self._format_collected_reply(
                    self._cfg_str(
                        "sticker_ai_auto_collect_saved_reply"
                    ),
                    category=category,
                )
            )

        if name == "list_sticker_categories":
            if not self._cfg_bool("safe_tools_allow_list_categories"):
                return None
            counts: dict[str, int] = {}
            for entry in self._sticker_entries:
                path = entry.get("path")
                emotion = str(entry.get("emotion", "") or "").strip()
                if isinstance(path, Path) and path.is_file() and emotion:
                    counts[emotion] = counts.get(emotion, 0) + 1
            if self._cfg_bool("collected_sticker_enabled"):
                for entry in self._valid_collected_entries():
                    category = str(entry.get("category", "") or "").strip()
                    if category:
                        counts[category] = counts.get(category, 0) + 1
            if not counts:
                return event.plain_result(
                    self._cfg_str("collected_sticker_empty_reply")
                )
            max_categories = max(
                1,
                self._cfg_int("safe_tools_max_categories", 1),
            )
            lines = ["可用表情分类："]
            for category, count in sorted(counts.items())[:max_categories]:
                lines.append(f"- {category}（{count}）")
            return event.plain_result("\n".join(lines))

        return None

    async def _route_safe_tool(
        self,
        event: AstrMessageEvent,
        text: str,
    ) -> tuple[str, Any | None, bool]:
        parsed = safe_tools_engine.parse_tool_markers(text)
        if not parsed.had_marker:
            return text, None, False
        clean_text, _, _ = self._extract_sticker_ai_marker(parsed.clean_text)
        if not self._cfg_bool("safe_tools_enabled"):
            logger.info(
                "qqbot_enhancer safe_tool marker tool=%s success=False "
                "reason=disabled",
                parsed.intents[0].name if parsed.intents else "invalid",
            )
            return clean_text, None, True
        enabled = self._enabled_safe_tool_names()
        max_actions = self._cfg_int("safe_tools_max_actions_per_reply", 1)
        if max_actions < 1 or len(parsed.intents) != 1:
            logger.info(
                "qqbot_enhancer safe_tool marker rejected reason=count_or_limit"
            )
            return clean_text, None, True
        intent = parsed.intents[0]
        if not intent.valid or intent.name not in enabled:
            logger.info(
                "qqbot_enhancer safe_tool marker tool=%s success=False "
                "reason=invalid_or_denied",
                intent.name,
            )
            return clean_text, None, True
        try:
            result = await self._execute_safe_tool(
                event,
                intent,
                clean_text,
            )
        except Exception:
            logger.warning(
                "qqbot_enhancer 安全工具执行失败，回退普通文本",
                exc_info=True,
            )
            result = None
        logger.info(
            "qqbot_enhancer safe_tool tool=%s success=%s",
            intent.name,
            result is not None,
        )
        return clean_text, result, True

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

    def _has_sticker_semantic_match(self, message_text: str) -> bool:
        text = str(message_text or "").casefold()
        if not text:
            return False
        for entry in self._sticker_entries:
            path = entry.get("path")
            if not isinstance(path, Path) or not path.is_file():
                continue
            if any(
                keyword and keyword.casefold() in text
                for keyword in entry.get("keywords", [])
            ):
                return True
        for keywords in self._emotion_keywords.values():
            if any(keyword.casefold() in text for keyword in keywords):
                return True
        if self._cfg_bool("collected_sticker_enabled"):
            for entry in self._valid_collected_entries():
                if any(
                    category.casefold() in text
                    for category in self._entry_categories(entry)
                ):
                    return True
                if any(
                    str(tag or "").strip()
                    and str(tag).strip().casefold() in text
                    for tag in entry.get("tags", [])
                ):
                    return True
                meaning = str(entry.get("meaning", "") or "").casefold()
                if meaning and meaning in text:
                    return True
        return False

    def _maybe_pick_sticker(
        self,
        event: AstrMessageEvent,
        message_text: str,
        *,
        require_semantic_match: bool = False,
    ) -> Path | None:
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
        if require_semantic_match and not self._has_sticker_semantic_match(
            message_text
        ):
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
    def _collected_store(self) -> CollectedStickerStore | None:
        if self._collected_root is None:
            return None
        return CollectedStickerStore(
            self._collected_root,
            max_file_bytes=self._cfg_int(
                "collected_sticker_max_file_bytes",
                1,
            ),
            category_normalizer=self._normalize_collected_category,
            default_category=DEFAULT_COLLECTED_CATEGORY,
            log_warning=self._log_collected_permission_warning,
            max_tags=self._cfg_int("collected_sticker_max_tags", 0),
            max_tag_chars=self._cfg_int(
                "collected_sticker_max_tag_chars",
                1,
            ),
            max_meaning_chars=self._cfg_int(
                "collected_sticker_max_meaning_chars",
                1,
            ),
            max_tone_chars=self._cfg_int(
                "collected_sticker_max_tone_chars",
                1,
            ),
            max_categories=self._cfg_int(
                "collected_sticker_max_categories",
                1,
            ),
        )

    def _log_collected_permission_warning(self, message: str) -> None:
        logger.warning("%s", message)

    def _resolve_collected_sticker_root(self) -> Path | None:
        try:
            plugin_name = str(getattr(self, "name", "") or "qqbot_enhancer")
            data_dir = Path(StarTools.get_data_dir(plugin_name=plugin_name)).resolve()
            raw_root = data_dir / COLLECTED_STICKER_DIR_NAME
            if raw_root.is_symlink():
                logger.warning("qqbot_enhancer 收藏目录不能是符号链接")
                return None
            raw_root.mkdir(
                parents=True,
                exist_ok=True,
                mode=0o755,
            )
            root = raw_root.resolve()
            if root.parent != data_dir:
                logger.warning("qqbot_enhancer 收藏目录越出插件数据目录")
                return None

            # The plugin data root itself must also be traversable by the host
            # management user; child 0755 alone is insufficient if this is 0700.
            for directory in (data_dir, root):
                try:
                    directory.chmod(0o755)
                except OSError as exc:
                    logger.warning(
                        "qqbot_enhancer 无法将收藏相关目录调整为 0755："
                        "%s；请确认容器用户与宿主机管理用户对 "
                        "plugin_data/qqbot_enhancer 有遍历权限，或由管理员"
                        "手动执行 chmod 755 修复",
                        exc.strerror or exc,
                    )

            if not self._collected_permissions_checked:
                migration_store = CollectedStickerStore(
                    root,
                    max_file_bytes=self._cfg_int(
                        "collected_sticker_max_file_bytes",
                        1,
                    ),
                    category_normalizer=self._normalize_collected_category,
                    default_category=DEFAULT_COLLECTED_CATEGORY,
                    log_warning=self._log_collected_permission_warning,
                )
                summary = migration_store.migrate_permissions(data_dir)
                if summary["errors"]:
                    logger.warning(
                        "qqbot_enhancer 收藏目录权限迁移完成但有 %s 个错误；"
                        "收藏数据未删除，可手动检查 plugin_data/"
                        "qqbot_enhancer 的 0755/0644 权限",
                        summary["errors"],
                    )
                self._collected_permissions_checked = True
            return root
        except Exception:
            logger.warning(
                "qqbot_enhancer 无法创建用户收藏表情数据目录或迁移权限；"
                "可手动检查 plugin_data/qqbot_enhancer 的容器/宿主机权限",
                exc_info=True,
            )
            return None

    def _collected_index_path(self) -> Path | None:
        if self._collected_root is None:
            return None
        return self._collected_root / COLLECTED_INDEX_FILENAME

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        CollectedStickerStore.atomic_write_bytes(
            path,
            data,
            log_warning=lambda message: logger.warning("%s", message),
        )

    def _save_collected_sticker_index(self) -> None:
        store = self._collected_store()
        if store is None:
            raise RuntimeError("收藏数据目录不可用")
        store.save_index(self._collected_entries)

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

    def _normalize_collected_categories_list(
        self,
        raw_categories: Any,
        *,
        primary: str | None = None,
    ) -> list[str] | None:
        if not isinstance(raw_categories, (list, tuple, set)):
            return None
        categories: list[str] = []
        for raw_category in raw_categories:
            category = self._normalize_collected_category(raw_category)
            if category is None:
                return None
            if category not in categories:
                categories.append(category)
        if primary is not None:
            primary_norm = self._normalize_collected_category(primary)
            if primary_norm is None:
                return None
            if primary_norm in categories:
                categories.remove(primary_norm)
            categories.insert(0, primary_norm)
        max_categories = max(
            1,
            self._cfg_int("collected_sticker_max_categories", 1),
        )
        if not categories or len(categories) > max_categories:
            return None
        return categories

    def _parse_collected_categories(self, raw: Any) -> list[str] | None:
        value = str(raw or "").strip()
        if not self._cfg_bool("collected_sticker_multi_category_enabled"):
            category = self._normalize_collected_category(value)
            return [category] if category is not None else None
        if not value:
            category = self._normalize_collected_category("")
            return [category] if category is not None else None
        separators = self._cfg_str(
            "collected_sticker_category_separators"
        ).strip() or ",，、;；|/"
        pattern = "[" + re.escape(separators) + "]+"
        parts = [
            part.strip()
            for part in re.split(pattern, value)
            if part.strip()
        ]
        return self._normalize_collected_categories_list(parts)

    def _collected_category_dir(
        self,
        category: str,
        *,
        create: bool = False,
    ) -> Path | None:
        store = self._collected_store()
        if store is None:
            return None
        return store.category_dir(category, create=create)

    def _collected_file_path_for(
        self,
        category: str,
        filename: str,
    ) -> Path | None:
        store = self._collected_store()
        if store is None:
            return None
        return store.file_path(category, filename)

    def _valid_collected_entries(self) -> list[dict[str, Any]]:
        store = self._collected_store()
        if store is None:
            return []
        return store.valid_entries(self._collected_entries)

    def _load_collected_sticker_index(self) -> None:
        self._collected_entries = []
        store = self._collected_store()
        if store is None or not store.index_path.is_file():
            return
        try:
            self._collected_entries = store.load_index()
        except Exception:
            logger.warning(
                "qqbot_enhancer 收藏表情 index.json 无法解析，本次从空列表启动",
                exc_info=True,
            )

    def _format_collected_reply(self, template: str, **kwargs: Any) -> str:
        try:
            return str(template).format(**kwargs)
        except (KeyError, ValueError):
            return str(template)

    def _local_image_ref_path(self, ref: Any) -> Path | None:
        return media_local_image_ref_path(ref)

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

    def _prune_sticker_ai_auto_collect_times(self, now: float) -> None:
        cutoff = now - 3600
        for session, timestamps in list(
            self._sticker_ai_auto_collect_times.items()
        ):
            kept = [timestamp for timestamp in timestamps if timestamp >= cutoff]
            if kept:
                self._sticker_ai_auto_collect_times[session] = kept
            else:
                self._sticker_ai_auto_collect_times.pop(session, None)
        if len(self._sticker_ai_auto_collect_times) > 128:
            oldest = sorted(
                self._sticker_ai_auto_collect_times.items(),
                key=lambda pair: min(pair[1]) if pair[1] else 0,
            )
            for session, _ in oldest[
                : len(self._sticker_ai_auto_collect_times) - 128
            ]:
                self._sticker_ai_auto_collect_times.pop(session, None)

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
        data = decode_base64_payload(raw_base64)
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
        return image_dimensions(data, mime)

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

    @staticmethod
    def _parse_ai_sticker_judgement(text: str) -> dict[str, Any] | None:
        return parse_ai_sticker_judgement(text)

    async def _ai_judge_sticker_payload(
        self,
        event: AstrMessageEvent,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        provider_id = self._cfg_str(
            "sticker_ai_auto_collect_provider_id"
        ).strip()
        if not provider_id:
            provider_id = await self._get_current_provider_id(event)
        if not provider_id:
            return "unavailable", None
        image_base64 = base64.b64encode(payload["data"]).decode("ascii")
        prompt = (
            "请判断这张图片是否适合作为聊天表情包/贴纸。"
            "只输出一个 JSON 对象，不要 Markdown、不要解释："
            '{"is_sticker": true 或 false, '
            '"category": "建议分类（中文/字母/数字/-/_，最多32字）", '
            '"confidence": 0.0 到 1.0}。'
            "如果图片是普通风景/人物照片/截图/文档，请返回 false。"
        )
        try:
            response_text = await self._llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                image_urls=[f"base64://{image_base64}"],
                system_prompt=(
                    "你只负责判断是否为聊天表情包/贴纸，必须只输出 JSON。"
                    "不要调用工具，不要读取文件，不要输出路径或凭据。"
                ),
            )
        except Exception:
            logger.warning(
                "qqbot_enhancer AI 自动收藏视觉判断失败，回退本地启发式",
                exc_info=True,
            )
            return "error", None
        judgement = self._parse_ai_sticker_judgement(response_text)
        if judgement is None:
            logger.info(
                "qqbot_enhancer AI 自动收藏返回无法解析，回退本地启发式"
            )
            return "error", None
        return ("ok" if judgement["is_sticker"] else "not_sticker"), judgement

    async def _maybe_ai_auto_collect_user_sticker(
        self,
        event: AstrMessageEvent,
    ) -> str | None:
        if not self._cfg_bool("sticker_ai_auto_collect_enabled"):
            return None
        if not self._cfg_bool("collected_sticker_enabled"):
            return None
        if self._collected_root is None:
            return None
        if self._rng.random() >= self._cfg_float(
            "sticker_ai_auto_collect_probability",
            0.0,
            1.0,
        ):
            return None

        now = self._clock()
        cooldown = self._cfg_int("auto_collect_sticker_cooldown_seconds", 0)
        last = self._last_auto_collect_at.get(event.unified_msg_origin)
        if last is not None and now - last < cooldown:
            return None
        self._prune_sticker_ai_auto_collect_times(now)
        max_per_hour = self._cfg_int(
            "sticker_ai_auto_collect_max_per_hour",
            1,
        )
        recent_attempts = self._sticker_ai_auto_collect_times.get(
            event.unified_msg_origin,
            [],
        )
        if len(recent_attempts) >= max_per_hour:
            return None

        status, payload = await self._extract_collectible_image_payload(event)
        if status != "ok" or payload is None:
            return None
        data = payload.get("data")
        if not isinstance(data, bytes):
            return None
        max_bytes = self._cfg_int("auto_collect_sticker_max_file_bytes", 1)
        if len(data) > max_bytes:
            return None
        dimensions = self._image_dimensions(
            data,
            str(payload.get("mime", "")),
        )
        if dimensions is None:
            return None
        width, height = dimensions
        if width <= 0 or height <= 0:
            return None
        max_dimension = max(
            32,
            self._cfg_int("auto_collect_sticker_max_dimension", 32),
        )
        if max(width, height) > max_dimension:
            return None

        # Count the provider attempt against the hourly cap even if the AI
        # fails; this also prevents hot loops that burn tokens.
        self._last_auto_collect_at[event.unified_msg_origin] = now
        self._sticker_ai_auto_collect_times.setdefault(
            event.unified_msg_origin,
            [],
        ).append(now)

        default_category = self._normalize_collected_category(
            self._cfg_str("sticker_ai_auto_collect_default_category")
        ) or DEFAULT_COLLECTED_CATEGORY
        category = default_category
        judgement_status, judgement = await self._ai_judge_sticker_payload(
            event,
            payload,
        )
        if judgement_status == "not_sticker":
            return None
        if judgement_status == "ok" and judgement is not None:
            confidence = float(judgement.get("confidence", 1.0))
            if confidence >= self._cfg_float(
                "sticker_ai_auto_collect_min_confidence",
                0.0,
                1.0,
            ):
                suggested = self._normalize_collected_category(
                    judgement.get("category")
                )
                if suggested is not None:
                    category = suggested
            else:
                # Low-confidence AI result is treated as uncertain -> local
                # safe fallback.
                if not self._is_probable_sticker_payload(payload):
                    return None
        else:
            # Provider unavailable/unparseable -> local heuristic fallback.
            if not self._is_probable_sticker_payload(payload):
                return None

        save_status, _message = await self._save_collected_payload(
            event,
            category,
            payload,
        )
        if save_status != "saved":
            logger.info(
                "qqbot_enhancer AI 自动收藏未保存: %s",
                save_status,
            )
            return None
        return self._format_collected_reply(
            self._cfg_str("sticker_ai_auto_collect_saved_reply"),
            category=category,
        )

    async def _save_collected_payload(
        self,
        event: AstrMessageEvent,
        category: str,
        payload: dict[str, Any],
        *,
        tags: list[str] | None = None,
        meaning: str = "",
        tone: str = "",
        categories: list[str] | None = None,
    ) -> tuple[str, str]:
        if self._collected_root is None:
            return (
                "storage",
                self._cfg_str("collected_sticker_storage_unavailable_reply"),
            )
        normalized_categories = self._normalize_collected_categories_list(
            list(categories or [category]),
            primary=category,
        )
        if normalized_categories is None:
            return (
                "invalid",
                self._cfg_str(
                    "collected_sticker_invalid_category_reply"
                ),
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
                "categories": normalized_categories,
                "size": int(payload.get("size", len(payload["data"]))),
                "created_at": int(time.time()),
                "source": source,
                "tags": list(tags or []),
                "meaning": str(meaning or ""),
                "tone": str(tone or ""),
                "updated_at": int(time.time()),
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
            self._last_collected_by_session[event.unified_msg_origin] = {
                "sha256": str(payload["sha256"]),
                "category": category,
                "filename": filename,
                "saved_at": int(time.time()),
            }
            if len(self._last_collected_by_session) > 256:
                oldest = sorted(
                    self._last_collected_by_session.items(),
                    key=lambda pair: int(
                        pair[1].get("saved_at", 0) or 0
                    ),
                )
                for session, _ in oldest[
                    : len(self._last_collected_by_session) - 256
                ]:
                    self._last_collected_by_session.pop(session, None)
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
        categories: list[str] | None = None,
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
            categories=categories,
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

    @staticmethod
    def _entry_categories(entry: dict[str, Any]) -> list[str]:
        categories = entry.get("categories")
        if isinstance(categories, (list, tuple, set)):
            result = [
                str(category).strip()
                for category in categories
                if str(category).strip()
            ]
            if result:
                return result
        category = str(
            entry.get("category", DEFAULT_COLLECTED_CATEGORY)
        ).strip()
        return [category] if category else []

    def _collected_category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._valid_collected_entries():
            for category in self._entry_categories(entry):
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
            entries = [
                entry
                for entry in entries
                if category in self._entry_categories(entry)
            ]
        if not entries:
            return None

        if category is None and message_text.strip():
            text = message_text.casefold()
            category_matches = [
                entry
                for entry in entries
                if any(
                    category.casefold() in text
                    for category in self._entry_categories(entry)
                )
                or any(
                    str(tag or "").strip().casefold()
                    and str(tag or "").strip().casefold() in text
                    for tag in entry.get("tags", [])
                )
                or (
                    str(entry.get("meaning", "") or "").casefold()
                    and str(entry.get("meaning", "")).casefold() in text
                )
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

