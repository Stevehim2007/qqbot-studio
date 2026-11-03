#!/bin/sh
# Contract + site tests for food.dlsz.top.
#
# 现状（v1.3 交付时）：生产源码已经包含 v1.1（stabilized codes）与 v1.2（后台删饭店 +
# 下拉修复），所以基线不再对源码重新打这两个补丁，而是：
#   v1    = 当前源码反向回退 v1.1 补丁   → 旧契约（按 error 文本匹配）回归
#   v1.1  = 当前源码原样                  → 契约增强版回归（FOOD_EXPECT_ENHANCED=1）
#   v1.2  = 当前源码原样                  → 站点修复回归（test_site_fixes 静态/鉴权断言）
#   v1.3  = 当前源码 + food-site-v1.3.patch（markdown / 全路径搜索 / 位置复用）
#
# Each run uses a throwaway build tree + its own sqlite DB; the production
# instance/foodlisten.db is never opened or written.
#
#   usage: ./run-tests.sh            # source defaults to /home/stevehim/food.dlsz.top
#          FOOD_SRC=/path/site ./run-tests.sh
#
# Needs: python3 + flask==2.0.3 werkzeug==2.0.3 flask_sqlalchemy==2.5.1
#        sqlalchemy==1.4.54 apscheduler==3.9.1 setuptools<81  (and node for the
#        markdown XSS renderer test; that one test skips if node is absent)
set -e
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
SRC=${FOOD_SRC:-/home/stevehim/food.dlsz.top}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/food-ai-contract.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/v1/instance" "$WORK/v1.1/instance" "$WORK/v1.2/instance" "$WORK/v1.3/instance"

cp "$SRC/app.py" "$WORK/v1/app.py"
(cd "$WORK/v1" && patch -R -p1 --quiet < "$HERE/food-ai-review-v1.1.patch")

cp "$SRC/app.py" "$WORK/v1.1/app.py"

cp "$SRC/app.py" "$WORK/v1.2/app.py"
cp -R "$SRC/static" "$SRC/templates" "$WORK/v1.2/"

cp "$SRC/app.py" "$WORK/v1.3/app.py"
cp -R "$SRC/static" "$SRC/templates" "$WORK/v1.3/"
(cd "$WORK/v1.3" && patch -p1 --quiet < "$HERE/food-site-v1.3.patch")

cd "$HERE"
echo "== v1: base contract (v1.1 patch reversed) =="
FOOD_APP_DIR="$WORK/v1" ENABLE_SCHEDULER=false python3 -m unittest test_ai_review_api
echo "== v1.1: current source (stable codes + ids) =="
FOOD_APP_DIR="$WORK/v1.1" FOOD_EXPECT_ENHANCED=1 ENABLE_SCHEDULER=false python3 -m unittest test_ai_review_api
echo "== v1.2: current source (admin restaurant delete + location dropdown) =="
FOOD_APP_DIR="$WORK/v1.2" ENABLE_SCHEDULER=false python3 -m unittest test_site_fixes
echo "== v1.3: markdown comments + full-address search + existing-location reuse =="
FOOD_APP_DIR="$WORK/v1.3" FOOD_SRC="$SRC" ENABLE_SCHEDULER=false python3 -m unittest test_site_v13
