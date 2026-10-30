"""Stable interfaces for QQ Bot Studio's modular architecture.

These :class:`typing.Protocol` definitions describe the capabilities that the
composition root may depend on.  Existing concrete classes intentionally keep
their public methods and are *not* required to inherit from the protocols, so
the contracts add a seam without changing deployment, state or API behaviour.

This first stage is backend-only.  UI component contracts are declared here for
the later frontend split but no UI behaviour is moved yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@runtime_checkable
class ConfigStore(Protocol):
    """Persistent configuration/secrets capability used by deployment code."""

    def load_config(self) -> dict[str, Any]: ...

    def save_config(self, config: dict[str, Any]) -> Any: ...

    def load_secrets(self) -> dict[str, Any]: ...

    def save_secrets(self, secrets_data: dict[str, Any]) -> None: ...

    def ensure_secrets(self) -> dict[str, Any]: ...


@runtime_checkable
class StateProvider(Protocol):
    """Read/write access to persisted deployment state."""

    def load_state(self) -> dict[str, Any]: ...

    def save_state(self, state: dict[str, Any]) -> None: ...


@runtime_checkable
class RuntimeBackend(Protocol):
    """Replaceable container/process runtime capability.

    DockerController satisfies this today.  A Windows Native backend can later
    implement the same surface without changing deployment orchestration.
    """

    def detect(self, *, force: bool = False) -> Any: ...

    def start(self) -> Any: ...

    def stop(self) -> Any: ...

    def restart(self) -> Any: ...

    def logs(self, service: str, lines: int = 200) -> str: ...


@runtime_checkable
class DeploymentProvider(Protocol):
    """High-level deployment orchestration capability."""

    def get_status(self) -> dict[str, Any]: ...

    def start_deploy(self, **kwargs: Any) -> str: ...

    def get_job(self, job_id: str) -> dict[str, Any] | None: ...

    def update_persona(self, prompt: str) -> str: ...

    def container_action(self, action: str) -> str: ...

    def get_logs(self, service: str, lines: int = 200) -> str: ...


@runtime_checkable
class ExtensionManager(Protocol):
    """Install/update lifecycle for an optional out-of-tree extension."""

    def status(self) -> dict[str, Any]: ...

    def install(self) -> dict[str, Any]: ...

    def update(self) -> dict[str, Any]: ...


@runtime_checkable
class ResourceStats(Protocol):
    """Platform-neutral resource measurement capability."""

    def directory_usage(self, path: Path | str) -> tuple[int, int, list[str]]: ...

    def disk_usage(self, path: Path | str) -> dict[str, Any]: ...

    def host_memory(self) -> dict[str, Any]: ...

    def parse_docker_stats(self, stdout: str) -> dict[str, dict[str, Any]]: ...


@runtime_checkable
class AstrBotClient(Protocol):
    """Minimal authenticated AstrBot dashboard API surface."""

    def wait_until_ready(self, timeout: float = 180.0, *, poll_interval: float = 2.0) -> None: ...

    def login(self, *, force: bool = False) -> str: ...

    def upsert_persona(self, persona_id: str, system_prompt: str) -> None: ...


# More explicit aliases for consumers that prefer the Provider suffix.
ConfigStoreProvider = ConfigStore
StateProviderProtocol = StateProvider
DeploymentProviderProtocol = DeploymentProvider
ExtensionManagerProtocol = ExtensionManager
ResourceStatsProvider = ResourceStats
AstrBotClientProtocol = AstrBotClient


@dataclass(frozen=True)
class UIComponentDescriptor:
    """Metadata-only UI contribution; no route is moved in phase 1."""

    component_id: str
    title: str
    route: str
    kind: str = "page"
    requires_token: bool = True
    api_prefixes: Sequence[str] = field(default_factory=tuple)
    capabilities: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.component_id,
            "title": self.title,
            "route": self.route,
            "kind": self.kind,
            "requires_token": bool(self.requires_token),
            "api_prefixes": list(self.api_prefixes),
            "capabilities": dict(self.capabilities),
        }


@runtime_checkable
class UIComponentProvider(Protocol):
    """Future frontend/backend component registry contract."""

    def ui_components(self) -> Sequence[UIComponentDescriptor]: ...


__all__ = [
    "AstrBotClient",
    "AstrBotClientProtocol",
    "ConfigStore",
    "ConfigStoreProvider",
    "DeploymentProvider",
    "DeploymentProviderProtocol",
    "ExtensionManager",
    "ExtensionManagerProtocol",
    "ResourceStats",
    "ResourceStatsProvider",
    "RuntimeBackend",
    "StateProvider",
    "StateProviderProtocol",
    "UIComponentDescriptor",
    "UIComponentProvider",
]
