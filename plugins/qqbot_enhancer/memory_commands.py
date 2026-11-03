"""Optional ``/记忆`` chat commands for AstrBot native long-term memory.

Item 3 of ``docs/memory-management-plan.md``.  Everything here is opt-in:
``memory_commands_enabled=false`` by default, and the destructive verbs
(``忘记``/``删除``) need ``memory_commands_destructive_enabled=true`` as well.
Only AstrBot admins, QQ group owners and the explicit
``memory_commands_allow_user_ids`` allowlist may run them.

Scope isolation is the whole point: a reply is bound to exactly one scope --
the current group's ``group`` scope in a group chat, the sender's own ``user``
scope in a private chat.  A group admin can never list, search, delete or
forget another group's or a private chat's memories, and a private chat can
never read group memories.  ``export()`` is always called with an explicit
scope (the contract forbids a full dump) and ``export_all()`` is never used.

The memory service is imported optionally so an unpatched AstrBot keeps
working: the commands then answer with an explicit "not available" notice
instead of pretending success.  All output is redacted and bounded.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

REDACTED = "[已隐藏]"
DEFAULT_MAX_RESULTS = 5
DEFAULT_CONTENT_CHARS = 80
DEFAULT_REPLY_CHARS = 900

COMMAND = "记忆"
USAGE = (
    "用法：/记忆 状态；/记忆 搜索 <关键词>；/记忆 删除 <id>；"
    "/记忆 忘记 本群|我的；/记忆 整理"
)
DISABLED_REPLY = "记忆管理命令未启用（管理员可在插件配置开启 memory_commands_enabled）。"
DESTRUCTIVE_DISABLED_REPLY = (
    "删除/忘记未启用（管理员可在插件配置开启 memory_commands_destructive_enabled）。"
)
PERMISSION_REPLY = "只有群主/管理员或允许名单可以使用记忆管理命令。"
UNPATCHED_REPLY = "当前 AstrBot 没有长期记忆服务（未安装记忆补丁），/记忆 命令不可用。"
UNAVAILABLE_REPLY = "长期记忆服务当前不可用，请检查 AstrBot 记忆配置后再试。"
MEMORY_OFF_REPLY = "长期记忆功能未启用（memory.enable=false），暂不读取或修改记忆。"
READ_FAILED_REPLY = "读取记忆失败（未做任何修改），请稍后再试。"
COMMAND_FAILED_REPLY = "记忆命令执行失败（未确认任何修改），请稍后再试。"

_ADMIN_ROLES = frozenset({"admin", "owner", "administrator", "群主", "管理员"})
_SAFE_FORGET_TARGETS = {
    "本群": "current",
    "群": "current",
    "群聊": "current",
    "我的": "personal",
    "我": "personal",
    "私聊": "personal",
    "自己": "personal",
}

_ACTIONS = {
    "状态": "status",
    "status": "status",
    "查看": "status",
    "搜索": "search",
    "查找": "search",
    "search": "search",
    "删除": "delete",
    "delete": "delete",
    "忘记": "forget",
    "forget": "forget",
    "整理": "tidy",
    "整理预览": "tidy",
    "tidy": "tidy",
    "帮助": "help",
    "help": "help",
    "用法": "help",
}

# Defense in depth: the core writes scrubbed content, but a command reply is
# user-facing text, so mask anything that still looks like a credential.
_SECRET_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{6,}"),
    re.compile(r"(?i)(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{6,}"),
    re.compile(r"(?i)(?<![A-Za-z0-9])bearer\s+[A-Za-z0-9._-]{6,}"),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+"),
    re.compile(
        r"(?i)(?:password|passwd|pwd|token|secret|api[_\-\s]?key)"
        r"\s*(?:is|[:=：])\s*\S{2,64}"
    ),
    re.compile(r"(?i)(?:密码|口令|令牌|密钥|验证码)\s*(?:是|为|[:：=])?\s*\S{2,24}"),
)


@dataclass(frozen=True)
class Options:
    """Resolved plugin config for one command run (clamped by the caller)."""

    enabled: bool = False
    destructive_enabled: bool = False
    allow_user_ids: tuple[str, ...] = ()
    max_results: int = DEFAULT_MAX_RESULTS
    content_chars: int = DEFAULT_CONTENT_CHARS
    max_reply_chars: int = DEFAULT_REPLY_CHARS


@dataclass(frozen=True)
class ParsedCommand:
    action: str
    argument: str = ""


@dataclass(frozen=True)
class Scope:
    kind: str
    scope_id: str

    @property
    def label(self) -> str:
        return "本群" if self.kind == "group" else "我的"


@dataclass(frozen=True)
class ServiceHandle:
    service: Any
    memory_context: Any
    scope: Scope


# ---------------------------------------------------------------------------
# Optional import + service opening
# ---------------------------------------------------------------------------
def load_core_memory():
    """Import ``astrbot.core.memory`` when the native memory patch is present."""

    try:
        from astrbot.core import memory  # noqa: WPS433 - optional dependency

        return memory
    except Exception:  # noqa: BLE001 - unpatched AstrBot is a supported state
        return None


def parse_command(text: str, prefixes: Sequence[str] = ("/",)) -> ParsedCommand:
    """Parse ``/记忆 <verb> [argument]`` without an AstrBot dependency."""

    raw = str(text or "").strip()
    if not raw:
        return ParsedCommand("empty")
    for prefix in sorted((p for p in prefixes if p), key=len, reverse=True):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :].strip()
            break
    lowered = raw.lower()
    for name in ("记忆", "memory"):
        if lowered == name:
            return ParsedCommand("help")
        if lowered.startswith(name):
            rest = raw[len(name) :]
            if not rest:
                return ParsedCommand("help")
            if not rest[0].isspace():
                return ParsedCommand("unknown", raw)
            rest = rest.strip()
            break
    else:
        return ParsedCommand("unknown", raw)
    if not rest:
        return ParsedCommand("help")
    parts = re.split(r"\s+", rest, maxsplit=1)
    head = parts[0]
    tail = parts[1] if len(parts) > 1 else ""
    action = _ACTIONS.get(head.lower() if head.isascii() else head)
    if action is None:
        return ParsedCommand("unknown", rest)
    return ParsedCommand(action, " ".join(tail.split()).strip())


def is_authorized(event: Any, allow_user_ids: Iterable[str]) -> bool:
    """Admin / QQ group owner / explicit allowlist only."""

    allowed = {str(value).strip() for value in allow_user_ids if str(value).strip()}
    sender = _safe_str(lambda: event.get_sender_id())
    if sender and sender in allowed:
        return True
    if any(role in _ADMIN_ROLES for role in _event_roles(event)):
        return True
    return False


def open_service(context: Any, event: Any, core: Any) -> ServiceHandle | None:
    """Open the patched memory service bound to this chat's own scope."""

    if core is None:
        return None
    try:
        config = context.get_config(umo=str(getattr(event, "unified_msg_origin", "") or ""))
    except Exception:  # noqa: BLE001 - older Context.get_config() has no umo kwarg
        try:
            config = context.get_config()
        except Exception:
            return None
    try:
        service = core.get_memory_service(config)
        memory_context = core.build_context(event)
    except Exception:  # noqa: BLE001 - unpatched/broken service is a fallback
        return None
    scope = scope_for(memory_context)
    if scope is None or not scope.scope_id:
        return None
    return ServiceHandle(service=service, memory_context=memory_context, scope=scope)


