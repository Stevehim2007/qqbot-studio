#!/usr/bin/env bash
# Release-owned post-checkout update hook.
#
# QQ Bot Studio copies the just-downloaded revision first, then runs this file
# before restarting the management service.  Each release may change the body.
#
# Contract:
#   * Idempotent. A retry must be safe.
#   * Local only. Do not download or execute remote content here.
#   * Fail closed. Any non-zero exit aborts the update and restores the
#     previous Git revision.
#   * Never print tokens, passwords, cookies or full config files.
#
# Environment supplied by qqbot_studio.updater:
#   QQBOT_UPDATE_ROOT, QQBOT_UPDATE_DATA_DIR,
#   QQBOT_UPDATE_FROM_VERSION, QQBOT_UPDATE_TO_VERSION,
#   QQBOT_UPDATE_CHANNEL
set -Eeuo pipefail

ROOT_DIR="${QQBOT_UPDATE_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
DATA_DIR="${QQBOT_UPDATE_DATA_DIR:-${QQBOT_STUDIO_DATA_DIR:-$HOME/.local/share/qqbot-studio}}"
FROM_VERSION="${QQBOT_UPDATE_FROM_VERSION:-unknown}"
TO_VERSION="${QQBOT_UPDATE_TO_VERSION:-unknown}"
CHANNEL="${QQBOT_UPDATE_CHANNEL:-unknown}"

echo "release update hook: ${FROM_VERSION} -> ${TO_VERSION} (${CHANNEL})"
echo "repo=${ROOT_DIR} data=${DATA_DIR}"

# ---------------------------------------------------------------------------
# Version-specific migrations go here.
#
# Keep this block cumulative and idempotent.  Example:
#
# case "$TO_VERSION" in
#   4.4.0-beta.11)
#     python3 - "$DATA_DIR" <<'PY'
#     ...
#     PY
#     ;;
# esac
# ---------------------------------------------------------------------------

# The current V2/beta.10 release needs no data migration.  Keep the hook in
# every release so future updates have a stable, trusted execution point.
exit 0
