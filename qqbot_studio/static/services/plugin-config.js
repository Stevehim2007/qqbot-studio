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

  const PLUGIN_SCHEMA_METADATA_KEYS = ['_ui_groups', 'ui_groups', 'uiGroups', '$schema'];

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
      candidates = Object.entries(schema)
        .filter(([key]) => !PLUGIN_SCHEMA_METADATA_KEYS.includes(key))
        .map(([key, definition]) => ({ key, definition: asRecord(definition) }));
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
        uiGroup: sanitisePluginText(def.ui_group || def.uiGroup || def.group || def.category),
        uiSubgroup: sanitisePluginText(def.ui_subgroup || def.uiSubgroup || def.subgroup),
        sensitive: false
      };
    }).filter(Boolean);
  }

  const PLUGIN_UI_GROUPS = [
    {
      id: 'common',
      title: '常用回复',
      advanced: false,
      order: 10,
      keys: [
        'reply_probability', 'system_prompt', 'llm_timeout_seconds', 'llm_error_reply',
        'empty_reply_fallback', 'max_input_chars', 'max_reply_chars', 'min_message_chars',
        'group_cooldown_seconds', 'user_cooldown_seconds', 'ignore_pure_image',
        'ignore_pure_face', 'ignore_command_prefixes', 'inherit_astrbot_wake_prefix',
        'block_default_llm_for_group_non_at'
      ],
      prefixes: ['reply_', 'group_cooldown_', 'user_cooldown_', 'llm_', 'ignore_']
    },
    {
      id: 'image',
      title: '识图/搜索',
      advanced: false,
      order: 20,
      keys: [
        'max_images_per_message', 'max_caption_chars', 'image_caption_prompt',
        'image_unavailable_reply', 'auto_use_astrbot_caption_provider',
        'native_vision_first'
      ],
      prefixes: ['image_search_', 'image_', 'vision_', 'native_vision_']
    },
    {
      id: 'sticker',
      title: '表情包',
      advanced: false,
      order: 30,
      keys: ['pending_sticker_ttl_seconds'],
      prefixes: ['sticker_', 'collected_sticker_', 'auto_collect_', 'user_sticker_']
    },
    {
      id: 'song',
      title: '本地点歌',
      advanced: false,
      order: 40,
      keys: [],
      prefixes: ['song_']
    },
    {
      id: 'conversation',
      title: '会话/人格',
      advanced: false,
      order: 50,
      keys: ['prompt_profiles', 'group_personas'],
      prefixes: ['conversation_', 'persona_', 'group_persona', 'prompt_profile_']
    },
    {
      id: 'memory',
      title: '记忆',
      advanced: false,
      order: 60,
      keys: ['memory_mode'],
      prefixes: ['memory_', 'ambient_', 'group_identity_']
    },
    {
      id: 'food',
      title: 'Food',
      advanced: false,
      order: 70,
      keys: [],
      prefixes: ['food_']
    },
    {
      id: 'web',
      title: '网页访问',
      advanced: false,
      order: 80,
      keys: [],
      prefixes: ['web_fetch_', 'web_auto_fetch_']
    },
    {
      id: 'safe_tools',
      title: '安全工具',
      advanced: false,
      order: 90,
      keys: ['ai_unified_tool_protocol_enabled'],
      prefixes: ['safe_tools_']
    },
    {
      id: 'advanced',
      title: '高级/其他',
      advanced: true,
      order: 900,
      keys: [],
      prefixes: ['advanced_', 'debug_', 'experimental_', 'option_']
    }
  ];

  function pluginGroupSlug(value) {
    return textValue(value, '').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '');
  }

  function pluginGroupDefinitions() {
    return PLUGIN_UI_GROUPS.map((group, index) => ({
      id: group.id,
      title: group.title,
      advanced: group.advanced === true,
      order: Number.isFinite(group.order) ? group.order : 100 + index,
      keys: (group.keys || []).map((key) => textValue(key, '').trim().toLowerCase()),
      prefixes: (group.prefixes || []).map((prefix) => textValue(prefix, '').trim().toLowerCase()),
      fields: []
    }));
  }

  function normalisePluginUiGroups(rawUiGroups) {
    const parsed = parsePluginSchemaValue(rawUiGroups);
    const root = asRecord(parsed);
    const byKey = new Map();
    const groups = [];
    const groupsRaw = Array.isArray(root.groups)
      ? root.groups
      : (Array.isArray(parsed) ? parsed : []);
    groupsRaw.forEach((item, index) => {
      const record = asRecord(item);
      const title = sanitisePluginText(record.title || record.label || record.name || record.id).trim();
      if (!title) return;
      const keysRaw = Array.isArray(record.keys)
        ? record.keys
        : (Array.isArray(record.fields) ? record.fields : []);
      const group = {
        id: pluginGroupSlug(record.id || record.key || title) || `custom_${index + 1}`,
        title,
        advanced: boolOrUndefined(record.advanced) === true,
        order: 500 + index,
        keys: keysRaw.map((key) => textValue(key, '').trim().toLowerCase()).filter(Boolean)
      };
      groups.push(group);
      for (const key of group.keys) byKey.set(key, group);
    });
    const fieldMap = asRecord(
      root.fields !== undefined
        ? root.fields
        : (root.keys !== undefined ? root.keys : (root.properties !== undefined ? root.properties : {}))
    );
    const explicitFieldMap = Object.keys(fieldMap).length > 0;
    const assignKey = (rawKey, rawValue) => {
      const key = textValue(rawKey, '').trim().toLowerCase();
      if (!key || key === 'groups') return;
      const record = asRecord(rawValue);
      const title = sanitisePluginText(
        record.title || record.label || record.group || (typeof rawValue === 'string' ? rawValue : '')
      ).trim();
      if (!title) return;
      byKey.set(key, {
        id: pluginGroupSlug(record.id || record.group || title),
        title,
        advanced: boolOrUndefined(record.advanced) === true
      });
    };
    if (explicitFieldMap) {
      for (const [key, value] of Object.entries(fieldMap)) assignKey(key, value);
    } else {
      for (const [key, value] of Object.entries(root)) assignKey(key, value);
    }
    return { groups, byKey };
  }

  function groupPluginFields(schema, rawUiGroups) {
    const fields = Array.isArray(schema) ? schema : [];
    const overrides = normalisePluginUiGroups(rawUiGroups);
    const groups = pluginGroupDefinitions();
    const byId = new Map(groups.map((group) => [group.id, group]));
    const byTitle = new Map(groups.map((group) => [group.title.toLowerCase(), group]));
    const remember = (group) => {
      const created = { ...group, fields: [] };
      groups.push(created);
      byId.set(created.id, created);
      byTitle.set(created.title.toLowerCase(), created);
      return created;
    };
    for (const [index, custom] of overrides.groups.entries()) {
      const existing = byId.get(custom.id) || byTitle.get(custom.title.toLowerCase());
      if (existing) continue;
      remember({ ...custom, order: 500 + index, prefixes: [] });
    }
    const getGroup = (reference) => {
      const id = textValue(asRecord(reference).id, '').trim();
      const title = sanitisePluginText(asRecord(reference).title).trim();
      const existing = (id && byId.get(id)) || (title && byTitle.get(title.toLowerCase()));
      if (existing) return existing;
      return remember({
        id: id || pluginGroupSlug(title) || `custom_${groups.length + 1}`,
        title: title || id || '自定义分组',
        advanced: boolOrUndefined(asRecord(reference).advanced) === true,
        order: 500 + groups.length,
        keys: [],
        prefixes: []
      });
    };
    for (const group of groups) group.fields = [];
    for (const field of fields) {
      const record = asRecord(field);
      const key = textValue(record.key, '').trim().toLowerCase();
      let group = null;
      const explicit = textValue(record.uiGroup, '').trim();
      if (explicit) group = getGroup({ title: explicit });
      if (!group && overrides.byKey.has(key)) group = getGroup(overrides.byKey.get(key));
      if (!group) {
        group = groups.find((candidate) => (
          candidate.keys.includes(key)
          || candidate.prefixes.some((prefix) => prefix && key.startsWith(prefix))
        )) || null;
      }
      if (!group) group = byId.get('advanced') || groups[groups.length - 1];
      group.fields.push({
        ...record,
        groupId: group.id,
        groupTitle: group.title,
        groupAdvanced: group.advanced === true,
        groupOrder: group.order
      });
    }
    return groups
      .filter((group) => group.fields.length > 0)
      .sort((left, right) => left.order - right.order)
      .map((group) => ({
        id: group.id,
        title: group.title,
        advanced: group.advanced === true,
        order: group.order,
        count: group.fields.length,
        fields: group.fields
      }));
  }

  function pluginFieldSearchText(field) {
    const record = asRecord(field);
    return [
      record.key,
      record.label,
      record.description,
      record.hint,
      record.groupTitle,
      record.type
    ].map((value) => textValue(value, '')).join(' ').toLowerCase();
  }

  function pluginFilterSchemaFields(schema, query) {
    const fields = Array.isArray(schema) ? schema : [];
    const needle = textValue(query, '').trim().toLowerCase();
    if (!needle) return fields.slice();
    return fields.filter((field) => pluginFieldSearchText(field).includes(needle));
  }

  function pluginComparableValue(value, type) {
    const kind = textValue(type, '').trim().toLowerCase();
    if (kind === 'list') {
      if (Array.isArray(value)) return value.map((item) => textValue(item, ''));
      return textValue(value, '') === '' ? [] : [textValue(value, '')];
    }
    if (kind === 'bool') {
      const parsed = boolOrUndefined(value);
      return parsed === undefined ? Boolean(value) : parsed;
    }
    if (kind === 'int' || kind === 'float') {
      if (value === null || value === undefined || value === '') return null;
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }
    if (value === null || value === undefined) return '';
    if (typeof value === 'boolean') return value;
    if (typeof value === 'object') {
      try { return JSON.stringify(value); } catch (error) { return ''; }
    }
    return textValue(value, '');
  }

  function pluginFieldValueChanged(field, currentValue, originalValue) {
    const type = textValue(asRecord(field).type, 'string');
    return JSON.stringify(pluginComparableValue(currentValue, type))
      !== JSON.stringify(pluginComparableValue(originalValue, type));
  }

  function pluginSchemaSnapshot(data) {
    const root = asRecord(data);
    const source = asRecord(root.config);
    const schemaRaw = root.schema !== undefined ? root.schema : (source.schema !== undefined ? source.schema : root.fields);
    const valuesRaw = root.values !== undefined ? root.values : (source.values !== undefined ? source.values : (source.config !== undefined ? source.config : {}));
    const uiGroupsRaw = root.ui_groups !== undefined
      ? root.ui_groups
      : (root.uiGroups !== undefined
        ? root.uiGroups
        : (source.ui_groups !== undefined
          ? source.ui_groups
          : (source.uiGroups !== undefined
            ? source.uiGroups
            : (schemaRaw && typeof schemaRaw === 'object' && !Array.isArray(schemaRaw)
              ? (schemaRaw.ui_groups !== undefined ? schemaRaw.ui_groups : schemaRaw._ui_groups)
              : undefined))));
    const groups = groupPluginFields(extractPluginSchemaDefinitions(schemaRaw), uiGroupsRaw);
    const schema = groups.reduce((all, group) => all.concat(group.fields), []);
    const allValues = asRecord(valuesRaw);
    const values = {};
    for (const field of schema) {
      if (Object.prototype.hasOwnProperty.call(allValues, field.key)) values[field.key] = allValues[field.key];
    }
    return {
      schema,
      groups,
      uiGroups: uiGroupsRaw,
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


  // ------------------------------------------------------------------
  // Save pipeline helpers (T17 P2): complete payload, baseline refresh and
  // "make the invalid field visible" planning.  Pure functions so the console
  // wiring stays a thin adapter and the contract is node-testable.
  // ------------------------------------------------------------------
  function pluginConfigSavePayload(entries) {
    const values = {};
    const invalid = [];
    for (const entry of entries || []) {
      if (!entry || entry.legacyHidden) continue;
      const key = textValue(entry.key, '');
      const result = typeof entry.readValue === 'function'
        ? entry.readValue()
        : { ok: false, message: '无法读取该参数。' };
      if (!key || !result || result.ok !== true) {
        invalid.push({ key, entry, message: (result && result.message) || '参数值无效。' });
        continue;
      }
      values[key] = result.value;
    }
    // Every editable field (including filtered-out ones) is submitted: saving
    // must never silently drop fields the current filter hides.
    return { ok: invalid.length === 0, values, invalid };
  }

  function pluginConfigUpdatedBaselines(entries, submitted, saved) {
    const submittedValues = asRecord(submitted);
    const savedValues = asRecord(saved);
    const updates = [];
    for (const entry of entries || []) {
      if (!entry || entry.legacyHidden) continue;
      const key = textValue(entry.key, '');
      if (!key || !Object.prototype.hasOwnProperty.call(submittedValues, key)) continue;
      updates.push({
        key,
        value: Object.prototype.hasOwnProperty.call(savedValues, key)
          ? savedValues[key]
          : submittedValues[key]
      });
    }
    return updates;
  }

  function pluginConfigRevealPlan(entry, uiState) {
    const ui = asRecord(uiState);
    const groupId = textValue(asRecord(entry).groupId, '').trim() || 'advanced';
    const activeGroup = textValue(ui.activeGroup, 'all') || 'all';
    return {
      clearSearch: true,
      modifiedOnly: false,
      activeGroup: activeGroup === 'all' || activeGroup === groupId ? activeGroup : 'all',
      expandGroup: groupId,
      scrollIntoView: true
    };
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
      groupPluginFields,
      normalisePluginUiGroups,
      pluginFilterSchemaFields,
      pluginFieldSearchText,
      pluginFieldValueChanged,
      PLUGIN_UI_GROUPS,
      extractPluginSchemaDefinitions,
      isSensitivePluginField,
      pluginConfigUnsupportedMessage,
      sanitisePluginText,
      pluginConfigSavePayload,
      pluginConfigUpdatedBaselines,
      pluginConfigRevealPlan,
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
