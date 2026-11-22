"""Static + node contract tests for the Studio memory-policy editor (T19).

These tests freeze the frontend contract: the console must render a 记忆策略
panel, talk to the policy API, keep the "only selected groups remember" warning
and never invent saved values.  The node part covers the pure save pipeline
helpers, including that a save payload still carries every one of the 256 plugin
fields (T17 P2 regression).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "qqbot_studio" / "static"
CONSOLE_JS = STATIC / "console.js"
CONSOLE_CSS = STATIC / "console.css"
CONSOLE_HTML = STATIC / "console.html"
API_JS = STATIC / "services" / "api.js"
PLUGIN_CONFIG_JS = STATIC / "services" / "plugin-config.js"

# Assertions here compare against very large source strings, so they use plain
# `assertTrue(token in text, token)` messages: unittest's assertIn would dump
# the entire file into the failure output.
PANEL_TOKENS = (
    "function buildMemoryPolicySection(instance)",
    "buildMemoryPolicySection(instance),",
    "'记忆策略'",
    "按群开启 AstrBot 原生长期记忆（memory.policy）",
    "'默认模式（未列出群的模式）'",
    "'私聊模式（固定 scope=user）'",
    "'按群覆盖'",
    "'添加群策略'",
    "'保存记忆策略'",
)

FIELD_TOKENS = (
    "idInput.dataset.field = 'group-id'",
    "makeSelect('mode',",
    "makeSelect('scope',",
    "makeCheckbox('extract',",
    "makeCheckbox('inject',",
    "makeBudget('max-items',",
    "makeBudget('max-chars',",
)

# `element.dataset.memoryPolicyX` renders as the `data-memory-policy-x`
# attribute, so the source tokens use the camelCase spelling.
MARKER_TOKENS = (
    "dataset.memoryPolicyRow",
    "dataset.memoryPolicyDelete",
    "dataset.memoryPolicyEdit",
    "dataset.memoryPolicy = 'add'",
    "dataset.memoryPolicy = 'save'",
    "dataset.memoryPolicy = 'warning'",
    "dataset.memoryPolicy = 'unsupported'",
    "dataset.memoryPolicy = 'error'",
    "dataset.memoryPolicy = 'default-mode'",
    "dataset.memoryPolicy = 'private-mode'",
    "dataset.memoryPolicy = 'groups'",
    "dataset.memoryPolicy = 'empty'",
    "dataset.memoryPolicy = 'status'",
)


class MemoryPolicyStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.console_js = CONSOLE_JS.read_text(encoding="utf-8")
        cls.console_css = CONSOLE_CSS.read_text(encoding="utf-8")
        cls.console_html = CONSOLE_HTML.read_text(encoding="utf-8")
        cls.plugin_config_js = PLUGIN_CONFIG_JS.read_text(encoding="utf-8")

    def _has(self, text: str, tokens, label: str) -> None:
        missing = [token for token in tokens if token not in text]
        self.assertFalse(missing, f"{label} 缺少：{missing}")

    def test_panel_is_rendered_in_instance_settings(self) -> None:
        self._has(self.console_js, PANEL_TOKENS, "console.js")

    def test_panel_row_edits_cover_every_policy_field(self) -> None:
        self._has(self.console_js, FIELD_TOKENS + MARKER_TOKENS, "console.js")

    def test_row_collector_sends_every_field(self) -> None:
        for token in (
            "payload.groups.push({",
            "group_id: groupId,",
            "mode: rowValue(rowElement, 'mode').value,",
            "scope: rowValue(rowElement, 'scope').value,",
            "extract: rowValue(rowElement, 'extract').checked,",
            "inject: rowValue(rowElement, 'inject').checked,",
            "max_items: budgetValue(rowValue(rowElement, 'max-items')),",
            "max_chars: budgetValue(rowValue(rowElement, 'max-chars'))",
        ):
            self.assertTrue(token in self.console_js, token)

    def test_rows_collapse_to_summary_with_edit_and_delete_actions(self) -> None:
        for token in (
            "memory-policy-row-summary",
            "memory-policy-row-actions",
            "memory-policy-row-fields",
            "editButton.dataset.memoryPolicyEdit = ''",
            "removeButton.dataset.memoryPolicyDelete = ''",
            "extractInput && extractInput.checked",
            "injectInput && injectInput.checked",
            "setEditing(fields.hidden)",
            "fields.hidden = true",
        ):
            self.assertTrue(token in self.console_js or token in self.console_css, token)

    def test_add_and_delete_rows_are_supported(self) -> None:
        for token in (
            "groupsContainer.insertBefore(",
            "rowElement.remove();",
            "没有群策略",
            "syncEmpty();",
        ):
            self.assertTrue(token in self.console_js, token)

    def test_warning_states_selected_groups_only(self) -> None:
        self._has(
            self.console_js,
            (
                "长期记忆只对下面列出的群生效",
                "未列出的群和私聊保持 conversation（只用会话历史）",
            ),
            "console.js",
        )
        self.assertTrue("memory-policy-warning" in self.console_css)

    def test_unsupported_state_is_explicit_and_not_faked(self) -> None:
        self._has(
            self.console_js,
            (
                "当前实例不支持编辑记忆策略",
                "Studio 不会显示伪造的已保存值",
                "status: 'unsupported'",
                "section.replaceWith(buildMemoryPolicySection(instance));",
                "memoryEnabled === false",
            ),
            "console.js",
        )

    def test_api_paths_and_loader_are_wired(self) -> None:
        self._has(
            self.console_js,
            (
                "/memory-policy",
                "instancePath('/api/memory-policy', instance.id)",
                "function loadMemoryPolicyForInstance(instance)",
                "memoryPolicy: emptyMemoryPolicyState()",
            ),
            "console.js",
        )

    def test_client_never_touches_the_memory_database(self) -> None:
        combined = self.console_js + self.plugin_config_js + self.console_html
        for token in ("astrbot_memory", "memory.db", "sqlite", "SELECT "):
            self.assertFalse(token in combined, f"前端不应出现 {token}")

    def test_bounds_and_enums_come_from_the_backend(self) -> None:
        for token in (
            "limits.maxItems",
            "limits.maxChars",
            "policy.modeOptions",
            "policy.scopeOptions",
            "memoryPolicyLimits(",
        ):
            self.assertTrue(token in self.console_js, token)

    def test_existing_settings_and_plugin_config_are_untouched(self) -> None:
        # The policy panel is additive: the plugin-config overlay and the
        # existing memory-mode section keep their own APIs.
        self.assertTrue("buildMemorySection(instance)," in self.console_js)
        self.assertTrue("buildPluginSection(instance)]" in self.console_js)
        self.assertTrue("'/api/memory'" in self.console_js or "/api/memory`" in self.console_js)


class MemoryPolicyWarningContractTests(unittest.TestCase):
    """t26 P2: invalid rows must be visible, never shown as effective."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.console_js = CONSOLE_JS.read_text(encoding="utf-8")
        cls.console_css = CONSOLE_CSS.read_text(encoding="utf-8")

    def test_rows_render_invalid_state_and_reasons(self) -> None:
        for token in (
            "rowElement.classList.add('is-invalid')",
            "该行配置非法：AstrBot 会把整条降级为 conversation",
            "dataset.memoryPolicy = 'row-errors'",
            "dataset.memoryPolicy = 'row-effective'",
            "实际生效：",
            "未知字段 ${row.unknownFields.join(', ')} 会被 AstrBot 忽略",
        ):
            self.assertTrue(token in self.console_js, token)

    def test_private_invalid_state_is_surfaced(self) -> None:
        for token in (
            "privateInvalid",
            "privateErrors",
            "dataset.memoryPolicy = 'private-notes'",
            "dataset.memoryPolicy = 'private-errors'",
            "私聊策略配置非法：AstrBot 会把私聊策略降级为 conversation",
            "未单独配置私聊策略：私聊跟随默认模式",
            "privateConfigured",
        ):
            self.assertTrue(token in self.console_js, token)

    def test_panel_message_turns_into_a_warning_when_rows_are_invalid(self) -> None:
        self.assertTrue(
            "policy.memoryEnabled === false || policy.invalidRows > 0 ? 'warning' : 'info'"
            in self.console_js
        )
        self.assertTrue("invalidRows: Number(root.invalid_rows)" in self.console_js)
        self.assertTrue("memory-policy-row-effective" in self.console_css)
        self.assertTrue("memory-policy-row-invalid" in self.console_css)

    def test_unknown_effective_mode_never_renders_as_long_term(self) -> None:
        js = self.console_js
        self.assertTrue("function memoryPolicyEffectiveMode(value)" in js)
        self.assertTrue("if (value === null || value === undefined || value === '') return '';" in js)
        self.assertTrue("未知（配置非法或重复，无法确定哪个生效）" in js)

    def test_every_warning_is_rendered(self) -> None:
        # The warnings list is built from the full server array (no slicing).
        self.assertTrue("for (const item of policy.warnings || [])" in self.console_js)
        self.assertTrue("warningList.hidden = !(policy.warnings || []).length" in self.console_js)

    def test_normaliser_keeps_invalid_bits(self) -> None:
        for token in (
            "invalid: record.invalid === true",
            "errors: memoryPolicyStringList(record.errors)",
            "unknownFields: memoryPolicyStringList(record.unknown_fields)",
            "effectiveMode: memoryPolicyEffectiveMode(record.effective_mode)",
            "privateInvalid: privateRecord.invalid === true",
        ):
            self.assertTrue(token in self.console_js, token)


