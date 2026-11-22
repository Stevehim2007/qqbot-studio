# 需求 brief：任意插件参数按群覆盖（框架阶段输入）

日期：2026-11-22
基线：主项目 `5.0.0-beta.31`（`19186e0` / `92826b1` / `c5947b6`，未 push）；插件仓库 `v4.4.0-beta.63`（`8a9277e`）。
线上：`/home/ubuntu/qqbot-studio` 运行 beta.30；bot-1/4/7/11 实测 core 4.3.12、safe_tools 3.3.5、memory_commands 4.3.3（`aux.py` 均含 `from . import aux_ai` 修复）。

> 用途：交给固定的框架 session 产出完整 Markdown 框架，**不写业务代码、不改仓库文件**。按 `AGENTS.md` / `docs/FRAMEWORK_FIRST.md` 流程。

## 1. 背景：上一版做了什么、为什么不够

`19186e0` 已在插件参数编辑器每个字段旁加了「加入群策略」按钮，登记 `registered[plugin_id][config_path] = policy_param`，群策略页可据此展示并覆盖该参数。`92826b1` 进一步把它改成诚实文案（不能覆盖的显示「仅全局」+ 原因）并显示「可按群覆盖 N/M 个参数」。

但实测覆盖率只有 **16 / 542**（编辑器中真实可按群覆盖的参数）：

| 插件 | 可覆盖/编辑器参数 | 插件 | 可覆盖/编辑器参数 |
|---|---|---|---|
| qqbot_core_reply | 8/119 | qqbot_web_reader | 0/38 |
| qqbot_stickers | 5/108 | qqbot_image_search | 0/52 |
| qqbot_food | 2/94 | qqbot_expression_state | 0/8 |
| qqbot_memory_commands | 1/46 | qqbot_scheduler / fortune / weather | 0/7、0/4、0/6 |
| qqbot_safe_tools | 0/77 | | |

原因不在登记机制，而在运行时：群覆盖值只进入 `policy_effective().params`，而插件只有显式读 `policy_params.get(x)` 的那几处才吃覆盖（core_reply 的 `participation.*`、food 的 `default_city/max_results`、stickers 的 `ai_decoration_probability/decoration_probability`），其余参数直接读自身 config，**写了覆盖也不会生效**。

## 2. 目标（用户原话：插件里每个参数都有可能被加入群策略）

R1 运行时：插件配置的**每个标量参数**都能被群覆盖并真实生效；生效范围、优先级、审计、回滚与现有 `capability_policy` 一致。
R2 安全：敏感参数（Key/密钥/白名单/凭据/管理员）永远不允许群覆盖；缺省为拒绝。
R3 Studio：编辑器里的「加入群策略」对可覆盖参数一律可用；不可覆盖的必须说明原因，不允许出现"点了没反应"。
R4 兼容：不改变现有 16 个参数的语义与已有 `selected/marked/registered` 数据结构；旧 prefs 文件、旧插件版本（无 overlay 能力）都要正常工作。

## 3. 非目标

- 不做按群覆盖插件"启用/停用"之外的权限模型改造（现有 per-group enabled 保持）。
- 不做 embedding/记忆类参数的特殊处理。
- 不在插件之间引入共享第三方包（每个插件仍独立 ZIP、独立加载）。
- 不做 AstrBot 本体补丁或改其配置读写。

## 4. 现状事实（已实测，框架必须据此设计）

### 4.1 插件配置读取的汇聚点

| 插件 | `self.config.get(` 直接调用 | `_cfg(...)` 汇聚点调用 | 现有 config 封装 |
|---|---|---|---|
| qqbot_core_reply | 0 | 0 | `RuntimeConfig`/`ConfigReader`（`config.py:176`/`:362`） |
| qqbot_stickers | 0 | 192 | `_cfg`（`main.py:360`） |
| qqbot_image_search | 0 | 71 | `_cfg` |
| qqbot_food | 0 | 52 | `_cfg` + `PluginConfig`（`config.py:119`） |
| qqbot_safe_tools | 0 | 49 | `_cfg` |
| qqbot_web_reader | 0 | 39 | `_cfg` |
| qqbot_memory_bank | 0 | 23 | `_cfg` |
| qqbot_memory_commands | 17 | 0 | 无（直接 `self.config.get`） |
| qqbot_expression_state | 0 | 9 | `_cfg` |
| qqbot_weather | 0 | 6 | `_cfg` |
| qqbot_fortune / qqbot_scheduler | 0 | 0 | 无（低频，读取方式需框架确认） |

