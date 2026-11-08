# 更新日志

本项目使用独立版本线：

- Linux 主项目：`qqbot_studio.__version__`
- QQ 增强插件：`plugins/qqbot_enhancer/metadata.yaml`
- Windows 发行版：Windows 仓库独立版本，内嵌 Linux core 版本单独记录
- AI 助手 B：独立仓库 `qqbot-studio-ai-assistant`

## v4.4.0-beta.5（测试版）— 修复 V2 群策略 RPC 白名单

- 将 `policy.scope`、`policy.audit`、`policy.templates`、`policy.param_schema`、`policy.scope_reset`、`policy.template_set/delete/apply` 加入 Studio 固定 RPC 方法白名单，修复群策略页 400 `control_method_not_allowed`。
- 主项目测试 848 tests OK；插件侧版本仍为 core 4.0.5 / safe_tools 3.2.1。

## v4.4.0-beta.4（测试版）— V2 分群策略控制面

- Studio 新增“群策略”分类：群/私聊 scope 查询、插件启停、字段级参数覆盖、模板应用、整群重置、审计记录。
- 新增 `/api/instances/{id}/group-policies/*`、`/group-param-schema/*`、`/group-policy-templates/*`，支持 GET/POST/PUT 与 revision CAS。
- safe_tools 新增 `policy.scope/scope_reset/audit/templates/template_set/template_delete/template_apply/param_schema` 控制方法。
- core_reply 新增 `/分群 状态|开启|关闭|重置|模板|概率` 群管理员命令；默认关闭 `group_admin_override_enabled`，开启后仅允许本群非敏感参数。
- 插件版本：core_reply 4.0.5、safe_tools 3.2.1；主项目测试 `848 tests OK`。

## v4.4.0-beta.3（测试版）— 对齐插件控制桥 RPC 协议

- Studio 控制桥请求改为 safe_tools 实际使用的 `digest`/`hmac`/单 scope 协议，响应解析支持插件的 `{ok, request_id, result}`。
- 保留本地模拟控制服务器的签名响应解析，测试覆盖两种响应。
- 生产实连验证：`/aux-budget`、`/aux-budget/usage`、`/plugin-policy` 均返回 200；预算写入与读取正常。

## v4.4.0-beta.2（测试版）— 修复控制桥 RPC 路径

- 修复 Studio 控制桥默认端点从 `/control` 改为插件实际监听的 `/rpc`，解决 `/aux-budget`、`/plugin-policy` 全部 503 的问题。
- 保留 `/control`、`/v1/control` 为兼容允许路径；新增默认端点契约测试。

## v4.4.0-beta.1（测试版）— V1 分群能力策略与辅助预算底座

- 新增 `capability_policy.json` v2 侧车：实例默认 < 群模板 < 群/私聊覆盖，字段级合并、revision CAS、原子写和审计。
- 新增 `aux_budget.json` + `aux_ledger.db`：五档预算（off/light/standard/advanced/full）、13 项辅助功能、reserve/commit/release、80% 省电、100% 停止、跨月 cycle 和幂等 request_id。
- safe_tools 新增 `policy.*`、`aux_budget.*` 控制方法和 Studio 辅助模型/预算 API；设置页可切换档位并单独开关 13 项功能。
- core_reply/food/stickers/memory_commands 接入分群策略门控；Food 的 `default_city` 只在群位置推断失败后兜底。
- 测试通道改为自动发现最新 `alpha/beta/rc` 预发布标签，不再写死分支白名单。
- 插件版本：core 4.0.4、safe_tools 3.2.0、food 3.0.4、stickers 3.0.3、memory_commands 4.0.2。
- 插件仓库发布 `v4.4.0-beta.1`；主项目测试 tag 同名。

## v4.3.1（正式版）— 插件市场、七插件运行时与通用工具调用

