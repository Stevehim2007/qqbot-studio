# QQ Bot Studio 文档索引

> 当前主项目：`5.0.0-beta.24`；插件仓库：`v4.4.0-beta.60`；运行时 12 个插件。
>
> 第一次使用先读主项目 [`README.md`](../README.md)。本索引只列当前有效文档；旧 QA 快照、已删除插件的说明和过时 API 契约已移除，历史版本可在 git 历史中查。

## 1. 入门、安装与运维

| 文档 | 内容 |
|---|---|
| [`../README.md`](../README.md) | 安装、首个机器人、控制台、记忆/插件/更新、FAQ、安全 |
| [`server-troubleshooting.md`](server-troubleshooting.md) | Git SSL、Docker、0.0.0.0/127、腾讯云安全组、SSH 隧道、端口冲突 |
| [`update-rollback.md`](update-rollback.md) | 稳定/测试更新通道、任意回滚、备份、失败恢复 |
| [`mobile-access.md`](mobile-access.md) | Android 通过 LAN/VPN 访问 DSH |
| [`e2e-validation.md`](e2e-validation.md) | 真实 QQ 端到端验证清单与边界 |

## 2. 当前功能

| 文档 | 内容 |
|---|---|
| [`v7-feature-guide.md`](v7-feature-guide.md) | V1–V7 完整使用手册：参与模式、人格/语料、好感度、表达状态、记忆、Studio、测试、回滚 |
| [`memory-layers.md`](memory-layers.md) | **四层记忆**：人格记忆、群记忆、个人 AI 记忆、共享知识库；自动召回、表结构、控制台、配置、边界 |
| [`memory-system.md`](memory-system.md) | 记忆系统底层：scope、BM25/FTS5、卡片生命周期、历史设计 |
| [`memory-admin-api.md`](memory-admin-api.md) | 控制台「记忆内容」原生记忆 API 契约与隐私边界 |
| [`memory-policy.md`](memory-policy.md) | 记忆策略/按群长期记忆结构、校验与 API |
| [`memory-management-plan.md`](memory-management-plan.md) | 记忆管理面设计与迁移计划（历史设计依据） |
| [`group-plugin-config.md`](group-plugin-config.md) | 分群插件启用、字段级覆盖、优先级、审计 |
| [`plugin-standard.md`](plugin-standard.md) | 插件目录、schema、tools.json、native 工具桥标准 |
| [`plugin-dependencies.md`](plugin-dependencies.md) | 插件依赖声明、检测、pip/apt 安装 |
| [`plugin-market-v1.md`](plugin-market-v1.md) | 插件市场 manifest/SHA256/ZIP 安全、安装更新删除、SQLite 分组 |
| [`default-off-parameters.md`](default-off-parameters.md) | 默认关闭/高风险参数清单 |
| [`post-update-verification.md`](post-update-verification.md) | 更新后版本脚本与自检 |
| [`ui-api-contract-v2.md`](ui-api-contract-v2.md) | 控制台、向导、实例、插件、记忆、统计、更新 HTTP API 契约 |

## 3. 架构与下一版设计

| 文档 | 内容 |
|---|---|
| [`architecture-modularization.md`](architecture-modularization.md) | 模块化/接口化架构、目标边界与迁移阶段 |
| [`plugin-modularization.md`](plugin-modularization.md) | 插件拆分原则、依赖与数据所有权 |
| [`FRAMEWORK_BRIEF.md`](FRAMEWORK_BRIEF.md) | 下一版框架简报 |
| [`FRAMEWORK_FIRST.md`](FRAMEWORK_FIRST.md) | 框架优先工作流、Pro session 复用规则 |
| [`next-major-brief.md`](next-major-brief.md) | 下一大版本需求简报 |
| [`next-major-decisions.md`](next-major-decisions.md) | 8 项拍板与解释 |

## 4. 食物 / 平台 / 研究资料

| 文档 | 内容 |
|---|---|
| [`food-ai-submission.md`](food-ai-submission.md) | 自然语言美食提交：站点契约、触发、写入门槛、密钥安全 |
| [`food-ai-contract/README.md`](food-ai-contract/README.md) | food.dlsz.top 契约、补丁说明、测试脚本 |
| [`integration-research.md`](integration-research.md) | AstrBot / QQ 官方 / DeepSeek 集成调研（历史资料） |
| [`windows-editions-research.md`](windows-editions-research.md) | Windows 发行形态调研（历史资料） |

## 5. 测试与版本

| 文档 | 内容 |
|---|---|
| [`v7-feature-guide.md`](v7-feature-guide.md) | 功能验收、使用和回滚的总入口 |
| [`v6-test-plan.md`](v6-test-plan.md) / [`v6-test-report.md`](v6-test-report.md) | V6 表达状态测试计划/报告 |
| [`v5-test-plan.md`](v5-test-plan.md) / [`v5-test-report.md`](v5-test-report.md) | V5 参与/好感度测试计划/报告 |
| [`../CHANGELOG.md`](../CHANGELOG.md) | 完整版本更新日志 |
| [`../STATE.md`](../STATE.md) | 当前分支、版本、部署和已知边界 |

## 6. 安全提醒

- 管理服务默认 `0.0.0.0:8765`；不要直接暴露公网。
- API Key、AppSecret、token 不写入日志/截图/issue。
- 记忆库不含 QQ 密码；`actor_hash` 是哈希，不存明文 QQ 号。
- 共享知识库只读；模型不能写入用户导入资料。
