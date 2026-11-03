/* QQ Bot Studio — 多实例控制台
 * 纯原生 JavaScript，无第三方依赖。所有动态数据通过 textContent / DOM API 渲染。
 */
(() => {
  'use strict';

  const TOKEN_RAW = typeof window.QB_TOKEN === 'string' ? window.QB_TOKEN : '';
  const QB_TOKEN = TOKEN_RAW && TOKEN_RAW !== '{{TOKEN}}' ? TOKEN_RAW : '';
  const AUTO_REFRESH_INTERVAL_MS = 30 * 60 * 1000;

  const studioNamespace = window.QQBotStudio || {};
  const apiNamespace = studioNamespace.services && studioNamespace.services.api;
  if (!apiNamespace || typeof apiNamespace.createApiClient !== 'function') {
    throw new Error('缺少共享 API service：请确认 /services/api.js 已加载。');
  }
  const apiClient = apiNamespace.createApiClient({ token: QB_TOKEN });
  const ApiError = apiClient.ApiError;
  const apiRequest = apiClient.request;
  const requestWithFallback = apiClient.requestWithFallback;
  const isMissingEndpointError = apiClient.isMissingEndpointError;
  const { asRecord, textValue, safeHttpUrl } = apiNamespace;
  const boolOrUndefined = apiNamespace.createBoolParser(
    ['true', '1', 'yes', 'on', 'running', 'deployed', 'ok', 'enabled', 'active', 'available', 'connected'],
    ['false', '0', 'no', 'off', 'stopped', 'not_deployed', 'error', 'disabled', 'inactive', 'unavailable', 'disconnected']
  );

  const pluginConfigNamespace = studioNamespace.services && studioNamespace.services.pluginConfig;
  if (!pluginConfigNamespace || typeof pluginConfigNamespace.createPluginConfigService !== 'function') {
    throw new Error('缺少插件配置 service：请确认 /services/plugin-config.js 已加载。');
  }
  const pluginConfigService = pluginConfigNamespace.createPluginConfigService({ api: apiClient });
  const sanitisePluginText = pluginConfigService.sanitisePluginText;
  const pluginConfigUnsupportedMessage = pluginConfigService.pluginConfigUnsupportedMessage;
  const pluginValueLookup = pluginConfigService.pluginValueLookup;
  const pluginListText = pluginConfigService.pluginListText;
  const pluginInputText = pluginConfigService.pluginInputText;
  const pluginDefaultText = pluginConfigService.pluginDefaultText;

  const lastKnownAiToolsValues = new Map();
  const lastKnownMemoryModes = new Map();

  const state = {
    instances: [],
    settings: null,
    loading: false,
    loadedOnce: false,
    actionInstanceId: '',
    creating: false,
    deleteArmedId: '',
    deletingInstanceId: '',
    lastSyncedAt: 0,
    pollTimer: null,
    updateBusy: false,
    updateChecked: false,
    updateArmed: false,
    updateCanApply: false,
    updateInfo: null,
    updateOpen: false,
    updateChannel: 'stable',
    updateStatus: null,
    updateVersions: null,
    updatePending: null,
    optionalSupport: {
      plugins: null,
      memory: null,
      memoryPolicy: null,
      stats: null
    },
    pluginConfig: {
      open: false,
      loading: false,
      saving: false,
      instanceId: '',
      instanceName: '',
      pluginId: '',
      pluginName: '',
      source: '',
      restartRequired: false,
      apiAvailable: true,
      requestSeq: 0,
      entries: [],
      groups: [],
      groupsById: new Map(),
      groupSections: new Map(),
      groupButtons: new Map(),
      ui: { byPlugin: {} }
    },
    promptManager: {
      open: false,
      loading: false,
      busy: false,
      unsupported: false,
      instanceId: '',
      prompts: [],
      activeId: '',
      editorMode: '',
      editorProfileId: '',
      requestSeq: 0,
      actionSeq: 0,
      deleteArmedId: '',
      profilesAvailable: false,
      profilesInstanceId: '',
      trigger: null
    },
    globalResourceStats: null,
    globalResourceStatus: 'unknown',
    globalResourceError: '',
    globalResourcePartial: false,
  };

  const $ = (id) => document.getElementById(id);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function setText(id, text) {
    const element = $(id);
    if (element) element.textContent = text;
  }

  const instanceSettingsNamespace = studioNamespace.components && studioNamespace.components.instanceSettings;
  if (!instanceSettingsNamespace || typeof instanceSettingsNamespace.createInstanceSettings !== 'function') {
    throw new Error('缺少实例设置组件：请确认 /components/instance-settings.js 已加载。');
  }
  const instanceSettings = instanceSettingsNamespace.createInstanceSettings({
    getInstance: (id) => state.instances.find((item) => item.id === id),
    renderSections: (instance) => [
      buildPluginSection(instance),
      buildAiToolsSection(instance),
      buildPromptManagerSection(instance),
      buildMemorySection(instance),
      buildMemoryPolicySection(instance),
      buildStatsSection(instance)
    ],
    subtitleText: (instance) => `实例 ${instance.name || instance.id} · ID ${instance.id || '缺失'} · ${v3SummaryText(instance)}`,
    findTrigger: (id) => {
      const card = findInstanceCard(id);
      return card ? card.querySelector('.instance-settings-btn') : null;
    },
    isBlocked: () => state.pluginConfig.open || state.promptManager.open,
    onVisibilityChange: () => syncConsoleModalLock()
  });

  function formatError(error, fallback = '操作失败，请稍后重试。') {
    if (error instanceof ApiError) {
      if (error.status === 401 || error.status === 403) {
        return '访问令牌无效或已过期，请回到启动器重新打开本页面。';
      }
      if (error.status === 404) return error.message || '本地服务未找到对应接口。';
      if (error.status >= 500) return error.message || '本地服务处理请求时出错，请查看启动器终端。';
      return error.message || fallback;
    }
    if (error instanceof TypeError) return '无法连接本地服务，请确认本地启动器仍在运行。';
    return error && typeof error.message === 'string' && error.message ? error.message : fallback;
  }

  function instancePath(path, instanceId) {
    try {
      const url = new URL(path, window.location.href);
      url.searchParams.set('instance', instanceId);
      return `${url.pathname}${url.search}`;
    } catch (error) {
      const separator = path.includes('?') ? '&' : '?';
      return `${path}${separator}instance=${encodeURIComponent(instanceId)}`;
    }
  }

  function wizardUrl(instanceId) {
    return `/wizard?instance=${encodeURIComponent(instanceId)}`;
  }

  function makeIcon(symbolId) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const use = document.createElementNS(ns, 'use');
    use.setAttribute('href', `#${symbolId}`);
    svg.appendChild(use);
    return svg;
  }

  function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function iconText(iconId, text) {
    const fragment = document.createDocumentFragment();
    fragment.appendChild(makeIcon(iconId));
    fragment.appendChild(document.createTextNode(text));
    return fragment;
  }

  function showToast(message, tone = 'info', timeout = 4400) {
    const region = $('console-toast-region');
    if (!region || !message) return;
    const toast = makeElement('div', `toast toast--${tone}`);
    toast.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    const iconId = tone === 'success' ? 'c-i-check'
      : tone === 'error' ? 'c-i-alert'
        : tone === 'warning' ? 'c-i-alert' : 'c-i-info';
    const icon = makeIcon(iconId);
    icon.classList.add('toast-icon');
    toast.appendChild(icon);
    toast.appendChild(makeElement('span', 'toast-text', message));
    region.appendChild(toast);
    window.setTimeout(() => {
      if (!toast.isConnected) return;
      toast.classList.add('is-leaving');
      window.setTimeout(() => toast.remove(), 220);
    }, timeout);
  }

  function setPill(element, text, tone) {
    if (!element) return;
    element.textContent = text;
    element.className = `pill pill--${tone}`;
  }

  function setConsoleError(message) {
    const box = $('console-error');
    if (!box) return;
    box.hidden = !message;
    if (message) $('console-error-text').textContent = message;
  }

  function formatCreatedAt(value) {
    const raw = textValue(value, '').trim();
    if (!raw) return '创建时间未知';
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return `创建于 ${raw}`;
    try {
      return `创建于 ${new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
      }).format(date)}`;
    } catch (error) {
      return `创建于 ${date.toLocaleString('zh-CN')}`;
    }
  }

  function normaliseInstance(raw) {
    const instance = asRecord(raw);
    const urls = asRecord(instance.urls);
    return {
      id: textValue(instance.id, ''),
      name: textValue(instance.name, '') || '未命名实例',
      created_at: textValue(instance.created_at, ''),
      deployed: boolOrUndefined(instance.deployed) === true,
      running: boolOrUndefined(instance.running),
      qq_account: textValue(instance.qq_account, ''),
      active_job_id: textValue(instance.active_job_id, ''),
      active_job_status: textValue(instance.active_job_status, ''),
      urls: {
        astrbot_webui: textValue(urls.astrbot_webui || urls.astrbot, ''),
        snowluma_webui: textValue(urls.snowluma_webui || urls.snowluma, ''),
        novnc: textValue(urls.novnc || urls.no_vnc, '')
      },
      memory_mode_hint: normaliseMemoryMode(instance.memory_mode || asRecord(instance.memory).mode),
      state_error: textValue(instance.state_error, ''),
      v3: {
        plugins: emptyPluginState(),
        memory: emptyMemoryState(instance.memory_mode || asRecord(instance.memory).mode),
        memoryPolicy: emptyMemoryPolicyState(),
        stats: emptyStatsState()
      }
    };
  }

  function mergeInstanceState(instance, raw) {
    const root = asRecord(raw);
    if (root.ok === false) throw new ApiError(textValue(root.message, '实例状态读取失败。'));
    const deployment = asRecord(root.deployment);
    const stateUrls = asRecord(deployment.urls);
    const mergedState = boolOrUndefined(deployment.deployed);
    return {
      ...instance,
      deployed: mergedState === undefined ? instance.deployed : mergedState,
      running: boolOrUndefined(deployment.running) ?? instance.running,
      qq_account: textValue(deployment.qq_account, instance.qq_account),
      active_job_id: textValue(deployment.active_job_id, instance.active_job_id),
      active_job_status: textValue(deployment.active_job_status, instance.active_job_status),
      urls: {
        astrbot_webui: textValue(stateUrls.astrbot_webui, instance.urls.astrbot_webui),
        snowluma_webui: textValue(stateUrls.snowluma_webui, instance.urls.snowluma_webui),
        novnc: textValue(stateUrls.novnc, instance.urls.novnc)
      },
      memory_mode_hint: normaliseMemoryMode(deployment.memory_mode || root.memory_mode || instance.memory_mode_hint),
      state_error: textValue(root.status_error, '')
    };
  }

  function jobStatusMeta(instance) {
    const status = textValue(instance.active_job_status, '').trim().toLowerCase();
    if (['success', 'succeeded', 'complete', 'completed', 'done', 'ok'].includes(status)) {
      return { label: '部署完成', tone: 'success' };
    }
    if (['error', 'failed', 'failure', 'interrupted', 'cancelled', 'canceled', 'aborted', 'paused'].includes(status)) {
      return { label: '部署中断', tone: 'danger' };
    }
    if (['running', 'pending', 'queued', 'starting', 'in_progress', 'processing'].includes(status)) {
      return { label: '部署进行中', tone: 'warning' };
    }
    return { label: '有部署任务', tone: 'warning' };
  }

  function setRefreshBusy(busy) {
    const button = $('console-refresh');
    const label = $('console-refresh-label');
    if (!button) return;
    button.disabled = busy;
    button.classList.toggle('is-loading', busy);
    button.setAttribute('aria-busy', busy ? 'true' : 'false');
    button.title = busy ? '正在刷新；自动刷新每 30 分钟' : '手动刷新；自动刷新每 30 分钟';
    button.setAttribute('aria-label', '手动刷新；自动刷新每 30 分钟');
    if (label) label.textContent = busy ? '刷新中…' : '手动刷新';
  }

  function setCreateBusy(busy) {
    state.creating = busy;
    const button = $('create-instance-submit');
    const label = $('create-instance-submit-text');
    const nameInput = $('new-instance-name');
    if (button) {
      button.disabled = busy;
      button.classList.toggle('is-loading', busy);
    }
    if (label) label.textContent = busy ? '创建中…' : '创建并打开向导';
    if (nameInput) nameInput.disabled = busy;
  }

  function setUpdateLabel(label, disabled = false) {
    const button = $('console-update');
    const text = $('console-update-label');
    if (button) button.disabled = disabled;
    if (text) text.textContent = label;
  }

  function updateChannelText(channel) {
    return channel === 'test' ? '测试版' : '稳定版';
  }

  function setUpdatePanelVisible(visible) {
    const overlay = $('update-overlay');
    if (!overlay) return;
    overlay.hidden = !visible;
    state.updateOpen = visible;
    if (visible) {
      const modal = $('update-modal');
      if (modal) modal.focus();
    }
  }

  function showUpdateError(message) {
    const element = $('update-error');
    if (!element) return;
    element.textContent = message || '';
    element.hidden = !message;
  }

  function showUpdateStatus(message) {
    const element = $('update-status');
    if (!element) return;
    element.textContent = message || '';
    element.hidden = !message;
  }

  function setUpdateWarning(message) {
    const element = $('update-warning');
    if (!element) return;
    element.textContent = message || '';
    element.hidden = !message;
  }

  function clearUpdateConfirm() {
    state.updatePending = null;
    const box = $('update-confirm');
    if (box) box.hidden = true;
    const text = $('update-confirm-text');
    if (text) text.textContent = '';
  }

  function armUpdateAction(label, warning, run) {
    if (state.updateBusy) return;
    state.updatePending = { label, run };
    const box = $('update-confirm');
    const text = $('update-confirm-text');
    const ok = $('update-confirm-ok');
    if (text) text.textContent = warning;
    if (ok) ok.textContent = label;
    if (box) box.hidden = false;
    window.setTimeout(() => {
      if (state.updatePending && state.updatePending.label === label) clearUpdateConfirm();
    }, 20000);
  }

  function updateBlockedReason() {
    const status = state.updateStatus || {};
    if (status.busy === true) return '已有更新/回滚任务正在运行，请等待它完成。';
    if (status.dirty === true) return '代码目录有未提交的修改，更新与回滚已禁用；请先在服务器处理这些修改。';
    return '';
  }

  function setUpdateBusyUi(busy) {
    const stable = $('update-apply-stable');
    if (stable) stable.disabled = busy || Boolean(updateBlockedReason());
    const tabStable = $('update-tab-stable');
    const tabTest = $('update-tab-test');
    if (tabStable) tabStable.disabled = busy;
    if (tabTest) tabTest.disabled = busy;
    const ok = $('update-confirm-ok');
    if (ok) ok.disabled = busy;
  }

  function renderUpdateStatus(status) {
    const current = asRecord(status.current);
    const tag = textValue(current.tag, '');
    const version = textValue(current.version, '—');
    setText('update-current-version', tag ? `${version} (${tag})` : version);
    setText('update-current-commit', textValue(current.commit, '—'));
    setText('update-current-major', `v${textValue(current.major, '?')}.x`);
    setText('update-current-channel', updateChannelText(textValue(current.channel, 'stable')));
    setText('update-current-dirty', status.dirty === true ? '有未提交修改' : '干净');
    setUpdateWarning(updateBlockedReason());
  }

  function makeUpdateVersionItem(options) {
    const item = makeElement('li', 'update-version');
    if (options.current) item.classList.add('is-current');
    const main = makeElement('div', 'update-version-main');
    main.appendChild(makeElement('span', 'update-version-name', options.name));
    main.appendChild(makeElement('span', 'update-version-meta', options.meta));
    if (options.note) main.appendChild(makeElement('span', 'update-version-note', options.note));
    item.appendChild(main);
    const button = makeElement('button', 'btn btn-small btn-quiet', options.buttonLabel);
    button.type = 'button';
    button.disabled = Boolean(options.disabled);
    if (!button.disabled && typeof options.onClick === 'function') {
      button.addEventListener('click', options.onClick);
    }
    item.appendChild(button);
    return item;
  }

  function renderUpdateVersions(data) {
    const list = $('update-versions');
    const empty = $('update-versions-empty');
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    const versions = Array.isArray(data.versions) ? data.versions : [];
    if (empty) empty.hidden = versions.length > 0;
    const blocked = Boolean(updateBlockedReason());
    const currentMajor = textValue(asRecord(data.current).major, '');
    versions.forEach((raw) => {
      const version = asRecord(raw);
      const tag = textValue(version.tag, '');
      const name = `v${textValue(version.version, tag)}`;
      const meta = [textValue(version.date, ''), textValue(version.commit, ''), textValue(version.subject, '')]
        .filter(Boolean)
        .join(' · ');
      const sameMajor = version.same_major === true;
      const current = version.current === true;
      let note = '';
      if (current) note = '当前版本';
      else if (!sameMajor) note = `跨大版本（当前 v${currentMajor}.x 不支持回滚）`;
      let buttonLabel = '回滚';
      if (current) buttonLabel = '当前版本';
      else if (!sameMajor) buttonLabel = '不可回滚';
      list.appendChild(
        makeUpdateVersionItem({
          name,
          meta,
          note,
          current,
          buttonLabel,
          disabled: current || !sameMajor || blocked || !tag,
          onClick: () => armUpdateAction(
            `回滚到 ${tag}`,
            `确认回滚到 ${tag}？会先备份代码、实例配置与密钥，再切换代码并重启管理服务；实例数据库和数据目录不会被覆盖。`,
            () => runUpdateAction('/api/update/rollback', {
              target: tag,
              allow_schema_downgrade: Boolean($('update-allow-schema') && $('update-allow-schema').checked)
            })
          )
        })
      );
    });
  }

  function renderUpdateTestRefs(data) {
    const list = $('update-test-refs');
    const empty = $('update-test-empty');
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    const refs = Array.isArray(data.versions) ? data.versions : [];
    if (empty) empty.hidden = refs.length > 0;
    const blocked = Boolean(updateBlockedReason());
    refs.forEach((raw) => {
      const entry = asRecord(raw);
      const refName = textValue(entry.ref, '').replace(/^origin\//, '');
      const isTag = textValue(entry.kind, 'branch') === 'tag';
      const target = isTag ? textValue(entry.tag, '') : refName;
      const meta = [textValue(entry.date, ''), textValue(entry.commit, ''), textValue(entry.subject, '')]
        .filter(Boolean)
        .join(' · ');
      list.appendChild(
        makeUpdateVersionItem({
          name: textValue(entry.version, target),
          meta,
          note: entry.current === true ? '当前版本' : '测试通道',
          current: entry.current === true,
          buttonLabel: entry.current === true ? '当前版本' : '切换到测试版',
          disabled: entry.current === true || blocked || !target,
          onClick: () => armUpdateAction(
            `切换到 ${target}`,
            `确认切换到测试版本 ${target}？会先备份代码、实例配置与密钥；测试版可能不稳定，实例数据不会被覆盖。`,
            () => runUpdateAction('/api/update/apply', { channel: 'test', test_ref: target })
          )
        })
      );
    });
  }

  function formatHistoryTime(record) {
    const at = textValue(record.at, '');
    if (at) return at.replace('T', ' ').slice(0, 19);
    const stamp = Number(record.time);
    if (!stamp) return '';
    try {
      return new Date(stamp).toLocaleString('zh-CN', { hour12: false });
    } catch (error) {
      return '';
    }
  }

  function historyResultText(result) {
    if (result === 'success') return '成功';
    if (result === 'restored') return '失败（已恢复原版本）';
    return '失败';
  }

  function renderUpdateHistory(records) {
    const list = $('update-history-list');
    const empty = $('update-history-empty');
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    const items = Array.isArray(records) ? records : [];
    if (empty) empty.hidden = items.length > 0;
    items.slice(0, 10).forEach((raw) => {
      const record = asRecord(raw);
      const from = asRecord(record.from);
      const to = asRecord(record.to);
      const fromText = textValue(from.version, textValue(from.commit, '?'));
      const toText = textValue(to.version, textValue(to.commit, '?'));
      const channel = record.action === 'rollback' ? '回滚' : updateChannelText(textValue(record.channel, 'stable'));
      const item = makeElement('li', 'update-history-item');
      const main = makeElement(
        'span',
        'update-history-main',
        `${channel}：${fromText} → ${toText} · ${historyResultText(textValue(record.result, ''))}`
      );
      const message = textValue(record.message, '');
      if (message) main.title = message;
      item.appendChild(main);
      item.appendChild(makeElement('span', 'update-history-meta', formatHistoryTime(record)));
      list.appendChild(item);
    });
  }

  async function loadUpdateVersions(channel) {
    const data = asRecord(await apiRequest(`/api/update/versions?channel=${encodeURIComponent(channel)}`));
    state.updateVersions = data;
    if (channel === 'test') renderUpdateTestRefs(data);
    else renderUpdateVersions(data);
    if (data.fetch_ok === false && data.message) {
      showUpdateStatus(`无法刷新远端版本，列表可能不是最新：${textValue(data.message, '')}`);
    }
    return data;
  }

  async function loadUpdateHistory() {
    const data = asRecord(await apiRequest('/api/update/history'));
    renderUpdateHistory(data.history);
  }

  async function loadUpdatePanelData() {
    const status = asRecord(await apiRequest('/api/update/status'));
    state.updateStatus = status;
    renderUpdateStatus(status);
    setUpdateBusyUi(false);
    await Promise.all([loadUpdateVersions(state.updateChannel), loadUpdateHistory(), checkForUpdate(true)]);
  }

  async function openUpdatePanel() {
    setUpdatePanelVisible(true);
    clearUpdateConfirm();
    showUpdateError('');
    showUpdateStatus('');
    setUpdateBusyUi(false);
    setText('update-subtitle', '正在读取版本信息…');
    try {
      await loadUpdatePanelData();
      setText('update-subtitle', '稳定版跟随正式发布；测试版只允许已登记的分支或预发布标签。');
    } catch (error) {
      const message = formatError(error, '读取版本信息失败。');
      showUpdateError(message);
      setText('update-subtitle', '无法读取版本信息');
      showToast(message, 'error', 6000);
    }
  }

  function closeUpdatePanel() {
    clearUpdateConfirm();
    setUpdatePanelVisible(false);
  }

  async function runUpdateAction(path, body) {
    if (state.updateBusy) return;
    clearUpdateConfirm();
    showUpdateError('');
    state.updateBusy = true;
    setUpdateBusyUi(true);
    showUpdateStatus('正在切换版本；备份和校验可能需要几十秒，请勿关闭页面。');
    try {
      const data = asRecord(await apiRequest(path, { method: 'POST', body: body || {} }));
      if (data.ok === false) throw new ApiError(textValue(data.message, '操作失败。'));
      const message = textValue(data.message, '操作已完成，管理服务即将重启。');
      showUpdateStatus(`${message} 页面将在约 12 秒后刷新。`);
      showToast(message, 'success', 8000);
      window.setTimeout(() => window.location.reload(), 12000);
    } catch (error) {
      const message = formatError(error, '操作失败。');
      showUpdateError(message);
      showUpdateStatus('');
      showToast(message, 'error', 7000);
      state.updateBusy = false;
      setUpdateBusyUi(false);
    }
  }

  async function checkForUpdate(silent = false) {
    if (state.updateBusy) return;
    state.updateBusy = true;
    setUpdateLabel('检查中…', true);
    try {
      const data = asRecord(await apiRequest('/api/update/check'));
      state.updateChecked = true;
      state.updateInfo = data;
      state.updateCanApply = data.ok === true && data.can_update === true;
      state.updateArmed = false;
      if (state.updateCanApply) {
        setUpdateLabel(`一键更新整个项目 (${data.behind || '?'} 个提交)`);
        if (!silent) showToast(data.message || '发现新版本。', 'info', 5000);
      } else {
        setUpdateLabel('检查更新');
        if (!silent) {
          showToast(data.message || (data.ok === false ? '无法检查更新。' : '当前已经是最新版本。'), data.ok === false ? 'warning' : 'success', 5000);
        }
      }
      return data;
    } catch (error) {
      const message = formatError(error, '检查更新失败。');
      setUpdateLabel('检查更新');
      if (!silent) showToast(message, 'error', 6000);
      return null;
    } finally {
      state.updateBusy = false;
      setUpdateBusyUi(false);
      const button = $('console-update');
      if (button) button.disabled = false;
    }
  }

  async function handleUpdateClick() {
    await openUpdatePanel();
  }

  function switchUpdateTab(channel) {
    const next = channel === 'test' ? 'test' : 'stable';
    state.updateChannel = next;
    const stableTab = $('update-tab-stable');
    const testTab = $('update-tab-test');
    const stablePane = $('update-stable-pane');
    const testPane = $('update-test-pane');
    if (stableTab) {
      stableTab.classList.toggle('is-active', next === 'stable');
      stableTab.setAttribute('aria-selected', next === 'stable' ? 'true' : 'false');
    }
    if (testTab) {
      testTab.classList.toggle('is-active', next === 'test');
      testTab.setAttribute('aria-selected', next === 'test' ? 'true' : 'false');
    }
    if (stablePane) stablePane.hidden = next !== 'stable';
    if (testPane) testPane.hidden = next !== 'test';
    clearUpdateConfirm();
    showUpdateError('');
    loadUpdateVersions(next).catch((error) => {
      showUpdateError(formatError(error, '读取版本列表失败。'));
    });
  }

  function formatSyncTime(timestamp) {
    if (!timestamp) return '';
    try {
      return new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false });
    } catch (error) {
      return '';
    }
  }

  function scheduleNextSync(delay = AUTO_REFRESH_INTERVAL_MS) {
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
    if (document.hidden) return;
    state.pollTimer = window.setTimeout(async () => {
      state.pollTimer = null;
      if (document.hidden) return;
      try {
        await loadConsole({ silent: true });
      } finally {
        scheduleNextSync();
      }
    }, delay);
  }

  async function manualRefresh() {
    if (state.loading) return false;
    try {
      return await loadConsole({ silent: false });
    } finally {
      scheduleNextSync(AUTO_REFRESH_INTERVAL_MS);
    }
  }

  function renderSettings() {
    const settings = asRecord(state.settings);
    const host = textValue(settings.bind_host, '').trim();
    const port = textValue(settings.bind_port, '').trim();
    const publicHost = textValue(settings.public_host, '').trim();
    const address = host && port ? `${host}:${port}` : host || '本地服务';
    const serverPill = $('console-bind-host');
    if (serverPill) {
      const span = serverPill.querySelector('span');
      if (span) span.textContent = address;
      serverPill.title = `本地服务监听：${address}${publicHost ? `；公开地址：${publicHost}` : ''}`;
    }

    const firstRun = boolOrUndefined(settings.first_run_complete);
    setPill(
      $('console-first-run'),
      firstRun === true ? '本地服务已初始化' : firstRun === false ? '首次运行未完成' : '初始化状态未知',
      firstRun === true ? 'success' : firstRun === false ? 'warning' : 'neutral'
    );

    let mirrorCount = '';
    if (Array.isArray(settings.mirror_nodes)) mirrorCount = ` · 镜像节点 ${settings.mirror_nodes.length} 个`;
    const syncTime = formatSyncTime(state.lastSyncedAt);
    const syncText = syncTime ? ` · 最后同步 ${syncTime}` : '';
    const status = `本地服务监听：${address}${publicHost ? ` · 公开地址：${publicHost}` : ''}${mirrorCount}${syncText} · 自动刷新：每 30 分钟`;
    const statusElement = $('console-settings-status');
    if (statusElement) statusElement.textContent = status;
  }

  function renderSettingsError(error) {
    const serverPill = $('console-bind-host');
    if (serverPill) {
      const span = serverPill.querySelector('span');
      if (span) span.textContent = '本地服务';
    }
    setPill($('console-first-run'), '初始化状态未知', 'neutral');
    const statusElement = $('console-settings-status');
    if (statusElement) statusElement.textContent = `本地设置读取失败：${formatError(error, '设置接口不可用。')} · 自动刷新：每 30 分钟`;
  }

  function setStats(instances) {
    const runningCount = instances.filter((instance) => instance.running === true).length;
    const jobCount = instances.filter((instance) => Boolean(instance.active_job_id)).length;
    const instanceCount = $('instance-count');
    const running = $('running-count');
    const jobs = $('job-count');
    if (instanceCount) instanceCount.textContent = String(instances.length);
    if (running) running.textContent = String(runningCount);
    if (jobs) jobs.textContent = String(jobCount);
  }

  function makeWebLink(label, rawUrl) {
    const url = safeHttpUrl(rawUrl);
    if (!url) {
      const disabled = makeElement('span', 'instance-web-link is-disabled', label);
      disabled.setAttribute('aria-disabled', 'true');
      disabled.title = '本地服务尚未提供可打开的地址';
      return disabled;
    }
    const link = makeElement('a', 'instance-web-link', label);
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.title = url;
    const icon = makeIcon('c-i-external');
    icon.classList.add('instance-link-icon');
    link.appendChild(icon);
    return link;
  }

  function makeActionButton(instance, action) {
    const labels = { start: '启动', stop: '停止', restart: '重启' };
    const icons = { start: 'c-i-play', stop: 'c-i-stop', restart: 'c-i-restart' };
    const busy = state.actionInstanceId === instance.id;
    const anyBusy = Boolean(state.actionInstanceId);
    let disabled = anyBusy;
    if (!busy) {
      if (action === 'start' && instance.running === true) disabled = true;
      if (action === 'stop' && instance.running === false) disabled = true;
    }
    const button = makeElement('button', 'instance-action-btn', '');
    button.type = 'button';
    button.disabled = disabled;
    button.appendChild(iconText(icons[action], busy ? `${labels[action]}中…` : labels[action]));
    button.setAttribute('aria-label', `${labels[action]}实例 ${instance.name}`);
    button.addEventListener('click', () => void handleInstanceAction(instance, action));
    return button;
  }

  const V3_MEMORY_META = {
    direct: {
      label: 'direct · 省 Token',
      description: '只发送当前消息和系统提示，通常更省 token。'
    },
    astrbot: {
      label: 'astrbot · 共享会话记忆',
      description: '使用 AstrBot 会话历史，群聊按群、私聊按用户共享记忆。'
    }
  };

  function numberOrUndefined(value) {
    if (value === null || value === undefined || value === '') return undefined;
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? number : undefined;
  }

  function pickNumber(source, keys) {
    const record = asRecord(source);
    for (const key of keys) {
      const value = numberOrUndefined(record[key]);
      if (value !== undefined) return value;
    }
    return undefined;
  }

  function formatBytes(value) {
    const number = numberOrUndefined(value);
    if (number === undefined) return '未知';
    if (number === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let scaled = number;
    let unit = 0;
    while (scaled >= 1024 && unit < units.length - 1) {
      scaled /= 1024;
      unit += 1;
    }
    const digits = scaled >= 100 ? 0 : scaled >= 10 ? 1 : 2;
    return `${Number(scaled.toFixed(digits))} ${units[unit]}`;
  }

  function formatPercent(value) {
    const number = numberOrUndefined(value);
    if (number === undefined) return '未知';
    return `${Number(number.toFixed(number >= 10 ? 1 : 2))}%`;
  }

  function formatFileCount(value) {
    const number = numberOrUndefined(value);
    if (number === undefined) return '未知';
    try {
      return `${new Intl.NumberFormat('zh-CN').format(Math.round(number))} 个文件`;
    } catch (error) {
      return `${Math.round(number)} 个文件`;
    }
  }

  function normaliseMemoryMode(value) {
    const mode = textValue(value, '').trim().toLowerCase();
    return mode === 'direct' || mode === 'astrbot' ? mode : '';
  }

  function emptyPluginState() {
    return {
      status: 'idle',
      items: [],
      hasList: false,
      error: '',
      message: '',
      restartRequired: false,
      cached: false,
      updatedAt: 0
    };
  }

  function emptyMemoryState(mode = '') {
    return {
      status: 'idle',
      mode: normaliseMemoryMode(mode),
      options: ['direct', 'astrbot'],
      restartRequired: false,
      message: '',
      error: '',
      configPath: '',
      scope: '',
      updatedAt: 0
    };
  }

  // ------------------------------------------------------------------
  // Per-group long-term memory policy (AstrBot memory.policy)
  // ------------------------------------------------------------------
  const MEMORY_POLICY_WARNING = '长期记忆只对下面列出的群生效；未列出的群和私聊保持 conversation（只用会话历史），既不写入也不读取长期记忆。';

  function emptyMemoryPolicyState() {
    return {
      status: 'idle',
      available: false,
      supported: false,
      saved: false,
      defaultMode: 'conversation',
      privateMode: 'conversation',
      privateExtract: true,
      privateInject: true,
      privateMaxItems: '',
      privateMaxChars: '',
      privateConfigured: true,
      privateInvalid: false,
      privateErrors: [],
      privateEffectiveMode: 'conversation',
      rows: [],
      invalidRows: 0,
      modeOptions: [
        { value: 'conversation', label: '会话记忆' },
        { value: 'long_term', label: '长期记忆' }
      ],
      scopeOptions: [
        { value: 'group', label: '群记忆（读写本群）' },
        { value: 'user', label: '个人记忆（读写发送者本人）' },
        { value: 'group_user', label: '群 + 个人（写本群，读两者）' }
      ],
      limits: { maxItems: [1, 50], maxChars: [80, 8000], maxGroups: 200 },
      warnings: [],
      message: '',
      error: '',
      configPath: '',
      memoryEnabled: null,
      hotReload: false,
      actions: [],
      permissionDenied: false,
      readChannel: '',
      writeChannel: '',
      readable: false,
      writable: false,
      containerRunning: false,
      dirty: false
    };
  }

  function normaliseMemoryPolicyMode(value) {
    const mode = textValue(value, '').trim().toLowerCase();
    return mode === 'long_term' ? 'long_term' : 'conversation';
  }

  function normaliseMemoryPolicyScope(value) {
    const scope = textValue(value, '').trim().toLowerCase();
    return ['group', 'user', 'group_user'].includes(scope) ? scope : 'group';
  }

  function memoryPolicyStringList(raw) {
    return (Array.isArray(raw) ? raw : [])
      .map((item) => textValue(item, '').trim())
      .filter(Boolean);
  }

  function memoryPolicyEffectiveMode(value) {
    // null/absent means "unknown" (e.g. duplicate group ids), which must not
    // be rendered as if the row were effectively long_term.
    if (value === null || value === undefined || value === '') return '';
    return normaliseMemoryPolicyMode(value);
  }

  function memoryPolicyBudgetText(value) {
    if (value === null || value === undefined || value === '') return '';
    const number = Number(value);
    return Number.isFinite(number) ? String(number) : '';
  }

  function memoryPolicyOptions(raw, fallback) {
    const options = [];
    for (const item of Array.isArray(raw) ? raw : []) {
      const record = asRecord(item);
      const value = textValue(record.value, '').trim();
      if (!value) continue;
      options.push({ value, label: textValue(record.label, '') || value });
    }
    return options.length ? options : fallback;
  }

  function memoryPolicyLimits(raw, fallback) {
    const record = asRecord(raw);
    const range = (value, base) => (Array.isArray(value) && value.length === 2
      ? [Number(value[0]), Number(value[1])]
      : base);
    return {
      maxItems: range(record.max_items, fallback.maxItems),
      maxChars: range(record.max_chars, fallback.maxChars),
      maxGroups: Number.isFinite(Number(record.max_groups)) ? Number(record.max_groups) : fallback.maxGroups
    };
  }

  function normaliseMemoryPolicyPayload(raw) {
    const base = emptyMemoryPolicyState();
    const root = asRecord(raw);
    const configPath = textValue(root.config_path, '');
    if (root.ok === false) {
      const permissionDenied = root.permission_denied === true
        || (root.supported === true && root.available === false);
      return {
        ...base,
        status: permissionDenied ? 'error' : 'unsupported',
        supported: permissionDenied ? true : root.supported === true,
        permissionDenied,
        configPath,
        actions: memoryPolicyStringList(root.actions),
        message: textValue(root.message, ''),
        error: textValue(root.error || root.message, '当前实例不支持保存记忆策略。')
      };
    }
    const privateRecord = asRecord(root.private);
    const rows = [];
    for (const item of Array.isArray(root.groups) ? root.groups : []) {
      const record = asRecord(item);
      const groupId = textValue(record.group_id || record.groupId, '').trim();
      if (!groupId) continue;
      rows.push({
        groupId,
        mode: normaliseMemoryPolicyMode(record.mode),
        scope: normaliseMemoryPolicyScope(record.scope),
        extract: record.extract !== false,
        inject: record.inject !== false,
        maxItems: memoryPolicyBudgetText(record.max_items),
        maxChars: memoryPolicyBudgetText(record.max_chars),
        // AstrBot degrades the whole entry to conversation on any invalid
        // field, so `effectiveMode` (not `mode`) is what is really in force.
        invalid: record.invalid === true,
        errors: memoryPolicyStringList(record.errors),
        unknownFields: memoryPolicyStringList(record.unknown_fields),
        effectiveMode: memoryPolicyEffectiveMode(record.effective_mode)
      });
    }
    return {
      ...base,
      status: root.available === false
        ? (root.supported === false ? 'unsupported' : 'error')
        : 'ok',
      available: root.available !== false,
      supported: root.supported !== false,
      actions: memoryPolicyStringList(root.actions),
      permissionDenied: root.permission_denied === true,
      readChannel: textValue(root.read_channel, ''),
      writeChannel: textValue(root.write_channel, ''),
      readable: root.readable === true,
      writable: root.writable !== false,
      containerRunning: root.container_running === true,
      saved: root.saved === true,
      defaultMode: normaliseMemoryPolicyMode(root.default_mode),
      privateMode: normaliseMemoryPolicyMode(privateRecord.mode),
      privateExtract: privateRecord.extract !== false,
      privateInject: privateRecord.inject !== false,
      privateMaxItems: memoryPolicyBudgetText(privateRecord.max_items),
      privateMaxChars: memoryPolicyBudgetText(privateRecord.max_chars),
      privateConfigured: privateRecord.configured !== false,
      privateInvalid: privateRecord.invalid === true,
      privateErrors: memoryPolicyStringList(privateRecord.errors),
      privateEffectiveMode: memoryPolicyEffectiveMode(privateRecord.effective_mode),
      invalidRows: Number(root.invalid_rows) > 0 ? Number(root.invalid_rows) : 0,
      rows,
      modeOptions: memoryPolicyOptions(root.mode_options, base.modeOptions),
      scopeOptions: memoryPolicyOptions(root.scope_options, base.scopeOptions),
      limits: memoryPolicyLimits(root.limits, base.limits),
      warnings: (Array.isArray(root.warnings) ? root.warnings : []).map((item) => textValue(item, '')).filter(Boolean),
      message: textValue(root.message, ''),
      error: textValue(root.error, ''),
      configPath,
      memoryEnabled: typeof root.memory_enabled === 'boolean' ? root.memory_enabled : null,
      hotReload: root.hot_reload === true
    };
  }

  async function loadMemoryPolicyForInstance(instance) {
    if (state.optionalSupport.memoryPolicy === false) {
      return {
        ...emptyMemoryPolicyState(),
        status: 'unsupported',
        error: '当前后端未提供记忆策略接口，无法保存按群长期记忆策略。'
      };
    }
    try {
      const data = await requestWithFallback([
        `/api/instances/${encodeURIComponent(instance.id)}/memory-policy`,
        instancePath('/api/memory-policy', instance.id)
      ]);
      state.optionalSupport.memoryPolicy = true;
      return normaliseMemoryPolicyPayload(data);
    } catch (error) {
      if (isMissingEndpointError(error)) {
        state.optionalSupport.memoryPolicy = false;
        return {
          ...emptyMemoryPolicyState(),
          status: 'unsupported',
          error: '当前后端未提供记忆策略接口，无法保存按群长期记忆策略。'
        };
      }
      return {
        ...emptyMemoryPolicyState(),
        status: 'error',
        error: formatError(error, '记忆策略读取失败。')
      };
    }
  }

  function emptyStatsState() {
    return {
      status: 'idle',
      hostMemory: {},
      global: {},
      instanceData: {},
      docker: {},
      dataSizeBytes: undefined,
      fileCount: undefined,
      containerMemoryUsageBytes: undefined,
      containerMemoryLimitBytes: undefined,
      containerMemoryPercent: undefined,
      cpuPercent: undefined,
      errors: [],
      error: '',
      updatedAt: 0
    };
  }

  function pluginEnabledFromRecord(raw) {
    const plugin = asRecord(raw);
    let enabled = boolOrUndefined(plugin.enabled);
    if (enabled !== undefined) return enabled;
    enabled = boolOrUndefined(plugin.active);
    if (enabled !== undefined) return enabled;
    const status = textValue(plugin.status, '').trim().toLowerCase();
    if (['enabled', 'active', 'running', 'on', 'ok'].includes(status)) return true;
    if (['disabled', 'inactive', 'stopped', 'off', 'error'].includes(status)) return false;
    return undefined;
  }

  function normalisePluginRecord(raw) {
    const plugin = asRecord(raw);
    const fallbackId = textValue(plugin.id || plugin.plugin_id || plugin.name, '');
    const id = textValue(plugin.id || plugin.plugin_id, '') || fallbackId;
    return {
      id,
      name: textValue(plugin.name || plugin.display_name, '') || id || '未命名插件',
      enabled: pluginEnabledFromRecord(plugin),
      version: textValue(plugin.version, ''),
      description: textValue(plugin.description, ''),
      source: textValue(plugin.source, ''),
      restartRequired: boolOrUndefined(plugin.restart_required) === true,
      configAvailable: boolOrUndefined(plugin.config_available ?? plugin.config_api_available ?? plugin.supports_config),
      error: textValue(plugin.error, '')
    };
  }

  function normalisePluginsPayload(raw) {
    const root = asRecord(raw);
    let rawPlugins = [];
    let hasList = false;
    if (Array.isArray(root.plugins)) {
      rawPlugins = root.plugins;
      hasList = true;
    } else if (root.plugins && typeof root.plugins === 'object') {
      rawPlugins = Object.entries(root.plugins).map(([id, value]) => {
        if (value && typeof value === 'object' && !Array.isArray(value)) return { id, ...value };
        return { id, enabled: boolOrUndefined(value) };
      });
      hasList = true;
    } else if (Array.isArray(root.items)) {
      rawPlugins = root.items;
      hasList = true;
    }

    const message = textValue(root.message, '');
    const error = textValue(root.error, root.ok === false ? (message || '插件列表读取失败。') : '');
    return {
      status: root.ok === false ? 'error' : 'ok',
      items: rawPlugins.map(normalisePluginRecord).filter((plugin) => Boolean(plugin.id || plugin.name)),
      hasList,
      error,
      message,
      restartRequired: boolOrUndefined(root.restart_required) === true,
      cached: boolOrUndefined(root.cached) === true,
      updatedAt: Date.now()
    };
  }

  function normaliseMemoryPayload(raw, fallbackMode = '') {
    const root = asRecord(raw);
    const mode = normaliseMemoryMode(root.mode || root.memory_mode) || normaliseMemoryMode(fallbackMode);
    const message = textValue(root.message, '');
    const error = textValue(root.error, root.ok === false ? (message || '记忆模式读取失败。') : '');
    return {
      status: root.ok === false ? 'error' : 'ok',
      mode,
      options: ['direct', 'astrbot'],
      restartRequired: boolOrUndefined(root.restart_required) === true,
      message,
      error,
      configPath: textValue(root.config_path, ''),
      scope: textValue(root.memory_scope, ''),
      updatedAt: Date.now()
    };
  }

  function findMetricEntry(rawList, instanceId) {
    const entries = Array.isArray(rawList) ? rawList : [];
    for (const item of entries) {
      const record = asRecord(item);
      const id = textValue(record.instance_id || record.id, '');
      if (id && id === instanceId) return record;
    }
    if (entries.length === 1 && !textValue(asRecord(entries[0]).instance_id || asRecord(entries[0]).id, '')) {
      return asRecord(entries[0]);
    }
    return {};
  }

  function normaliseStatsErrors(value) {
    const errors = Array.isArray(value) ? value : [];
    return errors.map((item) => {
      if (typeof item === 'string') return item;
      const record = asRecord(item);
      return textValue(record.message || record.error || record.detail, '');
    }).filter(Boolean);
  }

  function normaliseStatsPayload(raw, instanceId) {
    const root = asRecord(raw);
    const rawInstances = Array.isArray(root.instances)
      ? root.instances
      : Array.isArray(root.items) ? root.items : [];
    let instanceData = findMetricEntry(rawInstances, instanceId);
    if (Object.keys(instanceData).length === 0 && (root.scope === 'instance' || root.data_dir_size_bytes !== undefined)) {
      instanceData = root;
    }
    if (Object.keys(instanceData).length === 0 && root.instance && typeof root.instance === 'object') {
      instanceData = asRecord(root.instance);
    }

    let dockerEntries = Array.isArray(root.docker) ? root.docker : [];
    if (dockerEntries.length === 0 && root.docker && typeof root.docker === 'object' && !Array.isArray(root.docker)) {
      dockerEntries = [root.docker];
    }
    let dockerData = findMetricEntry(dockerEntries, instanceId);
    if (Object.keys(dockerData).length === 0 && dockerEntries.length === 1) {
      dockerData = asRecord(dockerEntries[0]);
    }

    const global = asRecord(root.global);
    const errors = normaliseStatsErrors(root.errors);
    const instanceErrors = normaliseStatsErrors(instanceData.errors);
    const dockerError = textValue(dockerData.error, '');
    if (dockerError && !errors.includes(dockerError)) errors.push(dockerError);
    for (const message of instanceErrors) {
      if (!errors.includes(message)) errors.push(message);
    }
    const error = textValue(root.error, root.ok === false ? (textValue(root.message, '') || '资源统计读取失败。') : '');
    return {
      status: root.ok === false ? 'error' : 'ok',
      hostMemory: asRecord(root.host_memory),
      global,
      instanceData,
      docker: dockerData,
      dataSizeBytes: pickNumber(instanceData, ['data_dir_size_bytes', 'data_size_bytes']),
      fileCount: pickNumber(instanceData, ['file_count', 'files']),
      containerMemoryUsageBytes: pickNumber(dockerData, ['memory_usage_bytes', 'used_bytes', 'memory_bytes']),
      containerMemoryLimitBytes: pickNumber(dockerData, ['memory_limit_bytes', 'limit_bytes']),
      containerMemoryPercent: pickNumber(dockerData, ['memory_percent', 'mem_percent']),
      cpuPercent: pickNumber(dockerData, ['cpu_percent', 'cpu_usage_percent']),
      errors,
      error,
      updatedAt: Date.now()
    };
  }

  function previousV3State(instance) {
    const previous = state.instances.find((item) => item.id === instance.id);
    return previous && previous.v3 ? previous.v3 : null;
  }

  async function loadPluginsForInstance(instance) {
    const previous = previousV3State(instance);
    const previousPlugins = previous && previous.plugins ? previous.plugins : emptyPluginState();
    if (state.optionalSupport.plugins === false) {
      return {
        ...previousPlugins,
        status: 'unsupported',
        error: '当前后端未提供插件管理接口，插件状态显示为未知。'
      };
    }
    try {
      const data = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugins`);
      state.optionalSupport.plugins = true;
      return normalisePluginsPayload(data);
    } catch (error) {
      if (isMissingEndpointError(error)) {
        state.optionalSupport.plugins = false;
        return {
          ...previousPlugins,
          status: 'unsupported',
          error: '当前后端未提供插件管理接口，插件状态显示为未知。'
        };
      }
      return {
        ...previousPlugins,
        status: 'error',
        error: formatError(error, '插件列表读取失败。')
      };
    }
  }

  async function loadMemoryForInstance(instance) {
    const previous = previousV3State(instance);
    const preservedMode = lastKnownMemoryModes.get(instance.id)
      || normaliseMemoryMode(instance.memory_mode_hint)
      || '';
    const previousMemory = previous && previous.memory
      ? { ...previous.memory, mode: previous.memory.mode || preservedMode }
      : emptyMemoryState(preservedMode);
    if (state.optionalSupport.memory === false) {
      return {
        ...previousMemory,
        status: 'unsupported',
        mode: previousMemory.mode || preservedMode,
        error: '当前后端未提供记忆模式接口，记忆模式显示为未知。'
      };
    }
    try {
      const data = await requestWithFallback([
        `/api/instances/${encodeURIComponent(instance.id)}/memory`,
        instancePath('/api/memory', instance.id)
      ]);
      state.optionalSupport.memory = true;
      const resolved = normaliseMemoryPayload(data, previousMemory.mode || preservedMode);
      if (resolved.status !== 'ok') {
        return {
          ...resolved,
          mode: previousMemory.mode || preservedMode || resolved.mode,
          error: resolved.error || '记忆模式读取失败。'
        };
      }
      if (resolved.mode) lastKnownMemoryModes.set(instance.id, resolved.mode);
      return resolved;
    } catch (error) {
      if (isMissingEndpointError(error)) {
        state.optionalSupport.memory = false;
        return {
          ...previousMemory,
          status: 'unsupported',
          mode: previousMemory.mode || preservedMode,
          error: '当前后端未提供记忆模式接口，记忆模式显示为未知。'
        };
      }
      return {
        ...previousMemory,
        status: 'error',
        mode: previousMemory.mode || preservedMode,
        error: formatError(error, '记忆模式读取失败。')
      };
    }
  }

  async function loadStatsForInstance(instance) {
    const previous = previousV3State(instance);
    const previousStats = previous && previous.stats ? previous.stats : emptyStatsState();
    if (state.optionalSupport.stats === false) {
      return {
        ...previousStats,
        status: 'unsupported',
        error: '当前后端未提供资源统计接口，数值显示为未知。'
      };
    }
    try {
      const data = await requestWithFallback([
        instancePath('/api/stats', instance.id),
        `/api/instances/${encodeURIComponent(instance.id)}/stats`
      ]);
      state.optionalSupport.stats = true;
      return normaliseStatsPayload(data, instance.id);
    } catch (error) {
      if (isMissingEndpointError(error)) {
        state.optionalSupport.stats = false;
        return {
          ...previousStats,
          status: 'unsupported',
          error: '当前后端未提供资源统计接口，数值显示为未知。'
        };
      }
      return {
        ...previousStats,
        status: 'error',
        error: formatError(error, '资源统计读取失败。')
      };
    }
  }

  async function loadV3ForInstance(instance) {
    const [plugins, memory, memoryPolicy, stats] = await Promise.all([
      loadPluginsForInstance(instance),
      loadMemoryForInstance(instance),
      loadMemoryPolicyForInstance(instance),
      loadStatsForInstance(instance)
    ]);
    return { ...instance, v3: { plugins, memory, memoryPolicy, stats } };
  }

  function setV3Status(element, message, tone = 'info') {
    if (!element) return;
    element.textContent = message || '';
    element.className = `instance-v3-status${tone ? ` is-${tone}` : ''}`;
    element.hidden = !message;
  }

  function findInstanceCard(instanceId) {
    return $$('.instance-card').find((card) => card.dataset.instanceId === instanceId) || null;
  }

  function v3SummaryText(instance) {
    const v3 = instance.v3 || {};
    const plugins = v3.plugins || emptyPluginState();
    const memory = v3.memory || emptyMemoryState(instance.memory_mode_hint || '');
    const stats = v3.stats || emptyStatsState();
    const pluginText = plugins.status === 'ok' ? `${plugins.items.length} 个插件` : '插件未知';
    const memoryText = memory.mode ? `记忆 ${memory.mode}` : '记忆未知';
    const statsText = stats.status === 'ok' ? '资源已同步' : '资源未知';
    return `${pluginText} · ${memoryText} · ${statsText}`;
  }

  function updateV3Summary(instance) {
    const card = findInstanceCard(instance.id);
    if (card) {
      const button = card.querySelector('.instance-settings-btn');
      if (button) button.title = `设置：${v3SummaryText(instance)}`;
    }
    instanceSettings.updateSubtitle(instance);
  }

  function pluginSourceLabel(source) {
    const value = textValue(source, '').trim().toLowerCase();
    if (value === 'builtin') return '内置';
    if (value === 'installed') return '已安装';
    return textValue(source, '');
  }

  function pluginStateText(enabled) {
    if (enabled === true) return '已启用';
    if (enabled === false) return '已禁用';
    return '状态未知';
  }

  let pluginConfigReturnFocus = null;

  function pluginConfigUiKey() {
    const config = state.pluginConfig;
    return `${config.instanceId}|${config.pluginId}`;
  }

  function pluginConfigUiState() {
    const config = state.pluginConfig;
    const key = pluginConfigUiKey();
    if (!config.ui.byPlugin[key]) {
      config.ui.byPlugin[key] = {
        activeGroup: 'all',
        modifiedOnly: false,
        collapsedGroups: {},
        scrollTops: {}
      };
    }
    return config.ui.byPlugin[key];
  }

  function pluginSectionExpanded(group, ui) {
    const state = group || { id: 'advanced', advanced: false };
    if (Object.prototype.hasOwnProperty.call(ui.collapsedGroups, state.id)) {
      return ui.collapsedGroups[state.id] !== true;
    }
    return state.advanced !== true;
  }

  function setPluginGroupExpanded(groupId, expanded, persist = true) {
    const section = state.pluginConfig.groupSections.get(groupId);
    if (!section) return;
    section.dataset.collapsed = expanded ? 'false' : 'true';
    const toggle = section.querySelector('.plugin-config-group-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    if (persist) {
      const ui = pluginConfigUiState();
      ui.collapsedGroups[groupId] = !expanded;
    }
  }

  function savePluginConfigScroll(ui, groupId) {
    const body = $('plugin-config-body');
    if (body) ui.scrollTops[groupId || 'all'] = body.scrollTop;
  }

  function refreshPluginConfigModifiedEntry(entry) {
    if (!entry || !entry.element || typeof entry.readValue !== 'function') return;
    const result = entry.readValue();
    entry.modified = pluginConfigService.pluginFieldValueChanged(
      entry.field,
      result && result.ok ? result.value : undefined,
      entry.originalValue
    );
    entry.element.classList.toggle('is-modified', entry.modified);
    if (entry.modifiedBadge) entry.modifiedBadge.hidden = !entry.modified;
  }

  function refreshAllPluginConfigModifiedEntries() {
    for (const entry of state.pluginConfig.entries || []) refreshPluginConfigModifiedEntry(entry);
  }

  function applyPluginConfigActiveGroup() {
    const config = state.pluginConfig;
    const ui = pluginConfigUiState();
    const active = config.groupButtons.has(ui.activeGroup) ? ui.activeGroup : 'all';
    ui.activeGroup = active;
    for (const [id, button] of config.groupButtons) {
      const isActive = id === active;
      button.setAttribute('aria-current', isActive ? 'true' : 'false');
      button.classList.toggle('is-active', isActive);
    }
  }

  function selectPluginConfigGroup(groupId) {
    const config = state.pluginConfig;
    if (!config.open) return;
    const ui = pluginConfigUiState();
    savePluginConfigScroll(ui, ui.activeGroup);
    ui.activeGroup = config.groupButtons.has(groupId) ? groupId : 'all';
    const search = $('plugin-config-search');
    if (search && search.value) search.value = '';
    applyPluginConfigActiveGroup();
    updatePluginConfigFilter();
    const body = $('plugin-config-body');
    if (body) body.scrollTop = ui.scrollTops[ui.activeGroup] || 0;
    const button = config.groupButtons.get(ui.activeGroup);
    if (button) button.focus({ preventScroll: true });
  }

  function makePluginGroupSection(group, entries) {
    const section = makeElement('section', 'plugin-config-group-section');
    section.dataset.groupId = group.id;
    section.id = `plugin-config-group-${group.id}`;
    const head = makeElement('div', 'plugin-config-group-head');
    const toggle = makeElement('button', 'plugin-config-group-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-controls', section.id);
    toggle.appendChild(
      makeElement('span', 'plugin-config-group-btn-label', group.title)
    );
    toggle.appendChild(
      makeElement(
        'span',
        'plugin-config-group-section-count',
        String(entries.length)
      )
    );
    toggle.addEventListener('click', () => {
      const expanded = section.dataset.collapsed !== 'true';
      setPluginGroupExpanded(group.id, !expanded);
    });
    head.appendChild(toggle);
    section.appendChild(head);
    const body = makeElement('div', 'plugin-config-group-fields');
    for (const entry of entries) body.appendChild(entry.element);
    section.appendChild(body);
    const expanded = pluginSectionExpanded(group, pluginConfigUiState());
    section.dataset.collapsed = expanded ? 'false' : 'true';
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    return section;
  }

  function renderPluginConfigGroupNav(groups) {
    const nav = $('plugin-config-groups');
    const config = state.pluginConfig;
    config.groups = groups;
    config.groupButtons = new Map();
    if (!nav) return;
    nav.replaceChildren();
    const addButton = (id, title, count) => {
      const button = makeElement('button', 'plugin-config-group-btn');
      button.type = 'button';
      button.dataset.groupId = id;
      button.setAttribute('aria-current', 'false');
      button.appendChild(
        makeElement('span', 'plugin-config-group-btn-label', title)
      );
      button.appendChild(
        makeElement('span', 'plugin-config-group-count', String(count))
      );
      button.addEventListener('click', () => selectPluginConfigGroup(id));
      config.groupButtons.set(id, button);
      nav.appendChild(button);
    };
    const total = groups.reduce((sum, group) => sum + group.count, 0);
    const ui = pluginConfigUiState();
    if (ui.activeGroup !== 'all' && !groups.some((group) => group.id === ui.activeGroup)) {
      ui.activeGroup = 'all';
    }
    addButton('all', '全部参数', total);
    for (const group of groups) addButton(group.id, group.title, group.count);
    applyPluginConfigActiveGroup();
  }

  function updatePluginConfigFilter() {
    const config = state.pluginConfig;
    const search = $('plugin-config-search');
    const query = search ? search.value.trim().toLowerCase() : '';
    const modifiedToggle = $('plugin-config-modified-only');
    const modifiedOnly = Boolean(modifiedToggle && modifiedToggle.checked);
    const ui = pluginConfigUiState();
    ui.modifiedOnly = modifiedOnly;
    refreshAllPluginConfigModifiedEntries();
    const entries = config.entries || [];
    const visibleByGroup = new Map();
    let visible = 0;
    for (const entry of entries) {
      if (entry.legacyHidden) {
        entry.element.hidden = true;
        continue;
      }
      const matchesQuery = !query
        || entry.element.dataset.searchText.includes(query);
      const matchesModified = !modifiedOnly || entry.modified === true;
      const matchesGroup = Boolean(query)
        || ui.activeGroup === 'all'
        || entry.groupId === ui.activeGroup;
      const show = matchesQuery && matchesModified && matchesGroup;
      entry.element.hidden = !show;
      if (show) {
        visible += 1;
        const groupId = entry.groupId || 'advanced';
        visibleByGroup.set(groupId, (visibleByGroup.get(groupId) || 0) + 1);
      }
    }
    for (const [groupId, section] of config.groupSections) {
      const count = visibleByGroup.get(groupId) || 0;
      section.hidden = count === 0;
      if (query) {
        setPluginGroupExpanded(groupId, true, false);
      } else if (count > 0) {
        setPluginGroupExpanded(
          groupId,
          pluginSectionExpanded(
            config.groupsById.get(groupId) || { id: groupId, advanced: false },
            ui
          ),
          false
        );
      }
    }
    const count = $('plugin-config-filter-count');
    if (count) {
      count.textContent = entries.length
        ? `显示 ${visible}/${entries.length} 个参数${modifiedOnly ? ' · 只看已修改' : ''}`
        : '';
    }
    const empty = $('plugin-config-empty');
    if (empty) {
      if (entries.length === 0) {
        empty.textContent = '没有可编辑的参数。';
        empty.hidden = false;
      } else if (visible === 0) {
        empty.textContent = modifiedOnly ? '没有已修改的参数。' : '没有匹配的参数。';
        empty.hidden = false;
      } else {
        empty.hidden = true;
      }
    }
    applyPluginConfigActiveGroup();
  }

  function clearPluginConfigFieldError(entry) {
    if (!entry || !entry.input) return;
    entry.input.removeAttribute('aria-invalid');
    if (entry.errorElement) {
      entry.errorElement.textContent = '';
      entry.errorElement.hidden = true;
    }
  }

  function clearAllPluginConfigFeedback() {
    for (const entry of state.pluginConfig.entries || []) clearPluginConfigFieldError(entry);
    const error = $('plugin-config-error');
    const status = $('plugin-config-status');
    if (error) { error.textContent = ''; error.hidden = true; }
    if (status) { status.textContent = ''; status.hidden = true; status.className = 'plugin-config-status'; }
  }

  function clearPluginConfigFeedbackOnly() {
    const error = $('plugin-config-error');
    const status = $('plugin-config-status');
    if (error) { error.textContent = ''; error.hidden = true; }
    if (status) { status.textContent = ''; status.hidden = true; status.className = 'plugin-config-status'; }
  }

  function setPluginConfigError(message) {
    const element = $('plugin-config-error');
    if (element) { element.textContent = sanitisePluginText(message); element.hidden = !message; }
  }

  function setPluginConfigStatus(message, warning = false) {
    const element = $('plugin-config-status');
    if (!element) return;
    element.textContent = sanitisePluginText(message);
    element.className = `plugin-config-status${warning ? ' is-warning' : ''}`;
    element.hidden = !message;
  }

  function showPluginConfigFieldError(entry, message, focus = true) {
    if (!entry || !entry.input) return;
    entry.input.setAttribute('aria-invalid', 'true');
    if (entry.errorElement) {
      entry.errorElement.textContent = message;
      entry.errorElement.hidden = false;
    }
    if (focus) entry.input.focus({ preventScroll: true });
  }

  // T17 P2: a field that failed validation must actually be visible. Clearing
  // the search box, the "只看已修改" filter and a foreign group tab (and
  // expanding the owning section) is what makes the red field reachable instead
  // of silently filtered out.
  function revealPluginConfigEntry(entry) {
    if (!entry || !entry.element) return;
    const ui = pluginConfigUiState();
    const plan = pluginConfigService.pluginConfigRevealPlan(entry, ui);
    const search = $('plugin-config-search');
    if (plan.clearSearch && search && search.value) search.value = '';
    const modifiedToggle = $('plugin-config-modified-only');
    if (modifiedToggle) modifiedToggle.checked = plan.modifiedOnly;
    ui.modifiedOnly = plan.modifiedOnly;
    ui.activeGroup = plan.activeGroup;
    setPluginGroupExpanded(plan.expandGroup, true);
    updatePluginConfigFilter();
    if (entry.element.hidden) {
      // Last resort: drop every filter so the field can never stay invisible.
      ui.activeGroup = 'all';
      ui.modifiedOnly = false;
      if (modifiedToggle) modifiedToggle.checked = false;
      if (search) search.value = '';
      updatePluginConfigFilter();
    }
    if (plan.scrollIntoView && typeof entry.element.scrollIntoView === 'function') {
      entry.element.scrollIntoView({ block: 'center' });
    }
  }

  function syncPluginConfigSaveState() {
    const button = $('plugin-config-save');
    const label = $('plugin-config-save-label');
    const closeButton = $('plugin-config-close');
    const cancelButton = $('plugin-config-cancel');
    const config = state.pluginConfig;
    if (button) button.disabled = config.loading || config.saving || !config.entries.length;
    if (label) label.textContent = config.saving ? '保存中…' : '保存参数';
    if (closeButton) closeButton.disabled = config.saving;
    if (cancelButton) cancelButton.disabled = config.saving;
  }

  function makePluginFieldEntry(field, value) {
    const isReplyProbability = field.key === 'reply_probability';
    const fieldElement = makeElement('div', `plugin-config-field${isReplyProbability ? ' is-highlight' : ''}`);
    const labelText = isReplyProbability ? '群聊非 @ 消息的回复概率' : field.label;
    const groupTitle = textValue(field.groupTitle, '').trim() || '高级/其他';
    const breadcrumb = makeElement('p', 'plugin-field-breadcrumb');
    breadcrumb.appendChild(makeElement('span', '', groupTitle));
    const breadcrumbSeparator = makeElement('span', 'plugin-field-breadcrumb-sep', '›');
    breadcrumbSeparator.setAttribute('aria-hidden', 'true');
    breadcrumb.appendChild(breadcrumbSeparator);
    breadcrumb.appendChild(makeElement('span', '', labelText));
    fieldElement.appendChild(breadcrumb);
    const head = makeElement('div', 'plugin-field-head');
    const titleWrap = makeElement('div', '');
    titleWrap.appendChild(makeElement('span', 'plugin-field-title', labelText));
    titleWrap.appendChild(makeElement('span', 'plugin-field-key', field.key));
    head.appendChild(titleWrap);
    if (field.hasDefault) head.appendChild(makeElement('span', 'plugin-field-default', `默认：${pluginDefaultText(field.default)}`));
    const modifiedBadge = makeElement('span', 'plugin-field-modified', '已修改');
    modifiedBadge.hidden = true;
    head.appendChild(modifiedBadge);
    fieldElement.appendChild(head);
    if (field.description) fieldElement.appendChild(makeElement('p', 'plugin-field-description', field.description));
    const control = makeElement('div', 'plugin-field-control');
    const errorElement = makeElement('p', 'plugin-field-error', '');
    errorElement.hidden = true;
    let input = null;
    let readValue = null;
    let numericMin;
    let numericMax;

    if (field.type === 'bool') {
      const row = makeElement('label', 'plugin-bool-row');
      input = document.createElement('input');
      input.setAttribute('type', 'checkbox');
      const current = boolOrUndefined(value);
      input.checked = current === undefined ? field.hasDefault && field.default === true : current;
      row.appendChild(input);
      const boolState = makeElement('span', '', input.checked ? '已启用' : '未启用');
      row.appendChild(boolState);
      input.addEventListener('change', () => { boolState.textContent = input.checked ? '已启用' : '未启用'; });
      control.appendChild(row);
      readValue = () => ({ ok: true, value: input.checked });
    } else if (field.type === 'int' || field.type === 'float') {
      input = document.createElement('input');
      input.setAttribute('type', 'number');
      input.setAttribute('inputmode', field.type === 'int' ? 'numeric' : 'decimal');
      numericMin = isReplyProbability ? 0 : field.min;
      numericMax = isReplyProbability ? 1 : field.max;
      const step = isReplyProbability ? 0.01 : (field.step !== undefined ? field.step : (field.type === 'int' ? 1 : 0.01));
      if (numericMin !== undefined) input.setAttribute('min', String(numericMin));
      if (numericMax !== undefined) input.setAttribute('max', String(numericMax));
      input.setAttribute('step', String(step));
      input.value = pluginInputText(value);
      const useSlider = isReplyProbability || field.slider;
      if (useSlider) {
        const row = makeElement('div', 'plugin-number-row');
        const range = document.createElement('input');
        range.setAttribute('type', 'range');
        range.setAttribute('min', String(numericMin !== undefined ? numericMin : 0));
        range.setAttribute('max', String(numericMax !== undefined ? numericMax : (isReplyProbability ? 1 : 100)));
        range.setAttribute('step', String(step));
        range.value = pluginInputText(value) || String(numericMin !== undefined ? numericMin : 0);
        range.addEventListener('input', () => { input.value = range.value; });
        input.addEventListener('input', () => {
          const number = Number(input.value);
          if (Number.isFinite(number)) range.value = String(number);
        });
        row.appendChild(input);
        row.appendChild(range);
        control.appendChild(row);
      } else {
        control.appendChild(input);
      }
      if (isReplyProbability) {
        control.appendChild(makeElement('p', 'plugin-probability', '0 = 不主动回复；1 = 每条非 @ 群聊消息都考虑回复。可直接输入 0–1。'));
      }
      readValue = () => {
        const raw = input.value.trim();
        if (raw === '') return { ok: false, message: '请输入数值。' };
        const number = Number(raw);
        if (!Number.isFinite(number)) return { ok: false, message: '请输入有效数字。' };
        if (field.type === 'int' && !Number.isInteger(number)) return { ok: false, message: '请输入整数。' };
        if (numericMin !== undefined && number < numericMin) return { ok: false, message: `不能小于 ${numericMin}。` };
        if (numericMax !== undefined && number > numericMax) return { ok: false, message: `不能大于 ${numericMax}。` };
        return { ok: true, value: number };
      };
    } else if (field.type === 'string' && field.options.length > 0) {
      input = document.createElement('select');
      for (const option of field.options) {
        const optionElement = document.createElement('option');
        optionElement.value = option.value;
        optionElement.textContent = sanitisePluginText(option.label);
        input.appendChild(optionElement);
      }
      const current = pluginInputText(value);
      if (current && !field.options.some((option) => option.value === current)) {
        const optionElement = document.createElement('option');
        optionElement.value = current;
        optionElement.textContent = `当前值：${current}`;
        input.appendChild(optionElement);
      }
      input.value = current || (field.options[0] ? field.options[0].value : '');
      control.appendChild(input);
      readValue = () => ({ ok: true, value: input.value });
    } else if (field.type === 'text' || field.type === 'list') {
      input = document.createElement('textarea');
      input.setAttribute('rows', field.type === 'list' ? '4' : '3');
      input.setAttribute('spellcheck', 'false');
      input.value = field.type === 'list' ? pluginListText(value) : pluginInputText(value);
      if (field.type === 'list') input.placeholder = '每行一项';
      control.appendChild(input);
      readValue = () => field.type === 'list'
        ? { ok: true, value: input.value.split(/\r?\n/).map((line) => line.trim()).filter((line) => line !== '') }
        : { ok: true, value: input.value };
    } else {
      input = document.createElement('input');
      input.setAttribute('type', 'text');
      input.setAttribute('autocomplete', 'off');
      input.value = pluginInputText(value);
      control.appendChild(input);
      readValue = () => ({ ok: true, value: input.value });
    }

    fieldElement.appendChild(control);
    if (field.hint) fieldElement.appendChild(makeElement('p', 'plugin-field-hint', field.hint));
    fieldElement.appendChild(errorElement);
    const entry = {
      key: field.key,
      type: field.type,
      field,
      element: fieldElement,
      input,
      errorElement,
      readValue,
      originalValue: value,
      groupId: textValue(field.groupId, '').trim() || 'advanced',
      groupTitle,
      modifiedBadge,
      modified: false
    };
    fieldElement.dataset.fieldKey = field.key;
    fieldElement.dataset.groupId = entry.groupId;
    fieldElement.dataset.searchText = pluginConfigService.pluginFieldSearchText(field);
    if (isReplyProbability) fieldElement.dataset.replyProbability = 'true';
    input.addEventListener('input', () => {
      clearPluginConfigFieldError({ input, errorElement });
      refreshPluginConfigModifiedEntry(entry);
      updatePluginConfigFilter();
    });
    if (input.tagName === 'SELECT') {
      input.addEventListener('change', () => {
        clearPluginConfigFieldError({ input, errorElement });
        refreshPluginConfigModifiedEntry(entry);
        updatePluginConfigFilter();
      });
    }
    refreshPluginConfigModifiedEntry(entry);
    return entry;
  }

  function renderPluginConfigFields() {
    const fields = $('plugin-config-fields');
    if (!fields) return;
    fields.replaceChildren();
    const config = state.pluginConfig;
    config.entries = [];
    config.groups = [];
    config.groupsById = new Map();
    config.groupSections = new Map();
    const snapshot = config.snapshot || { schema: [], values: {}, groups: [] };
    const groups = Array.isArray(snapshot.groups) && snapshot.groups.length
      ? snapshot.groups
      : pluginConfigService.groupPluginFields(snapshot.schema, snapshot.uiGroups);
    const entriesByGroup = new Map();
    for (const field of snapshot.schema) {
      const entry = makePluginFieldEntry(
        field,
        pluginValueLookup(snapshot.values, field)
      );
      config.entries.push(entry);
      const groupId = entry.groupId || 'advanced';
      if (!entriesByGroup.has(groupId)) entriesByGroup.set(groupId, []);
      entriesByGroup.get(groupId).push(entry);
    }
    config.groups = groups;
    config.groupsById = new Map(groups.map((group) => [group.id, group]));
    for (const group of groups) {
      const groupEntries = entriesByGroup.get(group.id) || [];
      entriesByGroup.delete(group.id);
      if (!groupEntries.length) continue;
      const section = makePluginGroupSection(group, groupEntries);
      config.groupSections.set(group.id, section);
      fields.appendChild(section);
    }
    // Safety net: a field with no matching group must still be rendered.
    for (const [groupId, orphanEntries] of entriesByGroup) {
      const group = { id: groupId, title: '高级/其他', advanced: true };
      config.groups.push({ ...group, count: orphanEntries.length, fields: [] });
      config.groupsById.set(groupId, group);
      const section = makePluginGroupSection(group, orphanEntries);
      config.groupSections.set(groupId, section);
      fields.appendChild(section);
    }
    renderPluginConfigGroupNav(config.groups);
    const modifiedToggle = $('plugin-config-modified-only');
    if (modifiedToggle) {
      modifiedToggle.checked = pluginConfigUiState().modifiedOnly === true;
    }
    const loading = $('plugin-config-loading');
    if (loading) loading.hidden = true;
    syncPluginConfigSaveState();
    updatePluginConfigFilter();
    const ui = pluginConfigUiState();
    const body = $('plugin-config-body');
    if (body) body.scrollTop = ui.scrollTops[ui.activeGroup] || 0;
  }

  function resetPluginConfigPanel() {
    state.pluginConfig.requestSeq += 1;
    state.pluginConfig.open = false;
    state.pluginConfig.loading = false;
    state.pluginConfig.saving = false;
    state.pluginConfig.entries = [];
    state.pluginConfig.snapshot = null;
    state.pluginConfig.groups = [];
    state.pluginConfig.groupsById = new Map();
    state.pluginConfig.groupSections = new Map();
    state.pluginConfig.groupButtons = new Map();
    const groupNav = $('plugin-config-groups');
    if (groupNav) groupNav.replaceChildren();
    const modifiedToggle = $('plugin-config-modified-only');
    if (modifiedToggle) modifiedToggle.checked = false;
    const overlay = $('plugin-config-overlay');
    if (overlay) overlay.hidden = true;
    syncConsoleModalLock();
    const fields = $('plugin-config-fields');
    if (fields) fields.replaceChildren();
    const search = $('plugin-config-search');
    if (search) { search.value = ''; search.disabled = false; }
    const count = $('plugin-config-filter-count');
    if (count) count.textContent = '';
    const title = $('plugin-config-title');
    if (title) title.textContent = '编辑插件参数';
    const subtitle = $('plugin-config-subtitle');
    if (subtitle) subtitle.textContent = '';
    const source = $('plugin-config-source');
    if (source) source.textContent = '';
    const empty = $('plugin-config-empty');
    if (empty) empty.hidden = true;
    const loading = $('plugin-config-loading');
    if (loading) loading.hidden = true;
    const save = $('plugin-config-save');
    if (save) save.disabled = true;
    clearAllPluginConfigFeedback();
  }

  function closePluginConfigPanel(options = {}) {
    if (state.pluginConfig.saving && options.force !== true) return false;
    const wasOpen = state.pluginConfig.open;
    if (wasOpen) {
      const ui = pluginConfigUiState();
      savePluginConfigScroll(ui, ui.activeGroup);
    }
    resetPluginConfigPanel();
    if (options.restoreFocus !== false && wasOpen && pluginConfigReturnFocus && pluginConfigReturnFocus.isConnected) {
      pluginConfigReturnFocus.focus({ preventScroll: true });
    }
    pluginConfigReturnFocus = null;
    return true;
  }

  async function loadPluginConfig(instance, plugin) {
    const config = state.pluginConfig;
    const seq = ++config.requestSeq;
    const fields = $('plugin-config-fields');
    if (fields) fields.replaceChildren();
    const loading = $('plugin-config-loading');
    if (loading) { loading.textContent = '正在读取插件参数…'; loading.hidden = false; }
    const empty = $('plugin-config-empty');
    if (empty) empty.hidden = true;
    const save = $('plugin-config-save');
    if (save) save.disabled = true;
    clearAllPluginConfigFeedback();
    config.loading = true;
    try {
      const result = await pluginConfigService.load(instance.id, plugin.id);
      if (!config.open || seq !== config.requestSeq) return;
      const snapshot = filterLegacyPromptSnapshot(result.snapshot, instance.id);
      config.snapshot = snapshot;
      config.loading = false;
      config.apiAvailable = snapshot.apiAvailable;
      config.restartRequired = snapshot.restartRequired;
      config.source = snapshot.source;
      const source = $('plugin-config-source');
      if (source) source.textContent = snapshot.source ? `配置来源：${snapshot.source}` : '';
      if (!snapshot.apiAvailable || snapshot.schema.length === 0) {
        if (loading) loading.hidden = true;
        if (empty) {
          empty.textContent = !snapshot.apiAvailable ? '当前插件未提供参数 schema，暂不支持参数编辑。' : '没有可编辑的参数。';
          empty.hidden = false;
        }
        setPluginConfigError(snapshot.error || (!snapshot.apiAvailable ? '当前插件未提供参数 schema，暂不支持参数编辑。' : ''));
        if (snapshot.message) setPluginConfigStatus(snapshot.message, snapshot.restartRequired);
        syncPluginConfigSaveState();
        return;
      }
      renderPluginConfigFields();
      if (snapshot.message) setPluginConfigStatus(snapshot.message, snapshot.restartRequired);
      if (snapshot.error) setPluginConfigError(snapshot.error);
    } catch (error) {
      if (!config.open || seq !== config.requestSeq) return;
      config.loading = false;
      const errorRecord = error instanceof ApiError ? asRecord(error.data) : {};
      const message = formatError(error, '插件参数读取失败。');
      const unsupported = isMissingEndpointError(error)
        || pluginConfigUnsupportedMessage(message)
        || pluginConfigUnsupportedMessage(errorRecord.message)
        || pluginConfigUnsupportedMessage(errorRecord.error);
      if (loading) loading.hidden = true;
      if (save) save.disabled = true;
      if (empty) {
        empty.textContent = unsupported ? '当前后端不支持参数编辑。' : '插件参数暂不可用。';
        empty.hidden = false;
      }
      setPluginConfigError(unsupported ? '当前后端不支持参数编辑。' : message);
      syncPluginConfigSaveState();
    } finally {
      if (config.open && seq === config.requestSeq) {
        config.loading = false;
        syncPluginConfigSaveState();
      }
    }
  }

  async function openPluginConfigPanel(instance, plugin, trigger) {
    if (!instance || !plugin || !plugin.id) return;
    resetPluginConfigPanel();
    const config = state.pluginConfig;
    config.open = true;
    config.loading = true;
    config.saving = false;
    config.instanceId = instance.id;
    config.instanceName = instance.name || instance.id;
    config.pluginId = plugin.id;
    config.pluginName = plugin.name || plugin.id;
    config.source = '';
    config.restartRequired = false;
    config.apiAvailable = true;
    pluginConfigReturnFocus = trigger || null;
    const overlay = $('plugin-config-overlay');
    if (overlay) overlay.hidden = false;
    syncConsoleModalLock();
    const title = $('plugin-config-title');
    if (title) title.textContent = plugin.name || plugin.id;
    const subtitle = $('plugin-config-subtitle');
    if (subtitle) subtitle.textContent = `实例：${instance.name || instance.id} · ${instance.id} · 插件：${plugin.id}`;
    const search = $('plugin-config-search');
    if (search) { search.value = ''; search.disabled = true; }
    const panel = $('plugin-config-panel');
    if (panel) panel.focus({ preventScroll: true });
    await loadPluginConfig(instance, plugin);
    if (search && config.open) search.disabled = false;
  }

  async function savePluginConfig() {
    const config = state.pluginConfig;
    if (!config.open || config.loading || config.saving || !config.entries.length) return;
    clearPluginConfigFeedbackOnly();
    if ($('plugin-config-error')) $('plugin-config-error').hidden = true;
    if ($('plugin-config-status')) $('plugin-config-status').hidden = true;
    for (const entry of config.entries) clearPluginConfigFieldError(entry);
    // The payload always carries every editable field, including the ones the
    // current search/group/modified filter hides.
    const plan = pluginConfigService.pluginConfigSavePayload(config.entries);
    if (!plan.ok) {
      plan.invalid.forEach((item, index) => {
        if (!item.entry) return;
        revealPluginConfigEntry(item.entry);
        showPluginConfigFieldError(item.entry, item.message || '参数值无效。', index === 0);
      });
      setPluginConfigError('请先修正标红的参数。');
      return;
    }
    const values = plan.values;
    config.saving = true;
    syncPluginConfigSaveState();
    setPluginConfigError('');
    setPluginConfigStatus('正在保存参数…', false);
    try {
      const data = await pluginConfigService.save(config.instanceId, config.pluginId, values);
      if (!config.open) return;
      const response = asRecord(data);
      const restart = boolOrUndefined(response.restart_required) === true
        || boolOrUndefined(asRecord(response.plugin).restart_required) === true;
      const apiAvailable = boolOrUndefined(response.api_available) !== false;
      const detail = sanitisePluginText(response.message || response.error);
      const outcome = restart
        ? '已保存；AstrBot 需要重启后生效。'
        : apiAvailable
          ? '已保存；AstrBot 已热重载。'
          : '已保存；AstrBot 热重载不可用，请重启后生效。';
      setPluginConfigStatus(detail ? `${outcome} ${detail}` : outcome, restart || !apiAvailable);
      config.restartRequired = restart;
      config.apiAvailable = apiAvailable;
      // T17 P2: refresh the comparison baseline so "已修改" badges clear after a
      // successful save (values the server reports win over what was sent).
      const savedValues = asRecord(response.values);
      for (const update of pluginConfigService.pluginConfigUpdatedBaselines(config.entries, values, savedValues)) {
        const entry = config.entries.find((item) => item.key === update.key);
        if (entry) entry.originalValue = update.value;
      }
      refreshAllPluginConfigModifiedEntries();
      updatePluginConfigFilter();
      const pluginState = instancePluginStatus(config.instanceId, config.pluginId);
      if (pluginState) {
        pluginState.status.message = outcome;
        pluginState.status.restartRequired = restart;
        updateV3Summary(pluginState.instance);
      }
    } catch (error) {
      if (!config.open) return;
      const message = formatError(error, '插件参数保存失败。');
      const unsupported = isMissingEndpointError(error) || pluginConfigUnsupportedMessage(message);
      setPluginConfigError(unsupported ? '当前后端不支持参数编辑。' : message);
      setPluginConfigStatus('', false);
    } finally {
      config.saving = false;
      syncPluginConfigSaveState();
    }
  }

  function instancePluginStatus(instanceId, pluginId) {
    const instance = state.instances.find((item) => item.id === instanceId);
    const status = instance && instance.v3 && instance.v3.plugins ? instance.v3.plugins : null;
    const plugin = status && Array.isArray(status.items) ? status.items.find((item) => item.id === pluginId) : null;
    return plugin ? { instance, plugin, status } : null;
  }

  function buildPluginSection(instance) {
    const plugins = (instance.v3 && instance.v3.plugins) || emptyPluginState();
    const section = makeElement('section', 'instance-v3-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-puzzle'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '插件管理'));
    headingCopy.appendChild(makeElement('p', '', '查看插件状态并启用或禁用；变更可能需要在重启 AstrBot 后生效。'));
    heading.appendChild(headingCopy);
    if (plugins.cached) heading.appendChild(makeElement('span', 'instance-v3-badge', '缓存'));
    section.appendChild(heading);

    const statusElement = makeElement('p', 'instance-v3-status');
    statusElement.dataset.v3Status = 'plugins';
    statusElement.setAttribute('role', 'status');
    if (plugins.status === 'unsupported' || plugins.status === 'error') {
      setV3Status(statusElement, plugins.error || '插件接口暂不可用，状态显示为未知。', plugins.status === 'error' ? 'error' : 'warning');
    } else if (plugins.error) {
      setV3Status(statusElement, plugins.error, 'error');
    } else if (plugins.message) {
      setV3Status(statusElement, plugins.message, plugins.restartRequired ? 'warning' : 'info');
    } else if (plugins.restartRequired) {
      setV3Status(statusElement, '插件配置已变更，请重启 AstrBot 生效。', 'warning');
    }

    if (plugins.items.length > 0) {
      const list = makeElement('div', 'plugin-list');
      for (const plugin of plugins.items) {
        const row = makeElement('div', 'plugin-item');
        const info = makeElement('div', 'plugin-info');
        const titleLine = makeElement('div', 'plugin-title-line');
        titleLine.appendChild(makeElement('strong', 'plugin-name', plugin.name));
        const sourceLabel = pluginSourceLabel(plugin.source);
        if (sourceLabel) titleLine.appendChild(makeElement('span', 'plugin-source-badge', sourceLabel));
        if (plugin.version) titleLine.appendChild(makeElement('span', 'plugin-version', `v${plugin.version}`));
        info.appendChild(titleLine);
        if (plugin.description) info.appendChild(makeElement('p', 'plugin-description', plugin.description));
        if (plugin.error) info.appendChild(makeElement('p', 'plugin-error', plugin.error));

        const control = makeElement('div', 'plugin-control');
        const canEditConfig = Boolean(plugin.id) && plugin.configAvailable !== false;
        if (canEditConfig) {
          const editButton = makeElement('button', 'plugin-edit-button', '编辑参数');
          editButton.setAttribute('type', 'button');
          editButton.setAttribute('aria-label', `编辑插件 ${plugin.name} 的参数`);
          editButton.addEventListener('click', () => void openPluginConfigPanel(instance, plugin, editButton));
          control.appendChild(editButton);
        }
        const stateText = makeElement('span', 'plugin-state-text', pluginStateText(plugin.enabled));
        const switchLabel = makeElement('label', 'plugin-switch');
        const checkbox = document.createElement('input');
        checkbox.setAttribute('type', 'checkbox');
        checkbox.className = 'plugin-switch-input';
        checkbox.checked = plugin.enabled === true;
        checkbox.disabled = plugins.status !== 'ok' || !plugin.id || plugin.enabled === undefined;
        checkbox.setAttribute('role', 'switch');
        checkbox.setAttribute('aria-label', `启用插件 ${plugin.name}`);
        const track = makeElement('span', 'plugin-switch-track');
        switchLabel.appendChild(checkbox);
        switchLabel.appendChild(track);
        checkbox.addEventListener('change', () => {
          void handlePluginToggle(instance, plugin, checkbox, row, stateText, statusElement);
        });
        control.appendChild(stateText);
        control.appendChild(switchLabel);
        row.appendChild(info);
        row.appendChild(control);
        list.appendChild(row);
      }
      section.appendChild(list);
    } else if (plugins.status === 'ok') {
      section.appendChild(makeElement('p', 'instance-v3-empty', plugins.hasList ? '未发现可管理的插件。' : '服务端没有返回插件列表数据。'));
    }
    section.appendChild(statusElement);
    return section;
  }

  const AI_TOOL_PERMISSIONS = [
    {
      key: 'safe_tools_enabled',
      label: '允许机器人自己调用插件能力',
      description: '总开关。仅开放下列白名单能力，不会开放电脑控制、终端或任意文件访问。',
      master: true
    },
    {
      key: 'safe_tools_allow_search_images',
      label: '搜索图片',
      description: '允许机器人在需要配图时搜索图片。仅白名单；无电脑控制、终端或任意文件访问。'
    },
    {
      key: 'safe_tools_allow_send_sticker',
      label: '发送表情',
      description: '允许机器人从本地表情库选择并发送表情。仅白名单；无电脑控制、终端或任意文件访问。'
    },
    {
      key: 'safe_tools_allow_collect_sticker',
      label: '收藏表情',
      description: '允许机器人把聊天中的图片或表情收藏到本地表情库。仅白名单；无电脑控制、终端或任意文件访问。'
    },
    {
      key: 'safe_tools_allow_list_categories',
      label: '查询表情分类',
      description: '允许机器人查询本地表情分类名称。仅白名单；无电脑控制、终端或任意文件访问。'
    },
    {
      key: 'safe_tools_allow_fetch_webpage',
      label: '读取公开网页',
      description: '允许 fetch_webpage 读取公开 HTTP(S) 网页；有 SSRF/大小/超时/重定向限制。'
    },
    {
      key: 'web_auto_fetch_enabled',
      label: '自动读取消息里的 URL',
      description: '消息含 URL 且处于阅读/提问语境时自动抓取网页正文；裸链接不触发。'
    },
    {
      key: 'web_auto_fetch_question_only',
      label: '仅提问语境自动读取',
      description: '只有“怎么安装/这篇文章讲了什么”等阅读语境才自动抓取，普通闲聊不抓。'
    },
    {
      key: 'safe_tools_allow_search_food_restaurants',
      label: '搜索饭店',
      description: '允许 search_food_restaurants 按名称/标签/位置查询饭店。'
    },
    {
      key: 'safe_tools_allow_submit_food_review',
      label: '提交饭店评价',
      description: '允许 submit_food_review 在明确添加意图下提交评价；不泄露发送者 QQ。'
    },
    {
      key: 'food_enabled',
      label: 'Food 功能总开关',
      description: '允许插件处理 Food 相关命令和自然语言请求。'
    },
    {
      key: 'food_search_enabled',
      label: 'Food 自然语言搜索',
      description: '允许自然语言搜索饭店；与手动工具开关独立。'
    }
  ];

  const AI_STICKER_SWITCHES = [
    {
      key: 'sticker_enabled',
      label: '启用自动表情',
      description: '总开关。允许插件在回复中附带本地表情，不会执行电脑控制、终端或任意文件操作。',
      master: true
    },
    {
      key: 'sticker_ai_select_enabled',
      id: 'ai-sticker-ai-select-enabled',
      label: '允许 AI 主动选择表情',
      description: '由 AI 按语境从本地表情库选择；仅白名单本地资源，无电脑控制。'
    },
    {
      key: 'sticker_ai_marker_enabled',
      id: 'ai-sticker-ai-marker-enabled',
      label: '启用安全表情 marker',
      description: 'direct 模式下由插件给模型提供安全 marker，模型只能引用允许的表情；无文件/终端访问。'
    },
    {
      key: 'collected_sticker_enabled',
      id: 'ai-sticker-collected-enabled',
      label: '使用已收藏表情',
      description: '允许从本实例已收藏的表情中选择；不访问任意文件路径。'
    }
  ];
  const AI_STICKER_NUMBERS = [
    { key: 'sticker_ai_select_probability', id: 'ai-sticker-ai-select-probability', label: 'AI 主动选择表情概率', min: 0, max: 1, step: 0.01, defaultValue: 0.6 },
    { key: 'sticker_probability', id: 'ai-sticker-probability', label: '自动带表情概率', min: 0, max: 1, step: 0.01, defaultValue: 0.3 },
    { key: 'sticker_cooldown_seconds', id: 'ai-sticker-cooldown-seconds', label: '表情冷却时间（秒）', min: 0, max: 86400, step: 1, defaultValue: 0, integer: true }
  ];
  const AI_STICKER_KEYS = [
    ...AI_STICKER_SWITCHES.map((item) => item.key),
    ...AI_STICKER_NUMBERS.map((item) => item.key)
  ];
  const AI_ALL_CONFIG_KEYS = [
    ...AI_TOOL_PERMISSIONS.map((item) => item.key),
    ...AI_STICKER_KEYS
  ];

  function aiStickerNumberValue(snapshot, field) {
    const values = snapshot ? asRecord(snapshot.values) : {};
    if (Object.prototype.hasOwnProperty.call(values, field.key)) {
      const value = pluginConfigService.pluginInputText(values[field.key]);
      if (value !== '') return value;
    }
    const schemaField = snapshot && Array.isArray(snapshot.schema)
      ? snapshot.schema.find((item) => item.key === field.key)
      : null;
    if (schemaField && schemaField.hasDefault) {
      const value = pluginConfigService.pluginInputText(schemaField.default);
      if (value !== '') return value;
    }
    return String(field.defaultValue);
  }

  function aiToolPluginRecord(instance) {
    const status = instance && instance.v3 && instance.v3.plugins ? instance.v3.plugins : null;
    const items = status && Array.isArray(status.items) ? status.items : [];
    return items.find((plugin) => plugin.id === 'qqbot_enhancer') || null;
  }

  function aiToolValue(snapshot, key) {
    if (!snapshot) return false;
    const values = asRecord(snapshot.values);
    if (Object.prototype.hasOwnProperty.call(values, key)) return boolOrUndefined(values[key]) === true;
    const field = (snapshot.schema || []).find((item) => item.key === key);
    return Boolean(field && field.hasDefault && boolOrUndefined(field.default) === true);
  }

  function setAiToolsStatus(ui, message, tone = 'info') {
    if (!ui || !ui.status) return;
    ui.status.textContent = sanitisePluginText(message) || '';
    ui.status.className = `ai-tools-status${tone ? ` is-${tone}` : ''}`;
    ui.status.hidden = !message;
  }

  function setAiToolsError(ui, message) {
    if (!ui || !ui.error) return;
    ui.error.textContent = sanitisePluginText(message) || '';
    ui.error.hidden = !message;
  }

  function updateAiToolsHint(ui) {
    if (!ui || !ui.hint) return;
    if (ui.loadingConfig && !ui.unsupportedReason) {
      ui.hint.textContent = '正在读取 AI 工具权限与自动表情参数…';
      return;
    }
    if (!ui.supported) {
      ui.hint.textContent = ui.unsupportedReason || '当前后端不支持 AI 工具权限。';
      return;
    }
    if (!ui.pluginEnabled) {
      ui.hint.textContent = 'QQ 增强插件当前已禁用。请先在插件管理中启用插件，再调整 AI 工具权限；当前开关不可操作。';
      return;
    }
    const safeMaster = ui.inputs.safe_tools_enabled ? ui.inputs.safe_tools_enabled.checked : false;
    const stickerMaster = ui.inputs.sticker_enabled ? ui.inputs.sticker_enabled.checked : false;
    ui.hint.textContent = `仅开放白名单插件能力和本地表情库；无电脑控制、终端、Shell 或任意文件访问。当前安全工具总开关：${safeMaster ? '开' : '关'}；自动表情总开关：${stickerMaster ? '开' : '关'}。`;
  }

  function syncAiToolsControlState(ui) {
    if (!ui) return;
    const safeMaster = ui.inputs.safe_tools_enabled ? ui.inputs.safe_tools_enabled.checked : false;
    const stickerMaster = ui.inputs.sticker_enabled ? ui.inputs.sticker_enabled.checked : false;
    const disabledBase = !ui.supported || !ui.pluginEnabled || ui.loadingConfig || ui.saving;
    for (const permission of AI_TOOL_PERMISSIONS) {
      const input = ui.inputs[permission.key];
      if (!input) continue;
      input.disabled = disabledBase || (!permission.master && permission.key.startsWith('safe_tools_allow_') && !safeMaster);
      const option = input.closest('.ai-tool-option');
      if (option) option.classList.toggle('is-disabled', input.disabled);
    }
    for (const permission of AI_STICKER_SWITCHES) {
      const input = ui.inputs[permission.key];
      if (!input) continue;
      input.disabled = disabledBase || (!permission.master && !stickerMaster);
      const option = input.closest('.ai-tool-option');
      if (option) option.classList.toggle('is-disabled', input.disabled);
    }
    for (const field of AI_STICKER_NUMBERS) {
      const input = ui.numberInputs[field.key];
      if (!input) continue;
      input.disabled = disabledBase || !stickerMaster;
      const wrapper = input.closest('.ai-sticker-number-field');
      if (wrapper) wrapper.classList.toggle('is-disabled', input.disabled);
    }
    for (const [key, status] of Object.entries(ui.moduleStatuses || {})) {
      if (!status) continue;
      let enabled = false;
      if (key === 'safe-tools') enabled = Boolean(ui.inputs.safe_tools_enabled && ui.inputs.safe_tools_enabled.checked);
      else if (key === 'sticker') enabled = Boolean(ui.inputs.sticker_enabled && ui.inputs.sticker_enabled.checked);
      else {
        const moduleKeys = key === 'web-search'
          ? ['safe_tools_allow_fetch_webpage', 'web_auto_fetch_enabled', 'web_auto_fetch_question_only']
          : ['safe_tools_allow_search_food_restaurants', 'safe_tools_allow_submit_food_review', 'food_enabled', 'food_search_enabled'];
        const count = moduleKeys.filter((item) => ui.inputs[item] && ui.inputs[item].checked).length;
        enabled = count > 0;
        status.textContent = count > 0 ? `已开启 ${count} 项` : '未开启';
        status.className = `ai-tools-module-status is-${enabled ? 'enabled' : 'disabled'}`;
        continue;
      }
      status.textContent = enabled ? '已开启' : '已关闭';
      status.className = `ai-tools-module-status is-${enabled ? 'enabled' : 'disabled'}`;
    }
    if (ui.saveButton) ui.saveButton.disabled = disabledBase;
    if (ui.controls) ui.controls.hidden = !ui.supported;
    if (ui.actions) ui.actions.hidden = !ui.supported;
    if (ui.retryButton) ui.retryButton.hidden = !(ui.loadFailed || ui.unsupportedReason);
    if (ui.section) {
      ui.section.classList.toggle('is-plugin-disabled', !ui.pluginEnabled);
      ui.section.classList.toggle('is-busy', Boolean(ui.loadingConfig || ui.saving));
    }
    updateAiToolsHint(ui);
  }

  function buildAiToolsSection(instance) {
    const section = makeElement('section', 'instance-v3-section ai-tools-section');
    section.dataset.aiToolsInstanceId = instance.id;
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-shield'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', 'AI 工具权限'));
    headingCopy.appendChild(makeElement('p', '', '控制 qqbot_enhancer 的白名单 AI 工具与自动表情；默认不开放系统控制能力。'));
    heading.appendChild(headingCopy);
    section.appendChild(heading);

    const plugin = aiToolPluginRecord(instance);
    const pluginEnabled = Boolean(plugin && plugin.enabled === true);
    const pluginState = makeElement('span', 'ai-tools-plugin-state', pluginEnabled ? '插件已启用' : '插件未启用');
    pluginState.className = `ai-tools-plugin-state is-${pluginEnabled ? 'enabled' : 'disabled'}`;
    const pluginRow = makeElement('div', 'ai-tools-plugin-row');
    pluginRow.appendChild(makeElement('span', 'ai-tools-plugin-label', 'QQ 增强插件'));
    pluginRow.appendChild(pluginState);
    section.appendChild(pluginRow);
    const hint = makeElement('p', 'ai-tools-hint');
    hint.setAttribute('id', 'ai-tools-hint');
    section.appendChild(hint);
    const loading = makeElement('p', 'ai-tools-loading', '正在读取 AI 工具权限…');
    loading.setAttribute('id', 'ai-tools-loading');
    loading.setAttribute('role', 'status');
    section.appendChild(loading);

    const controls = makeElement('div', 'ai-tools-controls');
    controls.setAttribute('id', 'ai-tools-controls');
    controls.hidden = true;
    const inputs = {};
    const numberInputs = {};
    const moduleStatuses = {};

    function makeCheckboxOption(permission, master = false) {
      const option = makeElement('label', master ? 'ai-tool-option is-master' : 'ai-tool-option');
      const input = document.createElement('input');
      input.setAttribute('type', 'checkbox');
      input.id = master
        ? (permission.key === 'safe_tools_enabled' ? 'ai-tools-enabled' : 'ai-sticker-enabled')
        : (permission.id || `ai-tools-${permission.key.replace(/_/g, '-')}`);
      if (master) input.dataset.aiToolKey = permission.key;
      else input.dataset.aiToolKey = permission.key;
      inputs[permission.key] = input;
      const copy = makeElement('span', 'ai-tool-copy');
      copy.appendChild(makeElement('strong', '', permission.label));
      copy.appendChild(makeElement('small', '', permission.description));
      option.appendChild(input);
      option.appendChild(copy);
      input.addEventListener('change', () => {
        setAiToolsError(ui, '');
        setAiToolsStatus(ui, '', '');
        syncAiToolsControlState(ui);
      });
      return option;
    }

    function makeNumberOption(field) {
      const wrapper = makeElement('label', 'ai-sticker-number-field');
      wrapper.appendChild(makeElement('strong', '', field.label));
      const input = document.createElement('input');
      input.setAttribute('type', 'number');
      input.setAttribute('inputmode', 'decimal');
      input.setAttribute('min', String(field.min));
      input.setAttribute('max', String(field.max));
      input.setAttribute('step', String(field.step));
      input.id = field.id || `ai-sticker-${field.key.replace(/^sticker_/, '').replace(/_/g, '-')}`;
      input.dataset.aiStickerKey = field.key;
      input.value = String(field.defaultValue);
      numberInputs[field.key] = input;
      wrapper.appendChild(input);
      wrapper.appendChild(makeElement('small', '', field.key === 'sticker_cooldown_seconds'
        ? '两次自动表情之间的最小间隔；0 表示不增加额外冷却。'
        : `0–1 之间的概率；默认 ${field.defaultValue}。`));
      input.addEventListener('input', () => {
        setAiToolsError(ui, '');
        setAiToolsStatus(ui, '', '');
      });
      return wrapper;
    }

    function makeModule(key, title, description, options, masterKey, numberFields = []) {
      const module = makeElement('div', 'ai-tools-module');
      module.dataset.aiToolsModule = key;
      module.classList.toggle('is-open', key === 'safe-tools');
      const summary = makeElement('button', 'ai-tools-module-summary');
      summary.type = 'button';
      summary.setAttribute('aria-expanded', key === 'safe-tools' ? 'true' : 'false');
      const copy = makeElement('span', 'ai-tools-module-copy');
      copy.appendChild(makeElement('strong', '', title));
      copy.appendChild(makeElement('small', '', description));
      const status = makeElement('span', 'ai-tools-module-status', '读取中');
      summary.appendChild(copy);
      summary.appendChild(status);
      moduleStatuses[key] = status;

      const body = makeElement('div', 'ai-tools-module-body');
      body.hidden = key !== 'safe-tools';
      summary.addEventListener('click', () => {
        body.hidden = !body.hidden;
        module.classList.toggle('is-open', !body.hidden);
        summary.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
      });
      const master = AI_TOOL_PERMISSIONS.find((item) => item.key === masterKey)
        || AI_STICKER_SWITCHES.find((item) => item.key === masterKey);
      if (master) body.appendChild(makeCheckboxOption(master, true));
      const grid = makeElement('div', 'ai-tools-grid');
      for (const permission of options) grid.appendChild(makeCheckboxOption(permission, false));
      body.appendChild(grid);
      if (numberFields.length) {
        const numberGrid = makeElement('div', 'ai-sticker-number-grid');
        for (const field of numberFields) numberGrid.appendChild(makeNumberOption(field));
        body.appendChild(numberGrid);
      }
      module.appendChild(summary);
      module.appendChild(body);
      controls.appendChild(module);
    }

    makeModule(
      'safe-tools',
      '安全工具',
      '只开放白名单插件能力；无电脑控制、终端、Shell 或任意文件访问。',
      ['safe_tools_allow_search_images', 'safe_tools_allow_send_sticker', 'safe_tools_allow_collect_sticker', 'safe_tools_allow_list_categories']
        .map((key) => AI_TOOL_PERMISSIONS.find((item) => item.key === key))
        .filter(Boolean),
      'safe_tools_enabled'
    );
    makeModule(
      'web-search',
      '搜索与网页',
      '网页读取、URL 自动阅读和搜索相关能力；受安全工具总开关和插件超时/大小限制约束。',
      [
        AI_TOOL_PERMISSIONS.find((item) => item.key === 'safe_tools_allow_fetch_webpage'),
        AI_TOOL_PERMISSIONS.find((item) => item.key === 'web_auto_fetch_enabled'),
        AI_TOOL_PERMISSIONS.find((item) => item.key === 'web_auto_fetch_question_only'),
      ].filter(Boolean),
      null
    );
    makeModule(
      'food-tools',
      'Food 工具',
      '饭店搜索与评价提交；评论只写 food.dlsz.top，密码仍由插件内部注入，不进入模型。',
      [
        AI_TOOL_PERMISSIONS.find((item) => item.key === 'safe_tools_allow_search_food_restaurants'),
        AI_TOOL_PERMISSIONS.find((item) => item.key === 'safe_tools_allow_submit_food_review'),
        AI_TOOL_PERMISSIONS.find((item) => item.key === 'food_enabled'),
        AI_TOOL_PERMISSIONS.find((item) => item.key === 'food_search_enabled'),
      ].filter(Boolean),
      null
    );
    makeModule(
      'sticker',
      'AI 自动表情',
      '从本实例本地表情库选择；direct 模式由插件给模型提供安全表情 marker；memory/astrbot 模式使用本地情绪或分类匹配；不会读取任意文件或执行系统命令。',
      AI_STICKER_SWITCHES.slice(1),
      'sticker_enabled',
      AI_STICKER_NUMBERS
    );
    section.appendChild(controls);

    const fullConfigHint = makeElement(
      'p',
      'ai-tools-full-config-hint',
      '这里是安全快捷面板；完整参数仍在「插件管理 → qqbot_enhancer」中维护。'
    );
    section.appendChild(fullConfigHint);


    const actions = makeElement('div', 'ai-tools-actions');
    const saveButton = makeElement('button', 'btn btn-primary btn-small', '保存 AI 工具与表情设置');
    saveButton.type = 'button';
    saveButton.setAttribute('id', 'ai-tools-save');
    actions.appendChild(saveButton);
    section.appendChild(actions);
    const status = makeElement('p', 'ai-tools-status');
    status.setAttribute('id', 'ai-tools-status');
    status.setAttribute('role', 'status');
    status.hidden = true;
    section.appendChild(status);
    const error = makeElement('p', 'ai-tools-error');
    error.setAttribute('id', 'ai-tools-error');
    error.setAttribute('role', 'alert');
    error.hidden = true;
    section.appendChild(error);
    const retryButton = makeElement('button', 'btn btn-quiet btn-small ai-tools-retry', '刷新重试');
    retryButton.type = 'button';
    retryButton.setAttribute('id', 'ai-tools-retry');
    retryButton.hidden = true;
    section.appendChild(retryButton);
    const cachedValues = lastKnownAiToolsValues.get(instance.id) || null;
    if (cachedValues) {
      for (const permission of [...AI_TOOL_PERMISSIONS, ...AI_STICKER_SWITCHES]) {
        if (inputs[permission.key]) inputs[permission.key].checked = boolOrUndefined(cachedValues[permission.key]) === true;
      }
      for (const field of AI_STICKER_NUMBERS) {
        if (numberInputs[field.key] && cachedValues[field.key] !== undefined) {
          numberInputs[field.key].value = pluginConfigService.pluginInputText(cachedValues[field.key]);
        }
      }
    }

    const ui = {
      section,
      controls,
      actions,
      saveButton,
      status,
      error,
      retryButton,
      hint,
      loading,
      pluginState,
      inputs,
      numberInputs,
      moduleStatuses,
      supported: Boolean(cachedValues),
      unsupportedReason: '',
      loadFailed: false,
      pluginEnabled,
      loadingConfig: true,
      saving: false
    };
    saveButton.addEventListener('click', () => void saveAiToolsConfig(instance, ui));
    retryButton.addEventListener('click', () => void loadAiToolsConfig(instance, ui));
    syncAiToolsControlState(ui);
    void loadAiToolsConfig(instance, ui);
    return section;
  }

  async function loadAiToolsConfig(instance, ui) {
    ui.loadingConfig = true;
    ui.loadFailed = false;
    ui.supported = Boolean(lastKnownAiToolsValues.get(instance.id));
    ui.unsupportedReason = '';
    ui.loading.hidden = false;
    setAiToolsError(ui, '');
    setAiToolsStatus(ui, '', '');
    syncAiToolsControlState(ui);
    try {
      const result = await pluginConfigService.load(instance.id, 'qqbot_enhancer');
      if (!ui.section.isConnected) return;
      const snapshot = result.snapshot;
      const availableKeys = new Set((snapshot.schema || []).map((field) => field.key));
      const missing = AI_ALL_CONFIG_KEYS.filter((key) => !availableKeys.has(key));
      if (!availableKeys.size) {
        ui.loadFailed = true;
        ui.supported = false;
        ui.unsupportedReason = '当前插件版本没有可用的 AI 工具参数，请先更新 qqbot_enhancer 插件。';
        ui.loadingConfig = false;
        ui.loading.hidden = true;
        setAiToolsError(ui, ui.unsupportedReason);
        syncAiToolsControlState(ui);
        return;
      }
      if (missing.length > 0) {
        ui.schemaWarning = `当前插件未提供部分参数：${missing.join(', ')}；已隐藏/禁用这些项，其余仍可保存。`;
      }
      for (const permission of [...AI_TOOL_PERMISSIONS, ...AI_STICKER_SWITCHES]) {
        const input = ui.inputs[permission.key];
        if (input) input.checked = aiToolValue(snapshot, permission.key);
      }
      for (const field of AI_STICKER_NUMBERS) {
        const input = ui.numberInputs[field.key];
        if (input) input.value = aiStickerNumberValue(snapshot, field);
      }
      const cachedValues = {};
      for (const permission of [...AI_TOOL_PERMISSIONS, ...AI_STICKER_SWITCHES]) {
        if (ui.inputs[permission.key]) cachedValues[permission.key] = Boolean(ui.inputs[permission.key].checked);
      }
      for (const field of AI_STICKER_NUMBERS) {
        const input = ui.numberInputs[field.key];
        if (input) cachedValues[field.key] = Number(input.value);
      }
      lastKnownAiToolsValues.set(instance.id, cachedValues);
      ui.supported = true;
      ui.loadFailed = false;
      ui.loadingConfig = false;
      ui.loading.hidden = true;
      if (ui.schemaWarning) {
        setAiToolsStatus(ui, ui.schemaWarning, 'warning');
      }
      if (!snapshot.apiAvailable) {
        setAiToolsStatus(ui, 'AstrBot 当前离线；保存会写入本地配置，可能需要启动或重启 AstrBot 后生效。', 'warning');
      }
      syncAiToolsControlState(ui);
    } catch (error) {
      if (!ui.section.isConnected) return;
      ui.loadingConfig = false;
      ui.loading.hidden = true;
      const unsupported = isMissingEndpointError(error)
        || pluginConfigUnsupportedMessage(error instanceof ApiError ? error.message : '');
      ui.loadFailed = true;
      ui.supported = Boolean(lastKnownAiToolsValues.get(instance.id));
      ui.unsupportedReason = unsupported
        ? '当前后端不支持 AI 工具权限。'
        : formatError(error, 'AI 工具权限读取失败。');
      setAiToolsError(ui, ui.unsupportedReason);
      syncAiToolsControlState(ui);
    }
  }

  async function saveAiToolsConfig(instance, ui) {
    if (!ui.supported || !ui.pluginEnabled || ui.loadingConfig || ui.saving) return;
    const values = {};
    for (const permission of [...AI_TOOL_PERMISSIONS, ...AI_STICKER_SWITCHES]) {
      const input = ui.inputs[permission.key];
      if (input) values[permission.key] = Boolean(input.checked);
    }
    for (const field of AI_STICKER_NUMBERS) {
      const input = ui.numberInputs[field.key];
      const raw = input ? input.value.trim() : '';
      const number = Number(raw);
      if (raw === '' || !Number.isFinite(number) || number < field.min || number > field.max || (field.integer && !Number.isInteger(number))) {
        setAiToolsError(ui, `请输入有效的${field.label}（${field.min}–${field.max}）。`);
        if (input) input.focus({ preventScroll: true });
        return;
      }
      values[field.key] = number;
    }
    ui.saving = true;
    setAiToolsError(ui, '');
    setAiToolsStatus(ui, '正在保存 AI 工具与表情设置…', 'info');
    const settingsBusy = instanceSettings.isOpenFor(instance.id);
    if (settingsBusy) instanceSettings.beginBusy();
    syncAiToolsControlState(ui);
    try {
      const data = await pluginConfigService.save(instance.id, 'qqbot_enhancer', values);
      if (!ui.section.isConnected) return;
      const response = asRecord(data);
      const restart = boolOrUndefined(response.restart_required) === true;
      const apiAvailable = boolOrUndefined(response.api_available) !== false;
      lastKnownAiToolsValues.set(instance.id, { ...values });
      const outcome = restart
        ? '已保存；AstrBot 需要重启后生效。'
        : apiAvailable
          ? '已保存；AstrBot 已热重载。'
          : '已保存；AstrBot 热重载不可用，请重启后生效。';
      const detail = sanitisePluginText(response.message || response.error);
      setAiToolsStatus(ui, detail ? `${outcome} ${detail}` : outcome, restart || !apiAvailable ? 'warning' : 'success');
      setAiToolsError(ui, '');
    } catch (error) {
      if (!ui.section.isConnected) return;
      const message = formatError(error, 'AI 工具与表情设置保存失败。');
      const unsupported = isMissingEndpointError(error) || pluginConfigUnsupportedMessage(message);
      if (unsupported) {
        ui.supported = false;
        ui.unsupportedReason = '当前后端不支持 AI 工具权限。';
      }
      setAiToolsError(ui, unsupported ? ui.unsupportedReason : message);
      setAiToolsStatus(ui, '', '');
    } finally {
      ui.saving = false;
      if (settingsBusy) instanceSettings.endBusy();
      syncAiToolsControlState(ui);
    }
  }

  const PROMPT_NAME_MAX_LENGTH = 80;
  const PROMPT_TEXT_MAX_LENGTH = 12000;
  const PROMPT_REQUEST_TIMEOUT_MS = 15000;
  const PROMPT_DELETE_CONFIRM_MS = 5000;
  let promptDeleteConfirmTimer = null;

  function withPromptTimeout(promise, timeoutMs = PROMPT_REQUEST_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const timer = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error('提示词请求超时，请检查本地服务后重试。'));
      }, timeoutMs);
      promise.then((value) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        resolve(value);
      }, (error) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        reject(error);
      });
    });
  }

  const LEGACY_PROMPT_FIELD_KEYS = new Set(['system_prompt', 'prompt_profiles', 'prompt_profile_active_id']);

  function isLegacyPromptProfileField(key) {
    return LEGACY_PROMPT_FIELD_KEYS.has(textValue(key, '').trim().toLowerCase());
  }

  function filterLegacyPromptSnapshot(snapshot, instanceId) {
    if (!snapshot || !state.promptManager.profilesAvailable || state.promptManager.profilesInstanceId !== instanceId) return snapshot;
    const schema = (snapshot.schema || []).filter((field) => !isLegacyPromptProfileField(field.key));
    if (schema.length === (snapshot.schema || []).length) return snapshot;
    const values = {};
    for (const field of schema) {
      if (Object.prototype.hasOwnProperty.call(snapshot.values || {}, field.key)) values[field.key] = snapshot.values[field.key];
    }
    const groups = pluginConfigService.groupPluginFields(schema, snapshot.uiGroups);
    return { ...snapshot, schema, groups, values };
  }

  function hideLegacyPromptFieldsInOpenEditor(instanceId) {
    const config = state.pluginConfig;
    if (!config.open || config.instanceId !== instanceId || config.saving) return;
    for (const entry of config.entries) {
      if (isLegacyPromptProfileField(entry.key)) {
        entry.legacyHidden = true;
        entry.element.hidden = true;
      }
    }
    if (config.snapshot) config.snapshot = filterLegacyPromptSnapshot(config.snapshot, instanceId);
  }

  async function probePromptProfiles(instance) {
    if (!instance || !instance.id) return false;
    if (state.promptManager.profilesAvailable && state.promptManager.profilesInstanceId === instance.id) return true;
    try {
      const data = await withPromptTimeout(apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/prompts`));
      const normalised = data && data.ok !== false ? normalisePromptPayload(data) : null;
      if (!normalised) return false;
      state.promptManager.profilesAvailable = true;
      state.promptManager.profilesInstanceId = instance.id;
      hideLegacyPromptFieldsInOpenEditor(instance.id);
      return true;
    } catch (error) {
      return false;
    }
  }

  function promptIdValue(value) {
    const record = asRecord(value);
    return textValue(record.id || record.profile_id || value, '').trim();
  }

  function normalisePromptRecord(raw, index) {
    const item = asRecord(raw);
    return {
      id: textValue(item.id || item.profile_id || item.profileId || item.key, '').trim(),
      name: textValue(item.name || item.title || item.label, '') || `提示词 ${index + 1}`,
      prompt: textValue(item.prompt ?? item.prompt_text ?? item.content ?? item.text ?? item.system_prompt, ''),
      pinned: boolOrUndefined(item.pinned ?? item.is_pinned) === true,
      updatedAt: textValue(item.updated_at || item.updatedAt || item.modified_at || item.created_at || item.createdAt, '')
    };
  }

  function normalisePromptPayload(raw) {
    const root = asRecord(raw);
    let promptItems = null;
    if (Array.isArray(root.prompts)) promptItems = root.prompts;
    else if (Array.isArray(root.items)) promptItems = root.items;
    else if (Array.isArray(root.profiles)) promptItems = root.profiles;
    if (!promptItems) return null;
    let prompts = promptItems.map(normalisePromptRecord).filter((item) => item.id || item.name || item.prompt);
    let activeId = promptIdValue(root.active_id ?? root.activeId ?? root.active_profile_id ?? root.activeProfileId ?? root.current_id ?? root.currentId ?? root.current);
    let pinnedIndex = prompts.findIndex((item) => item.id && item.id === activeId);
    if (pinnedIndex < 0) pinnedIndex = prompts.findIndex((item) => item.pinned);
    if (pinnedIndex >= 0) {
      activeId = prompts[pinnedIndex].id;
      prompts = prompts.map((item, index) => ({ ...item, pinned: index === pinnedIndex }));
    } else {
      activeId = '';
      prompts = prompts.map((item) => ({ ...item, pinned: false }));
    }
    prompts.sort((left, right) => Number(right.pinned) - Number(left.pinned));
    return { prompts, activeId };
  }

  function formatPromptTime(value) {
    const raw = textValue(value, '').trim();
    if (!raw) return '更新时间未知';
    try {
      const date = new Date(raw);
      if (Number.isNaN(date.getTime())) return `更新于 ${raw}`;
      return `更新于 ${date.toLocaleString('zh-CN', { hour12: false })}`;
    } catch (error) {
      return `更新于 ${raw}`;
    }
  }

  function promptSummary(value) {
    const text = textValue(value, '').trim();
    if (!text) return '（空提示词）';
    return text.length > 180 ? `${text.slice(0, 180)}…` : text;
  }

  function promptActionFeedback(data, action = '') {
    const root = asRecord(data);
    const message = action === 'activate'
      ? '已设为默认模板；仅影响之后新建的会话，不会切换或重建已有会话。'
      : sanitisePluginText(root.message);
    const parts = [message || '提示词模板已更新。'];
    if (boolOrUndefined(root.restart_required) === true) parts.push('需要重启 AstrBot 后生效。');
    return { message: parts.join(' '), restart: boolOrUndefined(root.restart_required) === true };
  }

  function setPromptManagerError(message) {
    const error = $('prompt-manager-error');
    if (!error) return;
    error.textContent = sanitisePluginText(message) || '';
    error.hidden = !message;
  }

  function setPromptManagerStatus(message, warning = false) {
    const status = $('prompt-manager-status');
    if (!status) return;
    status.textContent = sanitisePluginText(message) || '';
    status.className = `prompt-manager-status${warning ? ' is-warning' : ''}`;
    status.hidden = !message;
  }

  function syncPromptManagerAriaBusy() {
    const modal = $('prompt-manager-modal');
    const config = state.promptManager;
    if (modal) modal.setAttribute('aria-busy', config.busy || config.loading ? 'true' : 'false');
  }

  function clearPromptDeleteArm() {
    if (promptDeleteConfirmTimer) {
      window.clearTimeout(promptDeleteConfirmTimer);
      promptDeleteConfirmTimer = null;
    }
    state.promptManager.deleteArmedId = '';
  }

  function promptFocusableElements() {
    const modal = $('prompt-manager-modal');
    if (!modal) return [];
    return Array.from(modal.querySelectorAll('button, input, textarea, select, a[href]')).filter((element) => {
      if (element.disabled || element.hidden) return false;
      let ancestor = element;
      while (ancestor) {
        if (ancestor.hidden) return false;
        ancestor = ancestor.parentNode;
      }
      return true;
    });
  }

  function trapPromptModalFocus(event) {
    if (event.key !== 'Tab' || !state.promptManager.open) return false;
    const modal = $('prompt-manager-modal');
    if (!modal) return false;
    const focusable = promptFocusableElements();
    if (focusable.length === 0) {
      event.preventDefault();
      modal.focus({ preventScroll: true });
      return true;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    let inside = false;
    let node = active;
    while (node) {
      if (node === modal) { inside = true; break; }
      node = node.parentNode;
    }
    if (!inside) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus({ preventScroll: true });
      return true;
    }
    if (focusable.indexOf(active) === -1) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus({ preventScroll: true });
      return true;
    }
    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus({ preventScroll: true });
      return true;
    }
    if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus({ preventScroll: true });
      return true;
    }
    return false;
  }

  function cancelPromptOperation() {
    const config = state.promptManager;
    if (!config.open || !config.busy) return false;
    config.actionSeq += 1;
    config.requestSeq += 1;
    clearPromptDeleteArm();
    setPromptManagerBusy(false);
    setPromptManagerError('');
    setPromptManagerStatus('已取消等待；如果服务端请求稍后完成，请重新打开提示词模板库确认。', false);
    return true;
  }

  function setPromptFieldError(input, errorElement, message) {
    if (input) input.setAttribute('aria-invalid', message ? 'true' : 'false');
    if (errorElement) {
      errorElement.textContent = message || '';
      errorElement.hidden = !message;
    }
    if (message && input) input.focus({ preventScroll: true });
  }

  function clearPromptEditorErrors() {
    setPromptFieldError($('prompt-editor-name'), $('prompt-editor-name-error'), '');
    setPromptFieldError($('prompt-editor-prompt'), $('prompt-editor-prompt-error'), '');
  }

  function setPromptManagerBusy(busy) {
    const config = state.promptManager;
    config.busy = Boolean(busy);
    const closeButton = $('prompt-manager-close');
    const newButton = $('prompt-manager-new');
    const cancelButton = $('prompt-manager-cancel-busy');
    const form = $('prompt-manager-form');
    if (closeButton) closeButton.disabled = config.busy;
    if (newButton) newButton.disabled = config.busy || config.loading || config.unsupported || !config.open;
    if (cancelButton) {
      cancelButton.hidden = !config.busy;
      cancelButton.disabled = !config.busy;
    }
    if (form) {
      for (const control of form.querySelectorAll('input, textarea, button')) control.disabled = config.busy;
    }
    syncPromptManagerAriaBusy();
    renderPromptList();
  }

  function openPromptEditor(mode, profile) {
    const config = state.promptManager;
    if (!config.open || config.busy || config.unsupported) return;
    const prompt = asRecord(profile);
    config.editorMode = mode;
    config.editorProfileId = textValue(prompt.id, '');
    const form = $('prompt-manager-form');
    const title = $('prompt-editor-title');
    const nameInput = $('prompt-editor-name');
    const promptInput = $('prompt-editor-prompt');
    const promptField = $('prompt-editor-prompt-field');
    const saveButton = $('prompt-editor-save');
    if (!form || !nameInput || !promptInput) return;
    form.hidden = false;
    if (title) title.textContent = mode === 'create' ? '新增提示词模板' : mode === 'rename' ? '重命名提示词模板' : '编辑提示词模板';
    if (promptField) promptField.hidden = mode === 'rename';
    nameInput.value = textValue(prompt.name, '');
    promptInput.value = textValue(prompt.prompt, '');
    if (saveButton) saveButton.textContent = mode === 'create' ? '创建' : mode === 'rename' ? '重命名' : '保存';
    clearPromptEditorErrors();
    setPromptManagerError('');
    setPromptManagerStatus('', false);
    nameInput.focus({ preventScroll: true });
  }

  function closePromptEditor() {
    const config = state.promptManager;
    config.editorMode = '';
    config.editorProfileId = '';
    const form = $('prompt-manager-form');
    if (form) form.hidden = true;
    clearPromptEditorErrors();
  }

  function renderPromptList() {
    const config = state.promptManager;
    const list = $('prompt-manager-list');
    const empty = $('prompt-manager-empty');
    const loading = $('prompt-manager-loading');
    const newButton = $('prompt-manager-new');
    if (!list) return;
    syncPromptManagerAriaBusy();
    if (loading) loading.hidden = !config.loading;
    if (newButton) newButton.disabled = config.busy || config.loading || config.unsupported || !config.open;
    if (empty) {
      empty.hidden = config.loading || config.unsupported || config.prompts.length > 0;
      empty.textContent = '尚无提示词模板，点击“新增模板”创建。';
    }
    list.replaceChildren();
    if (config.unsupported || config.loading) return;
    const orderedPrompts = [...config.prompts].sort((left, right) => Number(right.pinned) - Number(left.pinned));
    for (const prompt of orderedPrompts) {
      const isDefault = Boolean(prompt.pinned || (prompt.id && prompt.id === config.activeId));
      const row = makeElement('article', `prompt-item${isDefault ? ' is-default' : ''}`);
      row.dataset.promptId = prompt.id;
      row.setAttribute('role', 'listitem');
      const top = makeElement('div', 'prompt-item-top');
      const titleWrap = makeElement('div', 'prompt-item-title-wrap');
      titleWrap.appendChild(makeElement('strong', 'prompt-item-name', prompt.name));
      if (isDefault) titleWrap.appendChild(makeElement('span', 'prompt-item-badge is-default', '默认模板'));
      top.appendChild(titleWrap);
      top.appendChild(makeElement('span', 'prompt-item-time', formatPromptTime(prompt.updatedAt)));
      row.appendChild(top);
      row.appendChild(makeElement('p', 'prompt-item-summary', promptSummary(prompt.prompt)));
      const actions = makeElement('div', 'prompt-item-actions');
      const activateButton = makeElement('button', 'btn btn-quiet btn-small', isDefault ? '默认模板' : '设为默认/置顶');
      activateButton.type = 'button';
      activateButton.disabled = config.busy || !prompt.id || isDefault || config.unsupported;
      if (!isDefault) activateButton.title = '设为新建会话的默认提示词；不会切换或重建已有会话';
      if (!isDefault) activateButton.addEventListener('click', () => void mutatePrompt('activate', prompt));
      actions.appendChild(activateButton);
      const editButton = makeElement('button', 'btn btn-quiet btn-small', '编辑');
      editButton.type = 'button';
      editButton.disabled = config.busy || !prompt.id || config.unsupported;
      editButton.addEventListener('click', () => openPromptEditor('edit', prompt));
      actions.appendChild(editButton);
      const renameButton = makeElement('button', 'btn btn-quiet btn-small', '重命名');
      renameButton.type = 'button';
      renameButton.disabled = config.busy || !prompt.id || config.unsupported;
      renameButton.addEventListener('click', () => openPromptEditor('rename', prompt));
      actions.appendChild(renameButton);
      const deleteArmed = Boolean(prompt.id && config.deleteArmedId === prompt.id);
      const deleteButton = makeElement('button', `btn btn-quiet btn-small prompt-delete-btn${deleteArmed ? ' is-confirm' : ''}`, deleteArmed ? '确认删除' : '删除');
      deleteButton.type = 'button';
      deleteButton.disabled = config.busy || config.prompts.length <= 1 || !prompt.id || config.unsupported;
      if (config.prompts.length <= 1) deleteButton.title = '至少保留一个提示词，不能删除最后一个';
      if (deleteArmed) deleteButton.title = '再次点击确认删除；删除不可撤销';
      deleteButton.addEventListener('click', () => requestPromptDelete(prompt));
      actions.appendChild(deleteButton);
      row.appendChild(actions);
      list.appendChild(row);
    }
  }

  function requestPromptDelete(profile) {
    const config = state.promptManager;
    const prompt = asRecord(profile);
    if (!config.open || config.busy || config.unsupported || !prompt.id) return;
    if (config.prompts.length <= 1) {
      setPromptManagerError('至少保留一个提示词，不能删除最后一个。');
      return;
    }
    if (config.deleteArmedId !== prompt.id) {
      if (promptDeleteConfirmTimer) window.clearTimeout(promptDeleteConfirmTimer);
      config.deleteArmedId = prompt.id;
      setPromptManagerError('');
      setPromptManagerStatus(`再次点击“确认删除”将删除提示词“${prompt.name}”；删除不可撤销。`, true);
      renderPromptList();
      promptDeleteConfirmTimer = window.setTimeout(() => {
        if (state.promptManager.open && state.promptManager.deleteArmedId === prompt.id) {
          state.promptManager.deleteArmedId = '';
          promptDeleteConfirmTimer = null;
          setPromptManagerStatus('删除确认已超时，请重新点击“删除”。', true);
          renderPromptList();
        }
      }, PROMPT_DELETE_CONFIRM_MS);
      return;
    }
    if (promptDeleteConfirmTimer) {
      window.clearTimeout(promptDeleteConfirmTimer);
      promptDeleteConfirmTimer = null;
    }
    config.deleteArmedId = '';
    setPromptManagerStatus('', false);
    void mutatePrompt('delete', prompt);
  }

  async function loadPromptList(instance, options = {}) {
    const config = state.promptManager;
    const seq = ++config.requestSeq;
    config.loading = true;
    if (!options.keepFeedback) {
      setPromptManagerError('');
      setPromptManagerStatus('', false);
    }
    renderPromptList();
    try {
      const data = await withPromptTimeout(apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/prompts`));
      if (!config.open || seq !== config.requestSeq) return false;
      if (!data || data.ok === false) {
        throw new ApiError(sanitisePluginText(asRecord(data).message || asRecord(data).error) || '提示词模板库读取失败。', { data });
      }
      const normalised = normalisePromptPayload(data);
      if (!normalised) {
        config.loading = false;
        config.unsupported = true;
        config.prompts = [];
        config.activeId = '';
        setPromptManagerError('当前后端不支持提示词列表。');
        renderPromptList();
        return false;
      }
      config.prompts = normalised.prompts;
      config.activeId = normalised.activeId;
      config.unsupported = false;
      config.profilesAvailable = true;
      config.profilesInstanceId = instance.id;
      hideLegacyPromptFieldsInOpenEditor(instance.id);
      config.loading = false;
      renderPromptList();
      return true;
    } catch (error) {
      if (!config.open || seq !== config.requestSeq) return false;
      config.loading = false;
      const unsupported = isMissingEndpointError(error)
        || pluginConfigUnsupportedMessage(error instanceof ApiError ? error.message : '')
        || /提示词.*(?:未提供|不支持|不存在)|(?:未提供|不支持|不存在).*提示词/i.test(error instanceof ApiError ? error.message : '');
      config.unsupported = unsupported;
      if (unsupported) {
        config.prompts = [];
        config.profilesAvailable = false;
        config.profilesInstanceId = '';
      }
      setPromptManagerError(unsupported ? '当前后端不支持提示词列表。' : formatError(error, '提示词模板库读取失败。'));
      renderPromptList();
      return false;
    }
  }

  async function openPromptManager(instance, trigger) {
    if (!instance || !instance.id) return false;
    const config = state.promptManager;
    config.open = true;
    config.loading = true;
    config.busy = false;
    config.unsupported = false;
    config.instanceId = instance.id;
    config.prompts = [];
    config.activeId = '';
    config.editorMode = '';
    config.editorProfileId = '';
    config.deleteArmedId = '';
    config.trigger = trigger || null;
    clearPromptDeleteArm();
    const overlay = $('prompt-manager-overlay');
    if (overlay) overlay.hidden = false;
    setText('prompt-manager-title', '提示词模板库');
    setText('prompt-manager-subtitle', `实例：${instance.name || instance.id} · ${instance.id} · 置顶模板仅作为新会话默认值`);
    const list = $('prompt-manager-list');
    if (list) list.replaceChildren();
    const form = $('prompt-manager-form');
    if (form) form.hidden = true;
    setPromptManagerError('');
    setPromptManagerStatus('', false);
    const closeButton = $('prompt-manager-close');
    if (closeButton) closeButton.disabled = false;
    const cancelButton = $('prompt-manager-cancel-busy');
    if (cancelButton) cancelButton.hidden = true;
    renderPromptList();
    syncConsoleModalLock();
    const modal = $('prompt-manager-modal');
    if (modal) modal.setAttribute('aria-busy', 'true');
    const title = $('prompt-manager-title');
    if (title) title.focus({ preventScroll: true });
    else if (modal) modal.focus({ preventScroll: true });
    await loadPromptList(instance);
    return true;
  }

  function closePromptManager(options = {}) {
    const config = state.promptManager;
    if (config.busy && options.force !== true) return false;
    config.requestSeq += 1;
    config.actionSeq += 1;
    clearPromptDeleteArm();
    const trigger = config.trigger;
    config.open = false;
    config.loading = false;
    config.busy = false;
    config.unsupported = false;
    config.instanceId = '';
    config.prompts = [];
    config.activeId = '';
    config.editorMode = '';
    config.editorProfileId = '';
    config.trigger = null;
    const overlay = $('prompt-manager-overlay');
    if (overlay) overlay.hidden = true;
    const list = $('prompt-manager-list');
    if (list) list.replaceChildren();
    const form = $('prompt-manager-form');
    if (form) form.hidden = true;
    const cancelButton = $('prompt-manager-cancel-busy');
    if (cancelButton) cancelButton.hidden = true;
    const modal = $('prompt-manager-modal');
    if (modal) modal.setAttribute('aria-busy', 'false');
    setPromptManagerError('');
    setPromptManagerStatus('', false);
    syncConsoleModalLock();
    if (options.restoreFocus !== false && trigger && trigger.isConnected) trigger.focus({ preventScroll: true });
    return true;
  }

  async function submitPromptEditor(event) {
    event.preventDefault();
    const config = state.promptManager;
    if (!config.open || config.busy || config.unsupported || !config.editorMode) return;
    const instance = state.instances.find((item) => item.id === config.instanceId);
    if (!instance) return;
    const nameInput = $('prompt-editor-name');
    const promptInput = $('prompt-editor-prompt');
    const name = nameInput ? nameInput.value.trim() : '';
    const promptText = promptInput ? promptInput.value : '';
    clearPromptEditorErrors();
    let invalid = false;
    const nameLength = Array.from(name).length;
    const promptLength = Array.from(promptText).length;
    if (!name) {
      setPromptFieldError(nameInput, $('prompt-editor-name-error'), '请输入提示词名称。');
      invalid = true;
    } else if (nameLength > PROMPT_NAME_MAX_LENGTH) {
      setPromptFieldError(nameInput, $('prompt-editor-name-error'), `提示词名称最多 ${PROMPT_NAME_MAX_LENGTH} 个字符（当前 ${nameLength}）。`);
      invalid = true;
    }
    if (config.editorMode !== 'rename') {
      if (!promptText.trim()) {
        setPromptFieldError(promptInput, $('prompt-editor-prompt-error'), '请输入提示词内容。');
        invalid = true;
      } else if (promptLength > PROMPT_TEXT_MAX_LENGTH) {
        setPromptFieldError(promptInput, $('prompt-editor-prompt-error'), `提示词内容最多 ${PROMPT_TEXT_MAX_LENGTH} 个字符（当前 ${promptLength}）。`);
        invalid = true;
      }
    }
    if (invalid) return;
    const payload = { action: config.editorMode === 'create' ? 'create' : 'update', name };
    if (config.editorMode !== 'create') payload.profile_id = config.editorProfileId;
    if (config.editorMode !== 'rename') payload.prompt = promptText;
    const actionSeq = ++config.actionSeq;
    setPromptManagerBusy(true);
    setPromptManagerError('');
    setPromptManagerStatus('正在保存提示词…', false);
    try {
      const data = await withPromptTimeout(apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/prompts`, { method: 'POST', body: payload }));
      if (!config.open || config.actionSeq !== actionSeq) return;
      if (!data || data.ok === false) {
        throw new ApiError(sanitisePluginText(asRecord(data).message || asRecord(data).error) || '提示词模板保存失败。', { data });
      }
      closePromptEditor();
      const feedback = promptActionFeedback(data, payload.action);
      const loaded = await loadPromptList(instance, { keepFeedback: true });
      if (loaded && config.actionSeq === actionSeq) setPromptManagerStatus(feedback.message, feedback.restart);
    } catch (error) {
      if (!config.open || config.actionSeq !== actionSeq) return;
      const unsupported = isMissingEndpointError(error)
        || pluginConfigUnsupportedMessage(error instanceof ApiError ? error.message : '');
      if (unsupported) {
        config.unsupported = true;
        setPromptManagerError('当前后端不支持提示词列表。');
        renderPromptList();
      } else {
        setPromptManagerError(formatError(error, '提示词模板保存失败。'));
      }
    } finally {
      if (config.open && config.actionSeq === actionSeq) setPromptManagerBusy(false);
    }
  }

  async function mutatePrompt(action, profile) {
    const config = state.promptManager;
    const prompt = asRecord(profile);
    if (!config.open || config.busy || config.unsupported || !prompt.id) return;
    if (action === 'delete' && config.prompts.length <= 1) {
      setPromptManagerError('至少保留一个提示词，不能删除最后一个。');
      return;
    }
    const instance = state.instances.find((item) => item.id === config.instanceId);
    if (!instance) return;
    const payload = { action, profile_id: prompt.id };
    const actionSeq = ++config.actionSeq;
    clearPromptDeleteArm();
    setPromptManagerBusy(true);
    setPromptManagerError('');
    setPromptManagerStatus(action === 'delete' ? '正在删除提示词模板…' : '正在设置默认提示词模板…', false);
    try {
      const data = await withPromptTimeout(apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/prompts`, { method: 'POST', body: payload }));
      if (!config.open || config.actionSeq !== actionSeq) return;
      if (!data || data.ok === false) {
        throw new ApiError(sanitisePluginText(asRecord(data).message || asRecord(data).error) || '提示词模板操作失败。', { data });
      }
      const feedback = promptActionFeedback(data, action);
      const loaded = await loadPromptList(instance, { keepFeedback: true });
      if (loaded && config.actionSeq === actionSeq) setPromptManagerStatus(feedback.message, feedback.restart);
    } catch (error) {
      if (!config.open || config.actionSeq !== actionSeq) return;
      const unsupported = isMissingEndpointError(error)
        || pluginConfigUnsupportedMessage(error instanceof ApiError ? error.message : '');
      if (unsupported) {
        config.unsupported = true;
        setPromptManagerError('当前后端不支持提示词列表。');
        renderPromptList();
      } else {
        setPromptManagerError(formatError(error, '提示词模板操作失败。'));
      }
    } finally {
      if (config.open && config.actionSeq === actionSeq) setPromptManagerBusy(false);
    }
  }

  function buildPromptManagerSection(instance) {
    const section = makeElement('section', 'instance-v3-section prompt-manager-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-edit'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '提示词模板库'));
    headingCopy.appendChild(makeElement('p', '', '管理多套提示词模板。置顶模板只作为新建会话的默认值，不会影响已有会话。'));
    heading.appendChild(headingCopy);
    section.appendChild(heading);
    const note = makeElement('p', 'prompt-manager-section-note', '置顶模板仅作为新建会话的默认提示词；发送 /新会话 时可通过插件选择模板，已有会话不会自动切换。');
    section.appendChild(note);
    const openButton = makeElement('button', 'btn btn-secondary btn-small', '打开提示词模板库');
    openButton.type = 'button';
    openButton.id = 'prompt-manager-open';
    openButton.addEventListener('click', () => void openPromptManager(instance, openButton));
    section.appendChild(openButton);
    void probePromptProfiles(instance);
    return section;
  }

  async function handlePluginToggle(instance, plugin, checkbox, row, stateText, statusElement) {
    const previousEnabled = plugin.enabled === true;
    const targetEnabled = checkbox.checked;
    const settingsBusy = instanceSettings.isOpenFor(instance.id);
    if (settingsBusy) instanceSettings.beginBusy();
    checkbox.disabled = true;
    row.classList.add('is-busy');
    stateText.textContent = '保存中…';
    setV3Status(statusElement, '正在保存插件状态…', 'info');
    try {
      const data = await requestWithFallback([
        `/api/instances/${encodeURIComponent(instance.id)}/plugins`,
        `/api/instances/${encodeURIComponent(instance.id)}/plugins/toggle`
      ], {
        method: 'POST',
        body: { plugin_id: plugin.id, enabled: targetEnabled }
      });
      if (!data || data.ok === false) {
        throw new ApiError(textValue(data && data.message, '插件状态切换失败。'));
      }
      const updatedPlugin = asRecord(data.plugin);
      const responseEnabled = pluginEnabledFromRecord(updatedPlugin);
      plugin.enabled = responseEnabled === undefined ? targetEnabled : responseEnabled;
      for (const field of ['name', 'version', 'description', 'source']) {
        const value = textValue(updatedPlugin[field], '');
        if (value) plugin[field] = value;
      }
      const pluginRestart = boolOrUndefined(updatedPlugin.restart_required) === true;
      const restartRequired = boolOrUndefined(data.restart_required) === true || pluginRestart;
      const message = textValue(data.message, plugin.enabled ? '插件已启用。' : '插件已禁用。');
      const finalMessage = restartRequired && !message.includes('重启')
        ? `${message} 请重启 AstrBot 生效。`
        : message;
      stateText.textContent = pluginStateText(plugin.enabled);
      checkbox.checked = plugin.enabled;
      setV3Status(statusElement, finalMessage, restartRequired ? 'warning' : 'success');
      if (instance.v3 && instance.v3.plugins) {
        instance.v3.plugins.message = finalMessage;
        instance.v3.plugins.restartRequired = restartRequired;
      }
      updateV3Summary(instance);
    } catch (error) {
      plugin.enabled = previousEnabled;
      checkbox.checked = previousEnabled;
      stateText.textContent = pluginStateText(previousEnabled);
      setV3Status(statusElement, formatError(error, '插件状态切换失败。'), 'error');
    } finally {
      row.classList.remove('is-busy');
      checkbox.disabled = !plugin.id || plugin.enabled === undefined || (instance.v3 && instance.v3.plugins.status !== 'ok');
      if (settingsBusy) instanceSettings.endBusy();
    }
  }

  function buildMemorySection(instance) {
    const sourceMemory = (instance.v3 && instance.v3.memory) || emptyMemoryState(instance.memory_mode_hint || '');
    const memoryMode = lastKnownMemoryModes.get(instance.id)
      || sourceMemory.mode
      || normaliseMemoryMode(instance.memory_mode_hint)
      || '';
    const memory = { ...sourceMemory, mode: memoryMode };
    const section = makeElement('section', 'instance-v3-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-memory'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '记忆模式'));
    headingCopy.appendChild(makeElement(
      'p',
      '',
      'direct 通常更省 token，只带当前消息；astrbot 共享 AstrBot 会话记忆，历史越长 token 成本越高。切换后可能需要重启 AstrBot。'
    ));
    heading.appendChild(headingCopy);
    section.appendChild(heading);

    const group = makeElement('div', 'memory-mode-group');
    group.setAttribute('role', 'radiogroup');
    group.setAttribute('aria-label', `选择实例 ${instance.name} 的记忆模式`);
    const radioName = `memory-mode-${String(instance.id || 'instance').replace(/[^a-zA-Z0-9_-]/g, '-')}`;
    for (const mode of ['direct', 'astrbot']) {
      const meta = V3_MEMORY_META[mode];
      const label = makeElement('label', 'memory-mode-option');
      const radio = document.createElement('input');
      radio.setAttribute('type', 'radio');
      radio.name = radioName;
      radio.value = mode;
      radio.checked = memory.mode === mode;
      radio.disabled = memory.status !== 'ok';
      const copy = makeElement('span', 'memory-mode-copy');
      copy.appendChild(makeElement('strong', '', meta.label));
      copy.appendChild(makeElement('small', '', meta.description));
      label.classList.toggle('is-selected', memory.mode === mode);
      label.appendChild(radio);
      label.appendChild(copy);
      radio.addEventListener('change', () => {
        if (radio.checked) void handleMemoryModeChange(instance, mode, group, statusElement);
      });
      group.appendChild(label);
    }
    section.appendChild(group);

    const statusElement = makeElement('p', 'instance-v3-status');
    statusElement.dataset.v3Status = 'memory';
    statusElement.setAttribute('role', 'status');
    if (memory.status === 'unsupported' || memory.status === 'error') {
      setV3Status(statusElement, memory.error || '记忆模式接口暂不可用，显示为未知。', memory.status === 'error' ? 'error' : 'warning');
    } else if (memory.restartRequired) {
      setV3Status(statusElement, memory.message || '记忆模式已变更，请重启 AstrBot 生效。', 'warning');
    } else if (memory.message) {
      setV3Status(statusElement, memory.message, 'info');
    } else if (!memory.mode) {
      setV3Status(statusElement, '服务端未返回当前记忆模式；可选择后保存。', 'info');
    }
    section.appendChild(statusElement);
    if (memory.status === 'error' || memory.status === 'unsupported') {
      const retryButton = makeElement('button', 'btn btn-quiet btn-small memory-refresh-btn', '刷新重试');
      retryButton.type = 'button';
      retryButton.title = '重新读取记忆模式；失败时保留上次已知选择';
      retryButton.addEventListener('click', () => void loadConsole({ silent: true }));
      section.appendChild(retryButton);
    }
    return section;
  }

  async function handleMemoryModeChange(instance, mode, group, statusElement) {
    const previousMode = normaliseMemoryMode(instance.v3 && instance.v3.memory && instance.v3.memory.mode);
    const radios = Array.from(group.querySelectorAll('input[type="radio"]'));
    const settingsBusy = instanceSettings.isOpenFor(instance.id);
    if (settingsBusy) instanceSettings.beginBusy();
    for (const radio of radios) radio.disabled = true;
    setV3Status(statusElement, '正在保存记忆模式…', 'info');
    try {
      const data = await requestWithFallback([
        `/api/instances/${encodeURIComponent(instance.id)}/memory`,
        instancePath('/api/memory', instance.id)
      ], {
        method: 'POST',
        body: { mode }
      });
      if (!data || data.ok === false) {
        throw new ApiError(textValue(data && data.message, '记忆模式保存失败。'));
      }
      const resolvedMode = normaliseMemoryMode(data.mode) || mode;
      const restartRequired = boolOrUndefined(data.restart_required) === true;
      const message = textValue(data.message, `记忆模式已切换为 ${resolvedMode}。`);
      lastKnownMemoryModes.set(instance.id, resolvedMode);
      if (instance.v3) {
        instance.v3.memory = {
          ...(instance.v3.memory || emptyMemoryState()),
          status: 'ok',
          mode: resolvedMode,
          restartRequired,
          message,
          error: '',
          updatedAt: Date.now()
        };
      }
      for (const radio of radios) {
        radio.checked = radio.value === resolvedMode;
        radio.disabled = false;
        const option = radio.closest('.memory-mode-option');
        if (option) option.classList.toggle('is-selected', radio.checked);
      }
      const finalMessage = restartRequired && !message.includes('重启')
        ? `${message} 请重启 AstrBot 生效。`
        : message;
      setV3Status(statusElement, finalMessage, restartRequired ? 'warning' : 'success');
      updateV3Summary(instance);
    } catch (error) {
      for (const radio of radios) {
        radio.checked = radio.value === previousMode;
        const option = radio.closest('.memory-mode-option');
        if (option) option.classList.toggle('is-selected', radio.checked);
        radio.disabled = false;
      }
      setV3Status(statusElement, formatError(error, '记忆模式保存失败。'), 'error');
    } finally {
      if (settingsBusy) instanceSettings.endBusy();
    }
  }

  function containerMemoryText(stats) {
    const usage = stats.containerMemoryUsageBytes;
    const limit = stats.containerMemoryLimitBytes;
    if (usage !== undefined && limit !== undefined) return `${formatBytes(usage)} / ${formatBytes(limit)}`;
    if (usage !== undefined) return formatBytes(usage);
    if (stats.containerMemoryPercent !== undefined) return `已用 ${formatPercent(stats.containerMemoryPercent)}`;
    return '未知';
  }

  function memoryPolicyModeLabel(policy, mode) {
    const options = (policy && policy.modeOptions) || [];
    const match = options.find((option) => option.value === mode);
    return match ? match.label : (mode === 'long_term' ? '长期记忆' : '会话记忆');
  }

  function memoryPolicyOptionLabel(options, value, fallback) {
    const match = (options || []).find((option) => option.value === value);
    return match ? match.label : (fallback || value || '');
  }

  function memoryPolicyEffectiveText(policy, mode) {
    if (!mode) return '未知（配置非法或重复，无法确定哪个生效）';
    if (mode === 'conversation') return 'conversation（只用会话历史）';
    return memoryPolicyModeLabel(policy, mode);
  }

  function buildMemoryPolicySection(instance) {
    const policy = (instance.v3 && instance.v3.memoryPolicy) || emptyMemoryPolicyState();
    const disabled = policy.supported !== true;
    const section = makeElement('section', 'instance-v3-section memory-policy-section');
    section.dataset.memoryPolicy = 'section';

    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-memory'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '记忆策略'));
    headingCopy.appendChild(makeElement(
      'p',
      '',
      '按群开启 AstrBot 原生长期记忆（memory.policy）：只写 AstrBot 配置文件，不读取记忆数据库内容。'
    ));
    heading.appendChild(headingCopy);
    section.appendChild(heading);

    const warning = makeElement('p', 'memory-policy-warning', MEMORY_POLICY_WARNING);
    warning.dataset.memoryPolicy = 'warning';
    section.appendChild(warning);

    const statusElement = makeElement('p', 'instance-v3-status');
    statusElement.dataset.memoryPolicy = 'status';
    statusElement.setAttribute('role', 'status');
    if (policy.status === 'unsupported') {
      setV3Status(statusElement, policy.error || '记忆策略接口暂不可用，未保存任何值。', 'warning');
    } else if (policy.status === 'error') {
      setV3Status(
        statusElement,
        policy.permissionDenied
          ? (policy.message || '本机无法读写 AstrBot 配置文件；请按下面的步骤处理，Studio 不会伪造已保存的值。')
          : (policy.error || '记忆策略读取失败，未保存任何值。'),
        'error'
      );
    } else if (policy.message) {
      const tone = policy.memoryEnabled === false || policy.invalidRows > 0 ? 'warning' : 'info';
      setV3Status(statusElement, policy.message, tone);
    }
    section.appendChild(statusElement);

    const warningList = makeElement('ul', 'memory-policy-warnings');
    warningList.dataset.memoryPolicy = 'warnings';
    for (const item of policy.warnings || []) warningList.appendChild(makeElement('li', '', item));
    warningList.hidden = !(policy.warnings || []).length;
    section.appendChild(warningList);

    const errorElement = makeElement('p', 'memory-policy-error', '');
    errorElement.dataset.memoryPolicy = 'error';
    errorElement.setAttribute('role', 'alert');
    errorElement.hidden = true;
    section.appendChild(errorElement);

    const actionsList = makeElement('ul', 'memory-policy-actions');
    actionsList.dataset.memoryPolicy = 'actions';
    for (const item of policy.actions || []) actionsList.appendChild(makeElement('li', '', item));
    actionsList.hidden = !(policy.actions || []).length;
    section.appendChild(actionsList);

    if (policy.readChannel || policy.permissionDenied) {
      const channelNotes = [];
      if (policy.readChannel) {
        channelNotes.push(policy.readChannel === 'container'
          ? '配置文件由 AstrBot 容器持有：已通过容器内命令读写（原子写入 0600）。'
          : '配置文件由本机 Studio 用户读写。');
      }
      if (policy.permissionDenied && !policy.writable) {
        channelNotes.push('当前无法写入：保存会明确失败，不会显示“已保存”。');
      }
      if (channelNotes.length) {
        const channelLine = makeElement('p', 'memory-policy-hint', channelNotes.join(' '));
        channelLine.dataset.memoryPolicy = 'channel';
        section.appendChild(channelLine);
      }
    }

    const unsupported = makeElement('p', 'memory-policy-unsupported', '');
    unsupported.dataset.memoryPolicy = 'unsupported';
    if (disabled) {
      unsupported.textContent = policy.error
        ? `当前实例不支持编辑记忆策略：${policy.error}`
        : '当前实例不支持编辑记忆策略，Studio 不会显示伪造的已保存值。';
      unsupported.hidden = false;
    } else {
      unsupported.hidden = true;
    }
    section.appendChild(unsupported);

    function makeSelect(field, options, value) {
      const select = document.createElement('select');
      select.dataset.field = field;
      select.disabled = disabled;
      for (const option of options) {
        const element = document.createElement('option');
        element.value = option.value;
        element.textContent = option.label;
        select.appendChild(element);
      }
      if (!options.some((option) => option.value === value)) {
        const element = document.createElement('option');
        element.value = value;
        element.textContent = `当前值：${value}`;
        select.appendChild(element);
      }
      select.value = value;
      return select;
    }

    function makeCheckbox(field, checked, labelText) {
      const label = makeElement('label', 'memory-policy-check');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.dataset.field = field;
      input.checked = checked === true;
      input.disabled = disabled;
      label.appendChild(input);
      label.appendChild(document.createTextNode(labelText));
      return label;
    }

    function makeBudget(field, value, range, labelText) {
      const label = makeElement('label', 'memory-policy-field memory-policy-budget-field');
      label.appendChild(makeElement('span', 'memory-policy-label', labelText));
      const input = document.createElement('input');
      input.type = 'number';
      input.min = String(range[0]);
      input.max = String(range[1]);
      input.step = '1';
      input.dataset.field = field;
      input.placeholder = `留空=继承（${range[0]}..${range[1]}）`;
      input.value = value === undefined || value === null ? '' : String(value);
      input.disabled = disabled;
      label.appendChild(input);
      return label;
    }

    const defaults = makeElement('div', 'memory-policy-defaults');
    const defaultField = makeElement('label', 'memory-policy-field');
    defaultField.appendChild(makeElement('span', 'memory-policy-label', '默认模式（未列出群的模式）'));
    const defaultSelect = makeSelect('default-mode', policy.modeOptions, policy.defaultMode);
    defaultSelect.dataset.memoryPolicy = 'default-mode';
    defaultField.appendChild(defaultSelect);
    defaultField.appendChild(makeElement(
      'small',
      'memory-policy-hint',
      '默认 conversation：只用会话历史。只有下面单独列出的群才会写入/读取长期记忆。'
    ));
    defaults.appendChild(defaultField);
    const privateField = makeElement('label', 'memory-policy-field');
    privateField.appendChild(makeElement('span', 'memory-policy-label', '私聊模式（固定 scope=user）'));
    const privateSelect = makeSelect('private-mode', policy.modeOptions, policy.privateMode);
    privateSelect.dataset.memoryPolicy = 'private-mode';
    privateField.appendChild(privateSelect);
    defaults.appendChild(privateField);
    const privateToggles = makeElement('div', 'memory-policy-toggles');
    const privateExtract = makeCheckbox('private-extract', policy.privateExtract, '私聊抽取');
    const privateInject = makeCheckbox('private-inject', policy.privateInject, '私聊注入');
    privateToggles.appendChild(privateExtract);
    privateToggles.appendChild(privateInject);
    privateToggles.appendChild(makeBudget('private-max-items', policy.privateMaxItems, policy.limits.maxItems, '私聊最大条数'));
    privateToggles.appendChild(makeBudget('private-max-chars', policy.privateMaxChars, policy.limits.maxChars, '私聊最大字符'));
    defaults.appendChild(privateToggles);
    const privateNotes = makeElement('div', 'memory-policy-private-notes');
    privateNotes.dataset.memoryPolicy = 'private-notes';
    if (!policy.privateConfigured) {
      privateNotes.appendChild(makeElement(
        'p',
        'memory-policy-hint',
        `未单独配置私聊策略：私聊跟随默认模式（当前 ${memoryPolicyModeLabel(policy, policy.privateEffectiveMode)}）。`
      ));
    }
    if (policy.privateInvalid) {
      privateNotes.appendChild(makeElement(
        'p',
        'memory-policy-row-invalid',
        '私聊策略配置非法：AstrBot 会把私聊策略降级为 conversation（不是 long_term 生效），请按下面的原因修正。'
      ));
      const privateErrors = makeElement('ul', 'memory-policy-row-errors');
      privateErrors.dataset.memoryPolicy = 'private-errors';
      for (const item of policy.privateErrors || []) privateErrors.appendChild(makeElement('li', '', item));
      privateNotes.appendChild(privateErrors);
    }
    if (!privateNotes.childNodes.length) privateNotes.hidden = true;
    defaults.appendChild(privateNotes);
    section.appendChild(defaults);

    const groupsHeader = makeElement('div', 'memory-policy-groups-header');
    groupsHeader.appendChild(makeElement('h5', '', '按群覆盖'));
    const addButton = makeElement('button', 'btn btn-ghost memory-policy-add', '添加群策略');
    addButton.type = 'button';
    addButton.dataset.memoryPolicy = 'add';
    addButton.disabled = disabled;
    groupsHeader.appendChild(addButton);
    section.appendChild(groupsHeader);

    const groupsContainer = makeElement('div', 'memory-policy-groups');
    groupsContainer.dataset.memoryPolicy = 'groups';
    const emptyText = makeElement('p', 'memory-policy-empty', '还没有群策略：所有群和私聊都按上面的默认模式处理。');
    emptyText.dataset.memoryPolicy = 'empty';

    function syncEmpty() {
      emptyText.hidden = groupsContainer.querySelectorAll('[data-memory-policy-row]').length > 0;
    }

    function makeGroupRow(row) {
      const rowElement = makeElement('div', 'memory-policy-row');
      rowElement.dataset.memoryPolicyRow = '';

      const summary = makeElement('div', 'memory-policy-row-summary');
      const summaryCopy = makeElement('div', 'memory-policy-row-summary-copy');
      const summaryTitle = makeElement('strong', 'memory-policy-row-title', '新群策略');
      const summaryMeta = makeElement('span', 'memory-policy-row-meta', '');
      summaryCopy.appendChild(summaryTitle);
      summaryCopy.appendChild(summaryMeta);
      const summaryActions = makeElement('div', 'memory-policy-row-actions');

      const editButton = makeElement('button', 'btn btn-ghost memory-policy-edit', '编辑');
      editButton.type = 'button';
      editButton.dataset.memoryPolicyEdit = '';
      editButton.disabled = disabled;
      const removeButton = makeElement('button', 'btn btn-ghost memory-policy-delete', '删除');
      removeButton.type = 'button';
      removeButton.dataset.memoryPolicyDelete = '';
      removeButton.disabled = disabled;
      summaryActions.appendChild(editButton);
      summaryActions.appendChild(removeButton);
      summary.appendChild(summaryCopy);
      summary.appendChild(summaryActions);
      rowElement.appendChild(summary);

      const fields = makeElement('div', 'memory-policy-row-fields');
      fields.hidden = true;

      const idField = makeElement('label', 'memory-policy-field');
      idField.appendChild(makeElement('span', 'memory-policy-label', '群号'));
      const idInput = document.createElement('input');
      idInput.type = 'text';
      idInput.dataset.field = 'group-id';
      idInput.placeholder = '如 838796241 或 aiocqhttp:838796241';
      idInput.value = row.groupId || '';
      idInput.disabled = disabled;
      idField.appendChild(idInput);
      fields.appendChild(idField);

      const modeField = makeElement('label', 'memory-policy-field');
      modeField.appendChild(makeElement('span', 'memory-policy-label', '模式'));
      const modeSelect = makeSelect('mode', policy.modeOptions, row.mode || 'long_term');
      modeField.appendChild(modeSelect);
      fields.appendChild(modeField);

      const scopeField = makeElement('label', 'memory-policy-field');
      scopeField.appendChild(makeElement('span', 'memory-policy-label', 'scope'));
      const scopeSelect = makeSelect('scope', policy.scopeOptions, row.scope || 'group');
      scopeField.appendChild(scopeSelect);
      fields.appendChild(scopeField);

      const toggles = makeElement('div', 'memory-policy-toggles');
      const extractCheck = makeCheckbox('extract', row.extract !== false, '抽取');
      const injectCheck = makeCheckbox('inject', row.inject !== false, '注入');
      toggles.appendChild(extractCheck);
      toggles.appendChild(injectCheck);
      toggles.appendChild(makeBudget('max-items', row.maxItems, policy.limits.maxItems, '最大条数'));
      toggles.appendChild(makeBudget('max-chars', row.maxChars, policy.limits.maxChars, '最大字符'));
      fields.appendChild(toggles);

      function updateSummary() {
        const groupId = String(idInput.value || '').trim();
        const budget = [];
        const maxItems = rowElement.querySelector('[data-field="max-items"]');
        const maxChars = rowElement.querySelector('[data-field="max-chars"]');
        if (maxItems && String(maxItems.value || '').trim()) budget.push(`条数 ${maxItems.value}`);
        if (maxChars && String(maxChars.value || '').trim()) budget.push(`字符 ${maxChars.value}`);
        summaryTitle.textContent = groupId || '未填写群号';
        summaryMeta.textContent = [
          memoryPolicyModeLabel(policy, modeSelect.value),
          memoryPolicyOptionLabel(policy.scopeOptions, scopeSelect.value, scopeSelect.value),
          extractCheck.checked ? '抽取' : '不抽取',
          injectCheck.checked ? '注入' : '不注入',
          ...budget,
        ].join(' · ') + (row.invalid ? ' · 配置非法，实际降级 conversation' : '');
      }

      function setEditing(editing) {
        fields.hidden = !editing;
        rowElement.classList.toggle('is-editing', editing);
        editButton.textContent = editing ? '完成' : '编辑';
        if (editing && typeof idInput.focus === 'function') idInput.focus();
      }

      rowElement.addEventListener('input', updateSummary);
      rowElement.addEventListener('change', updateSummary);
      editButton.addEventListener('click', () => setEditing(fields.hidden));
      removeButton.addEventListener('click', () => {
        rowElement.remove();
        syncEmpty();
      });
      updateSummary();

      if (row.invalid) {
        rowElement.classList.add('is-invalid');
        fields.appendChild(makeElement(
          'p',
          'memory-policy-row-invalid',
          '该行配置非法：AstrBot 会把整条降级为 conversation，不会按 long_term 生效；请按下面的原因修正后保存。'
        ));
      }
      if ((row.unknownFields || []).length) {
        fields.appendChild(makeElement(
          'p',
          'memory-policy-row-unknown',
          `未知字段 ${row.unknownFields.join(', ')} 会被 AstrBot 忽略（可能是拼写错误）。`
        ));
      }
      if ((row.errors || []).length) {
        const rowErrors = makeElement('ul', 'memory-policy-row-errors');
        rowErrors.dataset.memoryPolicy = 'row-errors';
        for (const item of row.errors) rowErrors.appendChild(makeElement('li', '', item));
        fields.appendChild(rowErrors);
      }
      const effective = makeElement(
        'p',
        'memory-policy-row-effective',
        `实际生效：${memoryPolicyEffectiveText(policy, row.effectiveMode)}`
      );
      effective.dataset.memoryPolicy = 'row-effective';
      fields.appendChild(effective);
      rowElement.appendChild(fields);
      rowElement._setMemoryPolicyEditing = setEditing;
      rowElement._updateMemoryPolicySummary = updateSummary;
      return rowElement;
    }

    for (const row of policy.rows || []) groupsContainer.appendChild(makeGroupRow(row));
    groupsContainer.appendChild(emptyText);
    syncEmpty();
    section.appendChild(groupsContainer);

    addButton.addEventListener('click', () => {
      const rowElement = makeGroupRow({ groupId: '', mode: 'long_term', scope: 'group', extract: true, inject: true, maxItems: '', maxChars: '' });
      groupsContainer.insertBefore(rowElement, emptyText);
      syncEmpty();
      if (typeof rowElement._setMemoryPolicyEditing === 'function') {
        rowElement._setMemoryPolicyEditing(true);
      }
    });

    function rowValue(rowElement, field) {
      const input = rowElement.querySelector(`[data-field="${field}"]`);
      return input ? input : null;
    }

    function budgetValue(input) {
      if (!input) return null;
      const raw = textValue(input.value, '').trim();
      if (raw === '') return null;
      const number = Number(raw);
      // Non-numeric text is forwarded so the server-side validator (the
      // authoritative one) rejects it with a precise message.
      return Number.isFinite(number) ? number : raw;
    }

    function setPolicyError(message) {
      errorElement.textContent = message || '';
      errorElement.hidden = !message;
    }

    const footer = makeElement('div', 'memory-policy-footer');
    const saveButton = makeElement('button', 'btn btn-primary memory-policy-save', '保存记忆策略');
    saveButton.type = 'button';
    saveButton.dataset.memoryPolicy = 'save';
    saveButton.disabled = disabled;
    footer.appendChild(saveButton);
    footer.appendChild(makeElement(
      'span',
      'memory-policy-hint',
      policy.hotReload
        ? '保存后下一轮消息生效（策略缓存最多 5 秒）。'
        : '保存后需重启 AstrBot（或重新加载配置）才生效：面板写配置文件，不调用 AstrBot 热重载接口。'
    ));
    if (policy.configPath) footer.appendChild(makeElement('span', 'memory-policy-path', `配置文件：${policy.configPath}`));
    section.appendChild(footer);

    let saving = false;
    async function savePolicy() {
      if (disabled || saving) return;
      setPolicyError('');
      setV3Status(statusElement, '', 'info');
      const payload = {
        default_mode: defaultSelect.value,
        private: {
          mode: privateSelect.value,
          scope: 'user',
          extract: privateExtract.querySelector('input').checked,
          inject: privateInject.querySelector('input').checked,
          max_items: budgetValue(privateToggles.querySelector('[data-field="private-max-items"]')),
          max_chars: budgetValue(privateToggles.querySelector('[data-field="private-max-chars"]'))
        },
        groups: []
      };
      let firstInvalid = null;
      for (const rowElement of groupsContainer.querySelectorAll('[data-memory-policy-row]')) {
        const idInput = rowValue(rowElement, 'group-id');
        const groupId = textValue(idInput && idInput.value, '').trim();
        if (!groupId) {
          firstInvalid = firstInvalid || idInput;
          rowElement.classList.add('is-invalid');
          continue;
        }
        rowElement.classList.remove('is-invalid');
        payload.groups.push({
          group_id: groupId,
          mode: rowValue(rowElement, 'mode').value,
          scope: rowValue(rowElement, 'scope').value,
          extract: rowValue(rowElement, 'extract').checked,
          inject: rowValue(rowElement, 'inject').checked,
          max_items: budgetValue(rowValue(rowElement, 'max-items')),
          max_chars: budgetValue(rowValue(rowElement, 'max-chars'))
        });
      }
      if (firstInvalid) {
        setPolicyError('请填写每一行群策略的群号，或删除该行。');
        if (typeof firstInvalid.focus === 'function') firstInvalid.focus();
        return;
      }
      saving = true;
      saveButton.disabled = true;
      saveButton.textContent = '保存中…';
      try {
        const data = await apiRequest(
          `/api/instances/${encodeURIComponent(instance.id)}/memory-policy`,
          { method: 'POST', body: payload }
        );
        const next = normaliseMemoryPolicyPayload(data);
        instance.v3 = instance.v3 || {};
        instance.v3.memoryPolicy = next;
        updateV3Summary(instance);
        // Re-render from the server response so no saved value is invented.
        section.replaceWith(buildMemoryPolicySection(instance));
      } catch (error) {
        const payload = error && error.data && typeof error.data === 'object' ? error.data : null;
        const steps = payload ? memoryPolicyStringList(payload.actions) : [];
        const detail = formatError(error, '记忆策略保存失败。');
        setPolicyError(steps.length ? `${detail} ${steps.join(' ')}` : detail);
        if (payload && payload.supported === true) {
          // Permission/container problem: the panel stays usable, but nothing
          // was saved and the steps say how to fix it.
          setV3Status(statusElement, '未保存：请按上方步骤处理后重试。Studio 不会显示伪造的已保存状态。', 'error');
        }
        const firstInput = section.querySelector('input, select');
        if (firstInput && typeof firstInput.focus === 'function') firstInput.focus();
      } finally {
        saving = false;
        saveButton.disabled = disabled;
        saveButton.textContent = '保存记忆策略';
      }
    }
    saveButton.addEventListener('click', () => void savePolicy());
    return section;
  }


  function buildStatsSection(instance) {
    const stats = (instance.v3 && instance.v3.stats) || emptyStatsState();
    const section = makeElement('section', 'instance-v3-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-database'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '存储与资源'));
    headingCopy.appendChild(makeElement('p', '', '数据目录、容器内存与 CPU 状态；无法读取的数字显示为“未知”。'));
    heading.appendChild(headingCopy);
    section.appendChild(heading);

    if (stats.status === 'unsupported' || stats.status === 'error') {
      section.appendChild(makeElement('p', 'instance-v3-note is-warning', stats.error || '资源统计暂不可用，数值显示为未知。'));
    } else if (stats.error) {
      section.appendChild(makeElement('p', 'instance-v3-note is-warning', stats.error));
    }

    const grid = makeElement('div', 'instance-resource-grid');
    const dataMetric = makeElement('div', 'instance-resource-metric');
    dataMetric.appendChild(makeElement('span', 'instance-resource-label', '数据目录'));
    dataMetric.appendChild(makeElement('strong', 'instance-resource-value', stats.status === 'ok' ? formatBytes(stats.dataSizeBytes) : '未知'));
    if (stats.status === 'ok' && stats.fileCount !== undefined) {
      dataMetric.appendChild(makeElement('small', 'instance-resource-detail', formatFileCount(stats.fileCount)));
    }
    const containerMetric = makeElement('div', 'instance-resource-metric');
    containerMetric.appendChild(makeElement('span', 'instance-resource-label', '容器内存'));
    containerMetric.appendChild(makeElement('strong', 'instance-resource-value', stats.status === 'ok' ? containerMemoryText(stats) : '未知'));
    if (stats.status === 'ok' && stats.containerMemoryPercent !== undefined) {
      containerMetric.appendChild(makeElement('small', 'instance-resource-detail', `占用 ${formatPercent(stats.containerMemoryPercent)}`));
    }
    const cpuMetric = makeElement('div', 'instance-resource-metric');
    cpuMetric.appendChild(makeElement('span', 'instance-resource-label', 'CPU'));
    cpuMetric.appendChild(makeElement('strong', 'instance-resource-value', stats.status === 'ok' ? formatPercent(stats.cpuPercent) : '未知'));
    grid.appendChild(dataMetric);
    grid.appendChild(containerMetric);
    grid.appendChild(cpuMetric);
    section.appendChild(grid);

    if (stats.status === 'ok' && stats.errors.length > 0) {
      section.appendChild(makeElement('p', 'instance-v3-note is-warning', `部分指标读取失败：${stats.errors.slice(0, 3).join('；')}`));
    }
    return section;
  }

  function syncConsoleModalLock() {
    document.body.classList.toggle('console-modal-open', Boolean(state.pluginConfig.open || state.promptManager.open || instanceSettings.isOpen()));
  }

  function renderGlobalResourceSummary() {
    const dataSize = $('global-data-size');
    const hostValue = $('global-host-memory');
    const hostDetail = $('global-host-memory-detail');
    const diskValue = $('global-disk-free');
    const diskDetail = $('global-disk-detail');
    const note = $('console-resource-note');
    const stats = state.globalResourceStats;
    const status = state.globalResourceStatus;

    if (!stats) {
      if (dataSize) dataSize.textContent = '未知';
      if (hostValue) hostValue.textContent = '未知';
      if (hostDetail) hostDetail.textContent = '数值不可用时显示未知';
      if (diskValue) diskValue.textContent = '未知';
      if (diskDetail) diskDetail.textContent = '数值不可用时显示未知';
      if (note) {
        if (!state.instances.length) note.textContent = '暂无实例，资源统计显示为未知。';
        else if (status === 'unsupported') note.textContent = '当前后端未提供资源统计接口，数值显示为未知。';
        else if (status === 'error') note.textContent = `资源统计读取失败，数值显示为未知${state.globalResourceError ? `：${state.globalResourceError}` : '。'}`;
        else note.textContent = '资源统计尚未读取，数值显示为未知。';
      }
      return;
    }

    const host = asRecord(stats.hostMemory);
    const global = asRecord(stats.global);
    const disk = asRecord(global.disk);
    const globalDataSize = pickNumber(global, ['data_dir_size_bytes', 'instances_total_bytes']);
    const hostUsed = pickNumber(host, ['used_bytes']);
    const hostTotal = pickNumber(host, ['total_bytes']);
    const hostAvailable = pickNumber(host, ['available_bytes']);
    const hostPercent = pickNumber(host, ['used_percent']);
    const diskFree = pickNumber(disk, ['free_bytes', 'available_bytes']);
    const diskTotal = pickNumber(disk, ['total_bytes']);
    const diskPercent = pickNumber(disk, ['used_percent']);

    if (dataSize) dataSize.textContent = formatBytes(globalDataSize);
    if (hostValue) {
      if (hostUsed !== undefined && hostTotal !== undefined) hostValue.textContent = `${formatBytes(hostUsed)} / ${formatBytes(hostTotal)}`;
      else if (hostUsed !== undefined) hostValue.textContent = formatBytes(hostUsed);
      else if (hostPercent !== undefined) hostValue.textContent = `已用 ${formatPercent(hostPercent)}`;
      else hostValue.textContent = '未知';
    }
    if (hostDetail) {
      if (hostPercent !== undefined) hostDetail.textContent = `已用 ${formatPercent(hostPercent)}`;
      else if (hostAvailable !== undefined) hostDetail.textContent = `可用 ${formatBytes(hostAvailable)}`;
      else if (hostTotal !== undefined) hostDetail.textContent = `总内存 ${formatBytes(hostTotal)}`;
      else hostDetail.textContent = '数值不可用';
    }
    if (diskValue) {
      if (diskFree !== undefined) diskValue.textContent = `可用 ${formatBytes(diskFree)}`;
      else if (diskPercent !== undefined) diskValue.textContent = `已用 ${formatPercent(diskPercent)}`;
      else diskValue.textContent = '未知';
    }
    if (diskDetail) {
      const details = [];
      if (diskTotal !== undefined) details.push(`总容量 ${formatBytes(diskTotal)}`);
      if (diskPercent !== undefined) details.push(`已用 ${formatPercent(diskPercent)}`);
      diskDetail.textContent = details.length ? details.join(' · ') : '数值不可用';
    }
    if (note) {
      const syncTime = formatSyncTime(state.lastSyncedAt || stats.updatedAt);
      const partial = state.globalResourcePartial ? ' · 部分实例或指标不可用' : '';
      note.textContent = `统计已更新${syncTime ? ` · ${syncTime}` : ''}${partial}`;
      note.title = stats.error || '';
    }
  }
  function resetDeleteButtons() {
    $$('.instance-delete-btn').forEach((button) => {
      button.textContent = '删除实例';
      button.disabled = false;
      button.classList.remove('is-loading');
    });
    state.deleteArmedId = '';
  }

  async function handleDeleteInstance(instance, button) {
    if (!instance.id || state.deletingInstanceId) return;
    if (state.deleteArmedId !== instance.id) {
      resetDeleteButtons();
      state.deleteArmedId = instance.id;
      button.textContent = '再次点击确认删除';
      window.setTimeout(() => {
        if (state.deleteArmedId === instance.id && !state.deletingInstanceId) {
          resetDeleteButtons();
        }
      }, 6000);
      return;
    }

    state.deleteArmedId = '';
    state.deletingInstanceId = instance.id;
    button.disabled = true;
    button.classList.add('is-loading');
    button.textContent = '正在删除…';
    try {
      const data = asRecord(await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}`, {
        method: 'DELETE'
      }));
      if (data.ok === false) throw new ApiError(textValue(data.message, '删除实例失败。'));
      showToast(`实例「${instance.name || instance.id}」已删除。`, 'success', 4500);
      state.deletingInstanceId = '';
      await loadConsole({ silent: true });
    } catch (error) {
      const message = formatError(error, '删除实例失败。');
      showToast(message, 'error', 7000);
    } finally {
      state.deletingInstanceId = '';
      resetDeleteButtons();
    }
  }

  function buildInstanceCard(instance) {
    const card = makeElement('article', 'instance-card');
    card.dataset.instanceId = instance.id;

    const header = makeElement('div', 'instance-card-header');
    const identity = makeElement('div', 'instance-identity');
    const avatar = makeElement('span', 'instance-avatar');
    avatar.appendChild(makeIcon('c-i-server'));
    const identityCopy = makeElement('div', 'instance-identity-copy');
    identityCopy.appendChild(makeElement('h2', '', instance.name));
    identityCopy.appendChild(makeElement('p', 'instance-subtitle', `ID：${instance.id || '缺失'} · ${formatCreatedAt(instance.created_at)}`));
    identity.appendChild(avatar);
    identity.appendChild(identityCopy);

    const badges = makeElement('div', 'instance-badges');
    badges.appendChild(makeElement('span', `pill pill--${instance.deployed ? 'success' : 'neutral'}`, instance.deployed ? '已部署' : '待部署'));
    const runningTone = instance.running === true ? 'success' : instance.running === false ? 'warning' : 'neutral';
    badges.appendChild(makeElement('span', `pill pill--${runningTone}`, instance.running === true ? '运行中' : instance.running === false ? '已停止' : '容器状态未知'));
    header.appendChild(identity);
    header.appendChild(badges);
    card.appendChild(header);

    if (instance.active_job_id) {
      const jobMeta = jobStatusMeta(instance);
      const jobBanner = makeElement('div', `instance-job-banner is-${jobMeta.tone}`);
      const jobIcon = makeElement('span', 'instance-job-icon');
      jobIcon.appendChild(makeIcon(jobMeta.tone === 'success' ? 'c-i-check' : jobMeta.tone === 'danger' ? 'c-i-alert' : 'c-i-refresh'));
      const jobCopy = makeElement('div', 'instance-job-copy');
      jobCopy.appendChild(makeElement('strong', '', jobMeta.label));
      jobCopy.appendChild(makeElement('code', '', `任务 ID：${instance.active_job_id}`));
      const jobLink = makeElement('a', 'instance-job-link', '查看进度');
      jobLink.href = wizardUrl(instance.id);
      jobBanner.appendChild(jobIcon);
      jobBanner.appendChild(jobCopy);
      jobBanner.appendChild(jobLink);
      card.appendChild(jobBanner);
    }

    const meta = makeElement('div', 'instance-meta');
    const qq = makeElement('div', 'instance-meta-item');
    qq.appendChild(makeElement('span', 'instance-meta-label', 'QQ 号'));
    qq.appendChild(makeElement('strong', 'instance-meta-value', instance.qq_account || '尚未配置'));
    meta.appendChild(qq);
    const stateStatus = makeElement('div', 'instance-meta-item');
    stateStatus.appendChild(makeElement('span', 'instance-meta-label', '实时状态'));
    stateStatus.appendChild(makeElement('strong', 'instance-meta-value', instance.state_error ? '读取失败' : '已同步'));
    meta.appendChild(stateStatus);
    card.appendChild(meta);

    if (instance.state_error) {
      card.appendChild(makeElement('p', 'instance-state-error', `实时状态读取失败：${instance.state_error}`));
    }

    const webSection = makeElement('div', 'instance-web-section');
    webSection.appendChild(makeElement('span', 'instance-section-label', 'WebUI 与远程桌面'));
    const webLinks = makeElement('div', 'instance-web-links');
    webLinks.appendChild(makeWebLink('AstrBot WebUI', instance.urls.astrbot_webui));
    webLinks.appendChild(makeWebLink('SnowLuma WebUI', instance.urls.snowluma_webui));
    webLinks.appendChild(makeWebLink('noVNC 扫码', instance.urls.novnc));
    webSection.appendChild(webLinks);
    card.appendChild(webSection);

    const actions = makeElement('div', 'instance-actions');
    const settingsButton = makeElement('button', 'btn btn-secondary btn-small instance-settings-btn', '');
    settingsButton.type = 'button';
    settingsButton.dataset.instanceSettingsId = instance.id;
    settingsButton.setAttribute('aria-haspopup', 'dialog');
    settingsButton.setAttribute('aria-controls', 'instance-settings-modal');
    settingsButton.setAttribute('aria-expanded', 'false');
    settingsButton.title = `设置：${v3SummaryText(instance)}`;
    settingsButton.appendChild(iconText('c-i-edit', '设置'));
    settingsButton.addEventListener('click', () => instanceSettings.open(instance, settingsButton));
    actions.appendChild(settingsButton);

    const wizardLink = makeElement('a', 'btn btn-primary btn-small instance-wizard-link', '');
    wizardLink.href = wizardUrl(instance.id);
    wizardLink.appendChild(iconText('c-i-edit', '打开向导'));
    actions.appendChild(wizardLink);

    const controlGroup = makeElement('div', 'instance-control-group');
    controlGroup.appendChild(makeActionButton(instance, 'start'));
    controlGroup.appendChild(makeActionButton(instance, 'stop'));
    controlGroup.appendChild(makeActionButton(instance, 'restart'));
    const deleteButton = makeElement('button', 'instance-delete-btn instance-action-btn', '删除实例');
    deleteButton.type = 'button';
    deleteButton.addEventListener('click', () => void handleDeleteInstance(instance, deleteButton));
    controlGroup.appendChild(deleteButton);
    actions.appendChild(controlGroup);
    card.appendChild(actions);

    return card;
  }

  function renderInstances() {
    const grid = $('instances-grid');
    const loading = $('instances-loading');
    const empty = $('instances-empty');
    if (!grid) return;

    if (loading) loading.hidden = true;
    grid.replaceChildren();
    state.instances.forEach((instance) => grid.appendChild(buildInstanceCard(instance)));

    if (empty) empty.hidden = state.instances.length !== 0;
    grid.hidden = state.instances.length === 0;
    setStats(state.instances);
    instanceSettings.refresh();
  }

  async function loadConsole(options = {}) {
    if (state.loading) return false;
    const silent = options.silent === true;
    state.loading = true;
    setRefreshBusy(true);
    setConsoleError('');
    if (!state.loadedOnce) {
      const loading = $('instances-loading');
      if (loading) loading.hidden = false;
    }

    try {
      const [settingsResult, instancesResult] = await Promise.allSettled([
        apiRequest('/api/settings'),
        apiRequest('/api/instances')
      ]);

      if (settingsResult.status === 'fulfilled') {
        const settingsData = asRecord(settingsResult.value);
        if (settingsData.ok === false) {
          renderSettingsError(new ApiError(textValue(settingsData.message, '本地设置读取失败。')));
        } else {
          state.settings = settingsData;
          renderSettings();
        }
      } else {
        renderSettingsError(settingsResult.reason);
      }

      if (instancesResult.status === 'rejected') {
        throw instancesResult.reason;
      }
      const instancesData = asRecord(instancesResult.value);
      if (instancesData.ok === false) {
        throw new ApiError(textValue(instancesData.message, '实例列表读取失败。'));
      }
      const rawInstances = Array.isArray(instancesData.instances)
        ? instancesData.instances
        : Array.isArray(instancesResult.value)
          ? instancesResult.value
          : [];
      const instances = rawInstances.map(normaliseInstance);

      const stateResults = await Promise.allSettled(instances.map(async (instance) => {
        if (!instance.id) throw new ApiError('实例响应缺少 id，无法读取状态。');
        return apiRequest(instancePath('/api/state', instance.id));
      }));

      let stateFailureCount = 0;
      const merged = instances.map((instance, index) => {
        const result = stateResults[index];
        if (result && result.status === 'fulfilled') {
          try {
            return mergeInstanceState(instance, result.value);
          } catch (error) {
            stateFailureCount += 1;
            return { ...instance, state_error: formatError(error, '实例状态读取失败。') };
          }
        }
        stateFailureCount += 1;
        return {
          ...instance,
          state_error: formatError(result ? result.reason : null, '实例状态读取失败。')
        };
      });

      const v3Instances = await Promise.all(merged.map(async (instance) => {
        if (!instance.id) return instance;
        try {
          return await loadV3ForInstance(instance);
        } catch (error) {
          const existing = instance.v3 || {
            plugins: emptyPluginState(),
            memory: emptyMemoryState(instance.memory_mode_hint || ''),
            memoryPolicy: emptyMemoryPolicyState(),
            stats: emptyStatsState()
          };
          const message = formatError(error, '可选运维信息读取失败。');
          return {
            ...instance,
            v3: {
              plugins: { ...existing.plugins, status: 'error', error: message },
              memory: { ...existing.memory, status: 'error', error: message },
              memoryPolicy: existing.memoryPolicy || { ...emptyMemoryPolicyState(), status: 'error', error: message },
              stats: { ...existing.stats, status: 'error', error: message }
            }
          };
        }
      }));

      const statsEntries = v3Instances
        .map((instance) => instance.v3 && instance.v3.stats)
        .filter(Boolean);
      const successfulStats = statsEntries.find((entry) => entry.status === 'ok') || null;
      const statsError = statsEntries.find((entry) => entry.status === 'error');
      const allStatsUnsupported = statsEntries.length > 0 && statsEntries.every((entry) => entry.status === 'unsupported');
      state.instances = v3Instances;
      state.loadedOnce = true;
      state.lastSyncedAt = Date.now();
      state.globalResourceStats = successfulStats;
      state.globalResourceStatus = successfulStats
        ? 'ok'
        : statsError
          ? 'error'
          : allStatsUnsupported
            ? 'unsupported'
            : 'unknown';
      state.globalResourceError = statsError ? statsError.error || '' : '';
      state.globalResourcePartial = Boolean(successfulStats) && statsEntries.some((entry) => entry.status !== 'ok');
      renderInstances();
      renderGlobalResourceSummary();
      renderSettings();
      setConsoleError('');
      if (!silent) {
        showToast(`实例列表已更新，共 ${merged.length} 个实例。`, 'success', 2800);
      }
      if (stateFailureCount > 0 && !silent) {
        showToast(`${stateFailureCount} 个实例的实时状态读取失败，请点击右上角“刷新”重试。`, 'warning', 6000);
      }
      return true;
    } catch (error) {
      const message = formatError(error, '实例列表读取失败。');
      setConsoleError(message);
      if (!state.loadedOnce) {
        state.instances = [];
        renderInstances();
        const empty = $('instances-empty');
        if (empty) empty.hidden = true;
      }
      if (!silent) showToast(message, 'error', 6000);
      return false;
    } finally {
      state.loading = false;
      setRefreshBusy(false);
      const loading = $('instances-loading');
      if (loading) loading.hidden = true;
    }
  }

  async function handleInstanceAction(instance, action) {
    if (state.actionInstanceId || !instance.id) return;
    const labels = { start: '启动', stop: '停止', restart: '重启' };
    if (!labels[action]) return;
    state.actionInstanceId = instance.id;
    renderInstances();
    showToast(`正在发送${labels[action]}指令…`, 'info', 2600);

    try {
      const data = await apiRequest(instancePath('/api/action', instance.id), {
        method: 'POST',
        body: { action }
      });
      if (data && data.ok === false) {
        throw new ApiError(textValue(data.message, `${labels[action]}操作失败。`));
      }
      const message = textValue(data && data.message, `${labels[action]}指令已发送。`);
      showToast(message, 'success');
      await loadConsole({ silent: true });
    } catch (error) {
      const message = formatError(error, `${labels[action]}操作失败。`);
      showToast(message, 'error', 6000);
    } finally {
      state.actionInstanceId = '';
      renderInstances();
    }
  }

  function openCreatePanel() {
    const panel = $('create-instance-panel');
    if (!panel) return;
    panel.hidden = false;
    $$('[data-open-create]').forEach((button) => button.setAttribute('aria-expanded', 'true'));
    const input = $('new-instance-name');
    if (input) {
      input.disabled = false;
      input.focus({ preventScroll: true });
    }
    panel.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  function closeCreatePanel() {
    const panel = $('create-instance-panel');
    if (!panel) return;
    panel.hidden = true;
    $$('[data-open-create]').forEach((button) => button.setAttribute('aria-expanded', 'false'));
    const error = $('new-instance-error');
    if (error) {
      error.hidden = true;
      error.textContent = '';
    }
    const input = $('new-instance-name');
    if (input) input.removeAttribute('aria-invalid');
  }

  async function handleCreateInstance(event) {
    event.preventDefault();
    if (state.creating) return;
    const input = $('new-instance-name');
    const errorElement = $('new-instance-error');
    const name = input ? input.value.trim() : '';
    if (errorElement) {
      errorElement.textContent = '';
      errorElement.hidden = true;
    }
    if (input) input.removeAttribute('aria-invalid');

    if (!name) {
      if (input) input.setAttribute('aria-invalid', 'true');
      if (errorElement) {
        errorElement.textContent = '请输入实例名称。';
        errorElement.hidden = false;
      }
      if (input) input.focus();
      return;
    }
    if (name.length > 60) {
      if (input) input.setAttribute('aria-invalid', 'true');
      if (errorElement) {
        errorElement.textContent = '实例名称不能超过 60 个字符。';
        errorElement.hidden = false;
      }
      if (input) input.focus();
      return;
    }

    setCreateBusy(true);
    try {
      const data = await apiRequest('/api/instances', {
        method: 'POST',
        body: { name }
      });
      if (data && data.ok === false) {
        throw new ApiError(textValue(data.message, '实例创建失败。'));
      }
      const instance = asRecord(data && data.instance);
      const instanceId = textValue(instance.id, '');
      if (!instanceId) throw new ApiError('本地服务未返回新实例 ID。');
      showToast('实例已创建，正在打开部署向导。', 'success', 3000);
      window.location.assign(wizardUrl(instanceId));
    } catch (error) {
      const message = formatError(error, '实例创建失败。');
      if (input) input.setAttribute('aria-invalid', 'true');
      if (errorElement) {
        errorElement.textContent = message;
        errorElement.hidden = false;
      }
      showToast(message, 'error', 6000);
    } finally {
      setCreateBusy(false);
    }
  }

  function bindEvents() {
    const refresh = $('console-refresh');
    if (refresh) refresh.addEventListener('click', () => void manualRefresh());
    const updateButton = $('console-update');
    if (updateButton) updateButton.addEventListener('click', () => void handleUpdateClick());
    const updateOverlay = $('update-overlay');
    if (updateOverlay) {
      updateOverlay.addEventListener('click', (event) => {
        if (event.target === updateOverlay) closeUpdatePanel();
      });
    }
    const updateClose = $('update-close');
    if (updateClose) updateClose.addEventListener('click', () => closeUpdatePanel());
    const updateTabStable = $('update-tab-stable');
    if (updateTabStable) updateTabStable.addEventListener('click', () => switchUpdateTab('stable'));
    const updateTabTest = $('update-tab-test');
    if (updateTabTest) updateTabTest.addEventListener('click', () => switchUpdateTab('test'));
    const updateApplyStable = $('update-apply-stable');
    if (updateApplyStable) {
      updateApplyStable.addEventListener('click', () => {
        armUpdateAction(
          '更新到最新稳定版',
          '确认快进更新整个项目到最新稳定版？会先备份代码、实例配置与密钥，再重启管理服务；实例数据不会被覆盖。',
          () => runUpdateAction('/api/update/apply', { channel: 'stable' })
        );
      });
    }
    const updateConfirmCancel = $('update-confirm-cancel');
    if (updateConfirmCancel) updateConfirmCancel.addEventListener('click', () => clearUpdateConfirm());
    const updateConfirmOk = $('update-confirm-ok');
    if (updateConfirmOk) {
      updateConfirmOk.addEventListener('click', () => {
        const pending = state.updatePending;
        if (!pending || typeof pending.run !== 'function') {
          clearUpdateConfirm();
          return;
        }
        clearUpdateConfirm();
        void pending.run();
      });
    }
    const retry = $('console-error-retry');
    if (retry) retry.addEventListener('click', () => void loadConsole());

    const pluginConfigOverlay = $('plugin-config-overlay');
    if (pluginConfigOverlay) {
      pluginConfigOverlay.addEventListener('click', (event) => {
        if (event.target === pluginConfigOverlay) closePluginConfigPanel();
      });
    }
    const pluginConfigClose = $('plugin-config-close');
    if (pluginConfigClose) pluginConfigClose.addEventListener('click', () => closePluginConfigPanel());
    const pluginConfigCancel = $('plugin-config-cancel');
    if (pluginConfigCancel) pluginConfigCancel.addEventListener('click', () => closePluginConfigPanel());
    const pluginConfigSave = $('plugin-config-save');
    if (pluginConfigSave) pluginConfigSave.addEventListener('click', () => void savePluginConfig());
    const pluginConfigSearch = $('plugin-config-search');
    if (pluginConfigSearch) pluginConfigSearch.addEventListener('input', updatePluginConfigFilter);
    const pluginConfigModifiedOnly = $('plugin-config-modified-only');
    if (pluginConfigModifiedOnly) pluginConfigModifiedOnly.addEventListener('change', updatePluginConfigFilter);
    const pluginConfigGroups = $('plugin-config-groups');
    if (pluginConfigGroups) {
      pluginConfigGroups.addEventListener('keydown', (event) => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        const buttons = Array.from(pluginConfigGroups.querySelectorAll('.plugin-config-group-btn'));
        if (!buttons.length) return;
        const index = buttons.indexOf(document.activeElement);
        if (index < 0) return;
        event.preventDefault();
        let next = index;
        if (event.key === 'ArrowDown') next = (index + 1) % buttons.length;
        else if (event.key === 'ArrowUp') next = (index - 1 + buttons.length) % buttons.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = buttons.length - 1;
        buttons[next].focus();
      });
    }

    const promptManagerOverlay = $('prompt-manager-overlay');
    if (promptManagerOverlay) {
      promptManagerOverlay.addEventListener('click', (event) => {
        if (event.target === promptManagerOverlay) closePromptManager();
      });
    }
    const promptManagerClose = $('prompt-manager-close');
    if (promptManagerClose) promptManagerClose.addEventListener('click', () => closePromptManager());
    const promptManagerNew = $('prompt-manager-new');
    if (promptManagerNew) promptManagerNew.addEventListener('click', () => openPromptEditor('create', {}));
    const promptManagerForm = $('prompt-manager-form');
    if (promptManagerForm) promptManagerForm.addEventListener('submit', submitPromptEditor);
    const promptEditorCancel = $('prompt-editor-cancel');
    if (promptEditorCancel) promptEditorCancel.addEventListener('click', closePromptEditor);
    const promptEditorName = $('prompt-editor-name');
    if (promptEditorName) promptEditorName.addEventListener('input', () => setPromptFieldError(promptEditorName, $('prompt-editor-name-error'), ''));
    const promptEditorPrompt = $('prompt-editor-prompt');
    if (promptEditorPrompt) promptEditorPrompt.addEventListener('input', () => setPromptFieldError(promptEditorPrompt, $('prompt-editor-prompt-error'), ''));
    const promptManagerCancelBusy = $('prompt-manager-cancel-busy');
    if (promptManagerCancelBusy) promptManagerCancelBusy.addEventListener('click', cancelPromptOperation);

    instanceSettings.mount();
    document.addEventListener('keydown', (event) => {
      if (state.promptManager.open) {
        if (event.key === 'Tab' && trapPromptModalFocus(event)) return;
        if (event.key === 'Escape') {
          event.preventDefault();
          closePromptManager();
          return;
        }
      }
      if (event.key !== 'Escape') return;
      if (state.pluginConfig.open) {
        event.preventDefault();
        closePluginConfigPanel();
        return;
      }
      if (instanceSettings.isOpen()) {
        event.preventDefault();
        instanceSettings.close();
      }
    });

    $$('[data-open-create]').forEach((button) => {
      button.setAttribute('aria-expanded', 'false');
      button.addEventListener('click', openCreatePanel);
    });
    const cancel = $('create-instance-cancel');
    if (cancel) cancel.addEventListener('click', closeCreatePanel);
    const form = $('create-instance-form');
    if (form) form.addEventListener('submit', handleCreateInstance);
    const nameInput = $('new-instance-name');
    if (nameInput) {
      nameInput.addEventListener('input', () => {
        nameInput.removeAttribute('aria-invalid');
        const error = $('new-instance-error');
        if (error) {
          error.hidden = true;
          error.textContent = '';
        }
      });
    }
  }

  async function init() {
    bindEvents();
    await loadConsole({ silent: true });
    scheduleNextSync();
    void checkForUpdate(true);
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (state.pollTimer) window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
      return;
    }
    void loadConsole({ silent: true }).finally(() => scheduleNextSync());
  });

  document.addEventListener('DOMContentLoaded', () => {
    init().catch((error) => {
      const message = formatError(error, '控制台初始化失败，请刷新页面重试。');
      setConsoleError(message);
      showToast(message, 'error', 7000);
    });
  });
})();
