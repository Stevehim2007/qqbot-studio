# Ponytail full 全仓审计与低风险精简

> 审计基线：`6ed41b9b281e`（`main`，v3.9.0）。
> 规则：DSH Ponytail `full` + `ponytail-audit`：先理解真实调用链，再按
> YAGNI → 已有代码复用 → stdlib/native → 已有依赖 → 一行化 → 最小实现
> 的顺序处理；本报告只处理过度工程和复杂度，不把正确性/安全/性能问题伪装成清理项。
>
> 范围：`qqbot_studio/`、`qqbot_studio/static/`、`plugins/qqbot_enhancer/`、
> `tests/`、`docs/`；Windows 独立仓库只读审计，未修改。
> `docs/ponytail-audit.md` 的路径/行号中，**已删除项使用 HEAD 行号**，其余使用当前工作树行号。

## 0. 结论先行

- **没有发现应为了“少代码”而移除的 P0 安全/数据边界**。token、Host 校验、SSRF 防护、
  API Key 脱敏、0600/0700 权限、Docker argv、v1/v2 迁移、sticker marker 兼容和
  前后端双校验都保留。
- 已实施：**13 个文件，+16/-145 行，净减 129 行**。其中源码 11 个文件净减 128 行，
  测试 2 个文件净减 1 行；**新增文件 0、删除文件 0、新增依赖 0**。
- 删除内容仅为：无引用的私有 helper/包装器、无引用的常量、未使用 import、重复的
  固定 regex 常量，以及一个由既有常量派生的情绪集合；没有改 API、数据格式、安全策略、
  插件功能或 Windows release。
- 保留项中仍有一个**值得后续处理但不应本轮进行**的边界：前端 `app.js` /
  `console.js` / `services/*.js` 的工具函数重复，以及前后端插件 schema 双校验。
- 当前全量测试：**357 tests OK（26.257s）**；插件专项：**161 tests OK（0.502s）**。
- Windows 独立仓库当前 `UPSTREAM.md` 记录的上游 commit 是 `6ed41b9`，本轮主仓库的
  Ponytail 改动提交后需要按 Windows 仓库既有 sync workflow 重新同步；本轮没有改它。

## 1. 审计基线、方法与规模

| 项目 | 规模（当前工作树） | 审计重点 |
|---|---:|---|
| 后端 Python：`qqbot_studio/*.py` | 10,248 行 | 聚合模块、兼容别名、部署/存储 helper |
| 前端：`qqbot_studio/static/*.js` + `*.html` | 7,856 行 | 经典脚本、重复 DOM/格式化 helper、无构建依赖 |
| 增强插件：`plugins/qqbot_enhancer/*.py` | 8,189 行 | marker、收藏/搜索、配置读取、私有包装器 |
| 测试：`tests/*.py` | 11,500 行 | 重复 fake/fixture、测试夹具边界 |
| 文档：`docs/*.md` | 4,918 行 | 历史文档、重复教程、过期计数 |
| 运行时依赖 | **0 个第三方 Python runtime 包**；前端无 CDN/打包器 | 是否存在可删依赖 |

审计动作：

1. 读 `docs/architecture-modularization.md`、行为/接口边界和历史兼容目标；
2. 用 AST/文本引用扫描查找未使用 import、无引用私有 helper、未使用常量；
3. 逐项追踪真实调用链和测试引用；只有“全仓无引用 + 无兼容注释 + 有直接替代”的叶子项才删除；
4. 每批删除后跑相关测试，最后跑全量测试、语法检查、JSON schema 检查和 Markdown 链接检查；
5. Windows 独立仓库只做只读比对（vendor 核心、sync allowlist、版本/上游 commit）。

## 2. 已实施：低风险、行为不变的删除/简化

### 2.1 精简汇总

