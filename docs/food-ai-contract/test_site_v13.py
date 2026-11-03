"""Tests for food.dlsz.top v1.3: markdown comments + full-address search + location reuse.

1) Markdown renderer (static/js/markdown.js) — XSS safety, exercised in node
   (no browser needed). Asserts escape-first output, allowed tags only, link
   scheme whitelist, and that main.js/admin.js route comments through it.
2) /api/locations/search — full path / no-separator path / single component /
   out-of-order components; plus restaurant search by location (GET + PUT).
3) POST /api/upload — existing location chain is reused (no 位置已存在), nested
   missing tail is created, duplicate review semantics preserved.
4) The v1.3 patch applies cleanly to the current site source (dry run) and the
   patched app.py compiles.

Runs against a build tree containing app.py at FOOD_APP_DIR; the app writes to
its own <build>/instance/foodlisten.db (override with FOOD_DB_URI), so the
production instance/foodlisten.db is never opened.

    FOOD_APP_DIR=/tmp/build/v1.3 FOOD_DB_URI=sqlite:////tmp/build/v1.3/instance/t.db \
        ENABLE_SCHEDULER=false python3 -m unittest -v test_site_v13
"""
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

APP_DIR = os.path.abspath(os.environ.get('FOOD_APP_DIR', '/home/stevehim/food.dlsz.top'))
PATCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'food-site-v1.3.patch')
os.environ['ENABLE_SCHEDULER'] = 'false'
os.environ.setdefault('ADMIN_PASSWORD', 'test-admin')
sys.path.insert(0, APP_DIR)
app_module = importlib.import_module('app')  # noqa: E402  (import after env setup)

food = app_module.app
db = app_module.db
LOC = {}
_ctx = None


def prepare():
    global _ctx
    if _ctx is None:
        food.config.update(TESTING=True)
        _ctx = food.app_context()
        _ctx.push()
        db.create_all()
        parent = None
        for name in ('中国', '吉林省', '长春市'):
            loc = app_module.Location(name=name, parent_id=parent)
            db.session.add(loc)
            db.session.flush()
            parent = loc.id
            LOC[name] = loc.id
        db.session.commit()


def make_child(name, parent_name):
    parent_id = LOC[parent_name]
    found = app_module.Location.query.filter_by(name=name, parent_id=parent_id).first()
    if found is not None:
        LOC.setdefault(name, found.id)
        return found
    loc = app_module.Location(name=name, parent_id=parent_id)
    db.session.add(loc)
    db.session.commit()
    LOC.setdefault(name, loc.id)
    return loc


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare()

    def setUp(self):
        self.client = food.test_client()

    def upload(self, **payload):
        return self.client.post('/api/upload', json=payload)

    def seed_location(self, path):
        """Create (or reuse) a location chain, returns the last node."""
        loc = None
        parent = None
        for name in path.split('/'):
            found = app_module.Location.query.filter_by(name=name, parent_id=parent).first()
            if not found:
                found = app_module.Location(name=name, parent_id=parent)
                db.session.add(found)
                db.session.flush()
            loc = found
            parent = found.id
        db.session.commit()
        return loc


# ── 1. Markdown / XSS ──────────────────────────────────────────────────────

