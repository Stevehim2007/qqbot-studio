"""Protocol interfaces for the QQ Bot enhancer plugin.

These protocols deliberately avoid importing AstrBot at module import time, so
the service layer can be unit-tested independently and main.py remains the
AstrBot adapter/composition root.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class MediaPayload:
    """Decoded image bytes plus safe metadata (no source path)."""

    data: bytes
    mime: str
    extension: str
    sha256: str
    width: int | None = None
    height: int | None = None


@dataclass(slots=True)
class StickerRecord:
    """A collected sticker index record without an absolute path field."""

    sha256: str
    filename: str
    category: str
    size: int
    created_at: int
    source: str = ""


@dataclass(slots=True)
class ImageCandidate:
    """A search result candidate with relevance metadata."""

    url: str
    title: str = ""
    description: str = ""
    page_url: str = ""
    referer: str = ""
    source: str = ""
    relevance_score: float = 0.0


@runtime_checkable
class MediaDecoder(Protocol):
    """Decode an AstrBot image component into safe bytes/metadata."""

    async def decode(self, image: Any, *, max_bytes: int) -> MediaPayload | None:
        ...


@runtime_checkable
class StickerRepository(Protocol):
    """Persistence contract for built-in and collected sticker services."""

    async def save(
        self,
        payload: MediaPayload,
        *,
        category: str,
        source: str = "",
    ) -> tuple[str, StickerRecord | None]:
        ...

    def categories(self) -> dict[str, int]:
        ...

    def list(self, category: str | None = None) -> list[StickerRecord]:
        ...

    def random(self, category: str | None = None) -> StickerRecord | None:
        ...


@runtime_checkable
class ImageSearchProvider(Protocol):
    """Search provider contract used by command/NL search flows."""

    async def search(
        self,
        keyword: str,
        *,
        limit: int,
    ) -> list[ImageCandidate]:
        ...


@runtime_checkable
class SongRepository(Protocol):
    """Local authorized-song repository contract."""

    def list_tracks(self) -> list[Path]:
        ...

    def resolve(self, filename: str) -> Path | None:
        ...


@runtime_checkable
class LLMService(Protocol):
    """Minimal provider-facing LLM operation used by the plugin."""

    async def generate(
        self,
        *,
        chat_provider_id: str,
        prompt: str,
        system_prompt: str | None = None,
        image_urls: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        ...