| 路径 | HEAD 行号/符号 | 替代 | 风险/验证 |
|---|---|---|---|
| `plugins/qqbot_enhancer/main.py` | 65/102 `COLLECTED_FILENAME_RE` import；118-131 两个无引用自然语言 regex；1605-1613 `_has_at_all`；1848 `_is_media_ref`；2459-2475 三个无引用 relevance wrapper；2595-2597 `_search_image_urls`；2944-2945 `_detect_image_magic`；3412-3414 `_extract_sticker_ai_marker`；4639-4642 `_collected_index_path`；4734-4742 `_collected_file_path_for`；4769-4770 `_local_image_ref_path`；4973-4974 `_image_dimensions`；5056-5057 `_parse_ai_sticker_judgement` | 已导入的 `media_is_media_ref`、`media_local_image_ref_path`、`detect_image_magic`、`image_dimensions`、`parse_ai_sticker_judgement`、`extract_sticker_ai_marker`；无替代定义则删除 | 全仓无调用；插件 161 tests 通过；API/消息路径不变；main.py 净减 91 行 |
| `plugins/qqbot_enhancer/media.py` | 26-33 `IMAGE_MIME_EXTENSIONS` | 无；`detect_image_magic()` 已返回 mime/扩展名 | 全仓无引用；删除 8 行 |
| `plugins/qqbot_enhancer/safe_tools.py` | 14 `from typing import Any` | 无 | AST 确认未使用；删除 1 行 |
| `plugins/qqbot_enhancer/sticker_reclassify.py` | 23-32 `KNOWN_EMOTIONS` 字面量 | 从已有 `EMOTION_KEYWORDS` 派生 | 集合值等价；插件测试通过；净减 9 行 |
| `qqbot_studio/static/console.js` | 78-81 `setText()` | 无 | 全文件无调用；`node --check` 通过；删除 5 行 |
| `qqbot_studio/assistant_ext.py` | 26 `datetime` import；508-509 无引用 `_now()` | 无 | 全仓无调用；删除 5 行 |
| `qqbot_studio/deployer.py` | 239-240 `_now()` 包装；4 处 `job.updated_at = self._now()` | 现有 `store.utc_now()` | 同一函数、同一格式；净减 4 行 |
| `qqbot_studio/launcher.py` | 11 `import socket` | 无 | AST 确认未使用；删除 1 行 |
| `qqbot_studio/resource_stats.py` | 18 `ResourceStats` import | 无 | 仅类型文档引用，运行时未用；删除 2 行 |
| `qqbot_studio/studio.py` | 18 `urlsplit`、31 `DockerError` import | 无 | 两个名字均无调用；净减 1 行 |
| `qqbot_studio/updater.py` | 13 `UPDATE_SCRIPT` | 无；实际更新走 `start_update()`/外部脚本参数 | 全仓无引用；删除 1 行 |
| `tests/test_astrbot_api.py` | 6 `AstrBotAPIError` import | 无 | 测试未使用；净 0 行 |
| `tests/test_studio.py` | 13 `detect_public_host` import | 无 | 测试未使用；删除 1 行 |

**实际精简口径：**上述 13 个受控文件合计 `+16/-145`，净 **-129 行**；没有删除测试文件，
也没有为了凑行数删除安全/兼容代码。

### 2.2 已删除项的判定证据

- `_has_at_all`、`_search_image_urls`、`_collected_index_path`、`_collected_file_path_for`
  在删除前全仓 grep 只有定义，没有调用；不是 `@staticmethod`/兼容 API，也没有“backward-compatible”
  注释。
- 六个一层包装器只把调用转给同一模块已导入的纯函数；删除后调用点直接使用纯函数，
  行为、异常和 monkeypatch 目标不改变（测试无需改逻辑）。
- `COLLECT_NATURAL_LANGUAGE_RE`/`REVERSED_RE` 的语法解析已由
  `_detect_natural_language_collect_intent()` 的动态分类路径覆盖；两个 regex 全仓无引用，
  不属于旧数据/旧 marker 兼容资产。
- `deployer._now()` 与 `store.utc_now()` 完全同义；直接使用已有实现，符合“已有代码复用”阶梯。

## 3. P0/P1/P2 发现

优先级口径：P0 = 不得为了精简而改变的边界；P1 = 有实际收益但跨模块/测试成本较高；
P2 = 局部、小而安全的后续清理项。

### P0：必须保留，不建议删除