NODE_HARNESS = r"""
const path = process.argv[2];
global.window = {};
const {renderMarkdown} = require(path);
const assert = require('assert');

function noTag(html, tag) {
  assert.ok(!new RegExp('<' + tag + '[\\s>]', 'i').test(html), 'unexpected <' + tag + '>: ' + html);
}

// raw HTML must stay text
let out = renderMarkdown('<script>alert(1)</script>');
noTag(out, 'script');
assert.ok(out.includes('&lt;script&gt;'));
assert.ok(!out.includes('<script'));

out = renderMarkdown('<img src=x onerror=alert(1)>');
noTag(out, 'img');
assert.ok(!out.includes('<img'), 'raw <img> must be escaped: ' + out);

out = renderMarkdown('"><svg onload=alert(1)>');
noTag(out, 'svg');

out = renderMarkdown('[x](javascript:alert(1))');
assert.ok(!out.includes('<a '), 'javascript: link must not become <a>: ' + out);
out = renderMarkdown('[x](JaVaScRiPt:alert(1))');
assert.ok(!out.includes('<a '), 'mixed-case javascript: must be rejected');
out = renderMarkdown('[x](data:text/html,<b>x</b>)');
assert.ok(!out.includes('<a '), 'data: link must be rejected');
out = renderMarkdown('[x](//evil.example)');
assert.ok(!out.includes('<a '), 'protocol-relative link must be rejected');

// allowed markdown
out = renderMarkdown('**粗体** *斜体* `a<b`');
assert.ok(out.includes('<strong>粗体</strong>'), out);
assert.ok(out.includes('<em>斜体</em>'), out);
assert.ok(out.includes('<code>a&lt;b</code>'), 'inline code escaped: ' + out);

out = renderMarkdown('[站点](https://example.com/a?b=1&c=2)');
assert.ok(out.includes('<a href="https://example.com/a?b=1&amp;c=2"'), out);
assert.ok(out.includes('rel="noopener noreferrer nofollow"'), out);
assert.ok(out.includes('target="_blank"'), out);

out = renderMarkdown('[mail](mailto:a@example.com)');
assert.ok(out.includes('<a href="mailto:a@example.com"'), out);

out = renderMarkdown('第一行\n第二行');
assert.ok(out.includes('第一行<br>第二行'), out);

out = renderMarkdown('```\n<script>alert(1)</script>\n```');
assert.ok(out.includes('<pre><code>'), out);
noTag(out, 'script');

// markdown inside code spans is not interpreted
out = renderMarkdown('`**not bold**`');
assert.ok(!out.includes('<strong>'), out);

// attribute-break payload cannot escape the href attribute
out = renderMarkdown('[x](https://evil.example/"onmouseover="alert(1))');
assert.ok(!out.includes('onmouseover="'), out);
assert.ok(renderMarkdown('**a** <b>b</b>').includes('&lt;b&gt;'));

// empty / null input
assert.strictEqual(renderMarkdown(''), '');
assert.strictEqual(renderMarkdown(null), '');

console.log('node markdown checks OK');
"""


