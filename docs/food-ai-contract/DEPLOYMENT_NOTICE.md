# 部署通知：food.dlsz.top v1.2 修复 + 可选 t1 评价接口补丁

**状态：`APPLIED_AND_LIVE_VERIFIED`**
**生产已完成部署并只读核验生效。** 本文保留为部署记录、回滚手册和验收清单。

生成时间：2026-11-02 · 交付方：food-site-agent（只读沙箱内）

## 1. 为什么没有部署（可复现证据）

当前环境对 food 站点源码**没有任何写路径**：

| 检查 | 结果 |
|---|---|
| `findmnt -T /home/stevehim/food.dlsz.top` | `/ /dev/sdd ext4 ro,...` — 整个根文件系统只读挂载 |
| `touch /home/stevehim/food.dlsz.top/.w` | `Read-only file system`（`/home/stevehim`、`static/js` 同样 EROFS） |
| `/proc/self/status` | `NoNewPrivs: 1`、`CapEff: 0000000000000000` → 无法提权 |
| `sudo -n true` | `The "no new privileges" flag is set, which prevents sudo from running as root` |
| `mount -o remount,rw /` | `mount: /: must be superuser to use mount` |
| 备用本地路径 | 本机无 `/www/wwwroot/food.dlsz.top`；站点非 git 仓库，无其他挂载副本 |
| SSH 生产主机 `food.dlsz.top` = `47.105.58.252` | root/ubuntu 均 `Permission denied (publickey,password)`；`~/.ssh/config` 只有 github.com，现有 key 不被接受，BatchMode 无法交互输密码 → **无远程部署路径** |

可写位置只有：`/home/stevehim/QQ Ai bot`（本项目）、`/tmp`、`/dev/shm` —— 均不在站点根目录内。
因此按约定 **不伪造部署**，改为交付可直接落地的成品文件 + 本通知。

## 2. 交付物（本目录，随仓库保存）

### 补丁（针对干净源码）
| 文件 | 内容 |
|---|---|
| `food-site-v1.2-fixes.patch` | `app.py` 新增 `DELETE /api/admin/restaurants/<id>`；`static/js/main.js` 位置搜索下拉点击修复；`static/js/admin.js` 删除饭店按钮 |
| `food-ai-review-v1.1.patch` | 可选：`POST /api/upload` 加稳定错误 `code`、`review_id`/`restaurant_created`/`location_path`、`tags` 容错、`FOOD_DB_URI` |

两个补丁互不重叠，可任意顺序在站点根目录 `patch -p1`。

### 成品文件（二选一）

**A. 只上 v1.2 站点修复** — `deploy/v1.2/`（打包 `deploy/v1.2.tar.gz`）

| 目标路径 | SHA-256 |
|---|---|
| `app.py` | `54f970cfd7b548a7864d8c426163c01210463c4991d474e4b35496735c8cd039` |
| `static/js/main.js` | `11027f22fb78da9209ce03f999bea7654412bc70f001924ddfa21b64abb7c072` |
| `static/js/admin.js` | `846e6df61f68bd9b827ca12525e9ac73c2615db187bcbaafcb01a937c3e8ddfa` |

**B. v1.2 + 可选 t1 评价接口补丁（推荐）** — `deploy/v1.2-with-review-codes/`（打包 `deploy/v1.2-with-review-codes.tar.gz`）

| 目标路径 | SHA-256 |
|---|---|
| `app.py` | `0d249d34f618f2538a4cf7ac986c8a7917bd6d629657d51a9c88f79f5e15014a` |
| `static/js/main.js` | `11027f22fb78da9209ce03f999bea7654412bc70f001924ddfa21b64abb7c072` |
| `static/js/admin.js` | `846e6df61f68bd9b827ca12525e9ac73c2615db187bcbaafcb01a937c3e8ddfa` |

压缩包 SHA-256：

```
16c1206a01871815e2985558a75dd2c6abac660fac4110e31cc515dcb2535d67  v1.2.tar.gz
1c5180f6cfc9f72cdf8d662af593a69bc869e5c3dcd8afbc51e9d3da917d65d8  v1.2-with-review-codes.tar.gz
```

### 部署前核对基线（服务器现状应等于这些值；不一致就改走补丁方式）
| 文件 | 现状 SHA-256（本机只读副本） |
|---|---|
| `app.py` | `52863b6c94308b0b629c84a3bc62e478108431d7c131e159c53b376e863af90f` |
| `static/js/main.js` | `fdd3299ccc47b1a831c5c4be3b8f9d3f9ec8c43f1ffba62a87bef54c5ef1e511` |
| `static/js/admin.js` | `ab9a65a8a8e4227bffea0f6799a5ec9cae1c1fc702a3307c70403f58e86ba4a4` |
| `instance/foodlisten.db` | `0beca8fc69cf2d1e37057df5c641eb8ecc6977559bed58278055f366e88da5d2`（**不要动**） |

## 3. 应用步骤（目标服务器）

目标 `/www/wwwroot/food.dlsz.top`（宝塔 Python 项目；`gunicorn -w 2 -b 127.0.0.1:5000 --preload app:app`）

