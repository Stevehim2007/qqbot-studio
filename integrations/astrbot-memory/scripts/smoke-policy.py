#!/usr/bin/env python3
"""Policy smoke check for a patched AstrBot tree.

    python3 scripts/smoke-policy.py /path/to/patched/AstrBot

Asserts the fail-safe default (no policy section -> conversation) and that
fixtures/memory_policy.example.json resolves as documented.  Used by
scripts/verify-patch.sh; standalone on purpose (no test framework).
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def load_package(src: Path):
    stub = types.ModuleType("astrbot")
    stub.__path__ = [str(src / "astrbot")]
    sys.modules["astrbot"] = stub
    core = types.ModuleType("astrbot.core")
    core.__path__ = [str(src / "astrbot" / "core")]
    sys.modules["astrbot.core"] = core
    from astrbot.core.memory import MemoryService, build_context
    from astrbot.core.memory.policy import parse_policy

    return MemoryService, build_context, parse_policy


class Event:
    """Duck-typed group event (same shape the hooks see)."""

    platform = "aiocqhttp"

    def __init__(self, group_id: str = "838796241", user_id: str = "1001") -> None:
        self.group_id = group_id
        self.user_id = user_id
        self.unified_msg_origin = f"aiocqhttp:GroupMessage:{group_id or user_id}"

    def get_platform_name(self):
        return self.platform

    def get_sender_id(self):
        return self.user_id

    def get_group_id(self):
        return self.group_id

    def is_private_chat(self):
        return not self.group_id

    def get_extra(self, key, default=None):
        return default

    def set_extra(self, key, value):
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: smoke-policy.py /path/to/patched/AstrBot [fixtures/memory_policy.example.json]")
        return 2
    src = Path(argv[1]).resolve()
    fixture = Path(argv[2]) if len(argv) > 2 else HERE / "fixtures" / "memory_policy.example.json"
    MemoryService, build_context, parse_policy = load_package(src)

    with tempfile.TemporaryDirectory() as tmp:
        # enable=true without a policy section: nothing is read, written or queued.
        service = MemoryService({"memory": {"enable": True, "db_path": str(Path(tmp) / "m.db")}})
        event = Event()
        assert service.policy_for(build_context(event)).mode == "conversation", "默认必须是 conversation"
        assert service.retrieve(build_context(event), "麻辣火锅") == ("", []), "conversation 不得检索"
        assert not service.enqueue_turn(build_context(event), "我喜欢麻辣火锅"), "conversation 不得入队"
        service.close()

    section = json.loads(fixture.read_text(encoding="utf-8"))
    table = parse_policy(section)
    entry = lambda **kw: table.entry_for(platform="aiocqhttp", is_private=kw.pop("is_private", False), **kw)
    assert entry(group_id="838796241").mode == "long_term", "列出的群应开启长期记忆"
    assert entry(group_id="12345").scope == "group_user", "平台前缀键应匹配"
    assert entry(group_id="999999").max_items == 3, "按群预算应生效"
    assert entry(group_id="2002").mode == "conversation", "非法条目必须 fail-safe"
    assert entry(group_id="424242").mode == "conversation", "未列出的群必须 conversation"
    assert entry(is_private=True).mode == "long_term", "private 条目应生效"
    assert table.errors, "非法条目必须被记录"
    print("policy smoke OK: 默认 conversation；列出群 long_term；非法条目 fail-safe")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
