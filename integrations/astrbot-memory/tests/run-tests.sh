#!/usr/bin/env bash
# Run the native-memory compatibility tests against a patched AstrBot tree.
#   ASTRBOT_SRC=/path/to/patched/AstrBot tests/run-tests.sh [unittest args]
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ASTRBOT_SRC="${ASTRBOT_SRC:-/tmp/astrbot-memory-src}" exec python3 -m unittest discover -s "$HERE" -t "$HERE" -p "test_*.py" "$@"
