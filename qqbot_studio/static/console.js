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
    lastSyncedAt: 0,
    pollTimer: null,
    updateBusy: false,
    updateChecked: false,
    updateArmed: false,
    updateCanApply: false,
    updateInfo: null
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
      if (['true', '1', 'yes', 'on', 'running', 'deployed', 'ok'].includes(normalised)) return true;
      if (['false', '0', 'no', 'off', 'stopped', 'not_deployed', 'error'].includes(normalised)) return false;
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
      state_error: textValue(instance.state_error, '')
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
        setUpdateLabel(`发现更新 ${data.behind || ''} 个提交`.trim());
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
      setUpdateLabel('再次点击确认更新并重启');
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
    const wizardLink = makeElement('a', 'btn btn-primary btn-small instance-wizard-link', '');
    wizardLink.href = wizardUrl(instance.id);
    wizardLink.appendChild(iconText('c-i-edit', '打开向导'));
    actions.appendChild(wizardLink);

    const controlGroup = makeElement('div', 'instance-control-group');
    controlGroup.appendChild(makeActionButton(instance, 'start'));
    controlGroup.appendChild(makeActionButton(instance, 'stop'));
    controlGroup.appendChild(makeActionButton(instance, 'restart'));
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

      state.instances = merged;
      state.loadedOnce = true;
      state.lastSyncedAt = Date.now();
      renderInstances();
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
              'AstrBot、SnowLuma、noVNC 的宿主机端口由程序自动分配，请查看控制台实例卡片的链接，不要猜端口。',
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

  function bindEvents() {
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
