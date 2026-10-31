"""Static and extracted-logic checks for the cross-platform setup wizard."""

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
INDEX = STATIC / "index.html"
APP = STATIC / "app.js"
STYLES = STATIC / "styles.css"


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    quote = ""
    escaped = False
    template_depth = 0
    index = brace
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            elif quote == "`" and char == "$" and index + 1 < len(source) and source[index + 1] == "{":
                template_depth += 1
        else:
            if char in ("'", '"', "`"):
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[start:index + 1]
        index += 1
    raise AssertionError(f"could not extract function {name}")


class WizardPlatformStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index_html = INDEX.read_text(encoding="utf-8")
        cls.app_js = APP.read_text(encoding="utf-8")
        cls.styles_css = STYLES.read_text(encoding="utf-8")
        cls.functions = "\n\n".join(
            _extract_function(cls.app_js, name)
            for name in (
                "getPlatformInfo",
                "evaluateRequirements",
                "getOsRequirementConfig",
                "getRequirementCopy",
                "applyPlatformCopy",
            )
        )

    def _run_node(self, body: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        prelude = r'''
        const stateChanges = {};
        const STEP_META = { env: { title: '', subtitle: '', index: 0 } };
        const app = { currentStep: 'env', platform: { kind: 'unknown' } };
        function setText(id, text) { stateChanges[id] = String(text); }
        function updatePageCopy(step) {
          stateChanges['page-title'] = STEP_META[step].title;
          stateChanges['page-subtitle'] = STEP_META[step].subtitle;
        }
        function asRecord(value) {
          return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
        }
        function textValue(value, fallback = '') {
          if (typeof value === 'string') return value;
          if (typeof value === 'number' && Number.isFinite(value)) return String(value);
          return fallback;
        }
        function boolOrUndefined(value) {
          if (typeof value === 'boolean') return value;
          if (value === 1 || value === 0) return value === 1;
          return undefined;
        }
        '''
        script = prelude + self.functions + "\n" + textwrap.dedent(body) + "\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "platform-test.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["node", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_windows_windows10_11_docker_state_is_ready(self) -> None:
        data = self._run_node(
            r'''
            const osName = 'Windows-11-10.0.22631-SP0';
            const requirements = {
              os: osName,
              docker_installed: true,
              docker_running: true,
              compose_installed: true,
              permission_ok: true
            };
            const platform = getPlatformInfo(osName);
            const result = evaluateRequirements(requirements);
            if (platform.kind !== 'windows') throw new Error('Windows platform not detected');
            if (!result.ready || result.failedCount !== 0) throw new Error('Windows 10/11 + Docker Desktop should be ready');
            if (result.checks.some((check) => /Ubuntu/i.test(check.label))) throw new Error('Windows still requires Ubuntu');
            const copy = getRequirementCopy(platform);
            const osCard = getOsRequirementConfig(platform, osName);
            console.log(JSON.stringify({ kind: platform.kind, ready: result.ready, copy, osCard }));
            '''
        )
        self.assertEqual(data["kind"], "windows")
        self.assertTrue(data["ready"])
        self.assertIn("Windows 10/11", data["osCard"]["title"])

    def test_windows_copy_has_no_ubuntu_apt_or_sudo(self) -> None:
        data = self._run_node(
            r'''
            const osName = 'Windows 10 Pro 10.0.19045';
            const platform = getPlatformInfo(osName);
            applyPlatformCopy(platform);
            const copy = getRequirementCopy(platform);
            const osCard = getOsRequirementConfig(platform, osName);
            console.log(JSON.stringify({ platform, copy, osCard, changes: stateChanges }));
            '''
        )
        rendered = json.dumps(data, ensure_ascii=False)
        self.assertNotRegex(rendered, r"(?i)\bapt(?:-get)?\b")
        self.assertNotRegex(rendered, r"(?i)\bsudo\b")
        self.assertNotIn("Ubuntu", rendered)
        self.assertIn("Windows PowerShell", rendered)
        self.assertIn("Docker Desktop", rendered)
        self.assertIn("Linux containers", rendered)
        self.assertIn("Docker Desktop", data["changes"]["env-step-hint"])

    def test_ubuntu_requirements_and_commands_regress(self) -> None:
        data = self._run_node(
            r'''
            const osName = 'Ubuntu 22.04.4 LTS';
            const requirements = {
              os: osName,
              docker_installed: true,
              docker_running: true,
              compose_installed: true,
              permission_ok: true
            };
            const platform = getPlatformInfo(osName);
            const result = evaluateRequirements(requirements);
            const copy = getRequirementCopy(platform);
            const osCard = getOsRequirementConfig(platform, osName);
            applyPlatformCopy(platform);
            console.log(JSON.stringify({ platform, result, copy, osCard, changes: stateChanges }));
            '''
        )
        self.assertEqual(data["platform"]["kind"], "ubuntu")
        self.assertTrue(data["result"]["ready"])
        self.assertEqual(data["osCard"]["tone"], "ready")
        self.assertIn("sudo apt-get", data["copy"]["docker"]["command"])
        self.assertIn("Ubuntu", data["changes"]["page-subtitle"])

    def test_other_linux_blocks_but_unknown_does_not_block_confirmed_docker(self) -> None:
        data = self._run_node(
            r'''
            const allDocker = {
              docker_installed: true,
              docker_running: true,
              compose_installed: true,
              permission_ok: true
            };
            const debian = evaluateRequirements({ ...allDocker, os: 'Debian GNU/Linux 12' });
            const debianCard = getOsRequirementConfig(getPlatformInfo('Debian GNU/Linux 12'), 'Debian GNU/Linux 12');
            const unknown = evaluateRequirements({ ...allDocker, os: '未知系统' });
            const unknownCard = getOsRequirementConfig(getPlatformInfo('未知系统'), '未知系统');
            applyPlatformCopy(getPlatformInfo('未知系统'));
            console.log(JSON.stringify({ debian, debianCard, unknown, unknownCard, changes: stateChanges }));
            '''
        )
        self.assertEqual(data["debian"]["platform"]["kind"], "linux-other")
        self.assertFalse(data["debian"]["ready"])
        self.assertGreater(data["debian"]["failedCount"], 0)
        self.assertEqual(data["debianCard"]["tone"], "warning")
        self.assertEqual(data["unknown"]["platform"]["kind"], "unknown")
        self.assertTrue(data["unknown"]["ready"])
        self.assertTrue(data["unknown"]["platformUnknown"])
        self.assertEqual(data["unknownCard"]["tone"], "unknown")
        self.assertIn("系统未识别", " ".join(data["changes"].values()))

    def test_wizard_static_copy_and_ids(self) -> None:
        for element_id in (
            "page-title", "page-subtitle", "env-step-hint", "env-heading",
            "env-intro", "env-platform-note", "env-help-title", "env-help-text",
            "requirements-grid", "requirements-summary", "env-next",
        ):
            self.assertIn(f'id="{element_id}"', self.index_html)
        for token in (
            "getPlatformInfo",
            "requirements.os",
            "Windows 10/11",
            "Docker Desktop",
            "Linux containers",
            "Windows PowerShell",
            "winget install --id Docker.DockerDesktop",
            "部署时本地服务会再次校验",
            "evaluateRequirements",
        ):
            self.assertIn(token, self.app_js)
        self.assertNotIn("value.includes('Windows')", self.app_js)
        self.assertNotIn("value: /ubuntu/i.test(osValue)", self.app_js)
        self.assertIn('<script src="/services/api.js"></script>', self.index_html)
        self.assertLess(self.index_html.index('/services/api.js'), self.index_html.index('app.js'))
        self.assertIn("createApiClient", self.app_js)
        self.assertRegex(
            self.app_js,
            r"else if \(summary\.ready\) \{[\s\S]{0,700}?nextButton\.disabled = false;",
        )

    def test_wizard_prompt_is_read_only_and_template_managed_elsewhere(self) -> None:
        for element_id in (
            "prompt-preview", "prompt-help", "prompt-error",
            "deployment-prompt-preview",
        ):
            self.assertIn(f'id="{element_id}"', self.index_html)
        self.assertIn("提示词模板请在控制台提示词模板库管理，向导不可修改", self.index_html)
        for forbidden_markup in (
            '<textarea id="prompt"',
            'id="restore-prompt"',
            'id="prompt-count"',
            'id="edit-prompt"',
            'id="persona-form"',
            '<textarea id="persona-input"',
            'id="persona-save"',
            'id="persona-cancel"',
        ):
            self.assertNotIn(forbidden_markup, self.index_html)
        for forbidden_js in (
            "/api/persona",
            "handlePersonaSubmit",
            "personaBusy",
            "persona-form",
            "persona-input",
            "restore-prompt",
            "edit-prompt",
            "updatePromptCount",
        ):
            self.assertNotIn(forbidden_js, self.app_js)
        for token in (
            "currentWizardPrompt",
            "setWizardPrompt",
            "prompt-preview",
            "textContent",
            "body: payload",
            "prompt,",
            "setWizardPrompt(deployment.prompt || DEFAULT_PROMPT)",
            "setDefaultPrompt();",
        ):
            self.assertIn(token, self.app_js)
        self.assertIn(".prompt-preview--readonly", self.styles_css)
        readonly_block = self.styles_css[
            self.styles_css.index(".prompt-preview--readonly {"):
            self.styles_css.index("}", self.styles_css.index(".prompt-preview--readonly {")) + 1
        ]
        self.assertIn("white-space: pre-wrap", readonly_block)
        self.assertIn("overflow: auto", readonly_block)
        self.assertIn("max-height: 320px", readonly_block)
        self.assertIn("@media (max-width: 700px)", self.styles_css)
        self.assertNotIn(".persona-", self.styles_css)

    def _run_wizard_prompt_validation(self, prompt_text: str) -> dict:
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        functions = "\n\n".join(
            _extract_function(self.app_js, name)
            for name in ("currentWizardPrompt", "validateConfigForm")
        )
        prelude = r"""
        const elements = {
          'qq-account': { value: '123456789' },
          'deepseek-key': { value: 'sk-test' },
          'prompt-preview': { textContent: '', setAttribute() {}, removeAttribute() {}, focus() {} },
          'accept-risk': { checked: true },
          'qq-account-error': {},
          'deepseek-key-error': {},
          'prompt-error': {},
          'risk-error': {},
          'config-heading': { scrollIntoView() {} },
        };
        function $(id) { return elements[id] || null; }
        function clearFormError() {}
        function clearFieldError(input, errorElement) {
          if (input && input.removeAttribute) input.removeAttribute('aria-invalid');
          if (errorElement) { errorElement.textContent = ''; errorElement.hidden = true; }
        }
        function setFieldError(input, errorElement, message) {
          if (input && input.setAttribute) input.setAttribute('aria-invalid', 'true');
          if (errorElement) { errorElement.textContent = message; errorElement.hidden = false; }
        }
        function showToast() {}
        elements['prompt-preview'].textContent = __PROMPT_TEXT__;
        const result = validateConfigForm();
        console.log(JSON.stringify({ result, promptError: elements['prompt-error'].textContent || '' }));
        """.replace("__PROMPT_TEXT__", json.dumps(prompt_text, ensure_ascii=False))
        script = prelude + "\n" + functions + "\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "prompt-validation-test.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["node", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_wizard_deploy_payload_uses_readonly_prompt_and_preserves_newlines(self) -> None:
        prompt = "\n第一行\n第二行\n"
        data = self._run_wizard_prompt_validation(prompt)
        self.assertIsNotNone(data["result"])
        self.assertEqual(data["result"]["prompt"], prompt)
        self.assertEqual(data["result"]["qq_account"], "123456789")
        self.assertEqual(data["result"]["deepseek_api_key"], "sk-test")
        self.assertTrue(data["result"]["accept_risk"])

    def test_wizard_prompt_undetermined_shows_error_instead_of_edit(self) -> None:
        data = self._run_wizard_prompt_validation("   \n  ")
        self.assertIsNone(data["result"])
        self.assertIn("无法确定部署提示词", data["promptError"])
        self.assertIn("控制台提示词模板库", data["promptError"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_app_javascript_syntax(self) -> None:
        subprocess.run(["node", "--check", str(APP)], check=True)


if __name__ == "__main__":
    unittest.main()