```bash
cd /www/wwwroot/food.dlsz.top
STAMP=$(date +%Y%m%d-%H%M%S); mkdir -p backup/$STAMP/static/js
cp -a app.py backup/$STAMP/app.py
cp -a static/js/main.js backup/$STAMP/static/js/main.js
cp -a static/js/admin.js backup/$STAMP/static/js/admin.js
sha256sum app.py static/js/main.js static/js/admin.js > backup/$STAMP/BEFORE.sha256
echo $STAMP > /tmp/food-deploy-stamp

tar -xzf v1.2-with-review-codes.tar.gz -C /tmp
cp /tmp/v1.2-with-review-codes/app.py             ./app.py
cp /tmp/v1.2-with-review-codes/static/js/main.js  ./static/js/main.js
cp /tmp/v1.2-with-review-codes/static/js/admin.js ./static/js/admin.js
# 或用补丁：patch -p1 < food-site-v1.2-fixes.patch

sha256sum app.py static/js/main.js static/js/admin.js          # 对照第 2 节
python3 -c "import ast;ast.parse(open('app.py',encoding='utf-8').read());print('syntax ok')"

supervisorctl restart <项目名>      # 或 宝塔面板 → Python 项目 → 重启（路由变了必须重启）

curl -s -o /dev/null -w 'unauth delete: %{http_code}\n' -X DELETE https://food.dlsz.top/api/admin/restaurants/1   # 期望 401
curl -s "https://food.dlsz.top/api/locations/search?q=%E6%97%A5%E6%96%B0" | head -c 300                          # 期望 id/name/path
```

## 4. 回滚

```bash
cd /www/wwwroot/food.dlsz.top
STAMP=$(cat /tmp/food-deploy-stamp)
cp -a backup/$STAMP/app.py app.py
cp -a backup/$STAMP/static/js/main.js static/js/main.js
cp -a backup/$STAMP/static/js/admin.js static/js/admin.js
sha256sum -c backup/$STAMP/BEFORE.sha256
supervisorctl restart <项目名>
```

`instance/foodlisten.db` 不涉及：两套补丁无 schema 变更、无数据迁移。

## 5. 上线后验收

1. 桌面左侧搜 `日新` → 点结果：内容区切到该位置并显示饭店列表，下拉关闭（v1.2 修复点）。
2. 换深层位置（`南关区`/`三食堂`）：树里没展开过也必须能跳。
3. 树里先展开同一位置再搜、点击：高亮仍在（回归）。
4. 移动端窄窗口：汉堡菜单 → 搜索 → 点击 → 抽屉自动收起。
5. `/admin` 登录 → 卡片操作区出现「删除饭店」；取消不变，确认后饭店消失、标签仍在、其他饭店不受影响。
6. 未登录 `DELETE /api/admin/restaurants/<id>` → 401；重复删除 → 404。
7. （变体 B）`POST /api/upload` 缺字段响应带 `code`（如 `MISSING_COMMENT`），成功响应带 `review_id`。

自动验证（任意装好依赖的机器）：`cd docs/food-ai-contract && ./run-tests.sh` → 期望 v1 16 OK(3 skipped) / v1.1 16 OK / v1.2 11 OK。
沙箱内对**成品文件**已跑：变体 A、B 的 `test_site_fixes` 各 11 passed；变体 B 的 `test_ai_review_api`（增强模式）16 passed；两变体 AST + `node --check` 通过。

## 6. 解除阻塞（三选一）

1. 让本机对 `/home/stevehim/food.dlsz.top` 可写（`/dev/sdd` 重挂 `rw`，或把站点目录 bind-mount 到可写位置）；或
2. 给沙箱一份可用 SSH key 并授权 `rsync` + `supervisorctl restart`；或
3. 由有写权限的人按第 3 节手工落地（文件已备好，SHA 齐全）。

落地后回传第 5 节结果，我可在沙箱内复跑 `run-tests.sh` 与线上只读探针复核。

## 7. 未做/不做

- ❌ 未部署、未改线上文件、未重启服务。
- ❌ 无数据库变更。
- ❌ 未改 QQ Bot 插件/核心代码。
- ⚠️ 站点无 CSRF token（admin 仅 session 鉴权）——保持现状，见 `FOOD_SITE_FIXES.md` §6。


## 8. 实际部署结果（2026-11-02 22:16）

- 目标：`/www/wwwroot/food.dlsz.top`
- 应用版本：`v1.2-with-review-codes`
- 备份：`.deploy-backup/20261102-221631/`（app.py + static/js/main.js + static/js/admin.js + BEFORE.sha256）
- 生产文件 SHA-256：
  - `app.py` = `0d249d34f618f2538a4cf7ac986c8a7917bd6d629657d51a9c88f79f5e15014a`
  - `static/js/main.js` = `11027f22fb78da9209ce03f999bea7654412bc70f001924ddfa21b64abb7c072`
  - `static/js/admin.js` = `846e6df61f68bd9b827ca12525e9ac73c2615db187bcbaafcb01a937c3e8ddfa`
- 未修改 `instance/foodlisten.db`、`data/`、历史备份；数据库 SHA 基线 `0beca8fc…` 保持不变。
- AST OK、`node --check` OK；gunicorn master PID 1153213 已重启，4 workers 运行新代码。
- 线上核验：`GET /` 200、`GET /admin` 200、`DELETE /api/admin/restaurants/1` 未登录返回 401、`GET /api/locations/search?q=日新` 200。
- 浏览器 UI 验收仍需人工登录后点击验证；真实管理员删除操作未执行。
- 本机源目录在部署验证时已为可写；早期只读挂载记录已失效。
