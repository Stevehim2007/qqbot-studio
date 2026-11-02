# food.dlsz.top v1.2 修复：位置搜索下拉点击 + 后台删除饭店

> ⚠️ **未部署**。本机 food 站点源码是只读挂载，这里只交付补丁 + 测试 + 文档。
> 补丁：`food-site-v1.2-fixes.patch`（针对干净源码：`app.py` / `static/js/main.js` / `static/js/admin.js`）
> 与 t1 的 `food-ai-review-v1.1.patch` **互不重叠**（那个改 `upload_review()`，这个改路由表/前端），可任意顺序应用。

## 1. Bug A：左侧位置筛选搜索「日新」，结果下拉点不动、跳不过去

**复现**：主页左侧「位置筛选」输入 `日新` → 下拉出现结果 → 点击条目无任何反应，位置树/内容区不切换。

**根因**（`static/js/main.js` `selectLocation()`，原第 85-93 行）：

```js
document.querySelectorAll('.tree-node-header').forEach(...);
document.querySelector('[data-location-id="' + id + '"]').classList.add('active');   // ← 抛错点
document.getElementById('welcomeScreen').style.display = 'none';
...
loadRestaurants(id);
closeSidebar();
```

- `/api/locations` 只返回**顶层**节点（省/国），子级是点开树时懒加载的；
  `/api/locations/search` 却返回**任意深度**的位置（例：「日新」在 中国/吉林省/长春市/南关区 之下）。
- 从搜索结果选中一个还没在树里渲染过的位置时，`querySelector` 返回 `null`，
  下一步 `.classList` 直接 `TypeError: Cannot read properties of null (reading 'classList')`。
- 该异常发生在同一个 click 监听器里，**后面的视图切换、`loadRestaurants()`、`closeSidebar()` 全被跳过**，
  下拉也不会关闭 → 表现就是"点了没反应、也跳不过去"。
- 树里已经展开过的节点反而能点（因为它渲染出来了），所以故障只在"搜索到未渲染位置"时出现。

**修复**（3 行，`static/js/main.js` `selectLocation()`）：

```js
var node = document.querySelector('.tree-node-header[data-location-id="' + id + '"]');
if (node) node.classList.add('active');          // 拿不到节点就只跳过高亮，后续流程照常
```

行为保持：节点在树里 → 照常高亮；不在 → 只少一个高亮，仍会切视图、加载该位置饭店、关闭下拉、清空搜索框；
移动端 `closeSidebar()` 也恢复执行（抽屉正常收起）。

**根因验证**（无浏览器，用 node 直接验证该表达式）：

```
$ node -e "var document={querySelector:()=>null,querySelectorAll:()=>[]};
           document.querySelector('[data-location-id=\"7\"]').classList.add('active')"
TypeError: Cannot read properties of null (reading 'classList')
```

## 2. Feature B：后台管理员直接删除饭店

### 后端（`app.py`，插在 `admin_delete_review` 之后、PUT 编辑路由之前）

```python
@app.route('/api/admin/restaurants/<int:restaurant_id>', methods=['DELETE'])
@admin_required
def admin_delete_restaurant(restaurant_id):
    """删除餐厅：评价与标签关联一并清理（标签本身保留），纠错记录保留（FK 置空）。单个事务。"""
    restaurant = db.session.get(Restaurant, restaurant_id)
    if not restaurant:
        return jsonify({'error': '餐厅不存在'}), 404
    try:
        deleted_reviews = Review.query.filter_by(restaurant_id=restaurant_id).delete(synchronize_session=False)
        deleted_restaurant_tags = RestaurantTag.query.filter_by(restaurant_id=restaurant_id).delete(synchronize_session=False)
        db.session.delete(restaurant)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({'error': '删除失败，已回滚'}), 500
    return jsonify({'success': True, 'restaurant_id': restaurant_id,
                    'deleted_reviews': deleted_reviews, 'deleted_restaurant_tags': deleted_restaurant_tags})
```

- 鉴权：`@admin_required` → 未登录 `401 {"error": "需要管理员登录"}`，与站点其他 admin 接口一致（同一 session cookie）。
- 404：餐厅不存在。
- 级联：`reviews`、`restaurant_tags` 一起删；`tags` 表**不动**（标签本身保留）；其他餐厅及其评价不受影响。
- 纠错记录（`corrections`）：**保留**（属用户提交内容），删除饭店后其 `restaurant_id` 由 ORM 置空，不留悬挂引用
  （后台纠错页对"饭店已不存在"本就 tolerant：`restaurant_name` 显示空）。
- 事务：三条删除 + 一次 commit，异常整体 rollback 并返回 500。

### 前端（`static/js/admin.js`）