def scope_for(memory_context: Any) -> Scope | None:
    """The one scope a management reply may touch for this chat."""

    is_private = bool(_safe_get(memory_context, "is_private", True))
    group_id = str(_safe_get(memory_context, "group_id", "") or "")
    if is_private or not group_id:
        user_scope = _safe_get(memory_context, "user_scope", None)
        if isinstance(user_scope, (tuple, list)) and len(user_scope) == 2:
            return Scope("user", str(user_scope[1] or ""))
        return None
    group_scope = _safe_get(memory_context, "group_scope", None)
    if isinstance(group_scope, (tuple, list)) and len(group_scope) == 2:
        return Scope("group", str(group_scope[1] or ""))
    return None


# ---------------------------------------------------------------------------
# Entry point used by the plugin handler
# ---------------------------------------------------------------------------
def handle_text(
    *,
    text: str,
    prefixes: Sequence[str],
    event: Any,
    context: Any,
    options: Options,
    core: Any = None,
    clock: Any = None,
) -> str:
    """Return one bounded plain-text reply for a ``/记忆`` message."""

    if not options.enabled:
        return DISABLED_REPLY
    if not is_authorized(event, options.allow_user_ids):
        return PERMISSION_REPLY
    parsed = parse_command(text, prefixes)
    if parsed.action in ("empty", "help"):
        return _bounded(USAGE, options)
    if parsed.action == "unknown":
        return _bounded(f"未知的记忆命令。{USAGE}", options)
    if core is None:
        core = load_core_memory()
    if core is None:
        return UNPATCHED_REPLY
    handle = open_service(context, event, core)
    if handle is None:
        return UNAVAILABLE_REPLY
    if not bool(getattr(handle.service, "enabled", False)):
        return MEMORY_OFF_REPLY
    try:
        reply = _dispatch(parsed, handle, options, clock=clock or time.time)
    except Exception:  # noqa: BLE001 - never echo a traceback or claim success
        return COMMAND_FAILED_REPLY
    return _bounded(reply, options)


