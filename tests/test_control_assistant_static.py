"""Static regression checks for the console and the standalone AI assistant page.

These tests do not start a browser. They verify the static routes, DOM contracts,
API hooks, self-contained assets, responsive CSS, and browser-safety boundaries.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "qqbot_studio" / "static"
CONSOLE_HTML = STATIC / "console.html"
CONSOLE_CSS = STATIC / "console.css"
CONSOLE_JS = STATIC / "console.js"
ASSISTANT_HTML = STATIC / "assistant.html"

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _StaticHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.external_assets: list[str] = []
        self.script_sources: list[str] = []
        self.stylesheet_links: list[str] = []
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag not in VOID_TAGS:
            self._stack.append(tag)
        for key in ("src", "href"):
            value = str(values.get(key) or "")
            if value.startswith(("http://", "https://", "//")):
                self.external_assets.append(value)
        if tag == "script" and values.get("src"):
            self.script_sources.append(str(values["src"]))
        if tag == "link" and "stylesheet" in str(values.get("rel") or "").lower():
            self.stylesheet_links.append(str(values.get("href") or ""))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if tag == "link" and "stylesheet" in str(values.get("rel") or "").lower():
            self.stylesheet_links.append(str(values.get("href") or ""))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self._stack or self._stack[-1] != tag:
            raise AssertionError(f"unbalanced closing tag </{tag}> at {self.getpos()}")
        self._stack.pop()


def _parse_html(text: str) -> _StaticHTMLParser:
    parser = _StaticHTMLParser()
    parser.feed(text)
    if parser._stack:
        raise AssertionError(f"unclosed HTML tags: {parser._stack[-5:]}")
    return parser


def _inline_scripts(text: str) -> list[str]:
    return re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", text, flags=re.IGNORECASE)


def _inline_styles(text: str) -> list[str]:
    return re.findall(r"<style(?:\s[^>]*)?>([\s\S]*?)</style>", text, flags=re.IGNORECASE)


def _assert_css_structurally_clean(testcase: unittest.TestCase, css: str, label: str) -> None:
    clean = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(clean):
        if char in "({[":
            stack.append(char)
        elif char in ")}]":
            testcase.assertTrue(stack, f"{label}: unexpected {char} at {index}")
            testcase.assertEqual(stack.pop(), pairs[char], f"{label}: mismatched {char} at {index}")
    testcase.assertFalse(stack, f"{label}: unclosed CSS delimiters {stack[-5:]}")
    testcase.assertNotRegex(clean, r"url\s*\(")
    testcase.assertNotRegex(clean, r"@import\b")


class ControlAssistantStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.console_html = CONSOLE_HTML.read_text(encoding="utf-8")
        cls.console_css = CONSOLE_CSS.read_text(encoding="utf-8")
        cls.console_js = CONSOLE_JS.read_text(encoding="utf-8")
        cls.assistant_html = ASSISTANT_HTML.read_text(encoding="utf-8")
        cls.console_parser = _parse_html(cls.console_html)
        cls.assistant_parser = _parse_html(cls.assistant_html)
        cls.assistant_scripts = _inline_scripts(cls.assistant_html)
        cls.assistant_css = "\n".join(_inline_styles(cls.assistant_html))
        cls.assistant_js = cls.assistant_scripts[-1] if cls.assistant_scripts else ""

    def test_console_ids_unique_and_js_id_references_exist(self) -> None:
        ids = self.console_parser.ids
        self.assertEqual(sorted({item for item in ids if ids.count(item) > 1}), [])
        refs = set(re.findall(r"(?<!\$)\$\('([^']+)'\)", self.console_js))
        self.assertTrue(refs)
        self.assertEqual(sorted(refs - set(ids)), [])

    def test_console_removed_offline_assistant_and_b_card(self) -> None:
        all_console = self.console_html + self.console_js + self.console_css
        for old_id in (
            "assistant-open", "assistant-panel", "assistant-close", "assistant-form",
            "assistant-b-card", "assistant-b-config-panel", "assistant-b-install",
        ):
            self.assertNotIn(old_id, all_console)
        for old_js in ("ASSISTANT_KB", "assistantPanelOpen", "loadAssistantBStatus", "handleAssistantBConfigSubmit"):
            self.assertNotIn(old_js, self.console_js)
        for old_css in (".assistant-panel", ".assistant-quick", ".assistant-message", ".assistant-b-"):
            self.assertNotIn(old_css, self.console_css)
        self.assertNotIn("assistant-b-", all_console)
        self.assertNotIn("使用助手", all_console)
        self.assertNotIn("AI 助手 B", self.console_html)

    def test_console_header_links_to_standalone_assistant_page(self) -> None:
        self.assertRegex(
            self.console_html,
            r'<a[^>]+href="/assistant"[^>]*>[\s\S]{0,260}?AI 助手',
        )
        self.assertNotIn("/assistant.html", self.console_html)

    def test_console_preserves_instances_v3_and_thirty_minute_refresh(self) -> None:
        required = {
            "console-refresh", "console-update", "console-error-retry", "create-instance-form",
            "instances-grid", "console-resource-summary",
        }
        self.assertTrue(required.issubset(set(self.console_parser.ids)))
        self.assertIn("AUTO_REFRESH_INTERVAL_MS = 30 * 60 * 1000", self.console_js)
        self.assertIn("function scheduleNextSync(delay = AUTO_REFRESH_INTERVAL_MS)", self.console_js)
        self.assertIn("async function manualRefresh()", self.console_js)
        self.assertIn("自动刷新：每 30 分钟", self.console_js)
        self.assertIn("手动刷新", self.console_html)
        for token in ("/api/instances", "/api/update/check", "/api/update/apply", "memory_mode", "plugin"):
            self.assertIn(token, self.console_js)
        self.assertIn(".instance-card { padding: 26px; }", self.console_css)
        self.assertIn("@media (max-width: 760px)", self.console_css)
        self.assertIn(".console-assistant-link", self.console_css)

    def test_console_browser_safety_and_css_cleanliness(self) -> None:
        for forbidden in ("innerHTML", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, self.console_js)
        self.assertNotRegex(self.console_js, r"\balert\s*\(|\bconfirm\s*\(")
        self.assertFalse(self.console_parser.external_assets)
        self.assertEqual(self.console_parser.script_sources, ["console.js"])
        _assert_css_structurally_clean(self, self.console_css, "console.css")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_javascript_syntax_for_console_and_assistant_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            console_path = Path(temp_dir) / "console.js"
            console_path.write_text(self.console_js, encoding="utf-8")
            subprocess.run(["node", "--check", str(console_path)], check=True)
            for index, script in enumerate(self.assistant_scripts):
                script_path = Path(temp_dir) / f"assistant-inline-{index}.js"
                script_path.write_text(script, encoding="utf-8")
                subprocess.run(["node", "--check", str(script_path)], check=True)

    def test_assistant_page_self_contained_and_token_placeholder(self) -> None:
        self.assertEqual(self.assistant_parser.external_assets, [])
        self.assertEqual(self.assistant_parser.script_sources, [])
        self.assertEqual(self.assistant_parser.stylesheet_links, [])
        self.assertGreaterEqual(len(self.assistant_scripts), 2)
        self.assertIn('window.QB_TOKEN = "{{TOKEN}}";', self.assistant_html)
        self.assertIn('aria-label="AI 助手页面"', self.assistant_html)
        self.assertIn('href="/console"', self.assistant_html)
        self.assertIn("X-QB-Token", self.assistant_js)
        self.assertNotIn("console.js", self.assistant_html)
        self.assertNotIn("使用助手", self.assistant_html)

    def test_assistant_page_required_management_controls(self) -> None:
        required = {
            "main-status", "main-badge", "main-notice", "install-card", "install-button",
            "install-label", "update-button", "install-meta", "runtime-card", "configured-state",
            "running-state", "host-state", "port-state", "runtime-url", "runtime-note",
            "open-button", "start-button", "stop-button", "config-card", "config-form",
            "key-source-independent", "key-source-instance", "api-key", "toggle-key",
            "instance-select", "port", "host-local", "host-public", "public-host",
            "public-host-fields", "public-host-error", "form-error", "form-status",
            "save-button", "unsupported-card", "refresh-status",
        }
        ids = self.assistant_parser.ids
        self.assertTrue(required.issubset(set(ids)))
        self.assertEqual(sorted({item for item in ids if ids.count(item) > 1}), [])
        refs = set(re.findall(r"(?<!\$)\$\('([^']+)'\)", self.assistant_js))
        self.assertTrue(refs)
        self.assertEqual(sorted(refs - set(ids)), [])
        self.assertIn('id="api-key" type="password"', self.assistant_html)
        self.assertIn('autocomplete="off"', self.assistant_html)
        self.assertIn('min="5000"', self.assistant_html)
        self.assertIn('max="10000"', self.assistant_html)
        self.assertIn('value="8766"', self.assistant_html)

    def test_assistant_page_install_update_config_start_stop_contract(self) -> None:
        for endpoint in (
            "/api/assistant/status",
            "/api/assistant/install",
            "/api/assistant/update",
            "/api/assistant/config",
            "/api/assistant/start",
            "/api/assistant/stop",
            "/api/instances",
        ):
            self.assertIn(endpoint, self.assistant_js)
        self.assertIn("installArmed", self.assistant_js)
        self.assertIn("再次点击确认安装", self.assistant_js)
        self.assertIn("state.installArmed = true", self.assistant_js)
        self.assertIn("method: 'POST', body: {}", self.assistant_js)
        self.assertIn("key_source", self.assistant_js)
        self.assertIn("instance_id", self.assistant_js)
        self.assertIn("public_host", self.assistant_js)
        self.assertIn("host: publicMode ? '0.0.0.0' : '127.0.0.1'", self.assistant_js)
        self.assertIn("public_host: publicMode ? publicHost : undefined", self.assistant_js)
        self.assertIn("ssh_tunnel", self.assistant_js)
        self.assertIn("access_note", self.assistant_js)
        self.assertIn("B 没有登录认证", self.assistant_html + self.assistant_js)
        for term in ("SSH", "安全组", "防火墙", "VPN", "不要", "公网"):
            self.assertIn(term, self.assistant_html + self.assistant_js)

    def test_assistant_page_key_never_persisted_or_rendered(self) -> None:
        self.assertIn("api_key", self.assistant_js)
        self.assertNotRegex(self.assistant_html + self.assistant_js, r"sk-[A-Za-z0-9_-]{8,}")
        for forbidden in ("innerHTML", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, self.assistant_js)
        self.assertNotRegex(self.assistant_js, r"\balert\s*\(|\bconfirm\s*\(")
        self.assertNotIn("state.config.apiKey", self.assistant_js)
        self.assertNotIn("state.config.api_key", self.assistant_js)
        self.assertIn("$('api-key').value = ''", self.assistant_js)
        self.assertIn("type=\"password\"", self.assistant_html)

    def test_assistant_page_css_is_balanced_and_responsive(self) -> None:
        self.assertTrue(self.assistant_css)
        _assert_css_structurally_clean(self, self.assistant_css, "assistant inline CSS")
        self.assertIn("@media (max-width: 760px)", self.assistant_css)
        self.assertIn("@media (max-width: 480px)", self.assistant_css)
        self.assertIn("[hidden] { display: none !important; }", self.assistant_css)


if __name__ == "__main__":
    unittest.main()
