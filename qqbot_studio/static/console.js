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
    pluginGroupView: {},
    pluginGroupExpanded: {},
    settingsTab: 'plugins',
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
    renderSections: (instance) => [buildSettingsShell(instance)],
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
          note: entry.current === true ? '当前版本' : '自动发现的测试版',
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
      setText('update-subtitle', '稳定版跟随正式发布；测试版自动获取最新 alpha/beta/rc 预发布版本。');
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

  async function loadPluginGroupsForInstance(instance) {
    try {
      const data = asRecord(await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugin-groups`));
      return data.ok === false ? { ok: false, groups: [], error: textValue(data.error, '插件分组读取失败。') } : data;
    } catch (error) {
      return { ok: false, groups: [], error: formatError(error, '插件分组读取失败。') };
    }
  }

  async function loadV3ForInstance(instance) {
    const results = await Promise.allSettled([
      loadPluginsForInstance(instance),
      loadMemoryForInstance(instance),
      loadMemoryPolicyForInstance(instance),
      loadStatsForInstance(instance),
      loadPluginGroupsForInstance(instance)
    ]);
    const fallback = (index, value) => {
      const result = results[index];
      if (result && result.status === 'fulfilled') return result.value;
      const message = formatError(result ? result.reason : null, '读取失败。');
      if (value && typeof value === 'object') return { ...value, status: 'error', error: message };
      return value;
    };
    const plugins = fallback(0, emptyPluginState());
    const memory = fallback(1, emptyMemoryState(instance.memory_mode_hint || ''));
    const memoryPolicy = fallback(2, { ...emptyMemoryPolicyState(), status: 'error' });
    const stats = fallback(3, emptyStatsState());
    const pluginGroups = fallback(4, { ok: false, groups: [], error: '插件分组读取失败。' });
    return { ...instance, v3: { plugins, memory, memoryPolicy, stats, pluginGroups } };
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
    const subgroups = new Map();
    for (const entry of entries) {
      const subgroup = textValue(entry.uiSubgroup, '').trim();
      if (!subgroup) continue;
      if (!subgroups.has(subgroup)) subgroups.set(subgroup, []);
      subgroups.get(subgroup).push(entry);
    }
    if (subgroups.size) {
      const placed = new Set();
      for (const [title, items] of subgroups) {
        const block = makeElement('div', 'plugin-config-subgroup');
        block.appendChild(makeElement('h5', 'plugin-config-subgroup-title', title));
        for (const entry of items) {
          block.appendChild(entry.element);
          placed.add(entry);
        }
        body.appendChild(block);
      }
      for (const entry of entries) {
        if (!placed.has(entry)) body.appendChild(entry.element);
      }
    } else {
      for (const entry of entries) body.appendChild(entry.element);
    }
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

  function pluginMarketActionLabel(entry) {
    const status = textValue(entry.status, '');
    if (status === 'available') return '安装';
    if (status === 'update_available') return '更新';
    if (status === 'reserved') return '主插件保留';
    if (status === 'unregistered') return '目录未登记';
    return '已安装';
  }

  function pluginMarketActionEnabled(entry) {
    return textValue(entry.status, '') === 'available' || textValue(entry.status, '') === 'update_available';
  }

  function pluginMarketStatusText(entry) {
    const status = textValue(entry.status, '');
    if (status === 'available') return '可安装';
    if (status === 'update_available') return '可更新';
    if (status === 'reserved') return '保留';
    if (status === 'unregistered') return '未登记目录';
    return '已安装';
  }

  function pluginMarketIdempotencyKey(prefix) {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  }

  function pluginMarketAskConfirm(panel, message) {
    return new Promise((resolve) => {
      const previous = panel.querySelector('[data-plugin-market-confirm]');
      if (previous) previous.remove();
      const box = makeElement('div', 'plugin-market-confirm');
      box.dataset.pluginMarketConfirm = 'true';
      box.setAttribute('role', 'alertdialog');
      box.setAttribute('aria-label', '确认插件市场操作');
      const copy = makeElement('p', 'plugin-market-confirm-copy', message);
      const actions = makeElement('div', 'plugin-market-confirm-actions');
      const cancel = makeElement('button', 'btn btn-small btn-quiet', '取消');
      cancel.type = 'button';
      const accept = makeElement('button', 'btn btn-small btn-primary', '确认');
      accept.type = 'button';
      const finish = (value) => {
        box.remove();
        resolve(value);
      };
      cancel.addEventListener('click', () => finish(false));
      accept.addEventListener('click', () => finish(true));
      actions.appendChild(cancel);
      actions.appendChild(accept);
      box.appendChild(copy);
      box.appendChild(actions);
      panel.prepend(box);
      cancel.focus();
    });
  }

  function pluginMarketAskText(panel, titleText, defaultValue) {
    return new Promise((resolve) => {
      const previous = panel.querySelector('[data-plugin-market-confirm]');
      if (previous) previous.remove();
      const box = makeElement('div', 'plugin-market-confirm');
      box.dataset.pluginMarketConfirm = 'true';
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-label', titleText);
      const label = makeElement('label', 'plugin-market-confirm-label', titleText);
      const input = document.createElement('input');
      input.type = 'text';
      input.maxLength = 80;
      input.value = textValue(defaultValue, '');
      const actions = makeElement('div', 'plugin-market-confirm-actions');
      const cancel = makeElement('button', 'btn btn-small btn-quiet', '取消');
      cancel.type = 'button';
      const accept = makeElement('button', 'btn btn-small btn-primary', '确定');
      accept.type = 'button';
      const finish = (value) => {
        box.remove();
        resolve(value);
      };
      cancel.addEventListener('click', () => finish(null));
      accept.addEventListener('click', () => finish(input.value));
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          finish(input.value);
        }
        if (event.key === 'Escape') {
          event.preventDefault();
          finish(null);
        }
      });
      label.appendChild(input);
      actions.appendChild(cancel);
      actions.appendChild(accept);
      box.appendChild(label);
      box.appendChild(actions);
      panel.prepend(box);
      input.focus();
      input.select();
    });
  }

  function pluginMarketSetStatus(panel, message, tone = 'info') {
    const target = panel.querySelector('[data-plugin-market-status]');
    if (target) setV3Status(target, message, tone);
  }

  function pluginMarketInstalledChoices(instance, marketData) {
    const choices = new Map();
    for (const item of Array.isArray(marketData && marketData.installed) ? marketData.installed : []) {
      const id = textValue(item.plugin_id || item.id, '');
      if (id) choices.set(id, textValue(item.name || id, id));
    }
    const plugins = instance && instance.v3 && instance.v3.plugins ? instance.v3.plugins.items : [];
    for (const item of Array.isArray(plugins) ? plugins : []) {
      const id = textValue(item.id, '');
      if (id) choices.set(id, textValue(item.name || id, id));
    }
    return choices;
  }

  async function applyPluginGroupActions(instance, actions, panel, confirmText, options = {}) {
    if (!Array.isArray(actions) || actions.length === 0) return false;
    if (!options.skipConfirm && !(await pluginMarketAskConfirm(panel, confirmText || '确认应用这些插件分组变更？不会删除任何插件文件。'))) return false;
    pluginMarketSetStatus(panel, '正在生成分组预览…', 'info');
    try {
      const preview = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugin-groups/preview`, {
        method: 'POST',
        body: { actions }
      });
      const result = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugin-groups/apply`, {
        method: 'POST',
        body: {
          preview_token: preview.preview_token,
          actions,
          confirm: true,
          idempotency_key: pluginMarketIdempotencyKey(`group-${instance.id}`)
        }
      });
      pluginMarketSetStatus(panel, textValue(result.message, '分组已更新。'), 'success');
      return true;
    } catch (error) {
      pluginMarketSetStatus(panel, formatError(error, '插件分组更新失败。'), 'error');
      return false;
    }
  }

  async function startPluginOperation(instance, entry, operation, panel) {
    if (operation !== 'delete' && !pluginMarketActionEnabled(entry)) return;
    const pluginId = textValue(entry.plugin_id || entry.id, '');
    if (!pluginId) return;
    const verb = operation === 'delete' ? '删除' : operation === 'update' ? '更新' : '安装';
    const version = textValue(entry.version, '');
    const sha = textValue(entry.sha256, '');
    const confirmMessage = operation === 'delete'
      ? `确认删除插件 ${pluginId} 的代码目录？\n只删除市场登记的代码目录；配置、plugin_data、数据库和会话都会保留，分组关系会清理。`
      : `确认${verb}插件 ${pluginId}${version ? ` v${version}` : ''}？\nSHA256：${sha}\n插件代码会写入实例插件目录，配置/plugin_data/数据库/会话不会被删除。`;
    if (!(await pluginMarketAskConfirm(panel, confirmMessage))) return;
    pluginMarketSetStatus(panel, `正在生成${verb}预览…`, 'info');
    try {
      const preview = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugin-operations/preview`, {
        method: 'POST',
        body: { operation, plugin_id: pluginId }
      });
      pluginMarketSetStatus(panel, `正在${verb}插件…`, 'info');
      const result = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugin-operations`, {
        method: 'POST',
        body: {
          operation,
          plugin_id: pluginId,
          preview_token: preview.preview_token,
          confirm: true,
          idempotency_key: pluginMarketIdempotencyKey(`operation-${instance.id}`)
        }
      });
      pluginMarketSetStatus(panel, textValue(result.message, `插件已${verb}。`), 'success');
      if (instance.v3) instance.v3.plugins = await loadPluginsForInstance(instance);
      updateV3Summary(instance);
      if (instanceSettings.isOpenFor(instance.id)) instanceSettings.refresh();
    } catch (error) {
      pluginMarketSetStatus(panel, formatError(error, `插件${verb}失败。`), 'error');
    }
  }

  function renderPluginMarketPanel(instance, panel, marketData, groupData, options = {}) {
    const marketOnly = options.marketOnly === true;
    panel.replaceChildren();
    panel.dataset.loaded = 'true';
    const status = makeElement('p', 'instance-v3-status plugin-market-status');
    status.dataset.pluginMarketStatus = 'status';
    status.setAttribute('role', 'status');
    status.hidden = true;
    panel.appendChild(status);

    const tabBar = makeElement('div', 'plugin-market-tabs');
    tabBar.setAttribute('role', 'tablist');
    const marketTab = makeElement('button', 'plugin-market-tab is-active', '独立插件市场');
    marketTab.type = 'button';
    marketTab.setAttribute('role', 'tab');
    marketTab.setAttribute('aria-selected', 'true');
    marketTab.setAttribute('aria-controls', `plugin-market-pane-${instance.id}`);
    const groupTab = makeElement('button', 'plugin-market-tab', '插件分组');
    groupTab.type = 'button';
    groupTab.setAttribute('role', 'tab');
    groupTab.setAttribute('aria-selected', 'false');
    groupTab.setAttribute('aria-controls', `plugin-group-pane-${instance.id}`);
    tabBar.appendChild(marketTab);
    if (!marketOnly) tabBar.appendChild(groupTab);
    panel.appendChild(tabBar);
    const marketPane = makeElement('div', 'plugin-market-pane');
    marketPane.id = `plugin-market-pane-${instance.id}`;
    marketPane.setAttribute('role', 'tabpanel');
    const groupPane = makeElement('div', 'plugin-market-pane');
    groupPane.id = `plugin-group-pane-${instance.id}`;
    groupPane.setAttribute('role', 'tabpanel');
    groupPane.hidden = true;
    const selectTab = (tab) => {
      const showGroups = tab === 'groups';
      marketTab.classList.toggle('is-active', !showGroups);
      if (groupTab.isConnected) {
        groupTab.classList.toggle('is-active', showGroups);
        groupTab.setAttribute('aria-selected', showGroups ? 'true' : 'false');
      }
      marketTab.setAttribute('aria-selected', showGroups ? 'false' : 'true');
      marketPane.hidden = showGroups;
      groupPane.hidden = !showGroups;
    };
    marketTab.addEventListener('click', () => selectTab('market'));
    groupTab.addEventListener('click', () => selectTab('groups'));

    const marketBlock = makeElement('section', 'plugin-market-block');
    const marketHead = makeElement('div', 'plugin-market-block-head');
    marketHead.appendChild(makeElement('h5', '', '独立插件市场'));
    const sourceBits = [];
    if (marketData && marketData.source && marketData.source.name) sourceBits.push(`来源：${marketData.source.name}`);
    if (marketData && marketData.refreshed_at) sourceBits.push(`刷新：${marketData.refreshed_at}`);
    marketHead.appendChild(makeElement('span', 'plugin-market-source', sourceBits.join(' · ') || '尚未刷新'));
    marketBlock.appendChild(marketHead);
    const entries = marketData && Array.isArray(marketData.entries) ? marketData.entries : [];
    if (!entries.length) {
      marketBlock.appendChild(makeElement('p', 'instance-v3-empty', '市场暂无条目。请把 catalog.json 放入实例 studio-plugins/market，或配置受信 URL 后刷新。'));
    } else {
      const pageSize = 5;
      const totalPages = Math.max(1, Math.ceil(entries.length / pageSize));
      let page = Number(panel.dataset.marketPage || '1');
      if (!Number.isFinite(page) || page < 1 || page > totalPages) page = 1;
      panel.dataset.marketPage = String(page);
      const pageEntries = entries.slice((page - 1) * pageSize, page * pageSize);
      const list = makeElement('div', 'plugin-market-list');
      for (const entry of pageEntries) {
        const row = makeElement('div', 'plugin-market-entry');
        const info = makeElement('div', 'plugin-info');
        const title = makeElement('div', 'plugin-title-line');
        title.appendChild(makeElement('strong', 'plugin-name', textValue(entry.name || entry.id, entry.id)));
        title.appendChild(makeElement('span', 'plugin-source-badge', pluginMarketStatusText(entry)));
        if (entry.version) title.appendChild(makeElement('span', 'plugin-version', `v${entry.version}`));
        info.appendChild(title);
        if (entry.description) info.appendChild(makeElement('p', 'plugin-description', entry.description));
        if (entry.installed_version && entry.installed_version !== entry.version) {
          info.appendChild(makeElement('p', 'plugin-market-meta', `当前版本：${entry.installed_version}`));
        }
        if (entry.error) info.appendChild(makeElement('p', 'plugin-error', entry.error));
        row.appendChild(info);
        const control = makeElement('div', 'plugin-market-entry-control');
        if (pluginMarketActionEnabled(entry)) {
          const operation = textValue(entry.status, '') === 'update_available' ? 'update' : 'install';
          const button = makeElement('button', 'btn btn-secondary btn-small', pluginMarketActionLabel(entry));
          button.type = 'button';
          button.addEventListener('click', () => void startPluginOperation(instance, entry, operation, panel));
          control.appendChild(button);
        }
        if (entry.installed === true && entry.reserved !== true) {
          const removeButton = makeElement('button', 'btn btn-small btn-quiet plugin-danger-action', '删除插件');
          removeButton.type = 'button';
          removeButton.addEventListener('click', () => void startPluginOperation(instance, entry, 'delete', panel));
          control.appendChild(removeButton);
        } else if (!pluginMarketActionEnabled(entry)) {
          const state = makeElement('span', 'plugin-state-text', pluginMarketActionLabel(entry));
          control.appendChild(state);
        }
        row.appendChild(control);
        list.appendChild(row);
      }
      marketBlock.appendChild(list);
      if (totalPages > 1) {
        const pager = makeElement('div', 'plugin-market-pager');
        const prev = makeElement('button', 'btn btn-quiet btn-small', '上一页');
        prev.type = 'button';
        prev.disabled = page <= 1;
        prev.addEventListener('click', () => {
          panel.dataset.marketPage = String(page - 1);
          renderPluginMarketPanel(instance, panel, marketData, groupData);
        });
        const label = makeElement('span', 'plugin-market-page-label', `第 ${page} / ${totalPages} 页`);
        const next = makeElement('button', 'btn btn-quiet btn-small', '下一页');
        next.type = 'button';
        next.disabled = page >= totalPages;
        next.addEventListener('click', () => {
          panel.dataset.marketPage = String(page + 1);
          renderPluginMarketPanel(instance, panel, marketData, groupData);
        });
        pager.appendChild(prev);
        pager.appendChild(label);
        pager.appendChild(next);
        marketBlock.appendChild(pager);
      }
    }
    marketPane.appendChild(marketBlock);
    const groupBlock = makeElement('section', 'plugin-market-block');
    const groupHead = makeElement('div', 'plugin-market-block-head');
    groupHead.appendChild(makeElement('h5', '', '插件分组'));
    groupHead.appendChild(makeElement('span', 'plugin-market-source', '分组只存 SQLite；删除分组不会删除插件文件。'));
    groupBlock.appendChild(groupHead);

    const createRow = makeElement('div', 'plugin-group-create');
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.maxLength = 80;
    nameInput.placeholder = '新分组名称';
    nameInput.setAttribute('aria-label', '新分组名称');
    const createButton = makeElement('button', 'btn btn-small btn-secondary', '创建分组');
    createButton.type = 'button';
    createButton.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      if (!name) {
        pluginMarketSetStatus(panel, '请输入分组名称。', 'warning');
        return;
      }
      const ok = await applyPluginGroupActions(
        instance,
        [{ op: 'create', name }],
        panel,
        `确认创建分组“${name}”？`
      );
      if (ok) await loadPluginMarketPanel(instance, panel);
    });
    createRow.appendChild(nameInput);
    createRow.appendChild(createButton);
    groupBlock.appendChild(createRow);

    const groups = groupData && Array.isArray(groupData.groups) ? groupData.groups : [];
    if (!groups.length) {
      groupBlock.appendChild(makeElement('p', 'instance-v3-empty', '尚未创建分组。'));
    } else {
      const choices = pluginMarketInstalledChoices(instance, marketData);
      const groupList = makeElement('div', 'plugin-group-list');
      for (const group of groups) {
        const groupId = textValue(group.id, '');
        const card = makeElement('div', 'plugin-group-card');
        const groupHeadRow = makeElement('div', 'plugin-group-head');
        groupHeadRow.appendChild(makeElement('strong', 'plugin-group-name', textValue(group.name, '未命名分组')));
        const rename = makeElement('button', 'btn btn-small btn-quiet', '改名');
        rename.type = 'button';
        rename.addEventListener('click', async () => {
          const nextName = await pluginMarketAskText(panel, '新的分组名称', textValue(group.name, ''));
          if (!nextName || !nextName.trim() || nextName.trim() === group.name) return;
          const ok = await applyPluginGroupActions(
            instance,
            [{ op: 'rename', group_id: groupId, name: nextName.trim() }],
            panel,
            `确认将分组改名为“${nextName.trim()}”？`
          );
          if (ok) await loadPluginMarketPanel(instance, panel);
        });
        const removeGroup = makeElement('button', 'btn btn-small btn-quiet plugin-danger-action', '删除分组');
        removeGroup.type = 'button';
        removeGroup.addEventListener('click', async () => {
          const ok = await applyPluginGroupActions(
            instance,
            [{ op: 'delete', group_id: groupId }],
            panel,
            `确认删除分组“${group.name}”？只删除分组元数据，不删除任何插件文件或配置。`
          );
          if (ok) await loadPluginMarketPanel(instance, panel);
        });
        groupHeadRow.appendChild(rename);
        groupHeadRow.appendChild(removeGroup);
        card.appendChild(groupHeadRow);

        const members = Array.isArray(group.plugin_ids) ? group.plugin_ids : [];
        if (members.length) {
          const chips = makeElement('div', 'plugin-group-members');
          for (const pluginId of members) {
            const chip = makeElement('span', 'plugin-group-chip', pluginId);
            const remove = makeElement('button', 'plugin-group-chip-remove', '×');
            remove.type = 'button';
            remove.title = `从分组移出 ${pluginId}`;
            remove.setAttribute('aria-label', `从分组移出 ${pluginId}`);
            remove.addEventListener('click', async () => {
              const ok = await applyPluginGroupActions(
                instance,
                [{ op: 'remove', group_id: groupId, plugin_id: pluginId }],
                panel,
                `确认把 ${pluginId} 移出分组“${group.name}”？不会删除插件文件。`
              );
              if (ok) await loadPluginMarketPanel(instance, panel);
            });
            chip.appendChild(remove);
            chips.appendChild(chip);
          }
          card.appendChild(chips);
        } else {
          card.appendChild(makeElement('p', 'instance-v3-empty', '分组内暂无插件。'));
        }

        const available = Array.from(choices.entries()).filter(([pluginId]) => !members.includes(pluginId));
        if (available.length) {
          const addRow = makeElement('div', 'plugin-group-add');
          const select = document.createElement('select');
          select.setAttribute('aria-label', `选择加入分组 ${group.name} 的插件`);
          for (const [pluginId, label] of available) {
            const option = document.createElement('option');
            option.value = pluginId;
            option.textContent = `${label} (${pluginId})`;
            select.appendChild(option);
          }
          const addButton = makeElement('button', 'btn btn-small btn-secondary', '加入分组');
          addButton.type = 'button';
          addButton.addEventListener('click', async () => {
            const pluginId = select.value;
            if (!pluginId) return;
            const ok = await applyPluginGroupActions(
              instance,
              [{ op: 'add', group_id: groupId, plugin_id: pluginId }],
              panel,
              '',
              { skipConfirm: true }
            );
            if (ok) {
              const option = Array.from(select.options).find((item) => item.value === pluginId);
              if (option) option.remove();
              if (!members.includes(pluginId)) members.push(pluginId);
              let chips = card.querySelector('.plugin-group-members');
              if (!chips) {
                const empty = card.querySelector('.instance-v3-empty');
                if (empty) empty.remove();
                chips = makeElement('div', 'plugin-group-members');
                card.insertBefore(chips, addRow);
              }
              const chip = makeElement('span', 'plugin-group-chip', pluginId);
              const remove = makeElement('button', 'plugin-group-chip-remove', '×');
              remove.type = 'button';
              remove.title = `从分组移出 ${pluginId}`;
              remove.addEventListener('click', async () => {
                const removed = await applyPluginGroupActions(
                  instance,
                  [{ op: 'remove', group_id: groupId, plugin_id: pluginId }],
                  panel,
                  `确认把 ${pluginId} 移出分组“${group.name}”？不会删除插件文件。`
                );
                if (removed) {
                  chip.remove();
                  const index = members.indexOf(pluginId);
                  if (index >= 0) members.splice(index, 1);
                  pluginMarketSetStatus(panel, `已把 ${pluginId} 移出分组“${group.name}”。`, 'success');
                }
              });
              chip.appendChild(remove);
              chips.appendChild(chip);

              // Update the already-rendered group card above without rebuilding settings.
              const groupRecord = instance && instance.v3 && instance.v3.pluginGroups
                ? (instance.v3.pluginGroups.groups || []).find((item) => textValue(item.id, '') === groupId)
                : null;
              if (groupRecord) {
                groupRecord.plugin_ids = Array.isArray(groupRecord.plugin_ids) ? groupRecord.plugin_ids : [];
                if (!groupRecord.plugin_ids.includes(pluginId)) groupRecord.plugin_ids.push(pluginId);
              }
              const section = panel.closest('.instance-v3-section');
              const groupBody = section
                ? Array.from(section.querySelectorAll('[data-plugin-group-body]')).find((item) => item.dataset.pluginGroupBody === groupId)
                : null;
              const pluginRecord = instance && instance.v3 && instance.v3.plugins
                ? (instance.v3.plugins.items || []).find((item) => String(item.id) === String(pluginId))
                : null;
              if (groupBody && pluginRecord) {
                const empty = groupBody.querySelector('.instance-v3-empty');
                if (empty) empty.remove();
                const groupStatus = section ? section.querySelector('[data-v3-status="plugins"]') : null;
                groupBody.appendChild(buildPluginRow(instance, pluginRecord, groupStatus, 'ok'));
              }
              pluginMarketSetStatus(panel, `已把 ${pluginId} 加入分组“${group.name}”。`, 'success');
            }
          });
          addRow.appendChild(select);
          addRow.appendChild(addButton);
          card.appendChild(addRow);
        }
        groupList.appendChild(card);
      }
      groupBlock.appendChild(groupList);
    }
    groupPane.appendChild(groupBlock);
    panel.appendChild(marketPane);
    if (!marketOnly) panel.appendChild(groupPane);
  }

  async function loadPluginMarketPanel(instance, panel, options = {}) {
    panel.dataset.loading = 'true';
    panel.dataset.loaded = '';
    panel.replaceChildren(makeElement('p', 'plugin-config-loading', '正在读取插件市场与分组…'));
    try {
      const [marketData, groupData] = await Promise.all([
        apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugin-market`),
        apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugin-groups`)
      ]);
      renderPluginMarketPanel(instance, panel, asRecord(marketData), asRecord(groupData), options);
    } catch (error) {
      panel.replaceChildren();
      const message = makeElement('p', 'plugin-error', formatError(error, '插件市场读取失败。'));
      message.setAttribute('role', 'alert');
      panel.appendChild(message);
      panel.dataset.loaded = 'true';
    } finally {
      panel.dataset.loading = '';
    }
  }

  async function refreshPluginMarket(instance, panel) {
    pluginMarketSetStatus(panel, '正在刷新市场清单…', 'info');
    try {
      const result = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/plugin-market/refresh`, {
        method: 'POST',
        body: {}
      });
      await loadPluginMarketPanel(instance, panel);
      pluginMarketSetStatus(panel, textValue(result.message, '插件市场已刷新。'), 'success');
    } catch (error) {
      pluginMarketSetStatus(panel, formatError(error, '插件市场刷新失败。'), 'error');
    }
  }

  function buildPluginRow(instance, plugin, statusElement, pluginStatus) {
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
    checkbox.disabled = pluginStatus !== 'ok' || !plugin.id || plugin.enabled === undefined;
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
    return row;
  }

  function buildPluginMarketSection(instance) {
    const section = makeElement('section', 'instance-v3-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-puzzle'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '插件市场'));
    headingCopy.appendChild(makeElement('p', '', '安装、更新和删除独立插件；市场与插件分组分开显示。'));
    heading.appendChild(headingCopy);
    section.appendChild(heading);
    const tools = makeElement('div', 'plugin-market-tools');
    const refresh = makeElement('button', 'btn btn-quiet btn-small', '刷新市场');
    refresh.type = 'button';
    const panel = makeElement('div', 'plugin-market-panel');
    panel.dataset.pluginMarket = 'panel';
    refresh.addEventListener('click', () => void refreshPluginMarket(instance, panel));
    tools.appendChild(refresh);
    tools.appendChild(panel);
    section.appendChild(tools);
    void loadPluginMarketPanel(instance, panel, { marketOnly: true });
    return section;
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

    const groupState = instance && instance.v3 ? instance.v3.pluginGroups : null;
    const groupError = groupState && groupState.error ? String(groupState.error) : '';
    if (!groupState || groupError) {
      const groupNote = makeElement('p', 'plugin-group-load-note', groupError || '正在加载插件分组…');
      section.appendChild(groupNote);
      const retryGroups = makeElement('button', 'btn btn-secondary btn-small', '重新加载分组');
      retryGroups.type = 'button';
      retryGroups.addEventListener('click', async () => {
        retryGroups.disabled = true;
        groupNote.textContent = '正在重新加载插件分组…';
        const data = await loadPluginGroupsForInstance(instance);
        instance.v3 = { ...(instance.v3 || {}), pluginGroups: data };
        section.replaceWith(buildPluginSection(instance));
      });
      section.appendChild(retryGroups);
      if (!groupState) {
        void (async () => {
          const data = await loadPluginGroupsForInstance(instance);
          instance.v3 = { ...(instance.v3 || {}), pluginGroups: data };
          section.replaceWith(buildPluginSection(instance));
        })();
      }
    }
    const groups = groupState && Array.isArray(groupState.groups) ? groupState.groups : [];
    const groupedPluginIds = new Set();
    for (const group of groups) {
      for (const pluginId of Array.isArray(group.plugin_ids) ? group.plugin_ids : []) groupedPluginIds.add(String(pluginId));
    }
    const visiblePlugins = plugins.items.filter((plugin) => !groupedPluginIds.has(String(plugin.id)));
    if (groups.length) {
      const groupHead = makeElement('div', 'plugin-group-view-head');
      groupHead.appendChild(makeElement('h5', '', '插件分组'));
      groupHead.appendChild(makeElement('span', 'plugin-market-source', '分组默认折叠；展开后可编辑组内插件。'));
      section.appendChild(groupHead);
      const groupCards = makeElement('div', 'plugin-group-view-list');
      const expandedState = state.pluginGroupExpanded[instance.id] || {};
      for (const group of groups) {
        const groupId = textValue(group.id, '');
        const memberIds = Array.isArray(group.plugin_ids) ? group.plugin_ids.map(String) : [];
        const groupPlugins = plugins.items.filter((plugin) => memberIds.includes(String(plugin.id)));
        const card = makeElement('div', 'plugin-group-view-card');
        card.dataset.pluginGroupId = groupId;
        const toggle = makeElement('button', 'plugin-group-view-toggle');
        toggle.type = 'button';
        const title = makeElement('span', 'plugin-group-view-title');
        title.appendChild(makeElement('strong', '', textValue(group.name, '未命名分组')));
        title.appendChild(makeElement('span', 'plugin-market-source', `${groupPlugins.length} 个插件`));
        const arrow = makeElement('span', 'plugin-group-view-arrow', expandedState[groupId] ? '▾' : '▸');
        toggle.appendChild(title);
        toggle.appendChild(arrow);
        const body = makeElement('div', 'plugin-group-view-body');
        body.dataset.pluginGroupBody = groupId;
        body.hidden = !expandedState[groupId];
        if (!groupPlugins.length) {
          body.appendChild(makeElement('p', 'instance-v3-empty', '该分组内暂无插件。'));
        } else {
          for (const plugin of groupPlugins) body.appendChild(buildPluginRow(instance, plugin, statusElement, plugins.status));
        }
        toggle.setAttribute('aria-expanded', expandedState[groupId] ? 'true' : 'false');
        toggle.addEventListener('click', () => {
          const next = body.hidden;
          body.hidden = !next;
          expandedState[groupId] = next;
          state.pluginGroupExpanded[instance.id] = expandedState;
          toggle.setAttribute('aria-expanded', next ? 'true' : 'false');
          arrow.textContent = next ? '▾' : '▸';
        });
        card.appendChild(toggle);
        card.appendChild(body);
        groupCards.appendChild(card);
      }
      section.appendChild(groupCards);
    }


    if (visiblePlugins.length > 0) {
      const list = makeElement('div', 'plugin-list');
      for (const plugin of visiblePlugins) list.appendChild(buildPluginRow(instance, plugin, statusElement, plugins.status));
      section.appendChild(list);
    } else if (plugins.status === 'ok') {
      section.appendChild(makeElement('p', 'instance-v3-empty', '未发现未分组插件。'));
    }
    section.appendChild(statusElement);
    return section;
  }

  function buildAuxBudgetSection(instance) {
    const section = makeElement('section', 'instance-v3-section aux-budget-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-database'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '辅助模型预算'));
    headingCopy.appendChild(makeElement(
      'p',
      '',
      '只限制后台可选的记忆整理、摘要、风格刷新、模糊关系判断等辅助调用；不限制主聊天、命令和工具。'
    ));
    heading.appendChild(headingCopy);
    section.appendChild(heading);

    const status = makeElement('p', 'instance-v3-status');
    status.setAttribute('role', 'status');
    section.appendChild(status);

    const toolbar = makeElement('div', 'aux-budget-toolbar');
    const tierLabel = makeElement('label', 'aux-budget-tier-label', '预算档位');
    const tierSelect = document.createElement('select');
    tierSelect.className = 'aux-budget-tier';
    for (const [value, label] of [
      ['off', '关闭（0 元/月）'],
      ['light', '轻量（10 元/月）'],
      ['standard', '标准（20 元/月）'],
      ['advanced', '进阶（50 元/月）'],
      ['full', '完整（100 元/月）']
    ]) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      tierSelect.appendChild(option);
    }
    tierLabel.appendChild(tierSelect);
    toolbar.appendChild(tierLabel);
    const saveButton = makeElement('button', 'btn btn-secondary btn-small', '保存档位');
    saveButton.type = 'button';
    toolbar.appendChild(saveButton);
    section.appendChild(toolbar);

    const summary = makeElement('div', 'aux-budget-summary');
    section.appendChild(summary);

    const featureHint = makeElement('p', 'aux-budget-hint', '单项辅助能力开关；关闭后不会产生该功能的辅助调用。');
    section.appendChild(featureHint);
    const featureList = makeElement('div', 'aux-budget-features');
    section.appendChild(featureList);

    let busy = false;
    let current = null;
    const setBusy = (value) => {
      busy = value;
      tierSelect.disabled = value;
      saveButton.disabled = value;
      for (const input of Array.from(featureList.querySelectorAll('input'))) input.disabled = value;
    };
    const setStatus = (message, tone = 'info') => {
      status.textContent = message || '';
      status.className = `instance-v3-status${tone ? ` is-${tone}` : ''}`;
      status.hidden = !message;
    };
    const money = (value) => `¥${Number(value || 0).toFixed(2)}`;
    const stateText = (value) => ({
      normal: '正常',
      warning: '接近上限（已降级）',
      stopped: '已停止辅助调用',
      unknown: '账本异常，已关闭辅助调用'
    }[value] || value || '未知');

    const renderSummary = () => {
      if (!current) return;
      const statusValue = asRecord(current.status);
      const configValue = asRecord(current.config);
      const usageValue = asRecord(current.usage);
      summary.replaceChildren();
      const items = [
        ['本月周期', textValue(statusValue.cycle, '—')],
        ['已用 / 上限', `${money(statusValue.spent_cny)} / ${money(statusValue.limit_cny)}`],
        ['预占', money(statusValue.reserved_cny)],
        ['状态', stateText(textValue(statusValue.state, ''))],
        ['调用次数', String((Array.isArray(usageValue.features) ? usageValue.features : []).reduce((sum, item) => sum + Number(item.calls || 0), 0))]
      ];
      for (const [label, value] of items) {
        const cell = makeElement('div', 'aux-budget-summary-item');
        cell.appendChild(makeElement('span', '', label));
        cell.appendChild(makeElement('strong', '', value));
        summary.appendChild(cell);
      }
      if (configValue.degraded === true || statusValue.degraded === true) {
        setStatus('预算账本异常，所有辅助调用已关闭；请检查 data/aux_budget.json 与 aux_ledger.db。', 'error');
      }
    };

    const renderFeatures = () => {
      featureList.replaceChildren();
      if (!current) return;
      const configValue = asRecord(current.config);
      const features = asRecord(configValue.features);
      const catalog = Array.isArray(configValue.feature_catalog) ? configValue.feature_catalog : [];
      for (const item of catalog) {
        const id = textValue(item.feature_id, '');
        if (!id) continue;
        const row = makeElement('label', 'aux-budget-feature');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = asRecord(features[id]).enabled === true;
        input.addEventListener('change', async () => {
          setBusy(true);
          setStatus(`正在保存 ${textValue(item.label, id)}…`, 'info');
          try {
            const result = asRecord(await apiRequest(
              `/api/instances/${encodeURIComponent(instance.id)}/aux-budget/features/${encodeURIComponent(id)}`,
              { method: 'POST', body: { enabled: input.checked, expected_revision: asRecord(current.status).revision } }
            ));
            if (result.ok === false) throw new ApiError(textValue(result.message || result.error, '保存失败。'));
            await load();
            setStatus('辅助能力开关已保存。', 'success');
          } catch (error) {
            input.checked = !input.checked;
            setBusy(false);
            setStatus(formatError(error, '保存辅助能力开关失败。'), 'error');
          }
        });
        row.appendChild(input);
        const copy = makeElement('span', 'aux-budget-feature-copy');
        copy.appendChild(makeElement('strong', '', textValue(item.label, id)));
        copy.appendChild(makeElement('small', '', id));
        row.appendChild(copy);
        featureList.appendChild(row);
      }
      if (!catalog.length) featureList.appendChild(makeElement('p', 'aux-budget-hint', '当前没有可显示的辅助能力。'));
    };

    async function load() {
      setBusy(true);
      setStatus('正在读取辅助模型预算…', 'info');
      try {
        const [data, usage] = await Promise.all([
          apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/aux-budget`),
          apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/aux-budget/usage`)
        ]);
        const dataValue = asRecord(data);
        if (dataValue.ok === false && dataValue.degraded !== true) {
          throw new ApiError(textValue(dataValue.message || dataValue.error, '读取辅助模型预算失败。'));
        }
        current = {
          status: asRecord(dataValue.status),
          config: asRecord(dataValue.config),
          usage: asRecord(usage)
        };
        const tier = textValue(current.status.tier, 'standard');
        if (!tierSelect.querySelector(`option[value="${tier}"]`)) {
          const option = document.createElement('option');
          option.value = tier;
          option.textContent = `当前值：${tier}`;
          tierSelect.appendChild(option);
        }
        tierSelect.value = tier;
        renderSummary();
        renderFeatures();
        setStatus(dataValue.degraded === true ? '预算账本异常，辅助调用已关闭。' : '辅助模型预算已读取。', dataValue.degraded === true ? 'error' : 'success');
      } catch (error) {
        setStatus(formatError(error, '辅助模型预算读取失败。'), 'error');
      } finally {
        setBusy(false);
      }
    }

    saveButton.addEventListener('click', async () => {
      setBusy(true);
      setStatus('正在保存预算档位…', 'info');
      try {
        const result = asRecord(await apiRequest(
          `/api/instances/${encodeURIComponent(instance.id)}/aux-budget`,
          { method: 'POST', body: { tier: tierSelect.value, expected_revision: asRecord(current && current.status).revision } }
        ));
        if (result.ok === false) throw new ApiError(textValue(result.message || result.error, '保存预算档位失败。'));
        await load();
        setStatus('预算档位已保存。', 'success');
      } catch (error) {
        setBusy(false);
        setStatus(formatError(error, '保存预算档位失败。'), 'error');
      }
    });

    load();
    return section;
  }

  const GROUP_POLICY_PARAM_META = {
    'participation.reply_probability': ['非 @ 接话概率', '群聊里没有 @ 机器人时，按这个概率随机参与；0 表示不主动接话。'],
    'participation.timezone': ['时区', '分群时间段判断使用的 IANA 时区，例如 Asia/Shanghai。'],
    'context.max_chars': ['上下文长度', '送入模型的上下文/输入最大字符数；过大增加成本。'],
    'persona.profile_id': ['人格 Profile', '该群绑定的人格 Profile ID；留空使用实例默认。'],
    'tool_allowlist': ['工具白名单', '该群允许模型调用的工具；敏感项仅 Studio 可改。'],
    'tool_cooldown_seconds': ['工具冷却', '同一工具在该群的最小调用间隔（秒）。'],
    'require_confirm': ['工具确认', '写操作是否需要二次确认；敏感项仅 Studio 可改。'],
    'default_city': ['默认城市', '群里没有显式地点时，Food 搜索使用的兜底城市。'],
    'max_results': ['最大结果数', '单次查询最多返回多少条结果。'],
    'sort': ['默认排序', 'Food 搜索结果的默认排序维度。'],
    'allow_submit_review': ['允许提交评价', '该群是否允许通过 Food 工具提交新评价。'],
    'decoration_probability': ['装饰概率', 'AI 未启用时，回复后追加本地表情的触发概率。'],
    'ai_decoration_enabled': ['AI 自动装饰', '是否允许 AI 从本地表情中选择一张追加到回复。'],
    'ai_decoration_probability': ['AI 装饰概率', 'AI 自动装饰的触发概率。'],
    'candidate_count': ['候选表情数', '交给 AI 选择的表情候选数量。'],
    'collect_enabled': ['允许收藏', '该群是否允许收藏用户表情。'],
    'allow_write': ['允许写入', '该群是否允许通过 /记忆 写入长期记忆。'],
    'visible_scopes': ['可见 scope', '该群可读取的记忆 scope；敏感项仅 Studio 可改。'],
    'favorability.half_life_days': ['好感半衰期', '好感度衰减到一半所需天数。'],
    'relationship.enabled': ['关系网', '是否启用群成员关系网。'],
    'relationship.min_evidence': ['关系最低证据', '建立关系边所需的最少证据数。'],
    'emotion.half_life_minutes': ['情绪半衰期', '情绪强度衰减到一半所需分钟数。'],
    'style.enabled': ['风格画像', '是否启用按用户/群调整表达风格。'],
    'style.refresh_hours': ['风格刷新间隔', '风格画像后台刷新的最小间隔（小时）。'],
    'inject.max_entries': ['词汇注入条数', '每轮最多注入多少条词汇库条目。'],
    'inject.max_chars': ['词汇注入字符数', '每轮最多注入多少字符的词汇说明。'],
    'help_command_enabled': ['/help 开关', '是否允许 AstrBot 内置 /help 命令；不影响普通聊天。']
  };

  function groupPolicyParamMeta(name, spec) {
    const known = GROUP_POLICY_PARAM_META[name];
    const label = known ? known[0] : name;
    const details = [];
    if (known) details.push(known[1]);
    if (spec && spec.kind) details.push(`类型：${spec.kind}`);
    if (spec && spec.minimum !== null && spec.minimum !== undefined) details.push(`最小：${spec.minimum}`);
    if (spec && spec.maximum !== null && spec.maximum !== undefined) details.push(`最大：${spec.maximum}`);
    if (spec && spec.enum && spec.enum.length) details.push(`可选：${spec.enum.join(' / ')}`);
    return { label, title: `${label}（${name}）\n${details.join('；')}` };
  }

  function buildGroupPolicySection(instance) {
    const section = makeElement('section', 'instance-v3-section group-policy-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-shield'));
    heading.appendChild(icon);
    const copy = makeElement('div', 'instance-v3-section-copy');
    copy.appendChild(makeElement('h4', '', '分群策略'));
    copy.appendChild(makeElement('p', '', '按群或私聊覆盖插件开关与允许参数；只显示已安装插件中经过安全筛选、且运行时真正读取的参数。'));
    heading.appendChild(copy);
    section.appendChild(heading);

    const status = makeElement('p', 'instance-v3-status');
    status.setAttribute('role', 'status');
    section.appendChild(status);

    const toolbar = makeElement('div', 'group-policy-toolbar');
    const scopeSelect = document.createElement('select');
    scopeSelect.className = 'group-policy-scope-type';
    [['group', 'QQ 群'], ['private', '私聊用户']].forEach(([value, label]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      scopeSelect.appendChild(option);
    });
    const scopeInput = document.createElement('input');
    scopeInput.type = 'text';
    scopeInput.className = 'group-policy-scope-id';
    scopeInput.placeholder = '群号或用户 QQ 号';
    scopeInput.autocomplete = 'off';
    const loadButton = makeElement('button', 'btn btn-secondary btn-small', '加载策略');
    loadButton.type = 'button';
    toolbar.append(scopeSelect, scopeInput, loadButton);
    section.appendChild(toolbar);

    const content = makeElement('div', 'group-policy-content');
    section.appendChild(content);
    const auditBox = makeElement('div', 'group-policy-audit');
    section.appendChild(auditBox);

    const setStatus = (message, tone = 'info') => {
      status.textContent = message;
      status.dataset.tone = tone;
    };
    const currentScope = () => ({
      type: scopeSelect.value || 'group',
      id: scopeInput.value.trim()
    });
    const scopePath = (scope, suffix = '') => {
      const base = `/api/instances/${encodeURIComponent(instance.id)}/group-policies/${encodeURIComponent(scope.type)}/${encodeURIComponent(scope.id)}`;
      return suffix ? `${base}/${suffix}` : base;
    };
    const collectParams = (card, schema) => {
      const params = {};
      for (const [name, spec] of Object.entries(schema || {})) {
        const field = card.querySelector(`[data-policy-param="${CSS.escape(name)}"]`);
        if (!field) continue;
        if (spec.kind === 'boolean') {
          params[name] = Boolean(field.checked);
        } else if (field.value !== '') {
          params[name] = field.value;
        }
      }
      return params;
    };

    const loadAudit = async (scope) => {
      try {
        const data = await apiRequest(`${scopePath(scope, 'audit')}?limit=20`);
        auditBox.replaceChildren();
        auditBox.appendChild(makeElement('h5', '', '最近修改'));
        const items = Array.isArray(data.items) ? data.items : [];
        if (!items.length) {
          auditBox.appendChild(makeElement('p', 'instance-v3-empty', '暂无修改记录。'));
          return;
        }
        const list = makeElement('div', 'group-policy-audit-list');
        items.forEach((row) => {
          const line = makeElement('div', 'group-policy-audit-row');
          line.appendChild(makeElement('span', 'group-policy-audit-action', textValue(row.action, 'update')));
          line.appendChild(makeElement('span', 'group-policy-audit-target', textValue(row.target, '')));
          line.appendChild(makeElement('span', 'group-policy-audit-time', textValue(row.ts, '')));
          list.appendChild(line);
        });
        auditBox.appendChild(list);
      } catch (error) {
        auditBox.replaceChildren(makeElement('p', 'plugin-error', formatError(error, '审计记录读取失败。')));
      }
    };

    const renderScope = (data, scope) => {
      content.replaceChildren();
      const templates = Array.isArray(data.templates) ? data.templates : [];
      const summary = makeElement('div', 'group-policy-summary');
      summary.appendChild(makeElement('span', '', `revision ${data.revision} · ${data.available_plugins.length} 个插件`));
      if (data.degraded) summary.appendChild(makeElement('span', 'group-policy-degraded', '策略库降级，写操作已关闭'));
      content.appendChild(summary);

      const templateRow = makeElement('div', 'group-policy-template-row');
      templateRow.appendChild(makeElement('label', '', '群模板'));
      const templateSelect = document.createElement('select');
      const none = document.createElement('option');
      none.value = '';
      none.textContent = '不使用模板';
      templateSelect.appendChild(none);
      templates.forEach((id) => {
        const option = document.createElement('option');
        option.value = id;
        option.textContent = id;
        templateSelect.appendChild(option);
      });
      templateSelect.value = textValue(data.template, '');
      templateRow.appendChild(templateSelect);
      const applyTemplate = makeElement('button', 'btn btn-secondary btn-small', '应用模板');
      applyTemplate.type = 'button';
      applyTemplate.addEventListener('click', async () => {
        if (!templateSelect.value) return;
        setStatus('正在应用模板…');
        try {
          await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/group-policy-templates/${encodeURIComponent(templateSelect.value)}/apply`, {
            method: 'POST',
            body: { group_id: scope.id, expected_revision: data.revision }
          });
          setStatus('模板已应用。', 'success');
          await loadScope();
        } catch (error) {
          setStatus(formatError(error, '模板应用失败。'), 'error');
        }
      });
      templateRow.appendChild(applyTemplate);
      const resetAll = makeElement('button', 'btn btn-danger btn-small', '重置整群');
      resetAll.type = 'button';
      resetAll.addEventListener('click', async () => {
        setStatus('正在重置…');
        try {
          await apiRequest(scopePath(scope, 'reset'), { method: 'POST', body: { expected_revision: data.revision } });
          setStatus('已重置。', 'success');
          await loadScope();
        } catch (error) {
          setStatus(formatError(error, '重置失败。'), 'error');
        }
      });
      templateRow.appendChild(resetAll);
      content.appendChild(templateRow);

      const pluginGrid = makeElement('div', 'group-policy-grid');
      const pluginMeta = new Map(
        (((instance.v3 || {}).plugins || {}).items || []).map((plugin) => [String(plugin.id || plugin.plugin_id || ''), plugin])
      );
      const availablePlugins = (data.available_plugins || []).filter((pluginId) => (
        pluginMeta.has(String(pluginId)) || Boolean(data.plugins && data.plugins[pluginId])
      ));
      for (const pluginId of availablePlugins) {
        const raw = data.plugins && data.plugins[pluginId] ? data.plugins[pluginId] : {};
        const effective = data.effective && data.effective[pluginId] ? data.effective[pluginId] : {};
        const schema = data.schemas && data.schemas[pluginId] ? data.schemas[pluginId] : {};
        const meta = pluginMeta.get(String(pluginId)) || {};
        const card = makeElement('div', 'group-policy-card');
        const cardHead = makeElement('div', 'group-policy-card-head');
        const title = makeElement('div', 'group-policy-card-title');
        title.appendChild(makeElement('strong', '', textValue(meta.name, pluginId)));
        title.appendChild(makeElement('code', 'group-policy-plugin-id', pluginId));
        const enabledLabel = makeElement('label', 'group-policy-enabled');
        const enabled = document.createElement('input');
        enabled.type = 'checkbox';
        enabled.checked = raw.enabled !== undefined ? Boolean(raw.enabled) : Boolean(effective.enabled !== false);
        enabledLabel.append(enabled, document.createTextNode(' 启用'));
        cardHead.appendChild(title);
        cardHead.appendChild(enabledLabel);
        card.appendChild(cardHead);
        if (meta.description) card.appendChild(makeElement('p', 'group-policy-plugin-desc', String(meta.description)));
        const rawParams = raw.params || {};
        const effectiveParams = effective.params || {};
        for (const [name, spec] of Object.entries(schema)) {
          if (!spec.group_overridable || spec.sensitive) continue;
          const paramMeta = groupPolicyParamMeta(name, spec);
          const label = makeElement('label', 'group-policy-field');
          label.title = paramMeta.title;
          label.appendChild(makeElement('span', 'group-policy-field-label', paramMeta.label));
          let field;
          if (spec.kind === 'boolean') {
            field = document.createElement('input');
            field.type = 'checkbox';
            field.checked = rawParams[name] !== undefined ? Boolean(rawParams[name]) : Boolean(effectiveParams[name]);
          } else if (spec.kind === 'enum') {
            field = document.createElement('select');
            const empty = document.createElement('option');
            empty.value = '';
            empty.textContent = `默认${effectiveParams[name] !== undefined ? `（${effectiveParams[name]}）` : ''}`;
            field.appendChild(empty);
            (spec.enum || []).forEach((value) => {
              const option = document.createElement('option');
              option.value = value;
              option.textContent = value;
              field.appendChild(option);
            });
            field.value = rawParams[name] !== undefined ? String(rawParams[name]) : '';
          } else {
            field = document.createElement('input');
            field.type = spec.kind === 'integer' || spec.kind === 'number' ? 'number' : 'text';
            if (spec.minimum !== null && spec.minimum !== undefined) field.min = spec.minimum;
            if (spec.maximum !== null && spec.maximum !== undefined) field.max = spec.maximum;
            field.value = rawParams[name] !== undefined ? String(rawParams[name]) : '';
            field.placeholder = effectiveParams[name] !== undefined ? String(effectiveParams[name]) : '默认';
          }
          field.dataset.policyParam = name;
          field.setAttribute('aria-label', `${paramMeta.label}（${name}）`);
          label.appendChild(field);
          const defaultValue = effectiveParams[name];
          label.appendChild(makeElement(
            'small',
            'group-policy-field-meta',
            defaultValue !== undefined ? `默认：${String(defaultValue)}` : '默认：未设置'
          ));
          card.appendChild(label);
        }
        const actions = makeElement('div', 'group-policy-actions');
        const save = makeElement('button', 'btn btn-primary btn-small', '保存');
        save.type = 'button';
        save.addEventListener('click', async () => {
          setStatus('正在保存…');
          try {
            await apiRequest(scopePath(scope, encodeURIComponent(pluginId)), {
              method: 'POST',
              body: { enabled: enabled.checked, params: collectParams(card, schema), expected_revision: data.revision }
            });
            setStatus(`${pluginId} 已保存。`, 'success');
            await loadScope();
          } catch (error) {
            setStatus(formatError(error, '保存失败。'), 'error');
          }
        });
        const reset = makeElement('button', 'btn btn-secondary btn-small', '重置');
        reset.type = 'button';
        reset.addEventListener('click', async () => {
          setStatus('正在重置…');
          try {
            await apiRequest(scopePath(scope, `${encodeURIComponent(pluginId)}/reset`), {
              method: 'POST', body: { expected_revision: data.revision }
            });
            setStatus(`${pluginId} 已重置。`, 'success');
            await loadScope();
          } catch (error) {
            setStatus(formatError(error, '重置失败。'), 'error');
          }
        });
        actions.append(save, reset);
        card.appendChild(actions);
        pluginGrid.appendChild(card);
      }
      content.appendChild(pluginGrid);
    };

    const loadScope = async () => {
      const scope = currentScope();
      if (!scope.id) {
        setStatus('请输入群号或用户 QQ 号。', 'error');
        return;
      }
      setStatus('正在读取策略…');
      content.replaceChildren(makeElement('p', 'plugin-config-loading', '正在读取分群策略…'));
      try {
        const data = await apiRequest(scopePath(scope));
        renderScope(data, scope);
        setStatus('策略已加载；修改会在下一条消息生效。', 'success');
        await loadAudit(scope);
      } catch (error) {
        content.replaceChildren();
        setStatus(formatError(error, '分群策略读取失败。'), 'error');
      }
    };
    loadButton.addEventListener('click', loadScope);
    scopeInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') loadScope();
    });
    return section;
  }

  function buildGroupPolicyTemplateSection(instance) {
    const section = makeElement('section', 'instance-v3-section group-policy-template-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-database'));
    heading.appendChild(icon);
    const copy = makeElement('div', 'instance-v3-section-copy');
    copy.appendChild(makeElement('h4', '', '群策略模板'));
    copy.appendChild(makeElement('p', '', '模板只包含允许群覆盖的非敏感参数；保存后可在群策略页应用。'));
    heading.appendChild(copy);
    section.appendChild(heading);

    const status = makeElement('p', 'instance-v3-status');
    status.setAttribute('role', 'status');
    section.appendChild(status);
    const toolbar = makeElement('div', 'group-policy-toolbar');
    const idInput = document.createElement('input');
    idInput.type = 'text';
    idInput.placeholder = '模板 ID，例如 standard';
    idInput.autocomplete = 'off';
    const loadButton = makeElement('button', 'btn btn-secondary btn-small', '加载');
    loadButton.type = 'button';
    toolbar.append(idInput, loadButton);
    section.appendChild(toolbar);
    const editor = document.createElement('textarea');
    editor.className = 'group-policy-template-editor';
    editor.rows = 12;
    editor.placeholder = '{"qqbot_food":{"enabled":true,"params":{"default_city":"长春市"}}}';
    section.appendChild(editor);
    const actions = makeElement('div', 'group-policy-actions');
    const save = makeElement('button', 'btn btn-primary btn-small', '保存模板');
    save.type = 'button';
    const remove = makeElement('button', 'btn btn-danger btn-small', '删除模板');
    remove.type = 'button';
    actions.append(save, remove);
    section.appendChild(actions);

    let revision = 0;
    const setStatus = (message, tone = 'info') => {
      status.textContent = message;
      status.dataset.tone = tone;
    };
    const loadTemplate = async () => {
      const templateId = idInput.value.trim();
      if (!templateId) {
        setStatus('请输入模板 ID。', 'error');
        return;
      }
      try {
        const data = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/group-policy-templates/${encodeURIComponent(templateId)}`);
        revision = Number(data.revision || 0);
        const entry = data.templates && data.templates[templateId];
        editor.value = entry ? JSON.stringify(entry.plugins || {}, null, 2) : '{}';
        setStatus('模板已加载。', 'success');
      } catch (error) {
        setStatus(formatError(error, '模板读取失败。'), 'error');
      }
    };
    save.addEventListener('click', async () => {
      const templateId = idInput.value.trim();
      if (!templateId) return setStatus('请输入模板 ID。', 'error');
      let plugins;
      try {
        plugins = JSON.parse(editor.value || '{}');
      } catch (error) {
        return setStatus('模板 JSON 格式错误。', 'error');
      }
      try {
        const result = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/group-policy-templates/${encodeURIComponent(templateId)}`, {
          method: 'POST', body: { plugins, expected_revision: revision }
        });
        revision = Number(result.revision || revision);
        setStatus('模板已保存。', 'success');
      } catch (error) {
        setStatus(formatError(error, '模板保存失败。'), 'error');
      }
    });
    remove.addEventListener('click', async () => {
      const templateId = idInput.value.trim();
      if (!templateId) return setStatus('请输入模板 ID。', 'error');
      try {
        const result = await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/group-policy-templates/${encodeURIComponent(templateId)}`, {
          method: 'POST', body: { action: 'delete', expected_revision: revision }
        });
        revision = Number(result.revision || revision);
        editor.value = '{}';
        setStatus('模板已删除。', 'success');
      } catch (error) {
        setStatus(formatError(error, '模板删除失败。'), 'error');
      }
    });
    loadButton.addEventListener('click', loadTemplate);
    return section;
  }

  function buildSettingsShell(instance) {
    const shell = makeElement('div', 'settings-shell');
    const nav = makeElement('nav', 'settings-nav');
    nav.setAttribute('aria-label', '实例设置菜单');
    const paneHost = makeElement('div', 'settings-panes');
    const categories = [
      { id: 'plugins', label: '插件管理', sections: [buildPluginSection(instance)] },
      { id: 'market', label: '插件市场', sections: [buildPluginMarketSection(instance)] },
      {
        id: 'memory',
        label: '记忆',
        sections: [
          buildMemorySection(instance),
          buildMemoryPolicySection(instance),
          buildMemoryContentSection(instance)
        ]
      },
      { id: 'groups', label: '群策略', sections: [buildGroupPolicySection(instance), buildGroupPolicyTemplateSection(instance)] },
      { id: 'prompts', label: '提示词', sections: [buildPromptManagerSection(instance)] },
      { id: 'aux', label: '辅助模型', sections: [buildAuxBudgetSection(instance)] },
      { id: 'stats', label: '运行状态', sections: [buildStatsSection(instance)] }
    ];
    const buttons = new Map();
    const panes = new Map();
    const select = (id) => {
      for (const [key, button] of buttons) {
        const active = key === id;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
      }
      for (const [key, pane] of panes) pane.hidden = key !== id;
      state.settingsTab = id;
    };
    for (const category of categories) {
      const button = makeElement('button', 'settings-nav-item', category.label);
      button.type = 'button';
      button.setAttribute('role', 'tab');
      button.addEventListener('click', () => select(category.id));
      buttons.set(category.id, button);
      nav.appendChild(button);
      const pane = makeElement('section', 'settings-pane');
      pane.dataset.settingsPane = category.id;
      for (const section of category.sections) if (section) pane.appendChild(section);
      panes.set(category.id, pane);
      paneHost.appendChild(pane);
    }
    shell.appendChild(nav);
    shell.appendChild(paneHost);
    const initial = buttons.has(state.settingsTab) ? state.settingsTab : 'plugins';
    select(initial);
    return shell;
  }

  function buildGroupWhitelistSection(instance) {
    const section = makeElement('section', 'instance-v3-section group-whitelist-section');
    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-shield'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '群组白名单'));
    headingCopy.appendChild(makeElement('p', '', '控制 AstrBot 是否只处理白名单中的 QQ 群；私聊不受此项影响。'));
    heading.appendChild(headingCopy);
    section.appendChild(heading);

    const status = makeElement('p', 'instance-v3-status');
    status.setAttribute('role', 'status');
    section.appendChild(status);
    const form = makeElement('div', 'group-whitelist-form');
    const toggleLabel = makeElement('label', 'group-whitelist-toggle');
    const enabled = document.createElement('input');
    enabled.type = 'checkbox';
    enabled.setAttribute('aria-label', '启用群组白名单');
    const toggleCopy = makeElement('span', '', '启用后只有名单中的群允许机器人发言');
    toggleLabel.appendChild(enabled);
    toggleLabel.appendChild(toggleCopy);
    form.appendChild(toggleLabel);

    const idLabel = makeElement('label', 'group-whitelist-label', 'QQ 群号（每行一个）');
    const ids = document.createElement('textarea');
    ids.rows = 5;
    ids.placeholder = '例如：\n123456789\n987654321';
    ids.setAttribute('aria-label', 'QQ 群号列表');
    ids.className = 'group-whitelist-ids';
    idLabel.appendChild(ids);
    form.appendChild(idLabel);

    const actions = makeElement('div', 'group-whitelist-actions');
    const saveButton = makeElement('button', 'btn btn-secondary btn-small', '保存并重启生效');
    saveButton.type = 'button';
    actions.appendChild(saveButton);
    form.appendChild(actions);
    section.appendChild(form);

    let busy = false;
    const setStatus = (message, tone = 'info') => {
      status.textContent = message || '';
      status.className = `instance-v3-status${tone ? ` is-${tone}` : ''}`;
      status.hidden = !message;
    };
    const syncDisabled = () => {
      ids.disabled = busy || !enabled.checked;
      saveButton.disabled = busy;
    };
    enabled.addEventListener('change', syncDisabled);

    const load = async () => {
      busy = true;
      syncDisabled();
      setStatus('正在读取 AstrBot 群组白名单…', 'info');
      try {
        const data = asRecord(await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/group-whitelist`));
        if (data.ok === false) throw new ApiError(textValue(data.error, '读取群组白名单失败。'));
        enabled.checked = data.enabled === true;
        ids.value = Array.isArray(data.group_ids) ? data.group_ids.join('\n') : '';
        busy = false;
        syncDisabled();
        setStatus(data.enabled ? '白名单已启用。' : '白名单当前未启用。', data.enabled ? 'success' : 'info');
      } catch (error) {
        busy = false;
        syncDisabled();
        setStatus(formatError(error, '群组白名单读取失败。'), 'error');
      }
    };

    saveButton.addEventListener('click', async () => {
      if (busy) return;
      const groupIds = ids.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      busy = true;
      syncDisabled();
      setStatus('正在保存群组白名单…', 'info');
      try {
        const data = asRecord(await apiRequest(`/api/instances/${encodeURIComponent(instance.id)}/group-whitelist`, {
          method: 'POST',
          body: { enabled: enabled.checked, group_ids: groupIds }
        }));
        if (data.ok === false) throw new ApiError(textValue(data.error, '保存群组白名单失败。'));
        setStatus(textValue(data.message, '群组白名单已保存；重启 AstrBot 后生效。'), 'success');
      } catch (error) {
        setStatus(formatError(error, '群组白名单保存失败。'), 'error');
      } finally {
        busy = false;
        syncDisabled();
      }
    });

    void load();
    return section;
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

  async function restartAstrBotForSettings(instance, setStatus, button) {
    if (!instance || !instance.id) return;
    const original = button ? button.textContent : '';
    if (button) button.disabled = true;
    try {
      setStatus('正在重启 AstrBot…', 'info');
      const data = await apiRequest(instancePath('/api/action', instance.id), {
        method: 'POST',
        body: { action: 'restart' }
      });
      const message = textValue(data && data.message, '重启指令已发送；请等待 AstrBot 恢复后刷新。');
      setStatus(message, 'success');
    } catch (error) {
      setStatus(formatError(error, '重启 AstrBot 失败。'), 'error');
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = original;
      }
    }
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
        const extractInput = rowElement.querySelector('[data-field="extract"]');
        const injectInput = rowElement.querySelector('[data-field="inject"]');
        if (maxItems && String(maxItems.value || '').trim()) budget.push(`条数 ${maxItems.value}`);
        if (maxChars && String(maxChars.value || '').trim()) budget.push(`字符 ${maxChars.value}`);
        summaryTitle.textContent = groupId || '未填写群号';
        summaryMeta.textContent = [
          memoryPolicyModeLabel(policy, modeSelect.value),
          memoryPolicyOptionLabel(policy.scopeOptions, scopeSelect.value, scopeSelect.value),
          extractInput && extractInput.checked ? '抽取' : '不抽取',
          injectInput && injectInput.checked ? '注入' : '不注入',
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
      updateSummary();
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
    const saveButton = makeElement('button', 'btn btn-primary btn-small memory-policy-save', '保存记忆策略');
    saveButton.type = 'button';
    saveButton.dataset.memoryPolicy = 'save';
    saveButton.disabled = disabled;
    footer.appendChild(saveButton);
    const restartButton = makeElement('button', 'btn btn-secondary btn-small memory-policy-restart', '重启 AstrBot');
    restartButton.type = 'button';
    restartButton.dataset.memoryPolicy = 'restart';
    restartButton.disabled = disabled;
    restartButton.addEventListener('click', () => {
      void restartAstrBotForSettings(
        instance,
        (message, tone) => setV3Status(statusElement, message, tone),
        restartButton
      );
    });
    footer.appendChild(restartButton);
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


  // ------------------------------------------------------------------
  // Memory content manager (scope-scoped read/write through Studio API)
  // ------------------------------------------------------------------
  const MEMORY_CONTENT_KINDS = ['fact', 'preference', 'identity', 'profile', 'habit', 'note'];
  const MEMORY_CONTENT_SCOPES = [
    { value: 'group', label: '群聊 group' },
    { value: 'group_member', label: '群内发言人 group_member' },
    { value: 'user', label: '用户 user' },
    { value: 'conversation', label: '会话 conversation' },
    { value: 'persona', label: '人格 persona' },
    { value: 'global', label: '全局 global' }
  ];
  const MEMORY_CONTENT_PAGE_SIZE = 20;
  const memoryContentStates = new Map();

  function emptyMemoryContentState() {
    return {
      scopeType: 'group',
      scopeId: '',
      query: '',
      offset: 0,
      total: 0,
      records: [],
      stats: null,
      status: 'idle',
      statusText: '',
      statusTone: 'info',
      loading: false,
      section: null,
      selectedId: '',
      editing: null,
      busy: false,
      armedDeleteId: '',
      armedForget: false,
      armedImport: false,
      armedReflect: false,
      importPreview: null,
      importError: '',
      importName: '',
      importFileName: '',
      importDedup: 'skip',
      importRecords: null,
      available: true,
      editingError: '',
      reflectPreview: null,
      reflectError: ''
    };
  }

  function memoryContentState(instance) {
    const key = instance.id;
    if (!memoryContentStates.has(key)) memoryContentStates.set(key, emptyMemoryContentState());
    return memoryContentStates.get(key);
  }

  function memoryContentScopeReady(state) {
    return Boolean(state.scopeType && state.scopeId.trim());
  }

  function memoryContentPath(instance, tail) {
    return `/api/instances/${encodeURIComponent(instance.id)}/memory/${tail}`;
  }

  function memoryContentQuery(state, extra = '') {
    const params = new URLSearchParams();
    params.set('scope_type', state.scopeType);
    params.set('scope_id', state.scopeId.trim());
    if (extra) params.set('extra', extra);
    return params.toString();
  }

  function memoryContentErrorText(error, fallback) {
    const payload = error && error.data && typeof error.data === 'object' ? error.data : {};
    if (payload && payload.available === false) {
      return textValue(payload.message || payload.error, '记忆数据库当前不可用。');
    }
    return formatError(error, fallback);
  }

  function memoryContentMarkUnavailable(instance, error) {
    const payload = error && error.data && typeof error.data === 'object' ? error.data : {};
    if (payload && payload.available === false) {
      memoryContentState(instance).available = false;
      return true;
    }
    return false;
  }

  function setMemoryContentStatus(state, tone, message) {
    state.statusTone = tone || 'info';
    state.statusText = message || '';
    const element = state.section ? state.section.querySelector('[data-memory-content="status"]') : null;
    if (element) setV3Status(element, state.statusText, state.statusTone);
  }

  function memoryContentSetControlsDisabled(state, disabled) {
    if (!state.section) return;
    for (const element of $$('[data-memory-content-action]', state.section)) element.disabled = disabled;
    for (const element of $$('[data-memory-content-field]', state.section)) element.disabled = disabled;
  }

  function memoryContentKindText(kind) {
    const map = {
      fact: '事实',
      preference: '偏好',
      identity: '身份',
      profile: '资料',
      habit: '习惯',
      note: '备注'
    };
    return map[kind] || kind || '未知';
  }

  function memoryContentTimeText(record) {
    const updated = textValue(record.updated_at, '');
    if (!updated) return '时间未知';
    const short = updated.replace('T', ' ').replace(/\.\d+/, '').slice(0, 16);
    return short ? short : '时间未知';
  }

  function memoryContentExpiryText(record) {
    if (!record || !record.expires_at_epoch) return '永久';
    if (record.expired) return '已过期';
    const seconds = Number(record.expires_in_seconds);
    if (!Number.isFinite(seconds) || seconds <= 0) return '即将过期';
    if (seconds >= 86400) return `${Math.floor(seconds / 86400)} 天后过期`;
    if (seconds >= 3600) return `${Math.floor(seconds / 3600)} 小时后过期`;
    return `${Math.max(1, Math.floor(seconds / 60))} 分钟后过期`;
  }

  async function loadMemoryContentScope(instance) {
    const state = memoryContentState(instance);
    if (!memoryContentScopeReady(state)) {
      state.total = 0;
      state.records = [];
      state.stats = null;
      state.status = 'unsupported';
      renderMemoryContentState(instance);
      return;
    }
    state.loading = true;
    state.status = 'loading';
    state.statusTone = 'info';
    state.statusText = '正在读取记忆…';
    renderMemoryContentState(instance);
    try {
      const query = memoryContentQuery(state);
      const statsData = asRecord(await apiRequest(`${memoryContentPath(instance, 'stats')}?${query}`));
      const listQuery = `${query}&q=${encodeURIComponent(state.query)}&limit=${MEMORY_CONTENT_PAGE_SIZE}&offset=${state.offset}`;
      const listData = asRecord(await apiRequest(`${memoryContentPath(instance, 'records')}?${listQuery}`));
      state.available = true;
      state.stats = asRecord(statsData.stats);
      state.total = Number(listData.total) || 0;
      state.records = Array.isArray(listData.records) ? listData.records : [];
      state.status = state.total > 0 ? 'ok' : 'empty';
      state.statusText = state.total > 0 ? '' : (textValue(listData.message, '该 scope 还没有记忆。'));
      if (!state.records.some((item) => String(item.id) === String(state.selectedId))) {
        state.selectedId = '';
        state.editing = null;
      }
    } catch (error) {
      memoryContentMarkUnavailable(instance, error);
      state.status = 'error';
      state.statusTone = 'error';
      state.statusText = memoryContentErrorText(error, '读取记忆失败。');
      state.records = [];
      state.total = 0;
      state.stats = null;
    } finally {
      state.loading = false;
      renderMemoryContentState(instance);
    }
  }

  function memoryContentOffsetMax(state) {
    const total = Number(state.total) || 0;
    return Math.max(0, Math.ceil(total / MEMORY_CONTENT_PAGE_SIZE) * MEMORY_CONTENT_PAGE_SIZE);
  }

  async function memoryContentPage(instance, nextOffset) {
    const state = memoryContentState(instance);
    state.offset = Math.max(0, Math.min(nextOffset, memoryContentOffsetMax(state)));
    await loadMemoryContentScope(instance);
  }

  async function memoryContentSearch(instance) {
    const state = memoryContentState(instance);
    state.offset = 0;
    await loadMemoryContentScope(instance);
  }

  function memoryContentScopeHint(instance) {
    const policy = (instance.v3 && instance.v3.memoryPolicy) || {};
    const groups = Array.isArray(policy.groups) ? policy.groups : [];
    const ids = groups
      .map((row) => textValue(row && row.group_id, ''))
      .filter((value) => value);
    if (!ids.length) {
      return 'scope_id 形如 <平台>:<群号> 或 <平台>:<QQ号>（例如 aiocqhttp:838796241）。';
    }
    return `scope_id 形如 <平台>:<群号>；已配置的群号：${ids.slice(0, 6).join('、')}（平台名以 AstrBot 适配器为准）。`;
  }

  function memoryContentDatalist(instance) {
    const policy = (instance.v3 && instance.v3.memoryPolicy) || {};
    const groups = Array.isArray(policy.groups) ? policy.groups : [];
    const list = makeElement('datalist');
    list.id = 'memory-content-scope-ids';
    for (const row of groups) {
      const value = textValue(row && row.group_id, '');
      if (!value) continue;
      const option = makeElement('option');
      option.value = value;
      list.appendChild(option);
    }
    if (!list.childElementCount) {
      const option = makeElement('option');
      option.value = 'all';
      list.appendChild(option);
    }
    return list;
  }

  function memoryContentField(state, field, labelText, input) {
    const wrapper = makeElement('label', 'memory-content-field');
    wrapper.appendChild(makeElement('span', 'memory-content-field-label', labelText));
    wrapper.appendChild(input);
    input.dataset.memoryContentField = field;
    return wrapper;
  }

  function memoryContentSelect(state, field, options, value) {
    const select = makeElement('select', 'memory-content-input');
    for (const option of options) {
      const element = makeElement('option', '', option.label);
      element.value = option.value;
      select.appendChild(element);
    }
    select.value = value;
    select.dataset.memoryContentField = field;
    return select;
  }

  function renderMemoryContentRecords(instance, list) {
    const state = memoryContentState(instance);
    list.replaceChildren();
    if (state.status === 'loading') {
      list.appendChild(makeElement('p', 'memory-content-empty', '正在读取记忆…'));
      return;
    }
    if (state.status === 'error') {
      list.appendChild(makeElement('p', 'memory-content-empty is-error', state.statusText || '读取记忆失败。'));
      return;
    }
    if (!state.records.length) {
      const text = state.query
        ? `没有匹配“${state.query}”的记忆。`
        : (state.statusText || '该 scope 还没有记忆。');
      list.appendChild(makeElement('p', 'memory-content-empty', text));
      return;
    }
    for (const record of state.records) {
      const item = makeElement('article', 'memory-content-record');
      if (String(record.id) === String(state.selectedId)) item.classList.add('is-selected');
      const head = makeElement('div', 'memory-content-record-head');
      const badge = makeElement('span', 'memory-content-kind', memoryContentKindText(record.kind));
      head.appendChild(badge);
      head.appendChild(makeElement('span', 'memory-content-record-id', `#${record.id}`));
      head.appendChild(makeElement('span', 'memory-content-record-time', memoryContentTimeText(record)));
      if (record.expired) head.appendChild(makeElement('span', 'memory-content-badge is-warn', '已过期'));
      if (record.redacted) head.appendChild(makeElement('span', 'memory-content-badge', '含敏感词已隐去'));
      item.appendChild(head);
      item.appendChild(makeElement('p', 'memory-content-record-text', record.content));
      const meta = makeElement('p', 'memory-content-record-meta');
      meta.textContent = `置信 ${Number(record.confidence || 0).toFixed(2)} · 重要度 ${Number(record.importance || 0).toFixed(2)} · 命中 ${record.hits || 0} 次 · ${memoryContentExpiryText(record)}`;
      item.appendChild(meta);
      const actions = makeElement('div', 'memory-content-record-actions');
      const detailButton = makeElement('button', 'btn btn-ghost btn-small', '查看/编辑');
      detailButton.type = 'button';
      detailButton.dataset.memoryContentAction = `detail:${record.id}`;
      actions.appendChild(detailButton);
      const armed = String(state.armedDeleteId) === String(record.id);
      const deleteButton = makeElement(
        'button',
        armed ? 'btn btn-danger btn-small is-armed' : 'btn btn-danger btn-small',
        armed ? '再次点击确认删除' : '删除'
      );
      deleteButton.type = 'button';
      deleteButton.dataset.memoryContentAction = `delete:${record.id}`;
      actions.appendChild(deleteButton);
      item.appendChild(actions);
      if (String(state.selectedId) === String(record.id)) item.appendChild(renderMemoryContentDetail(instance));
      list.appendChild(item);
    }
  }

  function renderMemoryContentDetail(instance, options = {}) {
    const state = memoryContentState(instance);
    const isNew = options.isNew === true;
    const editor = makeElement('div', 'memory-content-editor');
    const record = state.editing || {};
    const content = document.createElement('textarea');
    content.className = 'memory-content-textarea';
    content.rows = 3;
    content.maxLength = 200;
    content.value = textValue(record.content, '');
    content.dataset.memoryContentField = 'edit-content';
    editor.appendChild(memoryContentField(state, 'edit-content', '记忆内容（最多 200 字符）', content));
    const kind = memoryContentSelect(
      state,
      'edit-kind',
      MEMORY_CONTENT_KINDS.map((item) => ({ value: item, label: memoryContentKindText(item) })),
      textValue(record.kind, 'fact')
    );
    editor.appendChild(memoryContentField(state, 'edit-kind', '类型', kind));
    const importance = document.createElement('input');
    importance.type = 'number';
    importance.min = '0';
    importance.max = '1';
    importance.step = '0.05';
    importance.value = String(Number(record.importance || 0.5));
    importance.dataset.memoryContentField = 'edit-importance';
    editor.appendChild(memoryContentField(state, 'edit-importance', '重要度 0-1', importance));
    const confidence = document.createElement('input');
    confidence.type = 'number';
    confidence.min = '0';
    confidence.max = '1';
    confidence.step = '0.05';
    confidence.value = String(Number(record.confidence || 0.5));
    confidence.dataset.memoryContentField = 'edit-confidence';
    editor.appendChild(memoryContentField(state, 'edit-confidence', '置信度 0-1', confidence));
    const ttl = document.createElement('input');
    ttl.type = 'number';
    ttl.min = '0';
    ttl.step = '60';
    ttl.placeholder = isNew ? '留空=永久' : '留空=不修改；0=永久';
    ttl.dataset.memoryContentField = 'edit-ttl';
    editor.appendChild(memoryContentField(state, 'edit-ttl', 'TTL（秒）', ttl));
    const hint = makeElement(
      'p',
      'memory-content-hint',
      '保存后由后端返回真实记录；Studio 不会显示未保存的“已保存”状态。'
    );
    editor.appendChild(hint);
    const actions = makeElement('div', 'memory-content-editor-actions');
    const saveLabel = isNew ? '新增记忆' : '保存修改';
    const save = makeElement('button', 'btn btn-primary btn-small', state.busy ? '保存中…' : saveLabel);
    save.type = 'button';
    save.dataset.memoryContentAction = isNew ? 'save-new' : 'save-edit';
    save.disabled = state.busy;
    actions.appendChild(save);
    const cancel = makeElement('button', 'btn btn-ghost btn-small', '取消');
    cancel.type = 'button';
    cancel.dataset.memoryContentAction = 'cancel-edit';
    actions.appendChild(cancel);
    editor.appendChild(actions);
    if (state.editingError) editor.appendChild(makeElement('p', 'memory-content-error', state.editingError));
    return editor;
  }

  function renderMemoryContentImportPreview(instance, container) {
    const state = memoryContentState(instance);
    container.replaceChildren();
    if (state.importError) {
      container.appendChild(makeElement('p', 'memory-content-error', state.importError));
    }
    const preview = state.importPreview;
    if (!preview) return;
    const summary = makeElement('div', 'memory-content-preview');
    const lines = [
      `文件：${state.importFileName || '未选择'}`,
      `共 ${preview.total} 条：可写入 ${preview.valid} 条，非法 ${(preview.invalid || []).length} 条`,
      `新增 ${preview.would_create} 条 · 更新 ${preview.would_update} 条 · 跳过 ${preview.would_skip} 条 · 冲突 ${(preview.conflicts || []).length} 处`,
      `去重策略：${preview.dedup === 'overwrite' ? '同键覆盖' : '同键跳过'}`
    ];
    if (preview.exceeds_scope_cap) lines.push(`导入后将超过 ${preview.cap} 条上限，需先清理旧记忆。`);
    for (const line of lines) summary.appendChild(makeElement('p', '', line));
    for (const item of (preview.invalid || []).slice(0, 8)) {
      summary.appendChild(makeElement('p', 'memory-content-error', `第 ${Number(item.index) + 1} 条非法：${textValue(item.reason, '')}`));
    }
    container.appendChild(summary);
    const armed = state.armedImport;
    const apply = makeElement(
      'button',
      armed ? 'btn btn-danger btn-small is-armed' : 'btn btn-primary btn-small',
      armed ? '再次点击确认写入' : '确认写入'
    );
    apply.type = 'button';
    apply.dataset.memoryContentAction = 'apply-import';
    apply.disabled = state.busy || state.available === false || !preview.valid || preview.exceeds_scope_cap;
    container.appendChild(apply);
  }

  function renderMemoryContentReflectPreview(instance, container) {
    const state = memoryContentState(instance);
    container.replaceChildren();
    if (state.reflectError) container.appendChild(makeElement('p', 'memory-content-error', state.reflectError));
    const preview = state.reflectPreview;
    if (!preview) return;
    const counts = asRecord(preview.counts);
    container.appendChild(makeElement(
      'p',
      'memory-content-hint',
      `共 ${Number(counts.records) || 0} 条：近重复 ${Number(counts.duplicates) || 0} 组 · 疑似矛盾 ${Number(counts.contradictions) || 0} 对 · 已过期 ${Number(counts.expired) || 0} 条 · 低价值 ${Number(counts.low_value) || 0} 条`
    ));
    for (const cluster of (preview.duplicates || []).slice(0, 5)) {
      container.appendChild(makeElement(
        'p',
        '',
        `重复组保留 #${cluster.keep_id}，清理 #${(cluster.ids || []).filter((id) => id !== cluster.keep_id).join('、#')}：${(cluster.contents || [])[0] || ''}`
      ));
    }
    for (const pair of (preview.contradictions || []).slice(0, 5)) {
      container.appendChild(makeElement(
        'p',
        'memory-content-warn',
        `疑似矛盾（不删除）：#${(pair.ids || []).join(' / #')}：${(pair.contents || []).join(' ↔ ')}`
      ));
    }
    container.appendChild(makeElement('p', 'memory-content-hint', 'apply 会先写 0600 备份，再清理重复/过期记录；矛盾只报告不删除。'));
    const armed = state.armedReflect;
    const apply = makeElement(
      'button',
      armed ? 'btn btn-danger btn-small is-armed' : 'btn btn-primary btn-small',
      armed ? '再次点击确认整理' : '应用整理'
    );
    apply.type = 'button';
    apply.dataset.memoryContentAction = 'apply-reflect';
    apply.disabled = state.busy || state.available === false
      || !((Number(counts.duplicates) || 0) + (Number(counts.expired) || 0));
    container.appendChild(apply);
  }

  function renderMemoryContentState(instance) {
    const state = memoryContentState(instance);
    const section = state.section;
    if (!section) return;
    memoryContentSetControlsDisabled(state, state.busy || state.available === false);
    section.classList.toggle('is-unavailable', state.available === false);
    const stats = asRecord(state.stats);
    const summary = section.querySelector('[data-memory-content="summary"]');
    if (summary) {
      summary.textContent = state.stats
        ? `当前 scope 共 ${Number(stats.total) || 0} 条记忆 · 命中 ${Number(stats.hits) || 0} 次 · 已过期 ${Number(stats.expired) || 0} 条 · 上限 ${Number(stats.cap) || 500}`
        : '选择 scope 后读取统计。';
    }
    setV3Status(section.querySelector('[data-memory-content="status"]'), state.statusText, state.statusTone);
    const records = section.querySelector('[data-memory-content="records"]');
    if (records) renderMemoryContentRecords(instance, records);
    const pager = section.querySelector('[data-memory-content="pager"]');
    if (pager) {
      const page = Math.floor(state.offset / MEMORY_CONTENT_PAGE_SIZE) + 1;
      const pages = Math.max(1, Math.ceil(state.total / MEMORY_CONTENT_PAGE_SIZE));
      pager.textContent = state.total ? `第 ${page} / ${pages} 页 · 共 ${state.total} 条` : '';
    }
    const controlsDisabled = state.busy || state.available === false;
    const prev = section.querySelector('[data-memory-content="prev"]');
    const next = section.querySelector('[data-memory-content="next"]');
    if (prev) prev.disabled = controlsDisabled || state.offset <= 0;
    if (next) next.disabled = controlsDisabled || state.offset + MEMORY_CONTENT_PAGE_SIZE >= state.total;
    renderMemoryContentImportPreview(instance, section.querySelector('[data-memory-content="import-preview"]'));
    renderMemoryContentReflectPreview(instance, section.querySelector('[data-memory-content="reflect-preview"]'));
    const forgetButton = section.querySelector('[data-memory-content-action="forget"]');
    if (forgetButton) {
      forgetButton.textContent = state.armedForget ? '再次点击确认忘记整个 scope' : '忘记整个 scope';
      forgetButton.classList.toggle('is-armed', state.armedForget);
      forgetButton.disabled = controlsDisabled || !memoryContentScopeReady(state) || !state.total;
    }
    // A failed read must stay retryable: "读取" is the recovery action.
    const retryButton = section.querySelector('[data-memory-content-action="load"]');
    if (retryButton) retryButton.disabled = state.busy;
    if (state.selectedId === 'new') {
      const recordsBox = section.querySelector('[data-memory-content="records"]');
      if (recordsBox) recordsBox.appendChild(renderMemoryContentDetail(instance, { isNew: true }));
    }
  }

  async function memoryContentSelectRecord(instance, recordId) {
    const state = memoryContentState(instance);
    state.selectedId = String(recordId);
    state.editing = null;
    state.editingError = '';
    renderMemoryContentState(instance);
    try {
      const query = memoryContentQuery(state);
      const data = asRecord(await apiRequest(`${memoryContentPath(instance, 'records')}/${encodeURIComponent(recordId)}?${query}`));
      state.editing = asRecord(data.record);
      state.editingError = '';
    } catch (error) {
      state.editingError = memoryContentErrorText(error, '读取记忆详情失败。');
    }
    renderMemoryContentState(instance);
  }

  function memoryContentEditorValue(instance, field) {
    const state = memoryContentState(instance);
    const section = state.section;
    if (!section) return '';
    const element = section.querySelector(`[data-memory-content-field="${field}"]`);
    return element ? String(element.value || '') : '';
  }

  async function memoryContentSaveEdit(instance) {
    const state = memoryContentState(instance);
    if (!state.selectedId || state.busy) return;
    const payload = {
      scope_type: state.scopeType,
      scope_id: state.scopeId.trim(),
      content: memoryContentEditorValue(instance, 'edit-content'),
      kind: memoryContentEditorValue(instance, 'edit-kind'),
      importance: memoryContentEditorValue(instance, 'edit-importance'),
      confidence: memoryContentEditorValue(instance, 'edit-confidence')
    };
    const ttl = memoryContentEditorValue(instance, 'edit-ttl').trim();
    if (ttl) {
      if (Number(ttl) <= 0) payload.expires_at = null;
      else payload.ttl_seconds = Number(ttl);
    }
    state.busy = true;
    state.editingError = '';
    renderMemoryContentState(instance);
    try {
      const data = asRecord(await apiRequest(
        `${memoryContentPath(instance, 'records')}/${encodeURIComponent(state.selectedId)}`,
        { method: 'PATCH', body: payload }
      ));
      state.editing = asRecord(data.record);
      state.status = 'ok';
      state.statusText = textValue(data.message, '已保存修改。');
      state.statusTone = 'success';
      state.armedDeleteId = '';
      state.busy = false;
      await loadMemoryContentScope(instance);
      return;
    } catch (error) {
      state.editingError = memoryContentErrorText(error, '保存修改失败。');
      state.status = 'error';
      state.statusText = state.editingError;
      state.statusTone = 'error';
    } finally {
      state.busy = false;
    }
    renderMemoryContentState(instance);
  }

  async function memoryContentDelete(instance, recordId) {
    const state = memoryContentState(instance);
    if (state.busy) return;
    if (String(state.armedDeleteId) !== String(recordId)) {
      state.armedDeleteId = String(recordId);
      state.armedForget = false;
      renderMemoryContentState(instance);
      return;
    }
    state.busy = true;
    state.status = 'loading';
    state.statusText = '正在删除…';
    renderMemoryContentState(instance);
    try {
      const data = asRecord(await apiRequest(
        `${memoryContentPath(instance, 'records')}/${encodeURIComponent(recordId)}`,
        {
          method: 'DELETE',
          body: {
            scope_type: state.scopeType,
            scope_id: state.scopeId.trim(),
            confirm: String(recordId)
          }
        }
      ));
      state.armedDeleteId = '';
      state.selectedId = '';
      state.editing = null;
      state.busy = false;
      await loadMemoryContentScope(instance);
      setMemoryContentStatus(state, 'success', textValue(data.message, '已删除 1 条记忆。'));
      renderMemoryContentState(instance);
      return;
    } catch (error) {
      state.status = 'error';
      state.statusText = memoryContentErrorText(error, '删除记忆失败。');
      state.statusTone = 'error';
    } finally {
      state.busy = false;
    }
    renderMemoryContentState(instance);
  }

  async function memoryContentForget(instance) {
    const state = memoryContentState(instance);
    if (state.busy || !memoryContentScopeReady(state)) return;
    if (!state.armedForget) {
      state.armedForget = true;
      state.armedDeleteId = '';
      renderMemoryContentState(instance);
      return;
    }
    state.busy = true;
    state.status = 'loading';
    state.statusText = '正在清理整个 scope…';
    renderMemoryContentState(instance);
    try {
      const data = asRecord(await apiRequest(memoryContentPath(instance, 'forget'), {
        method: 'POST',
        body: {
          scope_type: state.scopeType,
          scope_id: state.scopeId.trim(),
          confirm: state.scopeId.trim()
        }
      }));
      state.armedForget = false;
      state.busy = false;
      await loadMemoryContentScope(instance);
      setMemoryContentStatus(state, 'success', textValue(data.message, '已清理该 scope。'));
      renderMemoryContentState(instance);
      return;
    } catch (error) {
      state.status = 'error';
      state.statusText = memoryContentErrorText(error, '忘记 scope 失败。');
      state.statusTone = 'error';
    } finally {
      state.busy = false;
    }
    renderMemoryContentState(instance);
  }

  async function memoryContentExport(instance) {
    const state = memoryContentState(instance);
    if (state.busy || !memoryContentScopeReady(state)) return;
    state.busy = true;
    renderMemoryContentState(instance);
    try {
      const data = asRecord(await apiRequest(memoryContentPath(instance, 'export'), {
        method: 'POST',
        body: { scope_type: state.scopeType, scope_id: state.scopeId.trim() }
      }));
      const payload = JSON.stringify(data, null, 2);
      const blob = new Blob([payload], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `memory-${state.scopeType}-${state.scopeId.trim().replace(/[^A-Za-z0-9_.-]/g, '_')}.json`;
      link.rel = 'noopener';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMemoryContentStatus(state, 'success', `已导出 ${Number(data.count) || 0} 条记忆。`);
    } catch (error) {
      setMemoryContentStatus(state, 'error', memoryContentErrorText(error, '导出失败。'));
    } finally {
      state.busy = false;
      renderMemoryContentState(instance);
    }
  }

  async function memoryContentReadImportFile(instance, file) {
    const state = memoryContentState(instance);
    state.importError = '';
    state.importPreview = null;
    state.armedImport = false;
    if (!file) return;
    state.importFileName = file.name || '';
    let text = '';
    try {
      text = await file.text();
    } catch (error) {
      state.importError = '无法读取所选文件。';
      renderMemoryContentState(instance);
      return;
    }
    let parsed = null;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      state.importError = '文件不是合法 JSON。';
      renderMemoryContentState(instance);
      return;
    }
    let records = Array.isArray(parsed) ? parsed : asRecord(parsed).records;
    if (!Array.isArray(records)) {
      state.importError = 'JSON 中缺少 records 数组。';
      renderMemoryContentState(instance);
      return;
    }
    if (!memoryContentScopeReady(state)) {
      state.importError = '请先选择 scope。';
      renderMemoryContentState(instance);
      return;
    }
    await memoryContentPreviewImport(instance, records);
  }

  async function memoryContentPreviewImport(instance, records) {
    const state = memoryContentState(instance);
    state.busy = true;
    state.importError = '';
    renderMemoryContentState(instance);
    try {
      const data = asRecord(await apiRequest(memoryContentPath(instance, 'import'), {
        method: 'POST',
        body: {
          scope_type: state.scopeType,
          scope_id: state.scopeId.trim(),
          records,
          apply: false,
          dedup: state.importDedup === 'overwrite' ? 'overwrite' : 'skip',
          skip_invalid: true
        }
      }));
      state.importPreview = asRecord(data.preview);
      state.importRecords = records;
    } catch (error) {
      state.importError = memoryContentErrorText(error, '导入预览失败。');
      state.importPreview = null;
    } finally {
      state.busy = false;
      renderMemoryContentState(instance);
    }
  }

  async function memoryContentApplyImport(instance) {
    const state = memoryContentState(instance);
    if (state.busy || !Array.isArray(state.importRecords)) return;
    if (!state.armedImport) {
      state.armedImport = true;
      renderMemoryContentState(instance);
      return;
    }
    state.busy = true;
    renderMemoryContentState(instance);
    try {
      const data = asRecord(await apiRequest(memoryContentPath(instance, 'import'), {
        method: 'POST',
        body: {
          scope_type: state.scopeType,
          scope_id: state.scopeId.trim(),
          records: state.importRecords,
          apply: true,
          confirm: state.scopeId.trim(),
          dedup: state.importDedup === 'overwrite' ? 'overwrite' : 'skip',
          skip_invalid: true
        }
      }));
      state.armedImport = false;
      state.importPreview = null;
      state.importRecords = null;
      state.busy = false;
      await loadMemoryContentScope(instance);
      setMemoryContentStatus(state, 'success', textValue(data.message, '导入完成。'));
      renderMemoryContentState(instance);
      return;
    } catch (error) {
      state.importError = memoryContentErrorText(error, '写入导入数据失败（事务已回滚，未写入半份）。');
      state.status = 'error';
      state.statusText = state.importError;
      state.statusTone = 'error';
    } finally {
      state.busy = false;
    }
    renderMemoryContentState(instance);
  }

  async function memoryContentReflect(instance, apply) {
    const state = memoryContentState(instance);
    if (state.busy || !memoryContentScopeReady(state)) return;
    if (apply && !state.reflectPreview) return;
    if (apply && !state.armedReflect) {
      state.armedReflect = true;
      renderMemoryContentState(instance);
      return;
    }
    state.busy = true;
    state.reflectError = '';
    renderMemoryContentState(instance);
    try {
      const data = asRecord(await apiRequest(memoryContentPath(instance, 'reflect'), {
        method: 'POST',
        body: {
          scope_type: state.scopeType,
          scope_id: state.scopeId.trim(),
          apply: Boolean(apply),
          confirm: apply ? state.scopeId.trim() : ''
        }
      }));
      if (apply) {
        state.armedReflect = false;
        state.reflectPreview = null;
        state.busy = false;
        await loadMemoryContentScope(instance);
        setMemoryContentStatus(state, 'success', textValue(data.message, '整理完成。'));
        renderMemoryContentState(instance);
        return;
      }
      state.reflectPreview = asRecord(data.preview);
      state.armedReflect = false;
      state.statusTone = 'info';
    } catch (error) {
      state.reflectError = memoryContentErrorText(error, '整理预览失败。');
      state.status = 'error';
      state.statusText = state.reflectError;
      state.statusTone = 'error';
    } finally {
      state.busy = false;
    }
    renderMemoryContentState(instance);
  }

  function buildMemoryContentSection(instance) {
    const state = memoryContentState(instance);
    const section = makeElement('section', 'instance-v3-section memory-content-section');
    section.dataset.memoryContent = 'section';
    state.section = section;

    const heading = makeElement('div', 'instance-v3-section-heading');
    const icon = makeElement('span', 'instance-v3-section-icon');
    icon.appendChild(makeIcon('c-i-memory'));
    heading.appendChild(icon);
    const headingCopy = makeElement('div', 'instance-v3-section-copy');
    headingCopy.appendChild(makeElement('h4', '', '记忆内容'));
    headingCopy.appendChild(makeElement(
      'p',
      '',
      '按 scope 查看、编辑、导出/导入 AstrBot 长期记忆内容；每次操作都必须显式指定 scope，Studio 不会返回其他 scope 的数据。'
    ));
    heading.appendChild(headingCopy);
    section.appendChild(heading);

    const controls = makeElement('div', 'memory-content-controls');
    const scopeSelect = memoryContentSelect(
      state,
      'scope-type',
      MEMORY_CONTENT_SCOPES,
      state.scopeType
    );
    controls.appendChild(memoryContentField(state, 'scope-type', 'Scope 类型', scopeSelect));
    const scopeInput = document.createElement('input');
    scopeInput.type = 'text';
    scopeInput.className = 'memory-content-input';
    scopeInput.value = state.scopeId;
    scopeInput.placeholder = '群号或 aiocqhttp:群号；group_member 填 aiocqhttp:群号:QQ';
    scopeInput.setAttribute('list', 'memory-content-scope-ids');
    scopeInput.dataset.memoryContentField = 'scope-id';
    controls.appendChild(memoryContentField(state, 'scope-id', 'Scope ID', scopeInput));
    controls.appendChild(memoryContentDatalist(instance));
    const searchInput = document.createElement('input');
    searchInput.type = 'search';
    searchInput.className = 'memory-content-input';
    searchInput.value = state.query;
    searchInput.placeholder = '关键词（内容/键）';
    searchInput.dataset.memoryContentField = 'query';
    controls.appendChild(memoryContentField(state, 'query', '搜索', searchInput));
    const loadButton = makeElement('button', 'btn btn-primary btn-small', '读取');
    loadButton.type = 'button';
    loadButton.dataset.memoryContentAction = 'load';
    controls.appendChild(loadButton);
    const searchButton = makeElement('button', 'btn btn-ghost btn-small', '搜索');
    searchButton.type = 'button';
    searchButton.dataset.memoryContentAction = 'search';
    controls.appendChild(searchButton);
    section.appendChild(controls);

    section.appendChild(makeElement('p', 'memory-content-hint', memoryContentScopeHint(instance)));
    section.appendChild(makeElement('p', 'memory-content-summary memory-content-hint', ''))
      .dataset.memoryContent = 'summary';
    const status = makeElement('p', 'instance-v3-status');
    status.dataset.memoryContent = 'status';
    status.setAttribute('role', 'status');
    section.appendChild(status);
    const summary = section.querySelector('[data-memory-content="summary"]');
    if (summary) summary.dataset.memoryContent = 'summary';

    const list = makeElement('div', 'memory-content-records');
    list.dataset.memoryContent = 'records';
    section.appendChild(list);

    const pager = makeElement('div', 'memory-content-pager');
    pager.dataset.memoryContent = 'pager';
    section.appendChild(pager);
    const prev = makeElement('button', 'btn btn-ghost btn-small', '上一页');
    prev.type = 'button';
    prev.dataset.memoryContent = 'prev';
    const next = makeElement('button', 'btn btn-ghost btn-small', '下一页');
    next.type = 'button';
    next.dataset.memoryContent = 'next';
    section.appendChild(prev);
    section.appendChild(next);

    const toolbar = makeElement('div', 'memory-content-toolbar');
    const addButton = makeElement('button', 'btn btn-primary btn-small', '手动新增');
    addButton.type = 'button';
    addButton.dataset.memoryContentAction = 'add';
    toolbar.appendChild(addButton);
    const exportButton = makeElement('button', 'btn btn-ghost btn-small', '导出当前 scope');
    exportButton.type = 'button';
    exportButton.dataset.memoryContentAction = 'export';
    toolbar.appendChild(exportButton);
    const reflectButton = makeElement('button', 'btn btn-ghost btn-small', '整理预览');
    reflectButton.type = 'button';
    reflectButton.dataset.memoryContentAction = 'reflect';
    toolbar.appendChild(reflectButton);
    const forgetButton = makeElement('button', 'btn btn-danger btn-small', '忘记整个 scope');
    forgetButton.type = 'button';
    forgetButton.dataset.memoryContentAction = 'forget';
    toolbar.appendChild(forgetButton);
    section.appendChild(toolbar);

    const importBox = makeElement('div', 'memory-content-import');
    importBox.appendChild(makeElement('h5', '', '导入/恢复'));
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'application/json,.json';
    fileInput.dataset.memoryContentField = 'import-file';
    importBox.appendChild(fileInput);
    const dedupSelect = memoryContentSelect(
      state,
      'import-dedup',
      [
        { value: 'skip', label: '冲突时跳过' },
        { value: 'overwrite', label: '同键覆盖' }
      ],
      state.importDedup
    );
    importBox.appendChild(memoryContentField(state, 'import-dedup', '去重策略', dedupSelect));
    const importPreview = makeElement('div', 'memory-content-preview-box');
    importPreview.dataset.memoryContent = 'import-preview';
    importBox.appendChild(importPreview);
    section.appendChild(importBox);

    const reflectBox = makeElement('div', 'memory-content-reflect');
    reflectBox.appendChild(makeElement('h5', '', '整理预览（dry-run）'));
    const reflectPreview = makeElement('div', 'memory-content-preview-box');
    reflectPreview.dataset.memoryContent = 'reflect-preview';
    reflectBox.appendChild(reflectPreview);
    section.appendChild(reflectBox);

    controls.querySelector('[data-memory-content-action="load"]').addEventListener('click', () => {
      state.armedForget = false;
      state.armedDeleteId = '';
      state.query = String((searchInput.value || '')).trim();
      void loadMemoryContentScope(instance);
    });
    controls.querySelector('[data-memory-content-action="search"]').addEventListener('click', () => {
      state.query = String((searchInput.value || '')).trim();
      void memoryContentSearch(instance);
    });
    prev.addEventListener('click', () => void memoryContentPage(instance, state.offset - MEMORY_CONTENT_PAGE_SIZE));
    next.addEventListener('click', () => void memoryContentPage(instance, state.offset + MEMORY_CONTENT_PAGE_SIZE));
    scopeSelect.addEventListener('change', () => {
      state.scopeType = scopeSelect.value;
      state.offset = 0;
      state.selectedId = '';
      state.editing = null;
      state.importPreview = null;
      state.reflectPreview = null;
      state.armedForget = false;
      state.armedReflect = false;
      state.armedImport = false;
      renderMemoryContentState(instance);
    });
    scopeInput.addEventListener('input', () => {
      state.scopeId = String(scopeInput.value || '').trim();
      renderMemoryContentState(instance);
    });
    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        state.query = String(searchInput.value || '').trim();
        void memoryContentSearch(instance);
      }
    });
    addButton.addEventListener('click', () => {
      if (!memoryContentScopeReady(state)) {
        setMemoryContentStatus(state, 'warning', '请先填写 scope ID。');
        return;
      }
      state.selectedId = 'new';
      state.editing = { content: '', kind: 'fact', importance: 0.5, confidence: 0.5 };
      state.editingError = '';
      renderMemoryContentState(instance);
    });
    exportButton.addEventListener('click', () => void memoryContentExport(instance));
    reflectButton.addEventListener('click', () => void memoryContentReflect(instance, false));
    forgetButton.addEventListener('click', () => void memoryContentForget(instance));
    fileInput.addEventListener('change', () => {
      const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
      void memoryContentReadImportFile(instance, file);
    });
    dedupSelect.addEventListener('change', () => {
      state.importDedup = dedupSelect.value === 'overwrite' ? 'overwrite' : 'skip';
      if (Array.isArray(state.importRecords)) void memoryContentPreviewImport(instance, state.importRecords);
    });
    section.addEventListener('click', (event) => {
      const target = event.target;
      if (!target || !target.dataset) return;
      const action = target.dataset.memoryContentAction || '';
      if (!action) return;
      event.preventDefault();
      if (action === 'save-new') {
        void memoryContentCreate(instance);
        return;
      }
      if (action.startsWith('detail:')) {
        void memoryContentSelectRecord(instance, action.slice(7));
        return;
      }
      if (action.startsWith('delete:')) {
        void memoryContentDelete(instance, action.slice(7));
        return;
      }
      if (action === 'save-edit') {
        void memoryContentSaveEdit(instance);
        return;
      }
      if (action === 'cancel-edit') {
        state.selectedId = '';
        state.editing = null;
        state.editingError = '';
        renderMemoryContentState(instance);
        return;
      }
      if (action === 'apply-import') void memoryContentApplyImport(instance);
      if (action === 'apply-reflect') void memoryContentReflect(instance, true);
    });

    renderMemoryContentState(instance);
    if (memoryContentScopeReady(state)) void loadMemoryContentScope(instance);
    else setMemoryContentStatus(state, 'info', '填写 scope ID 后点击“读取”；Studio 不会跨 scope 列出记忆。');
    return section;
  }

  async function memoryContentCreate(instance) {
    const state = memoryContentState(instance);
    if (state.busy) return;
    const payload = {
      scope_type: state.scopeType,
      scope_id: state.scopeId.trim(),
      content: memoryContentEditorValue(instance, 'edit-content'),
      kind: memoryContentEditorValue(instance, 'edit-kind'),
      importance: memoryContentEditorValue(instance, 'edit-importance'),
      confidence: memoryContentEditorValue(instance, 'edit-confidence'),
      dedup: 'skip'
    };
    const ttl = memoryContentEditorValue(instance, 'edit-ttl').trim();
    if (ttl && Number(ttl) > 0) payload.ttl_seconds = Number(ttl);
    state.busy = true;
    state.editingError = '';
    renderMemoryContentState(instance);
    try {
      const data = asRecord(await apiRequest(memoryContentPath(instance, 'records'), {
        method: 'POST',
        body: payload
      }));
      state.busy = false;
      state.selectedId = '';
      state.editing = null;
      await loadMemoryContentScope(instance);
      setMemoryContentStatus(state, 'success', textValue(data.message, '已新增 1 条记忆。'));
      renderMemoryContentState(instance);
      return;
    } catch (error) {
      state.editingError = memoryContentErrorText(error, '新增记忆失败。');
      state.status = 'error';
      state.statusText = state.editingError;
      state.statusTone = 'error';
    } finally {
      state.busy = false;
    }
    renderMemoryContentState(instance);
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

  function renderInstances(options = {}) {
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
    if (options.refreshSettings !== false) instanceSettings.refresh();
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
              stats: { ...existing.stats, status: 'error', error: message },
              pluginGroups: { ok: false, groups: [], error: message }
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
      renderInstances({ refreshSettings: !silent });
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
        renderInstances({ refreshSettings: !silent });
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
