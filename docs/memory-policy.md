# 记忆策略面板（按群长期记忆 memory.policy）

控制台实例设置里的「记忆策略」面板用于编辑 AstrBot 原生长期记忆的**按群策略**（`memory.policy`），
不需要手工改 JSON，也不需要打 Studio 侧补丁。面板只做两件事：读 AstrBot 配置里的
`memory.policy`，以及把校验通过的策略写回同一个键。

## 1. 生效范围（先读这一段）

- **只有面板里列出的群**会写入/读取长期记忆；其余群、以及所有未单独配置的私聊保持
  `conversation`，也就是只用 AstrBot 会话历史，不写库、不注入。
- 面板顶部固定显示这条警告，桌面端与移动端都会显示；保存接口返回的 `warning` 字段是同一句话，
  前端不自行改写。
- 长期记忆总开关仍是 AstrBot 的 `memory.enable`。若它为 `false`，策略可以保存但**不会生效**，
  面板会在状态行明确提示；Studio 不会假装已生效。
- 面板写的是 AstrBot 配置文件；上游**没有** `memory.policy` 的在线接口，所以保存后需要
  AstrBot 重启或重新加载配置才生效（面板不调用热重载，`restart_required=true`、`hot_reload=false`）。
  生效后按群门禁在下一轮消息按最新策略执行（AstrBot 侧策略缓存最多 5 秒）。
- 补丁侧 `refresh_config()` 只在「AstrBot 进程内的配置对象已被更新」（重启加载 / 配置重载 /
  Dashboard 保存）后于下一轮生效；它**不会**让 AstrBot 主动重读 `cmd_config.json` 文件，所以面板的
  `restart_required=true` 不因补丁而改变。详见 `integrations/astrbot-memory/README.md` §3.2 与局限 10。

## 2. 策略结构（与 astrbot/core/memory/policy.py 一致）

```json
{
  "memory": {
    "policy": {
      "default_mode": "conversation",
      "private": {"mode": "conversation", "scope": "user", "extract": true, "inject": true},
      "groups": {
        "aiocqhttp:838796241": {
          "mode": "long_term",
          "scope": "group_user",
          "extract": true,
          "inject": true,
          "max_items": 8,
          "max_chars": 1500
        }
      }
    }
  }
}
```

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `default_mode` | `conversation` / `long_term` | 未列出群的模式，默认 `conversation` |
| `groups.<id>.mode` | `conversation` / `long_term` | 该群是否启用长期记忆（`conversation` 行等价于关闭） |
| `scope` | `group` / `user` / `group_user` | 群=读写本群；user=读写发送者本人（群内显式 opt-in）；group_user=写本群、读本群+本人 |
| `extract` / `inject` | `true` / `false` | 独立开关：只写不读、只读不写都合法 |
| `max_items` | `1..50` | 留空=用 AstrBot 全局值 |
| `max_chars` | `80..8000` | 留空=用 AstrBot 全局值 |
| `private` | 同上的子集 | 私聊只允许 `scope=user` |

群号支持裸群号（`838796241`）或带平台前缀（`aiocqhttp:838796241`）；前缀键优先，与 AstrBot 一致。

### 2.1 非法条目的显示方式（不自作主张改写）

AstrBot 的 `parse_policy` 是**整条降级**：一个字段非法，这条策略整体变 `conversation`（不是只忽略坏字段）。
因此 Studio 读取时：

- **保留**配置里的原值（比如 private 写了 `scope=group`、某行写了 `max_items=999` 就照原样返回），
  不做静默归一化；
- 每条策略（含 `private`）额外返回 `invalid`、`errors[]`、`unknown_fields[]`、`effective_mode`、
  `effective_scope`，其中 `effective_mode` 是 AstrBot **实际**会用的模式（非法时恒为 `conversation`，
  群号重复无法判断时是 `null`=未知）；
- 顶层 `warnings[]` 逐条列出「字段 + 原因 + 降级后果」，`invalid_rows` 给出非法条数，
  `message` 明确写「不是 long_term 生效」；
- 面板把非法行标红、列出原因并显示「实际生效：conversation（只用会话历史）」，私聊条目非法时同样提示，
  重复群号显示「实际生效：未知」——**不会把非法条目显示成生效中的长期记忆**；
- `private` 为空对象/缺省时 AstrBot 会当作"未配置"（私聊跟随 `default_mode`），面板也如实显示跟随关系。

校验覆盖：`mode`/`scope` 枚举、`extract`/`inject` 布尔、`max_items 1..50`、`max_chars 80..8000`、
私聊 `scope` 只能是 `user`、条目必须是对象、未知字段（会警告但 AstrBot 忽略）、以及 strip 后重复的群号。

## 3. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/instances/<id>/memory-policy` | 读取实例策略（另支持 `GET /api/memory-policy?instance=<id>`） |
| POST | `/api/instances/<id>/memory-policy` | 校验并保存策略（另支持 `POST /api/memory-policy`） |

