# 插件模块化与插件仓库评估

## 结论

**插件安装/删除市场值得做；立即把 `qqbot_enhancer` 硬拆成多个插件不值得。**

当前 `qqbot_enhancer` 有 264 个 schema 字段，包含图片搜索、网页访问、Food、表情、人格、安全工具、聊天命令、会话辅助等子系统。它们共享：
- 同一事件拦截和优先级；
- 同一份配置 schema/默认值；
- safe tool marker 解析；
- 发送者身份/群上下文；
- 日志、存储、冷却和失败回退。

直接拆会导致：
- 多个插件重复收取同一事件；
- 多个插件重复回复；
- 配置和迁移不一致；
- 管理和卸载依赖关系复杂；
- 现有用户升级风险高。

## 推荐分阶段

### 阶段 1：插件仓库与安装/删除（优先）

新增 `qqbot-plugins` 仓库/index，每个条目包含：
```json
{
  "id": "qqbot_enhancer_food",
  "name": "Food",
  "version": "1.0.0",
  "astrbot": ">=4.29,<5",
  "sha256": "...",
  "download": "...",
  "dependencies": []
}
```

Studio 增加：
- 插件市场列表；
- 已安装/可安装/可更新状态；
- 安装前备份、SHA256 校验、依赖检查；
- 删除前确认，并拒绝删除仍被依赖的插件；
- 不删除实例 `data/`、配置和登录态。

### 阶段 2：抽出独立能力

按低耦合到高耦合逐步抽取：
1. `web_fetch`（网页读取）；
2. `image_search`；
3. `food`；
4. `sticker`；
5. `memory_commands`；
6. 保留一个 `qqbot_enhancer` 兼容/编排包。

每个新插件必须先有独立测试、配置 migration 和“未安装时主插件仍可用”的兼容路径。

### 阶段 3：共享核心

如果多个插件确实共享大量逻辑，再抽出：
```text
qqbot_enhancer_core
```
只包含：
- 事件/群/发送者上下文；
- 安全 marker 解析；
- 配置和日志适配；
- 限流/失败回退。

不要从第一天就建共享运行时；先让市场安装、版本、备份和回滚稳定。

## 风险与验收

- 安装包必须固定 SHA256，不允许任意 URL 直接执行；
- 删除插件不能删除实例数据；
- 插件版本必须与 AstrBot 版本有兼容范围；
- 安装/删除后自动跑 `scripts/post-update-test.sh` 类似的验证；
- 先做市场管理器，不要先做大规模拆分。

## 建议优先级

```text
P0 插件市场安装/删除/更新 API + UI
P1 web_fetch / image_search 独立化
P2 food / sticker 独立化
P3 memory_commands / 兼容包拆分
```

## 插件分组（必须支持）

插件市场需要支持用户自定义插件分组，例如：

```text
分组：QQ Bot
  - qqbot_core_reply
  - qqbot_image_search
  - qqbot_food
```

要求：创建/重命名/删除分组；把插件加入/移出分组；删除分组不能删除插件；删除插件要自动移除分组关系；插件列表按分组过滤/折叠；分组持久化在 Studio 实例数据，不写入 AstrBot 插件源码。
