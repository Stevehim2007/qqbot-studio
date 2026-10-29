"""Mock tests for plugins/qqbot_enhancer.

These tests intentionally do not require a real AstrBot installation. The very
small subset of AstrBot v4.29 APIs used by the plugin is stubbed in sys.modules.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json as json_module
import sys
import tempfile
import types
import unittest
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


class Record:
    def __init__(self, file=None, url=None, path=None, **kwargs):
        self.file = file
        self.url = url
        self.path = path

    @staticmethod
    def fromFileSystem(path, **kwargs):
        return Record(file=f"file://{path}", path=str(path), **kwargs)


class Image:
    def __init__(self, file=None, url=None, path=None, **kwargs):
        self.file = file
        self.url = url
        self.path = path

    @staticmethod
    def fromFileSystem(path, **kwargs):
        return Image(file=f"file://{path}", path=str(path), **kwargs)

    @staticmethod
    def fromURL(url, **kwargs):
        if not str(url).startswith(("http://", "https://")):
            raise ValueError("not an http(s) URL")
        return Image(file=str(url), url=str(url), **kwargs)

    async def convert_to_base64(self):
        # Deterministic tiny fake payload; tests only verify the wrapper.
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
    components = types.ModuleType("astrbot.api.message_components")
    components.__path__ = []
    for cls in (Plain, At, AtAll, Face, Image, Record):
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


async def run_option_command(plugin, event):
    results = []
    async for result in plugin.option_command(event):
        results.append(result)
    return results


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
<a class="iusc" m="{&quot;murl&quot;:&quot;https://cdn.example.com/cat-one.jpg&quot;,&quot;turl&quot;:&quot;https://bing.example/thumb.jpg&quot;}"></a>
<a class="iusc" m="{&quot;murl&quot;:&quot;https://cdn.example.com/cat-two.png?w=640&amp;h=480&quot;}"></a>
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
                "replaceUrl": [
                    {"ObjURL": "https://example.com/original-one.jpg"}
                ],
            },
            {
                "middleURL": "http://127.0.0.1/private.jpg",
                "thumbURL": "https://img0.baidu.com/thumb-two.png",
            },
            {"middleURL": "file:///etc/passwd"},
        ]
    },
    ensure_ascii=False,
)


PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


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
