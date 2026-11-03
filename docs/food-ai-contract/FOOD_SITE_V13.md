# food.dlsz.top v1.3：Markdown 评价 / 全地址搜索 / 已存在位置复用

> ⚠️ **未部署**。本机 food 站点源码（`/home/stevehim/food.dlsz.top`）是**只读挂载**，
> 本次只交付补丁 + 成品文件 + 测试 + 说明；改完源码后需要由有写权限的人部署并重启，
> 否则线上行为不变。**不要**把本文当成"已上线"。

- 补丁：`food-site-v1.3.patch`（对**当前**源码：已含 v1.1 稳定错误码 + v1.2 后台删饭店/下拉修复）
- 成品：`deploy/v1.3/`（含 `MANIFEST.sha256`，同名 `.tar.gz` 为打包版）
- 测试：`test_site_v13.py`（32 个用例，Flask test-client + node 渲染器/路径工具断言）
- 一键跑：`./run-tests.sh`（v1 / v1.1 / v1.2 / v1.3 全量回归）

改动文件（9 个）：`app.py`、`static/js/markdown.js`（新增）、`static/js/location-path.js`（新增）、
`static/js/main.js`、`static/js/admin.js`、`static/js/upload.js`、`templates/index.html`、
`templates/admin_dashboard.html`、`templates/upload.html`。
**无数据库 schema 迁移**，不动生产 `instance/foodlisten.db`。

## 1. 评价支持 Markdown（XSS 安全）

**渲染方式**：新增零依赖渲染器 `static/js/markdown.js`（`renderMarkdown(comment)`），
前台卡片、评价编辑弹窗、后台评价列表统一走它。数据库仍存**原文**（接口契约不变）——
渲染发生在浏览器端，所有输出都经过转义。

支持子集：`**粗体**`、`*斜体*`、`` `行内代码` ``、```` ```代码块``` ````、`[文本](链接)`、换行（`\n` → `<br>`）。

**安全模型（escape-first）**：

1. **先整段 HTML 转义**（`& < > " '`），再插入白名单标签 `strong/em/code/pre/a/br`。
   因此 `<script>alert(1)</script>`、`<img src=x onerror=...>` 只会显示成文本。
2. **链接协议白名单**：只有 `http://`、`https://`、`mailto:` 会生成 `<a>`；
   `javascript:`、`data:`、`vbscript:`、`//host`（协议相对）不渲染为链接，退化为原文。
   生成的 `<a>` 固定带 `target="_blank" rel="noopener noreferrer nofollow"`。
3. 代码块/行内代码先抽成占位符、最后回填，代码里的 `**`、标签不会被二次解释。
4. 顺带修掉旧代码里 `data-comment="..."` 只用 `escapeHTML`（不转引号）导致的
   属性截断隐患；编辑弹窗的 `<textarea>` 内容也改为转义后注入。

**未做（范围外）**：服务端 Markdown 渲染/存储审计、PDF 报告里的 Markdown 渲染、
图片/表格/标题等完整 CommonMark 语法、评论长度限制。需要时再加。

## 2. 全地址搜索（`/api/locations/search`）

**实现**：递归 CTE 现算 `full_path`，不落库、不加列、不加索引：

```sql
WITH RECURSIVE loc_path(id, name, parent_id, path, depth) AS (
    SELECT id, name, parent_id, name, 0 FROM locations WHERE parent_id IS NULL
    UNION ALL
    SELECT l.id, l.name, l.parent_id, p.path || '/' || l.name, p.depth + 1
    FROM locations l JOIN loc_path p ON l.parent_id = p.id
    WHERE p.depth < 64
)
SELECT id, name, path FROM loc_path
WHERE (path LIKE :t ESCAPE '\' OR REPLACE(path, '/', '') LIKE :t ESCAPE '\') ...
ORDER BY (REPLACE(path, '/', '') = :exact) DESC, LENGTH(path), id
LIMIT :limit
```

命中规则（`q` 按 `/ \ - — – > → ／ ＼ 空白` 拆成最多 6 段，每段都必须命中路径）：

| 输入 | 命中 |
|---|---|
| `朝阳区` | 任意层级名包含「朝阳区」 |
| `长春市朝阳区` | 去 `/` 后的全路径包含该串 |
| `中国吉林省长春市朝阳区` | 同上（无分隔符整串） |
| `中国/吉林省/长春市/朝阳区` | 分段，逐段命中 |
| `朝阳区 长春市` | 分段乱序也命中（AND 语义） |

排序：路径完全相等最前，其次路径短（更具体），再按 id；`LIMIT 20`。
返回项保持 `{id, name, path}`，与前端下拉字段兼容，可直接点选。

LIKE 的 `%`/`_`/`\` 做了转义（`ESCAPE '\'`），搜索 `%` 不会变成全表匹配。

**为什么不用物化 `full_path` 列**：位置表是几百行量级，递归 CTE 单次查询是毫秒级；
物化列需要 schema 迁移 + 父级改名时级联更新（触发器或应用层）+ 加索引，收益与维护成本
不成正比。若将来位置量级上万或搜索 QPS 很高，再迁移：`ALTER TABLE locations ADD COLUMN
full_path ...` + 写路径触发器 + `CREATE INDEX ... ON locations(full_path)`，并把本函数
改为直接查列；届时再补迁移脚本与回填。

