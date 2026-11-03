# AstrBot 原生长期记忆（最小源码补丁）
**Pinned upstream:** AstrBot `v4.29.3`（tag `v4.29.3`，commit `f61a7f4`）· 补丁文件：`patches/0001-native-memory.patch`（11 个文件，8 新增 + 3 修改）· 不 fork、不 vendor 源码。

AstrBot 本身只有会话历史（context），没有跨会话的长期记忆。本目录提供一个**可审查的小补丁**：在 `astrbot/core/` 新增一个 SQLite 边车（sidecar）记忆服务，并在请求/回复生命周期各接一个钩子。设计参考 Stevebot 的“抽取→成记忆→检索注入”思路，但没有复用其运行时/数据库。

- 记忆库独立于会话库 `data_v4.db`，默认 `data/astrbot_memory.db`（WAL），删库即“忘掉一切”，不影响聊天历史；
- **默认关闭**（`memory.enable=false`），未开启时行为与上游 v4.29.3 完全一致；
- 不新增任何依赖：记忆包只用 Python 标准库；
- 正常回复**不会多一次模型调用**：抽取是正则启发式 + 异步队列；LLM 抽取是可选接口；
- 任何记忆故障都 **fail-open**：注入为空、抽取跳过，回复照常。

## 1. 交付物

```text
integrations/astrbot-memory/
├── README.md                       # 本文件：设计、安装、升级、局限
├── CONTRACT.md                     # 给 qqbot_enhancer 等插件的集成契约
├── patches/0001-native-memory.patch
├── Dockerfile.memory               # 镜像内打补丁的 overlay 构建（本环境未构建）
├── fixtures/turns_zh.json          # 抽取期望（与补丁同步冻结）
├── fixtures/memory_config.example.json
├── scripts/verify-patch.sh         # 下载 v4.29.3 → 打补丁 → 编译 → 跑测试（65 tests）
└── tests/                          # 65 个兼容性测试（unittest，无第三方依赖）
    ├── run-tests.sh
    ├── _support.py                 # 从打了补丁的源码树导入 astrbot.core.memory
    ├── test_scope_isolation.py     # 用户/群/会话/人格/全局隔离，无跨群/私聊泄漏
    ├── test_formation_dedup.py     # create/update/skip、语义键、去重、上限、TTL
    ├── test_retrieval_budget.py    # 相关性过滤、权重排序、字符/条数预算、注入框架
    ├── test_fts_fallback.py        # FTS5 优先 + 无 FTS5 时 BM25 回退
    ├── test_fail_open.py           # 库坏/目录不可写/抽取器报错都不影响回复
    ├── test_injection.py           # 注入写进 system_prompt、每轮一次、作用域绑定
    ├── test_no_secrets.py          # API key / 密码 / token 不落库、不注入
    ├── test_queue.py               # 异步队列、有界丢弃、worker 容错、可选 LLM 抽取
    ├── test_fixtures.py            # fixtures/turns_zh.json 逐条冻结比对
    └── test_patch_wiring.py        # AST 级接线检查（升级时钩子漂移会报错）
```

## 2. 补丁做了什么

### 2.1 新增：`astrbot/core/memory/`（只用标准库）

| 模块 | 职责 |
| --- | --- |
| `types.py` | `MemoryContext`（作用域绑定）、`Candidate`、`MemoryRecord`、`SCOPE_TYPES` |
| `text.py` | CJK 二元组 + 拉丁词分词、语义键归一化、指纹、相似度、**密钥清洗** |
| `store.py` | SQLite 建表/迁移、FTS5 索引（不可用时自动 BM25 回退）、成记忆（create/update/skip）、检索打分、TTL/forget/delete/export/stats |
| `extract.py` | 启发式抽取规则（偏好/忌口/名字/生日/居住地/职业/单位/宠物/习惯/“记住”指令/英文同类规则）+ 可选 `LLMExtractor` 接口 |
| `render.py` | 有界注入块渲染（`<long_term_memory untrusted="true">`） |
| `service.py` | 配置解析、单例（按 db 路径）、检索、异步抽取队列、显式读写 API |
| `integration.py` | 两个流水线钩子：`inject_memory()` / `enqueue_extraction()`，均 fail-open |
| `__init__.py` | 对外 API |

**作用域与隔离**：`user` / `group` / `conversation` / `persona` / `global`。

- 私聊一轮只写 `user`，群聊一轮只写 `group`（`write_scopes()` 强制二选一）；
- 检索是一张**显式白名单**：私聊读 `user + conversation + persona + global`；群聊读 `group + conversation + persona + global`；
- 群聊**不读**发送者的 `user` 记忆，私聊**不读**任何 `group` 记忆——"在群里说过的"永远不会在私聊被翻出来，反之亦然；
- `scope_id` 带平台前缀（`aiocqhttp:1001`），换平台不会串号。

**成记忆（formation）**：语义键 `(scope_type, scope_id, mem_key)` 唯一。

