"""Shared helpers for the AstrBot native-memory compatibility tests.

The tests run against a *patched* AstrBot source tree (see README): the memory
package is imported through a stub ``astrbot`` / ``astrbot.core`` parent so the
test suite needs neither AstrBot's third-party dependencies nor a running
instance.
"""

from __future__ import annotations

import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

DEFAULT_SRC = "/tmp/astrbot-memory-src"


def astrbot_src() -> Path:
    return Path(os.environ.get("ASTRBOT_SRC", DEFAULT_SRC)).resolve()


def load_memory_package():
    """Import ``astrbot.core.memory`` from the patched source tree."""

    src = astrbot_src()
    package_dir = src / "astrbot" / "core" / "memory"
    if not (package_dir / "__init__.py").is_file():
        raise unittest.SkipTest(
            f"未找到打补丁的 AstrBot 源码：{package_dir}（设置 ASTRBOT_SRC 或先运行 scripts/verify-patch.sh）"
        )
    if not getattr(sys.modules.get("astrbot"), "__path__", None):
        stub = types.ModuleType("astrbot")
        stub.__path__ = [str(src / "astrbot")]
        stub.__version__ = "4.29.3"
        sys.modules["astrbot"] = stub
    if not getattr(sys.modules.get("astrbot.core"), "__path__", None):
        core_stub = types.ModuleType("astrbot.core")
        core_stub.__path__ = [str(src / "astrbot" / "core")]
        sys.modules["astrbot.core"] = core_stub
    return importlib.import_module("astrbot.core.memory")


class FakeConversation:
    def __init__(self, cid: str = "", persona_id: str = "") -> None:
        self.cid = cid
        self.persona_id = persona_id
        self.history = "[]"


class FakeEvent:
    """Minimal duck-typed stand-in for ``AstrMessageEvent``."""

    def __init__(
        self,
        *,
        platform: str = "aiocqhttp",
        user_id: str = "1001",
        group_id: str = "",
        message_str: str = "",
        persona_id: str = "",
    ) -> None:
        self.platform = platform
        self.user_id = user_id
        self.group_id = group_id
        self.message_str = message_str
        self._extras: dict = {}
        if persona_id:
            self._extras["_trace_persona_id"] = persona_id
        message_type = "GroupMessage" if group_id else "FriendMessage"
        self.unified_msg_origin = f"{platform}:{message_type}:{group_id or user_id}"

    def get_platform_name(self) -> str:
        return self.platform

    def get_sender_id(self) -> str:
        return self.user_id

    def get_group_id(self) -> str:
        return self.group_id

    def is_private_chat(self) -> bool:
        return not self.group_id

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self._extras[key] = value


class FakeRequest:
    """Minimal duck-typed stand-in for ``ProviderRequest``."""

    def __init__(self, prompt: str = "", system_prompt: str = "", conversation=None) -> None:
        self.prompt = prompt
        self.system_prompt = system_prompt
        self.conversation = conversation


def private_event(user_id: str = "1001", message_str: str = "") -> FakeEvent:
    return FakeEvent(user_id=user_id, message_str=message_str)


def group_event(group_id: str = "2001", user_id: str = "1001", message_str: str = "") -> FakeEvent:
    return FakeEvent(group_id=group_id, user_id=user_id, message_str=message_str)


def raw_memory_config(db_path: str | Path, **overrides) -> dict:
    """Raw config for ``MemoryService`` with every chat opted into long-term."""

    section = {
        "enable": True,
        "db_path": str(db_path),
        "policy": {"default_mode": "long_term"},
    }
    section.update(overrides)
    return {"memory": section}


def memory_config(db_path: str | Path, **overrides) -> dict:
    config = {
        "enable": True,
        "db_path": str(db_path),
        "max_chars": 400,
        "max_items": 4,
        "min_confidence": 0.3,
        "ttl_days": 0,
        # 既有用例测试存储/检索语义，默认全量开启；策略行为在 test_policy.py 单独覆盖。
        "policy": {"default_mode": "long_term"},
    }
    config.update(overrides)
    return {"memory": config}


class MemorySupport:
    """Mixin: patched-package loader, temp DB and per-test service reset."""

    @classmethod
    def setUpClass(cls):
        cls.memory = load_memory_package()
        logging.getLogger("astrbot.memory").setLevel(logging.CRITICAL)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "memory.db"
        self.memory.reset_memory_services()
        self.addCleanup(self.memory.reset_memory_services)

    def make_service(self, **overrides):
        config = memory_config(self.db_path, **overrides)
        service = self.memory.MemoryService(config)
        self.addCleanup(service.close)
        return service

    def remember(self, service, context, text: str, assistant_text: str = ""):
        extract = importlib.import_module("astrbot.core.memory.extract")
        return service.store_candidates(extract.confirmed_extract(text, assistant_text, context))

    def context(self, event) -> object:
        return self.memory.build_context(event, None)


class MemoryTestCase(MemorySupport, unittest.TestCase):
    """Synchronous memory tests."""


class AsyncMemoryTestCase(MemorySupport, unittest.IsolatedAsyncioTestCase):
    """Asynchronous (queue/worker) memory tests."""


def sqlite_has_fts5() -> bool:
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        connection.close()
        return True
    except sqlite3.Error:
        return False
