"""Collected-sticker persistence helpers.

The store is deliberately independent of AstrBot. It only accepts a root path,
config-derived limits and a category-normalizer callback from the composition
root. No user text, user id or absolute path is serialized.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

COLLECTED_STICKER_DIR_NAME = "collected_stickers"
COLLECTED_INDEX_FILENAME = "index.json"
COLLECTED_CATEGORY_RE = re.compile(r"^[0-9A-Za-z_\-\u4e00-\u9fff]{1,32}$")
COLLECTED_FILENAME_RE = re.compile(r"^[a-f0-9]{64}\.(?:jpg|png|gif|webp|bmp|avif)$")
COLLECTED_DIR_MODE = 0o755
COLLECTED_FILE_MODE = 0o644


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
        log_warning: Callable[[str], None] | None = None,
        max_tags: int = 12,
        max_tag_chars: int = 24,
        max_meaning_chars: int = 200,
        max_tone_chars: int = 24,
        max_categories: int = 8,
    ) -> None:
        self.root = Path(root)
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.category_normalizer = category_normalizer
        self.default_category = default_category
        self.clock = clock
        self.log_warning = log_warning
        self.max_tags = max(0, int(max_tags))
        self.max_tag_chars = max(1, int(max_tag_chars))
        self.max_meaning_chars = max(1, int(max_meaning_chars))
        self.max_tone_chars = max(1, int(max_tone_chars))
        self.max_categories = max(1, int(max_categories))

    def _warn(self, message: str) -> None:
        if self.log_warning is not None:
            self.log_warning(message)

    def _ensure_root_mode(self) -> bool:
        try:
            if self.root.is_symlink():
                self._warn(
                    "qqbot_enhancer 收藏根目录是符号链接，跳过权限调整"
                )
                return False
            self.root.mkdir(
                parents=True,
                exist_ok=True,
                mode=COLLECTED_DIR_MODE,
            )
            self.root.chmod(COLLECTED_DIR_MODE)
            return True
        except OSError as exc:
            self._warn(
                "qqbot_enhancer 无法将收藏根目录调整为 0755："
                f"{exc.strerror or exc}；请检查容器/宿主机用户对 "
                "plugin_data/qqbot_enhancer 的权限"
            )
            return False

    @property
    def index_path(self) -> Path:
        return self.root / COLLECTED_INDEX_FILENAME

    def _sanitize_tags(self, raw: Any) -> list[str] | None:
        if raw is None:
            return []
        if not isinstance(raw, (list, tuple, set)):
            return None
        tags: list[str] = []
        for item in raw:
            tag = str(item or "").strip()
            if not tag or len(tag) > self.max_tag_chars:
                return None
            if not re.fullmatch(r"[0-9A-Za-z_\-\u4e00-\u9fff]+", tag):
                return None
            if tag not in tags:
                tags.append(tag)
        if len(tags) > self.max_tags:
            return None
        return tags

    def _sanitize_meaning(self, raw: Any) -> str | None:
        meaning = str(raw or "").strip()
        if len(meaning) > self.max_meaning_chars:
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in meaning):
            return None
        return re.sub(r"\s+", " ", meaning).strip()

    def _sanitize_tone(self, raw: Any) -> str | None:
        tone = str(raw or "").strip()
        if not tone:
            return ""
        if len(tone) > self.max_tone_chars:
            return None
        if not re.fullmatch(r"[0-9A-Za-z_\-\u4e00-\u9fff]+", tone):
            return None
        return tone

    def _sanitize_categories(
        self,
        raw: Any,
        *,
        primary: str | None = None,
    ) -> list[str] | None:
        if not isinstance(raw, (list, tuple, set)):
            return None
        categories: list[str] = []
        for item in raw:
            category = self.category_normalizer(item)
            if category is None:
                return None
            if category not in categories:
                categories.append(category)
        if primary is not None:
            primary_norm = self.category_normalizer(primary)
            if primary_norm is None:
                return None
            if primary_norm in categories:
                categories.remove(primary_norm)
            categories.insert(0, primary_norm)
        if not categories or len(categories) > self.max_categories:
            return None
        return categories

    def _serialize_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        tags = self._sanitize_tags(entry.get("tags", []))
        meaning = self._sanitize_meaning(entry.get("meaning", ""))
        tone = self._sanitize_tone(entry.get("tone", ""))
        try:
            updated_at = int(
                entry.get("updated_at", entry.get("created_at", 0)) or 0
            )
        except (TypeError, ValueError):
            updated_at = 0
        primary = str(entry.get("category", self.default_category))
        raw_categories = entry.get("categories")
        categories = self._sanitize_categories(
            raw_categories if isinstance(raw_categories, (list, tuple, set)) else [primary],
            primary=primary,
        ) or [primary]
        return {
            "sha256": str(entry.get("sha256", "")),
            "filename": str(entry.get("filename", "")),
            "category": primary,
            "categories": categories,
            "size": int(entry.get("size", 0)),
            "created_at": int(entry.get("created_at", 0)),
            "source": str(entry.get("source", "")),
            "tags": tags or [],
            "meaning": meaning or "",
            "tone": tone or "",
            "updated_at": updated_at,
        }

    @staticmethod
    def atomic_write_bytes(
        path: Path,
        data: bytes,
        *,
        log_warning: Callable[[str], None] | None = None,
    ) -> None:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
            mode=COLLECTED_DIR_MODE,
        )
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
            try:
                os.chmod(path, COLLECTED_FILE_MODE)
            except OSError as exc:
                if log_warning is not None:
                    log_warning(
                        "qqbot_enhancer 收藏文件 chmod 0644 失败: "
                        f"{path.name}: {exc.strerror or exc}"
                    )
        except Exception:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def save_index(self, entries: list[dict[str, Any]]) -> None:
        self._ensure_root_mode()
        serializable = [
            self._serialize_entry(entry) for entry in entries
        ]
        payload = {
            "version": 1,
            "saved_at": int(self.clock()),
            "stickers": serializable,
        }
        self.atomic_write_bytes(
            self.index_path,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            log_warning=self.log_warning,
        )

    def category_dir(self, category: str, *, create: bool = False) -> Path | None:
        if not category:
            return None
        self._ensure_root_mode()
        try:
            base = self.root.resolve()
            raw_dir = base / category
            if raw_dir.is_symlink():
                return None
            if create:
                raw_dir.mkdir(
                    parents=False,
                    exist_ok=True,
                    mode=COLLECTED_DIR_MODE,
                )
            if not raw_dir.is_dir():
                return None
            resolved = raw_dir.resolve()
            if resolved.parent != base:
                return None
            self._chmod_safe(
                resolved,
                COLLECTED_DIR_MODE,
                base,
            )
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
            categories = normalized.get("categories")
            if not isinstance(categories, (list, tuple, set)):
                categories = [category]
            categories = self._sanitize_categories(
                categories,
                primary=category,
            )
            if categories is None:
                categories = [category]
            normalized["categories"] = categories
            normalized["category"] = category
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
            try:
                updated_at = int(
                    item.get("updated_at", created_at) or created_at
                )
            except (TypeError, ValueError):
                updated_at = created_at
            tags = self._sanitize_tags(item.get("tags", [])) or []
            meaning = self._sanitize_meaning(item.get("meaning", "")) or ""
            tone = self._sanitize_tone(item.get("tone", "")) or ""
            raw_categories = item.get("categories")
            if not isinstance(raw_categories, (list, tuple, set)):
                raw_categories = [category]
            categories = self._sanitize_categories(
                raw_categories,
                primary=category,
            ) or [category]
            loaded.append(
                {
                    "sha256": sha256,
                    "filename": filename,
                    "category": category,
                    "categories": categories,
                    "size": size,
                    "created_at": created_at,
                    "source": str(item.get("source", "") or ""),
                    "tags": tags,
                    "meaning": meaning,
                    "tone": tone,
                    "updated_at": updated_at,
                    "path": path,
                }
            )
        return loaded

    def _is_within(self, path: Path, base: Path) -> bool:
        try:
            if path.is_symlink():
                return False
            resolved = path.resolve()
            resolved_base = base.resolve()
            return (
                resolved == resolved_base
                or resolved_base in resolved.parents
            )
        except (OSError, ValueError):
            return False

    def _chmod_safe(self, path: Path, mode: int, base: Path) -> bool:
        if path.is_symlink():
            return False
        try:
            if not self._is_within(path, base):
                self._warn(
                    "qqbot_enhancer 收藏权限修复跳过越界路径: "
                    + path.name
                )
                return False
            path.chmod(mode)
            return True
        except OSError as exc:
            self._warn(
                "qqbot_enhancer 收藏权限修复失败: "
                f"{path.name}: {exc.strerror or exc}；请检查容器用户与"
                "宿主机管理用户对 plugin_data/qqbot_enhancer 的权限"
            )
            return False

    def migrate_permissions(
        self,
        data_root: Path | str,
    ) -> dict[str, int]:
        """Best-effort 0755/0644 migration without following symlinks.

        Only the collected_stickers subtree is walked. Symlinks and non-regular
        files are skipped; failures are logged and never delete or rewrite
        sticker content.
        """

        summary = {"dirs": 0, "files": 0, "skipped": 0, "errors": 0}
        try:
            base = Path(data_root).resolve()
            root = self.root.resolve()
            if root != base and base not in root.parents:
                self._warn(
                    "qqbot_enhancer 收藏目录越出插件数据根，跳过权限迁移"
                )
                summary["errors"] += 1
                return summary
            if not root.exists() or not root.is_dir():
                return summary
        except (OSError, ValueError) as exc:
            self._warn(
                "qqbot_enhancer 收藏权限迁移路径解析失败: "
                f"{exc!s}"
            )
            summary["errors"] += 1
            return summary

        def onerror(error: OSError) -> None:
            summary["errors"] += 1
            self._warn(
                "qqbot_enhancer 收藏权限迁移遍历失败: "
                f"{error.strerror or error}"
            )

        for dirpath, dirnames, filenames in os.walk(
            root,
            topdown=True,
            followlinks=False,
            onerror=onerror,
        ):
            dir_path = Path(dirpath)
            kept_dirnames: list[str] = []
            for dirname in dirnames:
                child = dir_path / dirname
                if child.is_symlink():
                    summary["skipped"] += 1
                    continue
                kept_dirnames.append(dirname)
            dirnames[:] = kept_dirnames
            if self._chmod_safe(dir_path, COLLECTED_DIR_MODE, base):
                summary["dirs"] += 1
            else:
                summary["errors"] += 1
            for filename in filenames:
                path = dir_path / filename
                if path.is_symlink():
                    summary["skipped"] += 1
                    continue
                try:
                    mode = path.lstat().st_mode
                except OSError as exc:
                    summary["errors"] += 1
                    self._warn(
                        "qqbot_enhancer 收藏权限迁移无法读取文件: "
                        f"{path.name}: {exc.strerror or exc}"
                    )
                    continue
                if not stat.S_ISREG(mode):
                    summary["skipped"] += 1
                    continue
                if self._chmod_safe(path, COLLECTED_FILE_MODE, base):
                    summary["files"] += 1
                else:
                    summary["errors"] += 1
        return summary

    def update_metadata(
        self,
        entries: list[dict[str, Any]],
        sha256: str,
        *,
        category: str | None = None,
        categories: Any = None,
        tags: Any = None,
        meaning: Any = None,
        tone: Any = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Update one sticker's category/tags/meaning/tone and index atomically.

        Returns status in {updated, same, not_found, invalid, conflict, storage}.
        Existing metadata is preserved for fields passed as None. File content is
        never rewritten; category changes use os.replace with rollback on index
        failure.
        """

        digest = str(sha256 or "").lower()
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            return "invalid", None
        entry = next(
            (
                item
                for item in entries
                if str(item.get("sha256", "")) == digest
            ),
            None,
        )
        if entry is None:
            return "not_found", None
        old_category = self.category_normalizer(entry.get("category"))
        filename = str(entry.get("filename", "") or "")
        if (
            old_category is None
            or old_category != entry.get("category")
            or not COLLECTED_FILENAME_RE.fullmatch(filename)
            or not filename.startswith(digest + ".")
        ):
            return "invalid", None
        old_path = self.file_path(old_category, filename)
        if old_path is None:
            return "invalid", None

        existing_categories = entry.get("categories")
        if not isinstance(existing_categories, (list, tuple, set)):
            existing_categories = [old_category]
        existing_categories = self._sanitize_categories(
            existing_categories,
            primary=old_category,
        ) or [old_category]
        if categories is not None:
            primary_hint = category
            new_categories = self._sanitize_categories(
                categories,
                primary=primary_hint,
            )
            if new_categories is None:
                return "invalid", None
        elif category is not None:
            normalized_category = self.category_normalizer(category)
            if normalized_category is None:
                return "invalid", None
            new_categories = [normalized_category]
        else:
            new_categories = list(existing_categories)
        new_category = new_categories[0]

        existing_tags = entry.get("tags", [])
        existing_meaning = entry.get("meaning", "")
        existing_tone = entry.get("tone", "")
        new_tags = (
            self._sanitize_tags(existing_tags)
            if tags is None
            else self._sanitize_tags(tags)
        )
        new_meaning = (
            self._sanitize_meaning(existing_meaning)
            if meaning is None
            else self._sanitize_meaning(meaning)
        )
        new_tone = (
            self._sanitize_tone(existing_tone)
            if tone is None
            else self._sanitize_tone(tone)
        )
        if (
            new_tags is None
            or new_meaning is None
            or new_tone is None
        ):
            return "invalid", None

        entry_copy = dict(entry)
        entry_copy["path"] = old_path
        category_changed = old_category != new_category
        metadata_changed = bool(
            new_tags != (existing_tags or [])
            or new_meaning != (existing_meaning or "")
            or new_tone != (existing_tone or "")
            or new_categories != existing_categories
            or category_changed
        )
        if not category_changed and not metadata_changed:
            return "same", entry_copy

        target_path = old_path
        moved = False
        if category_changed:
            new_dir = self.category_dir(new_category, create=True)
            if new_dir is None:
                return "storage", None
            candidate = new_dir / filename
            if candidate.is_symlink() or candidate.exists():
                return "conflict", None
            try:
                os.replace(old_path, candidate)
            except OSError as exc:
                self._warn(
                    "qqbot_enhancer 收藏表情移动失败: "
                    f"{exc.strerror or exc}；旧文件保持不变"
                )
                return "storage", None
            moved = True
            target_path = candidate
            try:
                os.chmod(target_path, COLLECTED_FILE_MODE)
            except OSError as exc:
                self._warn(
                    "qqbot_enhancer 重分类后文件 chmod 0644 失败: "
                    f"{exc.strerror or exc}"
                )
            try:
                new_dir.chmod(COLLECTED_DIR_MODE)
            except OSError as exc:
                self._warn(
                    "qqbot_enhancer 重分类目录 chmod 0755 失败: "
                    f"{exc.strerror or exc}"
                )

        new_entry = dict(entry_copy)
        new_entry.update(
            {
                "category": new_category,
                "categories": new_categories,
                "tags": new_tags,
                "meaning": new_meaning,
                "tone": new_tone,
                "updated_at": int(self.clock()),
                "path": target_path,
            }
        )
        updated_entries = [
            new_entry
            if str(item.get("sha256", "")) == digest
            else dict(item)
            for item in entries
        ]
        try:
            self.save_index(updated_entries)
        except Exception:
            if moved:
                try:
                    os.replace(target_path, old_path)
                    try:
                        os.chmod(old_path, COLLECTED_FILE_MODE)
                    except OSError:
                        pass
                except OSError as rollback_exc:
                    self._warn(
                        "qqbot_enhancer 收藏元数据更新失败且回滚移动失败: "
                        f"{rollback_exc.strerror or rollback_exc}"
                    )
            raise
        return "updated", new_entry

    def update_categories(
        self,
        entries: list[dict[str, Any]],
        sha256: str,
        *,
        categories: Any = None,
        add: Any = None,
        remove: Any = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Replace/add/remove category memberships without copying the file."""

        entry = next(
            (
                item
                for item in entries
                if str(item.get("sha256", "")) == str(sha256 or "").lower()
            ),
            None,
        )
        if entry is None:
            return "not_found", None
        old_category = self.category_normalizer(entry.get("category"))
        if old_category is None:
            return "invalid", None
        current = entry.get("categories")
        if not isinstance(current, (list, tuple, set)):
            current = [old_category]
        current = self._sanitize_categories(
            current,
            primary=old_category,
        ) or [old_category]
        if categories is not None:
            new_categories = self._sanitize_categories(categories)
            if new_categories is None:
                return "invalid", None
        else:
            new_categories = list(current)
            if add is not None:
                add_values = add if isinstance(add, (list, tuple, set)) else [add]
                sanitized_add = self._sanitize_categories(add_values)
                if sanitized_add is None:
                    return "invalid", None
                for category in sanitized_add:
                    if category not in new_categories:
                        new_categories.append(category)
            if remove is not None:
                remove_values = (
                    remove if isinstance(remove, (list, tuple, set)) else [remove]
                )
                sanitized_remove = self._sanitize_categories(remove_values)
                if sanitized_remove is None:
                    return "invalid", None
                new_categories = [
                    category
                    for category in new_categories
                    if category not in sanitized_remove
                ]
        if not new_categories or len(new_categories) > self.max_categories:
            return "invalid", None
        primary = (
            old_category
            if old_category in new_categories
            else new_categories[0]
        )
        # Keep primary category first for physical-path stability.
        new_categories = [primary] + [
            category for category in new_categories if category != primary
        ]
        return self.update_metadata(
            entries,
            sha256,
            category=primary,
            categories=new_categories,
        )

    def add_categories(
        self,
        entries: list[dict[str, Any]],
        sha256: str,
        add: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        return self.update_categories(entries, sha256, add=add)

    def remove_categories(
        self,
        entries: list[dict[str, Any]],
        sha256: str,
        remove: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        return self.update_categories(entries, sha256, remove=remove)

    def reclassify(
        self,
        entries: list[dict[str, Any]],
        sha256: str,
        new_category: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Compatibility wrapper: preserve metadata while changing category."""

        old_entry = next(
            (
                item
                for item in entries
                if str(item.get("sha256", "")) == str(sha256 or "").lower()
            ),
            None,
        )
        old_category = (
            old_entry.get("category") if old_entry is not None else None
        )
        status, new_entry = self.update_metadata(
            entries,
            sha256,
            category=new_category,
        )
        if status == "updated":
            if old_category != new_category:
                return "moved", new_entry
            return "updated", new_entry
        return status, new_entry

    move_sticker = reclassify

    @staticmethod
    def categories(entries: list[dict[str, Any]]) -> dict[str, int]:
        result: dict[str, int] = {}
        for entry in entries:
            categories = entry.get("categories")
            if not isinstance(categories, (list, tuple, set)):
                categories = [entry.get("category", "")]
            for raw_category in categories:
                category = str(raw_category or "")
                if category:
                    result[category] = result.get(category, 0) + 1
        return result

    @staticmethod
    def unique_count(entries: list[dict[str, Any]]) -> int:
        return len(
            {
                str(entry.get("sha256", ""))
                for entry in entries
                if entry.get("sha256")
            }
        )

    @staticmethod
    def list_entries(
        entries: list[dict[str, Any]],
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        if category is None:
            return list(entries)
        return [
            entry
            for entry in entries
            if category
            in (
                entry.get("categories")
                if isinstance(entry.get("categories"), (list, tuple, set))
                else [entry.get("category")]
            )
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
