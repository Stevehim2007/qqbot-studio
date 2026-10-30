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
AGENT_PERSONA_MARKER = "# Group Persona Instructions"


def persona_id_for(group_id: str, source: str) -> str:
    """Return a safe dedicated persona id; never the global/default persona."""

    if source == "group" and str(group_id or "").isdigit():
        return f"{AGENT_PERSONA_PREFIX}{group_id}"
    if source == "default":
        return AGENT_DEFAULT_PERSONA_ID
    return ""


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

    @property
    def supported(self) -> bool:
        return bool(
            self.persona_manager is not None
            and self.conversation_manager is not None
            and callable(getattr(self.persona_manager, "get_persona", None))
            and callable(getattr(self.persona_manager, "create_persona", None))
            and callable(getattr(self.persona_manager, "update_persona", None))
            and callable(getattr(self.conversation_manager, "get_curr_conversation_id", None))
            and callable(getattr(self.conversation_manager, "new_conversation", None))
            and callable(getattr(self.conversation_manager, "update_conversation", None))
        )

    async def ensure_persona(self, persona_id: str, prompt: str) -> bool:
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
            elif str(getattr(existing, "system_prompt", "")) != prompt:
                await self.persona_manager.update_persona(
                    persona_id, system_prompt=prompt
                )
            self._persona_cache[persona_id] = prompt
            return True
        except ValueError:
            # A concurrent create can win the race; update the existing row.
            try:
                await self.persona_manager.update_persona(
                    persona_id, system_prompt=prompt
                )
                self._persona_cache[persona_id] = prompt
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
                if conversation is not None and str(
                    getattr(conversation, "persona_id", "") or ""
                ) == persona_id:
                    return True
            await self.conversation_manager.update_conversation(
                unified_msg_origin,
                conversation_id=cid,
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
