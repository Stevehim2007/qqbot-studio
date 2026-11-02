# food-ai-contract — food.dlsz.top AI 评价接口契约（t1 交付物，**未部署**）

QQ Bot Studio 侧接入 food.dlsz.top 时用的接口契约、可选补丁与契约测试。

## 线上现状（不变）

AI 提交餐厅/评价直接调用**现有公开接口**，无需改站点：

```
POST https://food.dlsz.top/api/upload     Content-Type: application/json
```

线上当前行为（v1）已在 `API_AI_REVIEW.md` §2–§4 逐字段固定，并由测试覆盖。
**补丁未应用、生产未部署**：站点源码在本机是只读挂载，`app.py` 没有任何改动。

## 文件

| 文件 | 说明 |
|---|---|
| `API_AI_REVIEW.md` | 正式契约：端点、字段、成功/错误语义、AI 提交流程、部署步骤、已知限制 |
| `food-ai-review-v1.1.patch` | **可选**加性补丁：错误加稳定 `code`、响应加 `review_id`/`restaurant_created`/`location_path`、`tags` 容错、`FOOD_DB_URI` 环境变量 |
| `test_ai_review_api.py` | 16 个契约测试（Flask test-client + 临时 SQLite） |
| `run-tests.sh` | 从只读源临时重建 v1 / v1.1 / v1.2 三个变体并各跑一遍 |
| `FOOD_SITE_FIXES.md` | v1.2 站点修复说明：位置搜索下拉点击 bug 根因 + 后台删除饭店（含验收清单） |
| `food-site-v1.2-fixes.patch` | v1.2 补丁：`app.py` 新增 `DELETE /api/admin/restaurants/<id>`、`static/js/main.js` 下拉修复、`static/js/admin.js` 删除按钮 |
| `test_site_fixes.py` | v1.2 的 11 个用例（admin 删除鉴权/404/级联 + 下拉修复静态断言） |

不随仓库放 `app.py` 副本：站点源码随时可能变，`run-tests.sh` 现场从 `FOOD_SRC`
（默认 `/home/stevehim/food.dlsz.top`）取源码 + 打补丁，避免仓库里躺着一份过期生产代码。

## 为什么补丁是可选的

- 不加补丁：bot 按中文 `error` 文本匹配（契约 §4 已固定文本），可用但脆。
- 加补丁：每个错误带 `code`（`DUPLICATE_USERNAME` / `LOCATION_EXISTS` / `MISSING_*` / `INVALID_*` / `RESTAURANT_NOT_FOUND`），
  成功响应带 `review_id`、`restaurant_created`、`location_path`；`tags` 非字符串不再 500。
- 纯加法：网页表单只读 `error` 文本 + HTTP 状态，行为不变；无 DB 迁移。
- bot 侧建议：`code` 优先，取不到再回退文本匹配（这样补丁前后都能跑）。

## 部署（需有服务器写权限，本次**未执行**）

```bash
cd /www/wwwroot/food.dlsz.top
cp app.py app.py.bak                                # 1. 备份
patch -p1 < /path/to/food-ai-review-v1.1.patch      # 2. 打补丁（或直接上传新 app.py）
python3 -c "import ast;ast.parse(open('app.py',encoding='utf-8').read())"   # 3. 语法自检
# 4. 重启 gunicorn：宝塔 Python 项目管理器 → 重启；或 supervisorctl restart <项目名>
#    （多 worker 时 gunicorn -w 2 -b 127.0.0.1:5000 --preload app:app）
# 5. 只读冒烟：
curl -s "https://food.dlsz.top/api/locations/search?q=南关区"
```

回滚：`mv app.py.bak app.py` + 重启。

## 跑测试

```bash
python3 -m venv /tmp/foodvenv && . /tmp/foodvenv/bin/activate
pip install flask==2.0.3 werkzeug==2.0.3 flask-sqlalchemy==2.5.1 \
            sqlalchemy==1.4.54 apscheduler==3.9.1 "setuptools<81"
./run-tests.sh
```

期望：`v1` 13 passed / 3 skipped；`v1.1` 16 passed；`v1.2` 11 passed（站点修复）。
测试全程用临时 SQLite（`FOOD_DB_URI` / 临时目录），不会打开或写入生产 `instance/foodlisten.db`。