- 主项目合并插件市场、插件分组、安装/更新/删除、SHA-256 与 ZIP 安全校验、原子替换和操作预览/确认。
- 七个自包含插件：`qqbot_core_reply`、`qqbot_safe_tools`、`qqbot_food`、`qqbot_stickers`、`qqbot_memory_commands`、`qqbot_web_reader`、`qqbot_image_search`；不再依赖 `qqbot_enhancer` 运行时。
- `qqbot_core_reply` 4.0.3：memory 模式注入 safe_tools 目录；修复工具回复后 turn 未释放导致的会话锁死；`help me ...` 不再被误识别为 `/help`。
- `qqbot_safe_tools` 3.1.1：manifest `tools.json` 自动聚合、marker 与 `qqbot_safe_tool` native bridge；provider registry 路由修复。
- `qqbot_food` 3.0.3：`长春` 可匹配 `长春市`，优先城市级地点；保留歧义与单字保护。
- `qqbot_stickers` 3.0.2：删除死配置 `sticker_probability` 与旧迁移开关，装饰概率只读 `sticker_decorate_probability`。
- `qqbot_memory_commands` 4.0.1：移除旧 `memory_commands_*` 配置 fallback，只读 canonical 键。
- 插件仓库正式发布 `v4.3.1`；主项目版本提升为 `4.3.1`。
- 合并后全仓 `841 tests OK`。

## v4.3.0（正式版）— 长期记忆与记忆管理

- 正式合并长期记忆、按群策略、记忆内容管理、记忆聊天命令、reflection、LLM 抽取、语义检索、群内说话人 scope。
- 新增/更新 Studio 记忆内容面板与插件参数保存上限（264 字段 schema 可保存）。
- 修复 memory logger 的 `plugin_tag` / `short_levelname` 日志字段，避免日志触发导致 agent 中断。
- 更新/回滚确认框移到顶部；更新后自动测试与确认清单继续生效。

## v4.2.3（正式版）— 参数与更新体验修复

- 插件字段保存上限从 256 提升到 2048，适配当前 264 字段 schema。
- 更新/回滚确认框移动到弹窗上方并保持可见。

## v4.3.0-beta.4 — 七插件最终模块化测试版

- 插件仓库发布 beta 系列：七个自包含插件 ZIP、catalog/manifest v2、ABI v1 依赖与冲突声明；safe_tools 3.0.0、stickers 3.0.0、memory_commands 4.0.0 完成。
- 主项目新增 S0 盘点、owner epoch/迁移事务/固定 RPC 控制桥；部署器不再复制 `qqbot_enhancer`，新实例提示从插件市场安装七件套。
- 新增 `scripts/migrate_qqbot_plugins.py` 与 `plugin_final_migration.py`：dry-run/apply/retire，配置字节级备份，只移动旧代码目录，保留 config/data/会话/记忆。
- 新增最终运行时门禁 `tests/test_no_legacy_runtime.py`：七插件集合、无旧包导入、部署脚本不复制旧插件、部署不产生旧插件目录。

- 记忆策略增加 `data/astrbot/memory_policy.json` 持久化 sidecar；AstrBot 重启重写 `cmd_config.json` 后策略不再丢失，面板读取 sidecar 优先。

### 记忆内容管理（Studio，t33）

- 实例设置新增「记忆内容」面板：scope 选择（group/user/conversation/persona/global）、关键词搜索、
  分页、单条详情/编辑、单条删除、按 scope 忘记、导出 JSON、导入预览（冲突/非法/上限汇总）、
  整理 dry-run/apply；不可用时明确显示原因并禁用写操作，不显示伪造的已保存值。
- 新后端模块 `qqbot_studio/memory_admin.py` + 路由：
  `GET memory/stats|records[/<id>]`、`POST memory/records`、`PATCH/DELETE memory/records/<id>`、
  `POST memory/forget|export|import|reflect`。每个操作显式 scope，无跨 scope 列表；删除/忘记/导入/整理
  必须带 `confirm`（单条=记录 id，scope 级=scope_id）。
