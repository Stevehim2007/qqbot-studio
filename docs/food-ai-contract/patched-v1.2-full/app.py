import os
import re
import shutil
import calendar
import functools
import atexit
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file, abort, session, redirect, url_for

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import func, text, select

from apscheduler.schedulers.background import BackgroundScheduler

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('FOOD_DB_URI') or (
    'sqlite:///' + os.path.join(basedir, 'instance', 'foodlisten.db')
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.environ.get('SECRET_KEY', 'foodlisten-secret-key-change-in-production')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
db = SQLAlchemy(app)


# ── Models ──────────────────────────────────────────────────────────────────

class Location(db.Model):
    __tablename__ = 'locations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    code = db.Column(db.String(32), nullable=True)

    children = db.relationship(
        'Location',
        backref=db.backref('parent', remote_side=[id]),
        lazy='select',
        order_by='Location.sort_order'
    )


class Restaurant(db.Model):
    __tablename__ = 'restaurants'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    is_closed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    location = db.relationship('Location', backref='restaurants')
    reviews = db.relationship(
        'Review',
        backref='restaurant',
        lazy='dynamic',
        order_by='Review.created_at.desc()'
    )


class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurants.id'))
    username = db.Column(db.String(100), nullable=False)
    taste_score = db.Column(db.Float, nullable=True)
    value_score = db.Column(db.Float, nullable=True)
    comment = db.Column(db.Text)
    password_hash = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Tag(db.Model):
    __tablename__ = 'tags'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)


class RestaurantTag(db.Model):
    __tablename__ = 'restaurant_tags'
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurants.id'))
    tag_id = db.Column(db.Integer, db.ForeignKey('tags.id'))
    restaurant = db.relationship('Restaurant', backref='tag_associations')
    tag = db.relationship('Tag')


class Feedback(db.Model):
    __tablename__ = 'feedbacks'
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    marked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LocationCorrection(db.Model):
    __tablename__ = 'corrections'
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurants.id'))
    submitter = db.Column(db.String(100), nullable=False)
    old_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    new_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'))
    new_location_name = db.Column(db.String(200), nullable=True)
    new_location_parent_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    restaurant = db.relationship('Restaurant', backref='corrections')
    old_location = db.relationship('Location', foreign_keys=[old_location_id])
    new_location = db.relationship('Location', foreign_keys=[new_location_id])


# ── 旧备份兼容：自动补建缺失的表/列 ─────────────────────────────────────────

_schema_compat_checked = False


def ensure_schema_compat(full=False):
    """兼容老版本备份：自动补建缺失的表/列（如 locations.code）。

    启动时执行一次完整检查；每个请求做轻量检查（PRAGMA），
    保证恢复旧备份后无需重启也能自动迁移。
    """
    global _schema_compat_checked
    try:
        with app.app_context():
            if full or not _schema_compat_checked:
                db.create_all()
                _schema_compat_checked = True
            cols = [r[1] for r in db.session.execute(text('PRAGMA table_info(locations)')).fetchall()]
            if not cols:
                db.create_all()
                _schema_compat_checked = True
            elif 'code' not in cols:
                db.session.execute(text('ALTER TABLE locations ADD COLUMN code VARCHAR(32)'))
                db.session.commit()
                print('[Compat] locations 表已补充 code 列（兼容旧备份）')
    except Exception as e:
        print('[Compat] 兼容检查失败:', e)


@app.before_request
def compat_before_request():
    ensure_schema_compat()


ensure_schema_compat(full=True)


# ── Helpers ─────────────────────────────────────────────────────────────────

def get_restaurant_stats(restaurant):
    reviews = restaurant.reviews.all()
    if not reviews:
        return None

    taste_scores = [r.taste_score for r in reviews if r.taste_score is not None]
    value_scores = [r.value_score for r in reviews if r.value_score is not None]

    result = {
        'taste_avg': round(sum(taste_scores) / len(taste_scores), 2) if taste_scores else None,
        'value_avg': round(sum(value_scores) / len(value_scores), 2) if value_scores else None,
    }

    if result['taste_avg'] is not None and result['value_avg'] is not None:
        result['total_avg'] = round(result['taste_avg'] + result['value_avg'], 2)
    else:
        result['total_avg'] = None

    user_totals = []
    for r in reviews:
        if r.taste_score is not None and r.value_score is not None:
            user_totals.append(round(r.taste_score + r.value_score, 2))

    controversy_detail = []
    taste_delta = round(max(taste_scores) - min(taste_scores), 2) if len(taste_scores) >= 2 else 0
    value_delta = round(max(value_scores) - min(value_scores), 2) if len(value_scores) >= 2 else 0
    total_delta = round(max(user_totals) - min(user_totals), 2) if len(user_totals) >= 2 else 0

    if taste_delta >= 2: controversy_detail.append(f'味道分差距{taste_delta}（最高{max(taste_scores)} - 最低{min(taste_scores)}）')
    if value_delta >= 2: controversy_detail.append(f'性价比分差距{value_delta}（最高{max(value_scores)} - 最低{min(value_scores)}）')
    if total_delta >= 2: controversy_detail.append(f'总分差距{total_delta}（最高{max(user_totals)} - 最低{min(user_totals)}）')

    result['is_controversial'] = len(controversy_detail) > 0
    result['controversy_detail'] = controversy_detail

    latest = reviews[0]
    now = datetime.utcnow()
    days_ago = (now - latest.created_at).days
    expired_threshold = months_ago(now, 3)
    result['latest_review_date'] = latest.created_at.strftime('%Y-%m-%d')
    result['is_expired'] = all(r.created_at < expired_threshold for r in reviews)
    result['expiry_detail'] = f'上次评价: {result["latest_review_date"]}, 已过{days_ago}天'
    result['review_count'] = len(reviews)

    return result


