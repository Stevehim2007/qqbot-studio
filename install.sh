#!/usr/bin/env bash
# 安装运行环境（Docker、Python、screen），然后自动启动 start.sh。
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

cat <<'BANNER'
==============================================
  QQ Bot Studio v2
  AstrBot + SnowLuma + OneBot v11 + DeepSeek
  多机器人实例 / 镜像节点 / 后台 screen 运行
==============================================
BANNER
echo
echo "安全提示：管理服务默认监听 0.0.0.0，可能被局域网或公网扫描。"
echo "请务必使用防火墙/VPN/安全组限制访问，详见 README。"
echo

if [[ "${EUID}" -eq 0 ]]; then
  echo "请使用普通用户运行本脚本（不要直接 sudo ./install.sh）。" >&2
  exit 1
fi
command -v sudo >/dev/null 2>&1 || { echo "错误：安装需要 sudo。" >&2; exit 1; }

if ! command -v python3 >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3
fi

need_install=0
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  need_install=1
fi

if [[ "$need_install" -eq 1 ]]; then
  sudo -v || { echo "错误：安装 Docker 需要管理员权限。" >&2; exit 1; }
  sudo env QQ_BOT_STUDIO_TARGET_USER="${USER:-$(id -un)}" bash "$ROOT_DIR/scripts/install-docker.sh"
elif docker info >/dev/null 2>&1; then
  echo "Docker、Docker Compose 与守护进程均已就绪。"
else
  echo "Docker CLI 已安装；将在启动阶段检查守护进程和容器状态。"
fi

# screen 用于让管理服务和部署任务在关闭终端后继续运行。
if ! command -v screen >/dev/null 2>&1; then
  sudo -v || true
  sudo apt-get update
  sudo apt-get install -y screen
fi
if ! command -v curl >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y curl ca-certificates
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：Docker 安装未完成（可能已取消或失败），未启动向导。" >&2
  echo "请重新运行 ./install.sh，或手动安装 Docker 后重试。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "错误：Docker Compose 插件不可用，未启动向导。" >&2
  echo "请安装 docker-compose-v2 / docker-compose-plugin 后重试。" >&2
  exit 1
fi

# 可选：创建桌面入口（服务器上失败不影响启动）。
if [[ -x "$ROOT_DIR/scripts/install-desktop-entry.sh" ]]; then
  bash "$ROOT_DIR/scripts/install-desktop-entry.sh" "$ROOT_DIR" >/dev/null 2>&1 || true
fi

# 安装完成后自动启动 start。
exec bash "$ROOT_DIR/start.sh" "$@"
