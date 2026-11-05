---
name: framework-first
description: 在实现任何功能前，先让 GPT/WorkBuddy 或全新 session 产出完整 Markdown 框架，再实施。
---

# Framework First

当用户提出新功能、重构、插件、迁移或部署时：

1. 先写 `docs/FRAMEWORK_BRIEF.md`；
2. 发给 WorkBuddy/GPT，要求完整 Markdown 框架；
3. GPT 不可用时，新开 session 只做框架；
4. 审查框架；
5. 再实现、测试、部署。

禁止跳过框架直接复制旧插件或堆兼容包。
