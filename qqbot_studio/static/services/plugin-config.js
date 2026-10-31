/* QQ Bot Studio — plugin configuration contract service.
 * Pure schema/normalisation logic plus the authenticated GET/POST calls. DOM
 * rendering stays in console.js so the existing IDs and panel behavior remain.
 */
(function (global) {
  'use strict';

  const apiNamespace = global.QQBotStudio && global.QQBotStudio.services && global.QQBotStudio.services.api;
  if (!apiNamespace || typeof apiNamespace.createBoolParser !== 'function') {
    throw new Error('缺少共享 API service：请确认 /services/api.js 已加载。');
  }
  const { asRecord, textValue, createBoolParser } = apiNamespace;
  const boolOrUndefined = createBoolParser(
    ['true', '1', 'yes', 'on', 'running', 'deployed', 'ok', 'enabled', 'active', 'available', 'connected'],
    ['false', '0', 'no', 'off', 'stopped', 'not_deployed', 'error', 'disabled', 'inactive', 'unavailable', 'disconnected']
  );

  function pluginNumberValue(value) {
    if (value === null || value === undefined || value === '') return undefined;
    const number = Number(value);
    return Number.isFinite(number) ? number : undefined;
  }

  function sanitisePluginText(value) {
    return textValue(value, '').replace(/\bsk-[A-Za-z0-9_-]{8,}\b/g, 'sk-***');
  }

  function pluginConfigUnsupportedMessage(message) {
    return /未提供.*(?:接口|参数|插件)|不支持.*(?:参数|编辑|插件)|插件配置接口.*暂不可用|参数.*暂不可用|接口.*不存在|不存在.*接口|未实现|not\s*(?:supported|found)/i.test(textValue(message, ''));
  }

  function isSensitivePluginField(key, definition) {
    const def = asRecord(definition);
    if (boolOrUndefined(def.sensitive) === true || boolOrUndefined(def.secret) === true) return true;
    if (['password', 'secret', 'token', 'api_key', 'apikey'].includes(textValue(def.type, '').trim().toLowerCase())) return true;
    return /(?:api[\s_-]*key|access[\s_-]*token|auth[\s_-]*token|secret[\s_-]*key|client[\s_-]*secret|password|authorization|credential|private[\s_-]*key)/i.test(`${key} ${textValue(def.label || def.title, '')}`);
  }

  function pluginFieldType(definition) {
    const raw = textValue(asRecord(definition).type, 'string').trim().toLowerCase();
    if (['bool', 'boolean', 'checkbox', 'switch'].includes(raw)) return 'bool';
    if (['int', 'integer', 'number_int'].includes(raw)) return 'int';
    if (['float', 'number', 'double', 'decimal'].includes(raw)) return 'float';
    if (['text', 'textarea', 'multiline'].includes(raw)) return 'text';
    if (['list', 'array', 'string_list', 'string-array'].includes(raw)) return 'list';
    return 'string';
  }

  function normalisePluginOptions(rawOptions, rawLabels) {
    const labels = Array.isArray(rawLabels) ? rawLabels : [];
    let entries = [];
    if (Array.isArray(rawOptions)) {
      entries = rawOptions.map((item, index) => {
        const record = asRecord(item);
        if (Object.keys(record).length > 0 && Object.prototype.hasOwnProperty.call(record, 'value')) {
          return { value: textValue(record.value, ''), label: sanitisePluginText(record.label || record.title || record.value) || textValue(record.value, '') };
        }
        const value = textValue(item, '');
        return { value, label: sanitisePluginText(labels[index]) || value };
      });
    } else if (rawOptions && typeof rawOptions === 'object') {
      entries = Object.entries(rawOptions).map(([value, label]) => ({
        value,
        label: sanitisePluginText(label) || value
      }));
    }
    return entries.filter((entry, index, all) => entry.value !== '' && all.findIndex((item) => item.value === entry.value) === index);
  }

  function parsePluginSchemaValue(raw) {
    if (typeof raw !== 'string') return raw;
    const trimmed = raw.trim();
    if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) return raw;
    try { return JSON.parse(trimmed); } catch (error) { return raw; }
  }

  function extractPluginSchemaDefinitions(rawSchema) {
    const parsed = parsePluginSchemaValue(rawSchema);
    const schema = asRecord(parsed);
    let candidates = [];
    if (Array.isArray(parsed)) {
      candidates = parsed.map((item, index) => {
        const definition = asRecord(item);
        return { key: textValue(definition.key || definition.name || definition.id, `field_${index}`), definition };
      });
    } else if (Array.isArray(schema.fields)) {
      candidates = schema.fields.map((item, index) => {
        const definition = asRecord(item);
        return { key: textValue(definition.key || definition.name || definition.id, `field_${index}`), definition };
      });
    } else if (schema.fields && typeof schema.fields === 'object') {
      candidates = Object.entries(schema.fields).map(([key, definition]) => ({ key, definition: asRecord(definition) }));
    } else if (schema.properties && typeof schema.properties === 'object') {
      candidates = Object.entries(schema.properties).map(([key, definition]) => ({ key, definition: asRecord(definition) }));
    } else if (schema.schema) {
      return extractPluginSchemaDefinitions(schema.schema);
    } else if (parsed && typeof parsed === 'object') {
      candidates = Object.entries(schema).map(([key, definition]) => ({ key, definition: asRecord(definition) }));
    }

    return candidates.map(({ key, definition }) => {
      const def = asRecord(definition);
      if (isSensitivePluginField(key, def)) return null;
      const type = pluginFieldType(def);
      const sliderRaw = def.slider ?? def.range;
      const sliderDefinition = asRecord(sliderRaw);
      const min = pluginNumberValue(def.min ?? def.minimum ?? sliderDefinition.min ?? sliderDefinition.minimum);
      const max = pluginNumberValue(def.max ?? def.maximum ?? sliderDefinition.max ?? sliderDefinition.maximum);
      const step = pluginNumberValue(def.step ?? sliderDefinition.step);
      const options = normalisePluginOptions(def.options || def.choices || def.items, def.labels);
      const slider = sliderRaw === true || (sliderRaw !== null && typeof sliderRaw === 'object');
      return {
        key,
        type,
        label: sanitisePluginText(def.label || def.title) || key,
        description: sanitisePluginText(def.description || def.desc),
        hint: sanitisePluginText(def.hint),
        default: Object.prototype.hasOwnProperty.call(def, 'default') ? def.default : undefined,
        hasDefault: Object.prototype.hasOwnProperty.call(def, 'default'),
        min,
        max,
        step,
        options,
        slider,
        sensitive: false
      };
    }).filter(Boolean);
  }

  function pluginSchemaSnapshot(data) {
    const root = asRecord(data);
    const source = asRecord(root.config);
    const schemaRaw = root.schema !== undefined ? root.schema : (source.schema !== undefined ? source.schema : root.fields);
    const valuesRaw = root.values !== undefined ? root.values : (source.values !== undefined ? source.values : (source.config !== undefined ? source.config : {}));
    const schema = extractPluginSchemaDefinitions(schemaRaw);
    const allValues = asRecord(valuesRaw);
    const values = {};
    for (const field of schema) {
      if (Object.prototype.hasOwnProperty.call(allValues, field.key)) values[field.key] = allValues[field.key];
    }
    return {
      schema,
      values,
      source: sanitisePluginText(root.source || source.source),
      apiAvailable: boolOrUndefined(root.api_available) !== false,
      restartRequired: boolOrUndefined(root.restart_required) === true,
      message: sanitisePluginText(root.message),
      error: sanitisePluginText(root.error || (root.ok === false ? root.message : ''))
    };
  }

  function pluginValueLookup(values, field) {
    const record = asRecord(values);
    if (Object.prototype.hasOwnProperty.call(record, field.key)) return record[field.key];
    return field.hasDefault ? field.default : undefined;
  }

  function pluginListText(value) {
    if (Array.isArray(value)) return value.map((item) => textValue(item, '')).join('\n');
    return textValue(value, '');
  }

  function pluginInputText(value) {
    if (value === null || value === undefined) return '';
    if (Array.isArray(value)) return pluginListText(value);
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (typeof value === 'object') {
      try { return JSON.stringify(value); } catch (error) { return ''; }
    }
    return textValue(value, '');
  }

  function pluginDefaultText(value) {
    if (value === null) return 'null';
    if (value === undefined) return '';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (Array.isArray(value)) return value.map((item) => textValue(item, '')).join(', ');
    if (typeof value === 'object') {
      try { return JSON.stringify(value); } catch (error) { return ''; }
    }
    return textValue(value, '');
  }

  function pluginConfigPath(instanceId, pluginId) {
    return `/api/instances/${encodeURIComponent(instanceId)}/plugins/${encodeURIComponent(pluginId)}/config`;
  }

  function createPluginConfigService(options = {}) {
    const api = options.api;
    if (!api || typeof api.request !== 'function') {
      throw new Error('pluginConfig service requires an api client');
    }
    const ApiError = api.ApiError || Error;

    async function load(instanceId, pluginId) {
      const data = await api.request(pluginConfigPath(instanceId, pluginId));
      if (!data || data.ok === false) {
        throw new ApiError(sanitisePluginText(asRecord(data).message || asRecord(data).error) || '插件参数读取失败。', { data });
      }
      return { data, snapshot: pluginSchemaSnapshot(data) };
    }

    async function save(instanceId, pluginId, values) {
      const data = await api.request(pluginConfigPath(instanceId, pluginId), {
        method: 'POST',
        body: { values }
      });
      if (!data || data.ok === false) {
        throw new ApiError(sanitisePluginText(asRecord(data).message || asRecord(data).error) || '插件参数保存失败。', { data });
      }
      return data;
    }

    return Object.freeze({
      load,
      save,
      pluginSchemaSnapshot,
      extractPluginSchemaDefinitions,
      isSensitivePluginField,
      pluginConfigUnsupportedMessage,
      sanitisePluginText,
      pluginValueLookup,
      pluginListText,
      pluginInputText,
      pluginDefaultText
    });
  }

  const namespace = global.QQBotStudio = global.QQBotStudio || {};
  namespace.services = namespace.services || {};
  namespace.services.pluginConfig = Object.freeze({ createPluginConfigService });
})(typeof window !== 'undefined' ? window : globalThis);
