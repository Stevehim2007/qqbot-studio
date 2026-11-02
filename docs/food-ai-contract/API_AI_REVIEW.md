# food.dlsz.top — AI 提交餐厅 / 评价 接口契约（t1 交付物）

状态：由生产源码 `app.py`（2026-09-10 版，本机只读副本）+ 线上 GET 探针推导，
并用 Flask test-client 在**隔离 SQLite** 上验证（未触碰生产库）。
源码在本机为只读挂载 → §6 的 v1.1 补丁**未部署**，需人工应用。

## 1. 端点

```
POST https://food.dlsz.top/api/upload      Content-Type: application/json; charset=utf-8
```

结论：**现有公开接口已足够**，不需要新增 `/api/ai/review` 之类的专用端点。
- 无鉴权、无 CSRF、无频率限制（站点现状）→ 闸门必须在调用方（QQ 插件）：固定 host、路径白名单、单用户限频、字段白名单。
- 不得使用 admin 会话/密码调用；不得把 body 拼进 shell 命令。
- 该接口同时服务网页表单（`static/js/upload.js`），补丁只做加法，网页行为不变。

## 2. 请求字段

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `name` | string(≤200) | 必填 | 餐厅名，不能为空；与 `location_id` 组成餐厅唯一键 |
| `restaurant_id` | int | 可选 | 已存在餐厅 ID（追加评价）。传了它则**忽略** `name` 匹配与 `location_id`，位置固定为餐厅原位置；ID 不存在 → 404 `RESTAURANT_NOT_FOUND`，绝不静默新建 |
| `location_id` | int | 新餐厅必填* | 取自 `GET /api/locations/search` |
| `new_location_name` | string(≤200) | 新餐厅必填* | 新建位置；可多级，分隔符 `/ \ - — – > → ／ ＼`；放在已存在的父下且末级同名 → 400 `LOCATION_EXISTS`（同一事务回滚，**不会**留下餐厅记录） |
| `new_location_parent_id` | int | 可选 | `new_location_name` 的父位置 ID |
| `taste_score` | number | 必填 | **0.1–5.0 含端点**；int / float / 数字字符串均可 |
| `value_score` | number | 必填 | 同上 |
| `comment` | string | 必填 | 评价正文，去空后不能为空 |
| `username` | string(≤100) | 可选 | 缺省存成 `用户N`；**同一餐厅内唯一**，重复 → 400 `DUPLICATE_USERNAME` |
| `password` | string | 可选 | 设置后可用 `PUT /api/reviews/<id>` 改评价；服务端 pbkdf2:sha256 存储，任何响应/日志**不回显**。AI 流程默认 = 发送者 QQ 号（插件侧注入，不进 prompt/URL/日志） |
| `review_date` | `"YYYY-MM-DD"` | 可选 | 非法值**静默回退**为服务器当前时间（不报错） |
| `tags` | array[string] | 可选 | 标签自动创建并绑定到该餐厅；**必须是字符串数组**（v1 下数组含非字符串 → 500） |

*`location_id` 与 `new_location_name` 至少给一个，否则 400 `MISSING_LOCATION`。

`username` 唯一性对 bot 的实际影响：若 username = 群昵称，同一个人第二次提交同一家店会得到
`DUPLICATE_USERNAME`。想支持改评：用 `password`（=QQ）走 `PUT /api/reviews/<id>`，或换 username（如 `昵称#QQ尾号`）。

## 3. 成功响应

```json
v1（线上现状）  200 {"success": true, "restaurant_id": 35, "tags": ["火锅", "便宜"]}
v1.1（补丁）   200 {"success": true, "restaurant_id": 35, "restaurant_name": "回转小火锅",
                    "restaurant_created": false, "review_id": 412, "location_id": 9,
                    "location_path": "中国/吉林省/长春市/南关区", "tags": ["火锅"]}
```

`success: true` 只在评价已 commit 后返回 → 客户端可以据此宣称"已提交"。
v1 里判断"新店还是老店"不可靠（没有 `restaurant_created`）；需要时先做 §5 第 2 步的查重。

## 4. 错误语义

v1 只有中文 `error` 文本（HTTP 状态 + 文本匹配）：

| HTTP | `error` 文本 | 触发 |
|---|---|---|
| 400 | `店名不能为空` | name 缺失 |
| 400 | `位置不能为空` | location_id / new_location_name 都缺 |
| 400 | `味道分不能为空` / `性价比分不能为空` | 分数缺失 |
| 400 | `味道分格式不正确` / `性价比分格式不正确` | 分数非数字 |
| 400 | `味道分必须在0.5-5之间` / `性价比分必须在0.5-5之间` | 越界（**文案错**，实际接受 0.1–5） |
| 400 | `评价不能为空` | comment 缺失 |
| 400 | `用户名「X」在该店铺已存在` | username 重复（=已提交过） |
| 400 | `位置「X」已存在` | new_location_name 末级已存在 → 改用 `location_id` |
| 404 | `餐厅不存在` | restaurant_id 无效 |
| 500 | （HTML/异常） | `tags` 含非字符串等 |

