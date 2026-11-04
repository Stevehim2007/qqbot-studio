# WorkBuddy 架构师 + DSH 执行工作流

## 角色

```text
WorkBuddy ChatGPT：架构、拆分、接口、验收标准、最终 review
DSH / Lead：读取真实仓库、生成任务、改代码、测试、提交、部署
```

WorkBuddy 个人版没有 API，因此不自动直连；采用可审计的人工交接。

## 交接文件

### 1. `ARCHITECT_BRIEF.md`

由 Lead 填写，交给 WorkBuddy：

```text
项目/仓库：
当前版本与提交：
目标：
非目标：
相关文件：
关键接口/函数：
数据模型：
约束：
验收命令：
已知错误/日志：
允许修改范围：
禁止修改范围：
```

### 2. `IMPLEMENTATION_PLAN.md`

WorkBuddy 返回：

```text
阶段拆分
每个文件要做什么
新增/修改接口
测试用例
迁移步骤
风险与回退
验收标准
```

### 3. `REVIEW_BRIEF.md`

Lead 执行后整理：

```text
变更摘要
提交 hash
测试结果
失败/已知限制
需要 WorkBuddy 重点 review 的点
```

## 省 token 规则

- WorkBuddy 只看 brief、接口、关键片段，不贴整个仓库；
- DSH 只加载相关文件和测试；
- 日志只发最后 50 行和首个关键错误；
- 架构决策缓存到 brief，不重复解释；
- 常规执行使用 DeepSeek/Kimi，ChatGPT 只做高价值架构/review；
- 不在 brief 中放密码、API Key、VPS 登录信息、QQ 凭据。

## 当前已安装的辅助插件

```text
@wenaixi/dsh-ponytail 5.6.2
superpowers-dsh 0.4.0
```

- Ponytail：全项目优化、上下文/实现审查；
- Superpowers：计划、TDD、系统化调试、review、完成前验证。

`dsh --dump-config` 在当前工作区可能因 profile 的 `cordis.yml` 只读而失败（EROFS），这不是插件未安装；以 `package.json`/`cordis.patch.yml` 和实际技能目录为准。

## 每轮流程

```text
需求 → Brief → WorkBuddy 方案 → DSH 实施 → 测试 → Review Brief
→ WorkBuddy review → 修复 → 正式/测试版本发布 → 文档同步
```
