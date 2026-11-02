#!/bin/sh
# Contract tests for the AI review submission endpoint (POST /api/upload).
#
# Rebuilds both variants in a temp dir so nothing read-only is touched and no
# stale copy of the site source is vendored:
#   v1   = untouched production app.py               (base)
#   v1.1 = v1 + food-ai-review-v1.1.patch            (review API contract patch)
#   v1.2 = full source copy + food-site-v1.2-fixes.patch (admin delete + dropdown fix)
# Each run uses a throwaway sqlite DB; production instance/foodlisten.db is never opened.
#
#   usage: ./run-tests.sh            # source defaults to /home/stevehim/food.dlsz.top
#          FOOD_SRC=/path/site ./run-tests.sh
#
# Needs: python3 + flask==2.0.3 werkzeug==2.0.3 flask_sqlalchemy==2.5.1
#        sqlalchemy==1.4.54 apscheduler==3.9.1 setuptools<81
set -e
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
SRC=${FOOD_SRC:-/home/stevehim/food.dlsz.top}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/food-ai-contract.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/v1/instance" "$WORK/v1.1/instance"
cp "$SRC/app.py" "$WORK/v1/app.py"
cp "$SRC/app.py" "$WORK/v1.1/app.py"
(cd "$WORK/v1.1" && patch -p1 --quiet < "$HERE/food-ai-review-v1.1.patch")
cd "$HERE"
echo "== v1: untouched production app.py =="
FOOD_APP_DIR="$WORK/v1" ENABLE_SCHEDULER=false python3 -m unittest test_ai_review_api
echo "== v1.1: patch applied (stable codes + ids) =="
FOOD_APP_DIR="$WORK/v1.1" FOOD_EXPECT_ENHANCED=1 ENABLE_SCHEDULER=false python3 -m unittest test_ai_review_api
mkdir -p "$WORK/v1.2/instance"
cp "$SRC/app.py" "$WORK/v1.2/app.py"
cp -R "$SRC/static" "$WORK/v1.2/static"
cp -R "$SRC/templates" "$WORK/v1.2/templates"
(cd "$WORK/v1.2" && patch -p1 --quiet < "$HERE/food-site-v1.2-fixes.patch")
echo "== v1.2: site fixes (admin restaurant delete + location dropdown) =="
FOOD_APP_DIR="$WORK/v1.2" ENABLE_SCHEDULER=false python3 -m unittest test_site_fixes
