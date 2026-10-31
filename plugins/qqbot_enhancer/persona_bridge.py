"""Official AstrBot API bridge for per-conversation group personas.

AstrBot v4.29.3 documents:
- ``PersonaManager.create_persona/update_persona/get_persona``;
- ``ConversationManager.new_conversation/update_conversation/get_conversation``;
- ``on_waiting_llm_request`` and ``on_llm_request`` hooks.

This bridge creates only dedicated ``qqbot_enhancer_group_*`` personas, binds the
current AstrBot conversation (per unified_msg_origin) to that persona, and uses
the request hook as a one-turn fallback if binding is unavailable.  It never
touches provider/model/key settings, so the existing DeepSeek provider and API
key remain authoritative.
"""

from __future__ import annotations

from typing import Any

AGENT_PERSONA_PREFIX = "qqbot_enhancer_group_"
AGENT_DEFAULT_PERSONA_ID = AGENT_PERSONA_PREFIX + "default"
AGENT_PROMPT_PERSONA_PREFIX = "qqbot_enhancer_prompt_"
AGENT_PERSONA_MARKER = "# Group Persona Instructions"


def persona_id_for(group_id: str, source: str) -> str:
    """Return a safe dedicated persona id; never the global/default persona."""

    if source == "group" and str(group_id or "").isdigit():
        return f"{AGENT_PERSONA_PREFIX}{group_id}"
    if source == "default":
        return AGENT_DEFAULT_PERSONA_ID
    return ""


def persona_id_for_profile(profile_id: str) -> str:
    value = str(profile_id or "").strip()
    if not value or not all(char.isalnum() or char in "_-" for char in value):
        return ""
    return f"{AGENT_PROMPT_PERSONA_PREFIX}{value}"


class AgentPersonaBridge:
    """Small, defensive adapter around documented AstrBot manager APIs."""

    def __init__(
        self,
        *,
        persona_manager: Any = None,
        conversation_manager: Any = None,
        logger: Any = None,
    ) -> None:
        self.persona_manager = persona_manager
        self.conversation_manager = conversation_manager
        self.logger = logger
        self._persona_cache: dict[str, str] = {}
        self.last_persona_changed = False

    @property
    def supported(self) -> bool:
        return bool(
            self.persona_manager is not None
            and self.conversation_manager is not None
            and callable(getattr(self.persona_manager, "get_persona", None))
            and callable(getattr(self.persona_manager, "create_persona", None))
            and callable(getattr(self.persona_manager, "update_persona", None))
            and callable(getattr(self.conversation_manager, "get_curr_conversation_id", None))
            and callable(getattr(self.conversation_manager, "get_conversation", None))
            and callable(getattr(self.conversation_manager, "new_conversation", None))
            and callable(getattr(self.conversation_manager, "update_conversation", None))
        )

    async def ensure_persona(self, persona_id: str, prompt: str) -> bool:
        self.last_persona_changed = False
        if not persona_id or not prompt or not self.supported:
            return False
        cached = self._persona_cache.get(persona_id)
        if cached == prompt:
            return True
        try:
            existing = await self.persona_manager.get_persona(persona_id)
        except Exception:
            existing = None
        try:
            if existing is None:
                await self.persona_manager.create_persona(persona_id, prompt)
                self.last_persona_changed = True
            elif str(getattr(existing, "system_prompt", "")) != prompt:
                await self.persona_manager.update_persona(
                    persona_id, system_prompt=prompt
                )
                self.last_persona_changed = True
            self._persona_cache[persona_id] = prompt
            return True
        except ValueError:
            # A concurrent create can win the race; update the existing row.
            try:
                await self.persona_manager.update_persona(
                    persona_id, system_prompt=prompt
                )
                self._persona_cache[persona_id] = prompt
                self.last_persona_changed = True
                return True
            except Exception:
                return False
        except Exception:
            return False

    async def bind_conversation(
        self,
        *,
        unified_msg_origin: str,
        platform_id: str,
        persona_id: str,
        new_conversation_on_persona_change: bool = False,
        persona_prompt_changed: bool = False,
    ) -> bool:
        if not self.supported or not unified_msg_origin or not persona_id:
            return False
        try:
            cid = await self.conversation_manager.get_curr_conversation_id(
                unified_msg_origin
            )
            if not cid:
                await self.conversation_manager.new_conversation(
                    unified_msg_origin,
                    platform_id=platform_id,
                    persona_id=persona_id,
                )
                return True
            getter = getattr(self.conversation_manager, "get_conversation", None)
            if callable(getter):
                conversation = await getter(unified_msg_origin, cid)
                if (
                    conversation is not None
                    and str(getattr(conversation, "persona_id", "") or "") == persona_id
                    and not persona_prompt_changed
                ):
                    return True
            # Any effective persona id/prompt change must start a fresh
            # conversation.  Merely updating persona_id would leave old persona
            # history attached to the new persona.
            await self.conversation_manager.new_conversation(
                unified_msg_origin,
                platform_id=platform_id,
                persona_id=persona_id,
            )
            return True
        except Exception:
            return False

    @staticmethod
    def inject_system_prompt(req: Any, prompt: str, persona_name: str = "") -> bool:
        """Append a per-request fallback without changing provider/model/key."""

        if req is None or not prompt:
            return False
        existing = str(getattr(req, "system_prompt", "") or "")
        if AGENT_PERSONA_MARKER in existing:
            existing = existing.split(AGENT_PERSONA_MARKER, 1)[0].rstrip()
        label = str(persona_name or "").strip()
        heading = AGENT_PERSONA_MARKER + (f" ({label})" if label else "")
        block = (
            f"\n\n{heading}\n\n"
            "For this group conversation, follow these persona instructions. "
            "If they conflict with any earlier persona instructions, these take "
            "precedence for this conversation only:\n\n"
            f"{prompt}\n"
        )
        req.system_prompt = existing.rstrip() + block
        return True


