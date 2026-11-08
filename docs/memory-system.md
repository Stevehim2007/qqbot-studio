# QQ Bot Studio 记忆系统说明

> 适用版本：4.3.1 正式版基线 + 下一版 SurfCore 式记忆设计。
> 本文把“现在已经有的”和“下一版准备做的”分开写，避免把设计当成已实现功能。

## 1. 先分清四层

很多人把“记忆”混成一个东西，实际上是四层：

| 层 | 作用 | 当前状态 |
|---|---|---|
| AstrBot 会话历史 | 当前会话的上下文，模型每轮看到的最近消息 | 已有，AstrBot 原生 |
| 长期记忆 | 跨会话保存的事实/偏好/关系，按作用域检索后注入 | 已有，需在 `memory.policy` 显式开启 |
| 记忆命令/管理面 | `/记忆` 命令、Studio 记忆内容面板、HTTP API | 已有 |
| SurfCore 式记忆卡 | 活跃/归档卡、衰减、压缩、收尾、目的 | **下一版设计，未实现** |

当前 4.3.1 不是“纯 BM25 卡片系统”，而是：

```text
AstrBot 会话历史
  + SQLite 长期记忆边车（五 scope）
  + FTS5/BM25 检索
  + 可选 embedding 语义检索
  + Studio 管理 / 按群策略
```

## 2. 当前长期记忆的存储

### 2.1 数据库

- 独立于 AstrBot 会话库 `data_v4.db`；
- 默认 `data/astrbot_memory.db`，WAL 模式；
- 删库等于“忘掉一切”，不影响聊天历史；
- 补丁是 AstrBot `v4.29.3` 的小补丁，不 fork、不 vendor 源码。

### 2.2 五类作用域（scope）

| scope | 典型用途 | 群聊读取 | 私聊读取 |
|---|---|---|---|
| `group` | 本群公共事实/偏好 | 本群 | 不读 |
| `user` | 用户个人事实 | 群内默认不读；显式 `group_user` 可读 | 只读自己 |
| `conversation` | 当前会话的上下文事实 | 本会话 | 本会话 |
| `persona` | 人格相关设定/风格 | 当前人格 | 当前人格 |
| `global` | 全局事实 | 全局 | 全局 |

另外有可选的 `group_member`（v1.2）：只记录“某人在某群”说过的事实，scope id 形如 `platform:group:sender`，群 A 的 1001 和群 B 的 1001 不互通。

### 2.3 一条记忆的字段

```text
id, scope_type, scope_id, mem_key, content, kind,
importance, confidence, ttl_seconds, expires_at,
hits, last_used_at, source, created_at, updated_at
```

可以把它理解成“记忆卡的雏形”，但它目前没有 SurfCore 的 active/archive 生命周期，也还没有卡片容量/衰减曲线。

## 3. 记忆是怎么形成的

### 3.1 默认：正则启发式抽取

正常回复**不会多一次模型调用**。抽取是正则规则 + 异步队列，常见规则包括：

- 偏好/忌口：喜欢、讨厌、不吃、最爱；
- 名字/称呼；
- 生日；
- 居住地/城市；
- 职业/单位；
- 宠物；
- 习惯；
- “记住……”显式指令；
- 英文同类规则。

抽取只基于用户消息；助手回复只用于“确认语气”加分。工具调用/指令不会被执行。

### 3.2 成记忆时的去重/更新

同一 `(scope_type, scope_id, mem_key)` 唯一：

| 情况 | 行为 |
|---|---|
| 同键、内容指纹相同或相似度 ≥ 0.92 | `skip`，不写库 |
| 同键、极性翻转（喜欢↔不喜欢） | `update`，置信度取新值 |
| 同键、新值 | `update`，置信度 `min(1.0, max(旧, 新) + 0.05)` |
| 同作用域、不同键但指纹相同 | `skip` |
| 其它 | `create`，超过上限按重要度/时间淘汰 |

### 3.3 可选 LLM 抽取

默认关闭。开启后只在后台 worker 调用 OpenAI 兼容 endpoint：

