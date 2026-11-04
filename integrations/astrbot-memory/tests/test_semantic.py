"""Optional semantic retrieval: embeddings, fusion, isolation, fail-open.

BM25/FTS5 stays the baseline; the vector layer is opt-in and every failure
falls back to it.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from _support import AsyncMemoryTestCase, MemoryTestCase, group_event, private_event


class FixedProvider:
    """Deterministic 3-dim provider: every text gets the same vector."""

    model = "fixed-3"
    dim = 3

    def __init__(self, vector=(1.0, 0.0, 0.0), raise_exc=None):
        self.vector = list(vector)
        self.raise_exc = raise_exc
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.raise_exc is not None:
            raise self.raise_exc
        return [list(self.vector) for _ in texts]


class SemanticSettingsTests(MemoryTestCase):
    def test_defaults_are_disabled(self):
        settings, errors = self.memory.EmbeddingSettings.from_mapping({})
        self.assertFalse(settings.enable)
        self.assertEqual(settings.provider, "local")
        self.assertEqual(errors, [])

    def test_invalid_semantic_config_is_reported(self):
        settings, errors = self.memory.EmbeddingSettings.from_mapping(
            {"enable": True, "provider": "magic", "weight": 5, "dim": 2}
        )
        self.assertEqual(settings.provider, "local")
        self.assertEqual(settings.weight, 1.0)
        self.assertEqual(settings.dim, 8)
        self.assertTrue(errors)

    def test_local_provider_is_deterministic_and_normalised(self):
        provider = self.memory.LocalHashEmbeddingProvider(dim=32)
        first = provider.embed(["用户喜欢麻辣火锅"])[0]
        second = provider.embed(["用户喜欢麻辣火锅"])[0]
        self.assertEqual(first, second)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0, places=6)
        other = provider.embed(["用户住在杭州"])[0]
        self.assertNotEqual(first, other)

    def test_cosine_bounds(self):
        cosine = self.memory.cosine
        self.assertAlmostEqual(cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertEqual(cosine([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertEqual(cosine([], [1.0]), 0.0)
        self.assertEqual(cosine([1.0], [1.0, 2.0]), 0.0)

    def test_http_provider_parses_openai_payload(self):
        captured = {}

        def transport(url, headers, body, timeout):
            captured["url"] = url
            captured["payload"] = json.loads(body.decode("utf-8"))
            return 200, json.dumps(
                {"data": [{"embedding": [0.1, 0.2, 0.3]}, {"embedding": [0.3, 0.2, 0.1]}]}
            ).encode()

        settings = self.memory.EmbeddingSettings(
            enable=True, provider="http", endpoint="http://stub.invalid/v1/embeddings",
            model="stub-embed", dim=3,
        )
        provider = self.memory.HttpEmbeddingProvider(settings, transport=transport)
        vectors = provider.embed(["a", "b"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(captured["url"], settings.endpoint)
        self.assertEqual(captured["payload"]["model"], "stub-embed")
        self.assertEqual(captured["payload"]["input"], ["a", "b"])

    def test_http_provider_fails_loudly_on_bad_payload(self):
        def transport(url, headers, body, timeout):
            return 200, b'{"data": []}'

        settings = self.memory.EmbeddingSettings(
            enable=True, provider="http", endpoint="http://stub.invalid", dim=3
        )
        provider = self.memory.HttpEmbeddingProvider(settings, transport=transport)
        with self.assertRaises(RuntimeError):
            provider.embed(["a"])


class SemanticSearchTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.service = self.make_service()

    def _store(self, scope, key, content):
        record = self.service.add(scope[0], scope[1], key, content)[1]
        self.assertIsNotNone(record)
        return record

    def test_semantic_fusion_can_surface_a_row_without_lexical_overlap(self):
        scope = ("user", "aiocqhttp:1001")
        unrelated = self._store(scope, "n1", "完全无关的内容")
        self._store(scope, "n2", "另一条记录")
        self.assertTrue(self.service.store.put_embedding(unrelated.id, "fixed-3", [1.0, 0.0, 0.0]))

        # No lexical overlap with the query at all -> BM25 would drop it.
        self.assertEqual(self.service.store.search([scope], "zzz", limit=5), [])

        results = self.service.store.search(
            [scope],
            "zzz",
            query_vector=[1.0, 0.0, 0.0],
            semantic_model="fixed-3",
            semantic_weight=0.5,
            semantic_min_cosine=0.2,
        )
        self.assertEqual([record.id for record in results], [unrelated.id])

    def test_scope_filter_happens_before_scoring(self):
        own = ("group", "aiocqhttp:2001")
        other = ("group", "aiocqhttp:2002")
        mine = self._store(own, "k1", "本群事实")
        foreign = self._store(other, "k1", "别群事实")
        for record in (mine, foreign):
            self.service.store.put_embedding(record.id, "fixed-3", [1.0, 0.0, 0.0])

        results = self.service.store.search(
            [own],
            "zzz",
            query_vector=[1.0, 0.0, 0.0],
            semantic_model="fixed-3",
            semantic_weight=0.5,
            semantic_min_cosine=0.2,
        )
        self.assertEqual([record.id for record in results], [mine.id])
        self.assertNotIn(foreign.id, [record.id for record in results])

    def test_backfill_embeds_missing_rows(self):
        scope = ("user", "aiocqhttp:1001")
        service = self.make_service(
            semantic={
                "enable": True,
                "provider": "local",
                "dim": 32,
                "batch": 1,
            }
        )
        # Insert with the semantic layer switched off, then reload a semantic
        # service over the same DB to exercise the historical backfill path.
        plain = self.make_service()
        plain.add(scope[0], scope[1], "k1", "用户喜欢火锅")
        plain.add(scope[0], scope[1], "k2", "用户住在杭州")
        first = service.backfill_semantic([scope], limit=1)
        self.assertEqual((first["scanned"], first["embedded"]), (1, 1))
        second = service.backfill_semantic([scope], limit=10)
        self.assertEqual(second["embedded"], 1)
        stats = service.semantic_stats()
        self.assertTrue(stats["enabled"])
        self.assertEqual(stats["embedded"], 2)
        self.assertEqual(stats["provider"], "local")

    def test_semantic_search_uses_the_configured_provider_end_to_end(self):
        service = self.make_service(
            semantic={"enable": True, "provider": "local", "dim": 32, "weight": 0.5}
        )
        service.add("user", "aiocqhttp:1001", "k1", "用户喜欢火锅")
        self.assertEqual(service.semantic_stats()["embedded"], 1)
        block, records = service.retrieve(self.context(private_event("1001")), "火锅")
        self.assertTrue(block)
        self.assertEqual(len(records), 1)
        self.assertTrue(service.semantic_stats()["embedded"] >= 1)

    def test_provider_failure_falls_back_to_lexical(self):
        service = self.make_service(
            semantic={"enable": True, "provider": "local", "dim": 32}
        )
        service.add("user", "aiocqhttp:1001", "k1", "用户喜欢火锅")
        service._semantic_provider = FixedProvider(raise_exc=RuntimeError("down"))
        block, records = service.retrieve(self.context(private_event("1001")), "火锅")
        self.assertTrue(block, "向量失败时必须回退 BM25")
        self.assertEqual(len(records), 1)
        self.assertIn("语义检索失败", service.last_error)

    def test_auto_backfill_embeds_before_scoring(self):
        plain = self.make_service()
        plain.add("user", "aiocqhttp:1001", "k1", "用户喜欢火锅")
        service = self.make_service(
            semantic={"enable": True, "provider": "local", "dim": 32, "auto_backfill": True, "batch": 2}
        )
        self.assertEqual(service.semantic_stats()["embedded"], 0)
        block, records = service.retrieve(self.context(private_event("1001")), "火锅")
        self.assertTrue(block)
        self.assertEqual(service.semantic_stats()["embedded"], 1, "auto_backfill 应在检索前补齐向量")

    def test_backfill_refuses_when_disabled(self):
        result = self.service.backfill_semantic()
        self.assertFalse(result["ok"])

    def test_write_path_keeps_embeddings_in_sync_when_enabled(self):
        service = self.make_service(
            semantic={"enable": True, "provider": "local", "dim": 32}
        )
        record = service.add("user", "aiocqhttp:1001", "k1", "用户喜欢火锅")[1]
        rows = service.store.export("user", "aiocqhttp:1001")
        self.assertEqual(len(rows), 1)
        stats = service.store.embedding_stats()
        self.assertEqual(stats["embedded"], 1)
        self.assertEqual(stats["records"], 1)
        self.assertIsNotNone(record)


class SemanticMigrationTests(MemoryTestCase):
    def test_old_database_gets_sender_column_and_embedding_table(self):
        path = Path(self._tmp.name) / "old.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE astrbot_memory (
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
            """
        )
        connection.execute(
            "INSERT INTO astrbot_memory (scope_type, scope_id, mem_key, content, fingerprint,"
            " created_at, updated_at) VALUES ('group','aiocqhttp:2001','k','旧数据','fp',1,1)"
        )
        connection.commit()
        connection.close()

        store = self.memory.MemoryStore(path, enable_fts=False)
        self.addCleanup(store.close)
        records = store.list_scope("group", "aiocqhttp:2001")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].sender_id, "")
        self.assertEqual(store.embedding_stats()["records"], 1)
        self.assertTrue(store.put_embedding(records[0].id, "local", [0.5, 0.5]))

    def test_corrupt_embedding_blob_is_ignored(self):
        scope = ("user", "aiocqhttp:1001")
        service = self.make_service()
        record = service.add(scope[0], scope[1], "k", "用户喜欢火锅")[1]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO astrbot_memory_embeddings"
                " (memory_id, model, dim, vector, updated_at) VALUES (?,?,?,?,?)",
                (record.id, "broken", 4, b"\x00", 0.0),
            )
        results = service.store.search(
            [scope],
            "火锅",
            query_vector=[1.0, 0.0, 0.0, 0.0],
            semantic_model="broken",
            semantic_weight=0.5,
        )
        self.assertEqual(len(results), 1, "坏 BLOB 只能被忽略，不能中断检索")


if __name__ == "__main__":
    unittest.main()