| 发现 | 路径/行号 | 为什么不删 | 可替代/触发条件 |
|---|---|---|---|
| 三套原子私有 JSON 写入 | `qqbot_studio/store.py:197-219`；`qqbot_studio/deployer.py:1114-1141`；`qqbot_studio/assistant_ext.py:639-675`；`qqbot_studio/configgen.py:61-89` | 都涉及 0600/0700、fsync、临时文件、原子 replace；抽取共享 helper 一旦漏掉某个 chmod/失败分支就是数据/安全问题 | 保持现状；未来若统一，必须以现有权限/异常测试为门禁，先走安全 review |
| 插件 schema 前后端双校验 | `qqbot_studio/deployer.py:83-147`、`:1993-2186`；`qqbot_studio/static/services/plugin-config.js:43-195` | 后端负责信任边界/Key 过滤，前端负责输入体验/schema 归一化；跨语言“去重”会产生新的信任边界 | 保留；只增加契约测试，不合并成单一实现 |
| 两套 marker 协议 | `plugins/qqbot_enhancer/llm.py:13-32`（legacy `[[sticker:...]]`）；`plugins/qqbot_enhancer/safe_tools.py:15-197`（`[[tool:...]]`）；路由 `main.py:4213-4325` | wire format、权限、失败回退和安全语义不同；合并会破坏旧回复/旧实例兼容 | 保留；只可共享空白清理小函数，见 P2 |
| Protocol/接口 seam | `qqbot_studio/contracts.py:19-151`；`plugins/qqbot_enhancer/interfaces.py:53-111` | 当前虽多为单实现，但测试 mock、Windows runtime seam 和架构阶段明确要求保留；删除会改变模块契约 | 出现真实第二实现时再重新评估；不要因“单实现”删除 |
| 旧数据/旧容器/旧配置迁移 | `qqbot_studio/studio.py:392-460`；`qqbot_studio/deployer.py:900-965`、`:1107-1285`；`qqbot_studio/astrbot_api.py:181-207` | 旧 v1/v2 实例、SnowLuma `#sl`、旧 AstrBot endpoint 仍需可读/可停用；这是兼容资产不是死代码 | 保留；新功能通过 adapter/capability 扩展，不绕过迁移 |
| 安全/测试/可观测性代码 | token/Host 校验、Docker argv、SSRF/URL 过滤、Key 脱敏、测试 fake、smoke assert | Ponytail 明确不删正确性、安全、无障碍和最小 smoke test | 保留；不要以行数指标删除 |

### P1：有收益但本轮不做（跨边界/中等风险）

1. **前端工具函数重复**：`app.js:103-113,170,260-279,354,384` 与
   `console.js:99-109,120,160-186,206`，以及 `services/api.js:8-16`、
   `services/plugin-config.js:8-26` 的 `asRecord`/`textValue`/`boolOrUndefined`。
   可提取一个无依赖的 `static/ui-utils.js`，预计减少 **约 80 行**；但需要新增静态路由、
   调整两个页面的经典脚本顺序和 token/错误对象绑定，属于前端跨页重构，本轮按任务边界拒绝。
2. **Git runner 双实现**：`assistant_ext.py:51-79` 与 `updater.py:20-44` 都构造相同
   `GIT_TERMINAL_PROMPT=0`、非 Windows `GIT_ASKPASS`、`GIT_SSH_COMMAND` 和 `subprocess.run`
   参数。可抽到小型 `gitutil.py`，预计净减 **约 15 行**；但两处 runner 的注入签名分别是
   4 参/3 参，且测试直接注入 fake runner，迁移会触碰部署和自动更新两个回归面，暂缓。
3. **Windows vendor/sync 双路径**：主仓库保留 `windows/README.md` 和
   `windows/{common,start,console,install}.ps1`、`windows/studio_helper.py`；独立 Windows
   仓库只读快照有 24 个核心文件、27 个插件文件、8 个测试文件，核心约 10,262 行、插件约
   8,298 行；外部 `scripts/sync_upstream.py:29-37` 允许同步 `qqbot_studio/`、
   `plugins/qqbot_enhancer/`、`assets/`，并用 `_mirror_tree` 整体替换。
   这是独立发行版的必要 vendor，**不删主仓库 Windows 资料、不直接改外部仓库**。
   风险是主仓库更新后忘记同步；本轮主仓库代码 changes 需在提交后触发既有 sync workflow。
4. **测试 fake 固定模板重复**：`tests/test_assistant_ext.py:504,633,693,866` 有四个
   局部 `FakeProcess`；`tests/test_deployer.py:606,646,760` 有三个 `OfflineApi`；
   `tests/test_enhancer_plugin.py:93,332` 有两个不同用途的 `FakeContext`。可建测试 helper，
   预计减少 **约 20-40 行**；但各 fake 的回调/异常/上下文语义不同，测试风险高于源码叶子清理，
   本轮仅记录。