class MarkdownXss(Base):
    def _renderer_path(self):
        return os.path.join(APP_DIR, 'static', 'js', 'markdown.js')

    def test_renderer_is_xss_safe_in_node(self):
        if not shutil.which('node'):
            self.skipTest('node not installed')
        self.assertTrue(os.path.exists(self._renderer_path()), 'markdown.js 未部署')
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as handle:
            handle.write(NODE_HARNESS)
            harness = handle.name
        try:
            proc = subprocess.run(['node', harness, self._renderer_path()],
                                  capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(harness)
        self.assertEqual(proc.returncode, 0, 'stdout=%s\nstderr=%s' % (proc.stdout, proc.stderr))
        self.assertIn('node markdown checks OK', proc.stdout)

    def read(self, relative):
        with open(os.path.join(APP_DIR, relative), encoding='utf-8') as handle:
            return handle.read()

    def test_main_js_renders_comments_through_renderer(self):
        src = self.read('static/js/main.js')
        self.assertIn("renderMarkdown(rev.comment)", src)
        self.assertNotIn("'<p>' + escapeHTML(rev.comment", src,
                         '评价正文不得绕过 markdown 渲染器')

    def test_admin_js_renders_comments_through_renderer(self):
        src = self.read('static/js/admin.js')
        self.assertIn('renderMarkdown(rev.comment)', src)
        self.assertNotIn("escapeHTML(rev.comment || '')", src)

    def test_templates_load_markdown_before_renderers(self):
        for template, renderer in (('templates/index.html', 'main.js'),
                                   ('templates/admin_dashboard.html', 'admin.js')):
            src = self.read(template)
            self.assertIn('js/markdown.js', src, template)
            self.assertLess(src.index('js/markdown.js'), src.index('js/' + renderer),
                            '%s: markdown.js 必须先于 %s 加载' % (template, renderer))

    def test_edit_modal_textarea_is_escaped(self):
        src = self.read('static/js/main.js')
        self.assertIn("'<textarea id=\"editComment\" rows=\"4\">' + escapeHTML(comment)", src)
        self.assertIn('escapeAttrValue(rev.comment', src)


# ── 2. Full-address search ─────────────────────────────────────────────────

class LocationFullPathSearch(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.chaoyang_id = make_child('朝阳区', '长春市').id
        make_child('南关区', '长春市')
        make_child('吉林大学前卫南区', '朝阳区')

    def search(self, q):
        res = self.client.get('/api/locations/search', query_string={'q': q})
        self.assertEqual(res.status_code, 200)
        return res.get_json()

    def names(self, rows):
        return [row['name'] for row in rows]

    def test_full_path_concatenated(self):
        rows = self.search('长春市朝阳区')
        self.assertIn('朝阳区', self.names(rows))
        row = [r for r in rows if r['name'] == '朝阳区'][0]
        self.assertEqual(row['path'], '中国/吉林省/长春市/朝阳区')
        self.assertEqual(row['id'], self.chaoyang_id)

    def test_full_path_with_root(self):
        self.assertIn('朝阳区', self.names(self.search('中国吉林省长春市朝阳区')))
        self.assertIn('朝阳区', self.names(self.search('中国/吉林省/长春市/朝阳区')))

    def test_single_component(self):
        self.assertEqual(self.names(self.search('朝阳区'))[0], '朝阳区')
        self.assertIn('南关区', self.names(self.search('南关区')))

    def test_separated_components_out_of_order(self):
        for q in ('朝阳区 长春市', '长春市 朝阳区', '吉林省/朝阳区'):
            self.assertIn('朝阳区', self.names(self.search(q)), q)

    def test_leaf_only_name(self):
        self.assertEqual(self.names(self.search('吉林大学')), ['吉林大学前卫南区'])

    def test_no_match_and_empty(self):
        self.assertEqual(self.search('不存在的区'), [])
        self.assertEqual(self.search('   '), [])

    def test_exact_path_ranks_first(self):
        rows = self.search('朝阳区')
        self.assertEqual(rows[0]['name'], '朝阳区', '完全匹配/最短路径应排最前')

    def test_wildcard_input_is_literal(self):
        self.assertEqual(self.search('%'), [])

    def test_restaurant_search_by_location_get(self):
        loc = self.seed_location('中国/吉林省/长春市/朝阳区/测试楼')
        up = self.upload(name='位置搜索店', location_id=loc.id, taste_score=4,
                         value_score=3, comment='好吃', username='loc-u1')
        self.assertEqual(up.status_code, 200, up.get_data(as_text=True))
        rid = up.get_json()['restaurant_id']

        rows = self.client.get('/api/restaurants',
                               query_string={'q': '长春市朝阳区', 'search_type': 'location'}).get_json()
        self.assertIn(rid, [r['id'] for r in rows])
        # 子级位置（测试楼）也应命中父级「朝阳区」的搜索
        rows = self.client.get('/api/restaurants',
                               query_string={'q': '朝阳区', 'search_type': 'location'}).get_json()
        self.assertIn(rid, [r['id'] for r in rows])
        self.assertEqual(self.client.get(
            '/api/restaurants', query_string={'q': '火星', 'search_type': 'location'}).get_json(), [])

    def test_restaurant_search_by_location_put(self):
        loc = self.seed_location('中国/吉林省/长春市/南关区/测试胡同')
        up = self.upload(name='位置搜索店二', location_id=loc.id, taste_score=4,
                         value_score=3, comment='不错', username='loc-u2')
        rid = up.get_json()['restaurant_id']
        rows = self.client.put('/api/restaurants',
                               json={'q': '南关区测试胡同', 'search_type': 'location'}).get_json()
        self.assertIn(rid, [r['id'] for r in rows])
        self.assertEqual(self.client.put('/api/restaurants',
                                         json={'q': '火星', 'search_type': 'location'}).get_json(), [])

    def test_ui_offers_location_search_type(self):
        with open(os.path.join(APP_DIR, 'templates', 'index.html'), encoding='utf-8') as handle:
            html = handle.read()
        self.assertRegex(html, r'<option value="location">位置</option>')


# ── 3. Existing location reuse on upload ──────────────────────────────────

class UploadLocationReuse(Base):
    def test_full_chain_exists_is_reused(self):
        chain = '中国/吉林省/长春市/复用区/复用楼'
        first = self.upload(name='复用店', new_location_name=chain, taste_score=4,
                            value_score=3, comment='第一次', username='reuse-u1')
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        first_body = first.get_json()
        self.assertTrue(first_body['location_created'])
        loc_id = first_body['location_id']

        second = self.upload(name='复用店', new_location_name=chain, taste_score=4,
                             value_score=3, comment='第二次', username='reuse-u2')
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        body = second.get_json()
        self.assertEqual(body['location_id'], loc_id, '已存在的位置链必须复用，不能报「位置已存在」')
        self.assertFalse(body['location_created'])
        self.assertFalse(body['restaurant_created'], '同名同位置饭店复用')
        self.assertEqual(body['location_path'], chain)
        self.assertEqual(app_module.Review.query.filter_by(
            restaurant_id=body['restaurant_id']).count(), 2)

    def test_different_restaurant_same_chain_reuses_location(self):
        chain = '中国/吉林省/长春市/ reuse2区/楼A'.replace(' ', '')
        first = self.upload(name='复用店甲', new_location_name=chain, taste_score=4,
                            value_score=3, comment='甲', username='reuse2-u1').get_json()
        second = self.upload(name='复用店乙', new_location_name=chain, taste_score=4,
                             value_score=3, comment='乙', username='reuse2-u2')
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        body = second.get_json()
        self.assertEqual(body['location_id'], first['location_id'])
        self.assertTrue(body['restaurant_created'])
        self.assertFalse(body['location_created'])

    def test_duplicate_username_semantics_preserved(self):
        chain = '中国/吉林省/长春市/复用区/查重楼'
        first = self.upload(name='查重店', new_location_name=chain, taste_score=4,
                            value_score=3, comment='一', username='dup-user')
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        dup = self.upload(name='查重店', new_location_name=chain, taste_score=4,
                          value_score=3, comment='二', username='dup-user')
        self.assertEqual(dup.status_code, 400)
        self.assertEqual(dup.get_json()['code'], 'DUPLICATE_USERNAME')

    def test_nested_missing_tail_is_created(self):
        # 父链已存在（中国/吉林省/长春市），只缺尾段 朝阳区/新楼
        self.seed_location('中国/吉林省/长春市/朝阳区')
        res = self.upload(name='尾段店', new_location_name='中国/吉林省/长春市/朝阳区/新楼',
                          taste_score=4, value_score=3, comment='尾段', username='tail-u1')
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertTrue(body['location_created'])
        self.assertEqual(body['location_path'], '中国/吉林省/长春市/朝阳区/新楼')
        tail = app_module.Location.query.filter_by(name='新楼').first()
        self.assertIsNotNone(tail)
        parent = app_module.Location.query.filter_by(name='朝阳区').first()
        self.assertEqual(tail.parent_id, parent.id)
        # 父链没有被重复创建
        self.assertEqual(app_module.Location.query.filter_by(name='中国').count(), 1)

    def test_partial_existing_tail_under_custom_parent_id(self):
        res = self.upload(name='指定父级店', new_location_name='自定义尾段',
                          new_location_parent_id=LOC['长春市'],
                          taste_score=4, value_score=3, comment='指定父级', username='tail-u2')
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertTrue(body['location_created'])
        self.assertEqual(body['location_path'], '中国/吉林省/长春市/自定义尾段')
        again = self.upload(name='指定父级店二', new_location_name='自定义尾段',
                            new_location_parent_id=LOC['长春市'],
                            taste_score=4, value_score=3, comment='再来', username='tail-u3')
        self.assertEqual(again.status_code, 200, again.get_data(as_text=True))
        self.assertEqual(again.get_json()['location_id'], body['location_id'])
        self.assertFalse(again.get_json()['location_created'])


# ── 3b. Upload page: existing administrative location is selectable ───────

LOCATION_PATH_HARNESS = r"""
const path = process.argv[2];
global.window = {};
const tools = require(path);
const assert = require('assert');

assert.deepStrictEqual(tools.splitPath('中国/吉林省 长春市-朝阳区'),
    ['中国', '吉林省', '长春市', '朝阳区']);
assert.strictEqual(tools.normalizeFlat('中国 吉林省/长春市-朝阳区'), '中国吉林省长春市朝阳区');
assert.strictEqual(tools.normalizePath('中国/吉林省/长春市/朝阳区'), '中国/吉林省/长春市/朝阳区');

const rows = [
  {id: 1, name: '朝阳区', path: '中国/吉林省/长春市/朝阳区'},
  {id: 2, name: '朝阳区', path: '中国/辽宁省/沈阳市/朝阳区'},
  {id: 3, name: '南关区', path: '中国/吉林省/长春市/南关区'}
];
// 完整路径（带/不带分隔符）唯一 → 自动选中
assert.deepStrictEqual(tools.findExactPathMatch(rows, '中国/吉林省/长春市/朝阳区'), rows[0]);
assert.deepStrictEqual(tools.findExactPathMatch(rows, '中国吉林省长春市朝阳区'), rows[0]);
// 省略根、但保留连续尾段 → 也能自动选中
assert.deepStrictEqual(tools.findExactPathMatch(rows, '长春市/南关区'), rows[2]);
// 裸「朝阳区」有歧义 → 不自动选，交给用户点选
assert.strictEqual(tools.findExactPathMatch(rows, '朝阳区'), null);
assert.strictEqual(tools.findPathMatches(rows, '朝阳区').length, 2);
// 前缀存在 + 缺失尾段 → 只暂存尾段
const prefix = tools.splitExistingPrefix(rows, '中国/吉林省/长春市/朝阳区/新楼');
assert.strictEqual(prefix.existing.id, 1);
assert.strictEqual(prefix.tail, '新楼');
// 单级名称没有可拆前缀
assert.strictEqual(tools.splitExistingPrefix(rows, '新楼'), null);
assert.strictEqual(tools.normalizeFlat(''), '');
console.log('node location-path checks OK');
"""


class LocationPathTools(unittest.TestCase):
    def test_path_helpers_in_node(self):
        if not shutil.which('node'):
            self.skipTest('node not installed')
        renderer = os.path.join(APP_DIR, 'static', 'js', 'location-path.js')
        self.assertTrue(os.path.exists(renderer), 'location-path.js 未部署')
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as handle:
            handle.write(LOCATION_PATH_HARNESS)
            harness = handle.name
        try:
            proc = subprocess.run(['node', harness, renderer],
                                  capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(harness)
        self.assertEqual(proc.returncode, 0, 'stdout=%s\nstderr=%s' % (proc.stdout, proc.stderr))
        self.assertIn('node location-path checks OK', proc.stdout)


class UploadLocationUi(unittest.TestCase):
    """上传页（templates/upload.html + static/js/upload.js）静态断言。"""

    def read(self, relative):
        with open(os.path.join(APP_DIR, relative), encoding='utf-8') as handle:
            return handle.read()

    def test_template_loads_path_tools_before_upload_js(self):
        html = self.read('templates/upload.html')
        self.assertIn('js/location-path.js', html)
        self.assertLess(html.index('js/location-path.js'), html.index('js/upload.js'))
        self.assertIn('id="newLocationMatches"', html)
        self.assertIn('id="newLocationHint"', html)

    def test_typing_full_path_searches_locations_search(self):
        src = self.read('static/js/upload.js')
        at = src.index("nameInput.addEventListener('input'")
        body = src[at:at + 2600]
        self.assertIn('/api/locations/search', body, '新建位置输入框必须走全地址搜索')
        self.assertIn('findExactPathMatch', body)
        self.assertIn('selectExistingLocation', body)

    def test_exact_existing_path_selects_location_without_new_location_name(self):
        src = self.read('static/js/upload.js')
        start = src.index('function selectExistingLocation')
        body = src[start:src.index('\n    }\n', start)]
        self.assertIn("document.getElementById('locationSelect').value = loc.id", body,
                      '已存在位置必须写 hidden locationSelect（提交时 → location_id）')
        self.assertIn('pendingNewLocation = null', body,
                      '选中已存在位置必须清空 pendingNewLocation（不发 new_location_name）')
        self.assertNotIn('上传后创建', body, '已存在位置不得显示「上传后创建」')
        self.assertNotIn('new_location_name =', body)

    def test_submit_only_sends_new_location_name_for_pending_location(self):
        src = self.read('static/js/upload.js')
        start = src.index('if (restaurantId) {')
        body = src[start:src.index('var btn = form.querySelector', start)]
        self.assertIn('else if (pendingNewLocation) {', body)
        self.assertIn('data.new_location_name = pendingNewLocation.name;', body)
        self.assertIn("data.location_id = document.getElementById('locationSelect').value || null;", body,
                      '选中已存在位置时必须走 location_id 分支')

    def test_existing_prefix_stages_only_the_missing_tail(self):
        src = self.read('static/js/upload.js')
        self.assertIn('stagePendingLocation(resolved.tail, resolved.existing.id', src)
        self.assertIn('仅缺失尾段上传后创建', src, '缺失尾段提示应说明只创建尾段')

    def test_ambiguous_name_is_not_staged_as_new_location(self):
        src = self.read('static/js/upload.js')
        self.assertIn('resolved.matches', src)
        self.assertIn('已有同名位置，请点击选择', src)
        self.assertIn("fetchJSON('/api/locations?parent_id=' + parentId)", src,
                      '已选父级时先查该父级下是否已有同名子级')
        self.assertIn('selectExistingLocation({ id: kids[k].id', src)


class ExistingLocationSelectionApi(Base):
    def test_selecting_existing_admin_location_uploads_review(self):
        loc = self.seed_location('中国/吉林省/长春市/朝阳区')
        res = self.upload(name='选位置店', location_id=loc.id, taste_score=4, value_score=3,
                          comment='选中已有位置', username='pick-u1')
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertEqual(body['location_id'], loc.id)
        self.assertEqual(body['location_path'], '中国/吉林省/长春市/朝阳区')
        self.assertFalse(body.get('location_created'), '选已有位置不应创建位置')
        self.assertNotIn('LOCATION_EXISTS', res.get_data(as_text=True))
        self.assertEqual(app_module.Review.query.filter_by(
            restaurant_id=body['restaurant_id']).count(), 1)

    def test_existing_path_via_new_location_name_is_not_a_duplicate_error(self):
        chain = '中国/吉林省/长春市/朝阳区/选位置楼'
        first = self.upload(name='选位置店二', new_location_name=chain, taste_score=4,
                            value_score=3, comment='一', username='pick-u2')
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertNotIn('位置已存在', first.get_data(as_text=True))
        second = self.upload(name='选位置店二', new_location_name=chain, taste_score=4,
                             value_score=3, comment='二', username='pick-u3')
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        self.assertNotIn('LOCATION_EXISTS', second.get_data(as_text=True))
        self.assertEqual(second.get_json()['location_id'], first.get_json()['location_id'])


# ── 4. Patch dry run ──────────────────────────────────────────────────────

class PatchApplies(unittest.TestCase):
    def test_patch_dry_run_against_current_source(self):
        src = os.environ.get('FOOD_SRC', '/home/stevehim/food.dlsz.top')
        if not os.path.exists(os.path.join(src, 'app.py')):
            self.skipTest('site source not available: %s' % src)
        self.assertTrue(os.path.exists(PATCH_FILE), PATCH_FILE)
        work = tempfile.mkdtemp(prefix='food-v13-patch-')
        try:
            shutil.copy(os.path.join(src, 'app.py'), os.path.join(work, 'app.py'))
            for folder in ('static', 'templates'):
                shutil.copytree(os.path.join(src, folder), os.path.join(work, folder))
            proc = subprocess.run(['patch', '-p1', '--dry-run', '-i', PATCH_FILE],
                                  cwd=work, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, 'stdout=%s\nstderr=%s' % (proc.stdout, proc.stderr))
            self.assertIn('checking file app.py', proc.stdout)
            self.assertIn('checking file static/js/markdown.js', proc.stdout)
            self.assertIn('checking file static/js/location-path.js', proc.stdout)
            self.assertIn('checking file static/js/upload.js', proc.stdout)
            self.assertIn('checking file templates/upload.html', proc.stdout)
            applied = subprocess.run(['patch', '-p1', '--quiet', '-i', PATCH_FILE],
                                     cwd=work, capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            import ast
            with open(os.path.join(work, 'app.py'), encoding='utf-8') as handle:
                ast.parse(handle.read())
        finally:
            shutil.rmtree(work, ignore_errors=True)


class TestDbIsolation(unittest.TestCase):
    def test_runs_against_isolated_db(self):
        uri = food.config['SQLALCHEMY_DATABASE_URI']
        self.assertNotIn('/home/stevehim/food.dlsz.top', uri)
        self.assertIn('/instance/', uri)


if __name__ == '__main__':
    unittest.main(verbosity=2)