**按位置搜饭店**：`GET /api/restaurants` 与 `PUT /api/restaurants` 新增
`search_type=location`——先按上述规则找位置，再展开其所有后代位置，
返回这些位置下的饭店（`is_closed=0` 过滤保持不变）。主页搜索框新增「位置」选项，
选中后可直接搜「长春市朝阳区」这类全地址。

## 3. `POST /api/upload`：已存在位置链复用

旧行为：`new_location_name` 的末级位置已存在 → `400 LOCATION_EXISTS`「位置「X」已存在」。
新行为：

1. 先逐级查找/创建位置链：**已存在的层级原样复用**，只有缺失的尾段才创建；
2. 再按 `(店名, location_id)` 查饭店——同店复用、新店创建；
3. 评价照常写入；`DUPLICATE_USERNAME`（同店同用户名）语义**不变**。

响应新增 `location_created`（布尔，`true` = 本次创建了末级位置，`false` = 复用已有位置），
原有 `location_id` / `location_path` / `restaurant_created` / `review_id` 不变。
`/api/locations/create` 的「已存在」400 语义**未改**（不在本次范围）。

注意：同时传 `location_id` 与 `new_location_name` 时，位置链优先（与"新位置"语义一致）；
只传 `location_id` 的行为完全不变。

## 3b. 上传页：已存在的行政区划位置可直接选择（t32）

t29 只修了后端复用，上传表单仍会把已存在路径当"新位置"暂存并显示「上传后创建」。
本次把上传页的位置选择改成「优先选已有行政区划」：

- **输入即搜索**：`新建位置` 行里输入 `长春市/朝阳区`、`中国吉林省长春市朝阳区`
  这类全路径时，走 `/api/locations/search`（t29 的全地址搜索）；命中**唯一**已存在位置
  就直接选中：写 hidden `locationSelect`（提交时 → `location_id`）、触发器显示完整 path、
  清空 `pendingNewLocation`、**不发送** `new_location_name`、**不显示**「上传后创建」。
- **有歧义不猜**：裸名称如「朝阳区」可能对应多个同名位置，此时列出候选让用户点选
  （点选同样走"选中已存在位置"），不会静默当成新位置。
- **只有前缀存在**：输入 `中国/吉林省/长春市/朝阳区/新楼` 且 朝阳区 已存在时，
  自动从最长前缀逐级回退找到已存在的 朝阳区，只暂存缺失尾段 `新楼` 并挂在它下面
  （`new_location_name=新楼` + `new_location_parent_id=朝阳区`），提示「仅缺失尾段上传后创建」。
- **已选父级 + 单级名称**：先查该父级下是否已有同名子级，有 → 直接选用；
  没有 → 在父级下暂存该子级（旧行为）。
- **真的没有匹配**：才按新位置暂存（沿用旧流程，仍显示「上传后创建」）。

新增 `static/js/location-path.js`（纯函数：路径拆分/归一化、唯一匹配、最长已存在前缀 + 缺失尾段），
由 `upload.html` 在 `upload.js` 之前加载；node 单测直接 require 它，测的是真实逻辑。
`upload.js` 在缺少该文件（旧缓存/分步部署）时会退化为旧的"直接暂存"行为，不会 500/报错。

后端 `POST /api/upload` 在选中已有位置时只收到 `location_id`，走 t29 已实现的复用分支，
不会再出现 `LOCATION_EXISTS`；重复评价语义（同店同用户名 400 `DUPLICATE_USERNAME`）不变。

## 4. 测试

`test_site_v13.py`（32 个用例，全部通过；无浏览器，Markdown/路径工具用 node 跑真实代码）：

- **Markdown/XSS**：raw HTML 转义、`javascript:`/`data:`/`//` 链接拒绝、
  http(s)/mailto 链接、粗体/斜体/行内代码/代码块/换行、属性逃逸、模板加载顺序、
  main.js/admin.js 是否都走 `renderMarkdown`。
- **全路径搜索**：单级、连续全路径、带根全路径、分段乱序、叶子名、精确优先、
  无匹配/空查询、`%` 按字面量处理；`search_type=location`（GET/PUT，含子树展开）；
  UI 有「位置」选项。
- **位置复用**：整链已存在（复用且 `location_created=false`、评价累加）、
  同链不同店、同店同用户名仍 400 `DUPLICATE_USERNAME`、嵌套缺失尾段、
  指定 `new_location_parent_id` 的尾段复用。
- **上传页（t32）**：`upload.html` 先加载 `location-path.js`、有候选/提示容器；
  输入走 `/api/locations/search`；`selectExistingLocation` 写 hidden `locationSelect`、
  清空 `pendingNewLocation`、函数体内无「上传后创建」；提交只在 `pendingNewLocation`
  分支发 `new_location_name`，选中已存在位置走 `location_id`；缺失尾段只暂存尾段；
  同名歧义不静默新建；已选父级先查同名子级。
