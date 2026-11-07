# QQ Bot Plugin 标准

## 1. 命名

```text
插件 ID：qqbot_<domain>
目录：plugins/qqbot_<domain>/
版本：X.Y.Z
```

插件 ID 必须只使用小写字母、数字和下划线，且与 ZIP 唯一顶层目录一致。

## 2. 每个插件必须独立包含

```text
plugins/qqbot_xxx/
├── metadata.yaml
├── VERSION
├── _conf_schema.json
├── main.py
├── __init__.py
└── tests/
```

禁止依赖主项目源码、旧 `qqbot_enhancer` 目录、`site-packages` 修改或 pip 安装。

## 3. 单一职责

```text
qqbot_core_reply        主回复/会话/人格/概率
qqbot_safe_tools        marker 协议/白名单/调度
qqbot_web_reader        明确网页命令
qqbot_image_search      明确图片搜索命令
qqbot_food              Food 查询/提交/确认
qqbot_stickers          表情收藏/索引/发送
qqbot_memory_commands   记忆管理命令
```

- 业务插件只处理自己的明确命令；
- 不抢占普通聊天；
- 不启动主模型，除非是 `qqbot_core_reply`；
- 不自行解析 safe_tools marker，除非是 `qqbot_safe_tools`；
- 不在插件初始化时迁移或覆盖用户数据。

## 4. 配置

- 配置字段必须写在 `_conf_schema.json`；
- 默认值必须安全；
- 不在代码里硬编码用户 QQ、群号、密码、API Key；
- 配置路径由 AstrBot/StarTools 提供；
- 新插件配置必须保证 Studio 运行用户可以读写；
- 配置损坏时保留原文件，不自动覆盖。

## 5. 版本与 release

```text
tag：qqbot_xxx-vX.Y.Z
zip：qqbot_xxx-X.Y.Z.zip
```

ZIP 约束：

```text
唯一顶层目录 = 插件 ID
无符号链接/绝对路径/.. /特殊文件
不含安装脚本
不执行 pip
```

manifest 记录：

```json
{
  "id": "qqbot_xxx",
  "version": "X.Y.Z",
  "sha256": "...",
  "size": 1234,
  "download": "https://github.com/Stevehim2007/qqbot-plugins/releases/download/.../qqbot_xxx-X.Y.Z.zip",
  "root": "qqbot_xxx"
}
```

## 6. 测试

每个插件至少覆盖：

```text
命令解析
正常结果
参数错误
网络/超时错误
权限/安全边界
不调用主模型（业务插件）
不重复发送/执行
配置缺失或损坏
```

运行：

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

业务插件测试中把模型调用入口替换成“调用即失败”。

## 7. 添加新插件流程

```text
1. 复制 templates/plugin/
2. 改成 qqbot_<domain>
3. 实现明确命令
4. 填写 metadata/VERSION/schema
5. 写测试并运行
6. 构建 ZIP 并计算 SHA256
7. 创建 tag + Release + 上传 ZIP
8. 更新 manifest.json
9. 在隔离实例安装并检查插件加载
10. 通过后才更新生产实例
```

## 8. 禁止事项

```text
禁止把旧 qqbot_enhancer 整个复制成新插件
禁止多个插件同时处理同一条普通聊天
禁止未授权写外部系统
禁止删除实例 data/、记忆数据库、会话、QQ 登录态
禁止把真实密钥写入 Git、日志或测试 fixture
```

## 9. Studio 与 Safe Tools 接入

新插件进入项目时必须同时完成：

```text
1. 插件目录和 _conf_schema.json
2. 独立测试
3. manifest/catalog/release ZIP
4. Studio 插件市场登记
5. 如需 AI 调用：在 qqbot_safe_tools 显式登记 tool_id、目标插件、tool_* 方法、allow 开关、冷却、确认和超时
```

Safe Tools 当前是安全白名单，不会因为插件安装就自动获得 AI 调用开关。新增插件需提交独立登记和测试，默认关闭，由管理员显式开启。

## 10. Studio 实例设置约定

```text
插件管理：插件状态、折叠分组、组内插件参数
插件市场：按页安装/更新/删除，每页 5 个
插件参数：群组白名单等平台参数通过 qqbot_core_reply 参数保存
记忆：记忆模式、按群策略和记忆内容放在同一栏
```

插件市场保存的插件字段可能热重载；AstrBot 的 `platform_settings`（例如群组白名单）需要重启 AstrBot 才生效。保存通知显示在参数面板顶部。
