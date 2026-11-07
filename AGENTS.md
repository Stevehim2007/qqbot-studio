# Agent Workflow Gate

任何功能性改动开始前，必须先完成框架阶段：

1. 根据 `docs/FRAMEWORK_BRIEF.md` 写需求 brief；
2. 发给 WorkBuddy/GPT，要求输出一份完整 Markdown 框架；
3. 如果 GPT/WorkBuddy 不可用，启动一个全新上下文的 session，只让它做框架/任务书，不写业务代码；
4. 人工审查框架与当前仓库是否一致，列出验收项；
5. 框架通过后，才允许创建实现任务或改代码。

禁止跳过框架直接堆兼容包、复制旧插件或先上生产再补设计。

详细流程见 `docs/FRAMEWORK_FIRST.md`，skill 见 `skills/framework-first/SKILL.md`。

成本与上下文规则见 `skills/token-budget/SKILL.md`。默认只允许一个执行 session、一个写入 worktree，使用 `STATE.md` 做 handoff。
