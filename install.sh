#!/usr/bin/env bash
# QQ Bot Studio 一键准备并启动脚本
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

cat <<'BANNER'
==============================================
  QQ Bot Studio · QQ 机器人一键部署
  AstrBot + SnowLuma + OneBot v11 + DeepSeek
==============================================
BANNER
echo
echo "提示：本工具不会要求你输入 QQ 密码。QQ 登录全程使用官方扫码，"
echo "如遇设备验证、短信验证或安全验证，请在 noVNC 中按 QQ 提示完成。"
echo

if [[ "${EUID}" -eq 0 ]]; then
  echo "请使用普通用户运行本脚本（不要直接 sudo ./install.sh）。" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：未找到 python3。请先执行：sudo apt update && sudo apt install -y python3" >&2
  exit 1
fi

need_install=0
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  need_install=1
fi

if [[ "$need_install" -eq 1 ]]; then
  command -v sudo >/dev/null 2>&1 || { echo "错误：安装 Docker 需要 sudo。" >&2; exit 1; }
  sudo -v || { echo "错误：安装 Docker 需要管理员权限；请在有 sudo 权限的账户下重试。" >&2; exit 1; }
  sudo env QQ_BOT_STUDIO_TARGET_USER="${USER:-$(id -un)}" bash "$ROOT_DIR/scripts/install-docker.sh"
elif docker info >/dev/null 2>&1; then
  echo "Docker、Docker Compose 与守护进程均已就绪。"
else
  echo "Docker 与 Docker Compose CLI 已安装；守护进程状态将在启动阶段检查。"
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

if bash "$ROOT_DIR/scripts/install-desktop-entry.sh" "$ROOT_DIR"; then
  echo "桌面启动项已创建；以后可从应用菜单打开“QQ 机器人一键部署”。"
else
  echo "警告：桌面启动项安装失败；仍可在终端运行 ./start.sh。" >&2
fi
exec bash "$ROOT_DIR/start.sh" "$@"
