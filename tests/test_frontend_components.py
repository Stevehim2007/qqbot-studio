"""Node-based contract tests for extracted frontend services/components."""

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
API_JS = STATIC / "services" / "api.js"
PLUGIN_CONFIG_JS = STATIC / "services" / "plugin-config.js"
INSTANCE_SETTINGS_JS = STATIC / "components" / "instance-settings.js"


class FrontendComponentTests(unittest.TestCase):
    def _run_node(self, source: str) -> None:
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "component-test.js"
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_phase4_architecture_doc_records_extracted_boundaries(self) -> None:
        doc = (ROOT / "docs" / "architecture-modularization.md").read_text(encoding="utf-8")
        for token in (
            "Phase 4：前端组件化",
            "static/services/api.js",
            "static/services/plugin-config.js",
            "static/components/instance-settings.js",
            "Phase 4 剩余债务",
            "经典脚本命名空间",
        ):
            self.assertIn(token, doc)

    def test_api_service_token_error_and_fallback_contract(self) -> None:
        self._run_node(
            textwrap.dedent(
                f'''
                'use strict';
                const fs = require('fs');
                global.window = globalThis;
                eval(fs.readFileSync({json.dumps(str(API_JS))}, 'utf8'));
                const calls = [];
                function response(data, status = 200) {{
                  return {{ ok: status >= 200 && status < 300, status, async text() {{ return JSON.stringify(data); }} }};
                }}
                const sharedApi = window.QQBotStudio.services.api;
                const api = sharedApi.createApiClient({{
                  token: 'local-test-token',
                  fetchImpl: async (path, options) => {{
                    calls.push({{ path, options }});
                    if (path === '/missing') return response({{ ok: false, message: 'missing' }}, 404);
                    if (path === '/fail') return response({{ ok: false, message: 'bad request' }}, 400);
                    return response({{ ok: true, path }});
                  }}
                }});
                (async () => {{
                  const data = await api.request('/api/instances', {{ method: 'POST', body: {{ enabled: true }} }});
                  if (!data.ok) throw new Error('POST failed');
                  if (calls[0].options.headers['X-QB-Token'] !== 'local-test-token') throw new Error('token header missing');
                  if (calls[0].options.headers['Content-Type'] !== 'application/json') throw new Error('json content type missing');
                  if (calls[0].options.body !== JSON.stringify({{ enabled: true }})) throw new Error('body encoding wrong');
                  const fallback = await api.requestWithFallback(['/missing', '/ok']);
                  if (!fallback.ok || fallback.path !== '/ok') throw new Error('404 fallback failed');
                  let apiError = null;
                  try {{ await api.request('/fail'); }} catch (error) {{ apiError = error; }}
                  if (!(apiError instanceof api.ApiError) || apiError.status !== 400 || apiError.data.message !== 'bad request') {{
                    throw new Error('ApiError contract failed');
                  }}
                  const parser = sharedApi.createBoolParser(['yes', 'running'], ['no', 'stopped']);
                  if (parser(' YES ') !== true || parser('stopped') !== false || parser('other') !== undefined) throw new Error('boolean parser contract failed');
                  if (sharedApi.safeHttpUrl('javascript:alert(1)') !== '' || sharedApi.safeHttpUrl('https://example.com/a') !== 'https://example.com/a') {{
                    throw new Error('safe URL contract failed');
                  }}
                  const offline = sharedApi.createApiClient({{
                    connectionErrorMessage: '自定义连接失败',
                    fetchImpl: async () => {{ throw new Error('offline'); }}
                  }});
                  let offlineError = null;
                  try {{ await offline.request('/offline'); }} catch (error) {{ offlineError = error; }}
                  if (!(offlineError instanceof api.ApiError) || offlineError.status !== 0 || offlineError.message !== '自定义连接失败') {{
                    throw new Error('connection error message contract failed');
                  }}
                  console.log('api-service PASS');
                }})().catch((error) => {{ console.error(error); process.exit(1); }});
                '''
            )
        )

    def test_plugin_config_service_schema_and_sensitive_filtering(self) -> None:
        self._run_node(
            textwrap.dedent(
                f'''
                'use strict';
                const fs = require('fs');
                global.window = globalThis;
                class FakeApiError extends Error {{
                  constructor(message, options = {{}}) {{ super(message); this.status = options.status || 0; this.data = options.data || null; }}
                }}
                const calls = [];
                const api = {{
                  ApiError: FakeApiError,
                  async request(path, options = {{}}) {{
                    calls.push({{ path, options }});
                    if ((options.method || 'GET') === 'POST') return {{ ok: true, restart_required: false, api_available: true, message: 'saved' }};
                    return {{
                      ok: true,
                      schema: {{
                        reply_probability: {{ type: 'float', default: 0.12, slider: {{ min: 0, max: 1, step: 0.01 }}, description: '群聊非 @ 消息的回复概率' }},
                        mode: {{ type: 'string', default: 'smart', options: ['smart', 'always'], labels: ['智能', '总是'] }},
                        api_key: {{ type: 'string', default: 'must-not-leak' }},
                        allowed: {{ type: 'bool', default: true }}
                      }},
                      values: {{ reply_probability: 0.25, mode: 'always', allowed: false, api_key: 'sk-must-not-leak-1234' }},
                      source: 'local',
                      api_available: true,
                      restart_required: false,
                      message: 'loaded'
                    }};
                  }}
                }};
                eval(fs.readFileSync({json.dumps(str(API_JS))}, 'utf8'));
                eval(fs.readFileSync({json.dumps(str(PLUGIN_CONFIG_JS))}, 'utf8'));
                const service = window.QQBotStudio.services.pluginConfig.createPluginConfigService({{ api }});
                (async () => {{
                  const loaded = await service.load('bot-1', 'qqbot_enhancer');
                  if (calls[0].path !== '/api/instances/bot-1/plugins/qqbot_enhancer/config') throw new Error('GET path wrong');
                  if (!loaded.snapshot.schema.some((field) => field.key === 'reply_probability')) throw new Error('reply_probability schema missing');
                  if (loaded.snapshot.schema.some((field) => field.key === 'api_key')) throw new Error('sensitive schema rendered');
                  if (Object.prototype.hasOwnProperty.call(loaded.snapshot.values, 'api_key')) throw new Error('sensitive value retained');
                  if (JSON.stringify(loaded.snapshot).includes('sk-must-not-leak-1234')) throw new Error('key leaked in snapshot');
                  const saved = await service.save('bot-1', 'qqbot_enhancer', {{ reply_probability: 0.5, allowed: false }});
                  if (!saved.ok) throw new Error('save failed');
                  if (calls[1].path !== '/api/instances/bot-1/plugins/qqbot_enhancer/config') throw new Error('POST path wrong');
                  if (calls[1].options.method !== 'POST') throw new Error('POST method wrong');
                  if (JSON.stringify(calls[1].options.body) !== JSON.stringify({{ values: {{ reply_probability: 0.5, allowed: false }} }})) throw new Error('save payload wrong');
                  console.log('plugin-config-service PASS');
                }})().catch((error) => {{ console.error(error); process.exit(1); }});
                '''
            )
        )

    def test_plugin_config_group_navigation_search_and_modified_contract(self) -> None:
        js = textwrap.dedent(
            """

'use strict';
const fs = require('fs');
global.window = globalThis;
class FakeApiError extends Error {
  constructor(message, options = {}) { super(message); this.status = options.status || 0; this.data = options.data || null; }
}
const calls = [];
const api = {
  ApiError: FakeApiError,
  async request(path, options = {}) {
    calls.push({ path, options });
    return { ok: true, api_available: true, restart_required: false, message: 'ok' };
  }
};
eval(fs.readFileSync(__API_JS__, 'utf8'));
eval(fs.readFileSync(__PLUGIN_CONFIG_JS__, 'utf8'));
const service = window.QQBotStudio.services.pluginConfig.createPluginConfigService({ api });

const requiredTitles = [
  '常用回复', '识图/搜索', '表情包', '本地点歌', '会话/人格',
  '记忆', 'Food', '网页访问', '安全工具', '高级/其他'
];
const schema = {
  reply_probability: { type: 'float', description: '群聊非 @ 消息的回复概率', hint: '0 到 1' },
  image_search_enabled: { type: 'bool' },
  sticker_enabled: { type: 'bool' },
  song_enabled: { type: 'bool' },
  group_personas: { type: 'text' },
  memory_mode: { type: 'string' },
  food_enabled: { type: 'bool' },
  web_fetch_timeout_seconds: { type: 'int' },
  safe_tools_enabled: { type: 'bool' },
  sticker_ai_select_enabled: { type: 'bool' },
  mystery_setting: { type: 'string', description: '未映射字段也必须保留' },
  advanced_extra: { type: 'string' }
};
for (let index = 0; index < 244; index += 1) {
  schema['misc_field_' + index] = { type: 'string', description: '杂项 ' + index };
}
const fieldCount = Object.keys(schema).length;
const snapshot = service.pluginSchemaSnapshot({ ok: true, schema, values: {}, api_available: true });
if (snapshot.schema.length !== fieldCount) throw new Error('grouping lost fields');
const keys = new Set(snapshot.schema.map((field) => field.key));
if (keys.size !== fieldCount) throw new Error('grouping duplicated fields');
for (const key of Object.keys(schema)) if (!keys.has(key)) throw new Error('missing field ' + key);
if (!snapshot.schema.every((field) => field.groupId && field.groupTitle)) throw new Error('group metadata missing');
const titles = snapshot.groups.map((group) => group.title);
for (const title of requiredTitles) {
  if (!titles.includes(title)) throw new Error('required group missing: ' + title);
}
if (snapshot.groups[snapshot.groups.length - 1].title !== '高级/其他') throw new Error('advanced group must stay last');
const groupedFieldTotal = snapshot.groups.reduce((sum, group) => sum + group.fields.length, 0);
if (groupedFieldTotal !== fieldCount) throw new Error('group field total wrong');
const groupOf = (key) => snapshot.schema.find((field) => field.key === key).groupTitle;
const expectations = {
  reply_probability: '常用回复',
  image_search_enabled: '识图/搜索',
  sticker_enabled: '表情包',
  sticker_ai_select_enabled: '表情包',
  song_enabled: '本地点歌',
  group_personas: '会话/人格',
  memory_mode: '记忆',
  food_enabled: 'Food',
  web_fetch_timeout_seconds: '网页访问',
  safe_tools_enabled: '安全工具',
  mystery_setting: '高级/其他',
  advanced_extra: '高级/其他'
};
for (const [key, title] of Object.entries(expectations)) {
  if (groupOf(key) !== title) throw new Error('group inference wrong for ' + key + ': ' + groupOf(key));
}
const advancedGroup = snapshot.groups.find((group) => group.title === '高级/其他');
if (!advancedGroup.fields.some((field) => field.key === 'misc_field_0')) throw new Error('unmapped fields hidden');
const regrouped = service.groupPluginFields(snapshot.schema);
if (regrouped.reduce((sum, group) => sum + group.fields.length, 0) !== fieldCount) throw new Error('regroup lost fields');

const overridden = service.pluginSchemaSnapshot({
  ok: true,
  schema,
  values: {},
  ui_groups: {
    fields: { food_enabled: '自定义美食' },
    groups: [{ id: 'custom_web', title: '网页自定义', keys: ['web_fetch_timeout_seconds'] }]
  }
});
if (overridden.schema.length !== fieldCount) throw new Error('ui_groups override lost fields');
if (overridden.schema.find((field) => field.key === 'food_enabled').groupTitle !== '自定义美食') throw new Error('fields mapping override not applied');
if (overridden.schema.find((field) => field.key === 'web_fetch_timeout_seconds').groupTitle !== '网页自定义') throw new Error('custom group keys override not applied');
if (!overridden.groups.some((group) => group.title === '自定义美食')) throw new Error('custom group not created');
const fieldLevel = service.pluginSchemaSnapshot({ ok: true, schema: { only_field: { type: 'string', group: '字段自带分组' } } });
if (fieldLevel.groups[0].title !== '字段自带分组') throw new Error('field-level group ignored');

if (!service.pluginFilterSchemaFields(snapshot.schema, 'sticker_enabled').some((field) => field.key === 'sticker_enabled')) throw new Error('search by key failed');
if (!service.pluginFilterSchemaFields(snapshot.schema, '回复概率').some((field) => field.key === 'reply_probability')) throw new Error('search by description failed');
if (!service.pluginFilterSchemaFields(snapshot.schema, '0 到 1').some((field) => field.key === 'reply_probability')) throw new Error('search by hint failed');
if (!service.pluginFilterSchemaFields(snapshot.schema, 'food').some((field) => field.groupTitle === 'Food')) throw new Error('search by group title failed');
if (service.pluginFilterSchemaFields(snapshot.schema, 'no-such-token').length !== 0) throw new Error('no-match should be empty');
const searchText = service.pluginFieldSearchText(snapshot.schema.find((field) => field.key === 'web_fetch_timeout_seconds'));
if (!searchText.includes('网页访问') || !searchText.includes('web_fetch_timeout_seconds')) throw new Error('search text incomplete');

const changed = service.pluginFieldValueChanged;
if (!changed({ type: 'bool' }, false, true)) throw new Error('bool modified not detected');
if (changed({ type: 'bool' }, true, true)) throw new Error('bool equal flagged modified');
if (!changed({ type: 'list' }, ['a'], ['a', 'b'])) throw new Error('list modified not detected');
if (changed({ type: 'list' }, ['a', 'b'], ['a', 'b'])) throw new Error('list equal flagged modified');
if (!changed({ type: 'int' }, 3, 2)) throw new Error('int modified not detected');
if (changed({ type: 'int' }, '3', 3)) throw new Error('numeric text should equal number');
if (!changed({ type: 'string' }, 'x', '')) throw new Error('string modified not detected');

const values = {};
for (const field of snapshot.schema) values[field.key] = field.hasDefault ? field.default : '';
(async () => {
  await service.save('bot-1', 'qqbot_enhancer', values);
  const call = calls[calls.length - 1];
  if (call.path !== '/api/instances/bot-1/plugins/qqbot_enhancer/config') throw new Error('save path changed');
  if (call.options.method !== 'POST') throw new Error('save method changed');
  if (Object.keys(call.options.body.values).length !== fieldCount) throw new Error('save dropped fields');
  console.log('plugin-config-grouping PASS');
})().catch((error) => { console.error(error); process.exit(1); });

            """
        )
        js = (
            js.replace("__API_JS__", json.dumps(str(API_JS)))
            .replace("__PLUGIN_CONFIG_JS__", json.dumps(str(PLUGIN_CONFIG_JS)))
        )
        self._run_node(js)

    def test_instance_settings_component_lifecycle_and_busy_guard(self) -> None:
        self._run_node(
            textwrap.dedent(
                f'''
                'use strict';
                const fs = require('fs');
                global.window = globalThis;
                function makeElement(id) {{
                  return {{
                    id, hidden: id === 'instance-settings-overlay' ? true : false, disabled: false, textContent: '',
                    scrollTop: 0, isConnected: true, _attrs: {{}}, _listeners: {{}}, children: [],
                    addEventListener(type, callback) {{ (this._listeners[type] ||= []).push(callback); }},
                    setAttribute(name, value) {{ this._attrs[name] = String(value); }},
                    getAttribute(name) {{ return this._attrs[name] ?? null; }},
                    focus() {{ this.focused = true; }},
                    replaceChildren(...children) {{ this.children = children; }},
                    appendChild(child) {{ this.children.push(child); return child; }}
                  }};
                }}
                const elements = {{
                  'instance-settings-overlay': makeElement('instance-settings-overlay'),
                  'instance-settings-modal': makeElement('instance-settings-modal'),
                  'instance-settings-body': makeElement('instance-settings-body'),
                  'instance-settings-title': makeElement('instance-settings-title'),
                  'instance-settings-subtitle': makeElement('instance-settings-subtitle'),
                  'instance-settings-close': makeElement('instance-settings-close')
                }};
                global.document = {{ getElementById(id) {{ return elements[id] || null; }} }};
                eval(fs.readFileSync({json.dumps(str(INSTANCE_SETTINGS_JS))}, 'utf8'));
                let instance = {{ id: 'bot-1', name: '测试机器人' }};
                const trigger = {{ isConnected: true, _attrs: {{}}, setAttribute(name, value) {{ this._attrs[name] = String(value); }}, focus() {{ this.focused = true; }} }};
                let lastVisibility = null;
                const component = window.QQBotStudio.components.instanceSettings.createInstanceSettings({{
                  getInstance: (id) => id === instance.id ? instance : null,
                  renderSections: (value) => [{{ section: value.name, node: true }}],
                  subtitleText: (value) => `实例 ${{value.name}} · ${{value.id}}`,
                  findTrigger: () => trigger,
                  isBlocked: () => false,
                  onVisibilityChange: (open) => {{ lastVisibility = open; }}
                }});
                component.mount();
                if (!elements['instance-settings-overlay'].hidden) throw new Error('overlay should start hidden');
                component.open(instance, trigger);
                if (elements['instance-settings-overlay'].hidden) throw new Error('open failed');
                if (!elements['instance-settings-title'].textContent.includes('测试机器人')) throw new Error('title missing');
                if (elements['instance-settings-body'].children.length !== 1) throw new Error('content callback not used');
                if (lastVisibility !== true) throw new Error('visibility callback missing');
                if (trigger._attrs['aria-expanded'] !== 'true') throw new Error('aria-expanded true missing');
                component.beginBusy();
                if (component.close() !== false) throw new Error('busy close should be blocked');
                if (elements['instance-settings-overlay'].hidden) throw new Error('busy close hid overlay');
                component.endBusy();
                if (!component.close()) throw new Error('close after busy failed');
                if (!elements['instance-settings-overlay'].hidden) throw new Error('overlay did not hide');
                if (trigger._attrs['aria-expanded'] !== 'false') throw new Error('aria-expanded false missing');
                component.open(instance, trigger);
                instance = {{ id: 'bot-1', name: '重命名机器人' }};
                component.refresh();
                if (!elements['instance-settings-title'].textContent.includes('重命名机器人')) throw new Error('refresh did not update title');
                elements['instance-settings-overlay']._listeners.click[0]({{ target: elements['instance-settings-overlay'] }});
                if (!elements['instance-settings-overlay'].hidden) throw new Error('overlay click did not close');
                console.log('instance-settings-component PASS');
                '''
            )
        )


if __name__ == "__main__":
    unittest.main()
