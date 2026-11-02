"""Contract tests for AI review submission: POST /api/upload on food.dlsz.top.

Runs against a COPY of app.py (built by run-tests.sh in a temp dir) with a
throwaway sqlite DB — never touches instance/foodlisten.db on the live host.

    FOOD_APP_DIR=<dir containing app.py> ENABLE_SCHEDULER=false \
        python3 -m unittest -v test_ai_review_api

Baseline class = contract as served by current production code.
Enhanced class = optional additive patch (stable `code`, review_id, ...);
only runs with FOOD_EXPECT_ENHANCED=1.
"""
import atexit
import importlib
import os
import shutil
import sys
import tempfile
import unittest

APP_DIR = os.path.abspath(os.environ.get('FOOD_APP_DIR', '/home/stevehim/food.dlsz.top'))
TMP = tempfile.mkdtemp(prefix='food-ai-review-')
os.environ['FOOD_DB_URI'] = 'sqlite:///' + os.path.join(TMP, 'test.db')
os.environ['ENABLE_SCHEDULER'] = 'false'
os.environ['ADMIN_PASSWORD'] = 'test-admin'
sys.path.insert(0, APP_DIR)
app_module = importlib.import_module('app')  # noqa: E402  (import after env setup)

food = app_module.app
db = app_module.db
LOC = {}


def seed_locations():
    parent = None
    for name in ('中国', '吉林省', '长春市', '南关区'):
        loc = app_module.Location(name=name, parent_id=parent)
        db.session.add(loc)
        db.session.flush()
        parent = loc.id
        LOC[name] = loc.id
    other = app_module.Location(name='朝阳区', parent_id=LOC['长春市'])
    db.session.add(other)
    db.session.flush()
    LOC['朝阳区'] = other.id
    db.session.commit()


_ctx = None


def prepare():
    """One app context + one seeded schema, shared by both test classes."""
    global _ctx
    if _ctx is None:
        food.config.update(TESTING=True)
        _ctx = food.app_context()
        _ctx.push()
        db.create_all()
        seed_locations()
        atexit.register(shutil.rmtree, TMP, True)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare()
        cls.client = food.test_client()

    def post(self, **overrides):
        body = {
            'name': '测试饭店A',
            'location_id': LOC['南关区'],
            'taste_score': 4.5,
            'value_score': 3,
            'comment': '味道不错',
            'username': '10001',
            'password': '10001',
            'review_date': '2026-10-01',
            'tags': ['火锅', '便宜'],
        }
        body.update(overrides)
        return self.client.post('/api/upload', json=body)

    def restaurants(self, **params):
        return self.client.get('/api/restaurants', query_string=params).get_json()


