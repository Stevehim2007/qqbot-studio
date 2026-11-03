"""Static regression checks for the update channels / rollback console UI."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "qqbot_studio" / "static"
HTML = (STATIC / "console.html").read_text(encoding="utf-8")
CSS = (STATIC / "console.css").read_text(encoding="utf-8")
JS = (STATIC / "console.js").read_text(encoding="utf-8")


class UpdateUiStaticTests(unittest.TestCase):
    def test_console_has_update_panel_markup(self):
        required = {
            "console-update",
            "update-overlay",
            "update-modal",
            "update-title",
            "update-current-version",
            "update-current-commit",
            "update-current-major",
            "update-current-dirty",
            "update-tab-stable",
            "update-tab-test",
            "update-stable-pane",
            "update-test-pane",
            "update-apply-stable",
            "update-versions",
            "update-test-refs",
            "update-history-list",
            "update-confirm",
            "update-confirm-ok",
            "update-warning",
            "update-error",
            "update-status",
        }
        ids = set(re.findall(r'id="([^"]+)"', HTML))
        self.assertTrue(required.issubset(ids), sorted(required - ids))
        self.assertIn("稳定版", HTML)
        self.assertIn("测试版", HTML)
        self.assertIn("跨大版本", HTML)

    def test_javascript_wires_channels_rollback_and_history(self):
        for token in (
            "/api/update/status",
            "/api/update/versions",
            "/api/update/history",
            "/api/update/apply",
            "/api/update/rollback",
            "channel: 'test'",
            "channel: 'stable'",
            "test_ref:",
            "allow_schema_downgrade",
            "switchUpdateTab",
            "armUpdateAction",
        ):
            self.assertTrue(token in JS, token)
        for channel_label in ("稳定版", "测试版", "回滚"):
            self.assertTrue(channel_label in JS, channel_label)

    def test_browser_safety_boundaries(self):
        for forbidden in ("innerHTML", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, JS)
        self.assertNotRegex(JS, r"\balert\s*\(|\bconfirm\s*\(")

    def test_css_styles_the_panel_and_mobile(self):
        for selector in (
            ".update-overlay",
            ".update-modal",
            ".update-tabs",
            ".update-tab.is-active",
            ".update-versions",
            ".update-history-item",
            ".update-confirm",
            ".update-warning",
        ):
            self.assertIn(selector, CSS)
        self.assertIn(".update-overlay[hidden]", CSS)
        self.assertIn("@media (max-width: 560px)", CSS)


if __name__ == "__main__":
    unittest.main()