- 饭店卡片操作区新增 `<button class="btn-danger" ...>删除饭店</button>`（复用已有 `.btn-danger` 样式，**无需改 CSS**）。
- `window._deleteRestaurant(id, name, reviewCount)`：`confirm()` 弹出含"将同时删除 N 条评价和标签关联"的确认，
  成功后 `loadAllData()` 刷新列表。
  > 用 `confirm()` 而不是新做一套模态框：站点现有的「删除评价」「删除位置」都用 `confirm()`，保持一致、改动最小。

## 3. 应用补丁

```bash
cd /www/wwwroot/food.dlsz.top
cp app.py app.py.bak
cp static/js/main.js static/js/main.js.bak
cp static/js/admin.js static/js/admin.js.bak

patch -p1 < food-site-v1.2-fixes.patch        # 或：git apply，或手工按本文改动
python3 -c "import ast;ast.parse(open('app.py',encoding='utf-8').read())"   # 语法自检
# 可选：浏览器硬刷新后确认 static/js/main.js、admin.js 已更新（缓存）

# 重启（Flask 路由变了，必须重启）
supervisorctl restart <项目名>      # 或 宝塔面板 Python 项目 → 重启
# 若手工起：gunicorn -w 2 -b 127.0.0.1:5000 --preload app:app
```

回滚：`mv app.py.bak app.py; mv static/js/main.js.bak static/js/main.js; mv static/js/admin.js.bak static/js/admin.js` + 重启。

只读冒烟（不需要登录）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE https://food.dlsz.top/api/admin/restaurants/1   # 期望 401
curl -s "https://food.dlsz.top/api/locations/search?q=日新"                                          # 期望含 path 的 JSON
```

## 4. 手工验收清单

**A. 位置搜索下拉**
1. 桌面：主页左侧搜索 `日新` → 下拉显示若干条（含 `path`）。
2. 点第一条 → 下拉关闭、搜索框清空、右侧内容区切到该位置并显示饭店列表/空态、标题为位置名。✅
3. 换一个深层位置（如 `南关区`、`三食堂`）重复第 2 步：即使树里没渲染过也必须能跳转。✅
4. 树里先展开到同一位置再搜它、点击：高亮仍然生效（回归）。✅
5. 移动端（窄窗口）：汉堡菜单打开 → 搜索 → 点击结果 → 内容切换且侧边抽屉自动收起。✅
6. 搜索无结果时显示"未找到匹配位置"，不影响树。✅

**B. 后台删除饭店**
1. `/admin` 登录 → 找到目标饭店卡片 → 操作区有「删除饭店」。
2. 点删除 → 确认框含店名 + "N 条评价和标签关联"+ 不可撤销提示；**取消**则列表无变化。✅
3. 确认后：列表刷新、该饭店消失；其标签在其他饭店/标签列表里仍在。✅
4. 直接调用 `DELETE /api/admin/restaurants/<id>`（带 session）→ 200 且返回 `deleted_reviews` 计数；再删一次 → 404。✅
5. 未登录调用 → 401；删除不存在的 id → 404。✅
6. 被删饭店的纠错记录仍在后台纠错页（饭店名显示为空）。✅

## 5. 测试与证据

`test_site_fixes.py`（11 个用例，Flask test-client；JS 部分为静态断言 + node 复现，环境无浏览器）：

- 后端：未登录 401 / 不存在 404 / 级联计数（3 条评价、2 条标签关联）/ `tags` 保留 / 其他饭店不受影响 /
  重复删除 404 / GET 405 / 纠错记录保留且 FK 置空 / 确认跑在隔离 DB（URI 不含生产路径）
- 前端静态断言：`selectLocation` 不再有未保护的 `querySelector(...).classList`、含 `if (node)` 守卫、
  守卫在视图切换之前、`loadRestaurants` + `closeSidebar` 仍在、搜索结果 click 先 `selectLocation` 后隐藏下拉、
  admin.js 有 `_deleteRestaurant` 且用 `DELETE`、确认框在请求之前

结果：
- 打补丁后（`v1.2` 构建）：**11 passed**
- 未打补丁的干净源码：**7 failures + 1 error**（证明用例确实盯住了这两个缺陷）

复跑：`cd docs/food-ai-contract && ./run-tests.sh`（第 3 阶段即本补丁；v1/v1.1 为 t1 契约测试）

## 6. 已知限制（未在本次修改）

- 站点全站没有 CSRF token（admin 接口仅靠 session cookie 鉴权），本次保持一致、未引入新机制。
- JS 行为用静态断言 + node 表达式复现覆盖，没有 headless 浏览器 E2E。
- 删除**位置**（`DELETE /api/admin/locations/<id>`）仍会留下无主的饭店/评价/标签关联（既有行为，未在本次范围）。
- 删除饭店不会清理 `feedback`（与本表无关）。