### P2：局部清理/保留判断

| 发现 | 路径/行号 | 替代/判断 | 预计收益 |
|---|---|---|---|
| 四个完全相同的 32 字符分类 regex | `plugins/qqbot_enhancer/llm.py:17`、`safe_tools.py:23`、`sticker_reclassify.py:22`、`sticker_store.py:21` | 可在 `config.py` 提供单一 `SAFE_CATEGORY_RE` 并让四个模块 import；当前为了模块可独立加载/测试保留 | 约 -2 至 -3 行，收益很小；本轮不做 |
| marker 清理空白逻辑重复 | `llm.py:27-29`、`safe_tools.py:173-175` | 可共享一个小函数；不能合并协议本身 | 约 -2 行；低优先 |
| 无仓内调用的公开便利方法 | `sticker_store.py:820-845`（`unique_count`/`list_entries`）、`deployer.py:2055-2059`（backward-compatible `_load_plugin_schema`）、`studio.py:306-313`、`dockerctl.py:472-475`、`astrbot_api.py:208-211` | 公开方法或带兼容注释，保留；不要用“仓内无调用”推断外部/旧实例无调用 | 若强行删除约 -20 行，但风险不划算 |
| 旧版文档 | `docs/ui-api-contract.md`（191 行）、`docs/qa-report.md`（236 行） | 文档索引已标注为历史；删除可减 427 行，但会破坏历史链接/兼容参考，拒绝删除 | 0 |
| QA 测试计数过期 | `docs/qa-report-v2.md:26` | 已从旧快照 `236 tests` 更正为本次实际 `357 tests / 26.257s` | 文档正确性 |
| 主仓库 Windows 资料与独立发行仓库 | `windows/README.md`、`windows/*.ps1`、`windows/studio_helper.py`；外部 `UPSTREAM.md`/`scripts/sync_upstream.py` | 保留主仓库移植资料和外部独立发布物；不共享数据目录/自动更新 | 0 直接行数；避免重复维护靠 sync workflow |

## 4. 专项：重复实现/双路径核对

### 4.1 sticker marker 与 unified tool marker

- `llm.STICKER_AI_MARKER_RE` 是旧式 `[[sticker:标签]]` 解析，`extract_sticker_ai_marker()`
  返回清理文本、tag 和 had_marker。
- `safe_tools.TOOL_MARKER_RE` 是统一白名单工具协议 `[[tool:name ...]]`，带参数校验、
  路径/URL/命令拒绝和最多一个附件限制。
- `main._route_safe_tool()` 先处理 unified protocol，再清理/路由 legacy sticker；不是两套
  可互换实现，不能用一个 regex 替换。当前策略正确：保留两种 wire format，旧实例继续兼容。
- 唯一可减的是两个 parser 重复的空白清理两行；收益不足以本阶段跨模块引入共享 import。

### 4.2 插件配置

- 后端 `deployer.py`：敏感字段过滤、schema fallback、数值/类型校验、本地配置合并、
  AstrBot v4.29 API 读写、热重载/降级。
- 前端 `services/plugin-config.js`：schema 归一化、字段类型、options、敏感字段隐藏、
  GET/POST 契约和 404/405 fallback。
- 两侧职责和信任边界不同；不能删除任意一侧。保留并要求 API v2/插件文档与契约测试同步；
  不需要“代码行数”层面的合并。

### 4.3 Windows 适配

- 主仓库 `windows/` 包含旧端口/开发记录和 PowerShell 入口；独立 Windows 仓库是该发行形态的
  权威 source of truth，vendor upstream core 通过 sync allowlist 同步。
- 主仓库本轮代码修改后，Windows `qqbot_studio/`/`plugins/qqbot_enhancer/` 会暂时与主仓库
  工作树不同；这是预期的 commit 边界，提交后由独立仓库 workflow 同步，不在本任务直接改
  Windows release、VERSION 或 EXE。
- 只读审计确认外部 `UPSTREAM.md` 记录 upstream commit `6ed41b9`；正式同步时必须复核
  `qqbot_studio.__version__`、插件版本和双 EXE smoke，避免只看目录存在。

### 4.4 测试固定 fixture

