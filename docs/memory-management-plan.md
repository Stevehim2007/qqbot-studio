# 长期记忆管理功能备忘录

> 状态：进行中。仅测试分支；正式版不会合入长期记忆，直到真实 Docker/AstrBot/QQ E2E 通过。
> 代码优化原则：先补契约和测试，再改实现；小步提交、可回滚；避免重复造轮子；默认隐私/失败安全。

## 1. 记忆内容管理（Studio）

> 实现：v4.2.1-beta.1（t33）。面板「记忆内容」由 `qqbot_studio/static/console.js` 的 `buildMemoryContentSection` 渲染，后端 `qqbot_studio/memory_admin.py`；契约见 [`memory-admin-api.md`](memory-admin-api.md)。

- [x] 实例设置新增「记忆内容」面板；
- [x] 按 scope 筛选（真实存储 scope：group/user/conversation/persona/global；group_user/speaker 是策略名而不是库内 scope，见契约 §1）；
- [x] 关键词搜索、分页；
- [x] 查看单条详情；
- [x] 编辑正文/重要度/置信度/TTL；
- [x] 单条删除；
- [x] 按 scope 全部忘记；
- [x] 导出当前 scope；
- [x] 导入/恢复（预览、去重、失败不写半份）；
- [x] 显示 hits/last_used、来源和时间；
- [x] 删除/忘记二次确认；
- [x] 不展示 API Key、密码、跨 scope 内容。

## 2. 记忆管理 HTTP API

> 实现：v4.2.1-beta.1（t33），`/api/instances/<id>/memory/{stats,records,forget,export,import,reflect}`，全部显式 scope + 令牌校验；见 [`memory-admin-api.md`](memory-admin-api.md)。

- [x] `GET /api/instances/<id>/memory/stats`
- [x] `GET /api/instances/<id>/memory/records?scope_type=&scope_id=&q=&limit=&offset=`
- [x] `GET /api/instances/<id>/memory/records/<id>`
- [x] `PATCH /api/instances/<id>/memory/records/<id>`
- [x] `DELETE /api/instances/<id>/memory/records/<id>`
- [x] `POST /api/instances/<id>/memory/forget`
- [x] `POST /api/instances/<id>/memory/export`
- [x] `POST /api/instances/<id>/memory/import`
- [x] `POST /api/instances/<id>/memory/reflect`（dry-run/apply）
- [x] 所有接口显式 scope 校验；无 scope 不返回跨 scope 数据；令牌/权限与现有 Studio 一致。

## 3. 聊天命令

- [x] `/记忆 状态`
- [x] `/记忆 搜索 <关键词>`
- [x] `/记忆 忘记`（仅允许的策略/权限）
- [x] `/记忆 删除 <id>`
- [x] `/记忆 整理`（dry-run 预览）
- [x] 默认关闭，需显式开启；群管理员/私聊管理员限定；不把其他群内容拼进回复。

> 实现（qqbot_enhancer 2.9.0）：`plugins/qqbot_enhancer/memory_commands.py`。
> `memory_commands_enabled=false` 默认关闭，`删除/忘记` 另需
> `memory_commands_destructive_enabled=true`；群聊只管理本群 `group` 作用域，
> 私聊只管理自己的 `user` 作用域，从不调用 `export_all()`；未打补丁或
> `memory.enable=false` 时明确不可用。测试：`tests/test_memory_commands.py`
> （36）+ `tests/test_enhancer_plugin.py::TestMemoryChatCommands`（8）。

## 4. 手动新增/编辑

> 实现：v4.2.1-beta.1（t33）。`POST/PATCH memory/records`，校验 scope/长度/kind/0-1 概率/密钥，同键/同内容去重；见 [`memory-admin-api.md`](memory-admin-api.md) §3。

- [x] Studio 手动 add；
- [x] 编辑内容/kind/importance/confidence/TTL；
- [x] 校验 scope、长度、密钥清洗；
- [x] 写入幂等/去重。

## 5. 导入/恢复

