"""Per-group persona parsing and resolution for direct mode only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

GROUP_ID_RE = re.compile(r"^\d{1,20}$")


@dataclass(slots=True)
class Persona:
    name: str
    prompt: str


@dataclass(slots=True)
class PersonaConfig:
    default: Persona | None
    groups: dict[str, Persona]
    errors: int = 0

    @property
    def has_group_overrides(self) -> bool:
        return bool(self.groups)


def _safe_text(value: Any, *, max_chars: int, allow_newlines: bool) -> str | None:
    text = str(value or "").strip()
    if not text or len(text) > max_chars:
        return None
    for char in text:
        code = ord(char)
        if code == 127 or (code < 32 and char not in ("\n", "\t", "\r")):
            return None
    return text


def _parse_persona(
    raw: Any,
    *,
    max_prompt_chars: int,
    max_name_chars: int,
) -> Persona | None:
    if isinstance(raw, str):
        prompt = _safe_text(
            raw,
            max_chars=max_prompt_chars,
            allow_newlines=True,
        )
        if prompt is None:
            return None
        return Persona(name="", prompt=prompt)
    if not isinstance(raw, dict):
        return None
    prompt = _safe_text(
        raw.get("prompt"),
        max_chars=max_prompt_chars,
        allow_newlines=True,
    )
    if prompt is None:
        return None
    name = _safe_text(
        raw.get("name", ""),
        max_chars=max_name_chars,
        allow_newlines=False,
    )
    if name is None:
        name = ""
    return Persona(name=name, prompt=prompt)


def parse_group_personas(
    raw: Any,
    *,
    max_groups: int = 50,
    max_prompt_chars: int = 4000,
    max_name_chars: int = 64,
) -> PersonaConfig:
    """Parse an instance JSON value without ever throwing on user input."""

    data: Any = raw
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return PersonaConfig(default=None, groups={})
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return PersonaConfig(default=None, groups={}, errors=1)
    if not isinstance(data, dict):
        return PersonaConfig(default=None, groups={}, errors=1)

    errors = 0
    default = _parse_persona(
        data.get("default"),
        max_prompt_chars=max_prompt_chars,
        max_name_chars=max_name_chars,
    )
    if data.get("default") is not None and default is None:
        errors += 1

    groups_raw: dict[str, Any] = {}
    raw_groups = data.get("groups")
    if isinstance(raw_groups, dict):
        groups_raw.update(raw_groups)
    # Also accept a compact top-level {"123456": "prompt"} mapping.
    for key, value in data.items():
        if key != "groups" and key != "default":
            groups_raw[str(key)] = value

    max_groups = max(0, int(max_groups))
    groups: dict[str, Persona] = {}
    for raw_group_id, raw_persona in groups_raw.items():
        group_id = str(raw_group_id or "").strip()
        if not GROUP_ID_RE.fullmatch(group_id):
            errors += 1
            continue
        persona = _parse_persona(
            raw_persona,
            max_prompt_chars=max_prompt_chars,
            max_name_chars=max_name_chars,
        )
        if persona is None:
            errors += 1
            continue
        if len(groups) >= max_groups:
            errors += 1
            continue
        groups[group_id] = persona
    return PersonaConfig(default=default, groups=groups, errors=errors)


def resolve_persona(
    parsed: PersonaConfig,
    *,
    group_id: str = "",
    is_private: bool = False,
    legacy_prompt: str = "",
) -> tuple[str, str, str]:
    """Return (prompt, name, source); never raises for malformed group ids."""

    legacy = str(legacy_prompt or "")
    if is_private:
        if parsed.default is not None:
            return parsed.default.prompt, parsed.default.name, "default"
        return legacy, "", "legacy"
    safe_group_id = str(group_id or "").strip()
    if GROUP_ID_RE.fullmatch(safe_group_id):
        persona = parsed.groups.get(safe_group_id)
        if persona is not None:
            return persona.prompt, persona.name, "group"
    if parsed.default is not None:
        return parsed.default.prompt, parsed.default.name, "default"
    return legacy, "", "legacy"


def persona_summary(parsed: PersonaConfig) -> str:
    """A prompt-free summary safe for /option and logs."""

    default_state = "有" if parsed.default is not None else "无"
    return (
        "群人格映射：direct 模式按群 ID 注入；"
        f"群映射 {len(parsed.groups)} 个，默认人格 {default_state}"
    )


PROMPT_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(slots=True)
class PromptProfile:
    id: str
    name: str
    prompt: str


@dataclass(slots=True)
class PromptProfileConfig:
    profiles: list[PromptProfile]
    active_id: str = ""

    @property
    def active(self) -> PromptProfile | None:
        return next((item for item in self.profiles if item.id == self.active_id), None)


def parse_prompt_profiles(
    raw: Any,
    active_id: Any = "",
    *,
    max_profiles: int = 100,
    max_prompt_chars: int = 12000,
    max_name_chars: int = 80,
) -> PromptProfileConfig:
    """Parse Studio-synced prompt profiles without ever raising."""

    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw.strip() or "[]")
        except (TypeError, ValueError):
            return PromptProfileConfig(profiles=[])
    if not isinstance(data, list):
        return PromptProfileConfig(profiles=[])
    max_profiles = max(1, int(max_profiles))
    profiles: list[PromptProfile] = []
    seen: set[str] = set()
    for raw_item in data:
        if not isinstance(raw_item, dict):
            continue
        profile_id = str(raw_item.get("id") or "").strip()
        if not PROMPT_PROFILE_ID_RE.fullmatch(profile_id) or profile_id in seen:
            continue
        prompt = _safe_text(
            raw_item.get("prompt"),
            max_chars=max_prompt_chars,
            allow_newlines=True,
        )
        if prompt is None:
            continue
        name = _safe_text(
            raw_item.get("name", ""),
            max_chars=max_name_chars,
            allow_newlines=False,
        ) or f"提示词 {len(profiles) + 1}"
        profiles.append(PromptProfile(id=profile_id, name=name, prompt=prompt))
        seen.add(profile_id)
        if len(profiles) >= max_profiles:
            break
    safe_active = str(active_id or "").strip()
    if safe_active not in seen:
        safe_active = profiles[0].id if profiles else ""
    return PromptProfileConfig(profiles=profiles, active_id=safe_active)


def resolve_prompt_profile(
    config: PromptProfileConfig,
    *,
    legacy_prompt: str = "",
) -> tuple[str, str, str, str]:
    """Return (prompt, name, profile_id, source). Empty profile -> legacy."""

    active = config.active
    if active is not None:
        return active.prompt, active.name, active.id, "profile"
    legacy = str(legacy_prompt or "")
    return legacy, "", "", "legacy"