def get_restaurant_tags(restaurant_id):
    assocs = RestaurantTag.query.filter_by(restaurant_id=restaurant_id).all()
    tag_ids = [a.tag_id for a in assocs]
    if not tag_ids:
        return []
    tags = Tag.query.filter(Tag.id.in_(tag_ids)).all()
    return [t.name for t in tags]


def get_all_sub_location_ids(location_id):
    rows = db.session.execute(text("""
        WITH RECURSIVE sub(id) AS (
            SELECT id FROM locations WHERE id = :root
            UNION ALL
            SELECT l.id FROM locations l JOIN sub s ON l.parent_id = s.id
        )
        SELECT id FROM sub
    """), {'root': location_id}).fetchall()
    return [r[0] for r in rows]


def location_subtree_ids_query(location_id):
    """返回某位置及所有后代的 ID 子查询（避免 SQLite IN 参数数量上限）"""
    cte = select(Location.id).where(Location.id == location_id).cte(name='sub', recursive=True)
    cte = cte.union_all(select(Location.id).where(Location.parent_id == cte.c.id))
    return select(cte.c.id)


def location_full_path(loc):
    parts = [loc.name]
    p = loc.parent
    while p:
        parts.append(p.name)
        p = p.parent
    return '/'.join(reversed(parts))


def months_ago(dt, months):
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


LOCATION_SEP_PATTERN = re.compile(r'[\/\\\-—–>→／＼]+')


def split_location_names(raw):
    """把「长春大学-xx校区-xx楼」这类输入拆成多级名称列表，支持 / - > → 等分隔符。"""
    if not raw:
        return []
    if isinstance(raw, list):
        names = []
        for item in raw:
            names.extend(split_location_names(str(item)))
        return names
    return [p.strip() for p in LOCATION_SEP_PATTERN.split(str(raw)) if p.strip()]


def ensure_location_chain(names, parent_id):
    """按名称列表逐级查找/创建位置，返回 (最后一级节点, 最后一级是否原本已存在)。"""
    loc = None
    final_existed = False
    for i, name in enumerate(names):
        existing = Location.query.filter_by(name=name, parent_id=parent_id).first()
        is_last = (i == len(names) - 1)
        if existing:
            loc = existing
            if is_last:
                final_existed = True
        else:
            loc = Location(name=name, parent_id=parent_id)
            db.session.add(loc)
            db.session.flush()
        parent_id = loc.id
    return loc, final_existed


# ── Admin auth decorator ────────────────────────────────────────────────────

def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': '需要管理员登录'}), 401
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


# ── Pages ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload')
def upload_page():
    return render_template('upload.html')


@app.route('/changelog')
def changelog():
    import json
    changelog_path = os.path.join(basedir, 'changelog.json')
    versions = []
    if os.path.exists(changelog_path):
        with open(changelog_path, 'r', encoding='utf-8') as f:
            versions = json.load(f)
    return render_template('changelog.html', versions=versions)


@app.route('/admin')
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')


@app.route('/api/admin/login', methods=['POST'])
def admin_login_api():
    data = request.json or {}
    password = data.get('password', '')
    if password == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        return jsonify({'success': True})
    return jsonify({'error': '密码错误'}), 401


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    return render_template('admin_dashboard.html')


# ── API ─────────────────────────────────────────────────────────────────────

@app.route('/api/locations')
def api_locations():
    parent_id = request.args.get('parent_id', type=int)
    if parent_id is None:
        locs = Location.query.filter_by(parent_id=None).order_by(Location.sort_order).all()
    else:
        locs = Location.query.filter_by(parent_id=parent_id).order_by(Location.sort_order).all()

    child_counts = dict(
        db.session.query(Location.parent_id, func.count(Location.id))
        .filter(Location.parent_id.in_([l.id for l in locs]))
        .group_by(Location.parent_id)
        .all()
    ) if locs else {}

    return jsonify([
        {
            'id': l.id,
            'name': l.name,
            'parent_id': l.parent_id,
            'has_children': child_counts.get(l.id, 0) > 0
        }
        for l in locs
    ])


