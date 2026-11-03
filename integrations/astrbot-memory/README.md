# AstrBot 原生长期记忆（最小源码补丁）
**Pinned upstream:** AstrBot `v4.29.3`（tag `v4.29.3`，commit `f61a7f4`）· 补丁文件：`patches/0001-native-memory.patch`（v1.2：15 个文件，12 新增 + 3 修改）· 不 fork、不 vendor 源码。

AstrBot 本身只有会话历史（context），没有跨会话的长期记忆。本目录提供一个**可审查的小补丁**：在 `astrbot/core/` 新增一个 SQLite 边车（sidecar）记忆服务，并在请求/回复生命周期各接一个钩子。设计参考 Stevebot 的“抽取→成记忆→检索注入”思路，但没有复用其运行时/数据库。

- 记忆库独立于会话库 `data_v4.db`，默认 `data/astrbot_memory.db`（WAL），删库即“忘掉一切”，不影响聊天历史；
- **默认关闭**（`memory.enable=false`），未开启时行为与上游 v4.29.3 完全一致；
- 不新增任何依赖：记忆包只用 Python 标准库；
- 正常回复**不会多一次模型调用**：抽取是正则启发式 + 异步队列；LLM 抽取是可选接口；
- 任何记忆故障都 **fail-open**：注入为空、抽取跳过，回复照常；
- **按群开关**（v1.1）：只有 `memory.policy` 里显式列为 `long_term` 的群才读写长期记忆，其余群/私聊保持纯会话行为；规则详见 §3.2「生效时机」。
- **可选能力全部默认关闭**（v1.2）：LLM 抽取、语义检索、群内说话人维度（`group_member`）、记忆整理（reflection）都要显式开启；不开启时行为与 v1.1 完全一致。

## 1. 交付物

