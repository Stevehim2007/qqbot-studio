#!/usr/bin/env bash
# 新建一个 QQ 机器人实例，并打开部署向导。
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# If Docker is installed but the current shell lacks the refreshed docker
# group / cached sudo credentials, ask once in the terminal before starting the
# background manager. The web UI never receives the sudo password.
if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
  if ! sudo -n true >/dev/null 2>&1; then
    echo "需要一次管理员授权来访问 Docker（密码不会写入任何文件）。"
    sudo -v || true
  fi
fi

exec python3 -m qqbot_studio.launcher deploy "$@"