@app.route('/api/locations/path')
def api_location_path():
    loc_id = request.args.get('id', type=int)
    loc = db.session.get(Location, loc_id) if loc_id else None
    if not loc:
        return jsonify({'error': '位置不存在'}), 404
    return jsonify({'id': loc.id, 'name': loc.name, 'path': location_full_path(loc)})


@app.route('/api/locations/descendants')
def api_location_descendants():
    loc_id = request.args.get('id', type=int)
    if not loc_id or not db.session.get(Location, loc_id):
        return jsonify({'error': '位置不存在'}), 404
    return jsonify({'ids': get_all_sub_location_ids(loc_id)})


@app.route('/api/locations/list')
def api_locations_list():
    rows = Location.query.order_by(Location.sort_order).all()
    by_id = {loc.id: loc for loc in rows}

    def build_path(loc):
        parts = []
        seen = set()
        while loc and loc.id not in seen:
            seen.add(loc.id)
            parts.append(loc.name)
            loc = by_id.get(loc.parent_id)
        return '/'.join(reversed(parts))

    return jsonify([
        {'id': loc.id, 'name': loc.name, 'path': build_path(loc)}
        for loc in rows
    ])


@app.route('/api/locations/search')
def api_locations_search():
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify([])
    locs = Location.query.filter(Location.name.contains(q)).order_by(Location.sort_order).limit(20).all()
    return jsonify([
        {'id': l.id, 'name': l.name, 'path': location_full_path(l)}
        for l in locs
    ])


@app.route('/api/locations/create', methods=['POST'])
def create_location():
    data = request.json or {}
    names = split_location_names(data.get('names') if isinstance(data.get('names'), list) else (data.get('name') or ''))
    parent_id = data.get('parent_id')

    if not names:
        return jsonify({'error': '位置名称不能为空'}), 400

    loc, existed = ensure_location_chain(names, parent_id)
    if existed:
        db.session.rollback()
        return jsonify({'error': '位置「' + names[-1] + '」已存在', 'id': loc.id}), 400

    db.session.commit()
    return jsonify({'id': loc.id, 'name': loc.name, 'path': location_full_path(loc)})


@app.route('/api/tags', methods=['POST'])
def create_or_get_tag():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': '标签名不能为空'}), 400
    tag = Tag.query.filter_by(name=name).first()
    if not tag:
        tag = Tag(name=name)
        db.session.add(tag)
        db.session.commit()
    return jsonify({'id': tag.id, 'name': tag.name})


@app.route('/api/tags', methods=['GET'])
def list_tags():
    tags = Tag.query.order_by(Tag.name).all()
    return jsonify([{'id': t.id, 'name': t.name} for t in tags])


@app.route('/api/tags/search')
def search_tags():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    tags = Tag.query.filter(Tag.name.contains(q)).limit(10).all()
    return jsonify([{'id': t.id, 'name': t.name} for t in tags])


@app.route('/api/restaurants')
def api_restaurants():
    location_id = request.args.get('location_id', type=int)
    include_closed = request.args.get('include_closed', '0') == '1'
    tag_ids_str = request.args.get('tag_ids', '')
    q = request.args.get('q', '')
    search_type = request.args.get('search_type', 'name')

    query = Restaurant.query
    if not include_closed:
        query = query.filter_by(is_closed=False)
    if location_id:
        query = query.filter(Restaurant.location_id.in_(location_subtree_ids_query(location_id)))

    if q:
        if search_type == 'review':
            matching_review_restaurant_ids = db.session.query(Review.restaurant_id).filter(
                Review.comment.contains(q)
            ).distinct().all()
            matching_ids = [r[0] for r in matching_review_restaurant_ids]
            if matching_ids:
                query = query.filter(Restaurant.id.in_(matching_ids))
            else:
                return jsonify([])
        else:
            query = query.filter(Restaurant.name.contains(q))

    if tag_ids_str:
        tag_ids = [int(tid) for tid in tag_ids_str.split(',') if tid.strip().isdigit()]
        if tag_ids:
            for tid in tag_ids:
                subquery = db.session.query(RestaurantTag.restaurant_id).filter(
                    RestaurantTag.tag_id == tid
                ).subquery()
                query = query.filter(Restaurant.id.in_(subquery))

    restaurants = query.order_by(Restaurant.name).all()
    result = []

    for r in restaurants:
        stats = get_restaurant_stats(r)
        item = {
            'id': r.id,
            'name': r.name,
            'location_id': r.location_id,
            'location_name': r.location.name if r.location else '',
            'is_closed': r.is_closed,
            'tags': get_restaurant_tags(r.id),
        }
        if stats:
            item.update(stats)
        item['reviews'] = [
            {
                'id': rev.id,
                'username': rev.username,
                'taste_score': rev.taste_score,
                'value_score': rev.value_score,
                'comment': rev.comment,
                'date': rev.created_at.strftime('%Y-%m-%d')
            }
            for rev in r.reviews.all()
        ]
        result.append(item)

    return jsonify(result)


