/* QQ Bot Studio — 多实例控制台
 * 纯原生 JavaScript，无第三方依赖。所有动态数据通过 textContent / DOM API 渲染。
 */
(() => {
  'use strict';

  const TOKEN_RAW = typeof window.QB_TOKEN === 'string' ? window.QB_TOKEN : '';
  const QB_TOKEN = TOKEN_RAW && TOKEN_RAW !== '{{TOKEN}}' ? TOKEN_RAW : '';

  class ApiError extends Error {
    constructor(message, options = {}) {
      super(message);
      this.name = 'ApiError';
      this.status = Number(options.status) || 0;
      this.data = options.data || null;
    }
  }

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
    optionalSupport: {
      plugins: null,
      memory: null,
      stats: null
    },
    expandedV3: new Set(),
    globalResourceStats: null,
    globalResourceStatus: 'unknown',
    globalResourceError: '',
    globalResourcePartial: false,
    assistantB: {
      status: 'idle',
      installed: false,
      hasInstalledField: false,
      path: '',
      repo: '',
      commit: '',
      version: '',
      entry: '',
      readme: '',
      message: '',
      error: '',
      actionError: '',
      loading: false,
      busy: false,
      armed: false,
      action: '',
      updatedAt: 0,
      configPanelOpen: false,
      openConfigRequested: false,
      config: {
        status: 'idle',
        loading: false,
        saving: false,
        configured: false,
        running: false,
        url: '',
        configPath: '',
        accessNote: '',
        sshTunnel: '',
        pid: '',
        logPath: '',
        pidPath: '',
        keyPresent: false,
        keySource: 'independent',
        instanceId: '',
        port: 8766,
        host: '127.0.0.1',
        message: '',
        error: '',
        updatedAt: 0
      }
    }
  };

  const $ = (id) => document.getElementById(id);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

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
    if (typeof value === 'string') {
      const normalised = value.trim().toLowerCase();
      if (['true', '1', 'yes', 'on', 'running', 'deployed', 'ok', 'enabled', 'active', 'available', 'connected'].includes(normalised)) return true;
      if (['false', '0', 'no', 'off', 'stopped', 'not_deployed', 'error', 'disabled', 'inactive', 'unavailable', 'disconnected'].includes(normalised)) return false;
    }
    return undefined;
  }

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

  async function apiRequest(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const headers = {
      Accept: 'application/json',
      // 本机服务的 GET 接口也校验令牌，因此所有请求都显式携带。
      'X-QB-Token': QB_TOKEN || '',
      ...(options.headers || {})
    };
    const fetchOptions = {
      method,
      headers,
      cache: 'no-store',
      credentials: 'same-origin'
    };
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      fetchOptions.body = JSON.stringify(options.body);
    }

    let response;
    try {
      response = await fetch(path, fetchOptions);
    } catch (error) {
      throw new ApiError('无法连接本地服务，请确认本地启动器仍在运行。', { status: 0 });
    }

    const rawText = await response.text();
    let data = {};
    if (rawText) {
      try {
        data = JSON.parse(rawText);
      } catch (error) {
        if (response.ok) throw new ApiError('本地服务返回了无法识别的数据。', { status: response.status });
      }
    }
    if (!response.ok) {
      const payload = asRecord(data);
      throw new ApiError(textValue(payload.message || payload.error, `本地服务返回 HTTP ${response.status}。`), {
        status: response.status,
        data
      });
    }
    return data;
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

  function safeHttpUrl(value) {
    const raw = textValue(value, '').trim();
    if (!raw) return '';
    try {
      const parsed = new URL(raw);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href;
    } catch (error) {
      return '';
    }
    return '';
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
    if (label) label.textContent = busy ? '刷新中…' : '刷新';
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

  function resetUpdateArm() {
    if (state.updateBusy) return;
    state.updateArmed = false;
    if (state.updateCanApply && state.updateInfo) {
      setUpdateLabel(`更新 ${state.updateInfo.behind || ''} 个提交`.trim());
    } else {
      setUpdateLabel('检查更新');
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
    } catch (error) {
      const message = formatError(error, '检查更新失败。');
      setUpdateLabel('检查更新');
      if (!silent) showToast(message, 'error', 6000);
    } finally {
      state.updateBusy = false;
      const button = $('console-update');
      if (button) button.disabled = false;
    }
  }

  async function handleUpdateClick() {
    if (state.updateBusy) return;
    if (!state.updateCanApply) {
      await checkForUpdate(false);
      return;
    }
    if (!state.updateArmed) {
      state.updateArmed = true;
      setUpdateLabel('再次点击更新整个项目并重启');
      window.setTimeout(() => {
        if (!state.updateBusy) resetUpdateArm();
      }, 6000);
      return;
    }
    state.updateArmed = false;
    state.updateBusy = true;
    setUpdateLabel('正在更新…', true);
    try {
      const data = asRecord(await apiRequest('/api/update/apply', { method: 'POST', body: {} }));
      if (data.ok === false) throw new ApiError(textValue(data.message, '自动更新失败。'));
      showToast(data.message || '更新任务已启动，管理服务即将重启。', 'success', 8000);
      setUpdateLabel('等待服务重启…', true);
      window.setTimeout(() => window.location.reload(), 12000);
    } catch (error) {
      const message = formatError(error, '自动更新失败。');
      showToast(message, 'error', 7000);
      setUpdateLabel('检查更新');
      state.updateBusy = false;
    }
  }

  function formatSyncTime(timestamp) {
    if (!timestamp) return '';
    try {
      return new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false });
    } catch (error) {
      return '';
    }
  }

  function scheduleNextSync(delay = 8000) {
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
    const status = `本地服务监听：${address}${publicHost ? ` · 公开地址：${publicHost}` : ''}${mirrorCount}${syncText}`;
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
    if (statusElement) statusElement.textContent = `本地设置读取失败：${formatError(error, '设置接口不可用。')}`;
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

  function isMissingEndpointError(error) {
    return error instanceof ApiError && (error.status === 404 || error.status === 405);
  }

  async function requestWithFallback(paths, options = {}) {
    let lastError = null;
    for (const path of paths) {
      try {
        return await apiRequest(path, options);
      } catch (error) {
        lastError = error;
        if (!isMissingEndpointError(error)) throw error;
      }
    }
    throw lastError || new ApiError('本地服务未提供该功能。', { status: 404 });
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
    const previousMemory = previous && previous.memory ? previous.memory : emptyMemoryState(instance.memory_mode_hint || '');
    if (state.optionalSupport.memory === false) {
      return {
        ...previousMemory,
        status: 'unsupported',
        error: '当前后端未提供记忆模式接口，记忆模式显示为未知。'
      };
    }
    try {
      const data = await requestWithFallback([
        `/api/instances/${encodeURIComponent(instance.id)}/memory`,
        instancePath('/api/memory', instance.id)
      ]);
      state.optionalSupport.memory = true;
      return normaliseMemoryPayload(data, previousMemory.mode || instance.memory_mode_hint || '');
    } catch (error) {
      if (isMissingEndpointError(error)) {
        state.optionalSupport.memory = false;
        return {
          ...previousMemory,
          status: 'unsupported',
          error: '当前后端未提供记忆模式接口，记忆模式显示为未知。'
        };
      }
      return {
        ...previousMemory,
        status: 'error',
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
    const [plugins, memory, stats] = await Promise.all([
      loadPluginsForInstance(instance),
      loadMemoryForInstance(instance),
      loadStatsForInstance(instance)
    ]);
    return { ...instance, v3: { plugins, memory, stats } };
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
    if (!card) return;
    const summary = card.querySelector('.instance-v3-summary');
    if (summary) summary.textContent = v3SummaryText(instance);
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

  async function handlePluginToggle(instance, plugin, checkbox, row, stateText, statusElement) {
    const previousEnabled = plugin.enabled === true;
    const targetEnabled = checkbox.checked;
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
    }
  }

  function buildMemorySection(instance) {
    const memory = (instance.v3 && instance.v3.memory) || emptyMemoryState(instance.memory_mode_hint || '');
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
    return section;
  }

  async function handleMemoryModeChange(instance, mode, group, statusElement) {
    const previousMode = normaliseMemoryMode(instance.v3 && instance.v3.memory && instance.v3.memory.mode);
    const radios = Array.from(group.querySelectorAll('input[type="radio"]'));
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

  function buildV3Panel(instance) {
    const details = document.createElement('details');
    details.className = 'instance-v3-panel';
    const summary = document.createElement('summary');
    summary.className = 'instance-v3-summary-row';
    const icon = makeElement('span', 'instance-v3-summary-icon');
    icon.appendChild(makeIcon('c-i-puzzle'));
    summary.appendChild(icon);
    summary.appendChild(makeElement('span', 'instance-v3-summary-title', '插件、记忆与资源'));
    summary.appendChild(makeElement('span', 'instance-v3-summary', v3SummaryText(instance)));
    const chevron = makeElement('span', 'instance-v3-chevron');
    chevron.appendChild(makeIcon('c-i-chevron-right'));
    summary.appendChild(chevron);
    details.appendChild(summary);

    const body = makeElement('div', 'instance-v3-body');
    body.appendChild(buildPluginSection(instance));
    body.appendChild(buildMemorySection(instance));
    body.appendChild(buildStatsSection(instance));
    details.appendChild(body);
    details.open = state.expandedV3.has(instance.id);
    details.addEventListener('toggle', () => {
      if (details.open) state.expandedV3.add(instance.id);
      else state.expandedV3.delete(instance.id);
    });
    return details;
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

    card.appendChild(buildV3Panel(instance));

    const actions = makeElement('div', 'instance-actions');
    const wizardLink = makeElement('a', 'btn btn-primary btn-small instance-wizard-link', '');
    wizardLink.href = wizardUrl(instance.id);
    wizardLink.appendChild(iconText('c-i-edit', '打开向导'));
    actions.appendChild(wizardLink);

    const controlGroup = makeElement('div', 'instance-control-group');
    controlGroup.appendChild(makeActionButton(instance, 'start'));
    controlGroup.appendChild(makeActionButton(instance, 'stop'));
    controlGroup.appendChild(makeActionButton(instance, 'restart'));
    const deleteButton = makeElement('button', 'instance-delete-btn', '删除实例');
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
  }

  async function loadConsole(options = {}) {
    if (state.loading) return false;
    const silent = options.silent === true;
    void loadAssistantBStatus({ silent });
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
            stats: emptyStatsState()
          };
          const message = formatError(error, '可选运维信息读取失败。');
          return {
            ...instance,
            v3: {
              plugins: { ...existing.plugins, status: 'error', error: message },
              memory: { ...existing.memory, status: 'error', error: message },
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
      if (state.assistantB.configPanelOpen) populateAssistantBInstanceOptions();
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

  const ASSISTANT_KB = [
    {
      id: 'install',
      title: '安装、更新与依赖检查',
      keywords: ['安装', 'install', 'install.sh', '首次启动', '首次运行', '依赖', '检查依赖', 'python', 'screen', 'docker安装', 'docker 安装', '安装失败'],
      answer: {
        title: '安装、更新与依赖检查',
        intro: '推荐在 Ubuntu 22.04 / 24.04 上使用。安装器会检查 Python 3.10+、Docker、Compose、screen 等依赖；依赖没准备好时，不要反复点击部署。',
        sections: [
          {
            title: '首次安装',
            paragraphs: ['进入项目根目录后运行 install.sh。首次会询问管理服务监听 IP、端口和对外地址，默认是 0.0.0.0:8765，并自动调用 start.sh 创建第一个实例。'],
            commands: ['cd "/你的项目路径/QQ Ai bot"\n./install.sh']
          },
          {
            title: '安装后检查依赖',
            paragraphs: ['如果页面打不开、Docker 报错或部署提示缺少组件，先在终端逐项检查：'],
            bullets: [
              'docker info 必须成功，否则 Docker daemon 没有运行或当前用户没有权限。',
              'docker compose version 必须能输出版本号。',
              '权限不足时按启动器提示临时缓存 sudo，或注销后重新登录使 docker 组生效。'
            ],
            commands: ['python3 --version\ndocker --version\ndocker compose version\ndocker info\nscreen --version']
          },
          {
            title: '更新已有安装',
            paragraphs: [
              '优先使用控制台顶部的“检查更新 / 更新并重启”。它要求 Git 工作区干净、只接受 fast-forward，并且部署任务运行时禁止更新。',
              '如果自动更新失败，请使用快捷问题“自动更新失败”，或查看 README.md 的自动更新章节和 ~/.local/share/qqbot-studio/auto-update.log。'
            ]
          }
        ],
        references: ['README.md「1. 三个脚本」「13. 常见问题」', 'docs/server-troubleshooting.md「2–6. Docker 安装与失败排查」']
      }
    },
    {
      id: 'update',
      title: '自动更新失败排查',
      keywords: ['自动更新', '更新', '检查更新', '更新失败', '更新并重启', 'git', 'git pull', 'ff-only', '工作区', 'github', 'origin', '更新日志', '版本'],
      answer: {
        title: '自动更新失败排查',
        intro: '自动更新只应通过控制台“检查更新 / 更新并重启”执行。管理服务会先 git fetch，只接受干净的 Git 工作区和 fast-forward 更新；有部署任务运行时会拒绝更新。',
        sections: [
          {
            title: '先看管理服务更新日志',
            paragraphs: ['更新失败的真实原因通常会写入 auto-update.log。先确认工作区、远程地址和日志：'],
            commands: [
              'cd "/你的项目路径/QQ Ai bot"\ngit status --short\ngit remote -v\ngit fetch origin\ngit branch -vv\ntail -n 200 ~/.local/share/qqbot-studio/auto-update.log'
            ]
          },
          {
            title: '常见原因',
            bullets: [
              'git status --short 有输出：说明本地有未提交修改。先备份、提交或 stash，再重试；不要直接 git reset --hard 覆盖未知修改。',
              '无法连接 GitHub/origin：检查服务器网络、代理或远程仓库地址，恢复网络后再点检查更新。',
              '提示部署任务正在运行：等任务完成或失败后再更新。管理服务重启会让正在执行的任务中断。',
              '更新按钮要求二次确认：第一次点击只进入确认状态，第二次点击才执行更新并重启。'
            ]
          },
          {
            title: '更新后仍异常',
            paragraphs: ['更新不会删除实例数据目录。若管理服务没有自动恢复，查看 studio-server.log；已有实例可在控制台逐个启动并检查日志。'],
            commands: ['tail -n 200 ~/.local/share/qqbot-studio/studio-server.log']
          }
        ],
        references: ['README.md「1. 三个脚本 / 自动更新」「11. screen 使用教程」', 'docs/server-troubleshooting.md「2–5. Docker 排错」「9. screen 后台运行」']
      }
    },
    {
      id: 'tencent-cloud',
      title: '腾讯云安全组、防火墙与端口',
      keywords: ['腾讯云', '安全组', '防火墙', '端口', '8765', 'ufw', '公网', '外网', '访问不了', '入站规则', '轻量应用服务器', 'cvm', 'ssh隧道', '监听', '打不开'],
      answer: {
        title: '腾讯云安全组、防火墙与端口',
        intro: '公网访问要同时满足三件事：云安全组/轻量防火墙放行、服务器本机防火墙放行、程序监听在可访问地址。只开其中一层，页面仍然打不开。',
        sections: [
          {
            title: '需要放行哪些端口',
            bullets: [
              '管理控制台默认使用 TCP 8765；以控制台顶部显示的实际地址为准。',
              '每个实例会分配三个相邻的宿主机端口（AstrBot、SnowLuma、noVNC），所有宿主机服务端口限制在 5000-10000，并且实例之间不重叠；请以控制台实例卡片链接为准。',
              '删除实例请在对应卡片点击“删除实例”，第一次点击确认，第二次点击才会真正删除容器和实例数据。',
              '腾讯云 CVM 在“安全组 → 入站规则”放行；轻量应用服务器在“防火墙”放行。协议选 TCP，来源尽量限制为你自己的公网 IP。',
              '不要把 5900 或 noVNC 远程桌面直接暴露到公网；优先用安全组限制来源、VPN 或 SSH 隧道。'
            ]
          },
          {
            title: '启动参数与 UFW 示例',
            paragraphs: ['如果确实需要从公网访问管理控制台，可显式绑定并配置 public-host；然后检查主机防火墙：'],
            commands: [
              './console.sh --ip 0.0.0.0 --port 8765 --public-host <你的公网IP或域名>\nsudo ufw allow from <你的公网IP> to any port 8765 proto tcp\nsudo ufw status numbered'
            ]
          },
          {
            title: '更安全的本机访问方式',
            paragraphs: ['只在本机浏览器使用时，让管理服务只监听回环地址，再通过 SSH 隧道访问，不需要向公网开放 8765：'],
            commands: [
              './start.sh --ip 127.0.0.1 --port 8765\nssh -N -L 8765:127.0.0.1:8765 用户名@服务器IP'
            ],
            bullets: ['然后在本机浏览器打开 http://127.0.0.1:8765/console。', '实例 WebUI/noVNC 也可以使用类似的 SSH 端口转发。']
          }
        ],
        references: ['README.md「2. 首次启动：IP、端口和参数」「3. 默认 0.0.0.0 的安全风险」', 'docs/server-troubleshooting.md「8. 腾讯云开放端口 / 防火墙教程」']
      }
    },
    {
      id: 'scripts',
      title: 'start.sh 与 console.sh 的区别',
      keywords: ['start.sh', 'console.sh', 'install.sh', '区别', '脚本', '管理控制台', '实例', '新建实例', '控制台', '新建机器人'],
      answer: {
        title: 'start.sh 与 console.sh 的区别',
        intro: '三个脚本用途不同：install.sh 负责安装依赖和首次配置，start.sh 每次创建一个新实例，console.sh 打开已有实例的管理控制台。',
        sections: [
          {
            title: '命令速查',
            commands: ['cd "/你的项目路径/QQ Ai bot"\n./install.sh    # 首次安装依赖、确认 IP/端口，并创建第一个实例\n./start.sh      # 新建一个全新的机器人实例\n./console.sh    # 打开或启动多实例管理控制台']
          },
          {
            title: '关键区别',
            bullets: [
              'start.sh 会新建实例，并且是完全隔离的：拥有独立 QQ 号、数据目录、容器、端口和 SnowLuma 登录态；重复执行会创建下一个实例，每个新实例首次都要扫码。',
              'console.sh 不会创建实例，只打开/启动管理控制台，用来查看、启动、停止、重启、打开向导和维护已有实例。',
              'install.sh 是安装入口，只在首次安装或修复依赖时使用；成功后它会自动调用 start.sh。',
              '管理已有机器人请用 console.sh，不要为了“重启”而反复运行 start.sh。'
            ]
          }
        ],
        references: ['README.md「1. 三个脚本」「12. 常用命令」', 'docs/server-troubleshooting.md「脚本与实例管理」']
      }
    },
    {
      id: 'qq-login',
      title: '首次扫码与重启后的登录态',
      keywords: ['扫码', '二维码', '首次', '第一次', '登录态', '重启', 'noVNC', '验证', '设备验证', '短信验证', '滑块', '登录', '新实例', '退出', '过期'],
      answer: {
        title: '首次扫码与重启后的登录态',
        intro: '每个新实例第一次部署都必须在自己的 noVNC 里用手机 QQ 扫码登录。不同实例的数据和登录态互不共享。',
        sections: [
          {
            title: '新实例与重启',
            bullets: [
              '新实例：打开该实例的 noVNC，在 QQ 官方界面选择扫码登录，用手机 QQ 确认。',
              '重启已有容器：通常会复用 SnowLuma 已保存的登录态，一般不必重新扫码。',
              'QQ 会话过期、退出登录、切换网络或触发安全验证时，仍需回到 noVNC 重新完成官方登录。',
              '不要填写或保存 QQ 密码。如果出现设备验证、短信验证、滑块或安全验证，必须在 noVNC 的 QQ 官方页面按提示完成；本工具不能绕过验证。'
            ]
          },
          {
            title: '扫码窗口打不开或没有二维码',
            paragraphs: ['先等待 30–60 秒并刷新 noVNC，再检查 SnowLuma 容器和日志：'],
            commands: [
              "docker ps --format '{{.Names}}\\t{{.Status}}'\ndocker logs --tail 200 <snowluma容器名> 2>&1 | tail -n 80"
            ],
            bullets: ['确认容器正在运行且没有反复重启。', '多实例请确认打开的是当前实例的 noVNC 链接，而不是另一个实例。']
          }
        ],
        references: ['README.md「1. 三个脚本」「7. QQ 登录与验证」', 'docs/server-troubleshooting.md「10. 最终验证清单」']
      }
    },
    {
      id: 'snowluma-password',
      title: 'SnowLuma 密码错误与重置',
      keywords: ['snowluma', '密码', 'admin', '重置', '登录不上', '临时密码', 'webui', '用户名', '密码错误', '忘记密码', '初始密码'],
      answer: {
        title: 'SnowLuma 密码错误与重置',
        intro: 'SnowLuma WebUI 的用户名始终是 admin。密码可能是一次性初始密码，也可能已被你修改；完成页不会自动知道你后来改过的密码。',
        sections: [
          {
            title: '先试这些地方',
            bullets: [
              '打开该实例的部署向导完成页，查看 SnowLuma 卡片的当前密码。',
              '如果你曾在 SnowLuma WebUI 首次登录后修改密码，请使用你修改后的密码。',
              '当前版本完成页的 SnowLuma 卡片提供“重置密码”：第一次点击进入确认状态，第二次点击执行重置；成功后显示新密码。'
            ]
          },
          {
            title: '手动重置 WebUI 登录密码',
            paragraphs: [
              '先确认 SnowLuma 容器名并查看初始凭据日志；如果找不到旧密码，再备份并删除该实例的 data/snowluma/config/webui.json，然后重启 SnowLuma 容器。',
              '删除 webui.json 只重置 SnowLuma WebUI 登录，不会删除 QQ 数据目录；QQ 登录态是否保留取决于 SnowLuma 版本，必要时仍需在 noVNC 重新验证。'
            ],
            commands: [
              "docker ps --format '{{.Names}}' | grep snowluma\ndocker logs --tail 200 <snowluma容器名> 2>&1 | grep -i -A3 -B3 \"initial credentials\"\nfind ~/.local/share/qqbot-studio/instances -path '*/data/snowluma/config/webui.json' -print\ncp <webui.json路径> <webui.json路径>.bak\nrm <webui.json路径>\ndocker restart <snowluma容器名>\ndocker logs --tail 200 <snowluma容器名> 2>&1 | grep -i -A3 -B3 \"initial credentials\""
            ]
          }
        ],
        references: ['README.md「13. 常见问题：SnowLuma WebUI 登录不上 / 密码错误」']
      }
    },
    {
      id: 'plugin-memory-token',
      title: '概率回复、冷却、memory_mode 与 token',
      keywords: ['reply_probability', '概率', '冷却', 'cooldown', 'memory_mode', '长期记忆', '记忆', 'token', 'tokens', '成本', '省token', '插件', '私聊', '群聊', '上下文', 'context caching', '图片'],
      answer: {
        title: '概率回复、冷却、memory_mode 与 token',
        intro: '这些配置在 AstrBot WebUI → 插件 → QQ Bot 增强回复中调整。@ 和私聊是必回，不受概率与冷却限制；非 @ 群消息才按 reply_probability 命中。',
        sections: [
          {
            title: '推荐起步配置',
            bullets: [
              'reply_probability=0.12 表示非 @ 群消息约 12% 概率进入回复流程；设为 0.05 更安静，设为 0 就只回复 @ 和私聊。',
              'group_cooldown_seconds 和 user_cooldown_seconds 分别限制同群、同用户的概率回复频率。',
              'min_message_chars 过滤过短消息；ignore_pure_image=true 时，非 @ 纯图片不参与概率回复。',
              '保存后一般立即生效；不生效时重启对应 AstrBot 容器。'
            ],
            commands: ['reply_probability = 0.12\ngroup_cooldown_seconds = 30\nuser_cooldown_seconds = 90\nmemory_mode = direct']
          },
          {
            title: 'memory_mode 怎么选',
            bullets: [
              'direct：只带 system prompt 和当前消息，不保存跨轮历史，最省 token，适合群聊和简单问答。',
              'astrbot：交给 AstrBot 默认 Agent，使用会话管理器和持久化历史，能记住之前对话，但历史越长 token 越多。',
              'astrbot 模式可用 /reset 清空当前会话、/new 开启新会话。'
            ]
          },
          {
            title: '控制 token 成本',
            bullets: [
              '降低 reply_probability、增大群/用户冷却、提高 min_message_chars，能直接减少模型调用次数。',
              '保持系统提示词简洁稳定，不要把时间、随机数等动态内容写入 system prompt，以利于 DeepSeek Context Caching。',
              '图片 token 通常更贵；可减少 max_images_per_message，并保持 ignore_pure_image=true。',
              '本地表情包不会额外调用 LLM，sticker_probability 不增加模型 token。'
            ]
          }
        ],
        references: ['README.md「9. QQ 增强插件」', 'docs/plugin-qqbot-enhancer.md「概率、冷却、记忆模式和 Token」']
      }
    },
    {
      id: 'deploy-docker-logs',
      title: '部署中断、日志与 Docker 检查',
      keywords: ['部署中断', '中断', '部署失败', '失败', '卡住', '继续', '重试部署', '日志', '查看日志', 'docker', 'docker检查', '容器', 'compose', '状态', '恢复', '任务', '后台服务'],
      answer: {
        title: '部署中断、日志与 Docker 检查',
        intro: '先判断是部署任务、管理服务还是 Docker 本身的问题。不要连续重复提交部署；先查看任务状态和相关日志。',
        sections: [
          {
            title: '部署任务中断',
            bullets: [
              '控制台实例卡片会显示活动 job 和“查看进度”，点击可回到该实例的部署页。',
              '如果任务仍在运行，等待或继续获取进度，不要重复部署。',
              '如果显示“部署任务已中断”，可先点击“继续获取进度”检查原任务；确认任务丢失后点“重试部署”返回配置页重新提交。',
              '管理服务重启或更新会中断正在运行的部署任务；重新部署前确认 Docker 与网络正常。'
            ]
          },
          {
            title: '查看日志',
            paragraphs: ['优先在实例完成页/向导中查看 AstrBot 与 SnowLuma 服务日志；也可以在终端查看管理服务和容器日志：'],
            commands: [
              'tail -n 200 ~/.local/share/qqbot-studio/studio-server.log\ndocker ps --format "{{.Names}}\\t{{.Status}}"\ndocker logs --tail 200 <容器名> 2>&1'
            ]
          },
          {
            title: 'Docker 检查',
            paragraphs: ['以下命令全部正常，才说明 Docker/Compose/权限可用：'],
            commands: [
              'docker --version\ndocker compose version\ndocker info\ndocker ps --format "{{.Names}}\\t{{.Status}}"'
            ],
            bullets: [
              'docker info 失败：检查 docker 服务、当前用户权限或启动器缓存 sudo 的步骤。',
              'docker compose 不存在：安装 docker-compose-v2 后重新运行 ./install.sh 或 ./start.sh。',
              '容器反复重启：查看 docker logs 和 docs/server-troubleshooting.md 的 Docker/容器排错章节。'
            ]
          }
        ],
        references: ['README.md「5. 下载日志、部署恢复与鲁棒性」「6. Docker 检查与容器修复」「13. 常见问题」', 'docs/server-troubleshooting.md「2–6. Docker 排错」「9. screen 后台运行」']
      }
    },
    {
      id: 'windows',
      title: 'Windows 版说明',
      keywords: ['windows', 'win10', 'win11', '微软', 'windows版', 'windows 版', '电脑版', 'pc版', '支持吗', '后续'],
      answer: {
        title: 'Windows 版说明',
        intro: '当前主要验证目标是 Ubuntu 22.04 / 24.04 + Docker Compose。Windows 原生或 WSL2 + Docker Desktop 仍属于初始移植/后续方向，尚未在本项目环境完成真实 Windows 10/11 + Docker Desktop 联调。',
        sections: [
          {
            title: '现在能期待什么',
            bullets: [
              '不要将 Windows 版当作与 Ubuntu 版同等验证程度的一键生产方案。',
              '如果仓库提供了 windows/ 目录，请先阅读其中的 README 和未验证项说明。',
              'WSL2 + Docker Desktop 可以复用部分 Linux 容器思路，但会受端口转发、路径、文件权限、Docker Desktop 网络和安全组/系统防火墙影响。',
              '每个新实例首次仍需 noVNC 扫码；设备/短信/滑块验证必须在 QQ 官方界面完成。'
            ]
          },
          {
            title: '遇到问题怎么办',
            paragraphs: ['先在 README.md 查看当前支持范围，并查看 docs/server-troubleshooting.md 的端口、Docker 和登录排错章节。不要根据未验证的教程直接暴露管理端口到公网。']
          }
        ],
        references: ['README.md（支持范围与限制）', 'windows/README.md（如仓库已提供；当前仍未完成真实 Windows 联调）']
      }
    }
  ];

  const ASSISTANT_FALLBACK = {
    title: '没有匹配到内置答案',
    intro: '这是离线关键词助手，只能回答本机内置的安装、更新、端口、扫码、SnowLuma、插件、Docker、日志和 Windows 说明。你的问题没有匹配到明确主题。',
    sections: [
      {
        title: '建议下一步',
        bullets: [
          '换一种问法，并包含主题关键词，例如“腾讯云 端口”“SnowLuma 密码”“reply_probability”“部署中断日志”“Windows”。',
          '打开项目根目录 README.md，查看“常见问题”“常用命令”“QQ 增强插件”“自动更新与后台服务”。',
          '打开 docs/server-troubleshooting.md，查看排错索引、腾讯云开放端口/防火墙教程、Docker/容器排错和 QQ 登录排错。',
          '如果控制台显示错误，先点击刷新并查看对应实例卡片；不要把密码、API Key、验证码或令牌输入助手。'
        ]
      }
    ],
    references: ['README.md「常见问题 / 常用命令 / 排错」', 'docs/server-troubleshooting.md「排错索引」']
  };
  const ASSISTANT_FALLBACK_GUIDE = '未匹配时请查看项目根目录 README.md 的常见问题与排错索引，以及 docs/server-troubleshooting.md。';

  let assistantPanelOpen = false;
  let assistantReturnFocus = null;

  function normaliseAssistantText(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[，。！？、；：,!?;:()（）\[\]【】"'“”‘’]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function assistantEntryScore(entry, question) {
    const normalizedQuestion = normaliseAssistantText(question);
    if (!normalizedQuestion) return 0;
    let score = 0;
    for (const keyword of entry.keywords || []) {
      const term = normaliseAssistantText(keyword);
      if (!term) continue;
      if (normalizedQuestion === term) {
        score += 12;
      } else if (normalizedQuestion.includes(term)) {
        score += Math.min(8, Math.max(2, term.length));
      }
    }
    return score;
  }

  function findAssistantAnswer(question) {
    const normalizedQuestion = normaliseAssistantText(question);
    let bestEntry = null;
    let bestScore = 0;
    for (const entry of ASSISTANT_KB) {
      const score = assistantEntryScore(entry, normalizedQuestion);
      if (score > bestScore) {
        bestScore = score;
        bestEntry = entry;
      }
    }
    if (bestEntry && bestScore > 0) {
      return { answer: bestEntry.answer, matchedTitle: bestEntry.title, score: bestScore };
    }
    return { answer: ASSISTANT_FALLBACK, matchedTitle: '兜底建议', score: 0 };
  }

  function scrollAssistantMessages() {
    const messages = $('assistant-messages');
    if (!messages) return;
    messages.scrollTop = messages.scrollHeight || 0;
  }

  function setAssistantError(message) {
    const input = $('assistant-input');
    const error = $('assistant-error');
    if (input) {
      if (message) input.setAttribute('aria-invalid', 'true');
      else input.removeAttribute('aria-invalid');
    }
    if (error) {
      error.textContent = message || '';
      error.hidden = !message;
    }
  }

  function appendAssistantTextBlock(parent, tag, className, text) {
    if (!text) return null;
    const element = makeElement(tag, className, text);
    parent.appendChild(element);
    return element;
  }

  function renderAssistantAnswer(answer, bubble) {
    const title = makeElement('div', 'assistant-answer-title');
    title.appendChild(makeIcon('c-i-help'));
    title.appendChild(document.createTextNode(answer.title || '离线教程答案'));
    bubble.appendChild(title);

    appendAssistantTextBlock(bubble, 'p', '', answer.intro);

    for (const section of Array.isArray(answer.sections) ? answer.sections : []) {
      const sectionElement = makeElement('div', 'assistant-section');
      appendAssistantTextBlock(sectionElement, 'h4', 'assistant-section-title', section.title);
      for (const paragraph of Array.isArray(section.paragraphs) ? section.paragraphs : []) {
        appendAssistantTextBlock(sectionElement, 'p', '', paragraph);
      }
      if (Array.isArray(section.bullets) && section.bullets.length > 0) {
        const list = makeElement('ul');
        for (const bullet of section.bullets) {
          appendAssistantTextBlock(list, 'li', '', bullet);
        }
        sectionElement.appendChild(list);
      }
      for (const command of Array.isArray(section.commands) ? section.commands : []) {
        const block = makeElement('pre', 'assistant-code-block');
        const code = makeElement('code', '', command);
        block.appendChild(code);
        sectionElement.appendChild(block);
      }
      bubble.appendChild(sectionElement);
    }

    if (Array.isArray(answer.references) && answer.references.length > 0) {
      const reference = makeElement('div', 'assistant-reference');
      reference.appendChild(makeIcon('c-i-book'));
      reference.appendChild(document.createTextNode(`参考资料：${answer.references.join(' · ')}`));
      bubble.appendChild(reference);
    }
  }

  function appendAssistantMessage(role, payload) {
    const messages = $('assistant-messages');
    if (!messages) return;
    const row = makeElement('div', `assistant-message assistant-message--${role}`);
    if (role === 'bot') {
      const avatar = makeElement('span', 'assistant-avatar');
      avatar.setAttribute('aria-hidden', 'true');
      avatar.appendChild(makeIcon('c-i-help'));
      row.appendChild(avatar);
    }
    const bubble = makeElement('div', 'assistant-bubble');
    if (role === 'user') {
      bubble.appendChild(makeElement('p', '', payload));
    } else {
      renderAssistantAnswer(payload, bubble);
    }
    row.appendChild(bubble);
    messages.appendChild(row);
    scrollAssistantMessages();
  }

  function askAssistant(rawQuestion) {
    const question = textValue(rawQuestion, '').trim();
    setAssistantError('');
    if (!question) {
      setAssistantError('请输入一个问题，或点击下面的快捷问题。');
      const input = $('assistant-input');
      if (input) input.focus();
      return false;
    }
    if (question.length > 500) {
      setAssistantError('问题不能超过 500 个字符。');
      const input = $('assistant-input');
      if (input) input.focus();
      return false;
    }

    appendAssistantMessage('user', question);
    const result = findAssistantAnswer(question);
    appendAssistantMessage('bot', result.answer);
    const input = $('assistant-input');
    if (input) {
      input.value = '';
      input.focus({ preventScroll: true });
    }
    return true;
  }

  function openAssistant(options = {}) {
    const panel = $('assistant-panel');
    const openButton = $('assistant-open');
    if (!panel) return;
    if (!assistantPanelOpen) assistantReturnFocus = document.activeElement;
    assistantPanelOpen = true;
    panel.hidden = false;
    if (openButton) openButton.setAttribute('aria-expanded', 'true');
    const input = $('assistant-input');
    if (options.focus !== false && input) input.focus({ preventScroll: true });
    scrollAssistantMessages();
  }

  function closeAssistant() {
    const panel = $('assistant-panel');
    const openButton = $('assistant-open');
    if (!panel) return;
    assistantPanelOpen = false;
    panel.hidden = true;
    if (openButton) openButton.setAttribute('aria-expanded', 'false');
    if (assistantReturnFocus && typeof assistantReturnFocus.focus === 'function') {
      assistantReturnFocus.focus({ preventScroll: true });
    }
    assistantReturnFocus = null;
  }

  function handleAssistantSubmit(event) {
    event.preventDefault();
    const input = $('assistant-input');
    if (!input) return;
    const question = input.value.trim();
    if (!askAssistant(question)) input.focus();
  }

  function sanitiseAssistantBText(value) {
    return textValue(value, '').replace(/\bsk-[A-Za-z0-9_-]{8,}\b/g, 'sk-***');
  }

  function assistantBErrorMessageIsUnsupported(message) {
    return /未提供.*(?:接口|扩展|安装|管理)|不支持.*(?:扩展|该功能|安装)|unsupported/i.test(textValue(message, ''));
  }

  function mergeAssistantBPartial(target, partial) {
    if (!partial) return target;
    const next = { ...target };
    if (partial.hasInstalledField) next.installed = partial.installed;
    for (const field of ['path', 'repo', 'commit', 'version', 'entry', 'readme']) {
      if (partial[field]) next[field] = partial[field];
    }
    if (partial.message) next.message = partial.message;
    return next;
  }

  function extractAssistantBSource(raw) {
    const root = asRecord(raw);
    let source = { ...root };
    for (const key of ['assistant', 'status', 'result', 'config']) {
      const nested = asRecord(root[key]);
      if (Object.keys(nested).length > 0) source = { ...source, ...nested };
    }
    return source;
  }

  function normaliseAssistantBStatus(raw) {
    const source = extractAssistantBSource(raw);
    const installedValue = boolOrUndefined(source.installed);
    const ok = source.ok !== false;
    const message = sanitiseAssistantBText(source.message);
    const error = sanitiseAssistantBText(source.error || (ok ? '' : message));
    return {
      status: ok ? 'ok' : 'error',
      installed: installedValue === true,
      hasInstalledField: installedValue !== undefined,
      path: textValue(source.path, ''),
      repo: textValue(source.repo, ''),
      commit: textValue(source.commit, ''),
      version: textValue(source.version, ''),
      entry: textValue(source.entry, ''),
      readme: textValue(source.readme, ''),
      message,
      error,
      updatedAt: Date.now()
    };
  }

  function normaliseAssistantBConfig(raw, previous = {}) {
    const source = extractAssistantBSource(raw);
    const prev = asRecord(previous);
    const ok = source.ok !== false;
    const configuredValue = boolOrUndefined(source.configured);
    const runningValue = boolOrUndefined(source.running);
    const keyPresentValue = boolOrUndefined(source.key_present);
    const keySourceValue = textValue(source.key_source || source.keySource, '').trim().toLowerCase();
    const keySource = keySourceValue === 'instance' || keySourceValue === 'independent'
      ? keySourceValue
      : (prev.keySource === 'instance' ? 'instance' : 'independent');
    const portValue = numberOrUndefined(source.port);
    const port = portValue !== undefined
      ? Math.round(portValue)
      : (numberOrUndefined(prev.port) !== undefined ? Math.round(numberOrUndefined(prev.port)) : 8766);
    const message = sanitiseAssistantBText(source.message);
    const error = sanitiseAssistantBText(source.error || (ok ? '' : message));
    return {
      status: ok ? 'ok' : 'error',
      configured: configuredValue === undefined ? Boolean(prev.configured) : configuredValue,
      hasConfiguredField: configuredValue !== undefined,
      running: runningValue === undefined ? Boolean(prev.running) : runningValue,
      hasRunningField: runningValue !== undefined,
      url: textValue(source.url, prev.url || ''),
      configureUrl: textValue(source.configure_url, prev.configureUrl || ''),
      configPath: textValue(source.config_path, prev.configPath || ''),
      accessNote: sanitiseAssistantBText(textValue(source.access_note, prev.accessNote || '')),
      sshTunnel: sanitiseAssistantBText(textValue(source.ssh_tunnel, prev.sshTunnel || '')),
      pid: textValue(source.pid, prev.pid || ''),
      logPath: textValue(source.log_path, prev.logPath || ''),
      pidPath: textValue(source.pid_path, prev.pidPath || ''),
      keyPresent: keyPresentValue === undefined ? Boolean(prev.keyPresent) : keyPresentValue,
      hasKeyPresentField: keyPresentValue !== undefined,
      keySource,
      instanceId: textValue(source.instance_id, prev.instanceId || ''),
      port,
      host: textValue(source.host, prev.host || '127.0.0.1'),
      message,
      error,
      action: textValue(source.action, ''),
      updatedAt: Date.now()
    };
  }

  function isAssistantBLoopbackUrl(rawUrl) {
    const value = textValue(rawUrl, '').trim();
    if (!value) return false;
    try {
      const hostname = new URL(value).hostname.toLowerCase();
      return hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '::1' || hostname === '[::1]';
    } catch (error) {
      return false;
    }
  }

  function isAssistantBWildcardUrl(rawUrl) {
    const value = textValue(rawUrl, '').trim();
    if (!value) return false;
    try {
      const hostname = new URL(value).hostname.toLowerCase();
      return hostname === '0.0.0.0' || hostname === '::' || hostname === '[::]';
    } catch (error) {
      return false;
    }
  }

  function setAssistantBConfigStatus(message, tone = 'info') {
    const element = $('assistant-b-config-status');
    if (!element) return;
    element.textContent = message || '';
    element.className = `assistant-b-config-status is-${tone}`;
    element.hidden = !message;
  }

  function setAssistantBConfigError(message) {
    const element = $('assistant-b-config-error');
    if (!element) return;
    element.textContent = message || '';
    element.hidden = !message;
  }

  function setAssistantBConfigFieldError(input, errorElement, message) {
    if (input) input.setAttribute('aria-invalid', 'true');
    if (errorElement) {
      errorElement.textContent = message || '';
      errorElement.hidden = !message;
    }
  }

  function clearAssistantBConfigErrors() {
    for (const pair of [
      ['assistant-b-api-key', 'assistant-b-key-error'],
      ['assistant-b-instance-select', 'assistant-b-instance-error'],
      ['assistant-b-port', 'assistant-b-port-error']
    ]) {
      const input = $(pair[0]);
      const error = $(pair[1]);
      if (input) input.removeAttribute('aria-invalid');
      if (error) {
        error.textContent = '';
        error.hidden = true;
      }
    }
    setAssistantBConfigError('');
  }

  function populateAssistantBInstanceOptions() {
    const select = $('assistant-b-instance-select');
    if (!select) return;
    const desired = select.value || state.assistantB.config.instanceId || '';
    select.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = state.instances.length ? '请选择实例' : '暂无可用实例，请改用独立 Key';
    select.appendChild(placeholder);
    for (const instance of state.instances) {
      const option = document.createElement('option');
      option.value = instance.id;
      const qq = instance.qq_account ? ` · QQ ${instance.qq_account}` : '';
      option.textContent = `${instance.name} · ${instance.id}${qq}`;
      select.appendChild(option);
    }
    if (desired && !state.instances.some((instance) => instance.id === desired)) {
      const option = document.createElement('option');
      option.value = desired;
      option.textContent = `配置中的实例 · ${desired}`;
      select.appendChild(option);
    }
    select.value = desired;
  }

  function syncAssistantBConfigSourceFields() {
    const instanceRadio = $('assistant-b-key-source-instance');
    const independentFields = $('assistant-b-independent-fields');
    const instanceFields = $('assistant-b-instance-fields');
    const keyHint = $('assistant-b-config-key-hint');
    const source = instanceRadio && instanceRadio.checked ? 'instance' : 'independent';
    if (independentFields) independentFields.hidden = source !== 'independent';
    if (instanceFields) instanceFields.hidden = source !== 'instance';
    if (keyHint) {
      const config = state.assistantB.config;
      keyHint.textContent = source === 'independent' && config.keyPresent && config.keySource === 'independent'
        ? '本机已保存独立 Key；留空保持原值，输入新 Key 可覆盖。Key 不会回显或保存到浏览器。'
        : source === 'independent'
          ? '只写入本机 B 配置目录，不保存到浏览器内存之外。'
          : '只提交所选实例 ID；实例的真实 DeepSeek Key 不会返回浏览器。';
    }
  }

  function populateAssistantBConfigForm() {
    const config = state.assistantB.config;
    const source = config.keySource === 'instance' ? 'instance' : 'independent';
    const independentRadio = $('assistant-b-key-source-independent');
    const instanceRadio = $('assistant-b-key-source-instance');
    const keyInput = $('assistant-b-api-key');
    const portInput = $('assistant-b-port');
    const select = $('assistant-b-instance-select');
    if (independentRadio) independentRadio.checked = source === 'independent';
    if (instanceRadio) instanceRadio.checked = source === 'instance';
    populateAssistantBInstanceOptions();
    if (select) select.value = config.instanceId || '';
    if (portInput) portInput.value = String(config.port || 8766);
    if (keyInput) {
      keyInput.value = '';
      keyInput.type = 'password';
      const toggle = $('assistant-b-toggle-key');
      if (toggle) {
        toggle.textContent = '显示';
        toggle.setAttribute('aria-pressed', 'false');
      }
    }
    clearAssistantBConfigErrors();
    syncAssistantBConfigSourceFields();
    if (config.status === 'error') {
      setAssistantBConfigError(config.error || 'AI 助手 B 配置读取失败。');
    } else if (config.configured && config.running) {
      setAssistantBConfigStatus('已保存配置，AI 助手 B 正在运行。', 'success');
    } else if (config.configured) {
      setAssistantBConfigStatus('配置已保存但尚未启动；点击“保存并启动”重试启动。', 'info');
    } else {
      setAssistantBConfigStatus('', 'info');
    }
  }

  function assistantBConfigFocusTarget() {
    const instanceRadio = $('assistant-b-key-source-instance');
    if (instanceRadio && instanceRadio.checked) return $('assistant-b-instance-select');
    return $('assistant-b-api-key');
  }

  function openAssistantBConfigPanel(options = {}) {
    const assistant = state.assistantB;
    if (!assistant.installed) return false;
    if (assistant.config.status !== 'ok') {
      if (assistant.config.status !== 'unsupported') {
        void loadAssistantBConfig({ silent: false, open: true });
      }
      return false;
    }
    const panel = $('assistant-b-config-panel');
    if (!panel) return false;
    assistant.configPanelOpen = true;
    panel.hidden = false;
    renderAssistantBCard();
    const configureButton = $('assistant-b-configure');
    if (configureButton) configureButton.setAttribute('aria-expanded', 'true');
    populateAssistantBConfigForm();
    panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    if (options.focus !== false) {
      const target = assistantBConfigFocusTarget();
      if (target) target.focus({ preventScroll: true });
    }
    return true;
  }

  function closeAssistantBConfigPanel(options = {}) {
    const panel = $('assistant-b-config-panel');
    const assistant = state.assistantB;
    assistant.configPanelOpen = false;
    if (panel) panel.hidden = true;
    const configureButton = $('assistant-b-configure');
    if (configureButton) configureButton.setAttribute('aria-expanded', 'false');
    const keyInput = $('assistant-b-api-key');
    if (keyInput) {
      keyInput.value = '';
      keyInput.type = 'password';
      keyInput.removeAttribute('aria-invalid');
    }
    const toggle = $('assistant-b-toggle-key');
    if (toggle) {
      toggle.textContent = '显示';
      toggle.setAttribute('aria-pressed', 'false');
    }
    setAssistantBConfigError('');
    setAssistantBConfigStatus('', 'info');
    renderAssistantBCard();
    if (options.restoreFocus !== false && configureButton && !configureButton.hidden) {
      configureButton.focus({ preventScroll: true });
    }
  }

  async function loadAssistantBConfig(options = {}) {
    const assistant = state.assistantB;
    if (!assistant.installed) return false;
    if (assistant.config.loading) return false;
    const silent = options.silent !== false;
    if (assistant.config.status === 'unsupported' && silent && options.open !== true) return false;
    assistant.config.loading = true;
    assistant.config.error = '';
    renderAssistantBCard();
    try {
      const data = await apiRequest('/api/assistant/config');
      if (!data || data.ok === false) {
        throw new ApiError(
          sanitiseAssistantBText(asRecord(data).message || asRecord(data).error) || 'AI 助手 B 配置读取失败。',
          { data }
        );
      }
      const normalized = normaliseAssistantBConfig(data, assistant.config);
      assistant.config = {
        ...assistant.config,
        ...normalized,
        status: 'ok',
        loading: false,
        error: ''
      };
      renderAssistantBCard();
      if (assistant.configPanelOpen) populateAssistantBInstanceOptions();
      if (options.open === true || assistant.openConfigRequested) {
        assistant.openConfigRequested = false;
        openAssistantBConfigPanel({ focus: true });
      }
      return true;
    } catch (error) {
      const errorData = error instanceof ApiError ? asRecord(error.data) : {};
      const partial = Object.keys(errorData).length > 0
        ? normaliseAssistantBConfig(errorData, assistant.config)
        : null;
      const unsupported = isMissingEndpointError(error)
        || assistantBErrorMessageIsUnsupported(error instanceof ApiError ? error.message : '')
        || Boolean(partial && assistantBErrorMessageIsUnsupported(`${partial.message} ${partial.error}`));
      assistant.config = {
        ...assistant.config,
        ...(partial || {}),
        status: unsupported ? 'unsupported' : 'error',
        loading: false,
        error: unsupported
          ? '当前后端不支持 AI 助手 B 配置/启动。'
          : formatError(error, 'AI 助手 B 配置读取失败。')
      };
      if (unsupported) assistant.openConfigRequested = false;
      renderAssistantBCard();
      return false;
    }
  }

  async function handleAssistantBConfigureClick() {
    const assistant = state.assistantB;
    if (!assistant.installed || assistant.config.status === 'unsupported') return;
    if (assistant.configPanelOpen) {
      closeAssistantBConfigPanel();
      return;
    }
    if (assistant.config.status === 'ok') {
      openAssistantBConfigPanel({ focus: true });
    } else {
      await loadAssistantBConfig({ silent: false, open: true });
    }
  }

  function scheduleAssistantBConfigRedirect() {
    const target = '/console?assistant=configure';
    window.setTimeout(() => {
      try {
        if (window.location && typeof window.location.assign === 'function') {
          window.location.assign(target);
        } else if (window.location) {
          window.location.href = target;
        }
      } catch (error) {
        // The card remains usable if a test or locked-down browser blocks navigation.
      }
    }, 800);
  }

  async function handleAssistantBConfigSubmit(event) {
    event.preventDefault();
    const assistant = state.assistantB;
    if (!assistant.installed || assistant.config.saving) return;
    clearAssistantBConfigErrors();
    setAssistantBConfigStatus('', 'info');

    const independentRadio = $('assistant-b-key-source-independent');
    const instanceRadio = $('assistant-b-key-source-instance');
    const source = instanceRadio && instanceRadio.checked ? 'instance' : 'independent';
    const keyInput = $('assistant-b-api-key');
    const instanceSelect = $('assistant-b-instance-select');
    const portInput = $('assistant-b-port');
    const apiKey = keyInput ? keyInput.value.trim() : '';
    const instanceId = instanceSelect ? instanceSelect.value.trim() : '';
    const existingIndependentKey = assistant.config.keyPresent && assistant.config.keySource === 'independent';
    let valid = true;

    if (source === 'independent') {
      if (!apiKey && !existingIndependentKey) {
        setAssistantBConfigFieldError(keyInput, $('assistant-b-key-error'), '请输入独立的 DeepSeek API Key。');
        valid = false;
      }
    } else if (!instanceId || !state.instances.some((instance) => instance.id === instanceId)) {
      setAssistantBConfigFieldError(instanceSelect, $('assistant-b-instance-error'), '请选择一个可用实例。');
      valid = false;
    }

    const port = Number(portInput ? portInput.value : 0);
    if (!Number.isInteger(port) || port < 5000 || port > 10000) {
      setAssistantBConfigFieldError(portInput, $('assistant-b-port-error'), '端口必须是 5000–10000 之间的整数。');
      valid = false;
    }
    if (!valid) {
      const target = source === 'independent'
        ? (apiKey || existingIndependentKey ? portInput : keyInput)
        : (instanceId ? portInput : instanceSelect);
      if (target) target.focus({ preventScroll: true });
      return;
    }

    const body = {
      key_source: source,
      port,
      host: '127.0.0.1'
    };
    if (source === 'independent' && apiKey) body.api_key = apiKey;
    if (source === 'instance') body.instance_id = instanceId;

    assistant.config.saving = true;
    assistant.config.phase = 'saving';
    assistant.actionError = '';
    setAssistantBConfigStatus('正在保存配置…', 'info');
    renderAssistantBCard();
    try {
      const configData = await apiRequest('/api/assistant/config', { method: 'POST', body });
      if (!configData || configData.ok === false) {
        throw new ApiError(
          sanitiseAssistantBText(asRecord(configData).message || asRecord(configData).error) || 'AI 助手 B 配置保存失败。',
          { data: configData }
        );
      }
      const saved = normaliseAssistantBConfig(configData, assistant.config);
      assistant.config = {
        ...assistant.config,
        ...saved,
        status: 'ok',
        configured: saved.hasConfiguredField ? saved.configured : true,
        keyPresent: saved.hasKeyPresentField
          ? saved.keyPresent
          : (source === 'independent' ? Boolean(apiKey) || existingIndependentKey : true),
        keySource: source,
        instanceId: source === 'instance' ? instanceId : (saved.instanceId || ''),
        port,
        host: '127.0.0.1',
        error: ''
      };
      if (keyInput) keyInput.value = '';
      setAssistantBConfigStatus('配置已保存，正在启动 AI 助手 B…', 'info');
      assistant.config.phase = 'starting';
      renderAssistantBCard();

      const startData = await apiRequest('/api/assistant/start', { method: 'POST', body: {} });
      if (!startData || startData.ok === false) {
        throw new ApiError(
          sanitiseAssistantBText(asRecord(startData).message || asRecord(startData).error) || 'AI 助手 B 启动失败。',
          { data: startData }
        );
      }
      const started = normaliseAssistantBConfig(startData, assistant.config);
      assistant.config = {
        ...assistant.config,
        ...started,
        status: 'ok',
        configured: true,
        running: started.hasRunningField ? started.running : true,
        keySource: source,
        instanceId: source === 'instance' ? instanceId : assistant.config.instanceId,
        port,
        host: '127.0.0.1',
        saving: false,
        phase: '',
        error: '',
        message: started.message || 'AI 助手 B 已启动。'
      };
      assistant.message = assistant.config.message;
      assistant.actionError = '';
      assistant.configPanelOpen = false;
      const panel = $('assistant-b-config-panel');
      if (panel) panel.hidden = true;
      const configureButton = $('assistant-b-configure');
      if (configureButton) configureButton.setAttribute('aria-expanded', 'false');
      renderAssistantBCard();
    } catch (error) {
      const errorData = error instanceof ApiError ? asRecord(error.data) : {};
      const unsupported = isMissingEndpointError(error)
        || assistantBErrorMessageIsUnsupported(error instanceof ApiError ? error.message : '');
      const message = unsupported
        ? '当前后端不支持 AI 助手 B 配置/启动。'
        : formatError(error, 'AI 助手 B 配置或启动失败。');
      assistant.config.error = message;
      assistant.actionError = message;
      if (unsupported) assistant.config.status = 'unsupported';
      setAssistantBConfigError(message);
      setAssistantBConfigStatus('', 'info');
      renderAssistantBCard();
    } finally {
      assistant.config.saving = false;
      assistant.config.phase = '';
      renderAssistantBCard();
    }
  }

  function renderAssistantBCard() {
    const assistant = state.assistantB;
    const badge = $('assistant-b-badge');
    const statusElement = $('assistant-b-status');
    const meta = $('assistant-b-meta');
    const commitElement = $('assistant-b-commit');
    const pathElement = $('assistant-b-path');
    const entryElement = $('assistant-b-entry');
    const readmeElement = $('assistant-b-readme');
    const hint = $('assistant-b-hint');
    const installButton = $('assistant-b-install');
    const installLabel = $('assistant-b-install-label');
    const updateButton = $('assistant-b-update');
    const updateLabel = $('assistant-b-update-label');
    const retryButton = $('assistant-b-retry');
    const configureButton = $('assistant-b-configure');
    const configureLabel = $('assistant-b-configure-label');
    const openButton = $('assistant-b-open');
    const runtime = $('assistant-b-runtime');
    const urlElement = $('assistant-b-url');
    const urlNote = $('assistant-b-url-note');
    const configPanel = $('assistant-b-config-panel');
    const configSaveButton = $('assistant-b-config-save');
    const configSaveLabel = $('assistant-b-config-save-label');
    const configCancelButton = $('assistant-b-config-cancel');
    const config = assistant.config || {};

    if (!statusElement) return;

    if (assistant.loading) {
      setPill(badge, '检查中', 'neutral');
    } else if (assistant.status === 'unsupported') {
      setPill(badge, '后端不支持', 'warning');
    } else if (assistant.status === 'error') {
      setPill(badge, '状态异常', 'danger');
    } else if (assistant.installed && config.running) {
      setPill(badge, '运行中', 'success');
    } else if (assistant.installed) {
      setPill(badge, '已安装', 'success');
    } else {
      setPill(badge, '未安装', 'neutral');
    }

    let statusText = '';
    let statusTone = 'info';
    if (assistant.loading) {
      statusText = '正在检查 AI 助手 B 状态…';
      statusTone = 'info';
    } else if (assistant.actionError) {
      statusText = assistant.actionError;
      statusTone = 'error';
    } else if (assistant.busy) {
      statusText = assistant.action === 'update'
        ? '正在更新 AI 助手 B，请稍候…'
        : '正在安装 AI 助手 B，会联网下载，请稍候…';
      statusTone = 'info';
    } else if (assistant.status === 'unsupported') {
      statusText = '当前后端不支持扩展安装。A 离线使用助手不受影响。';
      statusTone = 'warning';
    } else if (assistant.status === 'error') {
      statusText = assistant.error || 'AI 助手 B 状态读取失败。';
      statusTone = 'error';
    } else if (assistant.installed && config.saving) {
      statusText = config.phase === 'starting' ? '配置已保存，正在启动 AI 助手 B…' : '正在保存 AI 助手 B 配置…';
      statusTone = 'info';
    } else if (assistant.installed && config.status === 'unsupported') {
      statusText = 'AI 助手 B 已安装；当前后端不支持配置/启动。';
      statusTone = 'warning';
    } else if (assistant.installed && config.status === 'error') {
      statusText = `AI 助手 B 已安装；配置状态读取失败：${config.error || '未知错误'}`;
      statusTone = 'error';
    } else if (assistant.installed && config.loading) {
      statusText = 'AI 助手 B 已安装，正在读取配置状态…';
      statusTone = 'info';
    } else if (assistant.installed && config.running) {
      statusText = config.message || 'AI 助手 B 已配置并启动。';
      statusTone = 'success';
    } else if (assistant.installed && config.configured) {
      statusText = config.message || 'AI 助手 B 已配置，但尚未启动。';
      statusTone = 'info';
    } else if (assistant.installed) {
      statusText = assistant.message || 'AI 助手 B 已安装。';
      statusTone = 'success';
    } else if (assistant.status === 'ok') {
      statusText = assistant.message || 'AI 助手 B 尚未安装。安装为可选操作，会联网下载独立仓库。';
      statusTone = 'info';
    } else {
      statusText = '正在检查 AI 助手 B 状态…';
      statusTone = 'info';
    }
    statusElement.textContent = statusText;
    statusElement.className = `assistant-b-status is-${statusTone}`;

    if (meta) meta.hidden = !assistant.installed;
    if (commitElement) {
      const parts = [];
      if (assistant.version) parts.push(`版本 ${assistant.version}`);
      if (assistant.commit) parts.push(`commit ${assistant.commit}`);
      const commitText = parts.join(' · ') || '未提供';
      commitElement.textContent = commitText;
      commitElement.title = commitText;
    }
    if (pathElement) {
      const value = assistant.path || '未提供';
      pathElement.textContent = value;
      pathElement.title = value;
    }
    if (entryElement) {
      const value = assistant.entry || '未提供';
      entryElement.textContent = value;
      entryElement.title = value;
    }
    if (readmeElement) {
      const value = assistant.readme || '未提供';
      readmeElement.textContent = value;
      readmeElement.title = value;
    }

    if (hint) {
      if (assistant.status === 'unsupported') {
        hint.textContent = '升级主项目后可启用该可选扩展；A 离线使用助手完全不受影响。';
      } else if (assistant.status === 'error') {
        hint.textContent = '状态读取失败时可点击“重试状态”。不会自动安装或下载任何内容。';
      } else if (assistant.installed) {
        if (config.status === 'unsupported') {
          hint.textContent = 'B 已安装，但当前后端不支持配置/启动；仍可查看状态或更新扩展。A 离线助手不受影响。';
        } else if (config.status === 'error') {
          hint.textContent = 'B 已安装，但配置状态读取失败；可点击“重试状态”。不会伪造启动地址。';
        } else if (config.running) {
          hint.textContent = 'AI 助手 B 已启动。若 URL 是 loopback，远程访问必须使用 SSH 隧道或在本机打开；不要把它当作公网直连地址。';
        } else if (config.configured) {
          hint.textContent = '配置已保存但尚未启动。点击“配置 / 启动”可重试启动；控制台不会读取或展示 API Key。';
        } else {
          hint.textContent = 'AI 助手 B 已安装。点击“配置 AI 助手 B”设置独立 Key 或复用实例 Key；API Key 不会保存到浏览器。';
        }
      } else if (assistant.armed) {
        hint.textContent = '再次点击将联网 clone 官方仓库。不会自动执行；如不同意，请不要再次点击。';
      } else {
        hint.textContent = '安装会联网 clone 官方仓库；A 离线助手一直可用。控制台不会读取或展示 B 的 API Key。';
      }
    }

    const installVisible = assistant.status === 'ok' && !assistant.installed;
    const updateVisible = assistant.status === 'ok' && assistant.installed;
    const configureVisible = assistant.status === 'ok'
      && assistant.installed
      && config.status === 'ok';
    const running = assistant.installed && config.running === true;
    if (installButton) {
      installButton.hidden = !installVisible;
      installButton.disabled = assistant.loading || assistant.busy;
      installButton.setAttribute('aria-busy', assistant.busy && assistant.action === 'install' ? 'true' : 'false');
    }
    if (installLabel) {
      installLabel.textContent = assistant.busy && assistant.action === 'install'
        ? '正在安装…'
        : assistant.armed
          ? '再次点击确认安装'
          : '安装 AI 助手 B';
    }
    if (updateButton) {
      updateButton.hidden = !updateVisible;
      updateButton.disabled = assistant.loading || assistant.busy;
      updateButton.setAttribute('aria-busy', assistant.busy && assistant.action === 'update' ? 'true' : 'false');
    }
    if (updateLabel) {
      updateLabel.textContent = assistant.busy && assistant.action === 'update' ? '正在更新…' : '更新 AI 助手 B';
    }
    if (configureButton) {
      configureButton.hidden = !configureVisible;
      configureButton.disabled = assistant.config.loading || assistant.config.saving;
      configureButton.setAttribute('aria-expanded', assistant.configPanelOpen ? 'true' : 'false');
    }
    if (configureLabel) {
      configureLabel.textContent = assistant.configPanelOpen
        ? '收起配置'
        : config.running
          ? '修改配置'
          : config.configured
            ? '配置 / 启动'
            : '配置 AI 助手 B';
    }
    if (openButton) {
      openButton.hidden = !running;
      const safeUrl = safeHttpUrl(config.url);
      if (safeUrl) {
        openButton.href = safeUrl;
        openButton.target = '_blank';
        openButton.rel = 'noopener noreferrer';
        openButton.removeAttribute('aria-disabled');
        openButton.removeAttribute('tabindex');
        openButton.title = safeUrl;
      } else {
        openButton.removeAttribute('href');
        openButton.removeAttribute('target');
        openButton.setAttribute('aria-disabled', 'true');
        openButton.title = '服务端尚未返回可打开的安全 URL';
      }
    }
    if (runtime) runtime.hidden = !running;
    if (urlElement) urlElement.textContent = config.url || '未提供';
    if (urlNote) {
      if (!running) {
        urlNote.textContent = '';
      } else if (!config.url) {
        urlNote.textContent = '服务端表示已启动，但没有返回访问 URL；请查看 B 的配置和日志。';
      } else if (isAssistantBLoopbackUrl(config.url)) {
        const tunnel = config.sshTunnel ? ` ${config.sshTunnel}` : '';
        const note = config.accessNote ? ` ${config.accessNote}` : '';
        urlNote.textContent = `该地址是 loopback（仅本机回环）。从远程电脑访问必须使用 SSH 隧道，或直接在服务器本机浏览器打开；它不是公网直连地址。${note}${tunnel}`;
      } else if (isAssistantBWildcardUrl(config.url)) {
        urlNote.textContent = config.accessNote
          || '0.0.0.0 是监听地址，不是可直接连接的公网地址；请使用服务器公网 IP/域名，并确认防火墙和安全组。';
      } else {
        urlNote.textContent = config.accessNote
          || '请确认该地址可达且防火墙/安全组已按需配置；不要把无认证的服务直接暴露到公网。';
      }
    }
    if (configPanel) {
      const panelAllowed = assistant.installed && assistant.status === 'ok' && config.status === 'ok';
      if (assistant.configPanelOpen && !panelAllowed) assistant.configPanelOpen = false;
      configPanel.hidden = !(assistant.configPanelOpen && panelAllowed);
    }
    if (configSaveButton) configSaveButton.disabled = config.saving === true;
    if (configSaveLabel) {
      configSaveLabel.textContent = config.saving
        ? (config.phase === 'starting' ? '正在启动…' : '正在保存…')
        : '保存并启动';
    }
    if (configCancelButton) configCancelButton.disabled = config.saving === true;
    for (const id of [
      'assistant-b-key-source-independent',
      'assistant-b-key-source-instance',
      'assistant-b-api-key',
      'assistant-b-instance-select',
      'assistant-b-port',
      'assistant-b-toggle-key'
    ]) {
      const control = $(id);
      if (control) control.disabled = config.saving === true;
    }
    if (retryButton) {
      retryButton.hidden = !(assistant.status === 'error'
        || (assistant.installed && config.status === 'error'));
    }
  }

  async function loadAssistantBStatus(options = {}) {
    const silent = options.silent !== false;
    if (state.assistantB.loading) return false;
    if (state.assistantB.status === 'unsupported' && silent) return false;
    state.assistantB.loading = true;
    state.assistantB.armed = false;
    state.assistantB.actionError = '';
    renderAssistantBCard();
    try {
      const data = await apiRequest('/api/assistant/status');
      if (!data || data.ok === false) {
        throw new ApiError(
          sanitiseAssistantBText(asRecord(data).message || asRecord(data).error) || 'AI 助手 B 状态读取失败。',
          { data }
        );
      }
      const normalized = normaliseAssistantBStatus(data);
      if (normalized.status === 'error') {
        throw new ApiError(normalized.error || normalized.message || 'AI 助手 B 状态读取失败。', { data });
      }
      const nextAssistant = {
        ...state.assistantB,
        ...normalized,
        status: 'ok',
        loading: false,
        error: '',
        actionError: '',
        armed: false
      };
      if (!normalized.hasInstalledField) nextAssistant.installed = state.assistantB.installed;
      state.assistantB = nextAssistant;
      renderAssistantBCard();
      if (state.assistantB.installed) {
        const openRequested = state.assistantB.openConfigRequested;
        void loadAssistantBConfig({ silent: !openRequested, open: openRequested });
      }
      return true;
    } catch (error) {
      const errorData = error instanceof ApiError ? asRecord(error.data) : {};
      const partial = Object.keys(errorData).length > 0
        ? normaliseAssistantBStatus(errorData)
        : null;
      const rawMessage = error instanceof ApiError
        ? error.message
        : (partial ? partial.message || partial.error : '');
      const unsupported = isMissingEndpointError(error)
        || assistantBErrorMessageIsUnsupported(rawMessage)
        || Boolean(partial && assistantBErrorMessageIsUnsupported(`${partial.message} ${partial.error}`));
      state.assistantB = mergeAssistantBPartial({
        ...state.assistantB,
        status: unsupported ? 'unsupported' : 'error',
        loading: false,
        error: unsupported
          ? '当前后端不支持扩展安装。'
          : formatError(error, 'AI 助手 B 状态读取失败。'),
        actionError: '',
        armed: false
      }, partial);
      renderAssistantBCard();
      return false;
    }
  }

  async function runAssistantBAction(action) {
    if (state.assistantB.busy || state.assistantB.loading) return;
    if (action !== 'install' && action !== 'update') return;
    state.assistantB.busy = true;
    state.assistantB.action = action;
    state.assistantB.armed = false;
    state.assistantB.actionError = '';
    renderAssistantBCard();
    let refreshAfter = false;
    let redirectToConfig = false;
    try {
      const data = action === 'update'
        ? await requestWithFallback([
          '/api/assistant/update',
          '/api/assistant/install'
        ], { method: 'POST', body: {} })
        : await apiRequest('/api/assistant/install', { method: 'POST', body: {} });
      if (!data || data.ok === false) {
        throw new ApiError(
          sanitiseAssistantBText(asRecord(data).message || asRecord(data).error) || 'AI 助手 B 操作失败。',
          { data }
        );
      }
      const source = extractAssistantBSource(data);
      const normalized = normaliseAssistantBStatus({ ...source, ok: true });
      if (action === 'install') {
        state.assistantB.installed = normalized.hasInstalledField ? normalized.installed : true;
      } else if (normalized.hasInstalledField) {
        state.assistantB.installed = normalized.installed;
      }
      for (const field of ['path', 'repo', 'commit', 'version', 'entry', 'readme']) {
        const value = textValue(source[field], '');
        if (value) state.assistantB[field] = value;
      }
      state.assistantB.status = 'ok';
      state.assistantB.error = '';
      state.assistantB.actionError = '';
      state.assistantB.message = sanitiseAssistantBText(source.message)
        || (action === 'install' ? 'AI 助手 B 安装完成。' : 'AI 助手 B 已更新。');
      state.assistantB.updatedAt = Date.now();
      if (action === 'install') {
        redirectToConfig = true;
      } else {
        refreshAfter = true;
      }
    } catch (error) {
      const unsupported = isMissingEndpointError(error)
        || assistantBErrorMessageIsUnsupported(error instanceof ApiError ? error.message : '');
      state.assistantB.actionError = unsupported
        ? '当前后端不支持扩展安装。'
        : formatError(error, action === 'install' ? 'AI 助手 B 安装失败。' : 'AI 助手 B 更新失败。');
      if (unsupported) state.assistantB.status = 'unsupported';
    } finally {
      state.assistantB.busy = false;
      state.assistantB.action = '';
      renderAssistantBCard();
    }
    if (redirectToConfig) {
      state.assistantB.openConfigRequested = true;
      state.assistantB.message = 'AI 助手 B 安装完成，正在打开配置页面…';
      renderAssistantBCard();
      scheduleAssistantBConfigRedirect();
      return;
    }
    if (refreshAfter) await loadAssistantBStatus({ silent: false });
  }

  function handleAssistantBInstallClick() {
    const assistant = state.assistantB;
    if (assistant.busy || assistant.loading || assistant.status !== 'ok' || assistant.installed) return;
    if (!assistant.armed) {
      assistant.armed = true;
      renderAssistantBCard();
      window.setTimeout(() => {
        if (state.assistantB.armed && !state.assistantB.busy) {
          state.assistantB.armed = false;
          renderAssistantBCard();
        }
      }, 8000);
      return;
    }
    assistant.armed = false;
    void runAssistantBAction('install');
  }

  function handleAssistantBUpdateClick() {
    if (state.assistantB.status !== 'ok' || !state.assistantB.installed) return;
    void runAssistantBAction('update');
  }

  function handleAssistantBKeySourceChange() {
    clearAssistantBConfigErrors();
    syncAssistantBConfigSourceFields();
  }

  function handleAssistantBKeyToggle() {
    const input = $('assistant-b-api-key');
    const toggle = $('assistant-b-toggle-key');
    if (!input) return;
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    if (toggle) {
      toggle.textContent = show ? '隐藏' : '显示';
      toggle.setAttribute('aria-pressed', show ? 'true' : 'false');
    }
    input.focus({ preventScroll: true });
  }

  function handleAssistantBRetryClick() {
    if (state.assistantB.loading || state.assistantB.busy) return;
    state.assistantB.status = 'idle';
    state.assistantB.error = '';
    state.assistantB.actionError = '';
    state.assistantB.config.status = 'idle';
    state.assistantB.config.error = '';
    renderAssistantBCard();
    void loadAssistantBStatus({ silent: false });
  }
  function bindEvents() {
    const assistantBConfigure = $('assistant-b-configure');
    if (assistantBConfigure) assistantBConfigure.addEventListener('click', () => void handleAssistantBConfigureClick());
    const assistantBConfigForm = $('assistant-b-config-form');
    if (assistantBConfigForm) assistantBConfigForm.addEventListener('submit', handleAssistantBConfigSubmit);
    const assistantBConfigCancel = $('assistant-b-config-cancel');
    if (assistantBConfigCancel) assistantBConfigCancel.addEventListener('click', () => closeAssistantBConfigPanel());
    const assistantBKeyToggle = $('assistant-b-toggle-key');
    if (assistantBKeyToggle) assistantBKeyToggle.addEventListener('click', handleAssistantBKeyToggle);
    $$('input[name="assistant-b-key-source"]').forEach((radio) => {
      radio.addEventListener('change', handleAssistantBKeySourceChange);
    });
    const assistantBApiKey = $('assistant-b-api-key');
    if (assistantBApiKey) {
      assistantBApiKey.addEventListener('input', () => {
        assistantBApiKey.removeAttribute('aria-invalid');
        const error = $('assistant-b-key-error');
        if (error) {
          error.hidden = true;
          error.textContent = '';
        }
      });
    }
    const assistantBInstanceSelect = $('assistant-b-instance-select');
    if (assistantBInstanceSelect) {
      assistantBInstanceSelect.addEventListener('change', () => {
        assistantBInstanceSelect.removeAttribute('aria-invalid');
        const error = $('assistant-b-instance-error');
        if (error) {
          error.hidden = true;
          error.textContent = '';
        }
      });
    }
    const assistantBPort = $('assistant-b-port');
    if (assistantBPort) {
      assistantBPort.addEventListener('input', () => {
        assistantBPort.removeAttribute('aria-invalid');
        const error = $('assistant-b-port-error');
        if (error) {
          error.hidden = true;
          error.textContent = '';
        }
      });
    }

    const assistantBInstall = $('assistant-b-install');
    if (assistantBInstall) assistantBInstall.addEventListener('click', handleAssistantBInstallClick);
    const assistantBUpdate = $('assistant-b-update');
    if (assistantBUpdate) assistantBUpdate.addEventListener('click', handleAssistantBUpdateClick);
    const assistantBRetry = $('assistant-b-retry');
    if (assistantBRetry) assistantBRetry.addEventListener('click', handleAssistantBRetryClick);

    const assistantOpen = $('assistant-open');
    if (assistantOpen) {
      assistantOpen.addEventListener('click', () => {
        if (assistantPanelOpen) closeAssistant();
        else openAssistant();
      });
    }
    const assistantClose = $('assistant-close');
    if (assistantClose) assistantClose.addEventListener('click', closeAssistant);
    const assistantForm = $('assistant-form');
    if (assistantForm) assistantForm.addEventListener('submit', handleAssistantSubmit);
    const assistantInput = $('assistant-input');
    if (assistantInput) {
      assistantInput.addEventListener('input', () => setAssistantError(''));
    }
    $$('[data-assistant-question]').forEach((button) => {
      button.addEventListener('click', () => {
        openAssistant({ focus: false });
        askAssistant(button.dataset.assistantQuestion || '');
        const input = $('assistant-input');
        if (input) input.focus({ preventScroll: true });
      });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && assistantPanelOpen) {
        event.preventDefault();
        closeAssistant();
      }
    });

    const refresh = $('console-refresh');
    if (refresh) refresh.addEventListener('click', () => void loadConsole());
    const updateButton = $('console-update');
    if (updateButton) updateButton.addEventListener('click', () => void handleUpdateClick());
    const retry = $('console-error-retry');
    if (retry) retry.addEventListener('click', () => void loadConsole());

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
    try {
      state.assistantB.openConfigRequested = new URLSearchParams(window.location.search).get('assistant') === 'configure';
    } catch (error) {
      state.assistantB.openConfigRequested = false;
    }
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
