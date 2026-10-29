"""Static regression checks for the offline console assistant.

These tests intentionally do not start the server or a browser. They only verify
the static HTML/CSS/JS contract, wording coverage, and browser-safety rules.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "qqbot_studio" / "static"
HTML_PATH = STATIC / "console.html"
CSS_PATH = STATIC / "console.css"
JS_PATH = STATIC / "console.js"

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _ConsoleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.external_assets: list[str] = []
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

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self._stack or self._stack[-1] != tag:
            raise AssertionError(f"unbalanced closing tag </{tag}> at {self.getpos()}")
        self._stack.pop()


class ControlAssistantStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.js = JS_PATH.read_text(encoding="utf-8")
        parser = _ConsoleHTMLParser()
        parser.feed(cls.html)
        if parser._stack:
            raise AssertionError(f"unclosed HTML tags: {parser._stack[-5:]}")
        cls.ids = parser.ids
        cls.external_assets = parser.external_assets

    def test_console_ids_are_unique_and_js_references_exist(self) -> None:
        duplicates = sorted({item for item in self.ids if self.ids.count(item) > 1})
        self.assertEqual(duplicates, [])
        refs = set(re.findall(r"(?<!\$)\$\('([^']+)'\)", self.js))
        self.assertTrue(refs)
        self.assertEqual(sorted(refs - set(self.ids)), [])

    def test_assistant_ui_is_present_and_accessible(self) -> None:
        required_ids = {
            "assistant-open",
            "assistant-panel",
            "assistant-close",
            "assistant-messages",
            "assistant-quick-title",
            "assistant-form",
            "assistant-input",
            "assistant-send",
            "assistant-error",
            "assistant-hint",
        }
        self.assertTrue(required_ids.issubset(set(self.ids)))
        self.assertIn('role="dialog"', self.html)
        self.assertIn('aria-labelledby="assistant-title"', self.html)
        self.assertIn('aria-controls="assistant-panel"', self.html)
        self.assertIn('aria-expanded="false"', self.html)
        self.assertIn('role="log"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn("使用助手", self.html)

    def test_quick_questions_cover_required_topics(self) -> None:
        questions = re.findall(r'data-assistant-question="([^"]+)"', self.html)
        self.assertGreaterEqual(len(questions), 6)
        self.assertLessEqual(len(questions), 10)
        joined = "\n".join(questions)
        for phrase in (
            "安装",
            "更新",
            "腾讯云",
            "端口",
            "start.sh",
            "console.sh",
            "扫码",
            "SnowLuma",
            "reply_probability",
            "memory_mode",
            "token",
            "部署中断",
            "日志",
            "Docker",
            "Windows",
        ):
            self.assertIn(phrase, joined)

    def test_answer_knowledge_coverage(self) -> None:
        required_terms = (
            "install.sh",
            "腾讯云",
            "安全组",
            "防火墙",
            "8765",
            "start.sh",
            "console.sh",
            "每个新实例",
            "登录态",
            "noVNC",
            "设备验证",
            "短信验证",
            "滑块",
            "SnowLuma",
            "admin",
            "重置密码",
            "webui.json",
            "reply_probability",
            "group_cooldown_seconds",
            "user_cooldown_seconds",
            "memory_mode",
            "astrbot",
            "token",
            "自动更新",
            "更新失败",
            "部署中断",
            "查看日志",
            "docker info",
            "Docker",
            "Windows",
            "未完成真实 Windows",
            "README.md",
            "docs/server-troubleshooting.md",
        )
        for term in required_terms:
            self.assertIn(term, self.js, term)

    def test_fallback_guides_to_readme_and_troubleshooting(self) -> None:
        fallback_start = self.js.index("const ASSISTANT_FALLBACK =")
        fallback = self.js[fallback_start:]
        self.assertIn("README.md", fallback)
        self.assertIn("docs/server-troubleshooting.md", fallback)
        self.assertIn("没有匹配到内置答案", fallback)

    def test_assistant_uses_dom_text_and_no_browser_storage(self) -> None:
        for forbidden in ("innerHTML", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, self.js)
            self.assertNotIn(forbidden, self.html)
        self.assertNotRegex(self.js, r"\balert\s*\(|\bconfirm\s*\(")
        self.assertIn("assistant-code-block", self.js)
        self.assertIn("textContent", self.js)
        self.assertIn("data-assistant-question", self.html)
        self.assertIn("assistantOpen.addEventListener('click'", self.js)
        self.assertIn("assistantClose.addEventListener('click'", self.js)
        self.assertIn("assistantForm.addEventListener('submit'", self.js)
        self.assertIn("event.key === 'Escape'", self.js)

    def test_assistant_knowledge_and_matching_are_offline(self) -> None:
        start = self.js.index("const ASSISTANT_KB =")
        end = self.js.index("function sanitiseAssistantBText")
        assistant_block = self.js[start:end]
        for forbidden in ("fetch(", "apiRequest(", "eval(", "new Function"):
            self.assertNotIn(forbidden, assistant_block)
        self.assertIn("findAssistantAnswer", assistant_block)
        self.assertIn("assistantEntryScore", assistant_block)
        self.assertIn("ASSISTANT_FALLBACK", assistant_block)

    def test_no_external_cdn_or_css_assets(self) -> None:
        self.assertEqual(self.external_assets, [])
        self.assertNotRegex(self.css, r"url\s*\(|@import")
        self.assertNotRegex(self.html, r"<(?:script|link)[^>]+(?:src|href)=[\"'](?:https?:)?//")

    def test_v3_operations_ui_and_api_hooks(self) -> None:
        required_ids = {
            "console-resource-summary",
            "console-resource-note",
            "global-data-size",
            "global-host-memory",
            "global-host-memory-detail",
            "global-disk-free",
            "global-disk-detail",
        }
        self.assertTrue(required_ids.issubset(set(self.ids)))
        for term in (
            "插件管理",
            "记忆模式",
            "direct",
            "astrbot",
            "instance-v3-panel",
            "plugin-switch",
            "memory-mode-option",
            "instance-resource-grid",
            "data_dir_size_bytes",
            "memory_usage_bytes",
            "cpu_percent",
            "host_memory",
            "containerMemoryUsageBytes",
        ):
            self.assertIn(term, self.js)
        for endpoint in (
            "/plugins",
            "/plugins/toggle",
            "/memory",
            "/api/stats",
            "/stats",
        ):
            self.assertIn(endpoint, self.js)
        for style in (
            ".console-resource-summary",
            ".instance-v3-panel",
            ".plugin-switch",
            ".memory-mode-option",
            ".instance-resource-grid",
        ):
            self.assertIn(style, self.css)
        self.assertIn("当前后端未提供插件管理接口", self.js)
        self.assertIn("当前后端未提供记忆模式接口", self.js)
        self.assertIn("当前后端未提供资源统计接口", self.js)

    def test_v3_errors_are_card_local(self) -> None:
        plugin_start = self.js.index("async function handlePluginToggle")
        plugin_end = self.js.index("function buildMemorySection")
        plugin_handler = self.js[plugin_start:plugin_end]
        self.assertNotIn("showToast", plugin_handler)
        self.assertIn("setV3Status", plugin_handler)
        memory_start = self.js.index("async function handleMemoryModeChange")
        memory_end = self.js.index("function containerMemoryText")
        memory_handler = self.js[memory_start:memory_end]
        self.assertNotIn("showToast", memory_handler)
        self.assertIn("setV3Status", memory_handler)

    def test_optional_assistant_b_ui_and_api_hooks(self) -> None:
        required_ids = {
            "assistant-b-card",
            "assistant-b-badge",
            "assistant-b-status",
            "assistant-b-meta",
            "assistant-b-commit",
            "assistant-b-path",
            "assistant-b-entry",
            "assistant-b-readme",
            "assistant-b-install",
            "assistant-b-install-label",
            "assistant-b-update",
            "assistant-b-update-label",
            "assistant-b-retry",
        }
        self.assertTrue(required_ids.issubset(set(self.ids)))
        for term in (
            "AI 助手 B",
            "可选扩展",
            "A · 离线使用助手",
            "安装 AI 助手 B",
            "再次点击确认安装",
            "更新 AI 助手 B",
            "会联网",
            "API Key",
            "当前后端不支持扩展安装",
        ):
            self.assertIn(term, self.html + self.js)
        for endpoint in (
            "/api/assistant/status",
            "/api/assistant/install",
            "/api/assistant/update",
        ):
            self.assertIn(endpoint, self.js)
        for style in (".assistant-b-card", ".assistant-b-meta", ".assistant-b-actions"):
            self.assertIn(style, self.css)

    def test_optional_assistant_b_is_confirm_first_and_card_local(self) -> None:
        start = self.js.index("function sanitiseAssistantBText")
        end = self.js.index("function bindEvents()")
        block = self.js[start:end]
        self.assertIn("assistant.armed", block)
        self.assertIn("再次点击确认安装", block)
        self.assertIn("runAssistantBAction('install')", block)
        self.assertIn("status === 'unsupported'", block)
        self.assertIn("当前后端不支持扩展安装", block)
        self.assertIn("data.ok === false", block)
        self.assertIn("hasInstalledField", block)
        self.assertIn("sanitiseAssistantBText", block)
        self.assertIn("entry", block)
        self.assertIn("readme", block)
        # The B helper may submit a key from the form, but must not persist or display it.
        self.assertIn("body.api_key = apiKey", block)
        self.assertIn("keyInput.value = ''", block)
        for forbidden in ("localStorage", "sessionStorage", "innerHTML", "state.assistantB.apiKey"):
            self.assertNotIn(forbidden, block)
        for forbidden in ("showToast", "setConsoleError"):
            self.assertNotIn(forbidden, block)

    def test_optional_assistant_b_config_ui_and_api_hooks(self) -> None:
        required_ids = {
            "assistant-b-runtime",
            "assistant-b-url",
            "assistant-b-url-note",
            "assistant-b-configure",
            "assistant-b-configure-label",
            "assistant-b-open",
            "assistant-b-config-panel",
            "assistant-b-config-form",
            "assistant-b-key-source-independent",
            "assistant-b-key-source-instance",
            "assistant-b-independent-fields",
            "assistant-b-instance-fields",
            "assistant-b-api-key",
            "assistant-b-instance-select",
            "assistant-b-port",
            "assistant-b-config-error",
            "assistant-b-config-status",
            "assistant-b-config-cancel",
            "assistant-b-config-save",
            "assistant-b-config-save-label",
        }
        self.assertTrue(required_ids.issubset(set(self.ids)))
        self.assertIn('id="assistant-b-api-key" type="password"', self.html)
        for term in (
            "配置 AI 助手 B",
            "复用实例 Key",
            "DeepSeek API Key",
            "保存并启动",
            "loopback",
            "SSH 隧道",
            "默认 8766",
            "当前后端不支持 AI 助手 B 配置/启动",
        ):
            self.assertIn(term, self.html + self.js)
        for token in (
            "/api/assistant/config",
            "/api/assistant/start",
            "key_source",
            "instance_id",
            "api_key",
            "access_note",
            "ssh_tunnel",
            "configure_url",
            "?assistant=configure",
        ):
            self.assertIn(token, self.js)
        for style in (
            ".assistant-b-runtime",
            ".assistant-b-config-panel",
            ".assistant-b-source-group",
            ".assistant-b-port-field",
        ):
            self.assertIn(style, self.css)

    def test_assistant_b_install_redirects_and_query_opens_config(self) -> None:
        self.assertIn("scheduleAssistantBConfigRedirect", self.js)
        self.assertIn("const target = '/console?assistant=configure'", self.js)
        self.assertIn("URLSearchParams(window.location.search).get('assistant') === 'configure'", self.js)
        start = self.js.index("async function runAssistantBAction")
        end = self.js.index("function handleAssistantBInstallClick")
        action_block = self.js[start:end]
        self.assertIn("redirectToConfig = true", action_block)
        self.assertIn("scheduleAssistantBConfigRedirect()", action_block)
        self.assertIn("openConfigRequested = true", action_block)
        # Key must be read only from the form and not persisted to browser storage.
        form_start = self.js.index("async function handleAssistantBConfigSubmit")
        form_end = self.js.index("function renderAssistantBCard")
        form_block = self.js[form_start:form_end]
        self.assertIn("apiKey = keyInput ? keyInput.value.trim() : ''", form_block)
        self.assertIn("body.api_key = apiKey", form_block)
        self.assertIn("if (keyInput) keyInput.value = ''", form_block)
        for forbidden in ("localStorage", "sessionStorage", "innerHTML"):
            self.assertNotIn(forbidden, form_block)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_console_javascript_syntax(self) -> None:
        result = subprocess.run(
            ["node", "--check", str(JS_PATH)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