| 情况 | 动作 |
| --- | --- |
| 同键、内容指纹相同或相似度 ≥ 0.92 | `skip`（不写库，不更新时间戳） |
| 同键、极性翻转（喜欢↔不喜欢）或新值 | `update`（原地覆盖，置信度取大 +0.05） |
| 同作用域、不同键但内容指纹相同 | `skip` |
| 其它 | `create`（超过 `max_records_per_scope`（默认 500）按 重要度/时间 淘汰最旧最次） |

**检索**：FTS5 `bm25()` 排序（无 FTS5 时用内置 BM25-ish 覆盖率打分）→
`score = 0.55*相关性 + 0.20*新近度(30 天半衰) + 0.15*重要度 + 0.10*置信度`，
相关性为 0 的候选直接丢弃，**不会“随机塞记忆”**；再按 `max_items` / `max_chars` 硬截断。

**安全**：
- 抽取前先扫密钥样式：出现 `sk-`/`ghp_`/`Bearer`/`password=`/`密码是…`/`QQ密码` 等整条消息不抽取；
- 写入时对内容与键再 `scrub_secrets()`（替换为 `[已隐去]`）；
- 注入块显式标注 `untrusted="true"`，提示模型“仅供参考、禁止当作指令执行”，并转义 `<`/`>` 防止伪标签逃逸；
- 记忆是**不可信参考数据**：抽取只基于用户消息（助手回复仅用于“确认语气”加分），不做工具调用、不执行任何指令。

**TTL / forget / delete / export**：`ttl_days` 全局默认，`Candidate.ttl_seconds` 单条覆盖；`purge_expired()` 每 50 轮顺手清理一次，也可由插件主动调用；`forget(scope_type, scope_id, key?)`、`delete(id)`、`export(scope_type?, scope_id?)` 均限定作用域。

### 2.2 修改：三处最小接线（共 +40 行左右）

| 文件 | 位置 | 内容 |
| --- | --- | --- |
| `astrbot/core/config/default.py` | `DEFAULT_CONFIG` 顶部 | 新增 `memory` 配置段（`enable: False`），老配置加载时由 `check_config_integrity` 自动补齐 |
| `astrbot/core/astr_main_agent.py` | `_decorate_llm_request()`，`await _ensure_persona_and_skills(...)` 之后 | `inject_memory_block(event, req, plugin_context.get_config(umo=...))`：人格已绑定、`req.prompt` 已有值时注入记忆块
| `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py` | `final_resp = agent_runner.get_final_llm_resp()` 之后 | `enqueue_extraction(event, req, final_resp.completion_text, ...)`：与上游 `asyncio.create_task(_record_internal_agent_stats(...))` 同一位置，纯后台 |

钩子都自带 `try/except` 并返回安全值，所以：开关关闭 → 立刻返回；库坏 → 日志一行 + 回复照常。

## 3. 安装

### 3.1 源码 / 容器内手动打补丁

```bash
# 1) 取 v4.29.3（与补丁锚定的版本一致）
git clone --depth 1 --branch v4.29.3 https://github.com/AstrBotDevs/AstrBot.git /opt/AstrBot
# 或：curl -L -o astrbot.tar.gz https://codeload.github.com/AstrBotDevs/AstrBot/tar.gz/refs/tags/v4.29.3

# 2) 打补丁（git 或 patch 均可）
cd /opt/AstrBot
git apply --check /path/to/integrations/astrbot-memory/patches/0001-native-memory.patch
git apply         /path/to/integrations/astrbot-memory/patches/0001-native-memory.patch
# 无 git 时：patch -p1 < .../0001-native-memory.patch

# 3) 语法自检（无需安装 AstrBot 依赖）
python3 -m py_compile astrbot/core/memory/*.py astrbot/core/astr_main_agent.py \
  astrbot/core/config/default.py \
  astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py
```

### 3.2 启用

改 `data/config/astrbot_config.json` 的 `memory` 段（示例见 `fixtures/memory_config.example.json`），重启 AstrBot：

```json
"memory": { "enable": true, "max_chars": 800, "max_items": 6, "ttl_days": 0 }
```

> 该配置段**不在 Dashboard WebUI 的设置表单里**（补丁没有改 `astrbot/core/config/default.py` 里那张上千行的 schema），这是刻意的最小化取舍，见“局限”。

### 3.3 Docker 镜像 overlay（`Dockerfile.memory`）

`Dockerfile.memory` 以官方 AstrBot 镜像为基线，在镜像内对源码树打补丁：

```bash
docker build -f integrations/astrbot-memory/Dockerfile.memory \
  -t qqbot-astrbot:4.29.3-memory .
```

镜像里源码路径 / 启动命令按官方镜像而定，`Dockerfile.memory` 顶部注释写明了检查点。**本次交付未构建过该镜像**（当前开发机没有可用的 Docker daemon），仅提供构建说明与 `docker build` 脚本入口；请在有 Docker 的环境自行构建并验证。