@app.route('/api/restaurants/search')
def search_restaurants():
    q = request.args.get('q', '')
    if not q or len(q) < 1:
        return jsonify([])

    restaurants = Restaurant.query.filter(
        Restaurant.name.contains(q),
        Restaurant.is_closed == False
    ).limit(8).all()

    return jsonify([
        {
            'id': r.id,
            'name': r.name,
            'location_id': r.location_id,
            'location_name': r.location.name if r.location else ''
        }
        for r in restaurants
    ])


@app.route('/api/restaurants', methods=['PUT'])
def search_restaurants_enhanced():
    data = request.json or {}
    q = (data.get('q') or '').strip()
    search_type = data.get('search_type', 'name')

    if not q:
        return jsonify([])

    if search_type == 'review':
        matching_review_restaurant_ids = db.session.query(Review.restaurant_id).filter(
            Review.comment.contains(q)
        ).distinct().all()
        matching_ids = [r[0] for r in matching_review_restaurant_ids]
        if not matching_ids:
            return jsonify([])
        query = Restaurant.query.filter(
            Restaurant.id.in_(matching_ids),
            Restaurant.is_closed == False
        )
    else:
        query = Restaurant.query.filter(
            Restaurant.name.contains(q),
            Restaurant.is_closed == False
        )

    restaurants = query.limit(8).all()

    return jsonify([
        {
            'id': r.id,
            'name': r.name,
            'location_id': r.location_id,
            'location_name': r.location.name if r.location else '',
            'tags': get_restaurant_tags(r.id),
        }
        for r in restaurants
    ])


@app.route('/api/restaurants/<int:restaurant_id>')
def get_restaurant(restaurant_id):
    r = db.session.get(Restaurant, restaurant_id)
    if not r:
        return jsonify({'error': '餐厅不存在'}), 404
    stats = get_restaurant_stats(r)
    item = {
        'id': r.id,
        'name': r.name,
        'location_id': r.location_id,
        'location_name': r.location.name if r.location else '',
        'location_path': location_full_path(r.location) if r.location else '',
        'is_closed': r.is_closed,
        'tags': get_restaurant_tags(r.id),
    }
    if stats:
        item.update(stats)
    return jsonify(item)


