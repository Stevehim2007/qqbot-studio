#!/usr/bin/env bash
# Verify the native-memory patch against a pristine AstrBot v4.29.3 tree and
# run the compatibility test suite against the patched result.
#
# Usage:
#   scripts/verify-patch.sh [workdir]
#
# Env:
#   ASTRBOT_SRC_URL   tarball URL (default: GitHub codeload for v4.29.3)
#   ASTRBOT_SRC_TGZ   use a local tarball instead of downloading
#   ASTRBOT_SRC       use an already-patched source tree (skips download/apply)
set -Eeuo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH="$HERE/patches/0001-native-memory.patch"
WORKDIR="${1:-${TMPDIR:-/tmp}/astrbot-memory-verify}"
URL_DEFAULT="https://codeload.github.com/AstrBotDevs/AstrBot/tar.gz/refs/tags/v4.29.3"

if [[ -n "${ASTRBOT_SRC:-}" ]]; then
  SRC="$ASTRBOT_SRC"
  echo "==> using existing patched source: $SRC"
else
  TARBALL="${ASTRBOT_SRC_TGZ:-}"
  if [[ -z "$TARBALL" ]]; then
    TARBALL="$WORKDIR/astrbot-v4.29.3.tar.gz"
    mkdir -p "$WORKDIR"
    if [[ ! -s "$TARBALL" ]]; then
      echo "==> downloading AstrBot v4.29.3 ($URL_DEFAULT)"
      curl -L --retry 4 --retry-all-errors -o "$TARBALL" "${ASTRBOT_SRC_URL:-$URL_DEFAULT}"
    fi
  fi
  SRC="$WORKDIR/src"
  echo "==> extracting $TARBALL"
  rm -rf "$SRC" && mkdir -p "$SRC"
  tar -xzf "$TARBALL" -C "$SRC" --strip-components=1
  echo "==> applying $PATCH"
  if git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$SRC" apply --check "$PATCH"
    git -C "$SRC" apply "$PATCH"
  else
    ( cd "$SRC" && patch -p1 --dry-run < "$PATCH" && patch -p1 < "$PATCH" )
  fi
fi

echo "==> compiling patched modules"
python3 -m py_compile \
  "$SRC/astrbot/core/memory/"*.py \
  "$SRC/astrbot/core/astr_main_agent.py" \
  "$SRC/astrbot/core/config/default.py" \
  "$SRC/astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py"

echo "==> running compatibility tests"
cd "$HERE/tests"
ASTRBOT_SRC="$SRC" python3 -m unittest discover -s . -p "test_*.py" -v