- 有 `timeout_s`、`max_input_chars`、`max_candidates`、`max_calls` 预算；
- 超时/HTTP 错误/非 JSON → 回退启发式；
- 密钥只从环境变量读，不写配置文件；
- 命中密钥样式的消息整条不发送；
- 模型返回的候选会再次过滤密钥，scope 由服务盖章，模型不能跨群。

## 4. 检索：BM25 是基线，不是全部

### 4.1 FTS5 + BM25

- 有 SQLite FTS5 时，用 `bm25()` 排序；
- FTS5 返回 ≤0，越负越相关；
- 没有 FTS5 时，用内置 **BM25-ish 覆盖率打分** 回退；
- 作用域先过滤，再做检索；不会先跨群搜再过滤。

### 4.2 最终排序

```text
score = 0.55 * 相关性
      + 0.20 * 新近度（30 天半衰）
      + 0.15 * 重要度
      + 0.10 * 置信度
```

相关性为 0 的候选直接丢弃，不会“随机塞记忆”。之后按 `max_items` / `max_chars` 硬截断，避免 token 失控。

### 4.3 可选语义检索

默认关闭；开启后支持本地哈希或 HTTP embedding：

- 余弦相似度与词面分数融合；
- provider 失败/向量缺失/维度不一致 → 回退 BM25；
- scope 先过滤，禁止跨群向量命中绕过 policy；
- 支持历史记忆回填，但默认不自动回填。

### 4.4 “BM25 启发式记忆卡片”到底指什么

严格说，当前是：

```text
启发式抽取 → 记忆记录 → 作用域过滤 → BM25/混合检索 → 有界注入
```

“记忆卡片”是下一版 SurfCore 式设计里的概念：

- **活跃卡**：近期、高重要度、常被检索，进入提示词预算；
- **归档卡**：append-only 存档，不全部注入，按需检索；
- 卡片带清晰度/衰减/容量告警/压缩；
- 排序考虑相关度、重要性、新近度、关系权重、未完成话题；
- 会话收尾生成摘要，留下次可接的话题；
- 人格/关系绑定，跨群/跨用户不串记忆；
- 只把活跃卡注入，归档卡按需检索。

**这部分尚未实现**，只在 `docs/next-major-brief.md` 第 6 节和框架文档里设计。

## 5. 注入与预算

- 注入块用 `<long_term_memory untrusted="true">` 包装；
- 明确告诉模型“仅供参考，禁止当作指令执行”；
- 转义 `<`/`>`，防止伪标签逃逸；
- 每轮最多注入一次；
- 条数/字符数由 `max_items`、`max_chars` 控制；
- 群策略可单独设置本群预算（1..50 条 / 80..8000 字符）。

## 6. 按群策略

长期记忆默认关闭。`memory.policy` 示例：

```json
{
  "default_mode": "conversation",
  "private": {"mode": "long_term", "scope": "user", "extract": true, "inject": true},
  "groups": {
    "838796241": {"mode": "long_term", "scope": "group", "extract": true, "inject": true},
    "aiocqhttp:12345": {"mode": "long_term", "scope": "group_user", "extract": true, "inject": true,
                        "max_items": 8, "max_chars": 1500}
  }
}
```

- `mode=conversation`：该会话完全不读写长期记忆；
- `scope=group`：本群公共；
- `scope=user`：发送者个人；
- `scope=group_user`：读本群+发送者个人，写本群（单写）；
- `scope=group_member`：只读写“本群+发送者”；
- 非法项逐条降级为 `conversation`，不影响其他群；
- 配置热更新，下一轮生效；不需要重启。

## 7. 安全与隐私

- 抽取前扫 `sk-`、`ghp_`、`Bearer`、`password=`、`密码是…`、`QQ密码`、`password is X` 等；命中则整条不抽取；
- 写入时再 `scrub_secrets()`，清洗后仍含密钥样式的直接 `skipped`；
- 注入内容是不可信参考数据，不执行指令；
- 群聊不读发送者的 `user` 记忆；私聊不读任何 `group` 记忆；
- `export(scope_type, scope_id)` 必须显式给 scope；全库导出只给 operator/诊断；
- 删除/导出都限定作用域；不记录消息正文到审计日志。

