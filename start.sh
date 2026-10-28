#!/usr/bin/env bash
# QQ Bot Studio - 在本机浏览器中打开一键部署向导
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

say() { printf '%s\n' "$*"; }
die() { printf '\n错误：%b\n' "$*" >&2; exit 1; }
running_as_root() { [[ "${EUID}" -eq 0 ]]; }

if ! command -v python3 >/dev/null 2>&1; then
  die "未找到 python3。请先执行：sudo apt update && sudo apt install -y python3"
fi
PY_OK="$(python3 - <<'PY'
import sys
print("yes" if sys.version_info >= (3, 10) else "no")
PY
)"
[[ "$PY_OK" == "yes" ]] || die "需要 Python 3.10 或更新版本。"

need_install=0
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  need_install=1
fi

if [[ "$need_install" -eq 1 ]]; then
  say "检测到 Docker / Docker Compose 尚未安装完整。"
  say "这一步需要管理员权限；安装完成后会自动继续。"
  say "注意：安装 Docker 需要联网，可能耗时数分钟。"
  echo
  if running_as_root; then
    bash "$ROOT_DIR/scripts/install-docker.sh"
  else
    command -v sudo >/dev/null 2>&1 || die "需要 sudo 来安装 Docker。"
    sudo -v || die "未能获取管理员权限，Docker 未安装。"
    sudo env QQ_BOT_STUDIO_TARGET_USER="${SUDO_USER:-${USER:-$(id -un)}}" bash "$ROOT_DIR/scripts/install-docker.sh"
  fi
  echo
fi

# 尝试启动 Docker 守护进程。Ubuntu 通常使用 systemd；WSL/部分环境可能使用
# service。失败不阻塞启动，后续检查会给出明确提示。
if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
  if running_as_root; then
    if command -v systemctl >/dev/null 2>&1; then
      systemctl start docker >/dev/null 2>&1 || true
    elif command -v service >/dev/null 2>&1; then
      service docker start >/dev/null 2>&1 || true
    fi
  else
    if ! sudo -n true >/dev/null 2>&1; then
      say "需要管理员权限启动 Docker 守护进程。"
      sudo -v || true
    fi
    if sudo -n true >/dev/null 2>&1; then
      if command -v systemctl >/dev/null 2>&1; then
        sudo -n systemctl start docker >/dev/null 2>&1 || true
      elif command -v service >/dev/null 2>&1; then
        sudo -n service docker start >/dev/null 2>&1 || true
      fi
    fi
  fi
  # 给守护进程一点启动时间。
  for _ in 1 2 3 4 5; do
    docker info >/dev/null 2>&1 && break
    sleep 0.6
  done
fi

# 如果当前用户已有 docker 组权限、但当前 shell 尚未刷新组信息，可用 sg
# 进入 docker 组后启动。参数逐项转义，避免路径或参数中的空格导致命令拆错。
if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
  if command -v sg >/dev/null 2>&1 \
    && id -nG "${USER:-$(id -un)}" 2>/dev/null | tr ' ' '\n' | grep -qx docker \
    && sg docker -c "docker info" >/dev/null 2>&1; then
    say "正在以 docker 组身份启动（无需重新登录）..."
    ROOT_Q="$(printf '%q' "$ROOT_DIR")"
    ARGS_Q=""
    if [[ "$#" -gt 0 ]]; then
      printf -v ARGS_Q ' %q' "$@"
    fi
    exec sg docker -c "cd $ROOT_Q && exec python3 -m qqbot_studio$ARGS_Q"
  fi

  # 否则让用户在终端缓存一次 sudo 凭据；后端只使用 `sudo -n docker`，
  # 不会在网页里请求 sudo 密码。
  if ! sudo -n true >/dev/null 2>&1; then
    say "Docker 已安装，但当前用户还没有 Docker 权限。"
    say "请输入一次管理员密码以完成本次部署（密码不会保存在本工具中）。"
    sudo -v || die "未能获取管理员权限。可注销并重新登录后再次运行本程序。"
  fi
  export QQBOT_SUDO_DOCKER=1
  say "本次运行将使用 sudo 调用 Docker。"
fi

# 安装/授权流程被取消、失败，或 Docker 只装了一半时，必须在这里明确停止，
# 不能带着“Docker 未就绪”的状态照常启动向导。
if ! command -v docker >/dev/null 2>&1; then
  die "Docker 安装未完成（可能已取消或失败），未启动向导。\n请重新运行 ./install.sh，或手动执行：\n  sudo apt update && sudo apt install -y docker.io docker-compose-v2\n  sudo systemctl enable --now docker\n  sudo usermod -aG docker \"${USER:-$(id -un)}\"\n然后注销并重新登录。"

fi
if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose 插件未安装，未启动向导。请运行：\nsudo apt update && sudo apt install -y docker-compose-v2\n或参考 Docker 官方文档安装 docker-compose-plugin。"
fi

docker_accessible() {
  docker info >/dev/null 2>&1 && return 0
  if [[ "${QQBOT_SUDO_DOCKER:-0}" == "1" ]] && sudo -n docker info >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

if ! docker_accessible; then
  die "Docker 服务未运行，或当前用户没有 Docker 权限，未启动向导。\n请执行以下命令后重试：\n  sudo systemctl start docker\n  sudo usermod -aG docker \"${USER:-$(id -un)}\"\n然后注销并重新登录；也可以运行 sudo -v 后重新打开本程序。\n验证：docker info"
fi

if [[ ! -f "$ROOT_DIR/qqbot_studio/__init__.py" ]]; then
  die "程序文件不完整：缺少 qqbot_studio/__init__.py。"
fi

# 首次运行时安装桌面图标（失败不影响主程序）。
if [[ -x "$ROOT_DIR/scripts/install-desktop-entry.sh" && ! -f "${HOME}/.local/share/applications/qqbot-studio.desktop" ]]; then
  bash "$ROOT_DIR/scripts/install-desktop-entry.sh" "$ROOT_DIR" >/dev/null 2>&1 || true
fi

exec python3 -m qqbot_studio "$@"