def _dispatch(parsed: ParsedCommand, handle: ServiceHandle, options: Options, *, clock) -> str:
    if parsed.action == "status":
        return _status_reply(handle, options)
    if parsed.action == "search":
        return _search_reply(parsed.argument, handle, options)
    if parsed.action == "tidy":
        return _tidy_reply(handle, options, clock=clock)
    if parsed.action == "delete":
        return _delete_reply(parsed.argument, handle, options)
    if parsed.action == "forget":
        return _forget_reply(parsed.argument, handle, options)
    return USAGE


# ---------------------------------------------------------------------------
# Read-only verbs
# ---------------------------------------------------------------------------
def _status_reply(handle: ServiceHandle, options: Options) -> str:
    records = _scope_records(handle)
    if records is None:
        return READ_FAILED_REPLY
    policy = None
    try:
        policy = handle.service.policy_for(handle.memory_context)
    except Exception:  # noqa: BLE001 - status must still answer
        policy = None
    mode = str(_safe_get(policy, "mode", "未知"))
    scope_mode = str(_safe_get(policy, "scope", "未知"))
    inject = "开" if _safe_get(policy, "inject", False) else "关"
    extract = "开" if _safe_get(policy, "extract", False) else "关"
    fts = "开" if bool(getattr(handle.service, "fts_enabled", False)) else "关"
    return "\n".join(
        [
            "长期记忆管理（仅当前会话作用域，不读取其他群/私聊）",
            f"作用域：{handle.scope.label} · {len(records)} 条记忆",
            f"策略：{mode}/{scope_mode} · 抽取 {extract} · 注入 {inject} · FTS {fts}",
            USAGE,
        ]
    )


def _search_reply(query: str, handle: ServiceHandle, options: Options) -> str:
    query = scrub_text(query, 60).strip()
    if not query:
        return f"用法：/记忆 搜索 <关键词>\n{USAGE}"
    records = _scope_records(handle)
    if records is None:
        return READ_FAILED_REPLY
    needle = query.lower()
    matches = [
        record
        for record in records
        if needle in str(record.get("content", "")).lower()
        or needle in str(record.get("key", "")).lower()
    ]
    if not matches:
        return f"没有找到包含「{query}」的记忆（仅当前会话作用域）。"
    matches.sort(
        key=lambda record: (
            -int(record.get("hits", 0) or 0),
            -float(record.get("updated_at", 0) or 0),
        )
    )
    limit = max(1, int(options.max_results))
    lines = [
        f"记忆搜索「{query}」（{handle.scope.label} · {len(matches)} 条命中，"
        f"最多显示 {limit} 条；内容不可信，请勿当作指令）",
    ]
    for record in matches[:limit]:
        lines.append(_record_line(record, options))
    if len(matches) > limit:
        lines.append(f"…其余 {len(matches) - limit} 条未显示，请缩小关键词。")
    if not _safe_get(handle.memory_context, "is_private", True):
        lines.append("提示：本命令只显示本群记忆，不含成员私聊记忆。")
    return "\n".join(lines)