- 安全：密钥/密码样式写入即拒绝、读取与导出再脱敏；响应不暴露数据库路径；读用 SQLite 只读 URI，
  写为单事务（失败回滚）+ `busy_timeout` 8s；导入/整理 apply 前写 0700 目录 + 0600 原子备份。
- Docker 权限降级：宿主无权限时改用 `docker exec` 在容器内执行同一批参数化语句（固定 argv、无 shell）。
- 契约：[`docs/memory-admin-api.md`](docs/memory-admin-api.md)；测试 `tests/test_memory_admin.py` 40 个用例；
  全仓 760 tests OK。

### 长期记忆聊天命令（qqbot_enhancer 2.9.0，默认关闭）

- 新增 `/记忆 状态|搜索 <关键词>|删除 <id>|忘记 本群|我的|整理`：默认
  `memory_commands_enabled=false`；仅 AstrBot 管理员、QQ 群主/管理员或
  `memory_commands_allow_user_ids` 允许名单可用；群聊只管理本群 `group` 作用域，
  私聊只管理自己的 `user` 作用域，从不 `export_all()`。
- `删除/忘记` 另需 `memory_commands_destructive_enabled=true`；`忘记` 必须显式
  `本群|我的`；未装记忆补丁、服务关闭、id 不在当前作用域、服务未确认删除/忘记时
  明确失败，不伪造成功；输出先脱敏密钥再截断，条数与总长度均可配置。
- `整理` 为纯本地 dry-run 预览（完全重复/近似重复/低价值/过期），不调用 LLM、不写库。
- 测试：`tests/test_memory_commands.py` 36 + 插件 handler 8；全仓 720 tests OK（记忆内容管理合入后 760 tests OK）。

- 测试分支：继承正式版 `4.2.0`，新增 AstrBot 原生长期记忆、按群 `conversation/long_term` 策略、Studio 记忆策略面板、参数分组面板和 stable/test 更新回退功能。
- 测试版不等同于正式版；长期记忆/策略需真实 AstrBot/Docker E2E 后才可合并。
- 主仓库测试：671 tests OK；AstrBot memory patch：125 tests OK（评审快照）。
- 美食智能（qqbot_enhancer t28）：按群默认地点 `food_group_locations`（JSON）+ 群名安全推断城市（仅显式
  `省/市/州` 后缀），「长春火锅」在已知长春群自动补城市、区县合并为完整路径，未知群仍追问；
  已有位置完整路径直接复用 `location_id`，`位置已存在`/`LOCATION_EXISTS` 只重解析并重试一次，
  不重复创建、无半截写入。

## v4.2.0 — 更新通道（稳定/测试）与同大版本任意回滚

- 控制台新增「更新与回滚」面板：当前版本/提交/大版本/工作区状态，稳定版与测试版分页。
- 稳定版只快进 `origin/main`；测试版只接受登记的测试分支/预发布标签（默认 `feat/plugin-repository-4.3`），拒绝任意分支、提交号或 URL。
- 回滚支持当前大版本内**任意**正式小版本（如 v4.2.0 → v4.0.4），列表展示标签、日期、提交与说明；跨大版本回滚拒绝。
- 切换前自动备份代码（git bundle）与实例配置/密钥文件；失败自动恢复原 ref；同一时间仅允许一个任务（锁文件），脏工作区拒绝。
- 实例数据/数据库从不被覆盖；目标版本 schema 低于本地数据时默认拒绝，需显式“允许 schema 降级”。
- 新增 `update-history.json`（from/to 版本、提交、通道、时间、结果，错误脱敏）及 `/api/update/status|versions|history|rollback`。
- 新增真实 Git 仓库测试：同大版本任意选择、跨大版本拒绝、非法 ref/URL 拒绝、脏工作区拒绝、备份与失败恢复、数据不变、历史与通道。

