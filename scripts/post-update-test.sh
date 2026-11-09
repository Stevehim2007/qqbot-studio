#!/usr/bin/env bash
# Run after code has been switched by the updater, before the management
# server is restarted.  Exit non-zero so auto-update.sh can refuse to restart
# a broken build and leave the previous process alive.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== post-update tests $(date -Is) ==="
echo "head=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

status=0
run_step() {
  local label="$1"
  shift
  echo "--- $label"
  if "$@"; then
    echo "--- $label: OK"
  else
    status=1
    echo "--- $label: FAILED"
  fi
}

run_step "repository unittest suite" \
  python3 -m unittest discover -s tests -q

run_step "python syntax" \
  python3 -m py_compile qqbot_studio/*.py

if command -v node >/dev/null 2>&1; then
  run_step "console javascript syntax" \
    node --check qqbot_studio/static/console.js
else
  echo "--- console javascript syntax: SKIP (node not installed)"
fi

if [ -n "${ASTRBOT_SRC_TGZ:-}" ] && [ -f "$ASTRBOT_SRC_TGZ" ]; then
  run_step "AstrBot memory patch verification" \
    bash integrations/astrbot-memory/scripts/verify-patch.sh "${QQBOT_STUDIO_VERIFY_WORKDIR:-/tmp/qqbot-memory-verify}"
else
  echo "--- AstrBot memory patch verification: SKIP (set ASTRBOT_SRC_TGZ to enable)"
fi

if [ "$status" -ne 0 ]; then
  echo "=== post-update tests FAILED; management server will NOT be restarted ==="
  exit 1
fi
echo "=== post-update tests PASS ==="
echo "manual acceptance checklist: docs/post-update-verification.md"
echo "required: containers healthy, memory import, policy, DB, /记忆, real QQ message, food site smoke"
