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
                const api = window.QQBotStudio.services.api.createApiClient({{
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
