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
API_SERVICE_JS = STATIC / "services" / "api.js"
PLUGIN_CONFIG_SERVICE_JS = STATIC / "services" / "plugin-config.js"
INSTANCE_SETTINGS_COMPONENT_JS = STATIC / "components" / "instance-settings.js"

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
        cls.api_service_js = API_SERVICE_JS.read_text(encoding="utf-8")
        cls.plugin_config_service_js = PLUGIN_CONFIG_SERVICE_JS.read_text(encoding="utf-8")
        cls.instance_settings_component_js = INSTANCE_SETTINGS_COMPONENT_JS.read_text(encoding="utf-8")
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
        self.assertIn("instance-delete-btn instance-action-btn", self.console_js)
        self.assertIn(".instance-delete-btn", self.console_css)
        self.assertIn("@media (max-width: 760px)", self.console_css)
        self.assertIn(".console-assistant-link", self.console_css)

    def test_console_browser_safety_and_css_cleanliness(self) -> None:
        for source in (self.console_js, self.api_service_js, self.plugin_config_service_js, self.instance_settings_component_js):
            for forbidden in ("innerHTML", "localStorage", "sessionStorage"):
                self.assertNotIn(forbidden, source)
            self.assertNotRegex(source, r"\balert\s*\(|\bconfirm\s*\(")
        self.assertFalse(self.console_parser.external_assets)
        self.assertEqual(
            self.console_parser.script_sources,
            ["/services/api.js", "/services/plugin-config.js", "/components/instance-settings.js", "console.js"],
        )
        _assert_css_structurally_clean(self, self.console_css, "console.css")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_javascript_syntax_for_console_and_assistant_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for filename, content in (
                ("console.js", self.console_js),
                ("api.js", self.api_service_js),
                ("plugin-config.js", self.plugin_config_service_js),
                ("instance-settings.js", self.instance_settings_component_js),
            ):
                script_path = Path(temp_dir) / filename
                script_path.write_text(content, encoding="utf-8")
                subprocess.run(["node", "--check", str(script_path)], check=True)
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
            "open-button", "start-button", "stop-button", "restart-button", "restart-label",
            "diagnose-button", "diagnose-label", "diagnose-panel", "diagnose-code",
            "diagnose-summary", "diagnose-listener", "diagnose-health", "diagnose-public",
            "diagnose-suggestions", "diagnose-log", "config-card", "config-form",
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
            "/api/assistant/diagnose",
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
        for term in (
            "SSH", "安全组", "防火墙", "VPN", "不要", "公网",
            "诊断公网访问", "停止→启动", "NAT hairpin", "不会自动修改云安全组",
        ):
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

    def test_console_instance_settings_modal_contract(self) -> None:
        required = {
            "instance-settings-overlay", "instance-settings-modal",
            "instance-settings-title", "instance-settings-subtitle",
            "instance-settings-close", "instance-settings-body",
        }
        self.assertTrue(required.issubset(set(self.console_parser.ids)))
        self.assertIn('role="dialog"', self.console_html)
        self.assertIn('aria-modal="true"', self.console_html)
        self.assertIn('aria-labelledby="instance-settings-title"', self.console_html)
        for token in (
            "createInstanceSettings", "openModal", "closeModal", "refreshModal",
            "beginBusy", "endBusy", "mount", "event.target === overlay",
            "options.renderSections", "body.replaceChildren", "aria-busy",
            "findTrigger", "options.onVisibilityChange",
            "if (busy > 0 && closeOptions.force !== true) return false;",
        ):
            self.assertIn(token, self.instance_settings_component_js)
        for token in (
            "instanceSettings.open(instance", "instanceSettings.refresh()",
            "instanceSettings.beginBusy()", "instanceSettings.endBusy()",
            "instanceSettings.isOpenFor", "instanceSettings.isOpen()",
            "instanceSettings.updateSubtitle", "buildPluginSection(instance)",
            "buildMemorySection(instance)", "buildStatsSection(instance)",
            "aria-haspopup", "aria-controls", "event.key !== 'Escape'",
            "if (state.pluginConfig.saving && options.force !== true) return false;",
        ):
            self.assertIn(token, self.console_js)
        self.assertNotIn("state.instanceSettings", self.console_js)
        self.assertNotIn("buildV3Panel", self.console_js)
        self.assertNotIn("document.createElement('details')", self.console_js)
        self.assertNotIn(".instance-v3-panel", self.console_css)
        for style in (
            ".instance-settings-overlay", ".instance-settings-modal",
            ".instance-settings-body", ".instance-settings-btn",
            ".instance-settings-overlay[hidden]",
        ):
            self.assertIn(style, self.console_css)
        self.assertIn("overflow-y: auto", self.console_css)
        self.assertIn("@media (max-width: 640px)", self.console_css)
        settings_body_start = self.console_css.index(".instance-settings-body {")
        settings_body_block = self.console_css[settings_body_start:self.console_css.index("}", settings_body_start) + 1]
        self.assertIn("overflow-y: auto", settings_body_block)

    def test_extracted_api_and_instance_settings_services(self) -> None:
        self.assertIn("createApiClient", self.api_service_js)
        self.assertIn("X-QB-Token", self.api_service_js)
        self.assertIn("requestWithFallback", self.api_service_js)
        self.assertIn("isMissingEndpointError", self.api_service_js)
        self.assertIn("cache: 'no-store'", self.api_service_js)
        self.assertIn("credentials: 'same-origin'", self.api_service_js)
        self.assertIn("createInstanceSettings", self.instance_settings_component_js)
        self.assertIn("onVisibilityChange", self.instance_settings_component_js)
        self.assertIn("beginBusy", self.instance_settings_component_js)
        self.assertIn("body.replaceChildren", self.instance_settings_component_js)
        self.assertIn("aria-busy", self.instance_settings_component_js)
        self.assertIn("options.renderSections", self.instance_settings_component_js)
        for source in (self.api_service_js, self.plugin_config_service_js, self.instance_settings_component_js):
            self.assertNotIn("innerHTML", source)
            self.assertNotIn("localStorage", source)
            self.assertNotIn("sessionStorage", source)
        for script in ("/services/api.js", "/services/plugin-config.js", "/components/instance-settings.js"):
            self.assertIn(f'src="{script}"', self.console_html)

    def test_console_ai_tools_and_auto_sticker_permissions_contract(self) -> None:
        for token in (
            "AI 工具权限",
            "AI 自动表情",
            "允许机器人自己调用插件能力",
            "搜索图片",
            "发送表情",
            "收藏表情",
            "查询表情分类",
            "仅白名单",
            "无电脑控制",
            "safe_tools_enabled",
            "safe_tools_allow_search_images",
            "safe_tools_allow_send_sticker",
            "safe_tools_allow_collect_sticker",
            "safe_tools_allow_list_categories",
            "sticker_enabled",
            "sticker_ai_select_enabled",
            "sticker_ai_marker_enabled",
            "sticker_ai_select_probability",
            "sticker_probability",
            "sticker_cooldown_seconds",
            "collected_sticker_enabled",
            "defaultValue: 0.6",
            "direct 模式由插件给模型提供安全表情 marker",
            "memory/astrbot 模式使用本地情绪或分类匹配",
            "pluginConfigService.load(instance.id, 'qqbot_enhancer')",
            "pluginConfigService.save(instance.id, 'qqbot_enhancer', values)",
            "当前后端不支持 AI 工具权限。",
            "已保存；AstrBot 已热重载。",
            "已保存；AstrBot 需要重启后生效。",
            "AI_STICKER_NUMBERS", "ui.numberInputs[field.key]",
            "values[field.key] = number;", "ai-sticker-number-grid",
            "ai-tools-controls", "ai-tools-enabled", "ai-tools-save",
            "ai-tools-error", "ai-sticker-enabled",
            "ai-sticker-ai-select-probability", "ai-sticker-probability",
            "ai-sticker-cooldown-seconds",
            "QQ 增强插件当前已禁用", "插件当前已禁用",
        ):
            self.assertIn(token, self.console_js)
        for style in (
            ".ai-tools-section", ".ai-tools-controls", ".ai-tools-grid",
            ".ai-tools-subsection", ".ai-sticker-number-grid",
            ".ai-tools-section.is-plugin-disabled",
        ):
            self.assertIn(style, self.console_css)
        for forbidden in ("innerHTML", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, self.console_js)
        self.assertNotRegex(self.console_js, r"\balert\s*\(|\bconfirm\s*\(")

    def test_console_prompt_manager_modal_contract(self) -> None:
        required = {
            "prompt-manager-overlay", "prompt-manager-modal", "prompt-manager-title",
            "prompt-manager-subtitle", "prompt-manager-warning", "prompt-manager-close",
            "prompt-manager-new", "prompt-manager-list", "prompt-manager-loading",
            "prompt-manager-empty", "prompt-manager-form", "prompt-editor-title",
            "prompt-editor-name", "prompt-editor-name-error", "prompt-editor-prompt",
            "prompt-editor-prompt-error", "prompt-editor-prompt-field",
            "prompt-editor-cancel", "prompt-editor-save", "prompt-manager-error",
            "prompt-manager-status",
        }
        self.assertTrue(required.issubset(set(self.console_parser.ids)))
        for token in (
            "openPromptManager", "closePromptManager", "openPromptEditor",
            "submitPromptEditor", "mutatePrompt", "loadPromptList",
            "prompt-manager-open", "prompt-manager-overlay", "prompt-manager-close",
            "prompt-manager-new", "prompt-manager-list", "prompt-editor-prompt",
            "prompt-item-summary", "prompt-item-badge", "state.promptManager.open",
            "/api/instances/${encodeURIComponent(instance.id)}/prompts",
            "action: config.editorMode === 'create' ? 'create' : 'update'",
            "payload.profile_id", "payload.prompt = promptText",
            "const payload = { action, profile_id: prompt.id };",
            "mutatePrompt('activate'", "mutatePrompt('delete'",
            "conversation_created", "restart_required",
            "root.items", "root.profiles", "item.profile_id", "item.content",
            "active_id", "updated_at", "config.prompts.length <= 1",
            "已开始新会话", "需要重启 AstrBot 后生效",
            "当前后端不支持提示词列表。",
            "至少保留一个提示词，不能删除最后一个",
            "切换后会开始新会话",
        ):
            self.assertIn(token, self.console_js)
        self.assertIn("prompt-manager", self.console_html)
        for style in (
            ".prompt-manager-overlay", ".prompt-manager-modal", ".prompt-manager-body",
            ".prompt-item", ".prompt-manager-form", ".prompt-item-summary",
        ):
            self.assertIn(style, self.console_css)
        summary_start = self.console_css.index(".prompt-item-summary {")
        summary_block = self.console_css[summary_start:self.console_css.index("}", summary_start) + 1]
        self.assertIn("white-space: pre-wrap", summary_block)
        self.assertIn("@media (max-width: 640px)", self.console_css)
        for forbidden in ("innerHTML", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, self.console_js)
        self.assertNotRegex(self.console_js, r"\balert\s*\(|\bconfirm\s*\(")

    def test_console_plugin_parameter_editor_contract(self) -> None:
        required = {
            "plugin-config-overlay", "plugin-config-panel", "plugin-config-title",
            "plugin-config-subtitle", "plugin-config-search", "plugin-config-filter-count",
            "plugin-config-body", "plugin-config-loading", "plugin-config-fields",
            "plugin-config-empty", "plugin-config-error", "plugin-config-status",
            "plugin-config-source", "plugin-config-close", "plugin-config-cancel",
            "plugin-config-save", "plugin-config-save-label",
        }
        self.assertTrue(required.issubset(set(self.console_parser.ids)))
        self.assertIn('role="dialog"', self.console_html)
        self.assertIn('aria-modal="true"', self.console_html)
        for token in (
            "plugin-edit-button", "编辑参数", "openPluginConfigPanel", "savePluginConfig",
            "updatePluginConfigFilter", "dataset.searchText", "dataset.fieldKey",
            "reply_probability", "群聊非 @ 消息的回复概率",
            "plugin-config-search", "input.setAttribute('type', 'checkbox')",
            "input.setAttribute('type', 'number')", "range.setAttribute('type', 'range')",
            "document.createElement('textarea')", "document.createElement('select')",
            "type === 'list'", "pluginConfigService.load", "pluginConfigService.save",
            "api_available", "restart_required",
            "AstrBot 已热重载", "AstrBot 需要重启后生效",
            "当前后端不支持参数编辑。",
            "const canEditConfig = Boolean(plugin.id) && plugin.configAvailable !== false;",
        ):
            self.assertIn(token, self.console_js)
        for token in (
            "pluginConfigPath", "/plugins/${encodeURIComponent(pluginId)}/config",
            "body: { values }", "isSensitivePluginField",
            "if (isSensitivePluginField(key, def)) return null;",
            "pluginSchemaSnapshot", "pluginConfigUnsupportedMessage",
            "sliderDefinition.min", "sliderDefinition.max", "sliderDefinition.step",
            "X-QB-Token", "cache: 'no-store'",
        ):
            self.assertIn(token, self.api_service_js + self.plugin_config_service_js)
        self.assertIn("/plugins/${encodeURIComponent(pluginId)}/config", self.plugin_config_service_js)
        for style in (
            ".plugin-edit-button", ".plugin-config-overlay", ".plugin-config-panel",
            ".plugin-config-body", ".plugin-config-field.is-highlight",
        ):
            self.assertIn(style, self.console_css)
        self.assertIn("overflow-y: auto", self.console_css)
        self.assertIn("@media (max-width: 640px)", self.console_css)
        for forbidden in ("localStorage", "sessionStorage", "innerHTML"):
            self.assertNotIn(forbidden, self.console_js)
        self.assertNotRegex(self.console_js, r"\balert\s*\(|\bconfirm\s*\(")


if __name__ == "__main__":
    unittest.main()
