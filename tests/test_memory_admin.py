"""Tests for the Studio memory-content admin API, module and panel contract."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from qqbot_studio.dockerctl import CommandResult
from qqbot_studio.memory_admin import (
    KINDS,
    MAX_CONTENT_CHARS,
    MemoryAdmin,
    MemoryAdminError,
    MemoryAdminUnavailable,
    MemoryConflict,
    MemoryRecordNotFound,
    SECRET_PATTERNS,
    _HostPermissionDenied,
    fingerprint,
    normalise_value,
    scrub_secrets,
    tokenize,
)
from qqbot_studio.server import create_server
from qqbot_studio.studio import StudioManager

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_JS = REPO_ROOT / "qqbot_studio" / "static" / "console.js"
CONSOLE_CSS = REPO_ROOT / "qqbot_studio" / "static" / "console.css"
MEMORY_PATCH = REPO_ROOT / "integrations" / "astrbot-memory" / "patches" / "0001-native-memory.patch"

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS astrbot_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    mem_key TEXT NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.5,
    hits INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'heuristic',
    fingerprint TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_used_at REAL,
    expires_at REAL,
    UNIQUE (scope_type, scope_id, mem_key)
);
CREATE INDEX IF NOT EXISTS idx_astrbot_memory_scope
    ON astrbot_memory (scope_type, scope_id);
"""
CORE_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS astrbot_memory_fts
    USING fts5(mem_key, content, tokens, tokenize='unicode61');