- **路径工具**（node）：`长春市/朝阳区`、`中国吉林省长春市朝阳区` 唯一命中；
  裸 `朝阳区` 歧义不自动选；`中国/吉林省/长春市/朝阳区/新楼` 拆出已有前缀 + 尾段 `新楼`。
- **接口层**：直接 `location_id` 选已有位置上传 → 200 且 `location_created=false`、
  无 `LOCATION_EXISTS`；同一 `new_location_name` 链二次提交不再报「位置已存在」。
- **补丁本身**：对当前源码 `patch -p1 --dry-run` 通过（含 upload.js/upload.html/location-path.js），
  实际应用后 `ast.parse` 通过。
- **数据隔离**：断言测试库路径不是生产 `instance/foodlisten.db`。

跑法：

```bash
python3 -m venv /tmp/foodvenv && . /tmp/foodvenv/bin/activate
pip install flask==2.0.3 werkzeug==2.0.3 flask-sqlalchemy==2.5.1 \
            sqlalchemy==1.4.54 apscheduler==3.9.1 "setuptools<81"
./run-tests.sh
```

期望：`v1` 13 passed / 3 skipped、`v1.1` 16 passed、`v1.2` 11 passed、`v1.3` 32 passed。
（v1.3 的 node 用例在没装 node 的机器上自动 skip，其余仍须通过。）

## 5. 部署（需写权限，本次**未执行**）

```bash
cd /www/wwwroot/food.dlsz.top
cp app.py app.py.bak && mkdir -p .deploy-backup/v1.3 && cp -a static/js/main.js static/js/admin.js templates/index.html templates/admin_dashboard.html .deploy-backup/v1.3/
patch -p1 < /path/to/food-site-v1.3.patch          # 或直接用 deploy/v1.3/ 覆盖同名文件
python3 -c "import ast;ast.parse(open('app.py',encoding='utf-8').read())"
# 重启 gunicorn（宝塔 Python 项目管理器 → 重启；或 supervisorctl restart <项目名>）
```

只覆盖静态文件不重启也能生效（浏览器缓存需刷新），但 `app.py` 的两项接口改动必须重启。
静态资源（`markdown.js`、`location-path.js`、模板、`upload.js`）与 `app.py` 可以分开部署，互不阻塞：
只部署 `app.py` → 搜索/复用生效、评价仍是纯文本、上传页仍是旧交互；
只部署前端 → 渲染/上传页选择生效、搜索仍是旧逻辑。上传页必须有 `location-path.js`
（缺了会退化为旧"直接暂存"行为，不会报错）。

**冒烟（只读）**：

```bash
curl -s "https://food.dlsz.top/api/locations/search?q=长春市朝阳区" | head -c 300
curl -s "https://food.dlsz.top/api/restaurants?q=长春市朝阳区&search_type=location" | head -c 300
# 评价 Markdown：前台任意一条含 **粗体** / `代码` 的评价应渲染为粗体/代码（而不是显示星号）
```

**回滚**：`mv app.py.bak app.py`，静态文件从 `.deploy-backup/v1.3/` 拷回，重启。

**验收清单**

- [ ] `/api/locations/search?q=中国吉林省长春市朝阳区` 返回带 `/` 全路径的位置
- [ ] 搜索 `朝阳区 长春市`（乱序分段）也能命中
- [ ] 下拉点选搜索结果能切到该位置并列出饭店（v1.2 的下拉修复仍有效）
- [ ] 搜索框「位置」类型能按全地址筛出饭店
- [ ] 提交含 `**粗体**`、`` `代码` ``、`[链接](https://...)` 的评价后前台正确渲染
- [ ] 提交 `<script>alert(1)</script>` 的评价只显示文本、不弹窗
- [ ] 上传页「+ 新建位置」输入 `中国/吉林省/长春市/朝阳区`（已存在）→ 触发器直接显示该完整路径，
      没有「上传后创建」字样；提交后该评价挂在**已有** location 下（不新增位置）
- [ ] 输入 `中国/吉林省/长春市/朝阳区/新楼`（尾段不存在）→ 提示只创建缺失尾段，
      提交后父级仍是已有「朝阳区」
- [ ] 同一 `new_location_name` 链提交第二个评价返回 200 且位置 id 不变（不再 400）
- [ ] 同店同用户名重复提交仍返回 400 `DUPLICATE_USERNAME`
- [ ] 缺失尾段（父链已存在）只创建尾段，父链不重复

## 6. 已知边界

- 递归 CTE 对 parent 环没有兜底（`depth < 64` 截断，不会死循环，但环上的位置路径会不完整）；
  现有数据与后台建位置流程不会产生环。
- 搜索仅按名称/路径包含匹配，不做拼音、别名、模糊纠错。
- Markdown 只支持上文列出的子集：图片、表格、引用、标题不渲染（显示原文）。
- 评论仍无长度上限（与改动前一致）。
- 上传页的"已存在位置"判断依赖 `/api/locations/search`：同名的不同行政区（如两个「朝阳区」）
  不会自动选，必须点选或写全路径；这是刻意的保守行为。