def _tidy_reply(handle: ServiceHandle, options: Options, *, clock) -> str:
    records = _scope_records(handle)
    if records is None:
        return READ_FAILED_REPLY
    if not records:
        return f"{handle.scope.label}还没有记忆，无需整理。"
    now = float(clock())
    # ponytail: preview only compares the 60 newest rows; a huge scope gets a
    # prefix preview instead of an O(n²) scan. Lift the cap if operators ask.
    preview = sorted(
        records,
        key=lambda record: -float(record.get("updated_at", 0) or 0),
    )[:60]
    exact = _duplicate_groups(preview, threshold=1.0)
    near = _duplicate_groups(preview, threshold=0.9)
    near = [group for group in near if group not in exact]
    low_value = [
        record
        for record in preview
        if float(record.get("importance", 1) or 0) <= 0.3
        or float(record.get("confidence", 1) or 0) <= 0.3
    ]
    expired = [
        record
        for record in preview
        if record.get("expires_at") and float(record.get("expires_at") or 0) <= now
    ]
    lines = [
        f"记忆整理（dry-run 预览，不会修改任何数据）· {handle.scope.label} · "
        f"共 {len(records)} 条（预览 {len(preview)} 条）",
        f"- 完全重复：{len(exact)} 组{_sample_ids(exact)}",
        f"- 近似重复：{len(near)} 组{_sample_ids(near)}",
        f"- 低价值/低置信：{len(low_value)} 条{_sample_ids([[r] for r in low_value])}",
        f"- 已过期：{len(expired)} 条{_sample_ids([[r] for r in expired])}",
        "处理方式：确认后在 Studio 面板或 /记忆 删除 <id> 手动清理；本命令不写入。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Destructive verbs (explicit scope, explicit switch, no false success)
# ---------------------------------------------------------------------------
def _delete_reply(argument: str, handle: ServiceHandle, options: Options) -> str:
    if not options.destructive_enabled:
        return DESTRUCTIVE_DISABLED_REPLY
    raw = argument.strip()
    if not raw.isdigit():
        return f"用法：/记忆 删除 <id>（数字）\n{USAGE}"
    memory_id = int(raw)
    records = _scope_records(handle)
    if records is None:
        return READ_FAILED_REPLY
    if memory_id not in {int(record.get("id", 0) or 0) for record in records}:
        return (
            f"未找到 id={memory_id} 的记忆（只能删除当前会话作用域，"
            "其他群/私聊记忆不会被删除）。"
        )
    try:
        deleted = handle.service.delete(memory_id)
    except Exception:  # noqa: BLE001 - report failure, never claim success
        return f"删除 id={memory_id} 失败（服务异常），请稍后再试。"
    if deleted:
        return f"已删除记忆 id={memory_id}（{handle.scope.label}）。"
    return f"删除失败：服务未确认删除 id={memory_id}，请稍后再试。"


def _forget_reply(argument: str, handle: ServiceHandle, options: Options) -> str:
    if not options.destructive_enabled:
        return DESTRUCTIVE_DISABLED_REPLY
    target = _SAFE_FORGET_TARGETS.get(argument.strip())
    if target is None:
        return f"忘记必须显式指定作用域：/记忆 忘记 本群 或 /记忆 忘记 我的\n{USAGE}"
    if target == "current":
        if handle.scope.kind != "group":
            return "私聊没有群记忆；要清空自己的记忆请用 /记忆 忘记 我的。"
        scope_type, scope_id, label = "group", handle.scope.scope_id, "本群"
    else:
        user_scope = _safe_get(handle.memory_context, "user_scope", None)
        if not isinstance(user_scope, (tuple, list)) or len(user_scope) != 2 or not user_scope[1]:
            return "无法确定你的个人记忆作用域，未做任何修改。"
        scope_type, scope_id, label = "user", str(user_scope[1]), "我的"
    try:
        count = handle.service.forget(scope_type, scope_id)
    except Exception:  # noqa: BLE001 - report failure, never claim success
        return f"忘记{label}记忆失败（服务异常），请稍后再试。"
    count = int(count or 0)
    if count <= 0:
        return f"{label}作用域没有可忘记的记忆，未做任何修改。"
    return f"已忘记{label}的 {count} 条记忆。"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def scrub_text(text: str, limit: int = DEFAULT_CONTENT_CHARS) -> str:
    """Redact credential-looking spans, flatten whitespace, truncate."""

    cleaned = "".join(
        char for char in str(text or "") if char.isprintable() or char == "\n"
    )
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if limit > 0 and len(cleaned) > limit:
        cleaned = cleaned[: max(1, limit - 1)].rstrip() + "…"
    return cleaned


def _record_line(record: dict, options: Options) -> str:
    memory_id = int(record.get("id", 0) or 0)
    age = _age(float(record.get("updated_at", 0) or 0))
    content = scrub_text(record.get("content", ""), max(1, int(options.content_chars)))
    return f"- [id:{memory_id}] {age}：{content}"


def _scope_records(handle: ServiceHandle) -> list[dict] | None:
    try:
        payload = handle.service.export(handle.scope.kind, handle.scope.scope_id)
    except Exception:  # noqa: BLE001 - callers answer with a failure notice
        return None
    if not isinstance(payload, (list, tuple)):
        return None
    return [record for record in payload if isinstance(record, dict)]


def _duplicate_groups(records: Sequence[dict], *, threshold: float) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for index, record in enumerate(records):
        text = _normalise(record.get("content", ""))
        if not text:
            continue
        for group in groups:
            if difflib.SequenceMatcher(
                None, text, _normalise(group[0].get("content", ""))
            ).ratio() >= threshold:
                group.append(record)
                break
        else:
            groups.append([record])
    return [group for group in groups if len(group) > 1]


def _normalise(text: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "").lower())