即 9/12 插件已有单一读取汇聚点，只有 memory_commands、fortune、scheduler 需要新增汇聚点。

### 4.2 参数 schema 来源

- 每个插件目录下的 `_conf_schema.json`（与 `main.py` 同级，线上已确认存在）：字段含 `type`（bool/int/float/string/text/list/object）、`default`、`slider.{min,max,step}`、`description`、`ui_group`、部分含 `options`。
- 类型统计：core_reply `{bool:32,string:14,float:7,int:42,list:9,text:15}`、stickers `{bool:22,float:6,text:38,int:30,string:11,list:1}`、food `{bool:7,int:38,text:49}`、safe_tools `{bool:17,int:33,string:27}` 等。
- 安全策略侧：`qqbot_safe_tools/capability_policy.py` 的 `PLUGIN_PARAM_SPECS`（手写，10 个插件共约 30 条）、`ParamSpec(kind, minimum, maximum, max_chars, enum, group_overridable=True, sensitive=False, default, config_key, legacy_key)`、`_validate_params` 对 `sensitive` 或 `not group_overridable` 抛 `PolicyError("sensitive_param")`、`param_schema(plugin_id)` 返回 `fields{name:{...}}`、`effective()` 只从策略库合并 global/private/template/group。
- Studio 侧：`deployer.get_plugin_config(plugin_id)` 提供 schema+values；`GET /api/instances/<id>/group-param-schema/<plugin_id>` → `policy.param_schema`；`GET /api/instances/<id>/group-policies/<type>/<id>` → `policy.scope`（返回所有插件的 schema 供群策略页渲染）。
- group policy prefs：`studio-group-policy-prefs.json`，`{selected, marked, registered, updated_at}`，`deployer.py:288 _clean_policy_pref_map` + `_clean_policy_registry_map` 清洗，`0600`。

### 4.3 加载与调用上下文

- AstrBot 以顶层模块方式加载插件，故 `qqbot_memory_commands/aux.py` 用 `try: from . import aux_ai except ImportError: sys.path.insert(...); import aux_ai`。任何新增模块必须同样兼容两种导入方式。
- 现有消费 policy 的调用点：`qqbot_core_reply/reply_pipeline.py`、`qqbot_food/main.py:857`、`qqbot_stickers/main.py:2721`、`qqbot_memory_commands/main.py`。
- 每个群消息处理是独立 asyncio 任务；后台任务（定时、维护）没有群上下文。

## 5. 候选方案（框架需评估/取舍，不要直接照抄）

- **S1 每个插件加"策略覆层 + 汇聚点注入"**：插件内新增 `policy_overlay.py`（contextvar 保存 `{plugin_id, group_id, revision, values, creator_task}`），在消息处理入口处用 `policy_effective(...)` 取本群参数并开作用域；`_cfg` 先查覆层再查 `self.config`。覆层只对**创建它的 asyncio 任务**生效，后台任务自动回退全局配置。
- **S2 自动生成 spec**：`safe_tools` 从兄弟目录 `_conf_schema.json`（`Path(__file__).parent.parent/<plugin_id>/_conf_schema.json`）生成 scalar `ParamSpec`，命名直接用配置键（扁平点号键），`group_overridable=True`、`sensitive=` 按键名启发式、复杂类型（list/object）不生成；缺失文件时退回手写表（bot-11 只装了 7 个插件，必须容错）。
- **S3 Studio 侧**：用自动生成后的 `policy.param_schema` 决定按钮可用性；`registered` 从"绕过白名单的唯一途径"变成"让群策略页默认只显示运维关心的参数"的名单机制（否则 `selected=null` 会一次渲染 542 行）。
- **备选 S4**：不引入 contextvar，改为把 params 显式透传到各读取点 —— 需要改数百处，除非框架能给出自动化且可测的证据，否则不建议。