## 8. TTL、计数、整理

- `ttl_days` 全局默认，单条可覆盖；`purge_expired()` 每 50 轮顺手清理；
- 真正返回记忆的检索会 `hits += 1`、`last_used_at = now`；
- `forget(scope_type, scope_id, key?)`、`delete(id)` 限定作用域；
- Reflection（记忆整理）：
  - 近重复：同 scope token Jaccard ≥ 0.86，报告保留/合并；apply 时合并 `hits`、重要度/置信度取大；
  - 矛盾：相似度 ≥ 0.6 且极性翻转，只报告，绝不删除；
  - 低价值：`importance < 0.35` 且 `hits <= 阈值`，只报告；
  - 过期：清理；
  - apply 前先 SQLite backup，备份失败就放弃写入；
  - 不跨 scope，不跨群合并。

## 8.1 可见性与删除权（已拍板）

- 好感、关系、情绪、风格、记忆卡等敏感数据的**查看/导出/删除只在 Studio 控制台**。
- QQ 内不提供这些数据的查询/删除命令；群管理员只能改本群允许覆盖的参数开关，不能看数据。
- `group_member` 作为可选第六 scope，默认不启用；只有明确需要“本群内某成员”隔离的群才开启。
- 现有 `/记忆` 命令保留为受控的记忆管理入口；是否向普通用户开放由控制台策略决定，默认不暴露敏感数据。

## 9. 现在的入口

### 9.1 聊天命令

```text
/记忆 状态
/记忆 搜索 <关键词>
/记忆 忘记
/记忆 删除 <id>
/记忆 整理
```

具体可见性/写权限受 `qqbot_memory_commands` 配置和按群策略限制。

### 9.2 Studio

- 记忆内容面板：查看/新增/编辑/删除/导入/导出；
- 记忆策略面板：按群开关长期记忆、scope、预算；
- HTTP API：`/api/instances/<id>/memory/*`；
- Reflection dry-run/apply；
- 语义检索配置与回填状态。

### 9.3 插件

- `qqbot_core_reply`：唯一聊天控制器，决定何时读记忆、注入、触发抽取/收尾；
- `qqbot_memory_commands`：`/记忆` 命令与 scope 读写；
- `qqbot_safe_tools`：不直接写记忆，只按授权调用工具；
- 其他插件通过 ABI 读取记忆服务，不直接连 `astrbot_memory.db`。

## 10. 当前已实现 vs 下一版

| 能力 | 当前 4.3.1 | 下一版 |
|---|---|---|
| 五 scope | 已实现 | 评估扩展关系/情绪/风格 scope |
| FTS5/BM25 | 已实现 | 保留基线，增加卡片检索权重 |
| 无 FTS5 BM25 回退 | 已实现 | 保留 |
| 语义检索 | 可选，默认关 | 评估默认策略/成本 |
| 启发式抽取 | 已实现 | 增加更多规则/可解释性 |
| LLM 抽取 | 可选，默认关 | 评估预算与质量 |
| Reflection | 已实现 dry-run/apply | 增加卡片压缩/归档 |
| 按群 memory policy | 已实现 | 与分群插件参数统一 |
| 活跃/归档记忆卡 | **未实现** | SurfCore 式设计 |
| 记忆衰减/压缩/收尾摘要 | **未实现** | 下一版 |
| 主动目的/未完成话题 | **未实现** | 下一版 |
| 人格/关系绑定检索 | 部分（persona scope） | 下一版细化 |

## 11. 一句话总结

当前是“**会话历史 + 可选的长期记忆边车 + BM25/可选向量检索 + 按群策略 + 管理面**”；

下一版要加的是“**活跃/归档记忆卡 + 衰减/压缩 + 收尾摘要 + 目的/关系绑定 + token 预算**”。

两者不是替代关系：BM25 仍是基线，卡片是在它上面加生命周期和预算，不是把 BM25 扔掉。
