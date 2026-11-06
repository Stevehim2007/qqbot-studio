---
name: token-budget
description: 低成本执行多步骤任务：单写入 session、文件 handoff、按需读取、分阶段测试，避免重复上下文和重复计费。
---

# Token Budget

## 触发场景

- 任务预计超过 6 个工具调用；
- 需要使用 WorkBuddy/GPT；
- 需要开子 session 或 subagent；
- 同一仓库多任务并行；
- 用户提到费用、token、上下文、重复读取。

## 核心原则

```text
session 不共享上下文
文件才是共享内存
只传路径，不贴全文
一个仓库同时只允许一个写入 owner
```

## 1. 只开一个执行 session

默认：

```text
1 个主协调 session
1 个执行 session
```

不要为每个小任务开新 session。

只有满足以下条件才新开：

```text
上下文已被无关任务污染
任务切换到完全不同的仓库
需要隔离验证且不能复用当前 worktree
```

## 2. 先写 Brief，再让 GPT 做框架

Brief 控制在 2KB 内：

```text
目标
非目标
相关文件路径
当前状态
验收命令
禁止事项
```

GPT/WorkBuddy 只输出一份 Markdown 框架文件，不直接改代码。

如果 GPT 不可用：

```text
新开一个架构 session
只产出框架
禁止写代码
```

不要同时开多个架构 session。

## 3. 用 STATE.md 做 handoff

统一写：

```text
STATE.md
```

内容：

```md
# STATE

## Goal

## Decisions

## Changed files

## Tests

## Known failures

## Next step
```

新 session 只读：

```text
STATE.md 路径
相关文件路径
必要函数片段
```

不要重新贴：

```text
完整历史
整份架构文档
完整日志
全仓库源码
```

## 4. 按需读取文件

优先使用：

```bash
grep -n "关键词" file.py
sed -n '120,220p' file.py
python3 -m unittest tests.test_xxx -q
```

不要：

```bash
cat 大文件
find 全仓库
重复读取同一文件
重复输出完整测试日志
```

长 Markdown 用工具读取片段，不要整份贴进聊天。

## 5. 分阶段测试

修改阶段：

```bash
python3 -m unittest tests.test_target -q
```

阶段结束才：

```bash
python3 -m unittest discover -s tests -q
```

服务器全量测试只在发布验收时跑。

## 6. 不重复轮询

不要反复调用：

```text
session_bridge_status
session_bridge_wait
workbuddy_poll
```

优先：

```text
一次长等待
或让任务写结果文件
然后读文件
```

任务完成或停滞就停止该 session。

## 7. 模型路由

```text
GPT/WorkBuddy：架构、评审、关键决策
DeepSeek/Kimi：实现、grep、测试、修复
小模型：格式检查、简单分类
```

不要让 GPT 做重复的文件读取和命令执行。

## 8. 并发限制

```text
同一 worktree 同时最多 1 个写入 session
不同 worktree 才允许并行
并行 session 总数默认不超过 2
```

并行前必须确认：

```text
文件不重叠
数据不重叠
测试端口不冲突
```

## 9. 停止和整理

任务结束：

```text
写 STATE.md
停止 session
清理临时文件
不删除 data/配置/会话
```

发现 session 停滞：

```text
先读一次状态
然后取消或转交
不要连续重试同一调用
```

## 10. 成本检查

每完成一个阶段记录：

```text
session 数
工具调用数
是否重复跑全量测试
是否重复读取大文件
是否并行写同一仓库
```

如果费用异常，先检查这四项，而不是继续换模型。
