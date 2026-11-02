"""Tests for food.dlsz.top v1.2 site fixes.

1) admin restaurant deletion: DELETE /api/admin/restaurants/<id>
   (auth, 404, cascade counts, tags preserved, other restaurants untouched)
2) location-search dropdown fix in static/js/main.js
   (static assertions only — no browser in CI)

Runs against a build tree containing app.py at FOOD_APP_DIR; the app writes to
its own <build>/instance/foodlisten.db, so production data is never opened.

    FOOD_APP_DIR=/tmp/build/v1.2 ENABLE_SCHEDULER=false \
        python3 -m unittest -v test_site_fixes
"""
import importlib
import os
import re
import sys
import unittest

APP_DIR = os.path.abspath(os.environ.get('FOOD_APP_DIR', '/home/stevehim/food.dlsz.top'))
os.environ['ENABLE_SCHEDULER'] = 'false'
os.environ.setdefault('ADMIN_PASSWORD', 'test-admin')
sys.path.insert(0, APP_DIR)
app_module = importlib.import_module('app')  # noqa: E402  (import after env setup)

food = app_module.app
db = app_module.db
ADMIN_PASSWORD = os.environ['ADMIN_PASSWORD']
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
        for name in ('中国', '吉林省', '长春市', '南关区'):
            loc = app_module.Location(name=name, parent_id=parent)
            db.session.add(loc)
            db.session.flush()
            parent = loc.id
            LOC[name] = loc.id
        db.session.commit()


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare()

    def setUp(self):
        # fresh client per test: the admin session cookie must not leak between tests
        self.client = food.test_client()

    def login(self):
        res = self.client.post('/api/admin/login', json={'password': ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))

    def upload(self, name, username, tags=None):
        res = self.client.post('/api/upload', json={
            'name': name,
            'location_id': LOC['南关区'],
            'taste_score': 4,
            'value_score': 3,
            'comment': '不错',
            'username': username,
            'tags': tags or [],
        })
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return res.get_json()['restaurant_id']

    def make_sample(self, name, reviews=3, tags=('火锅', '便宜')):
        rid = self.upload(name, name + '-u0', tags=list(tags))
        for index in range(1, reviews):
            self.upload(name, '%s-u%d' % (name, index))
        tag_ids = [t.id for t in app_module.Tag.query.filter(app_module.Tag.name.in_(list(tags))).all()]
        return rid, tag_ids