### 记忆策略面板（Studio）

- 修复「cmd_config.json 权限被拒 → 面板显示不支持」：本机 `EACCES/EPERM` 时自动改用 AstrBot 容器内读写
  （`docker exec`，argv 直传不经 shell；容器内写 `*.studio.tmp` → fsync → `chmod 0600` → `os.replace`
  原子替换；容器名校验，载荷上限 96 KB）。两条通道都不可用（容器未启动/容器内拒绝）时返回 503 +
  `actions`（启动实例 / chown+chmod 修权限）、`supported` 仍为 true、`saved:false`，面板显示"未保存"，
  绝不伪造已保存状态。GET 新增 `read_channel/write_channel/readable/writable/container_running/
  permission_denied/actions`（`writable` 用 `os.access(W_OK)` 实测）。错误信息统一脱敏，日志/响应不含密钥。


- 控制台实例设置新增「记忆策略」面板：默认模式（conversation/long_term）、私聊模式、
  按群增删改行（群号、模式、scope=group/user/group_user、抽取/注入开关、最大条数/字符）。
- 新增 `GET/POST /api/instances/<id>/memory-policy`（兼容 `/api/memory-policy?instance=`）：
  读写 AstrBot 的 `memory.policy`，只替换该键，其它设置与 `memory.*` 其它键原样保留，原子写 0600。
- 面板明确提示：长期记忆只对列出的群生效，未列出的群与私聊保持 conversation（只用会话历史）；
  `memory.enable=false` 时提示策略不会生效；上游没有 `memory.policy` 在线接口，保存后需重启 AstrBot
  （或重新加载配置）才生效，响应 `restart_required=true`。
- 实例没有 AstrBot 配置时显示"不支持"（POST 409），不伪造已保存值；旧后端返回 501。
- 后端校验：模式/scope 枚举、布尔开关、`max_items 1..50`/`max_chars 80..8000` 整数、群号
  4-20 位数字或 `platform:群号`、重复行/未知字段/超过 200 行一律拒绝且不写文件；令牌校验不变。
- 只读 JSON 配置，从不读取记忆数据库内容；日志/响应不含其它配置值或密钥。
- 插件参数编辑器（T17 P2）：保存成功后刷新比较基线使「已修改」标记正确清除；校验失败时清除
  搜索/分组/只看已修改过滤并滚动聚焦到出错字段；保存载荷仍包含全部 256 个字段。
- 非法存量策略如实上报（t24/t26 复查修正）：private `scope=group`、`max_items=999` 越界、未知字段、
  非法 mode/scope/类型、strip 后重复群号都会逐条进 `warnings[]`（字段+原因+降级后果），每条策略新增
  `invalid`/`errors[]`/`unknown_fields[]`/`effective_mode`/`effective_scope` 和顶层 `invalid_rows`；
  不再把 private 的 `scope=group` 静默改成 `user`，也不会把非法条目显示成生效中的 long_term
  （面板标红 + 「实际生效：conversation」，重复群号显示「未知」）。`private` 未配置时如实显示跟随默认模式。
- 测试：`tests/test_memory_policy_studio.py`（33 项）、`tests/test_memory_policy_ui_static.py`（19 项，
  含 node 契约与 256 字段回归）；文档见 `docs/memory-policy.md`。未做真实 AstrBot 进程 E2E。

## v4.1.1 — 网页智能阅读 + 饭店搜索/地址创建 + AstrBot 断连修复

