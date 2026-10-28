#!/usr/bin/env bash
# Install Docker Engine + Compose plugin on Ubuntu. Idempotent.
set -Eeuo pipefail

TARGET_USER="${QQ_BOT_STUDIO_TARGET_USER:-${SUDO_USER:-${USER:-}}}"
export DEBIAN_FRONTEND=noninteractive
umask 077

# Root-run temporary files must never use predictable /tmp names: another local
# user could pre-create a symlink and turn the download into an arbitrary-write
# or arbitrary-code-execution primitive.
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/qqbot-docker-install.XXXXXXXX")"
cleanup() { rm -rf -- "$TMP_DIR"; }
trap cleanup EXIT INT TERM

log()  { printf '[docker-install] %s\n' "$*"; }
warn() { printf '[docker-install] 警告：%s\n' "$*" >&2; }
die()  { printf '[docker-install] 错误：%s\n' "$*" >&2; exit 1; }

if [[ "${EUID}" -ne 0 ]]; then
  die "请通过 sudo 运行本脚本。"
fi
command -v apt-get >/dev/null 2>&1 || die "当前系统不是基于 apt 的 Ubuntu/Debian 发行版。"

docker_compose_ok() {
  command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}

if docker_compose_ok; then
  log "Docker 与 Compose 已安装，跳过。"
else
  # 基础依赖
  apt-get update -y
  apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release apt-transport-https

  . /etc/os-release
  DISTRO_ID="${ID:-ubuntu}"
  if [[ "$DISTRO_ID" != "ubuntu" && "$DISTRO_ID" != "debian" ]]; then
    DISTRO_ID="ubuntu"
  fi
  CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-noble}}"
  ARCH="$(dpkg --print-architecture)"

  installed=0
  # 优先使用 Docker 官方 apt 仓库，以获得 docker compose 插件。
  set +e
  install -m 0755 -d /etc/apt/keyrings
  # Pipe directly from curl to gpg. The destination is root-controlled and the
  # pipeline runs with `set -o pipefail`, so a failed download cannot be
  # mistaken for a successful signed repository setup.
  curl --http1.1 --retry 5 --retry-all-errors -fsSL \
    "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
    | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  curl_rc=$?
  if [[ "$curl_rc" -eq 0 ]]; then
    chmod a+r /etc/apt/keyrings/docker.gpg
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
      "$ARCH" "$DISTRO_ID" "$CODENAME" > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    if apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
      installed=1
    fi
  fi
  set -e

  if [[ "$installed" -eq 0 ]]; then
    warn "Docker 官方仓库安装失败，尝试 Ubuntu 官方软件包。"
    set +e
    apt-get install -y --no-install-recommends docker.io docker-compose-v2
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
      installed=1
    fi
  fi

  if [[ "$installed" -eq 0 ]]; then
    warn "尝试使用 Docker 官方便捷安装脚本。"
    GET_DOCKER_SCRIPT="${TMP_DIR}/get-docker.sh"
    if curl --http1.1 --retry 5 --retry-all-errors -fsSL \
      https://get.docker.com -o "$GET_DOCKER_SCRIPT" \
      && [[ -f "$GET_DOCKER_SCRIPT" && ! -L "$GET_DOCKER_SCRIPT" && -s "$GET_DOCKER_SCRIPT" ]]; then
      chmod 700 "$GET_DOCKER_SCRIPT"
      if sh "$GET_DOCKER_SCRIPT"; then
        installed=1
      else
        warn "Docker 官方便捷安装脚本执行失败。"
      fi
    fi
  fi

  if [[ "$installed" -eq 0 ]] || ! docker_compose_ok; then
    die "Docker 安装失败。请检查网络后重试，或参考 https://docs.docker.com/engine/install/ubuntu/ 手动安装。"
  fi

  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now docker >/dev/null 2>&1 || true
  elif command -v service >/dev/null 2>&1; then
    service docker start >/dev/null 2>&1 || true
  fi
fi

if [[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]]; then
  if ! id "$TARGET_USER" >/dev/null 2>&1; then
    warn "找不到用户 $TARGET_USER，未加入 docker 组。"
  elif id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx docker; then
    log "用户 $TARGET_USER 已在 docker 组中。"
  else
    usermod -aG docker "$TARGET_USER" || warn "无法将 $TARGET_USER 加入 docker 组。"
    log "已将 $TARGET_USER 加入 docker 组。注销并重新登录后，后续使用可不再输入 sudo 密码。"
  fi
fi

# 简单探测；无 systemd 的 WSL 环境可能需要手动启动 dockerd。
if ! docker info >/dev/null 2>&1; then
  warn "Docker 守护进程当前未运行。程序启动时会再检测；WSL 用户请在 Docker Desktop 中开启 WSL Integration。"
fi
log "Docker 安装流程结束。"