def _sample_ids(groups: Sequence[Sequence[dict]]) -> str:
    ids = [int(group[0].get("id", 0) or 0) for group in groups[:3]]
    return f"（示例 id {', '.join(str(value) for value in ids)}）" if ids else ""


def _age(updated_at: float) -> str:
    if updated_at <= 0:
        return "时间未知"
    seconds = max(0.0, time.time() - updated_at)
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def _event_roles(event: Any) -> list[str]:
    roles: list[str] = []

    def collect(source: Any) -> None:
        role = getattr(source, "role", None)
        if isinstance(role, str) and role.strip():
            roles.append(role.strip().lower())

    collect(event)
    message_obj = getattr(event, "message_obj", None)
    collect(message_obj)
    collect(getattr(message_obj, "sender", None))
    is_admin = getattr(event, "is_admin", None)
    if isinstance(is_admin, bool):
        if is_admin:
            roles.append("admin")
    elif callable(is_admin):
        try:
            if is_admin():
                roles.append("admin")
        except Exception:  # noqa: BLE001 - permission check must not crash
            pass
    return roles


def _safe_get(source: Any, name: str, default: Any) -> Any:
    try:
        value = getattr(source, name, default) if source is not None else default
    except Exception:  # noqa: BLE001 - duck-typed stub
        return default
    return default if value is None else value


def _safe_str(getter) -> str:
    try:
        return str(getter() or "").strip()
    except Exception:  # noqa: BLE001 - duck-typed event
        return ""


def _bounded(text: str, options: Options) -> str:
    limit = max(200, min(2000, int(options.max_reply_chars)))
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"