```text
integrations/astrbot-memory/
├── README.md                       # 本文件：设计、安装、升级、局限
├── CONTRACT.md                     # 给 qqbot_enhancer 等插件的集成契约
├── patches/0001-native-memory.patch
├── Dockerfile.memory               # 镜像内打补丁的 overlay 构建（本环境未构建）
├── fixtures/turns_zh.json          # 抽取期望（与补丁同步冻结）
├── fixtures/memory_config.example.json
├── fixtures/memory_policy.example.json  # 按群策略样例（含 group_member 与非法项，验证 fail-safe）
├── fixtures/reflection_example.json    # 记忆整理样例（期望的重复/矛盾/低价值/过期计数）
├── scripts/verify-patch.sh         # 下载 v4.29.3 → 打补丁 → 编译 → 策略冒烟 → 跑测试（194 tests）
└── tests/                          # 194 个兼容性测试（unittest，无第三方依赖）
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
    ├── test_policy.py              # 按群策略：门禁/隔离/热更新/fail-safe/scope/读写开关
    ├── test_policy_fixture.py      # fixtures/memory_policy.example.json 冻结比对
    ├── test_export_scope.py        # export 必须显式 scope；export_all 才是全库
    ├── test_retrieval_usage.py     # 检索更新 hits/last_used_at，未命中不计
    ├── test_config_reload.py       # enable/min_confidence/read_*/policy/上限热更新；死配置键已移除
    ├── test_speaker_scope.py       # group_member：说话人维度、策略门禁、跨群/私聊隔离、精确删除导出
    ├── test_reflection.py          # 记忆整理：dry-run/apply、备份、近重复合并、矛盾只报告、过期清理
    ├── test_llm_extract.py         # 可选 LLM 抽取：stub 传输、预算、截断、密钥不外发、失败回退（无真实调用）
    ├── test_semantic.py            # 可选语义检索：provider/余弦/融合、scope 先过滤、回退 BM25、迁移与回填
    ├── test_optional_config.py     # 可选能力默认关闭、热更新重建、深拷贝、stats 不泄露密钥
    ├── test_packaging.py           # Dockerfile COPY 源 vs README build context（无需 Docker）
    ├── test_docs.py                # README 测试数/置信度文案与代码一致
    └── test_patch_wiring.py        # AST 级接线检查（钩子漂移 / 补丁 hunk 头与正文一致）
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
| `policy.py` | **按群策略**：解析/校验 `memory.policy`、按群 identity 解析、TTL + 指纹缓存（热更新） |
| `service.py` | 配置解析、单例（按 db 路径）、策略门禁、检索、异步抽取队列、显式读写 API |
| `reflect.py` | **记忆整理**（v1.2）：确定性 dry-run/apply、近重复合并、矛盾只报告、低价值/过期预览、apply 前备份；无 LLM |
| `llm_extract.py` | **可选 LLM 抽取**（v1.2）：endpoint/model/超时/最大输入/调用预算、仅后台、失败回退、密钥不外发；默认关闭 |
| `embed.py` | **可选语义检索**（v1.2）：本地哈希或 HTTP embedding provider、余弦与词面融合、失败回退 BM25；默认关闭 |
| `integration.py` | 两个流水线钩子：`inject_memory()` / `enqueue_extraction()`，均 fail-open |
| `__init__.py` | 对外 API |

**作用域与隔离**：`user` / `group` / `conversation` / `persona` / `global`。
（v1.1 起，自动读写还要先过 `memory.policy` 的按群门禁，见 2.3。）

- 私聊一轮只写 `user`，群聊一轮只写 `group`（`write_scopes()` 强制二选一）；
- 检索是一张**显式白名单**：私聊读 `user + conversation + persona + global`；群聊读 `group + conversation + persona + global`；
- 群聊**不读**发送者的 `user` 记忆，私聊**不读**任何 `group` 记忆——"在群里说过的"永远不会在私聊被翻出来，反之亦然；
- `scope_id` 带平台前缀（`aiocqhttp:1001`），换平台不会串号。

**成记忆（formation）**：语义键 `(scope_type, scope_id, mem_key)` 唯一。

| 情况 | 动作 |
| --- | --- |
| 同键、内容指纹相同或相似度 ≥ 0.92 | `skip`（不写库，不更新时间戳） |
| 同键、极性翻转（喜欢↔不喜欢） | `update`（原地覆盖，**置信度取新值**，不沿用已被推翻的旧置信度） |
| 同键、新值（非翻转） | `update`（原地覆盖，置信度 `min(1.0, max(旧, 新) + 0.05)`） |
| 同作用域、不同键但内容指纹相同 | `skip` |
| 其它 | `create`（超过 `max_records_per_scope`（默认 500）按 重要度/时间 淘汰最旧最次） |

**检索**：FTS5 `bm25()` 排序（无 FTS5 时用内置 BM25-ish 覆盖率打分）→
`score = 0.55*相关性 + 0.20*新近度(30 天半衰) + 0.15*重要度 + 0.10*置信度`，
相关性为 0 的候选直接丢弃，**不会“随机塞记忆”**；再按 `max_items` / `max_chars` 硬截断。

**安全**：
- 抽取前先扫密钥样式：出现 `sk-`/`ghp_`/`Bearer`/`password=`/`密码是…`/`QQ密码`，以及英文 copula 形态 `password is X` / `my api key is X` / `token is X` / `pin is 1234` / `the password for my NAS is X` 等，**整条消息不抽取**（同一句里别的可记事实也会一起放弃——保守优先）；
- 写入时对内容与键再 `scrub_secrets()`（替换为 `[已隐去]`），`upsert()` 对清洗后仍含密钥样式的内容直接 `skipped`；
- 注入块显式标注 `untrusted="true"`，提示模型“仅供参考、禁止当作指令执行”，并转义 `<`/`>` 防止伪标签逃逸；
- 记忆是**不可信参考数据**：抽取只基于用户消息（助手回复仅用于“确认语气”加分），不做工具调用、不执行任何指令。

**TTL / forget / delete / export**：`ttl_days` 全局默认，`Candidate.ttl_seconds` 单条覆盖；`purge_expired()` 每 50 轮顺手清理一次，也可由插件主动调用；`forget(scope_type, scope_id, key?)`、`delete(id)` 均限定作用域。`export(scope_type, scope_id?)` **必须显式给 scope**（不给就报错，杜绝"顺手导出全库"）；要全库导出只能用 operator-only 的 `export_all()`，插件不要用。

**使用计数（hits/last_used_at）**：每次真正返回记忆的检索会把命中的行 `hits += 1`、`last_used_at = 现在`，`export()` / `stats()` 可见；计数写入失败只记 `last_error`，不影响检索本身。

### 2.3 按群策略（v1.1 新增）

目标：**少数群开长期记忆，其它群和私聊保持纯会话行为**（不读不写长期记忆，AstrBot 自己的会话历史不受影响）。

配置（`memory.policy`，`fixtures/memory_policy.example.json` 是完整样例）：

```json
"memory": {
  "enable": true,
  "policy": {
    "default_mode": "conversation",
    "private": {"mode": "long_term", "scope": "user", "extract": true, "inject": true},
    "groups": {
      "838796241":       {"mode": "long_term", "scope": "group",      "extract": true, "inject": true},
      "aiocqhttp:12345": {"mode": "long_term", "scope": "group_user", "extract": true, "inject": true,
                          "max_items": 8, "max_chars": 1500},
      "777777":          {"mode": "long_term", "scope": "group_member", "extract": true, "inject": true}
    }
  }
}
```

| 字段 | 取值 | 语义 |
| --- | --- | --- |
| `default_mode` | `conversation`（默认）/ `long_term` | 未列出会话的模式；**默认 conversation = 全部关闭** |
| `private` | 条目对象 | 私聊专用条目；缺省时私聊跟随 `default_mode`，scope 固定 `user` |
| `groups` | `{群id: 条目}` | 按群覆盖；键可写裸群号或带平台前缀 `aiocqhttp:12345`（前缀优先） |
| `mode` | `conversation` / `long_term` | **条目里必填**；`conversation` 时该会话完全不读写长期记忆 |
| `scope` | `group` / `user` / `group_user` / `group_member` | 见下表 |
| `extract` | `true`/`false` | 是否允许**写**（抽取入库），与 `inject` 独立 |
| `inject` | `true`/`false` | 是否允许**读**（检索注入），与 `extract` 独立 |
| `max_items` / `max_chars` | 1..50 / 80..8000 | 该会话的注入预算；缺省用 `memory.max_items` / `memory.max_chars` |

`scope` 的读写语义（自动抽取/注入，不含插件的显式 API）：

| scope | 群聊读 | 群聊写 | 私聊 |
| --- | --- | --- | --- |
| `group`（群条目默认） | 本群 | 本群 | 不适用 |
| `user` | **发送者个人**（显式选择让本群使用其私有记忆） | 发送者个人 | 只读写自己 |
| `group_user` | 本群 + 发送者个人 | 本群（单写，避免一条事实两行） | 不适用 |
| `group_member`（v1.2） | **仅本群的发送者个人**（同群别人读不到） | 仅本群 + 发送者个人（`group_member` scope） | 不适用 |

边界保证：

- 群 identity 取自事件的群 id（`get_group_id()`，缺失时从 UMO `platform:message_type:session_id` 兜底），**不来自消息文本**——私聊里写“群 12345”不会继承该群策略；
- 群 A 的 `group` 记忆在群 B / 私聊 / 另一个平台都读不到；私聊 `user` 记忆只在私聊或显式 `scope=user|group_user` 的群里可见；
- 写入还有第二道闸：`store_candidates(..., allowed_scopes=[...])` 会**丢弃**落点不在本会话写作用域内的候选（不重映射，避免把 A 的事实塞进 B）；
- 非法配置**逐条 fail-safe 到 conversation**：未知 `mode`/`scope`、非布尔的 `extract`、越界的 `max_items`、`groups` 不是对象、私聊配 `scope=group`…… 都只让**那一项**降级为 conversation（其它群照常），原因写进 `MemoryPolicy.error` / `service.stats()["policy"]` / 日志 warning；
- 热更新：配置文件里的 `policy` 改了以后，下一轮就生效（`get_memory_service()` 每轮用最新配置刷新已缓存的服务，策略缓存按配置指纹失效 + 5s TTL），不需要重启；同一轮已经排进抽取队列的消息会按**处理时刻**的策略再判一次，关掉就丢弃。

### 2.4 群内说话人维度（v1.2 新增：`scope = "group_member"`）

有些群想记的是“**这个人**在这个群里说过什么”，而不是“这个群的公共事实”。v1.2 为此新增了一个作用域，
**没有改动 `group_user` 的写语义**（`group_user` 仍然只写群作用域，单写一行）：

| scope | 读 | 写 |
| --- | --- | --- |
| `group` | 本群 | 本群 |
| `group_user`（不变） | 本群 + 发送者个人 | 本群（单写） |
| `group_member`（新增） | 仅本群 + 发送者个人 | 仅本群 + 发送者个人 |

- scope id 形如 `aiocqhttp:2001:1001`（平台:群:发送者），所以群 A 的 1001、群 B 的 1001、私聊的 1001 是**三个不同的作用域**；
- 每条记忆同时写入 `sender_id` 列（“这句话是谁说的”），旧库自动 `ALTER TABLE` 补列，不丢数据；
- 群内读取仍是**显式 opt-in**：群里配 `scope=group|group_user` 时读不到任何 `group_member` 行；
- 私聊任何情况都读不到群说话人记忆；策略里给私聊配 `group_member` 会整条降级为 conversation 并写进 `error`；
- 删除/导出必须给精确 scope：`forget("group_member", "aiocqhttp:2001:1001")` / `export("group_member", "aiocqhttp:2001:1001")`；
- A 的隐私不会变成 B 的事实：B 的检索白名单里根本不含 A 的 scope id。

### 2.5 记忆整理 / Reflection（v1.2 新增，纯本地、无 LLM）

`service.reflect(scopes, apply=False)`：对一个**显式作用域**做确定性整理，先出报告，再决定是否应用。

| 类别 | 判定 | dry-run | apply |
| --- | --- | --- | --- |
| 近重复 | 同作用域内内容 token Jaccard ≥ `similarity`（默认 0.86） | 列出保留行与被合并行 | 合并（`hits` 相加、重要度/置信度取大），删除被合并行 |
| 矛盾 | 相似度 ≥ `contradiction_similarity`（默认 0.6）且极性翻转（喜欢↔不喜欢） | 成对列出 | **只报告，绝不删除** |
| 低价值 | `importance < low_value_importance`（默认 0.35）且 `hits <= low_value_hits` | 列出 | **只报告，不删除** |
| 过期 | `expires_at <= now` | 列出 | 清理（本来也读不到） |

- `apply` 之前**先备份**：`sqlite3.Connection.backup()` 生成 `<db>.<ts>.bak` 或 `memory.reflect.backup_dir` 下的副本；备份失败就放弃写入（`ok=false, applied=false`）；
- 任务可重复：第二次 apply 不会重复合并（幂等）；
- 不跨作用域：只整理传入的 scope 列表，绝不把两个群的同文记忆合并；
- 纯标准库 token 相似度，不调用模型、不联网；
- 面向 Studio/运维：`reflect(scopes, apply)`（显式 scope）与 `reflect_for(context, apply)`（只允许本会话策略内的 scope，未开启长期记忆则 `ok=false` 且计入 `gated_turns`）。

### 2.6 可选 LLM 抽取（v1.2 新增，默认关闭、只在后台）

启发式规则仍是默认。想要更好的抽取，可以配置一个 OpenAI 兼容的 chat-completions 端点：

```json
"memory": {
  "llm_extract": true,
  "llm": {"enable": true, "endpoint": "http://127.0.0.1:8080/v1/chat/completions", "model": "your-model",
          "timeout_s": 8, "max_input_chars": 800, "max_candidates": 5, "max_calls": 50}
}
```

- **开关**：`llm.llm_extract=true` 或 `llm.enable=true`（两者取或；都默认 false）；
- **只在后台**：只在抽取 worker 里 await，回复已经发完；`enqueue_turn()` 立即返回，不阻塞；
- **预算**：`max_input_chars` 截断输入、`max_calls` 是进程级调用上限、`timeout_s` 包住 HTTP；超预算/超时/HTTP 错误/返回非 JSON → **回退启发式**（`client.last_error` 只记类型）；
- **密钥不外发**：命中密钥样式的消息整条不发送；模型返回的候选再 `contains_secret()` 过滤一遍；
- **scope 不可信**：候选的作用域由服务用本会话策略的写作用域盖章（`_bind_candidate`），模型给不出跨群 scope；
- **日志**：只记条数/错误类型，不记消息正文；
- API key 只从环境变量读（`llm.api_key_env`，默认 `ASTRBOT_MEMORY_LLM_API_KEY`），不写进配置文件；
- 测试用 stub 传输（`test_llm_extract.py`），**不产生任何真实调用/费用**。

### 2.7 可选语义检索（v1.2 新增，默认关闭、失败回退 BM25）

BM25/FTS5 仍是基线。开启后：

```json
"memory": {
  "semantic": {"enable": true, "provider": "local", "dim": 64, "weight": 0.3, "min_cosine": 0.2,
               "auto_backfill": false, "batch": 16}
}
```

- `provider=local`：纯标准库的 token 哈希向量（零依赖、零网络、确定性），适合“换个说法也能命中”的粗召回；
- `provider=http`：OpenAI 兼容 `POST {endpoint}`（`{"model","input"}` → `data[].embedding`），API key 同样只从环境变量读；
- 向量以 **float32 BLOB** 存在 `astrbot_memory_embeddings` 表（`memory_id/model/dim/vector/updated_at`），旧库自动建表；
- **先按 scope 过滤再打分**：候选行必须先通过 SQL 里的 `(scope_type, scope_id) IN (...)`，向量命中不可能绕过策略把别的群拉进来；
- 融合：`rel = (1-weight) * 归一化词面分 + weight * cosine`；纯语义命中需 `cosine >= min_cosine` 才保留；
- **回退**：没配 provider / 向量缺失 / provider 报错 / BLOB 损坏 / 维度不一致 → 直接用 BM25，只记 `last_error`；
- 写入时自动补向量（`add()` / `store_candidates()`）；历史数据用 `service.backfill_semantic(scopes?, limit?)` 回填（返回 `scanned/embedded/failed`），`semantic.auto_backfill=true` 时检索前最多回填 `batch` 条（HTTP provider 会增加回复前延迟，因此默认关闭）；
- `service.semantic_stats()` 暴露 `provider/model/embedded/records/errors`（不含密钥）。

### 2.2 修改：三处最小接线（共 +40 行左右）

| 文件 | 位置 | 内容 |
| --- | --- | --- |
| `astrbot/core/config/default.py` | `DEFAULT_CONFIG` 顶部 | 新增 `memory` 配置段（`enable: False`，`policy.default_mode: conversation`），老配置加载时由 `check_config_integrity` 自动补齐 |
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

改 AstrBot 的配置文件 **`data/cmd_config.json`** 里的 `memory` 段（AstrBot v4.29.3 的 `ASTRBOT_CONFIG_PATH = <data>/cmd_config.json`；路径写错不会报错，记忆只会一直不生效，所以请照抄这里的文件名。示例见 `fixtures/memory_config.example.json`）：

```json
"memory": {
  "enable": true,
  "max_chars": 800,
  "max_items": 6,
  "ttl_days": 0,
  "policy": {
    "default_mode": "conversation",
    "groups": {
      "838796241": {"mode": "long_term", "scope": "group", "extract": true, "inject": true}
    }
  }
}
```

> 注意 `enable: true` 只是"打开记忆子系统"，**具体哪个群记忆由 `policy` 决定**；`policy.default_mode` 默认 `conversation`，即不列出来的群/私聊一律不记。上例只让群 `838796241` 使用长期记忆。
>
> 该配置段**不在 Dashboard WebUI 的设置表单里**（补丁没有改 `astrbot/core/config/default.py` 里那张上千行的 schema），这是刻意的最小化取舍，见“局限”。

**生效时机（与 `docs/memory-policy.md`、Studio「记忆策略」面板一致）**：

1. `cmd_config.json` 只在 AstrBot 启动时读入内存（`astrbot_config_mgr` 缓存），所以**直接改文件需要重启 AstrBot**（或触发 AstrBot 自己的配置重载）才生效；
2. Studio 的记忆策略面板写的就是这个 `data/cmd_config.json`，因此它保存后返回 `restart_required=true` / `hot_reload=false`，不会宣称已生效；
3. 补丁里的 `refresh_config()` 是第二层：**一旦进程内的配置对象变了**（Dashboard 保存、配置重载、或重启加载），`enable` / `policy` / 预算就在**下一轮消息**生效，不需要为记忆策略再单独重启一次（策略缓存最多 5 秒刷新）；
4. 例外：改 `db_path` 必须重启（服务单例按旧路径缓存连接，见局限 10）。

### 3.3 Docker 镜像 overlay（`Dockerfile.memory`）

`Dockerfile.memory` 以官方 AstrBot 镜像为基线，在镜像内对源码树打补丁。**build context 是仓库根**（含 `integrations/` 的那一层），因为 Dockerfile 的 COPY 源都写成 `integrations/astrbot-memory/...` 的仓库根相对路径：

```bash
# 在仓库根执行（context = .）
docker build -f integrations/astrbot-memory/Dockerfile.memory \
  -t qqbot-astrbot:4.29.3-memory .