v1.1 补丁给每个错误加稳定 `code`（同时保留中文 `error` 供展示）：
`MISSING_NAME` `MISSING_LOCATION` `MISSING_TASTE_SCORE` `INVALID_TASTE_SCORE`
`MISSING_VALUE_SCORE` `INVALID_VALUE_SCORE` `MISSING_COMMENT` `DUPLICATE_USERNAME`
`LOCATION_EXISTS` `RESTAURANT_NOT_FOUND`。bot 侧建议 `code` 优先、缺失时回退文本匹配。

餐厅查重语义：**同 (name, location_id) 复用，不报错**（精确匹配、不做模糊去重），
新评价挂到老餐厅。想挂到别的区的同名店，必须先按 §5 第 2 步查到 `restaurant_id` 再提交，
否则会在当前 location 下新建一条同名餐厅。

## 5. AI 提交流程（bot 侧，含缺字段追问）

1. 位置：`GET /api/locations/search?q=<地名词>` → `[{id, name, path}]`（最多 20 条）；多条时让用户选，别猜。
2. 查重：`GET /api/restaurants?q=<店名>&location_id=<位置id>` → **含子级**列表，带 `reviews`、`taste_avg/value_avg/total_avg`。
   命中同名 → 下一步用它的 `restaurant_id`；未命中 → 用 `location_id` 新建。
3. 缺字段就问，不要提交：`name` `location?` `taste_score` `value_score` `comment` 任一缺失 → 回一句缺什么；
   `password` 由插件注入 = QQ 号；`username` = 群昵称；`review_date` 默认今天。
4. 提交：`POST /api/upload`。
5. 结果映射：200 → 成功（可说餐厅名 + 已提交）；`DUPLICATE_USERNAME` → "你已给这家店提交过评价"；
   `LOCATION_EXISTS` → 重查位置用 `location_id`；`RESTAURANT_NOT_FOUND` → 回到第 2 步重查。
6. 重试：网络超时后重试是安全的（用户名唯一键挡住重复评价），但重试会被识别为 `DUPLICATE_USERNAME` → 按"已提交"处理。

## 6. v1.1 补丁（可选，加性）与部署

补丁：`food-ai-review-v1.1.patch`（对 `app.py`，约 40 行改动）

- 成功响应加 `review_id` / `restaurant_name` / `restaurant_created` / `location_id` / `location_path`
- 错误加稳定 `code`；分数文案改成真实范围 0.1-5
- `tags` 容错（非数组→空、元素 `str()`），不再 500
- 新增 `FOOD_DB_URI` 环境变量（默认仍指向 `instance/foodlisten.db`），用于隔离测试
- 纯响应层变化：无 DB 迁移，不改变既有网页行为（`upload.js` 只读 `error` + HTTP 状态）

部署（需在服务器上做，本机站点源码为只读挂载）：

```bash
cd /www/wwwroot/food.dlsz.top && cp app.py app.py.bak     # 备份
# 上传 app.py + 补丁后：
patch -p1 < food-ai-review-v1.1.patch
python3 -c "import ast;ast.parse(open('app.py',encoding='utf-8').read())"   # 语法自检
# 重启 gunicorn（宝塔 Python 项目管理器 → 重启；或）
supervisorctl restart food.dlsz.top
# 验证：
curl -s "https://food.dlsz.top/api/locations/search?q=南关区"      # 只读探针，仍正常
```

未部署则按 §3/§4 的 **v1** 行实现，并在代码里预留 `body.get("code")` 回退。

## 7. 验证记录

- `test_ai_review_api.py`（16 个用例，Flask test-client + 临时 SQLite，`ENABLE_SCHEDULER=false`）
- 未改源码（线上现状）：13 passed / 3 skipped（enhanced-only）
- 打过 v1.1 补丁：16 passed（`FOOD_EXPECT_ENHANCED=1`）
- 生产 `instance/foodlisten.db` mtime 未变（测试全程用临时库）；线上只做 GET 探针
- 复跑：`. /path/to/venv/bin/activate && ./run-tests.sh`（脚本自己从只读源重建 v1/v1.1）

## 8. 已知限制（未在本次修改）

- 写接口无鉴权/无限流：公开站点可被刷 → 建议后续加 token 或 IP 限流
- 餐厅唯一键是 `(name, location_id)` 精确匹配，不做模糊去重（"XX店" 与 "XX店（万达）" 会成两条）
- `review_date` 非法值静默回退，客户端要自己校验格式
- 引用已关闭餐厅（`is_closed`）不在提交校验范围内