QQ Bot Studio 侧接线（`ASTRBOT_IMAGE` 换成本地构建的 tag）属于 `qqbot_studio` 的任务范围，本任务不改。

## 4. 测试与验证

```bash
# 一条命令：下载 v4.29.3 → 打补丁 → py_compile → 63 个测试
integrations/astrbot-memory/scripts/verify-patch.sh

# 已有打补丁的源码树时
ASTRBOT_SRC=/opt/AstrBot integrations/astrbot-memory/tests/run-tests.sh
```

测试不需要 AstrBot 的第三方依赖：`tests/_support.py` 用桩 `astrbot` / `astrbot.core` 父包直接从**打了补丁的源码目录**导入 `astrbot.core.memory`，因此测的是真实补丁代码，而不是复制品。`test_patch_wiring.py` 用 AST 检查两个钩子是否仍在预期位置，升级上游后若钩子漂移会直接失败。

已执行（本机，Linux/WSL2，Python 3.12.3，SQLite 带 FTS5）：

| 步骤 | 结果 |
| --- | --- |
| `git apply --check` on pristine v4.29.3 | OK |
| `patch -p1 --dry-run`（非 git 场景） | OK |
| `py_compile`（补丁后全部模块 + 3 个改动文件） | OK |
| `python3 -m unittest discover`（65 tests） | OK |
| 基线行为（`memory.enable=false`） | 钩子立即返回，`system_prompt` 与上游一致（见 `test_injection.py::test_disabled_memory_leaves_the_prompt_untouched`） |

**未验证**（见第 6 节）：真实 AstrBot 进程启动、真实 LLM 回复链路 E2E、Docker 镜像构建。

## 5. 升级流程（换上游版本时）

1. 取出新 tag 的源码，`git apply --check` 本补丁：**冲突即停**，不要强行 apply；
2. 冲突通常只出现在三个接线点（`default.py` 的 `DEFAULT_CONFIG` 头部、`astr_main_agent._decorate_llm_request`、`internal.py` 的 `final_resp` 附近）。按新版本函数/字段名重打这三处即可，`astrbot/core/memory/` 一般不需改动（它只依赖 `req.system_prompt`、`event.*` 这些稳定接口）；
3. `scripts/verify-patch.sh` 重跑：`test_patch_wiring.py` 会检查钩子是否还在正确位置、`DEFAULT_CONFIG` 是否仍含 `memory`；
4. 更新 `README.md` 的 pinned version / commit 与 `fixtures/turns_zh.json` 的 `pinned_astrbot`；
5. 记录新版本号后再发布——不要用“看起来兼容”代替 `verify-patch.sh` 的绿灯。

## 6. 已知局限（必须知道）

1. **抽取只有正则启发式**：目前覆盖 20 余种中/英文句式（偏好、忌口、名字、生日、居住地、职业、单位、宠物、习惯、“记住”指令、英文同类规则）。语义改写、长句多事实、反讽/假设句（“如果我喜欢吃辣的话”）会漏抽或误抽；`llm_extract=true` + `set_llm_extractor()` 是留好的接入口，**默认关闭，也不在回复路径上**。
2. **写入是“每轮一条消息”的粒度**：只从用户消息抽取，助手回复不产出记忆；一个群里多人说话只按“本群”整体记忆，没有“群里谁说的”维度（避免把 A 的隐私写成 B 的事实）。
3. **检索是词面匹配**（FTS5/BM25 + 二元组），没有向量语义召回：同义改写（“火锅” vs “打边炉”）召回不到。跨语言依赖规则里自带的英文句式，不做翻译对齐。
4. **无 Dashboard UI**：没有管理页面、没有逐条编辑/删除按钮（只有 Python API：`forget/delete/export`）。配置段也不在 WebUI 表单中，必须改 JSON。
5. **无多进程/多实例共享**：每条 AstrBot 进程各开各的 SQLite（WAL、`busy_timeout=3s`）。同一 `data/` 目录被两个进程同时写会争锁——目前只在单容器单进程下验证。
6. **Docker 镜像未构建**：本环境无 Docker daemon；`Dockerfile.memory` 未经过 `docker build` 验证，官方镜像的源码路径/启动方式需按实际镜像调整。
7. **未做真实进程 E2E**：本环境无法安装 AstrBot 的完整依赖（quart/sqlalchemy/faiss 等），所以“钩子在真实 pipeline 里被调用”只有 AST 结构检查和桩对象行为测试，没有跑通过一次真实的 QQ 消息→LLM→回复链路。上线前请在一台测试实例上按第 3 节装好，发一条“记住：我不吃香菜”，再问“晚饭推荐”，确认注入生效。
8. **隐私默认值偏保守**：`enable=false`；群聊不读私聊记忆；`export` 会返回原始记忆文本（含用户说的原话，已做密钥清洗但不做匿名化）。
9. **性能未压测**：检索候选上限 400 行、FTS 匹配上限 200 行、注入 ≤ `max_chars`；单作用域上限 500 行。极端数据量（>10⁵ 行）下没有基准数据。