CURRENT_SENDER_MARKER = "# Current Sender Identity"


def inject_current_sender_identity(
    req: Any,
    *,
    group_id: str = "",
    nickname: str = "",
    qq: str = "",
    max_chars: int = 500,
) -> bool:
    """Append current-turn sender metadata for AstrBot memory mode.

    Only ``req.system_prompt`` is touched. Conversation history, event text,
    provider/model/key settings are never modified. Historical messages still
    have no per-message identity prefix; this is only the current request.
    """

    if req is None:
        return False
    existing = str(getattr(req, "system_prompt", "") or "")
    if CURRENT_SENDER_MARKER in existing:
        existing = existing.split(CURRENT_SENDER_MARKER, 1)[0].rstrip()
    safe_group = "".join(
        char for char in str(group_id or "") if char.isdigit()
    )[:20]
    safe_nickname = "".join(
        char
        for char in str(nickname or "")
        if ord(char) >= 32 and ord(char) != 127
    )[:32].strip() or "未知"
    safe_qq = "".join(char for char in str(qq or "") if char.isdigit())[:20]
    lines = [
        CURRENT_SENDER_MARKER,
        "This metadata applies only to the current incoming message.",
    ]
    if safe_group:
        lines.append(f"Current group: {safe_group}")
    lines.append(f"Current sender nickname: {safe_nickname}")
    if safe_qq:
        lines.append(f"Current sender QQ: {safe_qq}")
    lines.append(
        "Use this only to distinguish the current group member. "
        "Do not output the QQ, group id, or nickname to the user."
    )
    block = "\n".join(lines)
    if len(block) > max(1, int(max_chars)):
        block = block[: max(1, int(max_chars)) - 1].rstrip() + "…"
    req.system_prompt = existing.rstrip() + "\n\n" + block + "\n"
    return True


AMBIENT_GROUP_MARKER = "# Ambient Group Context"


def inject_ambient_group_context(
    req: Any,
    history: list[dict[str, Any]],
    *,
    now: float | None = None,
    max_chars: int = 1500,
) -> bool:
    """Append bounded recent-group messages to this request only.

    This never modifies event text, persisted conversation history or provider
    settings.  If the hook is unavailable at the AstrBot layer, callers must
    degrade explicitly rather than inventing history.
    """

    if req is None or not history:
        return False
    existing = str(getattr(req, "system_prompt", "") or "")
    if AMBIENT_GROUP_MARKER in existing:
        existing = existing.split(AMBIENT_GROUP_MARKER, 1)[0].rstrip()
    lines = [
        AMBIENT_GROUP_MARKER,
        "Recent messages from this group only. Use them only as context;",
        "do not expose QQ numbers, group ids, or nicknames in the reply.",
    ]
    max_chars = max(1, int(max_chars))
    used = sum(len(line) + 1 for line in lines)
    for item in history:
        nickname = "".join(
            char
            for char in str(item.get("nickname", "") or "")
            if ord(char) >= 32 and ord(char) != 127
        )[:32].strip() or "未知"
        qq = "".join(
            char for char in str(item.get("qq", "") or "") if char.isdigit()
        )[:20]
        label = f"{nickname}({qq})" if qq else nickname
        text = "".join(
            char
            for char in str(item.get("text", "") or "")
            if ord(char) >= 32 or char in ("\n", "\t")
        )[:200].strip()
        timestamp = item.get("timestamp")
        age = ""
        if now is not None and timestamp is not None:
            try:
                seconds = max(0, int(float(now) - float(timestamp)))
                age = f"{seconds}s ago "
            except (TypeError, ValueError):
                age = ""
        line = f"- {age}[{label}] {text}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if len(lines) <= 3:
        return False
    block = "\n".join(lines)
    req.system_prompt = existing.rstrip() + "\n\n" + block + "\n"
    return True
