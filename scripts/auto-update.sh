#!/usr/bin/env bash
# Detached updater/restarter launched by QQ Bot Studio's management console.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${1:-${QQBOT_STUDIO_DATA_DIR:-$HOME/.local/share/qqbot-studio}}"
# pull   = fetch + fast-forward (legacy behaviour)
# restart = code was already switched by the updater (rollback/test channel)
MODE="${2:-pull}"
LOG_FILE="$DATA_DIR/auto-update.log"
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR" 2>/dev/null || true
exec >>"$LOG_FILE" 2>&1

echo "=== QQ Bot Studio auto update $(date -Is) ==="
echo "repo=$ROOT_DIR data=$DATA_DIR mode=$MODE"

export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/true
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ControlMaster=no -o ControlPath=none}"

# Let the HTTP response leave the old management server before replacing it.
sleep 2
cd "$ROOT_DIR"
if [ "$MODE" = "pull" ]; then
  git pull --ff-only
else
  echo "skip git pull; code already switched to $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

# Release-owned update hook.  Newer updaters run this synchronously before
# launching us and set QQBOT_UPDATE_SCRIPT_ALREADY_RAN=1.  Older updaters do
# not, so this is also the one-time bootstrap path for the first release that
# ships scripts/update.sh.
if [ "${QQBOT_UPDATE_SCRIPT_ALREADY_RAN:-0}" != "1" ] && [ -f "$ROOT_DIR/scripts/update.sh" ]; then
  echo "=== running release update hook (bootstrap path) ==="
  hook_log="$DATA_DIR/release-update.log"
  umask 077
  mkdir -p "$DATA_DIR"
  touch "$hook_log"
  chmod 600 "$hook_log" 2>/dev/null || true
  printf '=== %s from=%s to=%s channel=%s status=bootstrap ===\n' \
    "$(date -Is)" "${QQBOT_UPDATE_FROM_VERSION:-unknown}" "${QQBOT_UPDATE_TO_VERSION:-unknown}" "${QQBOT_UPDATE_CHANNEL:-unknown}" >> "$hook_log"
  if ! timeout 600 bash "$ROOT_DIR/scripts/update.sh" >> "$hook_log" 2>&1; then
    echo "=== auto update aborted: release update hook failed; old server left running ==="
    exit 1
  fi
fi

# Automatic post-update verification.  Keep the old management process alive
# when tests fail; a broken build must not be restarted silently.
if [ "${QQBOT_STUDIO_AUTO_TEST:-1}" != "0" ]; then
  if ! bash "$ROOT_DIR/scripts/post-update-test.sh"; then
    echo "=== auto update aborted: post-update tests failed; old server left running ==="
    exit 1
  fi
else
  echo "=== post-update tests disabled by QQBOT_STUDIO_AUTO_TEST=0 ==="
fi

# Final modular release: never copy qqbot_enhancer/qqbot_core_full into an
# instance.  The seven standalone plugins are installed and updated from the
# plugin market with dependency ordering and confirmation.

# Best-effort: restart existing AstrBot containers so plugin files and packaged
# code updates are loaded. If Docker is only accessible through sudo, leave the
# containers running; the manager restart below still succeeds.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && [ -d "$DATA_DIR/instances" ]; then
  for instance_dir in "$DATA_DIR"/instances/*; do
    [ -f "$instance_dir/compose.yml" ] || continue
    [ -f "$instance_dir/.env" ] || continue
    project_name="$(python3 - "$instance_dir" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1]) / "instance.json"
try:
    print(json.loads(path.read_text(encoding="utf-8")).get("project_name", ""))
except Exception:
    print("")
PY
)"
    [ -n "$project_name" ] || continue
    docker compose --project-name "$project_name" --env-file "$instance_dir/.env" -f "$instance_dir/compose.yml" restart astrbot >/dev/null 2>&1 || true
  done
fi

# Read the persisted manager bind settings.
read -r HOST PORT < <(python3 - "$DATA_DIR" <<'PY'
import json
import sys
from pathlib import Path

data_dir = Path(sys.argv[1])
settings_path = data_dir / "studio.json"
try:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
except Exception:
    settings = {}
print(str(settings.get("bind_host") or "0.0.0.0"), int(settings.get("bind_port") or 8765))
PY
)

screen_bin="$(command -v screen || true)"
if [ -n "$screen_bin" ]; then
  "$screen_bin" -S qqbot-studio-server -X quit >/dev/null 2>&1 || true
fi
if [ -f "$DATA_DIR/server.pid" ]; then
  old_pid="$(cat "$DATA_DIR/server.pid" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
  fi
fi
sleep 2

server_cmd="cd $(printf '%q' "$ROOT_DIR") && exec python3 -m qqbot_studio --host $(printf '%q' "$HOST") --port $(printf '%q' "$PORT") --data-dir $(printf '%q' "$DATA_DIR") --no-browser >> $(printf '%q' "$DATA_DIR/studio-server.log") 2>&1"
if [ -n "$screen_bin" ]; then
  if "$screen_bin" -dmS qqbot-studio-server bash -c "$server_cmd"; then
    echo "started with screen"
  else
    echo "screen failed; using nohup"
    nohup bash -c "$server_cmd" >/dev/null 2>&1 &
  fi
else
  echo "screen unavailable; using nohup"
  nohup bash -c "$server_cmd" >/dev/null 2>&1 &
fi

echo "=== update finished $(date -Is) ==="
