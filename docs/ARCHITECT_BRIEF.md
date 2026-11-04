# ARCHITECT_BRIEF：QQ Bot Studio 插件仓库与模块化

## 项目

- 主项目：QQ Bot Studio Linux `v4.3.0`
- 新仓库目标：`qqbot-plugins`（待创建，独立于 `QQ Ai bot` 主仓库）
- 当前状态：`qqbot_enhancer` 单体插件，`_conf_schema.json` 264 个字段
- 已有能力：图片搜索、网页读取、Food、表情、安全工具、`/记忆` 命令、会话辅助

## 目标

设计并给出一个可执行方案：

1. 新建独立 GitHub 插件仓库 `qqbot-plugins`；
2. 插件源码按能力拆分，每个插件独立 `metadata.yaml`、`_conf_schema.json`、`main.py`、测试、版本；
3. Studio 增加插件市场：发现、安装、删除、更新、SHA256、依赖/版本校验；
4. 保留兼容迁移路径，不能丢失现有用户配置；
5. 避免多个插件重复处理同一条消息、重复回复或重复调用模型；
6. 主项目只保留插件市场客户端和安装/卸载编排，不把 264 字段继续塞进单个插件。

## 建议拆分

```text
qqbot_core_reply
qqbot_safe_tools
qqbot_image_search
qqbot_web_reader
qqbot_food
qqbot_stickers
qqbot_memory_commands
```

## 必须回答的架构问题

- 插件事件优先级和互斥机制怎么设计；
- safe_tools marker 由哪个插件拥有，其他插件如何复用；
- 旧 `qqbot_enhancer_config.json` 如何按 key 迁移到多个插件；
- 插件安装/卸载时如何备份、回滚、校验；
- 插件市场如何做版本/兼容性/依赖；
- 删除插件为什么不能删除实例 `data/`；
- 分阶段落地顺序和每阶段验收命令。

## 验收

- 每个插件 schema 小于 100 字段（核心/兼容包可例外）；
- 单插件安装/卸载不影响其他插件；
- 安装/删除 API 有 scope/权限/SHA256/失败回滚测试；
- 旧配置迁移测试；
- 一条消息不会触发多个插件重复回复；
- 主项目测试和插件仓库测试均可独立运行。

## 禁止

- 不把新插件仓库放进 `/home/stevehim/QQ Ai bot`；
- 不把 API Key、QQ 密码、VPS 密码写入 brief 或仓库；
- 不先拆高耦合的 QQ 回复核心；
- 不删除实例数据目录。