class MemoryPolicyPermissionUiContractTests(unittest.TestCase):
    """t30: a permission error is actionable, never "unsupported" or "saved"."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.console_js = CONSOLE_JS.read_text(encoding="utf-8")
        cls.console_css = CONSOLE_CSS.read_text(encoding="utf-8")

    def test_permission_state_is_error_not_unsupported(self) -> None:
        js = self.console_js
        self.assertTrue("permissionDenied" in js)
        self.assertTrue("root.permission_denied === true" in js)
        self.assertTrue("root.supported === true && root.available === false" in js)
        self.assertTrue("status: permissionDenied ? 'error' : 'unsupported'" in js)
        self.assertTrue("policy.permissionDenied" in js)

    def test_actions_and_channel_are_rendered(self) -> None:
        js = self.console_js
        for token in (
            "dataset.memoryPolicy = 'actions'",
            "memory-policy-actions",
            "dataset.memoryPolicy = 'channel'",
            "已通过容器内命令读写（原子写入 0600）",
            "当前无法写入：保存会明确失败，不会显示“已保存”。",
        ):
            self.assertTrue(token in js, token)
        self.assertTrue("memory-policy-actions" in self.console_css)

    def test_save_failure_keeps_panel_usable_without_fake_save(self) -> None:
        js = self.console_js
        for token in (
            "const steps = payload ? memoryPolicyStringList(payload.actions) : [];",
            "未保存：请按上方步骤处理后重试。Studio 不会显示伪造的已保存状态。",
            "payload.supported === true",
        ):
            self.assertTrue(token in js, token)

    def test_normaliser_reads_channel_fields(self) -> None:
        for token in (
            "readChannel: textValue(root.read_channel, '')",
            "writeChannel: textValue(root.write_channel, '')",
            "writable: root.writable !== false",
            "containerRunning: root.container_running === true",
        ):
            self.assertTrue(token in self.console_js, token)


class PluginSavePipelineContractTests(unittest.TestCase):
    """T17 P2: payload completeness, badge baseline refresh, reveal plan."""

    def _run_node(self, source: str) -> None:
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "save-pipeline-test.js"
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_payload_keeps_all_256_fields_and_baselines_clear(self) -> None:
        self._run_node(
            f"""
            'use strict';
            const fs = require('fs');
            const assert = require('assert');
            global.window = globalThis;
            class FakeApiError extends Error {{
              constructor(message, options = {{}}) {{ super(message); this.status = options.status || 0; this.data = options.data || null; }}
            }}
            const api = {{ ApiError: FakeApiError, async request() {{ return {{ ok: true }}; }} }};
            eval(fs.readFileSync({json.dumps(str(API_JS))}, 'utf8'));
            eval(fs.readFileSync({json.dumps(str(PLUGIN_CONFIG_JS))}, 'utf8'));
            const service = window.QQBotStudio.services.pluginConfig.createPluginConfigService({{ api }});

            // 256 plugin fields, the size of the real qqbot_enhancer schema.
            const entries = [];
            for (let index = 0; index < 256; index += 1) {{
              entries.push({{
                key: `field_${{index}}`,
                type: 'string',
                field: {{ key: `field_${{index}}`, type: 'string' }},
                groupId: index < 128 ? 'g1' : 'g2',
                originalValue: `original-${{index}}`,
                readValue: () => ({{ ok: true, value: `value-${{index}}` }})
              }});
            }}
            const plan = service.pluginConfigSavePayload(entries);
            assert.strictEqual(plan.ok, true);
            assert.strictEqual(Object.keys(plan.values).length, 256);
            assert.strictEqual(plan.values.field_0, 'value-0');
            assert.strictEqual(plan.values.field_255, 'value-255');

            // Filtered-out fields are still submitted; legacy-hidden ones are
            // intentionally not (they were replaced by the prompt manager).
            const withHidden = entries.concat([
              {{ key: 'legacy', legacyHidden: true, readValue: () => ({{ ok: true, value: 'x' }}) }},
              {{ key: 'filtered', readValue: () => ({{ ok: true, value: 'y' }}) }}
            ]);
            const full = service.pluginConfigSavePayload(withHidden);
            assert.strictEqual(Object.keys(full.values).length, 257);
            assert.ok(!Object.prototype.hasOwnProperty.call(full.values, 'legacy'));
            assert.strictEqual(full.values.filtered, 'y');

            // An unreadable field blocks the save and keeps its entry so the
            // console can reveal + focus it.
            const broken = service.pluginConfigSavePayload([
              {{ key: 'good', readValue: () => ({{ ok: true, value: 1 }}) }},
              {{ key: 'bad', readValue: () => ({{ ok: false, message: '必须是数字' }}) }}
            ]);
            assert.strictEqual(broken.ok, false);
            assert.strictEqual(broken.invalid.length, 1);
            assert.strictEqual(broken.invalid[0].message, '必须是数字');
            assert.strictEqual(broken.invalid[0].entry.key, 'bad');
            assert.deepStrictEqual(Object.keys(broken.values), ['good']);

            // After a successful save the comparison baseline is refreshed, so
            // the "已修改" badge clears instead of sticking forever.
            const submitted = {{ field_0: 'value-0', field_1: 'value-1' }};
            const updates = service.pluginConfigUpdatedBaselines(entries, submitted, {{}});
            assert.deepStrictEqual(updates, [
              {{ key: 'field_0', value: 'value-0' }},
              {{ key: 'field_1', value: 'value-1' }}
            ]);
            const savedWins = service.pluginConfigUpdatedBaselines(entries, submitted, {{ field_0: 'normalised-0' }});
            assert.strictEqual(savedWins[0].value, 'normalised-0');
            assert.strictEqual(savedWins[1].value, 'value-1');
            // Fields the server never received keep their old baseline.
            assert.strictEqual(service.pluginConfigUpdatedBaselines(entries, {{}}).length, 0);

            // The post-save baseline really clears the badge logic.
            const entry = entries[0];
            entry.originalValue = 'original-0';
            assert.strictEqual(service.pluginFieldValueChanged(entry.field, 'value-0', entry.originalValue), true);
            entry.originalValue = service.pluginConfigUpdatedBaselines([entry], {{ field_0: 'value-0' }}, {{}})[0].value;
            assert.strictEqual(service.pluginFieldValueChanged(entry.field, 'value-0', entry.originalValue), false);

            // Validation failure: the reveal plan drops search/modified filters
            // and switches to (or keeps) the group that owns the field.
            const foreignGroup = service.pluginConfigRevealPlan(
              {{ groupId: 'g2' }},
              {{ activeGroup: 'g1', modifiedOnly: true }}
            );
            assert.deepStrictEqual(foreignGroup, {{
              clearSearch: true,
              modifiedOnly: false,
              activeGroup: 'all',
              expandGroup: 'g2',
              scrollIntoView: true
            }});
            const sameGroup = service.pluginConfigRevealPlan(
              {{ groupId: 'g2' }},
              {{ activeGroup: 'g2', modifiedOnly: true }}
            );
            assert.strictEqual(sameGroup.activeGroup, 'g2');
            assert.strictEqual(sameGroup.modifiedOnly, false);
            const allGroup = service.pluginConfigRevealPlan({{ groupId: 'g1' }}, {{ activeGroup: 'all' }});
            assert.strictEqual(allGroup.activeGroup, 'all');
            assert.ok(service.pluginConfigRevealPlan({{}}, {{}}).expandGroup.length > 0);

            console.log('save pipeline contract ok');
            """
        )

    def test_console_wires_the_save_pipeline(self) -> None:
        js = CONSOLE_JS.read_text(encoding="utf-8")
        for token in (
            "pluginConfigService.pluginConfigSavePayload(config.entries)",
            "pluginConfigService.pluginConfigUpdatedBaselines(",
            "pluginConfigService.pluginConfigRevealPlan(entry, ui)",
            "revealPluginConfigEntry(item.entry);",
            "refreshAllPluginConfigModifiedEntries();",
            "entry.element.scrollIntoView({ block: 'center' })",
            "function revealPluginConfigEntry(entry)",
            "请先修正标红的参数。",
        ):
            self.assertTrue(token in js, token)


class MemoryPolicyDocsContractTests(unittest.TestCase):
    def test_docs_and_changelog_record_the_panel(self) -> None:
        doc = (ROOT / "docs" / "memory-policy.md").read_text(encoding="utf-8")
        for token in (
            "memory.policy",
            "/api/instances/<id>/memory-policy",
            "长期记忆总开关仍是 AstrBot 的 `memory.enable`",
            "从不打开",
            "409",
            "没有做真实 AstrBot 进程 E2E",
            "restart_required=true",
        ):
            self.assertTrue(token in doc, token)
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        self.assertTrue("[`memory-policy.md`](memory-policy.md)" in index)
        contract = (ROOT / "docs" / "ui-api-contract-v2.md").read_text(encoding="utf-8")
        self.assertTrue("按群长期记忆策略（memory.policy）" in contract)
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertTrue("记忆策略面板（Studio）" in changelog)
        self.assertTrue("未做真实 AstrBot 进程 E2E" in changelog)


if __name__ == "__main__":
    unittest.main()

class GroupPolicyRegistrationStaticTests(unittest.TestCase):
    """T20: every plugin field can be published into group-policy-prefs.

    The editor offers a per-field "加入群策略" pill.  It must map the editor's
    config key onto the capability-policy parameter name (config_key), persist
    the mapping as ``registered[plugin_id][config_path] = policy_param`` and
    keep the group-policy page able to override that parameter per group.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.console_js = CONSOLE_JS.read_text(encoding="utf-8")
        cls.console_css = CONSOLE_CSS.read_text(encoding="utf-8")

    def _has(self, text: str, tokens, label: str) -> None:
        missing = [token for token in tokens if token not in text]
        self.assertFalse(missing, f"{label} 缺少：{missing}")

    def test_every_field_renders_a_group_policy_action(self) -> None:
        self._has(self.console_js, (
            "const policyButton = makeElement('button', 'plugin-field-policy', '加入群策略');",
            "policyButton.dataset.policyFieldKey = field.key;",
            "togglePluginFieldGroupPolicy(field, policyButton);",
        ), "console.js")

    def test_action_maps_editor_key_to_policy_param(self) -> None:
        self._has(self.console_js, (
            "function pluginFieldPolicyTarget(field)",
            "entries.find(([, spec]) => String(asRecord(spec).config_key || '') === key)",
            "if (detail.system_managed) return { param, spec, blocked: 'system' };",
            "if (detail.sensitive) return { param, spec, blocked: 'sensitive' };",
            "if (!detail.group_overridable) return { param, spec, blocked: 'global' };",
            "if (String(detail.source || '') === 'schema' && config.policyOverlaySupported === false) {",
            "blocked: 'overlay'",
            "'仅全局'",
        ), "console.js")

    def test_unsupported_fields_are_disabled_not_silently_hidden(self) -> None:
        self._has(self.console_js, (
            "button.disabled = true;",
            "该参数的运行时读取尚未接入群策略（capability_policy 未登记 config_key），当前只能全局修改。",
            "复杂类型（对象/数组）不支持按群覆盖，只能全局修改。",
            "敏感参数（Key/密钥/白名单）只允许全局修改，不能加入群策略。",
        ), "console.js")

    def test_editor_reports_group_override_coverage(self) -> None:
        # Most plugin settings are read straight from the plugin config, so the
        # editor must state how many parameters are actually group-overridable
        # instead of letting operators assume all of them are.
        self._has(self.console_js, (
            "function pluginConfigPolicyCoverage(config)",
            "if (!spec.group_overridable || spec.sensitive || spec.system_managed) continue;",
            "if (String(spec.source || '') === 'schema' && !overlayReady) continue;",
            "function pluginConfigPolicyHint(config)",
            "` · 可按群覆盖 ${covered}/${total} 个参数`",
        ), "console.js")

    def test_toggle_persists_registered_map_without_narrowing_roster(self) -> None:
        self._has(self.console_js, (
            "async function togglePluginFieldGroupPolicy(field, button)",
            "const registered = { ...asRecord(prefs.registered) };",
            "registry[path] = target.param;",
            "const eligible = Object.entries(asRecord(config.policySchema))",
            "if (!detail.group_overridable || detail.sensitive || detail.system_managed) return false;",
            "return String(detail.source || '') !== 'schema' || overlayReady;",
            "body: { selected, marked, registered }",
        ), "console.js")

    def test_editor_loads_schema_and_prefs_before_rendering(self) -> None:
        self._has(self.console_js, (
            "group-param-schema/${encodeURIComponent(plugin.id)}",
            "groupPolicyPrefsPath(instance.id)",
            "config.policySchema = asRecord(schemaData.fields);",
            "config.policyOverlaySupported = Boolean(schemaData.overlay_supported);",
            "config.policyUnsupported = asRecord(schemaData.unsupported);",
            "config.policyPrefs = {",
            "state.pluginConfig.policyPrefs = { selected: {}, marked: {}, registered: {} };",
        ), "console.js")

    def test_group_policy_page_fetches_schema_lazily(self) -> None:
        # `policy.scope` stopped embedding every plugin schema (hundreds of
        # fields); the page must backfill per plugin before rendering.
        self._has(self.console_js, (
            "`${scopePath(scope)}?include_schema=0`",
            "if (!data.schemas || !Object.keys(data.schemas).length) {",
            "const pluginIds = Array.isArray(data.available_plugins) ? data.available_plugins : [];",
            "group-param-schema/${encodeURIComponent(id)}",
            "schemas[id] = asRecord(asRecord(payload).fields);",
            "data.schemas = schemas;",
        ), "console.js")

    def test_group_policy_page_defaults_to_registered_and_marked(self) -> None:
        self._has(self.console_js, (
            "let policyShowAll = false;",
            "' 显示全部参数'",
            "policyShowAll = Boolean(showAllBox.checked);",
            "if (markedParamsFor(pluginId).has(key)) return true;",
            "return policyShowAll;",
        ), "console.js")

    def test_editor_button_states_cover_system_and_overlay(self) -> None:
        self._has(self.console_js, (
            "if (target.blocked === 'system') {",
            "button.textContent = '系统字段';",
            "if (target.blocked === 'overlay') {",
            "该插件尚未接入按群配置覆盖（缺少 policy_overlay.py）",
        ), "console.js")

    def test_group_policy_page_honours_registered_params(self) -> None:
        self._has(self.console_js, (
            "registeredFor(pluginId)",
            "registeredPathFor(pluginId, name)",
            "if (Object.values(registeredFor(pluginId)).map(String).includes(key)) return true;",
            "if (markedParamsFor(pluginId).has(key)) return true;",
            "return policyShowAll;",
            "`来自参数编辑：${registeredPath}`",
            "`来自插件参数：${registeredPath}`",
            "registered: policyPrefs.registered",
            "registered: asRecord(data.registered)",
        ), "console.js")

    def test_group_policy_page_curates_defaults_and_collapses_plugins(self) -> None:
        # The page used to dump every plugin at once.  Important knobs render by
        # default, every plugin card can be collapsed, and a dropdown limits the
        # grid to one plugin.
        self._has(self.console_js, (
            "const GROUP_POLICY_IMPORTANT = {",
            "const policyImportant = (pluginId, name) => {",
            "if (policyImportant(pluginId, key)) return true;",
            "group-policy-filter-row",
            "pluginFilter.className = 'group-policy-plugin-filter';",
            "'显示全部插件'",
            "group-policy-collapse",
            "collapsedPolicyPlugins",
            "const expandedPolicyPlugins = new Set();",
            "function policyCardCollapsed(pluginId) {",
            "return policyShowAll;",
            "function togglePolicyCard(pluginId) {",
            "const cardBody = makeElement('div', 'group-policy-card-body');",
            "cardBody.hidden = policyCardCollapsed(pluginId);",
            "▸ 展开（${paramCount}）",
            "params: collectParams(cardBody, schema)",
            "该实例未安装此插件，或没有可按群覆盖的参数。",
            "const fieldRow = makeElement('div', 'group-policy-field-row');",
            "fieldRow.appendChild(markButton);",
            "const description = spec && typeof spec.description === 'string' ? spec.description.trim() : '';",
            "label.appendChild(makeElement('small', 'group-policy-field-desc', paramMeta.description));",
        ), "console.js")
        self._has(self.console_css, (
            ".group-policy-filter-row {",
            ".group-policy-plugin-filter {",
            ".group-policy-collapse {",
            "align-items: start;",
            "max-height: 44vh;",
            "overflow-y: auto;",
            ".group-policy-card-body[hidden] {",
            ".group-policy-field-row {",
            ".group-policy-mark-button {",
            "margin-right: auto;",
            ".group-policy-field-desc {",
            "appearance: none;",
            "linear-gradient(45deg, transparent 50%, #6b7280 50%),",
            ".group-policy-plugin-filter:disabled,",
        ), "console.css")

    def test_registration_pill_has_desktop_and_mobile_styles(self) -> None:
        self._has(self.console_css, (
            ".plugin-field-policy {",
            ".plugin-field-policy:hover:not(:disabled)",
            ".plugin-field-policy.is-registered",
            ".plugin-field-policy:disabled",
            ".group-policy-field-registered",
            ".group-policy-prefs-path",
            ".plugin-field-policy { order: 3; flex: 1 0 100%; padding: 5px 8px; text-align: center; }",
        ), "console.css")

