# 更新后确认清单

每次通过 Studio 更新或回滚后，按下面的顺序确认。自动测试通过不等于真实 QQ/记忆链路通过。

## 1. 自动测试结果

查看：

```text
~/.local/share/qqbot-studio/auto-update.log
```

必须看到：

```text
=== post-update tests PASS ===
```

如果看到：

```text
=== post-update tests FAILED; management server will NOT be restarted ===
```

不要继续操作；旧管理服务仍在运行，先根据日志修复。

## 2. 容器状态

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
```

AstrBot 和 SnowLuma 都应显示 running；AstrBot 最好为 healthy。

## 3. AstrBot memory patch

```bash
sudo docker exec <astrbot容器> python3 -c \
  "import sys; sys.path.insert(0, '/AstrBot'); import astrbot.core.memory as m; print(m.__file__)"
```

预期有输出且不报 `ModuleNotFoundError`。

## 4. 记忆配置

确认 `cmd_config.json` 中：

```text
memory.enable = true
```

确认策略 sidecar：

```text
data/astrbot/memory_policy.json
```

至少有一个测试群配置：

```text
mode=long_term
extract=true
inject=true
scope=group_user 或 group_member
```

## 5. 数据库

```bash
sudo find <实例目录>/data/astrbot -maxdepth 1 -name 'astrbot_memory.db*' -print
```

首次阶段可以不存在；发送一条真实“记住”消息后再检查。

## 6. `/记忆` 命令

插件参数中确认：

```text
memory_commands_enabled = true
```

需要删除/忘记时再打开：

```text
memory_commands_destructive_enabled = true
```

发送：

```text
/记忆 状态
/记忆 搜索 生日
```

## 7. 写入和注入

在已配置的测试群发送：

```text
记住：我的生日是 1995-06-15
```

然后在 Studio「记忆内容」中按 scope 查询，或：

```text
/记忆 搜索 生日
```

再问一个需要该信息的问题，确认回复参考了记忆。

## 8. 隔离

确认：

- 其他群看不到本群记录；
- 私聊看不到群记录；
- `group_member` 只能看到自己的记录；
- 删除/忘记只影响当前 scope。

## 9. food.dlsz.top

每次 food 站点修改后必须重新部署，并检查：

```bash
curl -fsS https://food.dlsz.top/ >/dev/null
curl -fsS https://food.dlsz.top/changelog
```

Markdown、全地址搜索、已有位置直接选中分别按 `FOOD_SITE_V13.md` 的验收项检查。

## 10. 失败时

1. 停止更新，不要反复重启；
2. 保留 `auto-update.log`；
3. 使用更新前备份回滚；
4. 不删除 `astrbot_memory.db`、`cmd_config.json` 或实例 `data/`；
5. 修复后重新跑上面的自动测试，再做真实对话测试。
