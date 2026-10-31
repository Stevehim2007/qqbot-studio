#!/usr/bin/env bash
# Convenience wrapper for the real, opt-in container E2E smoke test.
# This script is intentionally not called by the normal deployment flow.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/tests/e2e/e2e_smoke.py" "$@"