class AdminDeleteRestaurant(ApiTest):
    def test_runs_against_isolated_db(self):
        uri = food.config['SQLALCHEMY_DATABASE_URI']
        self.assertNotIn('/home/stevehim/food.dlsz.top', uri)
        self.assertIn('/instance/', uri)

    def test_requires_admin_session(self):
        rid = self.upload('删除权限店', 'p1')
        res = self.client.delete('/api/admin/restaurants/%d' % rid)
        self.assertEqual(res.status_code, 401)
        self.assertIn('管理员', res.get_json()['error'])
        self.assertIsNotNone(db.session.get(app_module.Restaurant, rid))

    def test_missing_restaurant_404(self):
        self.login()
        res = self.client.delete('/api/admin/restaurants/999999')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.get_json()['error'], '餐厅不存在')

    def test_cascade_delete_counts_and_tags_kept(self):
        rid, tag_ids = self.make_sample('级联删除店', reviews=3)
        touched = self.upload('旁观店', 'bystander')
        tag_names_before = sorted(t.name for t in app_module.Tag.query.filter(app_module.Tag.id.in_(tag_ids)).all())

        self.login()
        res = self.client.delete('/api/admin/restaurants/%d' % rid)
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['restaurant_id'], rid)
        self.assertEqual(body['deleted_reviews'], 3)
        self.assertEqual(body['deleted_restaurant_tags'], 2)

        self.assertIsNone(db.session.get(app_module.Restaurant, rid))
        self.assertEqual(app_module.Review.query.filter_by(restaurant_id=rid).count(), 0)
        self.assertEqual(app_module.RestaurantTag.query.filter_by(restaurant_id=rid).count(), 0)
        still_there = sorted(t.name for t in app_module.Tag.query.filter(app_module.Tag.id.in_(tag_ids)).all())
        self.assertEqual(still_there, tag_names_before, '标签本身必须保留')

        survivor = db.session.get(app_module.Restaurant, touched)
        self.assertIsNotNone(survivor)
        self.assertEqual(app_module.Review.query.filter_by(restaurant_id=touched).count(), 1)

        listed = self.client.get('/api/restaurants', query_string={'include_closed': 1, 'q': '级联删除店'}).get_json()
        self.assertEqual([r for r in listed if r['id'] == rid], [])

    def test_second_delete_is_404(self):
        rid = self.upload('二次删除店', 'd1')
        self.login()
        self.assertEqual(self.client.delete('/api/admin/restaurants/%d' % rid).status_code, 200)
        self.assertEqual(self.client.delete('/api/admin/restaurants/%d' % rid).status_code, 404)

    def test_corrections_are_retained(self):
        rid = self.upload('纠错保留店', 'c1')
        db.session.add(app_module.LocationCorrection(
            restaurant_id=rid, submitter='tester',
            new_location_id=LOC['中国'], status='pending'))
        db.session.commit()
        self.login()
        self.assertEqual(self.client.delete('/api/admin/restaurants/%d' % rid).status_code, 200)
        kept = app_module.LocationCorrection.query.filter_by(submitter='tester').all()
        self.assertEqual(len(kept), 1, '纠错记录属用户提交内容，删除饭店时保留')
        self.assertIsNone(kept[0].restaurant_id, '失去饭店后 FK 由 ORM 置空，不留悬挂引用')

    def test_only_delete_method_is_offered(self):
        rid = self.upload('方法限制店', 'm1')
        self.login()
        self.assertEqual(self.client.get('/api/admin/restaurants/%d' % rid).status_code, 405)


class LocationDropdownFix(unittest.TestCase):
    """Static assertions: no browser available, so pin the fixed handler shape."""

    def read(self, relative):
        with open(os.path.join(APP_DIR, relative), encoding='utf-8') as handle:
            return handle.read()

    def test_select_location_guards_missing_tree_node(self):
        src = self.read('static/js/main.js')
        self.assertIsNone(
            re.search(r"querySelector\('\[data-location-id=\"' \+ id \+ '\"\]'\)\.classList", src),
            '未加保护的 querySelector(...).classList 会让搜索结果选择抛 TypeError')
        self.assertIn("var node = document.querySelector('.tree-node-header[data-location-id=\"' + id + '\"]');", src)
        self.assertIn('if (node) node.classList.add(\'active\');', src)

    def test_select_location_still_navigates(self):
        src = self.read('static/js/main.js')
        start = src.index('function selectLocation(id, name) {')
        body = src[start:start + 900]
        guard_at = body.index('if (node) node.classList.add')
        self.assertLess(guard_at, body.index("document.getElementById('welcomeScreen').style.display = 'none';"))
        self.assertIn("document.getElementById('restaurantView').style.display = 'block';", body)
        self.assertIn('loadRestaurants(id);', body)
        self.assertIn('closeSidebar();', body)

    def test_search_result_click_selects_then_closes_dropdown(self):
        src = self.read('static/js/main.js')
        self.assertIn("sidebarResults.addEventListener('click'", src)
        self.assertIn('selectLocation(parseInt(item.dataset.locId), item.dataset.locPath || item.dataset.locName);', src)
        handler = src[src.index("sidebarResults.addEventListener('click'"):]
        self.assertLess(handler.index('selectLocation(parseInt(item.dataset.locId)'),
                        handler.index("sidebarResults.style.display = 'none';"))

    def test_admin_delete_ui_wired(self):
        src = self.read('static/js/admin.js')
        self.assertIn('window._deleteRestaurant', src)
        self.assertIn("'/api/admin/restaurants/' + id, { method: 'DELETE' }", src)
        button_at = src.index("window._deleteRestaurant(' + r.id")
        self.assertGreater(button_at, 0)
        handler = src[src.index('window._deleteRestaurant = async'):]
        self.assertLess(handler.index('if (!confirm(msg)) return;'), handler.index('method'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