class LiveContractV1(ApiTest):
    """What production serves today (no source change required)."""

    def test_locations_search_shape(self):
        res = self.client.get('/api/locations/search', query_string={'q': '南关区'})
        self.assertEqual(res.status_code, 200)
        item = res.get_json()[0]
        self.assertEqual(item['name'], '南关区')
        self.assertEqual(item['path'], '中国/吉林省/长春市/南关区')
        self.assertIsInstance(item['id'], int)

    def test_new_restaurant_created_with_review(self):
        res = self.post()
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertIsInstance(data['restaurant_id'], int)
        self.assertEqual(sorted(data['tags']), ['便宜', '火锅'])
        found = [r for r in self.restaurants(q='测试饭店A', location_id=LOC['南关区']) if r['name'] == '测试饭店A']
        self.assertEqual(len(found), 1)
        review = found[0]['reviews'][0]
        self.assertEqual(review['username'], '10001')
        self.assertEqual(review['taste_score'], 4.5)
        self.assertEqual(review['date'], '2026-10-01')

    def test_same_name_and_location_reuses_restaurant(self):
        first = self.post(name='测试饭店B', username='20001').get_json()
        second = self.post(name='测试饭店B', username='20002').get_json()
        self.assertEqual(first['restaurant_id'], second['restaurant_id'])
        rows = [r for r in self.restaurants(q='测试饭店B', location_id=LOC['南关区']) if r['name'] == '测试饭店B']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['review_count'], 2)

    def test_duplicate_username_rejected(self):
        self.post(name='测试饭店C', username='30001')
        res = self.post(name='测试饭店C', username='30001', comment='重复提交')
        self.assertEqual(res.status_code, 400)
        self.assertIn('已存在', res.get_json()['error'])

    def test_scores_accepted_range_and_rejects(self):
        for name, taste in (('范围A', 0.1), ('范围B', 5)):
            res = self.post(name=name, username='t' + name, taste_score=taste)
            self.assertEqual(res.status_code, 200, (taste, res.get_data(as_text=True)))
        for name, taste, value in (
            ('范围C', 0.09, 3), ('范围D', 5.01, 3), ('范围E', 'abc', 3),
            ('范围F', 3, 0.09), ('范围G', 3, 5.01), ('范围H', 3, 'abc'),
        ):
            res = self.post(name=name, username='t' + name, taste_score=taste, value_score=value)
            self.assertEqual(res.status_code, 400, (name, res.get_data(as_text=True)))

    def test_missing_required_fields(self):
        cases = [
            ({'name': ''}, '店名不能为空'),
            ({'location_id': None}, '位置不能为空'),
            ({'taste_score': None}, '味道分不能为空'),
            ({'value_score': None}, '性价比分不能为空'),
            ({'comment': ''}, '评价不能为空'),
        ]
        for index, (override, expected) in enumerate(cases):
            res = self.post(username='miss%d' % index, **override)
            self.assertEqual(res.status_code, 400, override)
            self.assertIn(expected, res.get_json()['error'])

    def test_new_location_created_under_parent(self):
        res = self.post(name='测试饭店D', username='40001',
                        location_id=None, new_location_name='三食堂',
                        new_location_parent_id=LOC['南关区'])
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        loc = self.client.get('/api/locations/search', query_string={'q': '三食堂'}).get_json()[0]
        self.assertEqual(loc['path'], '中国/吉林省/长春市/南关区/三食堂')

    def test_existing_location_name_rejected_without_junk_rows(self):
        self.post(name='测试饭店E', username='50001', location_id=None,
                  new_location_name='四食堂', new_location_parent_id=LOC['南关区'])
        res = self.post(name='测试饭店F', username='50002', location_id=None,
                        new_location_name='四食堂', new_location_parent_id=LOC['南关区'])
        self.assertEqual(res.status_code, 400)
        self.assertIn('已存在', res.get_json()['error'])
        self.assertEqual([r for r in self.restaurants(q='测试饭店F', include_closed=1)], [])

    def test_restaurant_id_wins_over_location_id(self):
        rid = self.post(name='测试饭店G', username='60001').get_json()['restaurant_id']
        res = self.post(name='忽略', restaurant_id=rid, location_id=LOC['朝阳区'], username='60002')
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(res.get_json()['restaurant_id'], rid)
        rows = [r for r in self.restaurants(q='测试饭店G', location_id=LOC['南关区']) if r['id'] == rid]
        self.assertEqual(rows[0]['review_count'], 2)

    def test_unknown_restaurant_id_404(self):
        res = self.post(restaurant_id=999999)
        self.assertEqual(res.status_code, 404)

    def test_password_hashed_and_editable_never_echoed(self):
        res = self.post(name='测试饭店H', username='70001', password='secret-70001')
        body = res.get_data(as_text=True)
        self.assertNotIn('secret-70001', body)
        restaurant = app_module.Restaurant.query.filter_by(name='测试饭店H').first()
        review = restaurant.reviews.all()[-1]
        self.assertTrue(review.password_hash.startswith('pbkdf2:sha256'))
        edit = self.client.put('/api/reviews/%d' % review.id,
                               json={'password': 'secret-70001', 'comment': '改过的评价'})
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.get_json(), {'success': True})
        wrong = self.client.put('/api/reviews/%d' % review.id,
                                json={'password': 'nope', 'comment': 'x'})
        self.assertEqual(wrong.status_code, 403)

    def test_tags_applied_to_existing_restaurant(self):
        rid = self.post(name='测试饭店I', username='80001', tags=[]).get_json()['restaurant_id']
        self.post(name='测试饭店I', restaurant_id=rid, username='80002', tags=['奶茶'])
        row = [r for r in self.restaurants(q='测试饭店I', location_id=LOC['南关区']) if r['id'] == rid][0]
        self.assertIn('奶茶', row['tags'])

    def test_average_math(self):
        rid = self.post(name='测试饭店J', username='90001',
                        taste_score=4, value_score=3, tags=[]).get_json()['restaurant_id']
        self.post(name='测试饭店J', restaurant_id=rid, username='90002',
                  taste_score=2, value_score=5, tags=[])
        row = [r for r in self.restaurants(q='测试饭店J', location_id=LOC['南关区']) if r['id'] == rid][0]
        self.assertEqual(row['taste_avg'], 3.0)
        self.assertEqual(row['value_avg'], 4.0)
        self.assertEqual(row['total_avg'], 7.0)


@unittest.skipUnless(os.environ.get('FOOD_EXPECT_ENHANCED') == '1',
                     'enhanced contract not deployed (set FOOD_EXPECT_ENHANCED=1)')
class EnhancedV11(ApiTest):
    """Additive fields proposed in food-ai-review-v1.1.patch."""

    def test_success_payload_has_ids(self):
        data = self.post(name='增强饭店A', username='e1').get_json()
        self.assertIsInstance(data['review_id'], int)
        self.assertTrue(data['restaurant_created'])
        self.assertEqual(data['location_path'], '中国/吉林省/长春市/南关区')
        self.assertEqual(data['restaurant_name'], '增强饭店A')
        again = self.post(name='增强饭店A', restaurant_id=data['restaurant_id'], username='e2').get_json()
        self.assertFalse(again['restaurant_created'])

    def test_bad_tags_do_not_500(self):
        res = self.post(name='增强饭店C', username='e11', tags=[1, '  '])
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(res.get_json()['tags'], ['1'])
        nonlist = self.post(name='增强饭店D', username='e12', tags='火锅')
        self.assertEqual(nonlist.status_code, 200)
        self.assertEqual(nonlist.get_json()['tags'], [])

    def test_machine_codes(self):
        self.post(name='增强饭店B', username='e3')
        cases = [
            ({'username': 'e3', 'name': '增强饭店B'}, 'DUPLICATE_USERNAME'),
            ({'username': 'e4', 'taste_score': None}, 'MISSING_TASTE_SCORE'),
            ({'username': 'e5', 'taste_score': 'x'}, 'INVALID_TASTE_SCORE'),
            ({'username': 'e6', 'value_score': 9}, 'INVALID_VALUE_SCORE'),
            ({'username': 'e7', 'comment': ''}, 'MISSING_COMMENT'),
            ({'username': 'e8', 'location_id': None}, 'MISSING_LOCATION'),
            ({'username': 'e9', 'name': ''}, 'MISSING_NAME'),
            ({'username': 'e10', 'location_id': None, 'new_location_name': '南关区',
              'new_location_parent_id': LOC['长春市']}, 'LOCATION_EXISTS'),
        ]
        for override, code in cases:
            res = self.post(**override)
            self.assertEqual(res.status_code, 400, override)
            self.assertEqual(res.get_json().get('code'), code, (override, res.get_json()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