- 插件 2.7.0：URL+提问消息自动抓取网页标题和正文，作为不可信参考资料注入 direct/AstrBot memory；裸链接不抓取。
- 插件 2.6.0：提交评价时自动拆分完整地址并创建缺失位置（如中国牛逼市 → 中国 / 牛逼市）。
- 插件 2.5.0：新增自然语言 `search_food_restaurants`，支持名称、标签、位置筛选和按均分/口味/性价比/评价数/最新日期排序。
- 插件 2.4.0 修复：提交草稿按会话+发送者隔离；无效提交 marker 不再出现虚假“已提交”；网页抓取内容不会二次触发工具写入。
- Linux core：AstrBot 登录/插件列表遇 Broken pipe 有限重试、强制重新登录和可操作错误提示。
- food.dlsz.top 站点补丁（日新下拉、后台删除饭店）已交付但未部署；源目录只读，部署步骤见 docs/food-ai-contract/DEPLOYMENT_NOTICE.md。
- 全量 568 tests OK。

## 控制台插件参数分组导航（Studio UI）

- 插件参数弹窗新增左侧（移动端横向 chips）粘性分组导航：常用回复 / 识图·搜索 / 表情包 /
  本地点歌 / 会话·人格 / 记忆 / Food / 网页访问 / 安全工具 / 高级其他，带字段数量与
  点击跳转；分组可折叠，高级组默认折叠。
- 分组低风险落地：Studio 内置前缀映射 + 可选 `ui_groups`/字段 `group` 元数据覆盖；
  未匹配字段进入「高级/其他」，256+ 字段全部保留，保存/校验/敏感字段过滤逻辑不变。
- 搜索支持 key/label/description/hint/分组名，字段显示「分组 › 字段」面包屑；
  无匹配/无已修改状态、自动展开命中分组；新增「只看已修改」过滤与「已修改」标记。
- 弹窗打开期间（并在同一页面内跨打开）记住选中分组、折叠状态与滚动位置；
  分组按钮支持 `aria-current`、箭头键导航、`aria-expanded`，Escape/关闭行为不变。

## v4.1.0 — 网页访问 + food.dlsz.top AI 提交

- 新增安全 AI 网站访问：`[[tool:fetch_webpage url="..." max_chars="N"]]`，固定 HTTP(S) GET、公网 IP/SSRF 防护、逐跳重定向校验、超时/大小/内容类型限制。
- 新增自然语言美食提交：`submit_food_review`，固定 `https://food.dlsz.top/api/upload`；店名/位置/味道分/性价比分/评价必填，缺项追问，不提交不完整数据。
- 密钥默认发送者 QQ，只注入 POST body；日期不提取，站点自动当天；重复饭店/位置/用户名错误映射为中文。
- 群聊只在食物相关消息/图片时参与；普通聊天不写生产数据；异食癖吐槽遵循安全边界。
- food.dlsz.top 站点位置搜索“日新”下拉点击修复 + 管理员删除饭店 API/UI 已交付为补丁；源目录只读，未部署。
- fetch 网页工具注入防护、非法 food marker 假成功修复；全量 504 tests OK。

## qqbot_enhancer 2.7.0（插件）

- 新增智能网页阅读：消息同时含公开 http(s) 链接与提问/阅读语境时（如“根据这个文章，怎么安装 REFramework
  <链接>”），自动用既有 `web_fetch.fetch_page_text` 读取该页，把标题+网址+正文摘录（默认 4000 字）作为
  **外部不可信参考**注入 direct system prompt 与 astrbot memory `on_llm_request`；不再只复述链接。
