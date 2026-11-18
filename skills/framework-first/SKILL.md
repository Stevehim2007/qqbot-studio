---
name: framework-first
description: 在实现任何功能前，先让 GPT/WorkBuddy 或固定的 Pro 框架 session 产出完整 Markdown 框架，再实施。
---

# Framework First

当用户提出新功能、重构、插件、迁移或部署时：

1. 先写 `docs/FRAMEWORK_BRIEF.md`；
2. 发给 WorkBuddy/GPT，要求完整 Markdown 框架；
3. GPT/WorkBuddy 不可用时，必须复用同一个已有的 Pro 模型框架 session（记录并固定其 session id），只让它修改框架；**不得每次新建框架 session**。只有当前没有可复用 Pro 框架 session 时，才创建一次并固定复用；
4. 审查框架；
5. 再实现、测试、部署。

禁止跳过框架直接复制旧插件或堆兼容包。

## 固定框架 session 规则

- 所有框架修订优先发给同一个 Pro 模型 session；记录 session id，后续继续使用。
- 不允许因为“新问题”就新开框架 session；新问题作为同一 session 的增量修订。
- 只有在原 session 丢失/不可恢复时，才创建替代 session，并在框架文档头部记录替换关系。

