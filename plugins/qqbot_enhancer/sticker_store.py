"""Collected-sticker persistence helpers.

The store is deliberately independent of AstrBot. It only accepts a root path,
config-derived limits and a category-normalizer callback from the composition
root. No user text, user id or absolute path is serialized.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

COLLECTED_STICKER_DIR_NAME = "collected_stickers"
COLLECTED_INDEX_FILENAME = "index.json"
COLLECTED_CATEGORY_RE = re.compile(r"^[0-9A-Za-z_\-\u4e00-\u9fff]{1,32}$")
COLLECTED_FILENAME_RE = re.compile(r"^[a-f0-9]{64}\.(?:jpg|png|gif|webp|bmp|avif)$")


class StickerStoreError(ValueError):
    """Invalid sticker-store operation; callers should degrade safely."""


class CollectedStickerStore:
    def __init__(
        self,
        root: Path | str,
        *,
        max_file_bytes: int,
        category_normalizer: Callable[[Any], str | None],
        default_category: str = "未分类",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.category_normalizer = category_normalizer
        self.default_category = default_category
        self.clock = clock

    @property
    def index_path(self) -> Path:
        return self.root / COLLECTED_INDEX_FILENAME

    @staticmethod
    def atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".tmp-",
                dir=str(path.parent),
                delete=False,
            ) as temporary:
                temp_path = temporary.name
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temp_path, path)
        except Exception:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def save_index(self, entries: list[dict[str, Any]]) -> None:
        serializable = []
        for entry in entries:
            serializable.append(
                {
                    "sha256": str(entry.get("sha256", "")),
                    "filename": str(entry.get("filename", "")),
                    "category": str(
                        entry.get("category", self.default_category)
                    ),
                    "size": int(entry.get("size", 0)),
                    "created_at": int(entry.get("created_at", 0)),
                    "source": str(entry.get("source", "")),
                }
            )
        payload = {
            "version": 1,
            "saved_at": int(self.clock()),
            "stickers": serializable,
        }
        self.atomic_write_bytes(
            self.index_path,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def category_dir(self, category: str, *, create: bool = False) -> Path | None:
        if not category:
            return None
        try:
            base = self.root.resolve()
            raw_dir = base / category
            if raw_dir.is_symlink():
                return None
            if create:
                raw_dir.mkdir(parents=False, exist_ok=True, mode=0o700)
            if not raw_dir.is_dir():
                return None
            resolved = raw_dir.resolve()
            if resolved.parent != base:
                return None
            return resolved
        except (OSError, ValueError):
            return None

    def file_path(self, category: str, filename: str) -> Path | None:
        if not COLLECTED_FILENAME_RE.fullmatch(filename):
            return None
        category_dir = self.category_dir(category)
        if category_dir is None:
            return None
        candidate = category_dir / filename
        if candidate.is_symlink():
            return None
        try:
            resolved = candidate.resolve()
            if resolved.parent != category_dir or not resolved.is_file():
                return None
            return resolved
        except (OSError, ValueError):
            return None

    def valid_entries(
        self,
        entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        valid: list[dict[str, Any]] = []
        for entry in entries:
            category = self.category_normalizer(entry.get("category"))
            filename = str(entry.get("filename", ""))
            if category is None or category != entry.get("category"):
                continue
            path = self.file_path(category, filename)
            if path is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > self.max_file_bytes:
                continue
            normalized = dict(entry)
            normalized["path"] = path
            normalized["size"] = size
            valid.append(normalized)
        return valid

    def load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.is_file():
            return []
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw_entries = payload.get("stickers", []) if isinstance(payload, dict) else []
        if not isinstance(raw_entries, list):
            return []
        loaded: list[dict[str, Any]] = []
        seen_sha: set[str] = set()
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            sha256 = str(item.get("sha256", "") or "").lower()
            filename = str(item.get("filename", "") or "")
            category = self.category_normalizer(item.get("category"))
            if (
                not re.fullmatch(r"[a-f0-9]{64}", sha256)
                or category is None
                or not COLLECTED_FILENAME_RE.fullmatch(filename)
                or not filename.startswith(sha256 + ".")
                or sha256 in seen_sha
            ):
                continue
            path = self.file_path(category, filename)
            if path is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > self.max_file_bytes:
                continue
            seen_sha.add(sha256)
            try:
                created_at = int(item.get("created_at", 0) or 0)
            except (TypeError, ValueError):
                created_at = 0
            loaded.append(
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": category,
                    "size": size,
                    "created_at": created_at,
                    "source": str(item.get("source", "") or ""),
                    "path": path,
                }
            )
        return loaded

    @staticmethod
    def categories(entries: list[dict[str, Any]]) -> dict[str, int]:
        result: dict[str, int] = {}
        for entry in entries:
            category = str(entry.get("category", "") or "")
            if category:
                result[category] = result.get(category, 0) + 1
        return result

    @staticmethod
    def list_entries(
        entries: list[dict[str, Any]],
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        if category is None:
            return list(entries)
        return [
            entry for entry in entries if entry.get("category") == category
        ]

    @staticmethod
    def find_by_sha(
        entries: list[dict[str, Any]],
        sha256: str,
    ) -> dict[str, Any] | None:
        for entry in entries:
            if entry.get("sha256") == sha256:
                return entry
        return None

    def write_sticker(self, category: str, filename: str, data: bytes) -> Path:
        category_dir = self.category_dir(category, create=True)
        if category_dir is None:
            raise StickerStoreError("invalid sticker category")
        if not COLLECTED_FILENAME_RE.fullmatch(filename):
            raise StickerStoreError("invalid sticker filename")
        target = category_dir / filename
        self.atomic_write_bytes(target, data)
        return target