- `FakeProcess` 四次局部定义、`OfflineApi` 三次局部定义、`FakeContext` 两次定义，
  存在合并空间；但它们分别覆盖进程生命周期退出、HTTP offline fallback、插件 context
  不同回调，合并后一个 fake 容易变“万能 mock”，反而掩盖测试失败。保留到测试职责重构阶段。

## 5. 不建议删除的正确性/安全/测试项

以下项目即使看起来“行数多/只调用一次”也不建议删：

- `qqbot_studio/server.py` 的 token/Host/静态文件约束/错误映射；
- `qqbot_studio/dockerctl.py` 的 argv、sudo/权限检测、超时、容器名白名单；
- `qqbot_studio/assistant_ext.py` 的 Key 脱敏、URL/SSRF 防护、0600/0700、进程生命周期；
- `qqbot_studio/configgen.py` 的镜像 fallback、端口探测、Compose 服务和权限；
- `studio.py`/`deployer.py` 的旧实例迁移、旧容器发现和端口保留；
- `contracts.py`/`interfaces.py` 的 Protocol 和兼容别名；
- `plugins/qqbot_enhancer` 的安全 marker、分类 regex、收藏路径/符号链接/大小/权限校验；
- 所有 smoke/regression 测试、断言和日志脱敏测试；Ponytail 的“最小 smoke”不是冗余；
- Windows 发布脚本、独立仓库 `UPSTREAM.md`/sync allowlist/双 EXE workflow（只读审计）。

## 6. 预计收益与债务

### 已实现

- **-129 行**（源码/测试；13 文件，+16/-145）；
- **0 个新增依赖**，**0 个新增源码文件/删除文件**；
- 没有 API、数据、权限、安全或插件功能变更。

### 暂缓但可评估

- 前端公共 helper：约 **-80 行**，新增 1 个 `ui-utils.js`（不推荐在本任务做）；
- Git runner 合并：约 **-15 行**，新增 1 个共享模块（需先稳定 runner 注入签名）；
- 测试 fake helper：约 **-20 至 -40 行**（需保持 fake 语义可读）；
- category regex/空白 helper：约 **-4 行**（收益低）；
- 旧版文档删除：可减 **427 行**，但拒绝，因为文档索引/历史链接和兼容参考仍有价值；
- Windows vendored core/plugin 约 **18,560 行**属于独立发行版，不计入主仓库可删行数；
  靠 sync workflow 和版本门禁控制漂移，而不是把它内联回主仓库。

综合建议：本轮先落地 **低风险 -129 行**；后续若前端 helper 提取能同时通过双页面
token/脚本顺序回归，再考虑约 -80 行；不要为了数字删除协议/安全/兼容/Windows 发行边界。

## 7. 本轮验证记录

```bash
python3 -m unittest discover -s tests -v
# Ran 357 tests in 26.257s, OK

python3 -m unittest tests.test_enhancer_plugin -v
# Ran 161 tests in 0.502s, OK

node --check qqbot_studio/static/app.js
node --check qqbot_studio/static/console.js
node --check qqbot_studio/static/services/api.js
node --check qqbot_studio/static/services/plugin-config.js
node --check qqbot_studio/static/components/instance-settings.js

python3 -m py_compile qqbot_studio/*.py plugins/qqbot_enhancer/*.py tests/*.py
bash -n start.sh console.sh install.sh scripts/*.sh
python3 -m json.tool plugins/qqbot_enhancer/_conf_schema.json >/dev/null
python3 -m qqbot_studio --version
git diff --check
```

结果：全部通过；版本仍为 `qqbot-studio 3.9.0`；没有运行真实 Docker/QQ/noVNC/
DeepSeek E2E，因此安全边界以外的真实运行验证仍按现有 QA 报告标记为条件通过。

## 8. 结论

Ponytail 审计在本仓库的合理落点是“删掉确定没有调用方的私有叶子代码 + 使用已有纯函数”；
不应把安全双校验、旧数据兼容、Protocol seam、marker 协议、测试 fake 和 Windows 独立发行
当成无意义重复。当前实际精简安全、可回滚、测试全绿；剩余收益主要集中在前端 helper 和
测试 fixture，需要单独的重构任务，不应在本轮扩大为跨 API/前端大改。

关联文档：[架构与阶段进度](architecture-modularization.md) · [当前 QA 快照](qa-report-v2.md) · [文档索引](README.md)