"""

GROUP_SCOPE = ("group", "qq:838796241")
OTHER_GROUP_SCOPE = ("group", "qq:111111")
PRIVATE_SCOPE = ("user", "qq:10001")


def make_db(data_dir: Path, *, fts: bool = True, rows: int = 0) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "astrbot_memory.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(CORE_SCHEMA)
    if fts:
        try:
            connection.executescript(CORE_FTS_SCHEMA)
        except sqlite3.Error:  # pragma: no cover - sqlite without FTS5
            pass
    connection.commit()
    connection.close()
    return db_path


def seed_row(db_path: Path, scope=GROUP_SCOPE, key="pref.cilantro", content="群主不吃香菜", **overrides) -> int:
    now = overrides.pop("now", 1_800_000_000.0)
    payload = {
        "scope_type": scope[0],
        "scope_id": scope[1],
        "mem_key": key,
        "content": content,
        "kind": overrides.pop("kind", "preference"),
        "importance": overrides.pop("importance", 0.7),
        "confidence": overrides.pop("confidence", 0.8),
        "hits": overrides.pop("hits", 0),
        "source": overrides.pop("source", "heuristic"),
        "fingerprint": fingerprint(content),
        "created_at": now,
        "updated_at": now,
        "last_used_at": None,
        "expires_at": overrides.pop("expires_at", None),
    }
    connection = sqlite3.connect(db_path)
    cursor = connection.execute(
        "INSERT INTO astrbot_memory (scope_type, scope_id, mem_key, content, kind, importance,"
        " confidence, hits, source, fingerprint, created_at, updated_at, last_used_at, expires_at)"
        " VALUES (:scope_type,:scope_id,:mem_key,:content,:kind,:importance,:confidence,:hits,"
        ":source,:fingerprint,:created_at,:updated_at,:last_used_at,:expires_at)",
        payload,
    )
    connection.commit()
    identifier = int(cursor.lastrowid)
    connection.close()
    return identifier


class TempDbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data" / "astrbot"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def admin(self, **kwargs) -> MemoryAdmin:
        return MemoryAdmin(self.data_dir, **kwargs)

    def seed(self, **kwargs) -> int:
        return seed_row(self.data_dir / "astrbot_memory.db", **kwargs)


class SchemaContractTests(unittest.TestCase):
    """The admin SQL must match the core patch schema (drift guard)."""

    def test_admin_columns_match_core_patch_schema(self) -> None:
        patch = MEMORY_PATCH.read_text(encoding="utf-8")
        block = re.search(r'\+CREATE TABLE IF NOT EXISTS astrbot_memory \((.*?)\+\);', patch, re.S)
        self.assertIsNotNone(block, "core patch schema not found")
        columns = set(re.findall(r"^\+(?:\s*)([a-z_]+) (?:INTEGER|TEXT|REAL)", block.group(1), re.M))
        self.assertIn("scope_type", columns)
        for column in (
            "id", "scope_type", "scope_id", "mem_key", "content", "kind", "importance",
            "confidence", "hits", "source", "fingerprint", "created_at", "updated_at",
            "last_used_at", "expires_at",
        ):
            self.assertIn(column, columns)
        sql = MEMORY_PATCH.read_text(encoding="utf-8")
        for token in ("astrbot_memory_fts", "fts5(mem_key, content, tokens"):
            self.assertIn(token, sql)

    def test_kinds_and_scope_types_match_core(self) -> None:
        from qqbot_studio import memory_admin

        self.assertEqual(
            memory_admin.SCOPE_TYPES, ("user", "group_member", "group", "conversation", "persona", "global")
        )
        for kind in KINDS:
            self.assertIn(kind, {"fact", "preference", "identity", "profile", "habit", "note"})

    def test_tokenizer_and_fingerprint_mirror_core_text(self) -> None:
        self.assertEqual(tokenize("我不吃香菜"), ["我不", "不吃", "吃香", "香菜"])
        self.assertIn("cilantro", tokenize("No Cilantro please"))
        self.assertEqual(len(fingerprint("群主不吃香菜")), 24)
        self.assertEqual(normalise_value("  偏好.香菜  "), "偏好.香菜")
        self.assertEqual(scrub_secrets("api key is abc12345678"), "[已隐去]")
        self.assertTrue(any(pattern.search("记住 password is hunter2") for pattern in SECRET_PATTERNS))


class AdminUnitTests(TempDbTestCase):
    def test_missing_database_is_empty_state_not_a_crash(self) -> None:
        admin = self.admin()
        state = admin.stats(*GROUP_SCOPE)
        self.assertTrue(state["ok"])
        self.assertEqual(state["stats"]["total"], 0)
        self.assertEqual(admin.list_records(*GROUP_SCOPE)["records"], [])
        with self.assertRaises(MemoryAdminUnavailable) as caught:
            admin.create_record(*GROUP_SCOPE, {"content": "不吃香菜"})
        self.assertTrue(caught.exception.empty)

    def test_corrupt_database_is_unavailable(self) -> None:
        self.data_dir.mkdir(parents=True)
        (self.data_dir / "astrbot_memory.db").write_bytes(b"this is not sqlite")
        with self.assertRaises(MemoryAdminUnavailable) as caught:
            self.admin().stats(*GROUP_SCOPE)
        self.assertTrue(caught.exception.corrupt)

    def test_permission_denied_without_container_is_explicit(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()

        def denied(statements, *, write):  # noqa: ANN001
            raise _HostPermissionDenied("Permission denied")

        admin._execute_host = denied  # type: ignore[attr-defined]
        with self.assertRaises(MemoryAdminUnavailable) as caught:
            admin.stats(*GROUP_SCOPE)
        self.assertTrue(caught.exception.permission_denied)
        self.assertTrue(caught.exception.actions)
        self.assertNotIn(str(self.data_dir), str(caught.exception))

    def test_container_fallback_reads_and_writes(self) -> None:
        db_path = make_db(self.data_dir)
        docker = FakeContainerDocker(db_path, "qqbot-astrbot")
        admin = self.admin(docker=docker, container_name="qqbot-astrbot")

        def denied(statements, *, write):  # noqa: ANN001
            raise _HostPermissionDenied("Permission denied")

        admin._execute_host = denied  # type: ignore[attr-defined]
        self.assertEqual(admin.stats(*GROUP_SCOPE)["stats"]["total"], 0)
        created = admin.create_record(*GROUP_SCOPE, {"content": "通过容器写入"})
        self.assertEqual(created["action"], "created")
        self.assertEqual(admin.channel, "container")
        self.assertEqual(admin.list_records(*GROUP_SCOPE)["total"], 1)

    def test_bare_group_number_is_prefixed_for_admin_queries(self) -> None:
        from qqbot_studio import memory_admin
        kind, value = memory_admin.validate_scope("group", "1056821876")
        self.assertEqual(kind, "group")
        self.assertEqual(value, "aiocqhttp:1056821876")
        kind, value = memory_admin.validate_scope("group_member", "1056821876:123456789")
        self.assertEqual(value, "aiocqhttp:1056821876:123456789")

    def test_create_requires_explicit_valid_scope(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        with self.assertRaises(MemoryAdminError):
            admin.create_record("", "", {"content": "x"})
        with self.assertRaises(MemoryAdminError):
            admin.create_record("group_user", "qq:1", {"content": "x"})
        with self.assertRaises(MemoryAdminError):
            admin.create_record("group", "bad/id!", {"content": "x"})

    def test_secret_content_is_rejected_and_scrubbed_on_read(self) -> None:
        db_path = make_db(self.data_dir)
        admin = self.admin()
        for content in (
            "记住 password is hunter2",
            "我的 api key 是 sk-abcdefgh12345678",
            "qq密码是 123456",
            "token: abcdefgh12345678",
        ):
            with self.assertRaises(MemoryAdminError):
                admin.create_record(*GROUP_SCOPE, {"content": content})
        seed_row(db_path, key="legacy.secret", content="api key is hunter2")
        listed = admin.list_records(*GROUP_SCOPE)["records"][0]
        self.assertTrue(listed["redacted"])
        self.assertNotIn("hunter2", listed["content"])
        exported = admin.export_scope(*GROUP_SCOPE)
        self.assertNotIn("hunter2", json.dumps(exported, ensure_ascii=False))

    def test_crud_round_trip_keeps_other_scopes_untouched(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        other = admin.create_record(*OTHER_GROUP_SCOPE, {"content": "另一个群的事实"})
        created = admin.create_record(
            *GROUP_SCOPE,
            {"content": "群主不吃香菜", "kind": "preference", "importance": 0.9, "confidence": 0.8},
        )
        record_id = created["record"]["id"]
        self.assertEqual(created["action"], "created")
        fetched = admin.get_record(*GROUP_SCOPE, record_id)["record"]
        self.assertEqual(fetched["content"], "群主不吃香菜")
        updated = admin.update_record(*GROUP_SCOPE, record_id, {"confidence": 0.4, "ttl_seconds": 3600})
        self.assertAlmostEqual(updated["record"]["confidence"], 0.4)
        self.assertIsNotNone(updated["record"]["expires_at_epoch"])
        cleared = admin.update_record(*GROUP_SCOPE, record_id, {"expires_at": None})
        self.assertIsNone(cleared["record"]["expires_at_epoch"])
        listed = admin.list_records(*GROUP_SCOPE)
        self.assertEqual(listed["total"], 1)
        self.assertEqual(admin.list_records(*OTHER_GROUP_SCOPE)["total"], 1)
        self.assertEqual(admin.get_record(*OTHER_GROUP_SCOPE, other["record"]["id"])["record"]["id"], other["record"]["id"])
        with self.assertRaises(MemoryRecordNotFound):
            admin.get_record(*OTHER_GROUP_SCOPE, record_id)
        deleted = admin.delete_record(*GROUP_SCOPE, record_id, confirm=str(record_id))
        self.assertEqual(deleted["deleted"], record_id)
        self.assertEqual(admin.list_records(*GROUP_SCOPE)["total"], 0)
        self.assertEqual(admin.list_records(*OTHER_GROUP_SCOPE)["total"], 1)

    def test_delete_and_forget_require_matching_confirmation(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        record = admin.create_record(*GROUP_SCOPE, {"content": "不吃香菜"})["record"]
        with self.assertRaises(MemoryAdminError):
            admin.delete_record(*GROUP_SCOPE, record["id"], confirm="")
        with self.assertRaises(MemoryAdminError):
            admin.delete_record(*GROUP_SCOPE, record["id"], confirm="999")
        with self.assertRaises(MemoryAdminError):
            admin.forget(*GROUP_SCOPE, confirm="qq:111111")
        self.assertEqual(admin.forget(*GROUP_SCOPE, confirm="qq:838796241")["deleted"], 1)

    def test_duplicate_key_dedup_skip_and_overwrite(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        admin.create_record(*GROUP_SCOPE, {"mem_key": "偏好.香菜", "content": "不吃香菜"})
        skipped = admin.create_record(*GROUP_SCOPE, {"mem_key": "偏好.香菜", "content": "也不吃香菜"})
        self.assertEqual(skipped["action"], "skipped")
        updated = admin.update_record(
            *GROUP_SCOPE,
            skipped["record"]["id"],
            {"content": "确实不吃香菜", "importance": 0.2},
        )
        self.assertEqual(updated["record"]["content"], "确实不吃香菜")
        overwritten = admin.create_record(
            *GROUP_SCOPE,
            {"mem_key": "偏好.香菜", "content": "改成吃香菜", "dedup": "overwrite"},
        )
        self.assertEqual(overwritten["action"], "updated")
        self.assertEqual(overwritten["record"]["content"], "改成吃香菜")

    def test_pagination_and_search(self) -> None:
        db_path = make_db(self.data_dir)
        for index in range(25):
            seed_row(db_path, key="k.%d" % index, content="第 %d 条记忆" % index, now=1_800_000_000.0 + index)
        admin = self.admin()
        first = admin.list_records(*GROUP_SCOPE, limit=10, offset=0)
        self.assertEqual(first["total"], 25)
        self.assertEqual(len(first["records"]), 10)
        self.assertTrue(first["has_more"])
        last = admin.list_records(*GROUP_SCOPE, limit=10, offset=20)
        self.assertEqual(len(last["records"]), 5)
        self.assertFalse(last["has_more"])
        self.assertEqual(admin.list_records(*GROUP_SCOPE, query="第 7 条")["total"], 1)
        self.assertEqual(admin.list_records(*GROUP_SCOPE, query="%")["total"], 0)

    def test_export_and_import_round_trip_preserves_other_scopes(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        admin.create_record(*GROUP_SCOPE, {"mem_key": "偏好.香菜", "content": "不吃香菜", "ttl_seconds": 86400})
        admin.create_record(*GROUP_SCOPE, {"mem_key": "居住地", "content": "住在广州"})
        other = admin.create_record(*OTHER_GROUP_SCOPE, {"content": "另一个群的记忆"})
        exported = admin.export_scope(*GROUP_SCOPE)
        self.assertEqual(exported["count"], 2)
        self.assertEqual(exported["schema_version"], 1)
        # Restore the same docs into an empty scope and check the content survives.
        restored_scope = ("group", "qq:222222")
        preview = admin.import_records(
            *restored_scope, {"records": exported["records"], "apply": False}
        )
        self.assertEqual(preview["preview"]["would_create"], 2)
        result = admin.import_records(
            *restored_scope,
            {"records": exported["records"], "apply": True, "confirm": restored_scope[1]},
        )
        self.assertEqual(result["result"]["created"], 2)
        restored = {item["content"] for item in admin.list_records(*restored_scope)["records"]}
        self.assertEqual(restored, {"不吃香菜", "住在广州"})
        self.assertEqual(admin.list_records(*OTHER_GROUP_SCOPE)["total"], 1)
        self.assertEqual(admin.get_record(*OTHER_GROUP_SCOPE, other["record"]["id"])["record"]["content"], "另一个群的记忆")

    def test_import_preview_reports_invalid_conflicts_and_cap(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        admin.create_record(*GROUP_SCOPE, {"mem_key": "偏好.香菜", "content": "不吃香菜"})
        records = [
            {"mem_key": "偏好.香菜", "content": "重复键"},
            {"mem_key": "偏好.辣椒", "content": "爱吃辣椒"},
            {"mem_key": "bad", "content": "password is hunter2"},
            {"mem_key": "long", "content": "x" * (MAX_CONTENT_CHARS + 1)},
            {"mem_key": "kind", "content": "正常内容", "kind": "weird"},
            "not-an-object",
        ]
        preview = admin.import_records(*GROUP_SCOPE, {"records": records})["preview"]
        self.assertEqual(preview["total"], 6)
        self.assertEqual(preview["valid"], 1)
        self.assertEqual(len(preview["invalid"]), 4)
        self.assertEqual(preview["conflicts"][0]["kind"], "key")
        self.assertEqual(preview["would_skip"], 1)
        self.assertEqual(preview["would_create"], 1)
        with self.assertRaises(MemoryAdminError):
            admin.import_records(
                *GROUP_SCOPE, {"records": records, "apply": True, "confirm": GROUP_SCOPE[1]}
            )
        applied = admin.import_records(
            *GROUP_SCOPE,
            {"records": records, "apply": True, "confirm": GROUP_SCOPE[1], "skip_invalid": True},
        )
        self.assertEqual(applied["result"]["created"], 1)
        self.assertEqual(applied["result"]["skipped"], 5)

    def test_import_cap_and_oversized_batch_are_rejected(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        with self.assertRaises(MemoryAdminError):
            admin.import_records(
                *GROUP_SCOPE, {"records": [{"content": "x"}] * 501}
            )
        too_many = [{"content": "内容 %d" % index} for index in range(500)]
        preview = admin.import_records(*GROUP_SCOPE, {"records": too_many})["preview"]
        self.assertFalse(preview["exceeds_scope_cap"])
        seed_row(self.data_dir / "astrbot_memory.db", key="seed", content="已存在")
        preview = admin.import_records(*GROUP_SCOPE, {"records": too_many})["preview"]
        self.assertTrue(preview["exceeds_scope_cap"])
        with self.assertRaises(MemoryConflict):
            admin.import_records(
                *GROUP_SCOPE, {"records": too_many, "apply": True, "confirm": GROUP_SCOPE[1]}
            )

    def test_write_batch_rolls_back_on_constraint_error(self) -> None:
        db_path = make_db(self.data_dir)
        admin = self.admin()
        insert = (
            "INSERT INTO astrbot_memory (scope_type, scope_id, mem_key, content, kind, importance,"
            " confidence, hits, source, fingerprint, created_at, updated_at, expires_at)"
            " VALUES (?,?,?,?,?,?,?,0,'studio',?,?,?,NULL)"
        )
        statements = [
            {"sql": insert, "params": ["group", "qq:1", "a", "一", "fact", 0.5, 0.5, "f1", 1.0, 1.0]},
            {"sql": insert, "params": ["group", "qq:1", "a", "二", "fact", 0.5, 0.5, "f2", 1.0, 1.0]},
        ]
        with self.assertRaises(sqlite3.IntegrityError):
            admin._write(statements)  # noqa: SLF001 - transaction contract
        connection = sqlite3.connect(db_path)
        count = connection.execute("SELECT COUNT(*) FROM astrbot_memory").fetchone()[0]
        connection.close()
        self.assertEqual(count, 0)

    def test_import_apply_creates_private_backup(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        admin.create_record(*GROUP_SCOPE, {"content": "旧记忆"})
        result = admin.import_records(
            *GROUP_SCOPE,
            {
                "records": [{"content": "新记忆"}],
                "apply": True,
                "confirm": GROUP_SCOPE[1],
            },
        )
        self.assertTrue(result["backup"].endswith(".json"))
        backup = Path(admin.backup_dir) / result["backup"]
        self.assertTrue(backup.exists())
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(admin.backup_dir).stat().st_mode), 0o700)
        payload = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(payload["scope"]["scope_id"], GROUP_SCOPE[1])
        self.assertEqual(len(payload["records"]), 1)

    def test_reflect_preview_reports_duplicates_and_contradictions_without_deleting(self) -> None:
        db_path = make_db(self.data_dir)
        seed_row(db_path, key="a", content="我喜欢吃辣", confidence=0.9, now=1_800_000_000.0)
        seed_row(db_path, key="b", content="我喜欢吃辣", confidence=0.6, now=1_700_000_000.0)
        seed_row(db_path, key="c", content="我不喜欢吃辣", confidence=0.5, now=1_700_000_000.0)
        admin = self.admin()
        preview = admin.reflect(*GROUP_SCOPE)["preview"]
        self.assertEqual(preview["counts"]["duplicates"], 1)
        self.assertGreaterEqual(preview["counts"]["contradictions"], 1)
        self.assertEqual(preview["duplicates"][0]["keep_id"], 1)
        self.assertEqual(admin.list_records(*GROUP_SCOPE)["total"], 3)
        applied = admin.reflect(*GROUP_SCOPE, apply_changes=True, confirm=GROUP_SCOPE[1])
        self.assertEqual(applied["result"]["deleted"], 1)
        self.assertEqual(admin.list_records(*GROUP_SCOPE)["total"], 2)
        self.assertTrue((Path(admin.backup_dir) / applied["backup"]).exists())

    def test_expired_rows_are_reported_and_purged_by_reflect(self) -> None:
        db_path = make_db(self.data_dir)
        seed_row(db_path, key="old", content="过期记忆", expires_at=1.0)
        admin = self.admin()
        preview = admin.reflect(*GROUP_SCOPE)["preview"]
        self.assertEqual(preview["counts"]["expired"], 1)
        applied = admin.reflect(*GROUP_SCOPE, apply_changes=True, confirm=GROUP_SCOPE[1])
        self.assertEqual(applied["result"]["deleted"], 1)
        self.assertEqual(admin.list_records(*GROUP_SCOPE)["total"], 0)

    def test_forget_cleans_only_requested_scope(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        admin.create_record(*GROUP_SCOPE, {"content": "会忘掉"})
        admin.create_record(*OTHER_GROUP_SCOPE, {"content": "保留"})
        admin.create_record(*PRIVATE_SCOPE, {"content": "私聊保留"})
        deleted = admin.forget(*GROUP_SCOPE, confirm=GROUP_SCOPE[1])
        self.assertEqual(deleted["deleted"], 1)
        self.assertEqual(admin.list_records(*OTHER_GROUP_SCOPE)["total"], 1)
        self.assertEqual(admin.list_records(*PRIVATE_SCOPE)["total"], 1)

    def test_stats_track_hits_kinds_and_expired(self) -> None:
        db_path = make_db(self.data_dir)
        seed_row(db_path, key="a", content="爱好", kind="preference", hits=3)
        seed_row(db_path, key="b", content="过期", kind="note", expires_at=1.0)
        stats = self.admin().stats(*GROUP_SCOPE)["stats"]
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["hits"], 3)
        self.assertEqual(stats["expired"], 1)
        self.assertEqual(stats["kinds"], {"preference": 1, "note": 1})

    def test_fts_index_is_maintained_for_admin_writes(self) -> None:
        db_path = make_db(self.data_dir)
        admin = self.admin()
        created = admin.create_record(*GROUP_SCOPE, {"content": "喜欢吃粤菜"})["record"]
        admin.update_record(*GROUP_SCOPE, created["id"], {"content": "只爱吃面食"})
        connection = sqlite3.connect(db_path)
        old_hits = connection.execute(
            "SELECT rowid FROM astrbot_memory_fts WHERE astrbot_memory_fts MATCH ?", ('"喜欢"',)
        ).fetchall()
        new_hits = connection.execute(
            "SELECT rowid FROM astrbot_memory_fts WHERE astrbot_memory_fts MATCH ?", ('"面食"',)
        ).fetchall()
        connection.close()
        self.assertEqual(new_hits, [(created["id"],)])
        self.assertEqual(old_hits, [])

    def test_database_without_fts5_still_works(self) -> None:
        make_db(self.data_dir, fts=False)
        admin = self.admin()
        created = admin.create_record(*GROUP_SCOPE, {"content": "无 FTS 也能用"})
        self.assertEqual(created["action"], "created")
        self.assertEqual(admin.delete_record(*GROUP_SCOPE, created["record"]["id"], confirm=str(created["record"]["id"]))["deleted"], created["record"]["id"])

    def test_database_path_is_masked_from_errors(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        with self.assertRaises(MemoryAdminError) as caught:
            admin.create_record(*GROUP_SCOPE, {"content": "x" * (MAX_CONTENT_CHARS + 1)})
        self.assertNotIn(str(self.data_dir), str(caught.exception))
        self.assertNotIn("astrbot_memory.db", str(caught.exception))


class FakeContainerDocker:
    """Minimal docker client that runs the statement protocol against a temp copy."""

    def __init__(self, db_path: Path, container_name: str, *, fail: str = "") -> None:
        self.db_path = Path(db_path)
        self.ASTRBOT_CONTAINER = container_name
        self.fail = fail
        self.calls: list[tuple[str, list[str]]] = []

    def is_container_running(self, name: str) -> bool:  # noqa: ARG002
        return True

    def exec_in_container(self, container: str, argv, timeout: int = 0):  # noqa: ANN001
        self.calls.append((container, list(argv)))
        if self.fail:
            return CommandResult((), 1, "", self.fail)
        import base64

        payload = json.loads(base64.b64decode(argv[4]).decode("utf-8"))
        write = bool(payload.get("write"))
        handle, copy_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        shutil.copy(self.db_path, copy_path)
        os.chmod(copy_path, 0o600)
        connection = sqlite3.connect(
            "file:%s?mode=%s" % (copy_path, "rw" if write else "ro"), uri=True, timeout=5,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout=5000")
        output: list[object] = []
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            for statement in payload.get("statements") or []:
                cursor = connection.execute(statement["sql"], statement.get("params") or [])
                if cursor.description:
                    columns = [item[0] for item in cursor.description]
                    output.append([dict(zip(columns, row)) for row in cursor.fetchall()])
                else:
                    output.append({"rowcount": cursor.rowcount})
            if write:
                connection.execute("COMMIT")
        except BaseException:
            if write:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        if write:
            shutil.copy(copy_path, self.db_path)
        os.unlink(copy_path)
        return CommandResult((), 0, "MEMADMIN:" + json.dumps(output, ensure_ascii=False), "")


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = StudioManager(self.tmp.name)
        instance = self.studio.create_instance("测试实例")
        self.instance_id = instance["id"]
        self.manager = self.studio.get_manager(self.instance_id)
        self.data_dir = Path(self.manager.astrbot_data_dir)
        self.db_path = make_db(self.data_dir)
        self.server = create_server(self.studio, host="127.0.0.1", port=0, token="memory-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = "http://%s:%s" % (host, port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def call(self, method: str, path: str, payload=None, *, token: bool = True):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=body, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-QB-Token", "memory-token")
        try:
            with self.opener.open(request, timeout=6) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw or b"{}")
            except ValueError:
                return exc.code, {"raw": raw.decode("utf-8", "replace")}

    def path(self, tail: str = "") -> str:
        return "/api/instances/%s/memory%s" % (self.instance_id, tail)

    def scope_query(self, scope=GROUP_SCOPE, extra: str = "") -> str:
        query = "?scope_type=%s&scope_id=%s" % (scope[0], urllib.parse.quote(scope[1]))
        return query + extra

    def test_api_requires_token(self) -> None:
        for method, tail, payload in (
            ("GET", "/stats", None),
            ("GET", "/records", None),
            ("POST", "/records", {"content": "x"}),
            ("POST", "/export", {}),
        ):
            status, _ = self.call(method, self.path(tail), payload, token=False)
            self.assertEqual(status, 403, (method, tail))

    def test_crud_through_http(self) -> None:
        status, created = self.call(
            "POST",
            self.path("/records"),
            {"scope_type": GROUP_SCOPE[0], "scope_id": GROUP_SCOPE[1], "content": "群主不吃香菜"},
        )
        self.assertEqual(status, 200)
        record_id = created["record"]["id"]
        status, listed = self.call("GET", self.path("/records") + self.scope_query())
        self.assertEqual(status, 200)
        self.assertEqual(listed["total"], 1)
        status, fetched = self.call(
            "GET", self.path("/records/%d" % record_id) + self.scope_query()
        )
        self.assertEqual(status, 200)
        status, updated = self.call(
            "PATCH",
            self.path("/records/%d" % record_id),
            {"scope_type": GROUP_SCOPE[0], "scope_id": GROUP_SCOPE[1], "confidence": 0.3},
        )
        self.assertEqual(status, 200)
        self.assertAlmostEqual(updated["record"]["confidence"], 0.3)
        status, _ = self.call(
            "GET", self.path("/records/%d" % record_id) + self.scope_query(OTHER_GROUP_SCOPE)
        )
        self.assertEqual(status, 404)
        status, _ = self.call(
            "DELETE",
            self.path("/records/%d" % record_id) + self.scope_query(),
            {"scope_type": GROUP_SCOPE[0], "scope_id": GROUP_SCOPE[1]},
        )
        self.assertEqual(status, 400)
        status, deleted = self.call(
            "DELETE",
            self.path("/records/%d" % record_id) + self.scope_query(),
            {"scope_type": GROUP_SCOPE[0], "scope_id": GROUP_SCOPE[1], "confirm": str(record_id)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(deleted["deleted"], record_id)

    def test_scope_isolation_between_http_calls(self) -> None:
        self.call(
            "POST", self.path("/records"),
            {"scope_type": PRIVATE_SCOPE[0], "scope_id": PRIVATE_SCOPE[1], "content": "私聊记忆"},
        )
        status, listed = self.call("GET", self.path("/records") + self.scope_query())
        self.assertEqual(status, 200)
        self.assertEqual(listed["total"], 0)
        status, stats = self.call("GET", self.path("/stats") + self.scope_query())
        self.assertEqual(stats["stats"]["total"], 0)
        status, private = self.call(
            "GET", self.path("/records") + self.scope_query(PRIVATE_SCOPE)
        )
        self.assertEqual(private["total"], 1)
        self.assertNotIn("qq:838796241", json.dumps(private))
        missing_scope = self.call("GET", self.path("/records"))[0]
        self.assertEqual(missing_scope, 400)

    def test_forget_export_import_reflect_through_http(self) -> None:
        self.call(
            "POST", self.path("/records"),
            {"scope_type": GROUP_SCOPE[0], "scope_id": GROUP_SCOPE[1], "content": "导出我"},
        )
        status, exported = self.call(
            "POST", self.path("/export"),
            {"scope_type": GROUP_SCOPE[0], "scope_id": GROUP_SCOPE[1]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(exported["count"], 1)
        self.assertNotIn("astrbot_memory.db", json.dumps(exported))
        status, preview = self.call(
            "POST", self.path("/import"),
            {"scope_type": "group", "scope_id": OTHER_GROUP_SCOPE[1], "records": exported["records"]},
        )
        self.assertEqual(status, 200)
        self.assertFalse(preview["apply"])
        self.assertEqual(preview["preview"]["would_create"], 1)
        status, applied = self.call(
            "POST", self.path("/import"),
            {
                "scope_type": "group",
                "scope_id": OTHER_GROUP_SCOPE[1],
                "records": exported["records"],
                "apply": True,
                "confirm": OTHER_GROUP_SCOPE[1],
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("backup", applied)
        status, reflect = self.call(
            "POST", self.path("/reflect"),
            {"scope_type": "group", "scope_id": OTHER_GROUP_SCOPE[1]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(reflect["preview"]["counts"]["records"], 1)
        status, forged = self.call(
            "POST", self.path("/reflect"),
            {"scope_type": "group", "scope_id": OTHER_GROUP_SCOPE[1], "apply": True, "confirm": "wrong"},
        )
        self.assertEqual(status, 400)
        self.assertTrue(forged["ok"] is False)
        status, forgotten = self.call(
            "POST", self.path("/forget"),
            {"scope_type": "group", "scope_id": OTHER_GROUP_SCOPE[1], "confirm": OTHER_GROUP_SCOPE[1]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(forgotten["deleted"], 1)
        # The original scope still owns its record.
        status, stats = self.call("GET", self.path("/stats") + self.scope_query())
        self.assertEqual(stats["stats"]["total"], 1)

    def test_unavailable_database_is_reported_without_paths(self) -> None:
        self.db_path.write_bytes(b"definitely not sqlite")
        status, payload = self.call("GET", self.path("/stats") + self.scope_query())
        self.assertEqual(status, 503)
        self.assertFalse(payload["available"])
        self.assertTrue(payload["corrupt"])
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(str(self.data_dir), text)
        self.assertNotIn("astrbot_memory.db", text)

    def test_nested_delete_never_deletes_the_instance(self) -> None:
        status, _ = self.call("DELETE", self.path("/nope"), {})
        self.assertEqual(status, 404)
        status, instances = self.call("GET", "/api/instances")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in instances["instances"]], [self.instance_id])

    def test_unknown_memory_action_and_methods(self) -> None:
        status, _ = self.call("GET", self.path("/nope") + self.scope_query())
        self.assertEqual(status, 404)
        status, _ = self.call("PATCH", "/api/health", {"x": 1})
        self.assertEqual(status, 405)
        status, _ = self.call("PUT", self.path("/records/1"), {})
        self.assertEqual(status, 405)


class ConsolePanelStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = CONSOLE_JS.read_text(encoding="utf-8")
        cls.css = CONSOLE_CSS.read_text(encoding="utf-8")

    def test_panel_is_wired_into_instance_settings(self) -> None:
        for token in (
            "buildMemoryContentSection",
            "buildMemoryContentSection(instance)",
            "loadMemoryContentScope",
            "memoryContentPreviewImport",
            "memoryContentReflect",
            "memoryContentExport",
            "memoryContentForget",
            "memoryContentDelete",
        ):
            self.assertIn(token, self.js)
        self.assertIn("buildMemoryPolicySection(instance),\n      buildMemoryContentSection(instance),", self.js)

    def test_panel_calls_only_admin_endpoints(self) -> None:
        for tail in ("'stats'", "'records'", "'forget'", "'export'", "'import'", "'reflect'"):
            self.assertIn(tail, self.js)
        self.assertIn("/memory/${tail}", self.js)
        self.assertNotIn("api/instances/${instance.id}/memory-policy", self.js.replace("memory-content", ""))

    def test_panel_has_scope_search_pagination_and_confirmations(self) -> None:
        for token in (
            "MEMORY_CONTENT_SCOPES",
            "scope_type",
            "scope_id",
            "query",
            "MEMORY_CONTENT_PAGE_SIZE",
            "上一页",
            "下一页",
            "再次点击确认删除",
            "再次点击确认忘记整个 scope",
            "再次点击确认写入",
            "再次点击确认整理",
            "记忆内容",
        ):
            self.assertIn(token, self.js)
        self.assertIn("导出当前 scope", self.js)
        self.assertIn("整理预览", self.js)
        self.assertIn("去重策略", self.js)

    def test_panel_has_explicit_empty_loading_error_states(self) -> None:
        for token in (
            "正在读取记忆…",
            "该 scope 还没有记忆。",
            "记忆数据库当前不可用。",
            "没有匹配",
            "is-unavailable",
            "available === false",
        ):
            self.assertIn(token, self.js)

    def test_panel_stays_browser_safe(self) -> None:
        for forbidden in ("innerHTML", "localStorage", "sessionStorage", "document.write"):
            self.assertNotIn(forbidden, self.js)
        self.assertNotRegex(self.js, r"\balert\s*\(|\bconfirm\s*\(")
        self.assertIn("textContent", self.js)

    def test_panel_styles_exist_and_css_is_clean(self) -> None:
        for selector in (
            ".memory-content-section",
            ".memory-content-controls",
            ".memory-content-record",
            ".memory-content-editor",
            ".memory-content-error",
            ".memory-content-record.is-selected",
            "@media (max-width: 700px)",
        ):
            self.assertIn(selector, self.css)
        clean = re.sub(r"/\*.*?\*/", "", self.css, flags=re.DOTALL)
        self.assertEqual(clean.count("{"), clean.count("}"))
        self.assertNotIn("url(", clean)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_console_javascript_syntax(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "console.js"
            target.write_text(self.js, encoding="utf-8")
            subprocess.run(["node", "--check", str(target)], check=True)


class NoDatabaseAdminTests(TempDbTestCase):
    def test_empty_scope_returns_zero_stats_and_empty_list(self) -> None:
        make_db(self.data_dir)
        admin = self.admin()
        self.assertEqual(admin.stats(*PRIVATE_SCOPE)["stats"]["total"], 0)
        self.assertEqual(admin.list_records(*PRIVATE_SCOPE)["records"], [])
        self.assertEqual(admin.export_scope(*PRIVATE_SCOPE)["count"], 0)
        self.assertEqual(admin.reflect(*PRIVATE_SCOPE)["preview"]["counts"]["records"], 0)


if __name__ == "__main__":
    unittest.main()