@app.route('/api/upload', methods=['POST'])
def upload_review():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    restaurant_id = data.get('restaurant_id')
    taste_score = data.get('taste_score')
    value_score = data.get('value_score')
    comment = (data.get('comment') or '').strip()
    username = (data.get('username') or '').strip()
    review_date = data.get('review_date')
    location_id = data.get('location_id')
    new_location_name = (data.get('new_location_name') or '').strip()
    new_location_parent_id = data.get('new_location_parent_id')
    password = (data.get('password') or '').strip()
    tags = data.get('tags', [])

    if not name:
        return jsonify({'error': '店名不能为空', 'code': 'MISSING_NAME'}), 400

    created_restaurant = False

    if restaurant_id:
        restaurant = db.session.get(Restaurant, restaurant_id)
        if not restaurant:
            return jsonify({'error': '餐厅不存在', 'code': 'RESTAURANT_NOT_FOUND'}), 404
        location_id = restaurant.location_id
    else:
        if not location_id and not new_location_name:
            return jsonify({'error': '位置不能为空', 'code': 'MISSING_LOCATION'}), 400
        restaurant = Restaurant.query.filter_by(name=name, location_id=location_id).first()
        if not restaurant:
            created_restaurant = True
            if new_location_name:
                chain_names = split_location_names(new_location_name)
                loc, existed = ensure_location_chain(chain_names, new_location_parent_id)
                if existed:
                    db.session.rollback()
                    return jsonify({'error': '位置「' + chain_names[-1] + '」已存在', 'code': 'LOCATION_EXISTS'}), 400
                location_id = loc.id
            restaurant = Restaurant(name=name, location_id=location_id)
            db.session.add(restaurant)
            db.session.flush()

    if username:
        dup = Review.query.filter_by(restaurant_id=restaurant.id, username=username).first()
        if dup:
            return jsonify({'error': '用户名「' + username + '」在该店铺已存在', 'code': 'DUPLICATE_USERNAME'}), 400

    try:
        if taste_score not in (None, '', 'null'):
            taste_score = float(taste_score)
            if taste_score < 0.1 or taste_score > 5:
                return jsonify({'error': '味道分必须在0.1-5之间', 'code': 'INVALID_TASTE_SCORE'}), 400
        else:
            return jsonify({'error': '味道分不能为空', 'code': 'MISSING_TASTE_SCORE'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': '味道分格式不正确', 'code': 'INVALID_TASTE_SCORE'}), 400

    try:
        if value_score not in (None, '', 'null'):
            value_score = float(value_score)
            if value_score < 0.1 or value_score > 5:
                return jsonify({'error': '性价比分必须在0.1-5之间', 'code': 'INVALID_VALUE_SCORE'}), 400
        else:
            return jsonify({'error': '性价比分不能为空', 'code': 'MISSING_VALUE_SCORE'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': '性价比分格式不正确', 'code': 'INVALID_VALUE_SCORE'}), 400

    if not comment:
        return jsonify({'error': '评价不能为空', 'code': 'MISSING_COMMENT'}), 400

    if not username:
        count = restaurant.reviews.count()
        username = f'用户{count + 1}'

    if review_date:
        try:
            review_date = datetime.strptime(review_date, '%Y-%m-%d')
        except ValueError:
            review_date = datetime.utcnow()
    else:
        review_date = datetime.utcnow()

    hash_pw = None
    if password:
        hash_pw = generate_password_hash(password, method='pbkdf2:sha256')

    review = Review(
        restaurant_id=restaurant.id,
        username=username,
        taste_score=taste_score,
        value_score=value_score,
        comment=comment,
        password_hash=hash_pw,
        created_at=review_date
    )
    db.session.add(review)
    db.session.commit()

    saved_tags = []
    for tag_name in tags if isinstance(tags, list) else []:
        tag_name = str(tag_name).strip()
        if not tag_name:
            continue
        tag = Tag.query.filter_by(name=tag_name).first()
        if not tag:
            tag = Tag(name=tag_name)
            db.session.add(tag)
            db.session.flush()
        existing_rt = RestaurantTag.query.filter_by(restaurant_id=restaurant.id, tag_id=tag.id).first()
        if not existing_rt:
            rt = RestaurantTag(restaurant_id=restaurant.id, tag_id=tag.id)
            db.session.add(rt)
        if tag_name not in saved_tags:
            saved_tags.append(tag_name)
    db.session.commit()

    return jsonify({
        'success': True,
        'restaurant_id': restaurant.id,
        'restaurant_name': restaurant.name,
        'restaurant_created': created_restaurant,
        'review_id': review.id,
        'location_id': restaurant.location_id,
        'location_path': location_full_path(restaurant.location) if restaurant.location else '',
        'tags': saved_tags,
    })


@app.route('/api/reviews/<int:review_id>', methods=['PUT'])
def edit_review(review_id):
    data = request.json or {}
    password = (data.get('password') or '').strip()
    new_comment = (data.get('comment') or '').strip()
    new_taste = data.get('taste_score')
    new_value = data.get('value_score')

    review = db.session.get(Review, review_id)
    if not review:
        return jsonify({'error': '评价不存在'}), 404
    if not review.password_hash:
        return jsonify({'error': '该评价未设置密钥，无法修改'}), 400
    if not check_password_hash(review.password_hash, password):
        return jsonify({'error': '密钥错误'}), 403

    if new_taste not in (None, '', 'null'):
        try:
            t = float(new_taste)
            if 0.1 <= t <= 5:
                review.taste_score = t
        except (ValueError, TypeError):
            pass

    if new_value not in (None, '', 'null'):
        try:
            v = float(new_value)
            if 0.5 <= v <= 5:
                review.value_score = v
        except (ValueError, TypeError):
            pass

    if new_comment:
        review.comment = new_comment

    review.created_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


# ── Feedback ─────────────────────────────────────────────────────────────────

@app.route('/api/feedback', methods=['POST'])
def submit_feedback():
    data = request.json or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'error': '反馈内容不能为空'}), 400
    fb = Feedback(content=content)
    db.session.add(fb)
    db.session.commit()
    return jsonify({'success': True})


# ── Location Correction ────────────────────────────────────────────────────

@app.route('/api/corrections', methods=['POST'])
def submit_correction():
    data = request.json or {}
    restaurant_id = data.get('restaurant_id')
    submitter = (data.get('submitter') or '匿名').strip()
    new_location_id = data.get('new_location_id')
    new_location_name = (data.get('new_location_name') or '').strip()
    new_location_parent_id = data.get('new_location_parent_id')

    if not restaurant_id:
        return jsonify({'error': '缺少饭店ID'}), 400
    if not new_location_id and not new_location_name:
        return jsonify({'error': '请选择或新建位置'}), 400

    restaurant = db.session.get(Restaurant, restaurant_id)
    if not restaurant:
        return jsonify({'error': '饭店不存在'}), 404

    if new_location_name and not new_location_id:
        chain_names = split_location_names(new_location_name)
        pid = new_location_parent_id
        final_loc = None
        for n in chain_names:
            found = Location.query.filter_by(name=n, parent_id=pid).first()
            if not found:
                final_loc = None
                break
            final_loc = found
            pid = found.id
        if final_loc:
            return jsonify({'error': '位置「' + chain_names[-1] + '」已存在，请直接从列表中选取'}), 400

    correction = LocationCorrection(
        restaurant_id=restaurant_id,
        submitter=submitter,
        old_location_id=restaurant.location_id,
        new_location_id=new_location_id if new_location_id else None,
        new_location_name=new_location_name if not new_location_id else None,
        new_location_parent_id=new_location_parent_id if not new_location_id else None
    )
    db.session.add(correction)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/admin/corrections')
@admin_required
def admin_corrections_page():
    return render_template('admin_corrections.html')


@app.route('/admin/feedback')
@admin_required
def admin_feedback_page():
    return render_template('admin_feedback.html')


@app.route('/api/admin/corrections')
@admin_required
def admin_list_corrections():
    items = LocationCorrection.query.order_by(
        LocationCorrection.status == 'pending',
        LocationCorrection.created_at.desc()
    ).all()
    result = []
    for c in items:
        r = db.session.get(Restaurant, c.restaurant_id)
        old_loc = db.session.get(Location, c.old_location_id) if c.old_location_id else None
        new_loc = db.session.get(Location, c.new_location_id) if c.new_location_id else None
        result.append({
            'id': c.id,
            'restaurant_id': c.restaurant_id,
            'restaurant_name': r.name if r else '',
            'submitter': c.submitter,
            'old_location': location_full_path(old_loc) if old_loc else '',
            'new_location': location_full_path(new_loc) if new_loc else (c.new_location_name or ''),
            'status': c.status,
            'created_at': c.created_at.strftime('%Y-%m-%d %H:%M')
        })
    return jsonify(result)


@app.route('/api/admin/corrections/<int:correction_id>/approve', methods=['POST'])
@admin_required
def admin_approve_correction(correction_id):
    c = db.session.get(LocationCorrection, correction_id)
    if not c:
        return jsonify({'error': '不存在'}), 404
    if c.status != 'pending':
        return jsonify({'error': '已处理'}), 400

    restaurant = db.session.get(Restaurant, c.restaurant_id)
    if not restaurant:
        return jsonify({'error': '饭店不存在'}), 404

    if c.new_location_id:
        restaurant.location_id = c.new_location_id
    elif c.new_location_name:
        chain_names = split_location_names(c.new_location_name)
        loc, _ = ensure_location_chain(chain_names, c.new_location_parent_id)
        restaurant.location_id = loc.id

    c.status = 'approved'
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/corrections/<int:correction_id>/reject', methods=['POST'])
@admin_required
def admin_reject_correction(correction_id):
    c = db.session.get(LocationCorrection, correction_id)
    if not c:
        return jsonify({'error': '不存在'}), 404
    if c.status != 'pending':
        return jsonify({'error': '已处理'}), 400
    c.status = 'rejected'
    db.session.commit()
    return jsonify({'success': True})


# ── Admin API ───────────────────────────────────────────────────────────────

@app.route('/api/admin/all-data')
@admin_required
def admin_all_data():
    top_loc = Location.query.filter_by(parent_id=None).order_by(Location.sort_order).all()

    child_counts = dict(
        db.session.query(Location.parent_id, func.count(Location.id))
        .filter(Location.parent_id.in_([l.id for l in top_loc]))
        .group_by(Location.parent_id)
        .all()
    ) if top_loc else {}

    location_tree = [
        {
            'id': l.id,
            'name': l.name,
            'parent_id': l.parent_id,
            'full_path': location_full_path(l),
            'has_children': child_counts.get(l.id, 0) > 0
        }
        for l in top_loc
    ]

    restaurants = Restaurant.query.order_by(Restaurant.name).all()
    restaurant_data = []
    for r in restaurants:
        stats = get_restaurant_stats(r)
        item = {
            'id': r.id,
            'name': r.name,
            'location_id': r.location_id,
            'location_name': r.location.name if r.location else '',
            'location_path': location_full_path(r.location) if r.location else '',
            'is_closed': r.is_closed,
            'tags': get_restaurant_tags(r.id),
        }
        if stats:
            item.update(stats)
        item['reviews'] = [
            {
                'id': rev.id,
                'username': rev.username,
                'taste_score': rev.taste_score,
                'value_score': rev.value_score,
                'comment': rev.comment,
                'date': rev.created_at.strftime('%Y-%m-%d')
            }
            for rev in r.reviews.all()
        ]
        restaurant_data.append(item)

    return jsonify({
        'location_tree': location_tree,
        'restaurants': restaurant_data
    })


@app.route('/api/admin/reviews/<int:review_id>', methods=['DELETE'])
@admin_required
def admin_delete_review(review_id):
    review = db.session.get(Review, review_id)
    if not review:
        return jsonify({'error': '评论不存在'}), 404
    restaurant_id = review.restaurant_id
    db.session.delete(review)
    db.session.commit()
    return jsonify({'success': True})


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

    return jsonify({
        'success': True,
        'restaurant_id': restaurant_id,
        'deleted_reviews': deleted_reviews,
        'deleted_restaurant_tags': deleted_restaurant_tags,
    })


@app.route('/api/admin/restaurants/<int:restaurant_id>', methods=['PUT'])
@admin_required
def admin_edit_restaurant(restaurant_id):
    r = db.session.get(Restaurant, restaurant_id)
    if not r:
        return jsonify({'error': '餐厅不存在'}), 404

    data = request.json or {}
    if 'name' in data and data['name'].strip():
        r.name = data['name'].strip()
    if 'location_id' in data:
        r.location_id = data['location_id']
    if 'is_closed' in data:
        r.is_closed = bool(data['is_closed'])
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/locations/<int:location_id>', methods=['PUT'])
@admin_required
def admin_edit_location(location_id):
    loc = db.session.get(Location, location_id)
    if not loc:
        return jsonify({'error': '位置不存在'}), 404

    data = request.json or {}
    if 'name' in data and data['name'].strip():
        loc.name = data['name'].strip()
    if 'parent_id' in data:
        new_parent_id = data['parent_id']
        if new_parent_id == location_id:
            return jsonify({'error': '不能将自己设为父级'}), 400
        desc_ids = get_all_sub_location_ids(location_id)
        if new_parent_id in desc_ids:
            return jsonify({'error': '不能将位置移动到自己的子级下'}), 400
        loc.parent_id = new_parent_id
    db.session.commit()

    def update_descendant_paths(loc):
        pass

    return jsonify({'success': True, 'path': location_full_path(loc)})


@app.route('/api/admin/restaurants/<int:restaurant_id>/tags', methods=['POST'])
@admin_required
def admin_add_tag(restaurant_id):
    data = request.json or {}
    tag_name = (data.get('name') or '').strip()
    if not tag_name: return jsonify({'error': '标签名不能为空'}), 400
    tag = Tag.query.filter_by(name=tag_name).first()
    if not tag:
        tag = Tag(name=tag_name)
        db.session.add(tag)
        db.session.flush()
    exist = RestaurantTag.query.filter_by(restaurant_id=restaurant_id, tag_id=tag.id).first()
    if not exist:
        db.session.add(RestaurantTag(restaurant_id=restaurant_id, tag_id=tag.id))
        db.session.commit()
    return jsonify({'success': True, 'tag': {'id': tag.id, 'name': tag.name}})


@app.route('/api/admin/restaurants/<int:restaurant_id>/tags/<int:tag_id>', methods=['DELETE'])
@admin_required
def admin_delete_tag(restaurant_id, tag_id):
    rt = RestaurantTag.query.filter_by(restaurant_id=restaurant_id, tag_id=tag_id).first()
    if rt:
        db.session.delete(rt)
        db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/locations/<int:location_id>', methods=['DELETE'])
@admin_required
def admin_delete_location(location_id):
    loc = db.session.get(Location, location_id)
    if not loc:
        return jsonify({'error': '位置不存在'}), 404

    sub_ids = location_subtree_ids_query(location_id)
    Restaurant.query.filter(Restaurant.location_id.in_(sub_ids)).delete(synchronize_session=False)
    Location.query.filter(Location.id.in_(sub_ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/locations', methods=['POST'])
@admin_required
def admin_create_location():
    data = request.json or {}
    names = split_location_names(data.get('names') if isinstance(data.get('names'), list) else (data.get('name') or ''))
    parent_id = data.get('parent_id')
    if not names:
        return jsonify({'error': '名称不能为空'}), 400

    loc, existed = ensure_location_chain(names, parent_id)
    if existed:
        db.session.rollback()
        return jsonify({'error': '位置「' + names[-1] + '」已存在', 'id': loc.id}), 400

    db.session.commit()
    return jsonify({'id': loc.id, 'name': loc.name, 'path': location_full_path(loc)})


# ── Admin Feedback ─────────────────────────────────────────────────────────

@app.route('/api/admin/feedback')
@admin_required
def admin_list_feedback():
    items = Feedback.query.order_by(Feedback.marked.desc(), Feedback.created_at.desc()).all()
    return jsonify([
        {
            'id': fb.id,
            'content': fb.content,
            'marked': fb.marked,
            'created_at': fb.created_at.strftime('%Y-%m-%d %H:%M:%S')
        }
        for fb in items
    ])


@app.route('/api/admin/feedback/<int:feedback_id>/mark', methods=['POST'])
@admin_required
def admin_toggle_feedback_mark(feedback_id):
    fb = db.session.get(Feedback, feedback_id)
    if not fb:
        return jsonify({'error': '反馈不存在'}), 404
    fb.marked = not fb.marked
    db.session.commit()
    return jsonify({'success': True, 'marked': fb.marked})


@app.route('/api/admin/feedback/<int:feedback_id>', methods=['DELETE'])
@admin_required
def admin_delete_feedback(feedback_id):
    fb = db.session.get(Feedback, feedback_id)
    if not fb:
        return jsonify({'error': '反馈不存在'}), 404
    db.session.delete(fb)
    db.session.commit()
    return jsonify({'success': True})


# ── Admin Backup ────────────────────────────────────────────────────────────

BACKUP_DIR = os.path.join(basedir, 'instance', 'backups')


@app.route('/admin/backup')
@admin_required
def admin_backup_page():
    return render_template('admin_backup.html')


@app.route('/api/admin/backup', methods=['POST'])
@admin_required
def admin_create_backup():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        db_path = os.path.join(basedir, 'instance', 'foodlisten.db')
        if not os.path.exists(db_path):
            return jsonify({'error': '数据库文件不存在'}), 500
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        filename = f'backup_{timestamp}.db'
        shutil.copy2(db_path, os.path.join(BACKUP_DIR, filename))
        return jsonify({'success': True, 'filename': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/backups')
@admin_required
def admin_list_backups():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backups = []
        for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if f.endswith('.db'):
                fpath = os.path.join(BACKUP_DIR, f)
                stat = os.stat(fpath)
                backups.append({
                    'filename': f,
                    'size': stat.st_size,
                    'created_at': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
        return jsonify(backups)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/restore', methods=['POST'])
@admin_required
def admin_restore_backup():
    data = request.json or {}
    filename = (data.get('filename') or '').strip()
    if not filename:
        return jsonify({'error': '请指定备份文件'}), 400

    backup_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(backup_path):
        return jsonify({'error': '备份文件不存在'}), 404

    try:
        db_path = os.path.join(basedir, 'instance', 'foodlisten.db')
        db.session.close()
        db.engine.dispose()
        shutil.copy2(backup_path, db_path)
        ensure_schema_compat(full=True)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/backups/<filename>', methods=['DELETE'])
@admin_required
def admin_delete_backup(filename):
    backup_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(backup_path):
        return jsonify({'error': '备份文件不存在'}), 404
    try:
        os.remove(backup_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── PDF ─────────────────────────────────────────────────────────────────────

PDF_DIR = os.path.join(basedir, 'instance', 'pdfs')


def generate_pdf():
    try:
        os.makedirs(PDF_DIR, exist_ok=True)

        with app.app_context():
            relevant_ids = set()
            for (rid,) in db.session.query(Restaurant.location_id).filter(
                Restaurant.location_id.isnot(None)
            ).distinct().all():
                cur = db.session.get(Location, rid)
                while cur:
                    relevant_ids.add(cur.id)
                    cur = cur.parent

            def build_group(loc):
                restaurants = []
                for r in Restaurant.query.filter_by(location_id=loc.id, is_closed=False).order_by(Restaurant.name).all():
                    stats = get_restaurant_stats(r)
                    reviews = [
                        {
                            'username': rev.username,
                            'taste_score': rev.taste_score,
                            'value_score': rev.value_score,
                            'comment': rev.comment or '',
                            'date': rev.created_at.strftime('%Y-%m-%d')
                        }
                        for rev in r.reviews.all()
                    ]
                    restaurants.append({
                        'id': r.id,
                        'name': r.name,
                        'stats': stats,
                        'reviews': reviews
                    })

                children = []
                for child in Location.query.filter_by(parent_id=loc.id).order_by(Location.sort_order).all():
                    if child.id not in relevant_ids:
                        continue
                    subgroup = build_group(child)
                    if subgroup['restaurants'] or subgroup['children']:
                        children.append(subgroup)

                return {
                    'id': loc.id,
                    'name': loc.name,
                    'depth': 2,
                    'restaurants': restaurants,
                    'children': children
                }

            top = Location.query.filter_by(parent_id=None).order_by(Location.sort_order).all()
            groups = []
            for t in top:
                groups.append(build_group(t))

            def set_depth(grp_list, d):
                for g in grp_list:
                    g['depth'] = d
                    if g['children']:
                        set_depth(g['children'], d + 1)

            set_depth(groups, 2)

            html = render_template('pdf_report.html', groups=groups, now=datetime.utcnow())

        from weasyprint import HTML
        HTML(string=html).write_pdf(os.path.join(PDF_DIR, 'report.pdf'))
        print(f"[PDF] Generated at {datetime.utcnow()}")
    except ImportError:
        print("[PDF] WeasyPrint not installed. Run: pip install weasyprint")
    except OSError as e:
        print(f"[PDF] System library missing: {e}")
        print("[PDF] On Ubuntu/Debian run: apt install libgobject-2.0-0 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0")
    except Exception as e:
        print(f"[PDF] Generation failed: {e}")


@app.route('/download/report.pdf')
def download_report():
    pdf_path = os.path.join(PDF_DIR, 'report.pdf')
    if not os.path.exists(pdf_path):
        generate_pdf()
    if not os.path.exists(pdf_path):
        abort(404, description='PDF 尚未生成，请稍后再试')
    return send_file(pdf_path, as_attachment=True, download_name='foodlisten_report.pdf')


# ── Scheduler ───────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()
scheduler.add_job(generate_pdf, 'cron', hour=0, minute=0)

if os.environ.get('ENABLE_SCHEDULER', 'true').lower() == 'true':
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())


# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(os.path.join(basedir, 'instance'), exist_ok=True)
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', 5000))
    print(f"Server starting at http://0.0.0.0:{port}")
    app.run(debug=True, host='0.0.0.0', port=port)
