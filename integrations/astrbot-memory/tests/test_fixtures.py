"""Fixture-driven extraction compatibility: pinned expectations per turn."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from _support import MemoryTestCase

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "turns_zh.json"


class FixtureExtractionTests(MemoryTestCase):
    def setUp(self):
        super().setUp()
        self.data = json.loads(FIXTURES.read_text(encoding="utf-8"))

    def _context(self, turn):
        return self.memory.MemoryContext(
            platform="aiocqhttp",
            user_id="1001",
            group_id=turn.get("group_id", ""),
            is_private=turn.get("private", True),
            conversation_id="conv-1",
        )

    def test_fixtures_match_the_extractor(self):
        from astrbot.core.memory.extract import heuristic_extract

        for turn in self.data["turns"]:
            with self.subTest(turn["id"]):
                candidates = heuristic_extract(turn["text"], self._context(turn))
                expected = turn["expect"]
                self.assertEqual(len(candidates), len(expected), turn["text"])
                for candidate, want in zip(candidates, expected):
                    self.assertEqual(candidate.key, want["key"])
                    self.assertEqual(candidate.content, want["content"])
                    self.assertEqual(candidate.scope_type, want["scope_type"])
                    self.assertGreaterEqual(candidate.confidence, want["min_confidence"])

    def test_fixture_turns_round_trip_through_the_store(self):
        """Every non-empty fixture turn is stored once, and replaying is idempotent."""

        from astrbot.core.memory.extract import heuristic_extract

        service = self.make_service()
        expected_rows = 0
        for turn in self.data["turns"]:
            if not turn["expect"]:
                continue
            service.store_candidates(heuristic_extract(turn["text"], self._context(turn)))
            expected_rows += len(turn["expect"])
        self.assertEqual(len(service.export_all()), expected_rows)

        for turn in self.data["turns"]:
            if not turn["expect"]:
                continue
            service.store_candidates(heuristic_extract(turn["text"], self._context(turn)))
        self.assertEqual(len(service.export_all()), expected_rows)


if __name__ == "__main__":
    unittest.main()