```

镜像里源码路径 / 启动命令按官方镜像而定，`Dockerfile.memory` 顶部注释写明了检查点。**本次交付未构建过该镜像**（当前开发机没有可用的 Docker daemon），仅提供构建说明与 `docker build` 脚本入口；请在有 Docker 的环境自行构建并验证。COPY 源与 README 命令的一致性由 `tests/test_packaging.py` 静态校验（不需要 Docker）。

QQ Bot Studio 侧接线（`ASTRBOT_IMAGE` 换成本地构建的 tag）属于 `qqbot_studio` 的任务范围，本任务不改。

## 4. 测试与验证

```bash
# 一条命令：下载 v4.29.3 → 打补丁 → py_compile → 策略冒烟 → 194 个测试
integrations/astrbot-memory/scripts/verify-patch.sh

# 已有打补丁的源码树时
ASTRBOT_SRC=/opt/AstrBot integrations/astrbot-memory/tests/run-tests.sh

# 只跑 v1.2 新增能力的用例（同样需要 ASTRBOT_SRC）
cd integrations/astrbot-memory/tests
ASTRBOT_SRC=/opt/AstrBot python3 -m unittest test_speaker_scope test_reflection \
  test_llm_extract test_semantic test_optional_config -v
```

测试不需要 AstrBot 的第三方依赖：`tests/_support.py` 用桩 `astrbot` / `astrbot.core` 父包直接从**打了补丁的源码目录**导入 `astrbot.core.memory`，因此测的是真实补丁代码，而不是复制品。`test_patch_wiring.py` 用 AST 检查两个钩子是否仍在预期位置，升级上游后若钩子漂移会直接失败。

已执行（本机，Linux/WSL2，Python 3.12.3，SQLite 带 FTS5）：

| 步骤 | 结果 |
| --- | --- |
| `git apply --check` on pristine v4.29.3 | OK |
| `patch -p1 --dry-run`（非 git 场景） | OK |
| `py_compile`（补丁后全部模块 + 3 个改动文件） | OK |
| 策略冒烟（默认 `policy.default_mode == conversation`，列出的群解析为 long_term） | OK |
| `python3 -m unittest discover`（集成补丁测试，194 tests） | OK |
| `PYTHONPATH=. python3 -m unittest discover -s tests -t tests`（主仓库，仓库根执行，594 tests @ 提交 643351a 快照） | OK |
| 基线行为（`memory.enable=false`） | 钩子立即返回，`system_prompt` 与上游一致（见 `test_injection.py::test_disabled_memory_leaves_the_prompt_untouched`） |

主仓库测试用同一口径复跑：`git archive <commit> | tar -x -C /tmp/head` 出干净快照，再在该目录执行 `PYTHONPATH=. python3 -m unittest discover -s tests -t tests`（本次 594 tests OK @ `643351a`）。补丁本身不改 `qqbot_studio`/插件，所以这个数字随主分支变化——引用时必须带上提交号与该命令，不要单独写一个数字。

**未验证**（见第 6 节）：真实 AstrBot 进程启动、真实 LLM 回复链路 E2E、Docker 镜像构建。

## 5. 升级流程（换上游版本时）

1. 取出新 tag 的源码，`git apply --check` 本补丁：**冲突即停**，不要强行 apply；
2. 冲突通常只出现在三个接线点（`default.py` 的 `DEFAULT_CONFIG` 头部、`astr_main_agent._decorate_llm_request`、`internal.py` 的 `final_resp` 附近）。按新版本函数/字段名重打这三处即可，`astrbot/core/memory/` 一般不需改动（它只依赖 `req.system_prompt`、`event.*` 这些稳定接口）；
3. `scripts/verify-patch.sh` 重跑：`test_patch_wiring.py` 会检查钩子是否还在正确位置、`DEFAULT_CONFIG` 是否仍含 `memory`；
4. 更新 `README.md` 的 pinned version / commit 与 `fixtures/turns_zh.json` 的 `pinned_astrbot`；
5. 记录新版本号后再发布——不要用“看起来兼容”代替 `verify-patch.sh` 的绿灯。

## 6. 已知局限（必须知道）

0. **策略默认全关**：`memory.policy.default_mode=conversation`，`enable=true` 也不会自动记住任何群——必须显式列出群号；升级到 v1.1 后原有"全群记忆"行为会被关闭，这是刻意的隐私默认值。v1.0 的 `write_private_scope`/`write_group_scope` 键**已从默认配置和示例里删除**（写作用域现在由 `memory.policy.scope` 决定），旧配置文件里残留这两个键会被忽略。
1. **抽取只有正则启发式**：目前覆盖 20 余种中/英文句式（偏好、忌口、名字、生日、居住地、职业、单位、宠物、习惯、“记住”指令、英文同类规则）。语义改写、长句多事实、反讽/假设句（“如果我喜欢吃辣的话”）会漏抽或误抽；`llm_extract=true` + `set_llm_extractor()` 是留好的接入口，**默认关闭，也不在回复路径上**。
2. **写入是“每轮一条消息”的粒度**：只从用户消息抽取，助手回复不产出记忆。群内“谁说的”维度 v1.2 才提供，且是**显式 opt-in**：只有该群配 `scope=group_member` 才会按说话人隔离（默认 `group`/`group_user` 仍按本群整体记忆，避免把 A 的隐私写成 B 的事实）。
3. **基线检索仍是词面匹配**（FTS5/BM25 + 二元组）：同义改写（“火锅” vs “打边炉”）默认召回不到。v1.2 提供**可选**语义检索（`semantic.enable=true`），但 `provider=local` 只是 token 哈希的粗召回、不是真正的语义模型；要真语义需自己配 OpenAI 兼容 embedding 端点（费用/延迟由运维承担，默认关闭）。不做跨语言翻译对齐。
4. **无 Dashboard UI**：没有管理页面、没有逐条编辑/删除按钮（只有 Python API：`forget/delete/export`）。配置段也不在 WebUI 表单中，必须改 JSON。
5. **无多进程/多实例共享**：每条 AstrBot 进程各开各的 SQLite（WAL、`busy_timeout=3s`）。同一 `data/` 目录被两个进程同时写会争锁——目前只在单容器单进程下验证。
6. **Docker 镜像未构建**：本环境无 Docker daemon；`Dockerfile.memory` 未经过 `docker build` 验证，官方镜像的源码路径/启动方式需按实际镜像调整。
7. **未做真实进程 E2E**：本环境无法安装 AstrBot 的完整依赖（quart/sqlalchemy/faiss 等），所以“钩子在真实 pipeline 里被调用”只有 AST 结构检查和桩对象行为测试，没有跑通过一次真实的 QQ 消息→LLM→回复链路。上线前请在一台测试实例上按第 3 节装好，发一条“记住：我不吃香菜”，再问“晚饭推荐”，确认注入生效。
8. **隐私默认值偏保守**：`enable=false` + `policy.default_mode=conversation`；群聊默认不读私聊记忆（`scope=user|group_user` 是显式 opt-in）；`export` 会返回原始记忆文本（含用户说的原话，已做密钥清洗但不做匿名化）。
9. **策略只能改 JSON / 面板**：AstrBot 侧没有 `/记忆 开群xxx` 之类的聊天命令；改配置靠编辑 `data/cmd_config.json` 或 Studio 的「记忆策略」面板。文件改动要等 AstrBot 重启/重载才生效（面板明确 `restart_required=true`），生效后按群门禁在下一轮消息应用，不会中断正在进行的回复。
10. **热更新只覆盖“进程内已加载的配置”，`db_path` 仍需重启**：`enable`/`read_*`/`min_confidence`/`policy`/`max_items`/`max_chars`/`max_records_per_scope` 在配置对象更新后的下一轮生效（`get_memory_service()` 每轮 `refresh_config()`，`test_config_reload.py` 覆盖）；但**直接编辑 `cmd_config.json` 文件本身不会被 AstrBot 自动重读**，需要重启/重载（见 §3.2）。`db_path` 改变无论如何都要重启（服务单例按旧路径缓存旧连接）。旧路径的服务对象不会自动关闭（进程内最多每个不同路径一个连接，可接受但要注意）。
11. **性能未压测**：检索候选上限 400 行、FTS 匹配上限 200 行、注入 ≤ `max_chars`；单作用域上限 500 行。极端数据量（>10⁵ 行）下没有基准数据。
12. **记忆整理（reflection）是运维动作，不是后台任务**：需要显式调用 `reflect()`/`reflect_for()`；单作用域内两两比较是 O(n²)（上限 `max_scan` 500 行/次），没有调度器、没有自动合并；矛盾与低价值**永远只报告**，删除要人来做。
13. **语义向量会占空间、需要回填**：每条记忆额外一份 float32 向量（`dim` × 4 字节）；历史数据要 `backfill_semantic()`（或开 `auto_backfill`，HTTP provider 会加到回复前延迟）。换 embedding 模型/维度后旧向量不匹配会被忽略，需重新回填。
14. **LLM 抽取会花钱**：默认关闭；开启后每轮最多一次调用、受 `max_calls`/`max_input_chars`/`timeout_s` 约束，但费用与失败率取决于你自己的端点；模型输出只当候选，仍要过密钥过滤 + 策略写作用域盖章。
15. **`group_member` 的作用域粒度**：一条记忆只属于“一个群+一个发送者”，同一个人的私聊记忆与群说话人记忆**不互通**（这是刻意的隐私边界）；想在私聊里用到群里说过的事，需要 `scope=user` 的显式 opt-in，且那也只影响该群。