- 触发边界：`web_auto_fetch_enabled`、`web_auto_fetch_question_only`（默认仅提问语境）、
  `web_auto_fetch_max_urls=1`（最多 3）、`web_auto_fetch_max_chars=4000`、`web_auto_fetch_timeout_seconds=12；
  裸链接永不自动读取；群聊/私聊同一判断。
- 安全/成本：复用 SSRF/私网/元数据地址拦截、DNS 固定、逐跳重定向、大小/类型/超时限制；无 Cookie、无 JS、
  无浏览器、无文件/命令；页面正文已经 HTML 提取且 `[[` 中和为 `[ [`；同一 URL 成功缓存 5 分钟、失败 60 秒，
  同一事件只抓一次；自动阅读不依赖也不强制 `fetch_webpage` 工具。
- 失败时注入固定中文原因并要求如实告知（不编造）；`/option` 显示智能阅读状态；插件版本 2.7.0。
- 测试：新增 TestWebAutoFetch 16 例（direct/群聊触发、裸链接不触发、question_only 关闭、两链接只取一个、
  错误/超时、SSRF 不触达本地服务、本地文章端到端 HTML 提取、截断、去重缓存、`[[tool:` 中和、
  不强制工具 catalog、memory 幂等注入、/option）；插件 313 / 全仓 568 tests OK。
- 真实 CSDN 链接仅做 stdlib GET 探针；AstrBot / DeepSeek / QQ E2E 未验证。

## qqbot_enhancer 2.6.0（插件）

- `submit_food_review` 支持完整地址自动拆分与缺失位置创建：`中国牛逼市` → 已有“中国”下新建“牛逼市”；
  `中国/吉林省/长春市/南关区` 完整存在 → 直接用 location_id；未知前缀 → 整段作为 new_location_name。
  纯函数解析支持 `/ \ > → - —` 分隔符与 省/市/区/县/镇/乡/街道/路/街/州 后缀、`中国` 特例、长度上限、
  URL/控制字符拒绝与原文回退；仍只用现有 `GET /api/locations/search` + `POST /api/upload`，不新增站点端点。
- `位置已存在` 竞态：重新解析一次，拿到 id 才用 location_id 重试一次；仍不存在则回“位置已存在”，不重复建点。
  歧义/不安全输入中文追问、不部分写入；只有明确添加意图且字段齐全才解析/建点；密码/日期规则不变。
- 工具 catalog 提示 AI 可直接传完整地址，用户无需手动拆分；新增 `food_submit_location_max_parts` /
  `_part_max_chars` 配置（DEFAULTS/schema 251/251）。
- 测试：TestFoodLocationPath 12 例（纯解析/分隔符/中国特例/完整路径/缺失链/未知前缀/歧义/URL/
  字段缺失不建点/竞态重试/只重试一次/catalog）；插件 297 / 全仓 552 tests OK。

## qqbot_enhancer 2.5.0（插件）

- 新增自然语言餐厅搜索 `search_food_restaurants`（无需 slash）：固定 `https://food.dlsz.top` 的三个只读 GET
  （`/api/locations/search`、`/api/tags/search`、`/api/restaurants`），支持店名/多标签/地点组合、
  标签与地点精确匹配（歧义或不存在只追问，不发错结果），本地按均分/口味/性价比/评价数/店名/最新日期排序，
  `limit` 1–5 硬上限，输出店名/地点/标签/三项均分/评价数/最新日期 + 站点链接。
- 安全：复用 `/food` 的 HTTPS/host/path 校验、重定向校验、超时与响应大小；工具无 URL 参数、不 POST、不额外调用 LLM；
  一次回复最多一个工具；direct 与 astrbot memory 同一套安全 catalog；`/option` 显示搜索状态。
- 新增 15 个配置键（schema 与 DEFAULTS 234→249 同步）；插件版本 2.5.0。
- 测试：新增 TestFoodSearch 17 例（店名/标签/地点/组合、全部排序、limit、歧义/未知标签地点、无结果、
  错误/超时、固定 host/SSRF、direct+memory marker、一次调用、无 POST）；插件 285 / 全仓 527 tests OK。

## qqbot_enhancer 2.4.0（插件）

- 新增 AI 自然语言美食提交：群里自然说“帮我加到 food/记录评价”，AI 用 `submit_food_review`
  marker 提取店名/位置/味道分/性价比分/评价/标签，插件按 t1 契约 POST `https://food.dlsz.top/api/upload`。
- 写入门槛：只有明确添加意图或待补全草稿才写；普通聊天不写生产数据；缺字段中文追问、分数 0.1–5 校验、
  一次回复最多一次提交、重复用户名不重试；站点错误按 v1.1 `code`/v1 文本映射为可操作中文。
- 密钥默认发送者 QQ，由插件注入请求体，不进入 prompt/日志/回复；日期不提取，站点默认当天；用户名默认群昵称。
- 群聊非 @ 只对美食消息/图片参与（概率/冷却/最短字数），与 ambient 概率互斥不双触发；无文字美食图只追问不提交。
- direct 与 AstrBot memory 双模式注入幂等工具 catalog；`/option` 显示美食提交与美食话题状态。
- 新增 `docs/food-ai-submission.md`（契约/安全/测试证据/猫娘群 Bot 提示词）。
- 安全修复（t4 P1）：fetch_webpage 返回文本中和 `[[`、工具结果标记 `qqbot_enhancer_tool_processed`
  且 `on_decorating_result` 不再二次解析，恶意网页无法注入 `submit_food_review` 写入；
  被禁用/参数非法/字段超长/多条 marker 的提交工具整条替换为固定中文说明，不再保留模型“已提交”文字。
- 草稿隔离与无身份 fail safe：提交草稿按「会话 UMO + 发送者 ID」隔离，同群多用户交错补字段/确认
  不会串店名、分数、评价、用户名或密码；同一发送者跨群隔离；换店名重置旧草稿；TTL 过期清理；
  发送者 ID 为空时不建草稿、不写 cooldown、不提交，回固定中文说明。
- 全量 510 tests OK。

## v4.0.4

- 修复 AstrBot memory 结果链 marker 丢失 extra 时 `send_sticker` 不发图的问题。
- `on_decorating_result` 现在无论 memory extra 是否存在，只要发现 tool marker 就执行白名单工具并替换为真实附件。
- 失败时 marker 不残留，显示原因/可用分类；`/表情状态` 记录最近工具失败。
- 安全模糊匹配 category/tag/meaning，仅接受唯一最佳候选。
- 全量 453 tests OK。

## v4.0.3

- 内置 `qqbot_enhancer` 优先使用 bundled schema；实例 schema 权限拒绝/半写/损坏/空时自动回退并重试。
- 保存插件参数触发 AstrBot 热重载后，再次打开参数页不再因 schema 暂时不可读而阻塞。
- 移除 V1 根目录自动导入/移动；V1 文件不再被改写，当前 v4 实例保持不变。
- 全量 450 tests OK。

## v4.0.2

- 合并非 @ 群聊双概率：`reply_probability` 成为唯一普通参与概率。
- ambient 开启时只走 ambient 单一决策，未命中不再 fallback 旧 random；ambient 关闭才走 generic。
- `ambient_group_reply_probability` 弃用，旧配置不再叠加；关键词命中概率保留为独立分支。
- 全量 444 tests OK。

## v4.0.1

- 向导增加步骤下拉菜单。
- 部署完成后环境/账号/部署前三步锁定，只保留“完成与运行”；刷新后仍保持完成页。
- 保留提示词只读、服务控制、扫码和日志功能。
- 全量 441 tests OK。

## v4.0.0 — 2026-10-31

### 会话语义

- 一个 AstrBot conversation 绑定一个提示词、一个 persona 和一段独立记忆。
- 提示词列表改为模板库；置顶模板只作为新建会话的默认模板。
- 切换/置顶提示词不再触发 pending 或自动重建当前会话。
- `/新会话 名称 [提示词标题]` 创建会话、立即切换并绑定对应 persona。
- `/会话 名称` 只切换已有会话，使用目标会话自己的提示词/persona/记忆。
- `/会话列表` 显示标题、提示词、当前 x / 上限 y。
- 达到 `conversation_max_per_group`（默认 10）时，新建会话自动删除最老的非当前会话；失败则中止创建。
- `/删除会话 名称|短ID` 支持安全删除；删除当前会话前先切换。
- 向导中的机器人提示词改为只读，模板统一在控制台模板库管理。

### 配置与权限

- 插件配置损坏时按 `.bak` → 内存缓存 → Studio 镜像 → schema 默认值自动恢复。
- 损坏文件保留并生成 `.corrupt-*` 快照；显式保存才修复主文件。
- 记忆模式不再因一次解析失败而被重置为 `direct`。
- AI 工具权限读取失败保留上次有效 UI 值，并提供刷新重试。
- 提示词模板容量上限统一为 100 个 × 12000 字符，序列化 JSON 上限 2.6M，专用请求体 8 MiB。

### 群聊与表情

- 群聊身份上下文：昵称/QQ/群号只用于区分成员，不直接输出。
- ambient 群聊默认关闭；开启后低频参与、@ 必回、官方 @ 组件优先，失败降级 `@昵称`。
- ambient 全群上下文仅群聊注入；私聊不注入群聊文案。
- `/表情状态`/`/表情诊断` 显示收藏开关、权限、数量、大小限制、最近媒体和 AI 工具注入状态。
- AstrBot memory 模式注入短表情工具目录，修复模型误称“没装表情素材”；工具 marker 实际发送一张本地表情。
- 提示词模板文件名/内容变化按 mtime 热读取，改名后无需重启即可被下一轮 `/新会话` 识别。
- 收藏表情全局共享、支持多分类、语义标签 `tags/meaning/tone`。
- 统一 AI 工具 marker；一次回复最多一条文字 + 一个附件。

### 其它

- `/food` 接入 `food.dlsz.top`，固定 HTTPS、最多 5 家、不额外调用 LLM。
- 提示词换行保留；SnowLuma `#sl` 默认关闭；插件 schema 缺失回退主项目内置 schema。
- Ponytail 第二阶段低风险精简：源码/测试净减少约 195 行，新增运行依赖 0。
- Linux 真实容器 E2E 已覆盖 AstrBot 4.29.3 + SnowLuma 2.2.1、三端口、0.0.0.0、WebUI/noVNC 200、OneBot WebSocket 握手；QQ 扫码仍需人工。

### 验证

- Linux：438 tests OK。
- 插件：205 tests OK。
- 真实 Linux Docker E2E：AstrBot/SnowLuma/OneBot 自动部分通过；QQ 扫码仍需人工。

## v3.13.0

- 会话级提示词/persona/记忆基础语义。
- 提示词模板库、`/新会话`、`/会话列表`、pinned 默认模板。
- 损坏配置备份恢复、AI 权益/记忆模式读取稳定性。
- 表情保存诊断。

## v3.12.1

- Ponytail 第二阶段优化与最终 QA。
- 共享前端 API 工具、`gitutil.py`、测试 fixture 去重。

## v3.12.0

- 提示词模板/按名会话/ambient 群聊。
- QA P1 修复：容错、persona 冲突、私聊边界、容量上限。
- Linux Docker/AstrBot/SnowLuma E2E harness。

## v3.11.0

- 提示词列表、`/新会话`、ambient 低频参与、群身份上下文。

## v3.10.1

- AstrBot memory 模式当前发送者身份注入。
- 群 Persona/Conversation 官方 API 接入。

## v3.10.0

- AstrBot 每群独立 Persona/会话记忆。
- 群身份上下文、会话安全切换。

## v3.9.1

- Ponytail 第一阶段低风险清理。
- 配置/统计/提示词可靠性修复。

## v3.9.0

- `/food` 接入 food.dlsz.top。
- 群人格、schema 回退、`#sl` 关闭、提示词换行。
- AI 工具权限控制台入口。

## 更早版本

Linux v3.8.0 及更早：参见 Git 历史、`docs/qa-report-v2.md` 与各版本 tag。

Windows 发行版独立维护，正式 Release 与内嵌 core/plugin 版本以 Windows 仓库 README/VALIDATION 为准。
