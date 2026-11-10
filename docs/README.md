# QQ Bot Studio 文档索引

本目录是文档入口。第一次使用时先读主项目 [`README.md`](../README.md)，需要排查服务器问题时再按下面的索引进入专项文档。

## 1. 入门与安装

| 文档 | 内容 | 适用对象 |
|---|---|---|
| [`../README.md`](../README.md) | 最短安装、首个机器人、管理控制台、进阶功能、FAQ、声明 | 所有用户 |
| [`server-troubleshooting.md`](server-troubleshooting.md) | Git SSL、Docker、0.0.0.0/127、腾讯云安全组、防火墙、SSH 隧道、AstrBot WebUI、端口冲突等真实故障 | 云服务器/远程 Ubuntu 用户 |

## 2. 功能与接口

| 文档 | 内容 |
|---|---|
| [`plugin-qqbot-enhancer.md`](plugin-qqbot-enhancer.md) | QQ Bot 增强回复插件：@必回、概率、识图/找图、表情包、唱歌、`/option`、配置项和限制 |
| [`food-ai-submission.md`](food-ai-submission.md) | 自然语言美食提交（`submit_food_review`）：t1 站点契约、触发与写入门槛、密钥安全、测试证据、猫娘群 Bot 提示词 |
| [`food-ai-contract/API_AI_REVIEW.md`](food-ai-contract/API_AI_REVIEW.md) | food.dlsz.top `POST /api/upload` 完整契约、错误语义、v1.1 可选补丁与部署步骤 |
| [`ui-api-contract-v2.md`](ui-api-contract-v2.md) | v2 控制台、向导、多实例、镜像、插件、记忆、统计、自动更新和 AI 助手 B 的 HTTP API 契约 |
| [`memory-policy.md`](memory-policy.md) | 控制台「记忆策略」面板：按群长期记忆 `memory.policy` 结构、`/api/instances/<id>/memory-policy` 契约、校验规则、限制与隐私边界 |
| [`plugin-market-v1.md`](plugin-market-v1.md) | 测试版插件市场 v1：manifest/SHA256/ZIP 安全边界、安装更新删除、SQLite 分组、预览/确认/幂等 API 与配置迁移 field-map 预览 |
| [`memory-admin-api.md`](memory-admin-api.md) | 控制台「记忆内容」面板：按 scope 查看/编辑/删除/忘记/导出/导入/整理记忆，`/api/instances/<id>/memory/...` 契约、确认字段、状态码、隐私与已知限制 |
| [`memory-system.md`](memory-system.md) | 记忆系统完整说明：五 scope、BM25/FTS5 与回退、可选语义检索、启发式抽取、Reflection、按群策略，以及下一版 SurfCore 活跃/归档记忆卡设计 |
| [`group-plugin-config.md`](group-plugin-config.md) | 分群插件启用与分群参数设计：数据模型、字段级覆盖、优先级、运行时快照、Studio/API、审计与双群验收 |
| [`next-major-decisions.md`](next-major-decisions.md) | 下一大版本 8 项拍板与解释：插件拆分、侧车、首版预算、可见/删除权、五/六 scope、词汇库、测试版灰度、人格四层 |
| [`update-rollback.md`](update-rollback.md) | 更新通道（稳定/测试）、同大版本任意回滚、备份与失败恢复、schema 兼容、历史与 `/api/update/*` 契约 |
| [`integration-research.md`](integration-research.md) | AstrBot / QQ 官方平台 / DeepSeek 集成字段调研与来源（历史资料） |
| [`../plugins/qqbot_enhancer/README.md`](../plugins/qqbot_enhancer/README.md) | 插件开发者/高级用户的文件结构、安装、搜索、点歌与 `/option` 说明 |
| [`../plugins/qqbot_enhancer/songs/README.md`](../plugins/qqbot_enhancer/songs/README.md) | 本地点歌目录、授权音频格式和文件限制 |

## 3. QA 与版本记录

- [`qa-report-v2.md`](qa-report-v2.md)：当前 v4.1.1 条件验收报告、真实 E2E 待测清单和已知限制。
- [`ponytail-audit.md`](ponytail-audit.md)：Ponytail full 第一阶段全仓审计、低风险精简记录、保留/拒绝项和剩余债务。
- [`ponytail-optimization-phase2.md`](ponytail-optimization-phase2.md)：第二阶段跨页 helper、Git runner 与测试 fixture 精简记录、实际净行数、验证和 Windows 同步影响。
- [`qa-report.md`](qa-report.md)：旧版单实例 v1 时代的 QA 快照，仅作历史记录；接口和端口不要按它配置。
- [`ui-api-contract.md`](ui-api-contract.md)：旧版单实例 API 契约，仅作历史兼容参考；新开发请使用 [`ui-api-contract-v2.md`](ui-api-contract-v2.md)。

## 4. 平台与其他项目

| 项目/文档 | 说明 |
|---|---|
| [`../windows/README.md`](../windows/README.md) | 主仓库内保留的 Windows 初始移植资料/开发记录；正式 Windows 发行请使用独立仓库 |
| [QQ Bot Studio Windows](https://github.com/Stevehim2007/qqbot-studio-windows) | Windows 10/11 独立发行项目，Docker Desktop + WSL2，不是 Ubuntu 主项目的同一发行包 |
| [Windows EXE 构建 workflow](https://github.com/Stevehim2007/qqbot-studio-windows/actions/workflows/windows-build.yml) | 从最新成功的 `windows-build` artifact 下载 `QQBotStudioWindows.exe`；当前未提供正式 Release EXE 资产 |
| [Windows Releases](https://github.com/Stevehim2007/qqbot-studio-windows/releases) | 后续正式 EXE 发布页；当前 Releases 为空时请使用 Actions artifact |
| [QQ Bot Studio AI Assistant B](https://github.com/Stevehim2007/qqbot-studio-ai-assistant) | 可选、独立的生成式 AI 使用助手 B；主控制台通过独立 /assistant 页面管理 |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 上游聊天机器人框架 |
| [DeepSeek API Docs](https://api-docs.deepseek.com/) | DeepSeek API 文档 |

## 5. 安全提醒

- 管理服务默认监听 `0.0.0.0:8765`；每个机器人实例只发布一个自动分配的 AstrBot WebUI 端口。
- 不要把管理端口或 AstrBot WebUI 直接暴露到公网；先用安全组、防火墙、VPN 或 SSH 隧道限制访问。
- 本项目不保存 QQ 密码，不绕过 QQ 开放平台的授权、频控或权限要求。
- API Key、访问令牌、QQ 官方 AppSecret 不要贴到公开 issue、聊天截图或日志中。
- Windows 是独立项目，不能与 Linux 主项目共用数据目录或自动更新；AI 助手 B 也是独立可选项目，主项目不会自动启动它或传递它的 API Key。

- [`qa-report-final.md`](qa-report-final.md)：Ponytail 第二阶段后的最终 QA（v4.1.1，0 P0/P1）

- [CHANGELOG.md](../CHANGELOG.md)：完整版本更新日志



- [`mobile-access.md`](mobile-access.md)：Android 通过同一 LAN/VPN 安全访问 Linux DSH。

- [`plugin-market-v1.md`](plugin-market-v1.md)：插件市场、安装/更新/删除、分组与安全校验。
- [`plugin-standard.md`](plugin-standard.md)：插件目录标准、schema、tools.json 与 native 工具桥。
