"""Mock tests for plugins/qqbot_enhancer.

These tests intentionally do not require a real AstrBot installation. The very
small subset of AstrBot v4.29 APIs used by the plugin is stubbed in sys.modules.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import hashlib
import importlib.util
import inspect
import json as json_module
import os
import stat
import struct
import sys
import tempfile
import types
import unittest
import unittest.mock as mock
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "plugins" / "qqbot_enhancer" / "main.py"
STICKER_DIR = ROOT / "plugins" / "qqbot_enhancer" / "stickers"


# ---------------------------------------------------------------------------
# Minimal AstrBot v4.29 API stubs
# ---------------------------------------------------------------------------
class FakeLogger:
    def _noop(self, *args, **kwargs):
        return None

    debug = info = warning = error = exception = _noop


class FakeFilter:
    command_registrations = []

    class EventMessageType:
        GROUP_MESSAGE = "GroupMessage"
        PRIVATE_MESSAGE = "FriendMessage"
        OTHER_MESSAGE = "OtherMessage"
        ALL = "All"

    class PlatformAdapterType:
        AIOCQHTTP = 1
        QQOFFICIAL = 2
        QQOFFICIAL_WEBHOOK = 4

    @staticmethod
    def event_message_type(*args, **kwargs):
        return lambda func: func

    @staticmethod
    def platform_adapter_type(*args, **kwargs):
        return lambda func: func

    @staticmethod
    def on_decorating_result(*args, **kwargs):
        return lambda func: func

    @staticmethod
    def command(*args, **kwargs):
        FakeFilter.command_registrations.append((args, kwargs))
        return lambda func: func


class FakeStar:
    def __init__(self, context, config=None):
        self.context = context


class FakeStarTools:
    data_dir = None

    @classmethod
    def get_data_dir(cls, plugin_name=None):
        if cls.data_dir is None:
            cls.data_dir = Path(tempfile.mkdtemp(prefix="qqbot_enhancer_test_data_"))
        path = Path(cls.data_dir) / str(plugin_name or "qqbot_enhancer")
        path.mkdir(parents=True, exist_ok=True)
        return path


class FakeContext:
    pass


class FakeAstrMessageEvent:
    pass


class Plain:
    def __init__(self, text):
        self.text = str(text)

    def __repr__(self):
        return f"Plain({self.text!r})"


class At:
    def __init__(self, qq=None, name="", **kwargs):
        self.qq = qq
        self.name = name

    def __repr__(self):
        return f"At({self.qq!r})"


class AtAll(At):
    def __init__(self, **kwargs):
        super().__init__(qq="all")


class Face:
    def __init__(self, id=1):
        self.id = id


class Reply:
    def __init__(self, chain=None, id=1, sender_id=0, **kwargs):
        self.chain = list(chain or [])
        self.id = id
        self.sender_id = sender_id


class Record:
    def __init__(self, file=None, url=None, path=None, **kwargs):
        self.file = file
        self.url = url
        self.path = path

    @staticmethod
    def fromFileSystem(path, **kwargs):
        return Record(file=f"file://{path}", path=str(path), **kwargs)


class Image:
    def __init__(self, file=None, url=None, path=None, data=None, **kwargs):
        self.file = file
        self.url = url
        self.path = path
        self.data = data

    @staticmethod
    def fromFileSystem(path, **kwargs):
        return Image(file=f"file://{path}", path=str(path), **kwargs)

    @staticmethod
    def fromURL(url, **kwargs):
        if not str(url).startswith(("http://", "https://")):
            raise ValueError("not an http(s) URL")
        return Image(file=str(url), url=str(url), **kwargs)

    async def convert_to_base64(self):
        if self.data is not None:
            return base64.b64encode(self.data).decode("ascii")
        # Deterministic tiny fake payload for tests that only verify wrapping.
        return "ZmFrZV9pbWFnZQ=="


def _install_astrbot_stubs():
    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    api.logger = FakeLogger()
    event = types.ModuleType("astrbot.api.event")
    event.__path__ = []
    event.filter = FakeFilter
    event.AstrMessageEvent = FakeAstrMessageEvent
    star = types.ModuleType("astrbot.api.star")
    star.__path__ = []
    star.Star = FakeStar
    star.Context = FakeContext
    star.StarTools = FakeStarTools
    components = types.ModuleType("astrbot.api.message_components")
    components.__path__ = []
    for cls in (Plain, At, AtAll, Face, Image, Record, Reply):
        setattr(components, cls.__name__, cls)

    astrbot.api = api
    api.event = event
    api.star = star
    api.message_components = components
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
            "astrbot.api.message_components": components,
        }
    )


def _load_plugin_module():
    _install_astrbot_stubs()
    spec = importlib.util.spec_from_file_location(
        "qqbot_enhancer_main_under_test",
        PLUGIN_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load_plugin_module()


# ---------------------------------------------------------------------------
# Test doubles for events/providers
# ---------------------------------------------------------------------------
class FakeResult:
    def __init__(self, chain):
        self.chain = list(chain)

    @property
    def text(self):
        return "".join(
            comp.text for comp in self.chain if isinstance(comp, Plain)
        )


class FakeEvent:
    def __init__(
        self,
        components,
        *,
        message_str="",
        private=False,
        group_id="group-1",
        session_id="session-1",
        self_id="10001",
        sender_id="20002",
        sender_name="测试用户",
    ):
        self.message_obj = SimpleNamespace(
            type="FriendMessage" if private else "GroupMessage",
            message=list(components),
            self_id=self_id,
            group_id="" if private else group_id,
            sender=SimpleNamespace(user_id=sender_id, nickname=sender_name),
        )
        self.message_str = message_str
        self.private = private
        self.session_id = session_id
        self.unified_msg_origin = (
            f"aiocqhttp:{'FriendMessage' if private else 'GroupMessage'}:{session_id}"
        )
        # AstrBot 的真实默认值是 False；should_call_llm(True) 表示阻断默认 LLM。
        self.call_llm = False
        self.should_call_llm_calls = []
        self.stopped = False
        self.is_at_or_wake_command = private
        self._extras = {}
        self.result = None
        self.tracked_temporary_local_files = []

    def get_messages(self):
        return self.message_obj.message

    def get_message_type(self):
        return self.message_obj.type

    def is_private_chat(self):
        return self.private

    def get_group_id(self):
        return self.message_obj.group_id

    def get_self_id(self):
        return self.message_obj.self_id

    def get_sender_id(self):
        return self.message_obj.sender.user_id

    def get_sender_name(self):
        return self.message_obj.sender.nickname

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return "aiocqhttp"

    def should_call_llm(self, value):
        self.call_llm = value
        self.should_call_llm_calls.append(value)

    def stop_event(self):
        self.stopped = True

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key=None, default=None):
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def get_result(self):
        return self.result

    def track_temporary_local_file(self, path):
        self.tracked_temporary_local_files.append(str(path))

    def plain_result(self, text):
        return FakeResult([Plain(text)])

    def chain_result(self, chain):
        return FakeResult(chain)


class FakeProvider:
    def __init__(self, provider_id, modalities):
        self.provider_config = {"id": provider_id, "modalities": list(modalities)}

    def meta(self):
        return SimpleNamespace(id=self.provider_config["id"])


class FakeContext:
    def __init__(
        self,
        *,
        provider_id="deepseek-flash",
        modalities=("text", "image"),
        extra_providers=None,
        responder=None,
        global_provider_settings=None,
        stars=None,
    ):
        self.provider_id = provider_id
        self.providers = {}
        if provider_id:
            self.providers[provider_id] = FakeProvider(provider_id, modalities)
        for pid, pmodalities in (extra_providers or {}).items():
            self.providers[pid] = FakeProvider(pid, pmodalities)
        self.responder = responder
        self.calls = []
        self.stars = [] if stars is None else stars
        self.global_config = {
            "wake_prefix": ["/"],
            "provider_settings": {
                "default_image_caption_provider_id": "",
            },
        }
        if global_provider_settings:
            self.global_config["provider_settings"].update(global_provider_settings)

    async def get_current_chat_provider_id(self, umo):
        return self.provider_id

    def get_provider_by_id(self, provider_id):
        return self.providers.get(provider_id)

    async def get_using_provider_async(self, umo=None):
        return self.providers.get(self.provider_id)

    def get_using_provider(self, umo=None):
        return self.providers.get(self.provider_id)

    def get_config(self, umo=None):
        return self.global_config

    def get_all_stars(self):
        if isinstance(self.stars, BaseException):
            raise self.stars
        return self.stars

    async def llm_generate(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.responder is None:
            return SimpleNamespace(completion_text="默认回复")
        result = self.responder(kwargs)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, str):
            return SimpleNamespace(completion_text=result)
        if result is None:
            return SimpleNamespace(completion_text="")
        return result


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class FakeHttpResponse:
    def __init__(
        self,
        data,
        content_type="text/html; charset=utf-8",
        content_length=None,
        url="https://cdn.example.com/image.png",
    ):
        self._data = data
        self.url = url
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, size=-1):
        if size is None or size < 0:
            return self._data
        return self._data[:size]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FixedRng:
    def __init__(self, random_value=0.0, choice_index=0):
        self.random_value = random_value
        self.choice_index = choice_index

    def random(self):
        return self.random_value

    def choice(self, seq):
        seq = list(seq)
        return seq[self.choice_index % len(seq)]


def make_plugin(context, *, clock=None, **overrides):
    config = dict(MODULE.DEFAULTS)
    config.update(
        {
            "reply_probability": 1.0,
            "group_cooldown_seconds": 0,
            "user_cooldown_seconds": 0,
            "sticker_enabled": False,
            "user_sticker_reaction_enabled": False,
            "sticker_ai_select_enabled": False,
            "ai_unified_tool_protocol_enabled": False,
            "sticker_random_fallback_enabled": True,
            "llm_timeout_seconds": 5,
        }
    )
    config.update(overrides)
    plugin = MODULE.QQBotEnhancerPlugin(context, config)
    plugin._rng = FixedRng()
    plugin._clock = clock or FakeClock()
    return plugin


async def run_handler(plugin, event):
    results = []
    async for result in plugin.on_qq_message(event):
        results.append(result)
    return results


async def run_image_search_command(plugin, event):
    results = []
    async for result in plugin.find_image_command(event):
        results.append(result)
    return results


async def run_song_command(plugin, event):
    results = []
    async for result in plugin.local_song_command(event):
        results.append(result)
    return results


async def run_food_command(plugin, event):
    results = []
    async for result in plugin.food_command(event):
        results.append(result)
    return results


async def run_option_command(plugin, event):
    results = []
    async for result in plugin.option_command(event):
        results.append(result)
    return results


async def run_collect_sticker_command(plugin, event):
    results = []
    async for result in plugin.collect_sticker_command(event):
        results.append(result)
    return results


async def run_categories_command(plugin, event):
    results = []
    async for result in plugin.collected_categories_command(event):
        results.append(result)
    return results


async def run_list_command(plugin, event):
    results = []
    async for result in plugin.collected_list_command(event):
        results.append(result)
    return results


async def run_send_sticker_command(plugin, event):
    results = []
    async for result in plugin.send_collected_sticker_command(event):
        results.append(result)
    return results


def install_temp_collected_root(plugin, temp_dir):
    root = Path(temp_dir) / "collected_stickers"
    root.mkdir(parents=True, exist_ok=True)
    plugin._collected_root = root
    plugin._collected_entries = []
    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestMentionAndFilters(unittest.IsolatedAsyncioTestCase):
    def test_mention_detection_excludes_at_all(self):
        context = FakeContext()
        plugin = make_plugin(context)
        event = FakeEvent([Plain("hello")], self_id="10001")
        self.assertFalse(plugin._is_bot_mentioned(event, event.get_messages()))

        event = FakeEvent([At("99999"), Plain("hello")], self_id="10001")
        self.assertFalse(plugin._is_bot_mentioned(event, event.get_messages()))

        event = FakeEvent([At("10001"), Plain("hello")], self_id="10001")
        self.assertTrue(plugin._is_bot_mentioned(event, event.get_messages()))

        event = FakeEvent([AtAll(), Plain("hello")], self_id="10001")
        self.assertFalse(plugin._is_bot_mentioned(event, event.get_messages()))

        event = FakeEvent([At("all"), Plain("hello")], self_id="10001")
        self.assertFalse(plugin._is_bot_mentioned(event, event.get_messages()))

        event = FakeEvent([AtAll(), At("10001"), Plain("hello")], self_id="10001")
        self.assertTrue(plugin._is_bot_mentioned(event, event.get_messages()))

    def test_pure_image_face_empty_filtering(self):
        context = FakeContext()
        plugin = make_plugin(context)
        self.assertFalse(plugin._group_random_eligible("", [Image(url="https://x/y.png")]))
        self.assertFalse(plugin._group_random_eligible("", [Face(1)]))
        self.assertFalse(plugin._group_random_eligible("", []))
        self.assertFalse(plugin._group_random_eligible("hi", [Plain("hi")]))
        self.assertTrue(plugin._group_random_eligible("hello", [Plain("hello")]))

    async def test_group_pure_image_does_not_call_llm(self):
        context = FakeContext()
        plugin = make_plugin(context, reply_probability=1.0)
        event = FakeEvent([Image(url="https://x/y.png")], private=False)
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])
        self.assertTrue(event.call_llm)  # True 才是“阻断默认 LLM”
        self.assertEqual(event.should_call_llm_calls, [True])
        self.assertFalse(event.stopped)

    async def test_command_prefix_is_not_swallowed(self):
        context = FakeContext()
        plugin = make_plugin(context, reply_probability=1.0)
        event = FakeEvent([Plain("/help")], message_str="/help", private=False)
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])
        self.assertFalse(event.call_llm)  # 交给 AstrBot 指令/默认流程
        self.assertEqual(event.should_call_llm_calls, [])
        self.assertFalse(event.stopped)


class TestReplyRouting(unittest.IsolatedAsyncioTestCase):
    async def test_private_message_always_replies_and_blocks_duplicate(self):
        context = FakeContext(responder=lambda kwargs: "私聊回复")
        plugin = make_plugin(context)
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "私聊回复")
        self.assertEqual(len(context.calls), 1)
        self.assertEqual(context.calls[0]["chat_provider_id"], "deepseek-flash")
        self.assertTrue(event.call_llm)  # 阻断默认 LLM
        self.assertTrue(event.stopped)

    async def test_group_non_at_probability_and_group_cooldown(self):
        clock = FakeClock(100.0)
        context = FakeContext(responder=lambda kwargs: "概率回复")
        plugin = make_plugin(
            context,
            clock=clock,
            reply_probability=1.0,
            group_cooldown_seconds=60,
            user_cooldown_seconds=0,
        )
        first = FakeEvent(
            [Plain("今天天气不错呀")],
            message_str="今天天气不错呀",
            sender_id="user-a",
        )
        first_results = await run_handler(plugin, first)
        self.assertEqual(len(first_results), 1)
        self.assertTrue(first.stopped)
        self.assertTrue(first.call_llm)

        clock.value = 110.0
        second = FakeEvent(
            [Plain("再聊一句看看")],
            message_str="再聊一句看看",
            sender_id="user-b",
        )
        second_results = await run_handler(plugin, second)
        self.assertEqual(second_results, [])
        self.assertEqual(len(context.calls), 1)  # 冷却期内没有再调 LLM
        self.assertTrue(second.call_llm)  # 不回复时也必须阻断默认 LLM
        self.assertEqual(second.should_call_llm_calls, [True])
        self.assertFalse(second.stopped)

    async def test_at_bot_bypasses_random_cooldown(self):
        clock = FakeClock(100.0)
        context = FakeContext(responder=lambda kwargs: "收到")
        plugin = make_plugin(
            context,
            clock=clock,
            reply_probability=1.0,
            group_cooldown_seconds=999,
            user_cooldown_seconds=999,
        )
        first = FakeEvent([Plain("随便说点")], message_str="随便说点")
        await run_handler(plugin, first)
        self.assertEqual(len(context.calls), 1)

        mentioned = FakeEvent(
            [At("10001"), Plain("在吗")],
            message_str="在吗",
            group_id="group-1",
            session_id="session-1",
        )
        mentioned_results = await run_handler(plugin, mentioned)
        self.assertEqual(len(mentioned_results), 1)
        self.assertEqual(mentioned_results[0].text, "收到")
        self.assertEqual(len(context.calls), 2)
        self.assertTrue(mentioned.stopped)
        self.assertTrue(mentioned.call_llm)

    async def test_at_all_does_not_reply_from_random_and_blocks_default(self):
        context = FakeContext(responder=lambda kwargs: "不应调用")
        plugin = make_plugin(context, reply_probability=0.0)
        event = FakeEvent([AtAll(), Plain("大家好")], message_str="大家好")
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])
        self.assertTrue(event.call_llm)  # AtAll 不得唤醒默认 LLM
        self.assertEqual(event.should_call_llm_calls, [True])
        self.assertFalse(event.stopped)

    async def test_private_face_without_text_still_replies(self):
        context = FakeContext(responder=lambda kwargs: "表情收到")
        plugin = make_plugin(context)
        event = FakeEvent([Face(21)], message_str="", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertIn("QQ 表情", context.calls[0]["prompt"])
        self.assertTrue(event.stopped)


    async def test_explicit_message_memory_mode_delegates_to_default_agent(self):
        context = FakeContext(responder=lambda kwargs: "不应直接调用")
        plugin = make_plugin(context, memory_mode="astrbot")
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])
        self.assertTrue(event.is_at_or_wake_command)
        self.assertTrue(event.get_extra("qqbot_enhancer_memory_reply"))
        self.assertFalse(event.call_llm)
        self.assertFalse(event.stopped)

    async def test_random_message_memory_mode_delegates_to_default_agent(self):
        context = FakeContext(responder=lambda kwargs: "不应直接调用")
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            reply_probability=1.0,
            group_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        event = FakeEvent([Plain("今天天气不错呀")], message_str="今天天气不错呀")
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])
        self.assertTrue(event.is_at_or_wake_command)
        self.assertTrue(event.get_extra("qqbot_enhancer_memory_reply"))
        self.assertFalse(event.call_llm)
        self.assertFalse(event.stopped)

    async def test_memory_mode_non_reply_still_blocks_default_llm(self):
        context = FakeContext(responder=lambda kwargs: "不应调用")
        plugin = make_plugin(context, memory_mode="astrbot", reply_probability=0.0)
        event = FakeEvent([Plain("今天天气不错呀")], message_str="今天天气不错呀")
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])
        self.assertTrue(event.call_llm)
        self.assertFalse(event.is_at_or_wake_command)

    async def test_memory_mode_sticker_hook_appends_local_image(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
        )
        event = FakeEvent([Plain("哈哈")], message_str="哈哈")
        event.set_extra("qqbot_enhancer_memory_reply", True)
        event.set_extra("qqbot_enhancer_sticker_text", "哈哈")
        event.result = FakeResult([Plain("好呀")])
        await plugin.attach_memory_mode_sticker(event)
        self.assertTrue(any(isinstance(comp, Image) for comp in event.result.chain))

    async def test_yielded_result_occurs_before_stop_event(self):
        """handler 先 yield，finally 才 stop；因此下游 RespondStage 能发送结果。"""

        context = FakeContext(responder=lambda kwargs: "先回复")
        plugin = make_plugin(context)
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        stopped_at_yield = []
        seen_results = []
        async for result in plugin.on_qq_message(event):
            seen_results.append(result)
            stopped_at_yield.append(event.stopped)
        self.assertEqual(len(seen_results), 1)
        self.assertEqual(seen_results[0].text, "先回复")
        self.assertEqual(stopped_at_yield, [False])
        self.assertTrue(event.stopped)


BING_HTML_FIXTURE = """
<html><body>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://cdn.example.com/cat-one.jpg&quot;,&quot;t&quot;:&quot;千早爱音 猫 大树 山 测试图片一&quot;,&quot;desc&quot;:&quot;千早爱音 猫 大树 山 测试描述&quot;,&quot;purl&quot;:&quot;https://example.com/page-one&quot;,&quot;turl&quot;:&quot;https://bing.example/thumb.jpg&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://cdn.example.com/cat-two.png?w=640&amp;h=480&quot;,&quot;t&quot;:&quot;千早爱音 猫 大树 山 测试图片二&quot;,&quot;desc&quot;:&quot;千早爱音 猫 大树 山 测试描述二&quot;,&quot;purl&quot;:&quot;https://example.com/page-two&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;file:///etc/passwd&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;http://127.0.0.1/private.jpg&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://example.com/not-an-image.html&quot;}"></a>
<script>window.direct = "https://cdn.example.com/direct.webp";</script>
</body></html>
"""


BAIDU_JSON_FIXTURE = json_module.dumps(
    {
        "data": [
            {
                "middleURL": (
                    "https://img1.baidu.com/it/u=1,2&fm=253&fmt=auto&f=JPEG?w=500&h=500"
                ),
                "thumbURL": "https://img1.baidu.com/thumb-one.jpg",
                "fromPageTitleEnc": "千早爱音 猫 大树 百度图片一",
                "fromURLHost": "baike.baidu.com",
                "fromURL": "https://baike.baidu.com/item/千早爱音",
                "replaceUrl": [
                    {
                        "ObjURL": "https://example.com/original-one.jpg",
                        "FromURL": "https://example.com/source-one",
                    }
                ],
            },
            {
                "middleURL": "http://127.0.0.1/private.jpg",
                "thumbURL": "https://img0.baidu.com/thumb-two.png",
                "fromPageTitleEnc": "千早爱音 猫 大树 百度图片二",
            },
            {"middleURL": "file:///etc/passwd"},
        ]
    },
    ensure_ascii=False,
)


PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_test_png(width=32, height=32, tail=b""):
    """Small synthetic PNG header with valid width/height for heuristics."""

    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + tail
    )


UNRELATED_BING_HTML_FIXTURE = """
<html><body>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://cdn.example.com/landscape.jpg&quot;,&quot;t&quot;:&quot;美丽的山景风景照片&quot;,&quot;desc&quot;:&quot;自然风光山水&quot;,&quot;purl&quot;:&quot;https://example.com/landscape&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://cdn.example.com/food.jpg&quot;,&quot;t&quot;:&quot;美食晚餐&quot;,&quot;desc&quot;:&quot;餐厅菜品&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://cdn.example.com/no-meta.jpg&quot;}"></a>
</body></html>
"""


class TestImageSearch(unittest.IsolatedAsyncioTestCase):
    def test_extract_keyword_and_alias(self):
        context = FakeContext()
        plugin = make_plugin(context)
        event = FakeEvent([Plain("/找图 橘猫")], message_str="/找图 橘猫", private=True)
        self.assertEqual(plugin._extract_image_search_keyword(event), "橘猫")

        event = FakeEvent([Plain("/搜图 山水 风景")], message_str="/搜图 山水 风景", private=True)
        self.assertEqual(plugin._extract_image_search_keyword(event), "山水 风景")

        event = FakeEvent([Plain("/找图")], message_str="/找图", private=True)
        self.assertEqual(plugin._extract_image_search_keyword(event), "")

    def test_build_search_urls_encode_query_and_safe_search(self):
        context = FakeContext()
        plugin = make_plugin(context, image_search_safe_search="strict")
        bing_url = plugin._build_bing_image_search_url("橘猫 & 猫")
        parsed = urllib.parse.urlsplit(bing_url)
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "www.bing.com")
        self.assertEqual(params["q"], ["橘猫 & 猫"])
        self.assertEqual(params["adlt"], ["strict"])

        baidu_url = plugin._build_baidu_image_search_url("千早爱音")
        parsed = urllib.parse.urlsplit(baidu_url)
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "image.baidu.com")
        self.assertEqual(params["word"], ["千早爱音"])
        self.assertEqual(params["queryWord"], ["千早爱音"])

    def test_parse_bing_html_filters_unsafe_and_non_image_urls(self):
        context = FakeContext()
        plugin = make_plugin(context)
        urls = plugin._parse_bing_image_urls(BING_HTML_FIXTURE, limit=5)
        self.assertEqual(
            urls[:2],
            [
                "https://cdn.example.com/cat-one.jpg",
                "https://cdn.example.com/cat-two.png?w=640&h=480",
            ],
        )
        self.assertIn("https://cdn.example.com/direct.webp", urls)
        self.assertNotIn("file:///etc/passwd", urls)
        self.assertNotIn("http://127.0.0.1/private.jpg", urls)
        self.assertNotIn("https://example.com/not-an-image.html", urls)

    def test_parse_baidu_json_filters_unsafe_and_extracts_proxy_urls(self):
        context = FakeContext()
        plugin = make_plugin(context)
        urls = plugin._parse_baidu_image_urls(BAIDU_JSON_FIXTURE, limit=5)
        self.assertGreaterEqual(len(urls), 2)
        self.assertIn(
            "https://img1.baidu.com/it/u=1,2&fm=253&fmt=auto&f=JPEG?w=500&h=500",
            urls,
        )
        self.assertNotIn("http://127.0.0.1/private.jpg", urls)
        self.assertNotIn("file:///etc/passwd", urls)

    def test_download_validates_magic_content_type_and_headers(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_download_max_bytes=1024,
        )
        response = FakeHttpResponse(
            PNG_MAGIC_BYTES,
            content_type="image/png",
            content_length=len(PNG_MAGIC_BYTES),
        )
        captured_requests = []

        def fake_open(request, timeout):
            captured_requests.append((request, timeout))
            return response

        plugin._open_bounded_http = fake_open
        path = plugin._download_image_sync(
            "https://cdn.example.com/picture-without-extension",
            "https://www.bing.com/images/search",
        )
        self.assertTrue(Path(path).is_file())
        self.assertTrue(path.endswith(".png"))
        self.assertEqual(Path(path).read_bytes(), PNG_MAGIC_BYTES)
        headers = {key.lower(): value for key, value in captured_requests[0][0].headers.items()}
        self.assertIn("mozilla", headers["user-agent"].lower())
        self.assertEqual(headers["referer"], "https://www.bing.com/images/search")
        Path(path).unlink(missing_ok=True)

    def test_download_rejects_non_image_magic(self):
        context = FakeContext()
        plugin = make_plugin(context)
        response = FakeHttpResponse(
            b"<html>not an image</html>",
            content_type="image/png",
        )
        plugin._open_bounded_http = lambda request, timeout: response
        with self.assertRaises(MODULE.ImageSearchError):
            plugin._download_image_sync(
                "https://cdn.example.com/fake.png",
                "https://www.bing.com/images/search",
            )

    def test_download_rejects_oversized_image(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_download_max_bytes=1024,
        )
        response = FakeHttpResponse(
            PNG_MAGIC_BYTES * 100,
            content_type="image/png",
            content_length=1025,
        )
        plugin._open_bounded_http = lambda request, timeout: response
        with self.assertRaises(MODULE.ImageSearchError):
            plugin._download_image_sync(
                "https://cdn.example.com/large.png",
                "https://www.bing.com/images/search",
            )

    def test_relevance_normalizes_chinese_japanese_english(self):
        context = FakeContext()
        plugin = make_plugin(context)
        self.assertGreaterEqual(
            plugin._image_relevance_score("千早爱音", "千早 愛音 | MyGO", ""),
            0.6,
        )
        self.assertGreaterEqual(
            plugin._image_relevance_score(
                "千早爱音",
                "Anon Chihaya | MyGO",
                "Anon Chihaya character",
            ),
            0.6,
        )
        self.assertGreaterEqual(
            plugin._image_relevance_score("大树", "大树风景图片", ""),
            0.6,
        )
        self.assertGreaterEqual(
            plugin._image_relevance_score("cat", "cute cat wallpaper", ""),
            0.6,
        )
        self.assertLess(
            plugin._image_relevance_score("cat", "catalog page", ""),
            0.6,
        )
        self.assertLess(
            plugin._image_relevance_score("千早爱音", "美丽的山景", "山水"),
            0.6,
        )

    def test_candidate_parsers_preserve_title_and_description(self):
        context = FakeContext()
        plugin = make_plugin(context)
        bing_candidates = plugin._parse_bing_image_candidates(
            BING_HTML_FIXTURE,
            limit=5,
        )
        self.assertIn("千早爱音", bing_candidates[0]["title"])
        self.assertIn("测试描述", bing_candidates[0]["description"])
        self.assertEqual(
            bing_candidates[0]["page_url"],
            "https://example.com/page-one",
        )

        baidu_candidates = plugin._parse_baidu_image_candidates(
            BAIDU_JSON_FIXTURE,
            limit=5,
        )
        self.assertEqual(
            baidu_candidates[0]["title"],
            "千早爱音 猫 大树 百度图片一",
        )
        self.assertIn("baike.baidu.com", baidu_candidates[0]["description"])

    def test_relevance_filter_drops_unrelated_and_metadata_less_candidates(self):
        context = FakeContext()
        plugin = make_plugin(context)
        candidates = [
            {"url": "https://example.com/relevant.jpg", "title": "千早 愛音 MyGO", "description": "Anon"},
            {"url": "https://example.com/unrelated.jpg", "title": "山景风景", "description": "山水"},
            {"url": "https://example.com/no-meta.jpg", "title": "", "description": ""},
            {"url": "https://example.com/english.jpg", "title": "Anon Chihaya", "description": "MyGO"},
        ]
        filtered = plugin._filter_image_candidates_by_relevance(
            "千早爱音",
            candidates,
        )
        self.assertEqual(
            {item["url"] for item in filtered},
            {
                "https://example.com/relevant.jpg",
                "https://example.com/english.jpg",
            },
        )
        self.assertTrue(all(item["relevance_score"] >= 0.6 for item in filtered))

    async def test_command_does_not_download_when_all_bing_candidates_irrelevant(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_result_count=1,
            image_search_second_source_enabled=False,
            image_search_empty_reply="没有相关图片。",
        )
        plugin._fetch_image_search_html_sync = lambda url: UNRELATED_BING_HTML_FIXTURE
        download_calls = []

        async def fake_download(image_url, *, referer):
            download_calls.append(image_url)
            return "/tmp/should-not-exist.png"

        plugin._download_image = fake_download
        event = FakeEvent(
            [Plain("/找图 千早爱音")],
            message_str="/找图 千早爱音",
            private=True,
        )
        results = await run_image_search_command(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "没有相关图片。")
        self.assertEqual(len(results[0].chain), 1)
        self.assertEqual(download_calls, [])

    async def test_baidu_second_source_is_default_off_and_not_called(self):
        context = FakeContext()
        plugin = make_plugin(context)
        self.assertFalse(
            plugin._cfg_bool("image_search_second_source_enabled")
        )
        plugin._fetch_image_search_html_sync = lambda url: "<html>no images</html>"
        baidu_calls = []

        def fake_baidu(url):
            baidu_calls.append(url)
            return BAIDU_JSON_FIXTURE

        plugin._fetch_baidu_image_json_sync = fake_baidu
        event = FakeEvent(
            [Plain("/找图 千早爱音")],
            message_str="/找图 千早爱音",
            private=True,
        )
        results = await run_image_search_command(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "没有找到合适的图片，换个关键词试试吧。")
        self.assertEqual(baidu_calls, [])

    async def test_command_downloads_local_images_and_tracks_temp_files(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_result_count=2,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
            image_search_success_prefix="结果如下：",
        )
        plugin._fetch_image_search_html_sync = lambda url: BING_HTML_FIXTURE
        download_calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            local_one = Path(temp_dir) / "one.png"
            local_two = Path(temp_dir) / "two.png"
            local_one.write_bytes(PNG_MAGIC_BYTES)
            local_two.write_bytes(PNG_MAGIC_BYTES)

            async def fake_download(image_url, *, referer):
                download_calls.append((image_url, referer))
                return str(local_one if len(download_calls) == 1 else local_two)

            plugin._download_image = fake_download
            event = FakeEvent(
                [Plain("/找图 千早爱音")],
                message_str="/找图 千早爱音",
                private=True,
            )
            results = await run_image_search_command(plugin, event)
            self.assertEqual(len(results), 1)
            chain = results[0].chain
            self.assertEqual(chain[0].text, "结果如下：")
            self.assertEqual(len(chain), 3)
            self.assertIsInstance(chain[1], Image)
            self.assertEqual(chain[1].path, str(local_one))
            self.assertEqual(chain[2].path, str(local_two))
            self.assertEqual(len(download_calls), 2)
            self.assertEqual(
                download_calls[0][1],
                "https://www.bing.com/images/search",
            )
            self.assertIn(str(local_one), event.tracked_temporary_local_files)
            self.assertIn(str(local_two), event.tracked_temporary_local_files)
        self.assertEqual(context.calls, [])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.stopped)

    async def test_command_tries_next_candidate_when_download_fails(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_result_count=1,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
        )
        plugin._fetch_image_search_html_sync = lambda url: BING_HTML_FIXTURE
        attempted = []
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = Path(temp_dir) / "fallback.png"
            local_file.write_bytes(PNG_MAGIC_BYTES)

            async def flaky_download(image_url, *, referer):
                attempted.append(image_url)
                if len(attempted) == 1:
                    raise OSError("first URL failed")
                return str(local_file)

            plugin._download_image = flaky_download
            event = FakeEvent(
                [Plain("/找图 猫")],
                message_str="/找图 猫",
                private=True,
            )
            results = await run_image_search_command(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(attempted), 2)
            self.assertEqual(results[0].chain[1].path, str(local_file))

    async def test_command_remote_url_fallback_when_all_downloads_fail(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_result_count=1,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
        )
        plugin._fetch_image_search_html_sync = lambda url: BING_HTML_FIXTURE

        async def fail_download(image_url, *, referer):
            raise OSError("download failed")

        plugin._download_image = fail_download
        event = FakeEvent(
            [Plain("/找图 猫")],
            message_str="/找图 猫",
            private=True,
        )
        results = await run_image_search_command(plugin, event)
        self.assertEqual(len(results), 1)
        chain = results[0].chain
        self.assertIsInstance(chain[1], Image)
        self.assertEqual(chain[1].url, "https://cdn.example.com/cat-one.jpg")

    async def test_command_uses_baidu_when_bing_has_no_results(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_result_count=1,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
            image_search_second_source_enabled=True,
        )
        bing_urls = []
        baidu_urls = []

        def fake_bing(url):
            bing_urls.append(url)
            return "<html>no bing results</html>"

        def fake_baidu(url):
            baidu_urls.append(url)
            return BAIDU_JSON_FIXTURE

        plugin._fetch_image_search_html_sync = fake_bing
        plugin._fetch_baidu_image_json_sync = fake_baidu
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = Path(temp_dir) / "baidu.png"
            local_file.write_bytes(PNG_MAGIC_BYTES)

            async def fake_download(image_url, *, referer):
                self.assertIn("baidu.com", image_url)
                self.assertEqual(referer, "https://image.baidu.com/")
                return str(local_file)

            plugin._download_image = fake_download
            event = FakeEvent(
                [Plain("/找图 千早爱音")],
                message_str="/找图 千早爱音",
                private=True,
            )
            results = await run_image_search_command(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(bing_urls), 1)
            self.assertEqual(len(baidu_urls), 1)
            self.assertIn("baidu.com", baidu_urls[0])
            self.assertEqual(results[0].chain[1].path, str(local_file))

    async def test_command_no_results_returns_graceful_reply(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_second_source_enabled=False,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
            image_search_empty_reply="没有搜到，换一个词。",
        )
        plugin._fetch_image_search_html_sync = lambda url: "<html>no results</html>"
        event = FakeEvent(
            [Plain("/找图 不存在的词")],
            message_str="/找图 不存在的词",
            private=True,
        )
        results = await run_image_search_command(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "没有搜到，换一个词。")
        self.assertEqual(len(results[0].chain), 1)
        self.assertTrue(event.stopped)

    async def test_command_network_failure_returns_graceful_reply(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_second_source_enabled=False,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
            image_search_error_reply="搜索网络出错了。",
        )

        def fail(url):
            raise OSError("network down")

        plugin._fetch_image_search_html_sync = fail
        event = FakeEvent(
            [Plain("/找图 山")],
            message_str="/找图 山",
            private=True,
        )
        results = await run_image_search_command(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "搜索网络出错了。")
        self.assertTrue(event.stopped)

    async def test_command_cooldown_blocks_second_search(self):
        clock = FakeClock(100.0)
        calls = []

        def fake_fetch(url):
            calls.append(url)
            return BING_HTML_FIXTURE

        context = FakeContext()
        plugin = make_plugin(
            context,
            clock=clock,
            image_search_result_count=1,
            image_search_group_cooldown_seconds=60,
            image_search_user_cooldown_seconds=60,
        )
        plugin._fetch_image_search_html_sync = fake_fetch

        async def fake_download(image_url, *, referer):
            return "/tmp/fake-qqbot-search-image.png"

        plugin._download_image = fake_download
        first = FakeEvent(
            [Plain("/找图 猫")],
            message_str="/找图 猫",
            private=True,
            session_id="search-session",
        )
        first_results = await run_image_search_command(plugin, first)
        self.assertEqual(len(first_results), 1)
        self.assertEqual(len(calls), 1)

        clock.value = 110.0
        second = FakeEvent(
            [Plain("/找图 猫")],
            message_str="/找图 猫",
            private=True,
            session_id="search-session",
        )
        second_results = await run_image_search_command(plugin, second)
        self.assertEqual(len(second_results), 1)
        self.assertIn("50", second_results[0].text)
        self.assertEqual(len(calls), 1)  # 冷却期内不再发网络请求
        self.assertTrue(second.stopped)

    async def test_command_usage_when_keyword_missing(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_usage_reply="用法提示",
        )
        plugin._fetch_image_search_html_sync = lambda url: self.fail("不应调用")
        event = FakeEvent([Plain("/找图")], message_str="/找图", private=True)
        results = await run_image_search_command(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "用法提示")
        self.assertTrue(event.stopped)

    def test_response_size_limit_is_enforced(self):
        context = FakeContext()
        plugin = make_plugin(context, image_search_max_response_bytes=2048)
        response = FakeHttpResponse(b"x" * 2049, content_length=2049)
        with self.assertRaises(MODULE.ImageSearchError):
            plugin._read_search_response(response)

    async def test_disabled_search_returns_friendly_reply(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            image_search_enabled=False,
            image_search_disabled_reply="搜索已关闭。",
        )
        plugin._fetch_image_search_html_sync = lambda url: self.fail("不应调用")
        event = FakeEvent([Plain("/找图 猫")], message_str="/找图 猫", private=True)
        results = await run_image_search_command(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "搜索已关闭。")
        self.assertTrue(event.stopped)


class TestNaturalLanguageImageSearch(unittest.IsolatedAsyncioTestCase):
    def test_detects_conservative_search_intents(self):
        context = FakeContext()
        plugin = make_plugin(context)
        cases = {
            "你去找几张大树的图": ("大树", 2),
            "帮我搜两张千早爱音图片": ("千早爱音", 2),
            "帮我找几张关于大树的图片": ("大树", 2),
            "给我来两张猫的照片": ("猫", 2),
            "搜索 千早爱音 图片": ("千早爱音", 1),
            "我找不到图片了，帮我找几张猫图": ("猫", 2),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                intent = plugin._detect_natural_language_image_search(text)
                self.assertIsNotNone(intent)
                self.assertEqual(intent["keyword"], expected[0])
                self.assertEqual(intent["result_count"], expected[1])

    def test_ignores_negation_and_ordinary_chat(self):
        context = FakeContext()
        plugin = make_plugin(context)
        for text in (
            "我找不到图片了",
            "这张图真好看",
            "我没找到大树图片",
            "发几张图",
            "不要发图片",
            "我想找图片",
        ):
            with self.subTest(text=text):
                self.assertIsNone(
                    plugin._detect_natural_language_image_search(text)
                )

    async def test_private_natural_language_search_sends_and_blocks_llm(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(
            context,
            image_search_result_count=1,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
        )
        plugin._fetch_image_search_html_sync = lambda url: BING_HTML_FIXTURE
        download_calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = Path(temp_dir) / "nl-search.png"
            local_file.write_bytes(PNG_MAGIC_BYTES)

            async def fake_download(image_url, *, referer):
                download_calls.append(image_url)
                return str(local_file)

            plugin._download_image = fake_download
            event = FakeEvent(
                [Plain("帮我搜两张千早爱音图片")],
                message_str="帮我搜两张千早爱音图片",
                private=True,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(download_calls), 2)  # 数量词“两”覆盖默认结果数
            image_components = [
                comp for comp in results[0].chain if isinstance(comp, Image)
            ]
            self.assertEqual(len(image_components), 2)
            self.assertEqual(context.calls, [])
            self.assertTrue(event.call_llm)
            self.assertTrue(event.stopped)

    async def test_natural_language_search_reuses_image_search_cooldown(self):
        clock = FakeClock(100.0)
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(
            context,
            clock=clock,
            image_search_group_cooldown_seconds=60,
            image_search_user_cooldown_seconds=60,
        )
        fetch_calls = []

        def fake_fetch(url):
            fetch_calls.append(url)
            return BING_HTML_FIXTURE

        plugin._fetch_image_search_html_sync = fake_fetch
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = Path(temp_dir) / "cooldown.png"
            local_file.write_bytes(PNG_MAGIC_BYTES)

            async def fake_download(image_url, *, referer):
                return str(local_file)

            plugin._download_image = fake_download
            first = FakeEvent(
                [Plain("帮我搜两张千早爱音图片")],
                message_str="帮我搜两张千早爱音图片",
                private=True,
                session_id="nl-cooldown-session",
            )
            first_results = await run_handler(plugin, first)
            self.assertEqual(len(first_results), 1)
            self.assertEqual(len(fetch_calls), 1)

            clock.value = 110.0
            second = FakeEvent(
                [Plain("帮你找几张千早爱音图片")],
                message_str="帮你找几张千早爱音图片",
                private=True,
                session_id="nl-cooldown-session",
            )
            second_results = await run_handler(plugin, second)
            self.assertEqual(len(second_results), 1)
            self.assertIn("50", second_results[0].text)
            self.assertEqual(len(fetch_calls), 1)
            self.assertTrue(second.stopped)

    async def test_group_at_nl_search_runs_directly_even_in_astrbot_memory_mode(self):
        context = FakeContext(responder=lambda kwargs: "不应交给默认 Agent")
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            image_search_result_count=1,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
        )
        plugin._fetch_image_search_html_sync = lambda url: BING_HTML_FIXTURE
        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = Path(temp_dir) / "at-search.png"
            local_file.write_bytes(PNG_MAGIC_BYTES)

            async def fake_download(image_url, *, referer):
                return str(local_file)

            plugin._download_image = fake_download
            event = FakeEvent(
                [At("10001"), Plain("你去找几张大树的图")],
                message_str="你去找几张大树的图",
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertTrue(any(isinstance(comp, Image) for comp in results[0].chain))
            self.assertEqual(context.calls, [])
            self.assertTrue(event.call_llm)
            self.assertTrue(event.stopped)
            self.assertFalse(event.get_extra("qqbot_enhancer_memory_reply", False))

    async def test_non_at_group_message_does_not_trigger_nl_search(self):
        context = FakeContext(responder=lambda kwargs: "普通聊天回复")
        plugin = make_plugin(context, reply_probability=1.0)
        search_calls = []

        def fake_fetch(url):
            search_calls.append(url)
            return BING_HTML_FIXTURE

        plugin._fetch_image_search_html_sync = fake_fetch
        event = FakeEvent(
            [Plain("帮我搜两张千早爱音图片")],
            message_str="帮我搜两张千早爱音图片",
            private=False,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "普通聊天回复")
        self.assertEqual(search_calls, [])

    async def test_unrelated_private_message_goes_to_normal_chat(self):
        context = FakeContext(responder=lambda kwargs: "确实很好看")
        plugin = make_plugin(context)
        search_calls = []

        def fake_fetch(url):
            search_calls.append(url)
            return BING_HTML_FIXTURE

        plugin._fetch_image_search_html_sync = fake_fetch
        event = FakeEvent(
            [Plain("这张图真好看")],
            message_str="这张图真好看",
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "确实很好看")
        self.assertEqual(search_calls, [])
        self.assertEqual(len(context.calls), 1)
        self.assertTrue(event.stopped)


class TestImageHandling(unittest.IsolatedAsyncioTestCase):
    async def test_native_vision_provider_is_preferred(self):
        context = FakeContext(
            modalities=("text", "image"),
            responder=lambda kwargs: "图片里是一只猫",
        )
        plugin = make_plugin(context)
        event = FakeEvent(
            [
                Image(url="https://example.com/cat.png"),
                Plain("这是什么"),
            ],
            message_str="这是什么",
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "图片里是一只猫")
        self.assertEqual(len(context.calls), 1)
        self.assertEqual(
            context.calls[0]["image_urls"],
            ["https://example.com/cat.png"],
        )
        self.assertTrue(event.stopped)

    async def test_image_base64_conversion_fallback(self):
        context = FakeContext(
            modalities=("text", "image"),
            responder=lambda kwargs: "转换成功",
        )
        plugin = make_plugin(context)
        event = FakeEvent(
            [Image(file=None, url=None, path=None), Plain("看看")],
            message_str="看看",
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(
            context.calls[0]["image_urls"],
            ["base64://ZmFrZV9pbWFnZQ=="],
        )

    async def test_native_failure_uses_configured_caption_provider(self):
        def responder(kwargs):
            provider_id = kwargs["chat_provider_id"]
            if provider_id == "deepseek-flash" and kwargs.get("image_urls"):
                raise RuntimeError("image input unsupported")
            if provider_id == "vision-proxy":
                return "图中是一只橘猫，趴在窗台上。"
            if provider_id == "deepseek-flash":
                return "看起来是一只橘猫。"
            return "?"

        context = FakeContext(
            modalities=("text", "image"),
            extra_providers={"vision-proxy": ("image",)},
            responder=responder,
        )
        plugin = make_plugin(
            context,
            vision_provider_id="vision-proxy",
            auto_use_astrbot_caption_provider=False,
        )
        event = FakeEvent(
            [Image(url="https://example.com/cat.png"), Plain("描述一下")],
            message_str="描述一下",
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "看起来是一只橘猫。")
        self.assertEqual(
            [call["chat_provider_id"] for call in context.calls],
            ["deepseek-flash", "vision-proxy", "deepseek-flash"],
        )
        self.assertIn("橘猫", context.calls[-1]["prompt"])

    async def test_astrbot_default_caption_provider_is_used(self):
        def responder(kwargs):
            if kwargs["chat_provider_id"] == "vision-default":
                return "图中有一个红色气球。"
            return "红色气球很漂亮。"

        context = FakeContext(
            provider_id="deepseek-flash",
            modalities=("text",),
            extra_providers={"vision-default": ("image",)},
            responder=responder,
            global_provider_settings={
                "default_image_caption_provider_id": "vision-default",
            },
        )
        plugin = make_plugin(context, auto_use_astrbot_caption_provider=True)
        event = FakeEvent(
            [Image(url="https://example.com/balloon.png")],
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "红色气球很漂亮。")
        self.assertEqual(context.calls[0]["chat_provider_id"], "vision-default")
        self.assertEqual(context.calls[1]["chat_provider_id"], "deepseek-flash")

    async def test_no_vision_provider_returns_static_fallback(self):
        context = FakeContext(
            modalities=("text",),
            responder=lambda kwargs: "不应该被调用",
        )
        plugin = make_plugin(
            context,
            image_unavailable_reply="我暂时看不了这张图。",
            auto_use_astrbot_caption_provider=True,
        )
        event = FakeEvent(
            [Image(url="https://example.com/cat.png")],
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "我暂时看不了这张图。")
        self.assertEqual(context.calls, [])
        self.assertTrue(event.stopped)


class TestStickerHandling(unittest.IsolatedAsyncioTestCase):
    async def test_keyword_sticker_and_cooldown(self):
        clock = FakeClock(100.0)
        context = FakeContext(responder=lambda kwargs: "开心就好")
        plugin = make_plugin(
            context,
            clock=clock,
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=60,
        )
        first = FakeEvent(
            [Plain("今天真开心")],
            message_str="今天真开心",
            private=True,
            session_id="same-session",
        )
        first_results = await run_handler(plugin, first)
        self.assertEqual(len(first_results), 1)
        self.assertEqual(len(first_results[0].chain), 2)
        first_image = first_results[0].chain[1]
        self.assertIsInstance(first_image, Image)
        self.assertEqual(Path(first_image.path).name, "happy.png")

        clock.value = 110.0
        second = FakeEvent(
            [Plain("又很开心")],
            message_str="又很开心",
            private=True,
            session_id="same-session",
        )
        second_results = await run_handler(plugin, second)
        self.assertEqual(len(second_results), 1)
        self.assertEqual(len(second_results[0].chain), 1)  # cooldown 内只发文本

    async def test_prevent_repeat_chooses_another_sticker(self):
        clock = FakeClock(100.0)
        context = FakeContext(responder=lambda kwargs: "好的")
        plugin = make_plugin(
            context,
            clock=clock,
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=True,
        )
        first = FakeEvent(
            [Plain("开心")],
            message_str="开心",
            private=True,
            session_id="repeat-session",
        )
        first_results = await run_handler(plugin, first)
        first_name = Path(first_results[0].chain[1].path).name

        clock.value = 101.0
        second = FakeEvent(
            [Plain("随便聊聊")],
            message_str="随便聊聊",
            private=True,
            session_id="repeat-session",
        )
        second_results = await run_handler(plugin, second)
        second_name = Path(second_results[0].chain[1].path).name
        self.assertNotEqual(first_name, second_name)

    async def test_emotion_rule_selects_expected_sticker(self):
        context = FakeContext()
        plugin = make_plugin(context)
        selected = plugin._select_sticker("我有点好奇，这是啥", "emotion-session")
        self.assertIsNotNone(selected)
        self.assertEqual(Path(selected).name, "thinking.png")

    def test_at_least_eight_self_drawn_stickers_are_available(self):
        png_files = sorted(STICKER_DIR.glob("*.png"))
        self.assertGreaterEqual(len(png_files), 8)
        context = FakeContext()
        plugin = make_plugin(context)
        valid_entries = [
            entry
            for entry in plugin._sticker_entries
            if Path(entry["path"]).is_file()
        ]
        self.assertGreaterEqual(len(valid_entries), 8)

    async def test_missing_sticker_file_degrades_to_text(self):
        context = FakeContext(responder=lambda kwargs: "纯文本也行")
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_probability=1.0,
        )
        plugin._sticker_entries = [
            {
                "file": "missing.png",
                "path": STICKER_DIR / "missing.png",
                "keywords": [],
                "emotion": "",
            }
        ]
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 1)
        self.assertEqual(results[0].text, "纯文本也行")


class TestLocalSong(unittest.IsolatedAsyncioTestCase):
    async def test_song_command_sends_local_record_without_llm(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        with tempfile.TemporaryDirectory() as temp_dir:
            song_path = Path(temp_dir) / "authorized.wav"
            song_path.write_bytes(b"RIFF" + b"\x00" * 64)
            plugin = make_plugin(
                context,
                song_group_cooldown_seconds=0,
                song_user_cooldown_seconds=0,
                song_success_prefix="播放：{name}",
            )
            plugin._song_dir = Path(temp_dir)
            event = FakeEvent(
                [Plain("/唱歌")],
                message_str="/唱歌",
                private=True,
            )
            results = await run_song_command(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].chain[0].text, "播放：authorized")
            self.assertIsInstance(results[0].chain[1], Record)
            self.assertEqual(Path(results[0].chain[1].path).name, "authorized.wav")
            self.assertEqual(context.calls, [])
            self.assertTrue(event.call_llm)
            self.assertTrue(event.stopped)

    async def test_song_keyword_match_and_alias(self):
        context = FakeContext()
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "alpha.wav").write_bytes(b"alpha")
            (Path(temp_dir) / "beta.mp3").write_bytes(b"beta")
            plugin = make_plugin(
                context,
                song_group_cooldown_seconds=0,
                song_user_cooldown_seconds=0,
            )
            plugin._song_dir = Path(temp_dir)
            event = FakeEvent(
                [Plain("/点歌 beta")],
                message_str="/点歌 beta",
                private=True,
            )
            results = await run_song_command(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(Path(results[0].chain[1].path).name, "beta.mp3")

    async def test_song_missing_directory_returns_graceful_reply(self):
        context = FakeContext()
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = make_plugin(
                context,
                song_empty_reply="请把授权音频放进 songs/。",
            )
            plugin._song_dir = Path(temp_dir)
            event = FakeEvent([Plain("/唱歌")], message_str="/唱歌", private=True)
            results = await run_song_command(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].text, "请把授权音频放进 songs/。")
            self.assertEqual(context.calls, [])
            self.assertTrue(event.stopped)

    async def test_song_cooldown_blocks_second_send(self):
        clock = FakeClock(100.0)
        context = FakeContext()
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "song.wav").write_bytes(b"song")
            plugin = make_plugin(
                context,
                clock=clock,
                song_group_cooldown_seconds=60,
                song_user_cooldown_seconds=60,
            )
            plugin._song_dir = Path(temp_dir)
            first = FakeEvent(
                [Plain("/唱歌")],
                message_str="/唱歌",
                private=True,
                session_id="song-session",
            )
            first_results = await run_song_command(plugin, first)
            self.assertEqual(len(first_results), 1)
            self.assertIsInstance(first_results[0].chain[1], Record)

            clock.value = 110.0
            second = FakeEvent(
                [Plain("/唱歌")],
                message_str="/唱歌",
                private=True,
                session_id="song-session",
            )
            second_results = await run_song_command(plugin, second)
            self.assertEqual(len(second_results), 1)
            self.assertIn("50", second_results[0].text)
            self.assertTrue(second.stopped)

    async def test_song_oversized_file_is_rejected(self):
        context = FakeContext()
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "huge.wav").write_bytes(b"x" * 32)
            plugin = make_plugin(
                context,
                song_max_file_bytes=10,
                song_too_large_reply="这首歌文件太大了。",
            )
            plugin._song_dir = Path(temp_dir)
            event = FakeEvent([Plain("/唱歌")], message_str="/唱歌", private=True)
            results = await run_song_command(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].text, "这首歌文件太大了。")

    def test_song_path_traversal_and_indirect_files_rejected(self):
        context = FakeContext()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            plugin = make_plugin(context)
            plugin._song_dir = base
            outside = base.parent / "outside-song.wav"
            outside.write_bytes(b"outside")
            try:
                self.assertIsNone(plugin._resolve_song_file("../outside-song.wav"))
                link = base / "link.wav"
                try:
                    link.symlink_to(outside)
                except OSError:
                    link = None
                if link is not None:
                    self.assertIsNone(plugin._resolve_song_file("link.wav"))
            finally:
                outside.unlink(missing_ok=True)

    def test_no_copyright_audio_is_bundled(self):
        bundled_audio = [
            path
            for path in MODULE.SONG_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in MODULE.SONG_SUFFIXES
        ]
        self.assertEqual(bundled_audio, [])


class TestOptionCommand(unittest.IsolatedAsyncioTestCase):
    async def test_option_lists_self_commands_config_and_loaded_plugins(self):
        stars = [
            SimpleNamespace(
                name="qqbot_enhancer",
                display_name="QQ Bot 增强回复",
                version="1.4.1",
                desc="插件增强",
                activated=True,
            ),
            SimpleNamespace(
                name="other_plugin",
                display_name="其他插件",
                version="0.2.0",
                desc="其他插件说明",
                activated=False,
            ),
        ]
        context = FakeContext(
            stars=stars,
            responder=lambda kwargs: "不应调用 LLM",
            global_provider_settings={"api_key": "secret-should-not-appear"},
        )
        plugin = make_plugin(context)
        event = FakeEvent([Plain("/option")], message_str="/option", private=True)
        results = await run_option_command(plugin, event)
        self.assertEqual(len(results), 1)
        text = results[0].text
        self.assertIn("QQ Bot 增强回复", text)
        self.assertIn("v1.4.1", text)
        self.assertIn("/找图", text)
        self.assertIn("/搜图", text)
        self.assertIn("/唱歌", text)
        self.assertIn("/点歌", text)
        self.assertIn("/收藏表情", text)
        self.assertIn("/表情分类", text)
        self.assertIn("/表情列表", text)
        self.assertIn("/发表情", text)
        self.assertIn("/option", text)
        self.assertIn("配置入口", text)
        self.assertIn("memory_mode=", text)
        self.assertIn("其他插件", text)
        self.assertNotIn("secret-should-not-appear", text)
        self.assertIn("不逐个枚举其他插件命令", text)
        self.assertEqual(context.calls, [])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.stopped)

    async def test_option_falls_back_when_plugin_registry_unavailable(self):
        context = FakeContext(stars=RuntimeError("registry unavailable"))
        plugin = make_plugin(context)
        event = FakeEvent([Plain("/option")], message_str="/option", private=True)
        results = await run_option_command(plugin, event)
        self.assertEqual(len(results), 1)
        text = results[0].text
        self.assertIn("QQ Bot 增强回复", text)
        self.assertIn("仅列本插件", text)
        self.assertTrue(event.stopped)

    async def test_option_output_is_length_limited(self):
        context = FakeContext(stars=[])
        plugin = make_plugin(context, option_max_chars=200)
        event = FakeEvent([Plain("/option")], message_str="/option", private=True)
        results = await run_option_command(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(len(results[0].text), 200)
        self.assertTrue(results[0].text.endswith("…"))

    def test_option_command_aliases_are_registered(self):
        matching = [
            kwargs
            for args, kwargs in FakeFilter.command_registrations
            if args and args[0] == "option"
        ]
        self.assertTrue(matching)
        aliases = matching[0].get("alias", set())
        self.assertIn("插件用法", aliases)
        self.assertIn("用法", aliases)


class TestUserStickerReaction(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_face_reaction_can_attach_local_sticker(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(
            context,
            user_sticker_reaction_enabled=True,
            user_sticker_reaction_probability=1.0,
            user_sticker_reaction_attach_sticker=True,
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            collected_sticker_enabled=False,
        )
        event = FakeEvent([Face(21)], message_str="", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertGreaterEqual(len(results[0].chain), 1)
        self.assertIsInstance(results[0].chain[0], Plain)
        self.assertEqual(context.calls, [])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.stopped)

    async def test_explicit_image_reaction_without_sticker_attachment(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(
            context,
            user_sticker_reaction_enabled=True,
            user_sticker_reaction_probability=1.0,
            user_sticker_reaction_attach_sticker=False,
        )
        event = FakeEvent(
            [Image(data=PNG_MAGIC_BYTES)],
            message_str="",
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 1)
        self.assertIn(
            results[0].text,
            MODULE.DEFAULTS["user_sticker_reaction_replies"],
        )
        self.assertEqual(context.calls, [])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.stopped)

    async def test_non_at_group_face_does_not_trigger_reaction(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(
            context,
            user_sticker_reaction_enabled=True,
            user_sticker_reaction_probability=1.0,
            reply_probability=0.0,
        )
        event = FakeEvent([Face(21)], message_str="", private=False)
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])
        self.assertFalse(event.stopped)


class TestCollectedStickers(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, plugin, category, data=PNG_MAGIC_BYTES):
        event = FakeEvent(
            [
                Image(data=data),
                Plain(f"/收藏表情 {category}"),
            ],
            message_str=f"/收藏表情 {category}",
            private=True,
        )
        results = await run_collect_sticker_command(plugin, event)
        return event, results

    async def test_collect_category_dedup_and_atomic_index(self):
        context = FakeContext()
        plugin = make_plugin(context)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            event, results = await self._collect(plugin, "开心")
            self.assertEqual(len(results), 1)
            self.assertIn("已收藏到「开心」", results[0].text)
            self.assertEqual(len(plugin._collected_entries), 1)
            stored_path = plugin._collected_entries[0]["path"]
            self.assertTrue(stored_path.is_file())
            self.assertTrue((root / "index.json").is_file())
            index_data = json_module.loads(
                (root / "index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index_data["version"], 1)
            self.assertEqual(len(index_data["stickers"]), 1)
            self.assertNotIn("path", index_data["stickers"][0])
            self.assertTrue(event.stopped)

            duplicate_event, duplicate_results = await self._collect(
                plugin,
                "其它",
            )
            self.assertIn("已经收藏过", duplicate_results[0].text)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertTrue(duplicate_event.stopped)

    async def test_invalid_category_is_rejected(self):
        context = FakeContext()
        plugin = make_plugin(context)
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [
                    Image(data=PNG_MAGIC_BYTES),
                    Plain("/收藏表情 ../evil"),
                ],
                message_str="/收藏表情 ../evil",
                private=True,
            )
            results = await run_collect_sticker_command(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertIn("分类名不合法", results[0].text)
            self.assertEqual(plugin._collected_entries, [])

    async def test_collection_size_limit(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            collected_sticker_max_file_bytes=16,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            _, results = await self._collect(plugin, "大图")
            self.assertIn("超过收藏大小限制", results[0].text)
            self.assertEqual(plugin._collected_entries, [])

    async def test_collection_count_limit(self):
        context = FakeContext()
        plugin = make_plugin(context, collected_sticker_max_count=1)
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            _, first = await self._collect(plugin, "开心")
            self.assertIn("已收藏", first[0].text)
            _, second = await self._collect(
                plugin,
                "其它",
                PNG_MAGIC_BYTES + b"a",
            )
            self.assertIn("收藏数量已达上限", second[0].text)
            self.assertEqual(len(plugin._collected_entries), 1)

    async def test_no_image_and_face_only_have_friendly_replies(self):
        context = FakeContext()
        plugin = make_plugin(context)
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            no_image_event = FakeEvent(
                [Plain("/收藏表情")],
                message_str="/收藏表情",
                private=True,
            )
            no_image_results = await run_collect_sticker_command(
                plugin,
                no_image_event,
            )
            self.assertIn("没有可收藏的图片", no_image_results[0].text)

            face_event = FakeEvent(
                [Face(21), Plain("/收藏表情")],
                message_str="/收藏表情",
                private=True,
            )
            face_results = await run_collect_sticker_command(plugin, face_event)
            self.assertIn("QQ 原生 Face", face_results[0].text)
            self.assertEqual(plugin._collected_entries, [])

    async def test_recent_media_cache_enables_text_only_collection(self):
        context = FakeContext(responder=lambda kwargs: "收到图片")
        plugin = make_plugin(
            context,
            auto_collect_user_sticker_enabled=False,
            user_sticker_reaction_enabled=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            image_event = FakeEvent(
                [Image(data=make_test_png(64, 64, b"recent"))],
                message_str="",
                private=True,
                session_id="cache-session",
            )
            await run_handler(plugin, image_event)
            self.assertIsNotNone(
                plugin._get_recent_user_media(image_event.unified_msg_origin)
            )

            command_event = FakeEvent(
                [Plain("/收藏表情 猫猫")],
                message_str="/收藏表情 猫猫",
                private=True,
                session_id="cache-session",
            )
            results = await run_collect_sticker_command(plugin, command_event)
            self.assertIn("已收藏到「猫猫」", results[0].text)
            self.assertEqual(plugin._collected_entries[0]["category"], "猫猫")

    async def test_natural_language_collect_uses_recent_media(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(
            context,
            auto_collect_user_sticker_enabled=False,
            user_sticker_reaction_enabled=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            image_event = FakeEvent(
                [Image(data=make_test_png(48, 48, b"nl"))],
                message_str="",
                private=True,
                session_id="nl-collect-session",
            )
            await plugin._remember_recent_user_media(
                image_event,
                image_event.get_messages(),
            )
            collect_event = FakeEvent(
                [Plain("收藏表情 猫猫")],
                message_str="收藏表情 猫猫",
                private=True,
                session_id="nl-collect-session",
            )
            results = await run_handler(plugin, collect_event)
            self.assertEqual(len(results), 1)
            self.assertIn("已收藏到「猫猫」", results[0].text)
            self.assertEqual(context.calls, [])
            self.assertTrue(collect_event.call_llm)
            self.assertTrue(collect_event.stopped)

    async def test_quoted_image_precedes_cached_image(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            auto_collect_user_sticker_enabled=False,
            user_sticker_reaction_enabled=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            cached_data = PNG_MAGIC_BYTES + b"cached"
            quoted_data = PNG_MAGIC_BYTES + b"quoted"
            cache_event = FakeEvent(
                [Image(data=cached_data)],
                message_str="",
                private=True,
                session_id="quoted-session",
            )
            await plugin._remember_recent_user_media(
                cache_event,
                cache_event.get_messages(),
            )
            command_event = FakeEvent(
                [
                    Reply(chain=[Image(data=quoted_data)]),
                    Plain("/收藏表情 引用"),
                ],
                message_str="/收藏表情 引用",
                private=True,
                session_id="quoted-session",
            )
            results = await run_collect_sticker_command(plugin, command_event)
            self.assertIn("已收藏", results[0].text)
            self.assertEqual(
                plugin._collected_entries[0]["sha256"],
                hashlib.sha256(quoted_data).hexdigest(),
            )
            self.assertNotEqual(
                plugin._collected_entries[0]["sha256"],
                hashlib.sha256(cached_data).hexdigest(),
            )

    async def test_cache_expiry_returns_no_image_fallback(self):
        clock = FakeClock(100.0)
        context = FakeContext()
        plugin = make_plugin(
            context,
            clock=clock,
            auto_collect_user_sticker_enabled=False,
            user_sticker_reaction_enabled=False,
            pending_sticker_ttl_seconds=10,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            image_event = FakeEvent(
                [Image(data=make_test_png(32, 32, b"ttl"))],
                message_str="",
                private=True,
                session_id="ttl-session",
            )
            await plugin._remember_recent_user_media(
                image_event,
                image_event.get_messages(),
            )
            clock.value = 111.0
            command_event = FakeEvent(
                [Plain("/收藏表情")],
                message_str="/收藏表情",
                private=True,
                session_id="ttl-session",
            )
            results = await run_collect_sticker_command(plugin, command_event)
            self.assertIn("没有可收藏的图片", results[0].text)
            self.assertEqual(plugin._collected_entries, [])

    async def test_default_auto_collect_is_disabled_for_plain_images(self):
        context = FakeContext(responder=lambda kwargs: "普通图片回复")
        plugin = make_plugin(
            context,
            user_sticker_reaction_enabled=False,
        )
        self.assertFalse(
            plugin._cfg_bool("auto_collect_user_sticker_enabled")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(64, 64, b"default-off"))],
                message_str="",
                private=True,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].text, "普通图片回复")
            self.assertEqual(plugin._collected_entries, [])

    async def test_recent_media_cache_keeps_only_newest_three(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            auto_collect_user_sticker_enabled=False,
            user_sticker_reaction_enabled=False,
        )
        images = [
            Image(data=make_test_png(16, 16, bytes([i])))
            for i in range(4)
        ]
        event = FakeEvent(images, message_str="", private=True)
        await plugin._remember_recent_user_media(
            event,
            event.get_messages(),
        )
        cached = plugin._get_recent_user_media_list(
            event.unified_msg_origin
        )
        self.assertEqual(len(cached), 3)
        # Message order last image is newest and must be first in LRU list.
        self.assertEqual(
            cached[0]["sha256"],
            hashlib.sha256(images[-1].data).hexdigest(),
        )
        self.assertNotIn(
            hashlib.sha256(images[0].data).hexdigest(),
            {item["sha256"] for item in cached},
        )

    async def test_auto_collect_small_square_image_replies_once(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(
            context,
            auto_collect_user_sticker_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(64, 64, b"auto"))],
                message_str="",
                private=True,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(results[0].chain), 1)
            self.assertIn("已收藏到", results[0].text)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertEqual(
                plugin._collected_entries[0]["category"],
                "未分类",
            )
            self.assertEqual(context.calls, [])
            self.assertTrue(event.call_llm)
            self.assertTrue(event.stopped)

    async def test_auto_collect_skips_large_image(self):
        context = FakeContext(responder=lambda kwargs: "大图内容回复")
        plugin = make_plugin(
            context,
            user_sticker_reaction_enabled=False,
            auto_collect_user_sticker_enabled=True,
            auto_collect_sticker_max_dimension=512,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(1024, 768, b"large"))],
                message_str="",
                private=True,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].text, "大图内容回复")
            self.assertEqual(plugin._collected_entries, [])
            self.assertGreaterEqual(len(context.calls), 1)

    async def test_auto_collect_cooldown_limits_repeated_images(self):
        clock = FakeClock(100.0)
        context = FakeContext(responder=lambda kwargs: "普通图片回复")
        plugin = make_plugin(
            context,
            clock=clock,
            user_sticker_reaction_enabled=False,
            auto_collect_user_sticker_enabled=True,
            auto_collect_sticker_cooldown_seconds=60,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(64, 64, b"first"))],
                message_str="",
                private=True,
                session_id="auto-cooldown",
            )
            first_results = await run_handler(plugin, first)
            self.assertIn("已收藏到", first_results[0].text)
            self.assertEqual(len(plugin._collected_entries), 1)

            clock.value = 110.0
            second = FakeEvent(
                [Image(data=make_test_png(64, 64, b"second"))],
                message_str="",
                private=True,
                session_id="auto-cooldown",
            )
            second_results = await run_handler(plugin, second)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertEqual(second_results[0].text, "普通图片回复")
            self.assertGreaterEqual(len(context.calls), 1)

    async def test_categories_list_and_send_collected(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(context)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            await self._collect(plugin, "开心", PNG_MAGIC_BYTES)
            await self._collect(plugin, "日常", PNG_MAGIC_BYTES + b"b")

            categories_event = FakeEvent(
                [Plain("/表情分类")],
                message_str="/表情分类",
                private=True,
            )
            categories_results = await run_categories_command(
                plugin,
                categories_event,
            )
            category_text = categories_results[0].text
            self.assertIn("开心", category_text)
            self.assertIn("日常", category_text)
            self.assertIn("共 2 张", category_text)

            filename = plugin._collected_entries[0]["filename"]
            list_event = FakeEvent(
                [Plain("/表情列表 开心")],
                message_str="/表情列表 开心",
                private=True,
            )
            list_results = await run_list_command(plugin, list_event)
            list_text = list_results[0].text
            self.assertIn(filename, list_text)
            self.assertNotIn(str(root), list_text)

            send_event = FakeEvent(
                [Plain("/发表情 开心")],
                message_str="/发表情 开心",
                private=True,
            )
            send_results = await run_send_sticker_command(plugin, send_event)
            self.assertEqual(len(send_results), 1)
            self.assertEqual(len(send_results[0].chain), 2)
            self.assertIsInstance(send_results[0].chain[1], Image)
            self.assertTrue(Path(send_results[0].chain[1].path).is_file())
            self.assertEqual(context.calls, [])
            self.assertTrue(send_event.call_llm)
            self.assertTrue(send_event.stopped)

    async def test_collected_sticker_joins_normal_chat_reply(self):
        context = FakeContext(responder=lambda kwargs: "正常回复")
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            collected_sticker_enabled=True,
            collected_sticker_use_probability=1.0,
            user_sticker_reaction_enabled=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            category_dir = root / "开心"
            category_dir.mkdir()
            data = PNG_MAGIC_BYTES + b"normal"
            sha256 = __import__("hashlib").sha256(data).hexdigest()
            filename = sha256 + ".png"
            path = category_dir / filename
            path.write_bytes(data)
            plugin._collected_entries = [
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": "开心",
                    "size": len(data),
                    "created_at": 1,
                    "source": "test",
                    "path": path,
                }
            ]
            event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(results[0].chain), 2)
            self.assertEqual(results[0].chain[1].path, str(path))
            self.assertEqual(context.calls[0]["chat_provider_id"], "deepseek-flash")
            self.assertGreaterEqual(len(plugin._sticker_entries), 8)

    async def test_cold_start_loads_existing_index(self):
        old_data_dir = FakeStarTools.data_dir
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            root = data_dir / "qqbot_enhancer" / "collected_stickers"
            category_dir = root / "开心"
            category_dir.mkdir(parents=True)
            data = PNG_MAGIC_BYTES + b"cold"
            sha256 = __import__("hashlib").sha256(data).hexdigest()
            filename = sha256 + ".png"
            (category_dir / filename).write_bytes(data)
            index = {
                "version": 1,
                "stickers": [
                    {
                        "sha256": sha256,
                        "filename": filename,
                        "category": "开心",
                        "size": len(data),
                        "created_at": 10,
                        "source": "test",
                    }
                ],
            }
            (root / "index.json").write_text(
                json_module.dumps(index, ensure_ascii=False),
                encoding="utf-8",
            )
            FakeStarTools.data_dir = data_dir
            try:
                plugin = make_plugin(FakeContext())
                self.assertEqual(len(plugin._collected_entries), 1)
                self.assertEqual(
                    plugin._collected_entries[0]["category"],
                    "开心",
                )
                self.assertTrue(plugin._collected_entries[0]["path"].is_file())
                self.assertGreaterEqual(len(plugin._sticker_entries), 8)
            finally:
                FakeStarTools.data_dir = old_data_dir

    def test_collection_commands_are_registered(self):
        registered = {
            args[0] for args, _kwargs in FakeFilter.command_registrations if args
        }
        for command_name in ("收藏表情", "表情分类", "表情列表", "发表情"):
            self.assertIn(command_name, registered)


class TestAIContextStickerSelection(unittest.IsolatedAsyncioTestCase):
    async def test_direct_mode_marker_is_removed_and_builtin_sticker_attached(self):
        context = FakeContext(
            responder=lambda kwargs: "好呀 [[sticker:happy]]"
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_probability=0.0,
            sticker_cooldown_seconds=0,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_ai_select_probability=1.0,
        )
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "好呀")
        self.assertEqual(len(results[0].chain), 2)
        self.assertIsInstance(results[0].chain[1], Image)
        self.assertEqual(
            Path(results[0].chain[1].path).name,
            "happy.png",
        )
        self.assertEqual(len(context.calls), 1)
        system_prompt = context.calls[0]["system_prompt"]
        self.assertIn("可选表情标签", system_prompt)
        self.assertIn("happy", system_prompt)
        self.assertNotIn("tools", context.calls[0])
        self.assertNotIn("func_tool", context.calls[0])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.stopped)

    async def test_invalid_marker_is_stripped_and_keeps_plain_text(self):
        context = FakeContext(
            responder=lambda kwargs: "普通文本 [[sticker:../../etc/passwd]]"
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_ai_select_probability=1.0,
        )
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "普通文本")
        self.assertEqual(len(results[0].chain), 1)
        self.assertNotIsInstance(results[0].chain[0], Image)

    async def test_multiple_markers_are_removed_but_not_honored(self):
        context = FakeContext(
            responder=lambda kwargs: (
                "好呀 [[sticker:happy]] [[sticker:sad]]"
            )
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_probability=0.0,
            sticker_cooldown_seconds=0,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_ai_select_probability=1.0,
        )
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(results[0].text, "好呀")
        self.assertEqual(len(results[0].chain), 1)

    async def test_collected_category_tag_attaches_collected_sticker(self):
        context = FakeContext(
            responder=lambda kwargs: "喵 [[sticker:猫猫]]"
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_probability=0.0,
            sticker_cooldown_seconds=0,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_ai_select_probability=1.0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            category_dir = root / "猫猫"
            category_dir.mkdir()
            data = make_test_png(48, 48, b"cat")
            sha256 = hashlib.sha256(data).hexdigest()
            filename = sha256 + ".png"
            path = category_dir / filename
            path.write_bytes(data)
            plugin._collected_entries = [
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": "猫猫",
                    "size": len(data),
                    "created_at": 1,
                    "source": "test",
                    "path": path,
                }
            ]
            event = FakeEvent(
                [Plain("你好呀")],
                message_str="你好呀",
                private=True,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(results[0].text, "喵")
            self.assertEqual(results[0].chain[1].path, str(path))
            self.assertIn("猫猫", context.calls[0]["system_prompt"])

    async def test_marker_disabled_does_not_inject_system_instruction(self):
        context = FakeContext(responder=lambda kwargs: "普通回复")
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_probability=0.0,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=False,
        )
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(results[0].text, "普通回复")
        self.assertNotIn(
            "可选表情标签",
            context.calls[0]["system_prompt"],
        )

    async def test_direct_marker_mode_without_marker_keeps_text_only(self):
        context = FakeContext(responder=lambda kwargs: "普通文本")
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_ai_select_probability=1.0,
        )
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "普通文本")
        self.assertEqual(len(results[0].chain), 1)

    async def test_memory_mode_local_semantic_match_attaches_sticker(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=False,
            sticker_ai_select_enabled=True,
            sticker_ai_select_probability=1.0,
        )
        event = FakeEvent(
            [Plain("今天好开心")],
            message_str="今天好开心",
            private=True,
        )
        event.set_extra("qqbot_enhancer_memory_reply", True)
        event.set_extra("qqbot_enhancer_sticker_text", "今天好开心")
        event.result = FakeResult([Plain("太好了")])
        await plugin.attach_memory_mode_sticker(event)
        self.assertTrue(any(isinstance(comp, Image) for comp in event.result.chain))
        image = next(comp for comp in event.result.chain if isinstance(comp, Image))
        self.assertEqual(Path(image.path).name, "happy.png")
        self.assertEqual(context.calls, [])

    async def test_memory_mode_local_category_match_attaches_collected(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=False,
            collected_sticker_enabled=True,
            collected_sticker_use_probability=1.0,
            sticker_ai_select_enabled=True,
            sticker_ai_select_probability=1.0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            category_dir = root / "猫猫"
            category_dir.mkdir()
            data = make_test_png(48, 48, b"memory-cat")
            sha256 = hashlib.sha256(data).hexdigest()
            filename = sha256 + ".png"
            path = category_dir / filename
            path.write_bytes(data)
            plugin._collected_entries = [
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": "猫猫",
                    "size": len(data),
                    "created_at": 1,
                    "source": "test",
                    "path": path,
                }
            ]
            event = FakeEvent(
                [Plain("发个猫猫表情")],
                message_str="发个猫猫表情",
                private=True,
            )
            event.set_extra("qqbot_enhancer_memory_reply", True)
            event.set_extra("qqbot_enhancer_sticker_text", "发个猫猫表情")
            event.result = FakeResult([Plain("好呀")])
            await plugin.attach_memory_mode_sticker(event)
            images = [
                comp for comp in event.result.chain if isinstance(comp, Image)
            ]
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].path, str(path))
            self.assertEqual(context.calls, [])

    async def test_memory_mode_ai_probability_zero_does_not_attach(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_ai_select_enabled=True,
            sticker_ai_select_probability=0.0,
        )
        event = FakeEvent([Plain("开心")], message_str="开心", private=True)
        event.set_extra("qqbot_enhancer_memory_reply", True)
        event.set_extra("qqbot_enhancer_sticker_text", "开心")
        event.result = FakeResult([Plain("好呀")])
        await plugin.attach_memory_mode_sticker(event)
        self.assertFalse(
            any(isinstance(comp, Image) for comp in event.result.chain)
        )


class TestAIAutoCollectStickers(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_by_default_does_not_call_vision_or_collect(self):
        context = FakeContext()
        plugin = make_plugin(context)
        self.assertFalse(
            plugin._cfg_bool("sticker_ai_auto_collect_enabled")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(64, 64, b"off"))],
                message_str="",
                private=False,
                sender_id="other-group-member",
            )
            results = await run_handler(plugin, event)
            self.assertEqual(results, [])
            self.assertEqual(plugin._collected_entries, [])
            self.assertEqual(context.calls, [])

    async def test_enabled_any_group_member_ai_sticker_is_saved(self):
        context = FakeContext(
            responder=lambda kwargs: json_module.dumps(
                {
                    "is_sticker": True,
                    "category": "猫猫",
                    "confidence": 0.95,
                },
                ensure_ascii=False,
            )
        )
        plugin = make_plugin(
            context,
            sticker_ai_auto_collect_enabled=True,
            sticker_ai_auto_collect_probability=1.0,
            sticker_ai_auto_collect_max_per_hour=5,
            auto_collect_sticker_cooldown_seconds=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(64, 64, b"ai-save"))],
                message_str="",
                private=False,
                sender_id="other-group-member",
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertIn("这个表情我收下了", results[0].text)
            self.assertIn("猫猫", results[0].text)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertEqual(
                plugin._collected_entries[0]["category"],
                "猫猫",
            )
            self.assertEqual(len(context.calls), 1)
            self.assertNotIn("tools", context.calls[0])
            self.assertIn("base64://", context.calls[0]["image_urls"][0])
            self.assertTrue(event.call_llm)
            self.assertTrue(event.stopped)

    async def test_ai_failure_falls_back_to_local_heuristic(self):
        def fail(kwargs):
            raise RuntimeError("vision provider unavailable")

        context = FakeContext(responder=fail)
        plugin = make_plugin(
            context,
            sticker_ai_auto_collect_enabled=True,
            sticker_ai_auto_collect_probability=1.0,
            sticker_ai_auto_collect_max_per_hour=5,
            auto_collect_sticker_cooldown_seconds=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(64, 64, b"fallback"))],
                message_str="",
                private=False,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertIn("未分类", results[0].text)
            self.assertEqual(len(context.calls), 1)

    async def test_ai_says_not_sticker_does_not_collect_or_reply(self):
        context = FakeContext(
            responder=lambda kwargs: json_module.dumps(
                {
                    "is_sticker": False,
                    "category": "未分类",
                    "confidence": 0.99,
                },
                ensure_ascii=False,
            )
        )
        plugin = make_plugin(
            context,
            sticker_ai_auto_collect_enabled=True,
            sticker_ai_auto_collect_probability=1.0,
            auto_collect_sticker_cooldown_seconds=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(64, 64, b"not-sticker"))],
                message_str="",
                private=False,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(results, [])
            self.assertEqual(plugin._collected_entries, [])
            self.assertEqual(len(context.calls), 1)

    async def test_large_image_and_face_skip_ai_call(self):
        context = FakeContext(
            responder=lambda kwargs: self.fail("不应调用视觉 provider")
        )
        plugin = make_plugin(
            context,
            sticker_ai_auto_collect_enabled=True,
            sticker_ai_auto_collect_probability=1.0,
            auto_collect_sticker_cooldown_seconds=0,
            auto_collect_sticker_max_dimension=512,
            user_sticker_reaction_enabled=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            large = FakeEvent(
                [Image(data=make_test_png(1024, 768, b"large-ai"))],
                message_str="",
                private=False,
            )
            large_results = await run_handler(plugin, large)
            self.assertEqual(large_results, [])
            self.assertEqual(plugin._collected_entries, [])
            self.assertEqual(context.calls, [])

            face = FakeEvent([Face(21)], message_str="", private=False)
            face_results = await run_handler(plugin, face)
            self.assertEqual(face_results, [])
            self.assertEqual(plugin._collected_entries, [])
            self.assertEqual(context.calls, [])

    async def test_hourly_limit_prevents_more_vision_calls(self):
        clock = FakeClock(100.0)
        context = FakeContext(
            responder=lambda kwargs: json_module.dumps(
                {
                    "is_sticker": True,
                    "category": "表情",
                    "confidence": 0.99,
                },
                ensure_ascii=False,
            )
        )
        plugin = make_plugin(
            context,
            clock=clock,
            sticker_ai_auto_collect_enabled=True,
            sticker_ai_auto_collect_probability=1.0,
            sticker_ai_auto_collect_max_per_hour=1,
            auto_collect_sticker_cooldown_seconds=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(64, 64, b"first-ai"))],
                message_str="",
                private=False,
                session_id="ai-hourly",
            )
            first_results = await run_handler(plugin, first)
            self.assertEqual(len(first_results), 1)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertEqual(len(context.calls), 1)

            clock.value = 110.0
            second = FakeEvent(
                [Image(data=make_test_png(64, 64, b"second-ai"))],
                message_str="",
                private=False,
                session_id="ai-hourly",
            )
            second_results = await run_handler(plugin, second)
            self.assertEqual(second_results, [])
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertEqual(len(context.calls), 1)

    async def test_provider_override_and_malicious_category_are_safe(self):
        seen_providers = []

        def responder(kwargs):
            seen_providers.append(kwargs["chat_provider_id"])
            return json_module.dumps(
                {
                    "is_sticker": True,
                    "category": "../../etc/passwd",
                    "confidence": 0.99,
                },
                ensure_ascii=False,
            )

        context = FakeContext(
            responder=responder,
            extra_providers={"vision-collector": ("image",)},
        )
        plugin = make_plugin(
            context,
            sticker_ai_auto_collect_enabled=True,
            sticker_ai_auto_collect_probability=1.0,
            sticker_ai_auto_collect_provider_id="vision-collector",
            auto_collect_sticker_cooldown_seconds=0,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(48, 48, b"safe-cat"))],
                message_str="",
                private=False,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(seen_providers, ["vision-collector"])
            self.assertEqual(len(results), 1)
            self.assertEqual(
                plugin._collected_entries[0]["category"],
                "未分类",
            )
            self.assertNotIn(str(root), results[0].text)
            self.assertNotIn("passwd", results[0].text)

class TestModuleArchitecture(unittest.TestCase):
    def test_deploy_package_relative_imports_work(self):
        package_dir = PLUGIN_PATH.parent
        package_name = "qqbot_enhancer_deploy_test_pkg"
        init_spec = importlib.util.spec_from_file_location(
            package_name,
            package_dir / "__init__.py",
            submodule_search_locations=[str(package_dir)],
        )
        self.assertIsNotNone(init_spec)
        package = importlib.util.module_from_spec(init_spec)
        sys.modules[package_name] = package
        assert init_spec.loader is not None
        init_spec.loader.exec_module(package)

        main_spec = importlib.util.spec_from_file_location(
            f"{package_name}.main",
            PLUGIN_PATH,
        )
        self.assertIsNotNone(main_spec)
        deployed_main = importlib.util.module_from_spec(main_spec)
        sys.modules[f"{package_name}.main"] = deployed_main
        assert main_spec.loader is not None
        main_spec.loader.exec_module(deployed_main)
        schema = json_module.loads(
            (
                PLUGIN_PATH.parent / "_conf_schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(set(deployed_main.DEFAULTS), set(schema))
        self.assertTrue(hasattr(deployed_main, "QQBotEnhancerPlugin"))

    def test_config_reader_centralized_defaults_and_old_config(self):
        config_module = importlib.import_module("config")
        schema = json_module.loads(
            (
                PLUGIN_PATH.parent / "_conf_schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(set(config_module.DEFAULTS), set(schema))
        reader = config_module.ConfigReader({"sticker_probability": 0.9})
        self.assertAlmostEqual(
            reader.float("sticker_probability", 0.0, 1.0),
            0.9,
        )
        self.assertFalse(reader.bool("sticker_ai_auto_collect_enabled"))
        self.assertEqual(
            config_module.DEFAULTS["sticker_ai_auto_collect_enabled"],
            False,
        )

    def test_media_song_and_relevance_modules(self):
        media = importlib.import_module("media")
        image_search = importlib.import_module("image_search")
        songs = importlib.import_module("songs")

        png = make_test_png(48, 64, b"module-media")
        self.assertEqual(media.detect_image_magic(png), ("image/png", ".png"))
        self.assertEqual(media.image_dimensions(png, "image/png"), (48, 64))
        self.assertEqual(
            media.sanitize_http_url(
                "file:///etc/passwd",
                require_image_extension=False,
            ),
            "",
        )
        self.assertEqual(
            media.sanitize_http_url(
                "http://127.0.0.1/private.png",
                require_image_extension=True,
            ),
            "",
        )
        self.assertTrue(
            media.sanitize_http_url(
                "https://cdn.example.com/public.png",
                require_image_extension=True,
            )
        )
        self.assertGreaterEqual(
            image_search.relevance_score(
                "千早爱音",
                "Anon Chihaya | MyGO",
                "",
            ),
            0.6,
        )
        self.assertGreaterEqual(
            image_search.relevance_score(
                "千早爱音",
                "千早 愛音 | MyGO",
                "",
            ),
            0.6,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "alpha.mp3").write_bytes(b"authorized-audio")
            (root / "beta.wav").write_bytes(b"authorized-audio-2")
            (root / "ignore.txt").write_text("not audio")
            files, oversized = songs.list_song_files(
                root,
                max_bytes=1024,
            )
            self.assertEqual([path.name for path in files], ["alpha.mp3", "beta.wav"])
            self.assertEqual([path.name for path in oversized], [])
            repository = songs.LocalSongRepository(root)
            self.assertIsNotNone(repository.resolve("alpha.mp3"))
            self.assertIsNone(repository.resolve("../outside.mp3"))

    def test_sticker_store_and_old_index_compatibility(self):
        store_module = importlib.import_module("sticker_store")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            category_dir = root / "猫猫"
            category_dir.mkdir()
            data = make_test_png(32, 32, b"store-module")
            sha256 = hashlib.sha256(data).hexdigest()
            filename = sha256 + ".png"
            (category_dir / filename).write_bytes(data)

            def normalize(raw):
                value = str(raw or "").strip()
                if not value:
                    return "未分类"
                if len(value) > 32:
                    return None
                if not store_module.COLLECTED_CATEGORY_RE.fullmatch(value):
                    return None
                return value

            store = store_module.CollectedStickerStore(
                root,
                max_file_bytes=1024 * 1024,
                category_normalizer=normalize,
            )
            old_index = {
                "version": 1,
                "saved_at": 1,
                "stickers": [
                    {
                        "sha256": sha256,
                        "filename": filename,
                        "category": "猫猫",
                        "size": len(data),
                        "created_at": 1,
                        "source": "legacy",
                    }
                ],
            }
            (root / "index.json").write_text(
                json_module.dumps(old_index, ensure_ascii=False),
                encoding="utf-8",
            )
            loaded = store.load_index()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["category"], "猫猫")
            self.assertEqual(loaded[0]["categories"], ["猫猫"])
            self.assertEqual(loaded[0]["path"], category_dir / filename)
            self.assertEqual(store.categories(loaded), {"猫猫": 1})
            store.save_index(loaded)
            migrated_index = json_module.loads(
                (root / "index.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                migrated_index["stickers"][0]["categories"],
                ["猫猫"],
            )
            self.assertIsNotNone(store.find_by_sha(loaded, sha256))
            store.save_index(loaded)
            reloaded = store.load_index()
            self.assertEqual(len(reloaded), 1)

    def test_llm_helpers_parse_marker_and_judgement(self):
        llm = importlib.import_module("llm")
        clean, tag, present = llm.extract_sticker_ai_marker(
            "好呀 [[sticker:猫猫]]"
        )
        self.assertEqual(clean, "好呀")
        self.assertEqual(tag, "猫猫")
        self.assertTrue(present)
        self.assertEqual(
            llm.extract_sticker_ai_marker("好呀"),
            ("好呀", None, False),
        )
        judgement = llm.parse_ai_sticker_judgement(
            '```json\n{"is_sticker": true, '
            '"category": "猫猫", "confidence": 0.9}\n```'
        )
        self.assertEqual(judgement["category"], "猫猫")
        self.assertTrue(judgement["is_sticker"])
        self.assertEqual(
            llm.sticker_ai_system_instruction(["happy", "猫猫"]),
            "可选表情标签：happy, 猫猫。"
            "仅在确实贴切时，于回复末尾原样附加一个"
            "[[sticker:标签]] 标记；不需要就别输出标记，不要输出多个，不要解释标记。",
        )

    def test_safe_tools_whitelist_and_argument_validation(self):
        safe_tools = importlib.import_module("safe_tools")
        parsed = safe_tools.parse_tool_markers(
            '好呀 [[tool:search_images keyword="千早爱音" count=1]]'
        )
        self.assertTrue(parsed.had_marker)
        self.assertEqual(parsed.clean_text, "好呀")
        self.assertEqual(len(parsed.intents), 1)
        self.assertTrue(parsed.intents[0].valid)
        self.assertEqual(parsed.intents[0].args["keyword"], "千早爱音")

        valid_sticker = safe_tools.parse_tool_markers(
            '[[tool:send_sticker emotion="happy"]]'
        ).intents[0]
        self.assertTrue(valid_sticker.valid)
        valid_categories = safe_tools.parse_tool_markers(
            "[[tool:list_sticker_categories]]"
        ).intents[0]
        self.assertTrue(valid_categories.valid)

        invalid_markers = [
            '[[tool:send_sticker category="../../etc/passwd"]]',
            '[[tool:search_images keyword="file:///etc/passwd"]]',
            '[[tool:search_images keyword="cat" count=0]]',
            '[[tool:search_images keyword="cat" path="/tmp/x"]]',
            '[[tool:send_sticker category="猫猫" emotion="happy"]]',
            '[[tool:rm_rf path="/etc"]]',
            '[[tool:collect_sticker category="a b"]]',
        ]
        for marker in invalid_markers:
            parsed = safe_tools.parse_tool_markers(marker)
            self.assertTrue(parsed.had_marker, marker)
            self.assertEqual(len(parsed.intents), 1, marker)
            self.assertFalse(parsed.intents[0].valid, marker)
        self.assertIn("search_images", safe_tools.ALLOWED_TOOLS)
        self.assertNotIn("eval", safe_tools.ALLOWED_TOOLS)
        source = (
            PLUGIN_PATH.parent / "safe_tools.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"eval", "exec", "compile"}:
                    forbidden_calls.add(node.func.id)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if any(
                    name.split(".")[0] in {"subprocess", "os", "ctypes"}
                    for name in names
                ):
                    forbidden_calls.add("unsafe-import")
        self.assertEqual(forbidden_calls, set())

    def test_sticker_intent_module_emotion_category_and_generic(self):
        sticker_intent = importlib.import_module("sticker_intent")
        emotions = [
            "happy",
            "sad",
            "angry",
            "thinking",
            "love",
            "sleepy",
            "surprised",
            "cool",
        ]
        categories = ["猫猫"]
        self.assertEqual(
            sticker_intent.detect_sticker_request(
                "给我发个嘲讽表情包",
                available_emotions=emotions,
                available_categories=categories,
            )["value"],
            "angry",
        )
        self.assertEqual(
            sticker_intent.detect_sticker_request(
                "来一个开心的表情",
                available_emotions=emotions,
                available_categories=categories,
            )["value"],
            "happy",
        )
        self.assertEqual(
            sticker_intent.detect_sticker_request(
                "发张猫猫表情包",
                available_emotions=emotions,
                available_categories=categories,
            ),
            {"kind": "category", "value": "猫猫", "target": "猫猫"},
        )
        self.assertEqual(
            sticker_intent.detect_sticker_request(
                "发个表情",
                available_emotions=emotions,
                available_categories=categories,
            )["kind"],
            "generic",
        )
        self.assertIsNone(
            sticker_intent.detect_sticker_request(
                "今天天气真好",
                available_emotions=emotions,
                available_categories=categories,
            )
        )
        self.assertIsNone(
            sticker_intent.detect_sticker_request(
                "我发了个表情包",
                available_emotions=emotions,
                available_categories=categories,
            )
        )

    def test_sticker_reclassify_parser_examples_and_safety(self):
        reclassify = importlib.import_module("sticker_reclassify")
        examples = {
            "刚刚的表情包是嘲讽的意思": ("嘲讽", "angry"),
            "刚才那个表情包分类为猫猫": ("猫猫", "猫猫"),
            "这个表情是阴阳怪气": ("阴阳怪气", "angry"),
            "把上一个表情改成无语分类": ("无语", "angry"),
            "刚刚那个是开心的": ("开心", "happy"),
        }
        for text, (target, category) in examples.items():
            intent = reclassify.detect_reclassify_request(text)
            self.assertIsNotNone(intent, text)
            self.assertEqual(intent["target"], target)
            self.assertEqual(
                reclassify.classify_target_local(intent["target"]),
                category,
            )
        self.assertIsNone(
            reclassify.detect_reclassify_request("我觉得这个表情很嘲讽")
        )
        self.assertIsNone(
            reclassify.detect_reclassify_request("今天天气真好")
        )
        self.assertTrue(
            reclassify.looks_like_incomplete_reclassify_request(
                "刚刚的表情包分类为"
            )
        )

    def test_group_persona_parser_limits_and_resolution(self):
        personas = importlib.import_module("personas")
        raw = json_module.dumps(
            {
                "default": {"name": "默认", "prompt": "你是默认群人格"},
                "groups": {
                    "123456": {"name": "A", "prompt": "你是A群人格"},
                    "987654": "你是B群人格",
                    "bad id": "非法",
                },
            },
            ensure_ascii=False,
        )
        parsed = personas.parse_group_personas(
            raw,
            max_groups=10,
            max_prompt_chars=100,
            max_name_chars=20,
        )
        self.assertEqual(parsed.default.prompt, "你是默认群人格")
        self.assertEqual(parsed.groups["123456"].name, "A")
        self.assertEqual(parsed.groups["987654"].prompt, "你是B群人格")
        self.assertNotIn("bad id", parsed.groups)
        self.assertGreaterEqual(parsed.errors, 1)
        self.assertEqual(
            personas.resolve_persona(parsed, group_id="123456"),
            ("你是A群人格", "A", "group"),
        )
        self.assertEqual(
            personas.resolve_persona(parsed, group_id="000000"),
            ("你是默认群人格", "默认", "default"),
        )
        self.assertEqual(
            personas.resolve_persona(parsed, is_private=True),
            ("你是默认群人格", "默认", "default"),
        )

        too_long = json_module.dumps(
            {
                "groups": {
                    "1": "x" * 20,
                    "2": "y" * 20,
                    "3": "z" * 20,
                }
            }
        )
        limited = personas.parse_group_personas(
            too_long,
            max_groups=2,
            max_prompt_chars=10,
        )
        self.assertEqual(limited.groups, {})
        self.assertGreaterEqual(limited.errors, 3)
        self.assertFalse(personas.parse_group_personas("{bad json").groups)

    def test_protocol_interfaces_are_exposed(self):
        interfaces = importlib.import_module("interfaces")
        for name in (
            "StickerRepository",
            "MediaDecoder",
            "ImageSearchProvider",
            "SongRepository",
            "LLMService",
        ):
            self.assertTrue(hasattr(interfaces, name))
        payload = interfaces.MediaPayload(
            data=b"x",
            mime="image/png",
            extension=".png",
            sha256="a" * 64,
        )
        self.assertEqual(payload.extension, ".png")


class TestSafeToolRouter(unittest.IsolatedAsyncioTestCase):
    async def test_direct_search_images_tool_sends_one_image(self):
        context = FakeContext(
            responder=lambda kwargs: (
                '帮你找 [[tool:search_images keyword="千早爱音" count=1]]'
            )
        )
        plugin = make_plugin(
            context,
            image_search_group_cooldown_seconds=0,
            image_search_user_cooldown_seconds=0,
            safe_tools_enabled=True,
            safe_tools_allow_search_images=True,
        )

        async def fake_search(keyword):
            self.assertEqual(keyword, "千早爱音")
            return [
                {
                    "url": "https://cdn.example.com/anon.png",
                    "title": "千早爱音",
                    "description": "",
                }
            ]

        async def fake_download(event, candidates, *, result_limit):
            self.assertEqual(result_limit, 1)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(make_test_png(16, 16, b"tool-search"))
                path = f.name
            return [path], ["https://cdn.example.com/anon.png"]

        plugin._search_image_candidates = fake_search
        plugin._download_search_image_files = fake_download
        event = FakeEvent(
            [Plain("你好呀")],
            message_str="你好呀",
            private=True,
        )
        try:
            results = await run_handler(plugin, event)
        finally:
            # The fake download path is temporary and intentionally removed.
            pass
        self.assertEqual(len(results), 1)
        self.assertNotIn("[[tool:", results[0].text)
        self.assertEqual(len(results[0].chain), 2)
        self.assertIsInstance(results[0].chain[1], Image)
        self.assertIn("可选安全工具", context.calls[0]["system_prompt"])
        self.assertIn("search_images", context.calls[0]["system_prompt"])
        self.assertNotIn("tools", context.calls[0])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.stopped)

    async def test_direct_send_sticker_tool_sends_one_builtin(self):
        context = FakeContext(
            responder=lambda kwargs: (
                '接好 [[tool:send_sticker emotion="happy"]]'
            )
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_cooldown_seconds=0,
            safe_tools_enabled=True,
            safe_tools_allow_send_sticker=True,
        )
        event = FakeEvent([Plain("发个开心表情")], message_str="发个开心表情", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertNotIn("[[tool:", results[0].text)
        self.assertEqual(len(results[0].chain), 2)
        self.assertIsInstance(results[0].chain[1], Image)
        self.assertEqual(Path(results[0].chain[1].path).name, "happy.png")

    async def test_direct_collect_sticker_tool_uses_current_image(self):
        context = FakeContext(
            responder=lambda kwargs: (
                '收到 [[tool:collect_sticker category="猫猫"]]'
            )
        )
        plugin = make_plugin(
            context,
            safe_tools_enabled=True,
            safe_tools_allow_collect_sticker=True,
            safe_tools_collect_cooldown_seconds=0,
            auto_collect_user_sticker_enabled=False,
            sticker_ai_auto_collect_enabled=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            event = FakeEvent(
                [Image(data=make_test_png(48, 48, b"tool-collect"))],
                message_str="这张图怎么样",
                private=True,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertIn("猫猫", results[0].text)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertEqual(plugin._collected_entries[0]["category"], "猫猫")
            self.assertEqual(len(results[0].chain), 1)
            self.assertNotIsInstance(results[0].chain[0], Image)

    async def test_direct_list_sticker_categories_tool(self):
        context = FakeContext(
            responder=lambda kwargs: (
                '分类如下 [[tool:list_sticker_categories]]'
            )
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            safe_tools_enabled=True,
            safe_tools_allow_list_categories=True,
        )
        event = FakeEvent([Plain("有哪些表情分类")], message_str="有哪些表情分类", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertIn("happy", results[0].text)
        self.assertNotIn("[[tool:", results[0].text)
        self.assertEqual(len(results[0].chain), 1)
        self.assertNotIsInstance(results[0].chain[0], Image)

    async def test_invalid_path_and_disabled_permission_fall_back_to_text(self):
        context = FakeContext(
            responder=lambda kwargs: (
                '普通文本 [[tool:send_sticker category="../../etc/passwd"]]'
            )
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            safe_tools_enabled=True,
            safe_tools_allow_send_sticker=True,
        )
        event = FakeEvent([Plain("你好")], message_str="你好", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "普通文本")
        self.assertEqual(len(results[0].chain), 1)
        self.assertNotIn("passwd", results[0].text)

        disabled_context = FakeContext(
            responder=lambda kwargs: (
                '普通文本 [[tool:send_sticker emotion="happy"]]'
            )
        )
        disabled_plugin = make_plugin(
            disabled_context,
            sticker_enabled=True,
            safe_tools_enabled=True,
            safe_tools_allow_send_sticker=False,
        )
        disabled_event = FakeEvent([Plain("你好")], message_str="你好", private=True)
        disabled_results = await run_handler(disabled_plugin, disabled_event)
        self.assertEqual(disabled_results[0].text, "普通文本")
        self.assertEqual(len(disabled_results[0].chain), 1)

    async def test_multiple_tool_markers_do_not_execute(self):
        context = FakeContext(
            responder=lambda kwargs: (
                '不执行 [[tool:list_sticker_categories]] '
                '[[tool:send_sticker emotion="happy"]]'
            )
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            safe_tools_enabled=True,
            safe_tools_allow_send_sticker=True,
            safe_tools_allow_list_categories=True,
        )
        event = FakeEvent([Plain("你好")], message_str="你好", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "不执行")
        self.assertEqual(len(results[0].chain), 1)
        self.assertNotIsInstance(results[0].chain[0], Image)

    async def test_memory_mode_tool_marker_replaces_agent_chain(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            sticker_enabled=True,
            safe_tools_enabled=True,
            safe_tools_allow_list_categories=True,
        )
        event = FakeEvent(
            [Plain("有哪些分类")],
            message_str="有哪些分类",
            private=True,
        )
        event.set_extra("qqbot_enhancer_memory_reply", True)
        event.set_extra("qqbot_enhancer_sticker_text", "有哪些分类")
        event.result = FakeResult(
            [Plain("分类如下 [[tool:list_sticker_categories]]")]
        )
        await plugin.attach_memory_mode_sticker(event)
        self.assertEqual(len(event.result.chain), 1)
        self.assertIsInstance(event.result.chain[0], Plain)
        self.assertIn("可用表情分类", event.result.chain[0].text)
        self.assertIn("happy", event.result.chain[0].text)
        self.assertFalse(
            any(isinstance(comp, Image) for comp in event.result.chain)
        )

class TestCollectedStickerPermissions(unittest.IsolatedAsyncioTestCase):
    async def test_saved_dirs_and_files_are_0755_and_0644(self):
        plugin = make_plugin(
            FakeContext(),
            collected_sticker_enabled=True,
            collected_sticker_max_count=10,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            root.chmod(0o700)
            event = FakeEvent(
                [Image(data=make_test_png(48, 48, b"permission-save"))],
                message_str="",
                private=True,
            )
            status, payload = await plugin._extract_collectible_image_payload(
                event
            )
            self.assertEqual(status, "ok")
            save_status, _ = await plugin._save_collected_payload(
                event,
                "猫猫",
                payload,
            )
            self.assertEqual(save_status, "saved")
            category_dir = root / "猫猫"
            filename = (
                plugin._collected_entries[0]["sha256"] + ".png"
            )
            sticker_file = category_dir / filename
            index_file = root / "index.json"
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)
            self.assertEqual(
                stat.S_IMODE(category_dir.stat().st_mode),
                0o755,
            )
            self.assertEqual(
                stat.S_IMODE(sticker_file.stat().st_mode),
                0o644,
            )
            self.assertEqual(
                stat.S_IMODE(index_file.stat().st_mode),
                0o644,
            )
            self.assertEqual(plugin._collected_entries[0]["category"], "猫猫")

    def test_existing_tree_migration_skips_symlinks_and_preserves_files(self):
        store_module = importlib.import_module("sticker_store")
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "collected_stickers"
            category_dir = root / "猫猫"
            nested_dir = category_dir / "nested"
            nested_dir.mkdir(parents=True)
            root.chmod(0o700)
            category_dir.chmod(0o700)
            nested_dir.chmod(0o700)
            data = make_test_png(32, 32, b"migration-survives")
            sticker_file = category_dir / ("a" * 64 + ".png")
            sticker_file.write_bytes(data)
            sticker_file.chmod(0o600)
            index_file = root / "index.json"
            index_payload = b'{"version":1,"stickers":[]}'
            index_file.write_bytes(index_payload)
            index_file.chmod(0o600)
            nested_file = nested_dir / "legacy.txt"
            nested_file.write_text("keep-me", encoding="utf-8")
            nested_file.chmod(0o600)

            outside_dir = base / "outside"
            outside_dir.mkdir()
            outside_dir.chmod(0o700)
            outside_file = base / "outside.txt"
            outside_file.write_text("outside", encoding="utf-8")
            outside_file.chmod(0o600)
            try:
                os.symlink(outside_dir, root / "escape-dir")
                os.symlink(outside_file, category_dir / "escape.txt")
            except (OSError, NotImplementedError):
                self.skipTest("当前平台不支持创建符号链接")

            warnings = []
            store = store_module.CollectedStickerStore(
                root,
                max_file_bytes=1024 * 1024,
                category_normalizer=lambda raw: (
                    str(raw) if raw and len(str(raw)) <= 32 else "未分类"
                ),
                log_warning=warnings.append,
            )
            summary = store.migrate_permissions(base)
            self.assertGreaterEqual(summary["skipped"], 2)
            for directory in (root, category_dir, nested_dir):
                self.assertEqual(
                    stat.S_IMODE(directory.stat().st_mode),
                    0o755,
                )
            for file_path in (sticker_file, index_file, nested_file):
                self.assertEqual(
                    stat.S_IMODE(file_path.stat().st_mode),
                    0o644,
                )
            self.assertEqual(sticker_file.read_bytes(), data)
            self.assertEqual(index_file.read_bytes(), index_payload)
            self.assertEqual(nested_file.read_text(encoding="utf-8"), "keep-me")
            # Symlink targets outside the data root must never be chmodded.
            self.assertEqual(
                stat.S_IMODE(outside_dir.stat().st_mode),
                0o700,
            )
            self.assertEqual(
                stat.S_IMODE(outside_file.stat().st_mode),
                0o600,
            )

    def test_resolve_root_migrates_legacy_index_and_permissions(self):
        old_data_dir = MODULE.StarTools.data_dir
        with tempfile.TemporaryDirectory() as temp_dir:
            MODULE.StarTools.data_dir = Path(temp_dir)
            try:
                data_dir = Path(temp_dir) / "qqbot_enhancer"
                root = data_dir / "collected_stickers"
                category_dir = root / "猫猫"
                category_dir.mkdir(parents=True)
                data_dir.chmod(0o700)
                root.chmod(0o700)
                category_dir.chmod(0o700)
                data = make_test_png(40, 40, b"legacy-root-migration")
                sha256 = hashlib.sha256(data).hexdigest()
                filename = sha256 + ".png"
                sticker_file = category_dir / filename
                sticker_file.write_bytes(data)
                sticker_file.chmod(0o600)
                index_file = root / "index.json"
                index_file.write_text(
                    json_module.dumps(
                        {
                            "version": 1,
                            "saved_at": 1,
                            "stickers": [
                                {
                                    "sha256": sha256,
                                    "filename": filename,
                                    "category": "猫猫",
                                    "size": len(data),
                                    "created_at": 1,
                                    "source": "legacy",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                index_file.chmod(0o600)

                plugin = make_plugin(
                    FakeContext(),
                    collected_sticker_enabled=True,
                )
                self.assertEqual(
                    plugin._collected_root,
                    root.resolve(),
                )
                self.assertEqual(len(plugin._collected_entries), 1)
                for directory in (data_dir, root, category_dir):
                    self.assertEqual(
                        stat.S_IMODE(directory.stat().st_mode),
                        0o755,
                    )
                for file_path in (sticker_file, index_file):
                    self.assertEqual(
                        stat.S_IMODE(file_path.stat().st_mode),
                        0o644,
                    )
            finally:
                MODULE.StarTools.data_dir = old_data_dir

    def test_root_resolution_permission_failure_degrades_without_crash(self):
        with mock.patch.object(
            MODULE.StarTools,
            "get_data_dir",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            plugin = make_plugin(
                FakeContext(),
                collected_sticker_enabled=True,
            )
        self.assertIsNone(plugin._collected_root)
        self.assertEqual(plugin._collected_entries, [])

    def test_store_reclassify_moves_atomically_and_handles_same_and_symlink(self):
        store_module = importlib.import_module("sticker_store")
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "collected_stickers"
            store = store_module.CollectedStickerStore(
                root,
                max_file_bytes=1024 * 1024,
                category_normalizer=lambda raw: (
                    str(raw) if raw and len(str(raw)) <= 32 else None
                ),
            )
            data = make_test_png(40, 40, b"store-reclass")
            sha256 = hashlib.sha256(data).hexdigest()
            filename = sha256 + ".png"
            old_path = store.write_sticker("未分类", filename, data)
            entries = [
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": "未分类",
                    "size": len(data),
                    "created_at": 1,
                    "source": "test",
                    "path": old_path,
                }
            ]
            store.save_index(entries)
            status, new_entry = store.reclassify(entries, sha256, "angry")
            self.assertEqual(status, "moved")
            self.assertIsNotNone(new_entry)
            new_path = root / "angry" / filename
            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.is_file())
            self.assertEqual(
                stat.S_IMODE(new_path.parent.stat().st_mode),
                0o755,
            )
            self.assertEqual(
                stat.S_IMODE(new_path.stat().st_mode),
                0o644,
            )
            loaded = store.load_index()
            self.assertEqual(loaded[0]["category"], "angry")
            same_status, _ = store.reclassify(loaded, sha256, "angry")
            self.assertEqual(same_status, "same")

            outside = base / "outside-category"
            outside.mkdir()
            os.symlink(outside, root / "evil")
            conflict_status, conflict_entry = store.reclassify(
                loaded,
                sha256,
                "evil",
            )
            self.assertEqual(conflict_status, "storage")
            self.assertIsNone(conflict_entry)
            self.assertTrue(new_path.exists())
            self.assertEqual(store.load_index()[0]["category"], "angry")

    def test_store_reclassify_rolls_back_file_when_index_write_fails(self):
        store_module = importlib.import_module("sticker_store")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "collected_stickers"
            store = store_module.CollectedStickerStore(
                root,
                max_file_bytes=1024 * 1024,
                category_normalizer=lambda raw: str(raw),
            )
            data = make_test_png(32, 32, b"rollback-reclass")
            sha256 = hashlib.sha256(data).hexdigest()
            filename = sha256 + ".png"
            old_path = store.write_sticker("未分类", filename, data)
            entries = [
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": "未分类",
                    "size": len(data),
                    "created_at": 1,
                    "source": "test",
                    "path": old_path,
                }
            ]
            store.save_index(entries)
            original_save_index = store.save_index

            def fail_save_index(_entries):
                raise OSError("simulated index failure")

            store.save_index = fail_save_index
            with self.assertRaises(OSError):
                store.reclassify(entries, sha256, "happy")
            self.assertTrue(old_path.exists())
            self.assertFalse((root / "happy" / filename).exists())
            store.save_index = original_save_index
            self.assertEqual(store.load_index()[0]["category"], "未分类")

    def test_permission_failures_are_logged_not_raised(self):
        store_module = importlib.import_module("sticker_store")
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "collected_stickers"
            category_dir = root / "猫猫"
            category_dir.mkdir(parents=True)
            sticker_file = category_dir / ("b" * 64 + ".png")
            sticker_file.write_bytes(make_test_png(24, 24, b"no-crash"))
            warnings = []
            store = store_module.CollectedStickerStore(
                root,
                max_file_bytes=1024 * 1024,
                category_normalizer=lambda raw: "猫猫",
                log_warning=warnings.append,
            )
            with mock.patch.object(
                Path,
                "chmod",
                side_effect=PermissionError(13, "Permission denied"),
            ):
                summary = store.migrate_permissions(base)
            self.assertGreater(summary["errors"], 0)
            self.assertTrue(warnings)
            self.assertTrue(sticker_file.is_file())
            self.assertEqual(
                sticker_file.read_bytes(),
                make_test_png(24, 24, b"no-crash"),
            )

class TestNaturalLanguageStickerIntent(unittest.IsolatedAsyncioTestCase):
    async def test_private_snark_request_sends_angry_without_llm(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=False,
            sticker_natural_language_enabled=True,
            user_sticker_reaction_enabled=False,
        )
        event = FakeEvent(
            [Plain("给我发个嘲讽表情包")],
            message_str="给我发个嘲讽表情包",
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 2)
        self.assertIsInstance(results[0].chain[1], Image)
        self.assertEqual(Path(results[0].chain[1].path).name, "angry.png")
        self.assertEqual(context.calls, [])
        self.assertTrue(event.call_llm)
        self.assertTrue(event.stopped)

    async def test_group_at_happy_request_sends_one_sticker_without_llm(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=False,
            sticker_natural_language_enabled=True,
        )
        event = FakeEvent(
            [At("10001"), Plain("来一个开心的表情")],
            message_str="来一个开心的表情",
            private=False,
            self_id="10001",
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 2)
        self.assertEqual(Path(results[0].chain[1].path).name, "happy.png")
        self.assertEqual(context.calls, [])

    async def test_collected_category_exact_match_is_sent(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            collected_sticker_enabled=True,
            sticker_cooldown_seconds=0,
            sticker_natural_language_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            category_dir = root / "猫猫"
            category_dir.mkdir()
            data = make_test_png(40, 40, b"nl-cat")
            sha256 = hashlib.sha256(data).hexdigest()
            filename = sha256 + ".png"
            path = category_dir / filename
            path.write_bytes(data)
            plugin._collected_entries = [
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": "猫猫",
                    "size": len(data),
                    "created_at": 1,
                    "source": "test",
                    "path": path,
                }
            ]
            event = FakeEvent(
                [Plain("发张猫猫表情包")],
                message_str="发张猫猫表情包",
                private=True,
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(results[0].chain), 2)
            self.assertEqual(results[0].chain[1].path, str(path))
            self.assertEqual(context.calls, [])

    async def test_unknown_target_and_empty_pool_reply_text_only(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            sticker_natural_language_enabled=True,
            sticker_cooldown_seconds=0,
        )
        event = FakeEvent(
            [Plain("发张不存在分类的表情包")],
            message_str="发张不存在分类的表情包",
            private=True,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 1)
        self.assertIsInstance(results[0].chain[0], Plain)
        self.assertIn("没有找到", results[0].text)
        self.assertNotIn("[[sticker:", results[0].text)
        self.assertEqual(context.calls, [])

        plugin._sticker_entries = []
        plugin._cfg_bool = lambda key: (
            True if key == "sticker_natural_language_enabled" else False
        )
        empty_event = FakeEvent(
            [Plain("发个表情")],
            message_str="发个表情",
            private=True,
        )
        empty_results = await run_handler(plugin, empty_event)
        self.assertEqual(len(empty_results), 1)
        self.assertIn("暂时没有", empty_results[0].text)
        self.assertEqual(len(empty_results[0].chain), 1)

    async def test_non_at_group_request_does_not_trigger(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            reply_probability=0.0,
            sticker_enabled=True,
            sticker_natural_language_enabled=True,
        )
        event = FakeEvent(
            [Plain("给我发个嘲讽表情包")],
            message_str="给我发个嘲讽表情包",
            private=False,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])

    async def test_cooldown_and_disabled_fall_back_without_duplicate(self):
        clock = FakeClock(100.0)
        context = FakeContext()
        plugin = make_plugin(
            context,
            clock=clock,
            sticker_enabled=True,
            sticker_cooldown_seconds=60,
            sticker_natural_language_enabled=True,
        )
        first = FakeEvent(
            [Plain("发个表情")],
            message_str="发个表情",
            private=True,
            session_id="nl-cooldown",
        )
        first_results = await run_handler(plugin, first)
        self.assertEqual(len(first_results[0].chain), 2)

        clock.value = 101.0
        second = FakeEvent(
            [Plain("发个表情")],
            message_str="发个表情",
            private=True,
            session_id="nl-cooldown",
        )
        second_results = await run_handler(plugin, second)
        self.assertEqual(len(second_results), 1)
        self.assertIn("稍等", second_results[0].text)
        self.assertEqual(len(second_results[0].chain), 1)
        self.assertEqual(context.calls, [])

        disabled_context = FakeContext(responder=lambda kwargs: "普通回复")
        disabled_plugin = make_plugin(
            disabled_context,
            sticker_enabled=False,
            sticker_natural_language_enabled=False,
        )
        disabled_event = FakeEvent(
            [Plain("发个表情")],
            message_str="发个表情",
            private=True,
        )
        disabled_results = await run_handler(disabled_plugin, disabled_event)
        self.assertEqual(len(disabled_results), 1)
        self.assertEqual(disabled_results[0].text, "普通回复")
        self.assertEqual(len(disabled_results[0].chain), 1)

class TestNaturalLanguageReclassify(unittest.IsolatedAsyncioTestCase):
    async def test_auto_collected_sticker_is_moved_by_local_phrase(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            collected_sticker_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(48, 48, b"reclass-source"))],
                message_str="",
                private=True,
                session_id="reclass-local",
            )
            status, payload = await plugin._extract_collectible_image_payload(
                first
            )
            self.assertEqual(status, "ok")
            save_status, _ = await plugin._save_collected_payload(
                first,
                "未分类",
                payload,
            )
            self.assertEqual(save_status, "saved")
            old_path = plugin._collected_entries[0]["path"]
            self.assertEqual(plugin._collected_entries[0]["category"], "未分类")

            second = FakeEvent(
                [Plain("刚刚的表情包是嘲讽的意思")],
                message_str="刚刚的表情包是嘲讽的意思",
                private=True,
                session_id="reclass-local",
            )
            results = await run_handler(plugin, second)
            self.assertEqual(len(results), 1)
            self.assertIn("已记住", results[0].text)
            self.assertIn("嘲讽", results[0].text)
            self.assertNotIn("没存下来", results[0].text)
            self.assertEqual(plugin._collected_entries[0]["category"], "嘲讽")
            self.assertIn("嘲讽", plugin._collected_entries[0]["tags"])
            self.assertIn("讽刺", plugin._collected_entries[0]["meaning"])
            self.assertEqual(plugin._collected_entries[0]["tone"], "sarcastic")
            new_path = plugin._collected_entries[0]["path"]
            self.assertNotEqual(old_path, new_path)
            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.exists())
            self.assertEqual(
                stat.S_IMODE(new_path.parent.stat().st_mode),
                0o755,
            )
            self.assertEqual(
                stat.S_IMODE(new_path.stat().st_mode),
                0o644,
            )
            self.assertEqual(context.calls, [])

    async def test_local_auto_collect_then_reclassify_to_angry(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            collected_sticker_enabled=True,
            auto_collect_user_sticker_enabled=True,
            auto_collect_sticker_cooldown_seconds=0,
            sticker_natural_language_reclassify_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(64, 64, b"auto-reclass"))],
                message_str="",
                private=True,
                session_id="reclass-auto",
            )
            first_results = await run_handler(plugin, first)
            self.assertEqual(len(first_results), 1)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertEqual(plugin._collected_entries[0]["category"], "未分类")
            self.assertIn(
                first.unified_msg_origin,
                plugin._last_collected_by_session,
            )

            second = FakeEvent(
                [Plain("刚刚的表情包是嘲讽的意思")],
                message_str="刚刚的表情包是嘲讽的意思",
                private=True,
                session_id="reclass-auto",
            )
            second_results = await run_handler(plugin, second)
            self.assertEqual(len(second_results), 1)
            self.assertIn("已记住", second_results[0].text)
            self.assertIn("嘲讽", second_results[0].text)
            self.assertEqual(plugin._collected_entries[0]["category"], "嘲讽")
            self.assertEqual(context.calls, [])

    async def test_cached_uncollected_image_is_saved_to_target_category(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            collected_sticker_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(48, 48, b"cache-reclass"))],
                message_str="",
                private=True,
                session_id="reclass-cache",
            )
            await plugin._remember_recent_user_media(
                first,
                first.get_messages(),
            )
            self.assertNotIn(
                first.unified_msg_origin,
                plugin._last_collected_by_session,
            )

            second = FakeEvent(
                [Plain("这个表情是开心的")],
                message_str="这个表情是开心的",
                private=True,
                session_id="reclass-cache",
            )
            results = await run_handler(plugin, second)
            self.assertEqual(len(results), 1)
            self.assertIn("已记住", results[0].text)
            self.assertIn("开心", results[0].text)
            self.assertEqual(len(plugin._collected_entries), 1)
            self.assertEqual(plugin._collected_entries[0]["category"], "开心")
            self.assertIn("开心", plugin._collected_entries[0]["tags"])
            self.assertEqual(context.calls, [])

    async def test_ai_target_classification_with_safe_fallback(self):
        context = FakeContext(
            responder=lambda kwargs: json_module.dumps(
                {
                    "category": "猫猫",
                    "tags": ["可爱", "猫猫"],
                    "meaning": "用于表达可爱",
                    "tone": "cute",
                    "confidence": 0.95,
                },
                ensure_ascii=False,
            )
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            collected_sticker_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(48, 48, b"ai-reclass"))],
                message_str="",
                private=True,
                session_id="reclass-ai",
            )
            status, payload = await plugin._extract_collectible_image_payload(
                first
            )
            self.assertEqual(status, "ok")
            await plugin._save_collected_payload(first, "未分类", payload)
            second = FakeEvent(
                [Plain("刚刚那个表情分类为超级 可爱的猫猫")],
                message_str="刚刚那个表情分类为超级 可爱的猫猫",
                private=True,
                session_id="reclass-ai",
            )
            results = await run_handler(plugin, second)
            self.assertEqual(len(results), 1)
            self.assertIn("猫猫", results[0].text)
            self.assertEqual(plugin._collected_entries[0]["category"], "猫猫")
            self.assertEqual(
                plugin._collected_entries[0]["tags"],
                ["可爱", "猫猫"],
            )
            self.assertEqual(
                plugin._collected_entries[0]["meaning"],
                "用于表达可爱",
            )
            self.assertEqual(plugin._collected_entries[0]["tone"], "cute")
            self.assertEqual(len(context.calls), 1)
            self.assertIn("用户说明", context.calls[0]["prompt"])
            self.assertNotIn("tools", context.calls[0])

            disabled_context = FakeContext(
                responder=lambda kwargs: self.fail("AI 不应被调用")
            )
            disabled_plugin = make_plugin(
                disabled_context,
                sticker_enabled=True,
                collected_sticker_enabled=True,
                sticker_natural_language_reclassify_ai_enabled=False,
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                install_temp_collected_root(disabled_plugin, temp_dir)
                first = FakeEvent(
                    [Image(data=make_test_png(48, 48, b"ai-disabled"))],
                    message_str="",
                    private=True,
                    session_id="reclass-ai-disabled",
                )
                status, payload = await disabled_plugin._extract_collectible_image_payload(
                    first
                )
                await disabled_plugin._save_collected_payload(
                    first,
                    "未分类",
                    payload,
                )
                second = FakeEvent(
                    [Plain("刚刚那个表情分类为超级 可爱的猫猫")],
                    message_str="刚刚那个表情分类为超级 可爱的猫猫",
                    private=True,
                    session_id="reclass-ai-disabled",
                )
                results = await run_handler(disabled_plugin, second)
                self.assertEqual(len(results), 1)
                self.assertIn("没听清", results[0].text)
                self.assertEqual(
                    disabled_plugin._collected_entries[0]["category"],
                    "未分类",
                )
                self.assertEqual(disabled_context.calls, [])

    async def test_ai_unavailable_keeps_old_category_and_falls_back_text(self):
        def fail(kwargs):
            raise RuntimeError("classification provider unavailable")

        context = FakeContext(responder=fail)
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            collected_sticker_enabled=True,
            sticker_natural_language_reclassify_ai_enabled=True,
            sticker_natural_language_reclassify_min_confidence=0.6,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(40, 40, b"ai-unavailable"))],
                message_str="",
                private=True,
                session_id="reclass-ai-fail",
            )
            status, payload = await plugin._extract_collectible_image_payload(
                first
            )
            await plugin._save_collected_payload(first, "未分类", payload)
            second = FakeEvent(
                [Plain("刚刚那个表情分类为超级 可爱的猫猫")],
                message_str="刚刚那个表情分类为超级 可爱的猫猫",
                private=True,
                session_id="reclass-ai-fail",
            )
            results = await run_handler(plugin, second)
            self.assertEqual(len(results), 1)
            self.assertIn("没听清", results[0].text)
            self.assertEqual(plugin._collected_entries[0]["category"], "未分类")
            self.assertEqual(len(context.calls), 1)

    async def test_no_recent_media_and_ordinary_chat_do_not_move(self):
        context = FakeContext(responder=lambda kwargs: "普通聊天回复")
        plugin = make_plugin(
            context,
            collected_sticker_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            no_media = FakeEvent(
                [Plain("刚刚的表情包是嘲讽的意思")],
                message_str="刚刚的表情包是嘲讽的意思",
                private=True,
            )
            results = await run_handler(plugin, no_media)
            self.assertEqual(len(results), 1)
            self.assertIn("没有找到最近", results[0].text)
            self.assertEqual(context.calls, [])

            ordinary = FakeEvent(
                [Plain("我觉得这个表情很嘲讽")],
                message_str="我觉得这个表情很嘲讽",
                private=True,
            )
            ordinary_results = await run_handler(plugin, ordinary)
            self.assertEqual(len(ordinary_results), 1)
            self.assertEqual(ordinary_results[0].text, "普通聊天回复")
            self.assertEqual(len(context.calls), 1)

    async def test_reclassify_move_failure_keeps_old_entry(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            collected_sticker_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(48, 48, b"reclass-fail"))],
                message_str="",
                private=True,
                session_id="reclass-fail",
            )
            status, payload = await plugin._extract_collectible_image_payload(
                first
            )
            await plugin._save_collected_payload(first, "未分类", payload)
            old_path = plugin._collected_entries[0]["path"]
            store = plugin._collected_store()
            self.assertIsNotNone(store)
            original_save_index = store.save_index

            def fail_save(entries):
                raise OSError("index write failed")

            store.save_index = fail_save
            second = FakeEvent(
                [Plain("刚刚的表情包是嘲讽的意思")],
                message_str="刚刚的表情包是嘲讽的意思",
                private=True,
                session_id="reclass-fail",
            )
            # Patch the store factory used by the handler to use this store.
            plugin._collected_store = lambda: store
            results = await run_handler(plugin, second)
            self.assertEqual(len(results), 1)
            self.assertIn("分类失败", results[0].text)
            self.assertEqual(plugin._collected_entries[0]["category"], "未分类")
            self.assertTrue(old_path.exists())

    async def test_incomplete_and_marker_messages_do_not_move(self):
        context = FakeContext(responder=lambda kwargs: "普通回复")
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            collected_sticker_enabled=True,
            sticker_natural_language_reclassify_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            first = FakeEvent(
                [Image(data=make_test_png(40, 40, b"incomplete-reclass"))],
                message_str="",
                private=True,
                session_id="reclass-incomplete",
            )
            status, payload = await plugin._extract_collectible_image_payload(
                first
            )
            await plugin._save_collected_payload(first, "未分类", payload)
            incomplete = FakeEvent(
                [Plain("刚刚的表情包分类为")],
                message_str="刚刚的表情包分类为",
                private=True,
                session_id="reclass-incomplete",
            )
            incomplete_results = await run_handler(plugin, incomplete)
            self.assertEqual(len(incomplete_results), 1)
            self.assertIn("没听清", incomplete_results[0].text)
            self.assertEqual(plugin._collected_entries[0]["category"], "未分类")

            marker_text = (
                "刚刚的表情包是嘲讽的意思 [[sticker:happy]]"
            )
            marker_event = FakeEvent(
                [Plain(marker_text)],
                message_str=marker_text,
                private=True,
                session_id="reclass-incomplete",
            )
            await run_handler(plugin, marker_event)
            self.assertEqual(plugin._collected_entries[0]["category"], "未分类")

    async def test_non_at_group_reclassify_does_not_trigger(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            reply_probability=0.0,
            sticker_enabled=True,
            collected_sticker_enabled=True,
        )
        event = FakeEvent(
            [Plain("刚刚的表情包是嘲讽的意思")],
            message_str="刚刚的表情包是嘲讽的意思",
            private=False,
        )
        results = await run_handler(plugin, event)
        self.assertEqual(results, [])
        self.assertEqual(context.calls, [])

class TestMultiCategoryAndGlobalPool(unittest.IsolatedAsyncioTestCase):
    async def test_multi_category_collect_and_cross_session_commands(self):
        context = FakeContext(responder=lambda kwargs: "不应调用 LLM")
        plugin = make_plugin(
            context,
            collected_sticker_enabled=True,
            sticker_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            collect_event = FakeEvent(
                [
                    Image(data=make_test_png(48, 48, b"multi-category")),
                    Plain("/收藏表情 嘲讽,无语"),
                ],
                message_str="/收藏表情 嘲讽,无语",
                private=True,
                session_id="session-a",
            )
            collect_results = await run_collect_sticker_command(
                plugin,
                collect_event,
            )
            self.assertEqual(len(collect_results), 1)
            self.assertEqual(len(plugin._collected_entries), 1)
            entry = plugin._collected_entries[0]
            self.assertEqual(entry["category"], "嘲讽")
            self.assertEqual(entry["categories"], ["嘲讽", "无语"])
            filename = entry["filename"]
            category_files = [
                path for path in root.rglob(filename) if path.is_file()
            ]
            self.assertEqual(len(category_files), 1)

            categories_event = FakeEvent(
                [Plain("/表情分类")],
                message_str="/表情分类",
                private=True,
                session_id="session-b",
            )
            categories_results = await run_categories_command(
                plugin,
                categories_event,
            )
            category_text = categories_results[0].text
            self.assertIn("共 1 张", category_text)
            self.assertIn("分类归属 2 次", category_text)
            self.assertIn("嘲讽", category_text)
            self.assertIn("无语", category_text)

            list_event = FakeEvent(
                [Plain("/表情列表 无语")],
                message_str="/表情列表 无语",
                private=True,
                session_id="session-b",
            )
            list_results = await run_list_command(plugin, list_event)
            self.assertIn(filename, list_results[0].text)

            send_event = FakeEvent(
                [Plain("/发表情 无语")],
                message_str="/发表情 无语",
                private=True,
                session_id="session-b",
            )
            send_results = await run_send_sticker_command(plugin, send_event)
            self.assertEqual(len(send_results), 1)
            self.assertEqual(len(send_results[0].chain), 2)
            self.assertEqual(
                Path(send_results[0].chain[1].path),
                entry["path"],
            )
            self.assertEqual(context.calls, [])

    async def test_natural_add_remove_and_only_multiple_categories(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            collected_sticker_enabled=True,
            sticker_enabled=True,
            sticker_semantic_tags_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = install_temp_collected_root(plugin, temp_dir)
            collect_event = FakeEvent(
                [
                    Image(data=make_test_png(48, 48, b"multi-natural")),
                    Plain("/收藏表情 未分类"),
                ],
                message_str="/收藏表情 未分类",
                private=True,
                session_id="multi-natural",
            )
            await run_collect_sticker_command(plugin, collect_event)
            old_path = plugin._collected_entries[0]["path"]
            self.assertEqual(plugin._collected_entries[0]["categories"], ["未分类"])

            add_event = FakeEvent(
                [Plain("刚刚的表情包加入猫猫分类")],
                message_str="刚刚的表情包加入猫猫分类",
                private=True,
                session_id="multi-natural",
            )
            add_results = await run_handler(plugin, add_event)
            self.assertEqual(len(add_results), 1)
            self.assertIn("加入分类", add_results[0].text)
            self.assertEqual(
                plugin._collected_entries[0]["categories"],
                ["未分类", "猫猫"],
            )
            self.assertEqual(plugin._collected_entries[0]["path"], old_path)

            remove_event = FakeEvent(
                [Plain("刚刚的表情包移出猫猫分类")],
                message_str="刚刚的表情包移出猫猫分类",
                private=True,
                session_id="multi-natural",
            )
            remove_results = await run_handler(plugin, remove_event)
            self.assertEqual(len(remove_results), 1)
            self.assertIn("移出分类", remove_results[0].text)
            self.assertEqual(plugin._collected_entries[0]["categories"], ["未分类"])

            only_event = FakeEvent(
                [Plain("把上一个表情只放到猫猫,可爱分类")],
                message_str="把上一个表情只放到猫猫,可爱分类",
                private=True,
                session_id="multi-natural",
            )
            only_results = await run_handler(plugin, only_event)
            self.assertEqual(len(only_results), 1)
            self.assertEqual(
                plugin._collected_entries[0]["categories"],
                ["猫猫", "可爱"],
            )
            self.assertEqual(plugin._collected_entries[0]["category"], "猫猫")
            self.assertTrue(
                plugin._collected_entries[0]["path"].is_file()
            )
            self.assertIn("猫猫", str(plugin._collected_entries[0]["path"]))
            self.assertFalse(old_path.exists())
            self.assertEqual(context.calls, [])

    async def test_learned_tags_select_collected_sticker_across_sessions(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            collected_sticker_enabled=True,
            sticker_enabled=True,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=False,
            sticker_natural_language_enabled=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            source_event = FakeEvent(
                [Image(data=make_test_png(48, 48, b"tagged-sticker"))],
                message_str="",
                private=True,
                session_id="session-source",
            )
            status, payload = await plugin._extract_collectible_image_payload(
                source_event
            )
            save_status, _ = await plugin._save_collected_payload(
                source_event,
                "未分类",
                payload,
                tags=["嘲讽", "无语"],
                meaning="讽刺、无语或轻微挖苦",
                tone="sarcastic",
            )
            self.assertEqual(save_status, "saved")
            tagged_path = plugin._collected_entries[0]["path"]

            natural_event = FakeEvent(
                [Plain("发个嘲讽表情包")],
                message_str="发个嘲讽表情包",
                private=True,
                session_id="session-other",
            )
            natural_results = await run_handler(plugin, natural_event)
            self.assertEqual(len(natural_results), 1)
            self.assertEqual(len(natural_results[0].chain), 2)
            self.assertEqual(
                Path(natural_results[0].chain[1].path),
                tagged_path,
            )
            self.assertEqual(context.calls, [])

            safe_context = FakeContext(
                responder=lambda kwargs: (
                    '给你 [[tool:send_sticker tag="嘲讽"]]'
                )
            )
            safe_plugin = make_plugin(
                safe_context,
                collected_sticker_enabled=True,
                sticker_enabled=True,
                sticker_cooldown_seconds=0,
                sticker_prevent_repeat=False,
                safe_tools_enabled=True,
                safe_tools_allow_send_sticker=True,
            )
            safe_plugin._collected_root = plugin._collected_root
            safe_plugin._collected_entries = [dict(plugin._collected_entries[0])]
            safe_event = FakeEvent(
                [Plain("你好")],
                message_str="你好",
                private=True,
                session_id="session-safe-tag",
            )
            safe_results = await run_handler(safe_plugin, safe_event)
            self.assertEqual(len(safe_results), 1)
            self.assertEqual(len(safe_results[0].chain), 2)
            self.assertEqual(
                Path(safe_results[0].chain[1].path),
                tagged_path,
            )

    async def test_ai_marker_can_select_collected_tag(self):
        context = FakeContext(
            responder=lambda kwargs: "好呀 [[sticker:嘲讽]]"
        )
        plugin = make_plugin(
            context,
            sticker_enabled=True,
            collected_sticker_enabled=True,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_ai_select_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=False,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            install_temp_collected_root(plugin, temp_dir)
            source_event = FakeEvent(
                [Image(data=make_test_png(40, 40, b"ai-tag-marker"))],
                message_str="",
                private=True,
                session_id="ai-tag-source",
            )
            status, payload = await plugin._extract_collectible_image_payload(
                source_event
            )
            await plugin._save_collected_payload(
                source_event,
                "未分类",
                payload,
                tags=["嘲讽"],
                meaning="讽刺或无语",
                tone="sarcastic",
            )
            tagged_path = plugin._collected_entries[0]["path"]
            event = FakeEvent(
                [Plain("你好呀")],
                message_str="你好呀",
                private=True,
                session_id="ai-tag-call",
            )
            results = await run_handler(plugin, event)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(results[0].chain), 2)
            self.assertEqual(Path(results[0].chain[1].path), tagged_path)
            self.assertNotIn("[[sticker:", results[0].text)

    def test_store_add_remove_categories_and_file_stays_single(self):
        store_module = importlib.import_module("sticker_store")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "collected_stickers"
            store = store_module.CollectedStickerStore(
                root,
                max_file_bytes=1024 * 1024,
                category_normalizer=lambda raw: str(raw),
                max_categories=8,
            )
            data = make_test_png(32, 32, b"store-multi")
            sha256 = hashlib.sha256(data).hexdigest()
            filename = sha256 + ".png"
            old_path = store.write_sticker("未分类", filename, data)
            entries = [
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": "未分类",
                    "categories": ["未分类"],
                    "size": len(data),
                    "created_at": 1,
                    "source": "test",
                    "path": old_path,
                }
            ]
            store.save_index(entries)
            status, _ = store.add_categories(
                entries,
                sha256,
                ["嘲讽", "无语", "嘲讽"],
            )
            self.assertEqual(status, "updated")
            after_add = store.load_index()
            self.assertEqual(
                after_add[0]["categories"],
                ["未分类", "嘲讽", "无语"],
            )
            self.assertEqual(after_add[0]["path"], old_path)
            self.assertEqual(len(list(root.rglob(filename))), 1)

            status, _ = store.remove_categories(
                after_add,
                sha256,
                ["未分类"],
            )
            self.assertEqual(status, "updated")
            after_remove = store.load_index()
            self.assertEqual(after_remove[0]["category"], "嘲讽")
            self.assertEqual(after_remove[0]["categories"], ["嘲讽", "无语"])
            self.assertFalse(old_path.exists())
            self.assertTrue((root / "嘲讽" / filename).is_file())
            self.assertEqual(len(list(root.rglob(filename))), 1)
            self.assertEqual(
                stat.S_IMODE((root / "嘲讽").stat().st_mode),
                0o755,
            )
            self.assertEqual(
                stat.S_IMODE((root / "嘲讽" / filename).stat().st_mode),
                0o644,
            )

class TestUnifiedAIProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_unified_direct_prompt_uses_one_tool_protocol(self):
        context = FakeContext(responder=lambda kwargs: "普通回复")
        plugin = make_plugin(
            context,
            ai_unified_tool_protocol_enabled=True,
            safe_tools_enabled=True,
            safe_tools_marker_enabled=True,
            safe_tools_allow_send_sticker=True,
            sticker_enabled=True,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_random_fallback_enabled=False,
        )
        event = FakeEvent([Plain("你好")], message_str="你好", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 1)
        system_prompt = context.calls[0]["system_prompt"]
        self.assertIn("可选安全工具", system_prompt)
        self.assertIn("send_sticker", system_prompt)
        self.assertNotIn("可选表情标签", system_prompt)
        self.assertNotIn("[[sticker:标签]]", system_prompt)

    async def test_two_marker_protocols_execute_only_one_attachment(self):
        context = FakeContext(
            responder=lambda kwargs: (
                '好呀 [[tool:send_sticker emotion="happy"]] '
                "[[sticker:sad]]"
            )
        )
        plugin = make_plugin(
            context,
            ai_unified_tool_protocol_enabled=True,
            safe_tools_enabled=True,
            safe_tools_allow_send_sticker=True,
            sticker_enabled=True,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_ai_select_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=False,
            sticker_random_fallback_enabled=False,
        )
        event = FakeEvent([Plain("你好")], message_str="你好", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 2)
        self.assertEqual(len([c for c in results[0].chain if isinstance(c, Image)]), 1)
        self.assertEqual(
            Path(results[0].chain[1].path).name,
            "happy.png",
        )
        self.assertNotIn("[[tool:", results[0].text)
        self.assertNotIn("[[sticker:", results[0].text)

    async def test_no_marker_does_not_add_random_sticker(self):
        context = FakeContext(responder=lambda kwargs: "普通回复")
        plugin = make_plugin(
            context,
            ai_unified_tool_protocol_enabled=True,
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_random_fallback_enabled=True,
        )
        event = FakeEvent([Plain("今天真开心")], message_str="今天真开心", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 1)
        self.assertFalse(any(isinstance(c, Image) for c in results[0].chain))

    async def test_memory_mode_semantic_only_never_appends_random_or_double_text(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            ai_unified_tool_protocol_enabled=True,
            sticker_enabled=True,
            sticker_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_ai_select_enabled=True,
            sticker_ai_select_probability=1.0,
        )
        event = FakeEvent([Plain("今天天气真好")], message_str="今天天气真好")
        event.set_extra("qqbot_enhancer_memory_reply", True)
        event.set_extra("qqbot_enhancer_sticker_text", "今天天气真好")
        event.result = FakeResult([Plain("天气不错")])
        await plugin.attach_memory_mode_sticker(event)
        self.assertEqual(len(event.result.chain), 1)
        self.assertIsInstance(event.result.chain[0], Plain)
        self.assertFalse(any(isinstance(c, Image) for c in event.result.chain))

    async def test_legacy_sticker_marker_still_compatible_in_unified_mode(self):
        context = FakeContext(
            responder=lambda kwargs: "好呀 [[sticker:happy]]"
        )
        plugin = make_plugin(
            context,
            ai_unified_tool_protocol_enabled=True,
            safe_tools_enabled=True,
            safe_tools_allow_send_sticker=True,
            sticker_enabled=True,
            sticker_ai_select_enabled=True,
            sticker_ai_marker_enabled=True,
            sticker_ai_select_probability=1.0,
            sticker_cooldown_seconds=0,
            sticker_prevent_repeat=False,
            sticker_random_fallback_enabled=False,
        )
        event = FakeEvent([Plain("你好")], message_str="你好", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0].chain), 2)
        self.assertEqual(Path(results[0].chain[1].path).name, "happy.png")
        self.assertNotIn("[[sticker:", results[0].text)

    async def test_invalid_tool_marker_keeps_text_and_no_sticker(self):
        context = FakeContext(
            responder=lambda kwargs: (
                '普通回复 [[tool:send_sticker category="../../etc/passwd"]]'
            )
        )
        plugin = make_plugin(
            context,
            ai_unified_tool_protocol_enabled=True,
            safe_tools_enabled=True,
            safe_tools_allow_send_sticker=True,
            sticker_enabled=True,
            sticker_random_fallback_enabled=False,
        )
        event = FakeEvent([Plain("你好")], message_str="你好", private=True)
        results = await run_handler(plugin, event)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "普通回复")
        self.assertEqual(len(results[0].chain), 1)
        self.assertNotIn("passwd", results[0].text)
        self.assertFalse(any(isinstance(c, Image) for c in results[0].chain))

class TestFoodCommand(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _restaurant(index: int) -> dict:
        return {
            "id": index,
            "name": f"测试火锅店{index}",
            "location_name": "南关区" if index == 1 else "长春市",
            "tags": ["火锅", "实惠"] if index == 1 else ["火锅"],
            "taste_avg": 4.0,
            "total_avg": 4.5,
            "value_avg": 4.2,
            "review_count": index,
            "latest_review_date": "2026-05-01",
        }

    async def test_food_keyword_lookup_formats_results_without_llm(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            food_cooldown_seconds=0,
            food_max_results=5,
        )
        calls = []

        async def fake_fetch(url):
            calls.append(url)
            self.assertIn("/api/restaurants?", url)
            self.assertIn("q=%E7%81%AB%E9%94%85", url)
            return [self._restaurant(1)]

        plugin._fetch_food_json = fake_fetch
        event = FakeEvent(
            [Plain("/food 火锅")],
            message_str="/food 火锅",
            private=True,
        )
        results = await run_food_command(plugin, event)
        self.assertEqual(len(results), 1)
        text = results[0].text
        self.assertIn("测试火锅店1", text)
        self.assertIn("地点：南关区", text)
        self.assertIn("标签：火锅、实惠", text)
        self.assertIn("均分 4.5", text)
        self.assertIn("口味 4", text)
        self.assertIn("性价比 4.2", text)
        self.assertIn("评价：1 条", text)
        self.assertIn("更多：https://food.dlsz.top", text)
        self.assertNotIn("{", text)
        self.assertEqual(context.calls, [])
        self.assertEqual(len(calls), 1)
        self.assertTrue(event.call_llm)
        self.assertTrue(event.stopped)

    async def test_food_location_both_argument_orders_use_location_id(self):
        context = FakeContext()
        plugin = make_plugin(context, food_cooldown_seconds=0)
        seen_urls = []

        async def fake_fetch(url):
            seen_urls.append(url)
            if "/api/locations/search?" in url:
                if "q=%E7%81%AB%E9%94%85" in url:
                    return []
                self.assertIn("q=%E5%8D%97%E5%85%B3%E5%8C%BA", url)
                return [{"id": 7632, "name": "南关区", "path": "中国/吉林省/长春市/南关区"}]
            if "/api/restaurants?" in url:
                self.assertIn("location_id=7632", url)
                self.assertIn("q=%E7%81%AB%E9%94%85", url)
                return [self._restaurant(1)]
            self.fail(url)

        plugin._fetch_food_json = fake_fetch
        for args in ("南关区 火锅", "火锅 南关区"):
            event = FakeEvent(
                [Plain(f"/food {args}")],
                message_str=f"/food {args}",
                private=True,
                session_id=f"food-location-{args}",
            )
            results = await run_food_command(plugin, event)
            self.assertIn("测试火锅店1", results[0].text)
        self.assertEqual(len(seen_urls), 5)
        self.assertEqual(context.calls, [])

    async def test_food_location_not_found_no_result_and_error_paths(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            food_cooldown_seconds=0,
            food_location_not_found_reply="地点没找到：{location}",
            food_empty_reply="没有餐厅。",
            food_timeout_reply="超时了。",
            food_error_reply="接口坏了。",
        )
        calls = []

        async def no_locations(url):
            calls.append(url)
            if "/api/locations/search?" in url:
                return []
            self.fail("stations should not be queried for missing location")

        plugin._fetch_food_json = no_locations
        event = FakeEvent(
            [Plain("/food 南关区 火锅")],
            message_str="/food 南关区 火锅",
            private=True,
        )
        results = await run_food_command(plugin, event)
        self.assertIn("地点没找到：南关区", results[0].text)
        self.assertEqual(len(calls), 2)

        async def empty_restaurants(url):
            if "/api/restaurants?" in url:
                return []
            self.fail(url)

        plugin._fetch_food_json = empty_restaurants
        empty_event = FakeEvent(
            [Plain("/food 火锅")],
            message_str="/food 火锅",
            private=True,
            session_id="food-empty",
        )
        empty_results = await run_food_command(plugin, empty_event)
        self.assertEqual(empty_results[0].text, "没有餐厅。")

        async def timeout_restaurant(url):
            raise asyncio.TimeoutError()

        plugin._fetch_food_json = timeout_restaurant
        timeout_event = FakeEvent(
            [Plain("/food 火锅")],
            message_str="/food 火锅",
            private=True,
            session_id="food-timeout",
        )
        timeout_results = await run_food_command(plugin, timeout_event)
        self.assertEqual(timeout_results[0].text, "超时了。")

        async def bad_api(url):
            raise MODULE.food_engine.FoodAPIError("non-json")

        plugin._fetch_food_json = bad_api
        bad_event = FakeEvent(
            [Plain("/food 火锅")],
            message_str="/food 火锅",
            private=True,
            session_id="food-error",
        )
        bad_results = await run_food_command(plugin, bad_event)
        self.assertEqual(bad_results[0].text, "接口坏了。")
        self.assertEqual(context.calls, [])

    async def test_food_max_results_and_cooldown(self):
        context = FakeContext()
        clock = FakeClock(100.0)
        plugin = make_plugin(
            context,
            clock=clock,
            food_cooldown_seconds=60,
            food_max_results=3,
        )
        call_count = 0

        async def many_restaurants(url):
            nonlocal call_count
            call_count += 1
            return [self._restaurant(i) for i in range(1, 8)]

        plugin._fetch_food_json = many_restaurants
        event = FakeEvent(
            [Plain("/food 火锅")],
            message_str="/food 火锅",
            private=True,
            session_id="food-cooldown",
        )
        results = await run_food_command(plugin, event)
        text = results[0].text
        self.assertIn("找到 3 家", text)
        self.assertIn("测试火锅店3", text)
        self.assertNotIn("测试火锅店4", text)

        clock.value = 110.0
        second = FakeEvent(
            [Plain("/food 火锅")],
            message_str="/food 火锅",
            private=True,
            session_id="food-cooldown",
        )
        second_results = await run_food_command(plugin, second)
        self.assertIn("太频繁", second_results[0].text)
        self.assertEqual(call_count, 1)
        self.assertEqual(context.calls, [])

    def test_food_module_fixed_host_url_and_non_json_parser(self):
        food_module = importlib.import_module("food")
        valid = food_module.build_food_url(
            food_module.FOOD_RESTAURANTS_PATH,
            {"q": "火锅", "location_id": 7632},
        )
        self.assertTrue(valid.startswith("https://food.dlsz.top/api/restaurants?"))
        self.assertTrue(food_module.validate_food_final_url(valid))
        self.assertFalse(
            food_module.validate_food_final_url(
                "https://evil.example/api/restaurants?q=x"
            )
        )
        self.assertFalse(
            food_module.validate_food_final_url(
                "http://food.dlsz.top/api/restaurants?q=x"
            )
        )
        self.assertIsNone(food_module.sanitize_food_query("../../etc/passwd"))
        self.assertIsNone(food_module.sanitize_food_query("https://evil"))
        with self.assertRaises(food_module.FoodAPIError):
            food_module.decode_food_payload(b"<html>not json</html>")

    async def test_food_rejects_user_url_and_registers_aliases(self):
        context = FakeContext()
        plugin = make_plugin(context, food_cooldown_seconds=0)
        fetch_calls = []

        async def should_not_fetch(url):
            fetch_calls.append(url)
            self.fail("user URL must not reach network")

        plugin._fetch_food_json = should_not_fetch
        event = FakeEvent(
            [Plain("/food https://evil.example/path")],
            message_str="/food https://evil.example/path",
            private=True,
        )
        results = await run_food_command(plugin, event)
        self.assertIn("不合法", results[0].text)
        self.assertEqual(fetch_calls, [])
        self.assertEqual(context.calls, [])

        registered = {
            args[0] for args, _kwargs in FakeFilter.command_registrations if args
        }
        self.assertIn("food", registered)
        matching = [
            kwargs
            for args, kwargs in FakeFilter.command_registrations
            if args and args[0] == "food"
        ]
        self.assertTrue(matching)
        aliases = matching[0].get("alias", set())
        self.assertIn("吃什么", aliases)
        self.assertIn("饭店", aliases)

class TestGroupPersonas(unittest.IsolatedAsyncioTestCase):
    def _persona_json(self):
        return json_module.dumps(
            {
                "default": {
                    "name": "默认",
                    "prompt": "你是默认群人格，请正常回复。",
                },
                "groups": {
                    "111111": {
                        "name": "群A",
                        "prompt": "你是A群专属人格，只谈A群话题。",
                    },
                    "222222": {
                        "name": "群B",
                        "prompt": "你是B群专属人格，只谈B群话题。",
                    },
                },
            },
            ensure_ascii=False,
        )

    async def test_two_groups_isolated_private_and_unknown_fallback(self):
        context = FakeContext(responder=lambda kwargs: "回复")
        plugin = make_plugin(
            context,
            group_persona_enabled=True,
            group_personas=self._persona_json(),
            system_prompt="旧默认人格",
        )
        group_a = FakeEvent(
            [At("10001"), Plain("你好呀")],
            message_str="你好呀",
            private=False,
            self_id="10001",
            group_id="111111",
            session_id="persona-a",
        )
        await run_handler(plugin, group_a)
        group_b = FakeEvent(
            [At("10001"), Plain("你好呀")],
            message_str="你好呀",
            private=False,
            self_id="10001",
            group_id="222222",
            session_id="persona-b",
        )
        await run_handler(plugin, group_b)
        private_event = FakeEvent(
            [Plain("你好呀")],
            message_str="你好呀",
            private=True,
            session_id="persona-private",
        )
        await run_handler(plugin, private_event)
        unknown_group = FakeEvent(
            [At("10001"), Plain("你好呀")],
            message_str="你好呀",
            private=False,
            self_id="10001",
            group_id="999999",
            session_id="persona-unknown",
        )
        await run_handler(plugin, unknown_group)

        prompts = [call["system_prompt"] for call in context.calls]
        self.assertEqual(len(prompts), 4)
        self.assertIn("A群专属人格", prompts[0])
        self.assertNotIn("B群专属人格", prompts[0])
        self.assertIn("B群专属人格", prompts[1])
        self.assertNotIn("A群专属人格", prompts[1])
        self.assertIn("默认群人格", prompts[2])
        self.assertNotIn("A群专属人格", prompts[2])
        self.assertIn("默认群人格", prompts[3])
        self.assertNotIn("B群专属人格", prompts[3])

    async def test_invalid_or_overlong_mapping_falls_back_to_legacy(self):
        context = FakeContext(responder=lambda kwargs: "回复")
        plugin = make_plugin(
            context,
            group_persona_enabled=True,
            group_personas=json_module.dumps(
                {
                    "groups": {
                        "not-a-group": "不应生效",
                        "111111": "x" * 20,
                    }
                }
            ),
            group_persona_max_prompt_chars=10,
            system_prompt="旧默认人格保持不变",
        )
        event = FakeEvent(
            [At("10001"), Plain("你好呀")],
            message_str="你好呀",
            private=False,
            self_id="10001",
            group_id="111111",
            session_id="persona-invalid",
        )
        await run_handler(plugin, event)
        prompt = context.calls[0]["system_prompt"]
        self.assertIn("旧默认人格保持不变", prompt)
        self.assertNotIn("不应生效", prompt)
        self.assertNotIn("xxxxxxxx", prompt)

    def test_memory_mode_is_safe_degradation_without_prompt_leak(self):
        context = FakeContext()
        plugin = make_plugin(
            context,
            memory_mode="astrbot",
            group_persona_enabled=True,
            group_personas=self._persona_json(),
            system_prompt="旧默认人格",
        )
        option_event = FakeEvent(
            [Plain("/option")],
            message_str="/option",
            private=True,
        )
        # Use the async command through a tiny inline runner to avoid adding a
        # dependency on a particular command helper ordering.
        async def collect():
            return [
                result
                async for result in plugin.option_command(option_event)
            ]
        results = asyncio.run(collect())
        text = results[0].text
        self.assertIn("memory_mode=astrbot", text)
        self.assertIn("安全降级", text)
        self.assertNotIn("A群专属人格", text)
        self.assertNotIn("B群专属人格", text)
        self.assertEqual(context.calls, [])

class TestFinalization(unittest.IsolatedAsyncioTestCase):
    async def test_reply_is_truncated(self):
        context = FakeContext(responder=lambda kwargs: "x" * 80)
        plugin = make_plugin(context, max_reply_chars=20)
        event = FakeEvent([Plain("你好呀")], message_str="你好呀", private=True)
        results = await run_handler(plugin, event)
        self.assertLessEqual(len(results[0].text), 21)
        self.assertTrue(results[0].text.endswith("…"))


if __name__ == "__main__":
    unittest.main()
