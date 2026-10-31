/* QQ Bot Studio — shared browser API service.
 * Classic-script namespace (no bundler): console and future components can use the
 * same token header, JSON parsing, ApiError shape and 404/405 fallback behavior.
 */
(function (global) {
  'use strict';

  function asRecord(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }

  function textValue(value, fallback = '') {
    if (typeof value === 'string') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    return fallback;
  }

  function createBoolParser(trueValues, falseValues) {
    const truthy = Array.isArray(trueValues) ? trueValues : [];
    const falsy = Array.isArray(falseValues) ? falseValues : [];
    return function boolOrUndefined(value) {
      if (typeof value === 'boolean') return value;
      if (value === 1 || value === 0) return value === 1;
      if (typeof value === 'string') {
        const normalised = value.trim().toLowerCase();
        if (truthy.includes(normalised)) return true;
        if (falsy.includes(normalised)) return false;
      }
      return undefined;
    };
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

  class ApiError extends Error {
    constructor(message, options = {}) {
      super(message);
      this.name = 'ApiError';
      this.status = Number(options.status) || 0;
      this.data = options.data || null;
    }
  }

  function createApiClient(options = {}) {
    const token = typeof options.token === 'string' ? options.token : '';
    const suppliedFetch = typeof options.fetchImpl === 'function' ? options.fetchImpl : null;
    const fetchImpl = suppliedFetch || (typeof global.fetch === 'function' ? global.fetch.bind(global) : null);
    const connectionErrorMessage = typeof options.connectionErrorMessage === 'string' && options.connectionErrorMessage
      ? options.connectionErrorMessage
      : '无法连接本地服务，请确认本地启动器仍在运行。';

    async function request(path, requestOptions = {}) {
      if (!fetchImpl) {
        throw new ApiError('当前浏览器不支持 fetch，无法访问本地服务。', { status: 0 });
      }
      const method = String(requestOptions.method || 'GET').toUpperCase();
      const headers = {
        Accept: 'application/json',
        // 本机服务的 GET 接口也校验令牌，因此所有请求都显式携带。
        'X-QB-Token': token,
        ...(requestOptions.headers || {})
      };
      const fetchOptions = {
        method,
        headers,
        cache: 'no-store',
        credentials: 'same-origin'
      };
      if (requestOptions.body !== undefined) {
        headers['Content-Type'] = 'application/json';
        fetchOptions.body = JSON.stringify(requestOptions.body);
      }

      let response;
      try {
        response = await fetchImpl(path, fetchOptions);
      } catch (error) {
        throw new ApiError(connectionErrorMessage, { status: 0 });
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

    function isMissingEndpointError(error) {
      return error instanceof ApiError && (error.status === 404 || error.status === 405);
    }

    async function requestWithFallback(paths, requestOptions = {}) {
      let lastError = null;
      for (const path of paths) {
        try {
          return await request(path, requestOptions);
        } catch (error) {
          lastError = error;
          if (!isMissingEndpointError(error)) throw error;
        }
      }
      throw lastError || new ApiError('本地服务未提供该功能。', { status: 404 });
    }

    return Object.freeze({
      request,
      requestWithFallback,
      isMissingEndpointError,
      ApiError
    });
  }

  const namespace = global.QQBotStudio = global.QQBotStudio || {};
  namespace.services = namespace.services || {};
  namespace.services.api = Object.freeze({ createApiClient, ApiError, asRecord, textValue, createBoolParser, safeHttpUrl });
})(typeof window !== 'undefined' ? window : globalThis);
