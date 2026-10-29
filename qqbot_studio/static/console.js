/* QQ Bot Studio — 多实例控制台
 * 纯原生 JavaScript，无第三方依赖。所有动态数据通过 textContent / DOM API 渲染。
 */
(() => {
  'use strict';

  const TOKEN_RAW = typeof window.QB_TOKEN === 'string' ? window.QB_TOKEN : '';
  const QB_TOKEN = TOKEN_RAW && TOKEN_RAW !== '{{TOKEN}}' ? TOKEN_RAW : '';
  const AUTO_REFRESH_INTERVAL_MS = 30 * 60 * 1000;

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