GET 返回：`ok`、`available`、`supported`、`default_mode`、`private`、`groups[]`、`warnings[]`、
`mode_options[]`、`scope_options[]`、`limits`、`memory_enabled`、`patch_hint`、`hot_reload`、
`config_path`、`message`、`warning`、`error`。

POST 请求体：

```json
{
  "default_mode": "conversation",
  "private": {"mode": "conversation", "scope": "user", "extract": true, "inject": true},
  "groups": [
    {"group_id": "838796241", "mode": "long_term", "scope": "group_user",
     "extract": true, "inject": true, "max_items": 8, "max_chars": 1500}
  ]
}
```

状态码：

| 状态 | 场景 |
| --- | --- |
| 200 | 读取成功，或保存成功（`saved: true`，响应即磁盘上的最新值） |
| 400 | 校验失败（枚举/范围/群号/重复行/未知字段）；**不写文件** |
| 403 | 访问令牌缺失或错误（与其它 `/api/*` 一致，未新增 CSRF 例外） |
| 409 | 当前实例没有 AstrBot 配置（`data/astrbot/cmd_config.json` 不存在或不可读写），即"不支持"状态；**不写文件、不伪造值** |
| 501 | 后端没有记忆策略接口（旧后端） |

## 4. 后端校验（写盘前）

- `default_mode` / `mode` 必须属于枚举；`scope` 必须属于 `group|user|group_user`，私聊只允许 `user`。
- `extract` / `inject` 必须是 JSON 布尔（`"yes"`、`1` 一律拒绝）。
- `max_items` 必须是 `1..50` 的整数、`max_chars` 必须是 `80..8000` 的整数（布尔与浮点拒绝）；`null` 表示继承。
- 群号只允许 `4-20` 位数字或 `platform:群号`；含 `/`、`://`、`..` 的值全部拒绝 —— 它只会成为配置对象的键，不可能变成路径或 URL。
- 单实例最多 200 行群策略；重复群号拒绝；行内/顶层出现未知字段拒绝（防止把任意 JSON 塞进 AstrBot 配置）。
- 写入是「读整份 `cmd_config.json` → 只替换 `memory.policy` → 原子写 0600」：其它 AstrBot 设置
  与 `memory.*` 的其它键逐字节保留。若写入失败返回 409 并说明原因，不会留下半份文件。

## 5. 隐私边界

- 只读 AstrBot 的 JSON 配置，**从不打开** `data/astrbot_memory.db`，因此面板与接口都不会展示记忆内容。
- 错误信息与响应只包含字段名/范围，不回显其它配置值（含 API Key）；测试用哨兵字符串校验过。
- 读失败时面板显示"不支持/读取失败"，不缓存旧值当成"已保存"。

## 6. 已知限制（不要当成 E2E 结论）

1. **没有做真实 AstrBot 进程 E2E**：本仓库测试只覆盖 Studio 后端校验、写盘往返、静态/组件契约；
   真实链路需要在测试实例上按 integrations/astrbot-memory/README §3 手工验证一次"记住 → 晚饭推荐"。
2. 面板写的是配置文件；AstrBot 运行中可能重写 `cmd_config.json`（Studio 读取时已容忍空/半份文件并重试），
   并发写入的极端情况未做压测。
3. 是否能被当前镜像读取，取决于该镜像是否应用了 `integrations/astrbot-memory/patches`；未打补丁的
   AstrBot 会忽略 `memory.policy`。面板用 `patch_hint` 标注"无法从配置确认"，不宣称已生效。
4. 面板不写 `memory.enable` 等其它记忆总开关，保持与 AstrBot WebUI/JSON 的既有值一致。
5. 删除群策略行 = 该群回到 `default_mode`；长期记忆里已经写入的历史记录不会被删除（清理由 AstrBot 侧 TTL/forget 负责）。
6. 非法条目只能被**报告**，不能替用户猜意图：面板不会自动把 `scope=group` 改成 `user`、也不会把
   `max_items=999` 裁到 50 —— 必须由用户改正后保存（保存接口本身仍会 400 拒绝这些值）。

## 7. 契约测试

- `tests/test_memory_policy_studio.py`：默认回退、未部署=不支持（GET/409）、保存/读取往返、其它配置
  保留、群增删、私聊策略、脏配置告警、25 类非法输入、200 行上限、空预算省略、无跨实例污染、
  无记忆库内容泄漏、无令牌=403、旧后端=501；以及 t26 的行级告警：private `scope=group` 不被改写、
  `max_items=999`/`max_chars=10` 越界、未知字段、非法 scope/类型、重复群号、生效模式不冒充
  long_term、GET 不改写配置、HTTP 响应带全部 warnings。
- `tests/test_memory_policy_ui_static.py`：面板/字段/标记静态契约、警告文案、不支持态不伪造、
  256 字段保存载荷完整性、"已修改"基线刷新与校验失败时的可见性计划（node）。