框架需要明确：覆层作用域边界、任务继承风险、与现有 16 个 `config_key` 映射的兼容（点号键 vs 语义名的优先级）、spec 生成的可信度（schema 会不会包含不该暴露的参数）、以及 `policy.scope` 负载从 ~30 项涨到 ~570 项后的性能与 UI 分页。

## 6. 现有功能保留清单

- 群策略页现有：按插件分组、启用开关、参数标记（★）、模板、审计、revision 冲突检测、`selected/marked/registered` 语义。
- 编辑器现有：字段分组/搜索、默认值徽章、已修改徽章、保存/重置、辅助 AI 设置、移动端布局。
- `capability_policy` 现有：手写 30 条 spec 的校验、`sensitive_param`/`unknown_param`/`unknown_plugin` 错误语义。
- 插件独立打包：每个 ZIP 不依赖其它插件的源码文件；`safe_tools` 缺失时其余插件仍能工作（降级为全局配置）。

## 7. 验收命令（框架需补齐每组用例）

```
PYTHONPATH=. python3 -m unittest discover -s tests -p "test_deployer.py" -q
PYTHONPATH=. python3 -m unittest discover -s tests -p "test_server.py" -q
PYTHONPATH=. python3 -m unittest discover -s tests -p "test_memory_policy_ui_static.py" -q
PYTHONPATH=. python3 -m unittest discover -s tests -q          # 全量（发布前）
cd .qqbot-plugins-staging/plugins/qqbot_safe_tools && python3 -m pytest -q
cd .qqbot-plugins-staging/plugins/qqbot_core_reply && python3 -m pytest -q
```

线上验收（只读 + 一次写入）：群策略页对本群改一个**原本不可覆盖**的参数（如 `qqbot_food.max_results` 之外的新键）→ bot 行为变化 → 审计有记录 → 重置恢复。

## 8. 风险

- contextvar 泄漏到后台任务 → 群覆盖串到别的群/私聊。
- schema 与运行时键不一致（schema 有、代码不读；或代码读、schema 无）→ UI 说能覆盖但无效。
- 敏感参数误判（例如 `owner_qq`、`proxy`、`session`）→ 群管理员改到安全边界。
- `policy.scope` 与 `param_schema` 负载膨胀（570 项 × 多实例）→ 控制台首屏慢。
- 12 个插件重复实现覆层 → 行为漂移、测试成本。
- 旧插件版本（无 overlay）遇到已登记的 prefs → 静默不生效。

## 9. 禁止修改

- 不把密钥/Token 写进日志、prefs、审计、测试夹具。
- 不改 AstrBot 本体与其 `cmd_config.json` 写入路径；不把群覆盖写回插件配置文件（必须留在策略库）。
- 不破坏插件独立 ZIP 结构、不引入跨插件 import。
- 不删除现有 `selected/marked/registered` 字段与其兼容逻辑。

## 10. 要求输出（Markdown 框架，落盘 `workbuddy-local/plugin-param-group-override-framework.md`）

1. 最终架构（组件、职责、数据流、与现有 policy/prefs 的关系）
2. 分阶段 v1/v2/v3（v1 建议先 3 个高价值插件 + spec 自动生成打通端到端）
3. 文件级改动清单（主项目 / safe_tools / 12 个插件，标注新增 vs 修改）
4. 数据/配置迁移（prefs、policy store 的 schema 版本、旧 prefs 与旧插件兼容）
5. 生命周期与回滚（生效点、缓存失效、revision、回滚到 beta.31 的步骤）
6. 测试矩阵与 E2E 场景（含 contextvar 泄漏、缺 schema、缺 safe_tools、敏感拒绝、负载）
7. 风险与未决问题（明确需要人工决策的点，例如哪些键名算 sensitive、`selected=null` 的新语义）