> 实现：v4.2.1-beta.1（t33）。预览/冲突汇总/去重策略/单事务回滚/0600 备份/不覆盖其他 scope；见 [`memory-admin-api.md`](memory-admin-api.md) §4。

- [x] JSON schema 校验；
- [x] scope 冲突预览；
- [x] 去重策略；
- [x] 原子事务；失败回滚；
- [x] 不覆盖其他 scope。

## 6. Reflection / 记忆整理

- [ ] 纯本地、可解释的 dry-run；
- [ ] 同 scope 近重复合并；
- [ ] 矛盾记忆报告，不擅自删除；
- [ ] 低价值/过期预览；
- [ ] apply 前备份；
- [ ] 默认不调用 LLM，不增加聊天路径 token。

## 7. LLM 抽取

- [ ] 保留 heuristic 为默认；
- [ ] 可配置 LLM extractor（模型/endpoint/超时/最大输入）；
- [ ] 仅异步抽取，不阻塞回复；
- [ ] 失败 fail-open 回退 heuristic/跳过；
- [ ] 密钥清洗、预算上限、日志不含正文；
- [ ] 测试用本地 stub，不产生真实费用。

## 8. 语义检索

- [ ] 保留 FTS5/BM25 为基线；
- [ ] 可选 embedding provider（本地/远程 endpoint）；
- [ ] SQLite 存 embedding BLOB，余弦相似度；
- [ ] 与词面分数融合、scope 先过滤；
- [ ] 未配置/失败自动回退 BM25；
- [ ] 不把跨群向量命中绕过 policy。

## 9. 群内说话人维度

- [ ] 新增显式 scope（建议 `group_member` 或 `group_speaker`），不复用 `group_user` 的写语义；
- [ ] 记录发送者身份，但群内读取仍需策略 opt-in；
- [ ] 私聊/跨群隔离测试；
- [ ] 删除/导出按 scope 精确匹配；
- [ ] 文档写清 A 的隐私不会变成 B 的事实。

## 测试协议（每次更新后用户执行）

### 零成本

```bash
python3 -m unittest discover -s tests -q
cd integrations/astrbot-memory && ASTRBOT_SRC_TGZ=<astrbot-v4.29.3.tar.gz> bash scripts/verify-patch.sh /tmp/verify
```

### 本地 Docker（Windows 宿主 Docker）

```bash
# WSL 调用 Windows Docker
/mnt/e/Docker/resources/bin/docker.exe info
/mnt/e/Docker/resources/bin/docker.exe build -f integrations/astrbot-memory/Dockerfile.memory -t astrbot-memory-test .
```

### 真实对话（人工）

1. 使用独立测试实例/测试群，备份 `data/`；
2. 设置该群 `mode=long_term`, `scope=group_user` 或新 speaker scope, `extract=true`, `inject=true`；
3. 发“记住：我不吃香菜”；
4. 检查面板/API 出现记录；
5. 再问“晚饭推荐”，确认回复参考；
6. 重启 AstrBot，确认记录仍在；
7. 删除记录，确认下一轮不再注入；
8. 检查其他群/私聊不可见；
9. 检查日志无密码/API Key。

### LLM/Embedding 预算

- 默认 `llm_extract=false`、embedding 关闭；
- 测试时使用本地 stub 或 `max_input_chars`/`max_calls` 严格上限；
- 每次更新只跑一次真实调用，不在每轮消息中调用；
- 预算以“每次抽取最多 1 次、输入 ≤ N 字符、超时 ≤ 10s”控制。

## 代码优化

已确认安装 `superpowers-dsh@0.4.0`（提供 TDD、系统化调试、计划、子 agent 开发、完成前验证等技能）；已有 `ponytail-optimizer` teammate 可做全项目优化审计。按这些方法论执行：
- 先契约/测试，后实现；
- 消除重复状态与手写解析器（已用 marked/DOMPurify 替换手写 Markdown）；
- 模块拆分为 manager/store/policy/render；
- 所有管理操作可审计、可回滚；
- 任何 LLM/向量能力默认关闭、失败回退。
