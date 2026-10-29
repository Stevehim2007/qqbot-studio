"""Mock tests for plugins/qqbot_enhancer.

These tests intentionally do not require a real AstrBot installation. The very
small subset of AstrBot v4.29 APIs used by the plugin is stubbed in sys.modules.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

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


class Image:
    def __init__(self, file=None, url=None, path=None, **kwargs):
        self.file = file
        self.url = url
        self.path = path

    @staticmethod
    def fromFileSystem(path, **kwargs):
        return Image(file=f"file://{path}", path=str(path), **kwargs)

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
    for cls in (Plain, At, AtAll, Face, Image):
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
    ):
        self.provider_id = provider_id
        self.providers = {}
        if provider_id:
            self.providers[provider_id] = FakeProvider(provider_id, modalities)
        for pid, pmodalities in (extra_providers or {}).items():
            self.providers[pid] = FakeProvider(pid, pmodalities)
        self.responder = responder
        self.calls = []
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
