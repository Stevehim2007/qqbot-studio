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
# Git 也是自动更新和可选 AI 助手 B 的依赖。
if ! command -v git >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y git
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

# 可选：安装独立的生成式 AI 助手 B。A 离线助手始终保留，B 默认不安装。
STUDIO_DATA_DIR="${QQBOT_STUDIO_DATA_DIR:-$HOME/.local/share/qqbot-studio}"
AI_ASSISTANT_REPO="https://github.com/Stevehim2007/qqbot-studio-ai-assistant.git"
AI_ASSISTANT_DIR="$STUDIO_DATA_DIR/extensions/ai-assistant"

optional_ai_assistant() {
  local choice="${QQBOT_INSTALL_AI_ASSISTANT:-ask}"
  local answer=""
  local update_existing=0

  if [[ -e "$AI_ASSISTANT_DIR" && ! -d "$AI_ASSISTANT_DIR/.git" ]]; then
    echo "提示：$AI_ASSISTANT_DIR 已存在但不是 Git 仓库，未修改该目录。"
    echo "如需重新安装，请先手动备份并移走该目录后重试。"
    return 0
  fi

  if [[ -d "$AI_ASSISTANT_DIR/.git" ]]; then
    if [[ "$choice" =~ ^(0|no|n|false)$ ]]; then
      echo "已跳过 AI 助手 B 更新；B 不会自动启动。"
      return 0
    elif [[ "$choice" =~ ^(1|yes|y|true)$ ]]; then
      update_existing=1
    elif [[ ! -t 0 ]]; then
      echo "非交互安装：AI 助手 B 已存在，默认跳过更新。"
      return 0
    else
      echo
      echo "检测到可选 AI 助手 B 已安装：$AI_ASSISTANT_DIR"
      read -r -p "是否检查并快进更新 AI 助手 B（会联网访问 GitHub）？[y/N] " answer || answer=""
      if [[ "$answer" =~ ^[Yy]$ ]]; then
        update_existing=1
      fi
    fi

    if [[ "$update_existing" -eq 1 ]]; then
      if [[ -n "$(git -C "$AI_ASSISTANT_DIR" status --porcelain 2>/dev/null || true)" ]]; then
        echo "警告：AI 助手 B 工作区有未提交修改，已跳过更新；主项目继续安装。"
        return 0
      fi
      echo "正在更新 AI 助手 B（仅允许快进）…"
      if git -C "$AI_ASSISTANT_DIR" fetch origin --prune && \
         git -C "$AI_ASSISTANT_DIR" pull --ff-only; then
        echo "AI 助手 B 已更新。主项目不会自动启动它。"
      else
        echo "警告：AI 助手 B 更新失败，已保留原版本；主项目继续安装。"
        echo "可稍后重试：git -C \"$AI_ASSISTANT_DIR\" pull --ff-only"
      fi
    fi
    return 0
  fi

  case "$choice" in
    0|no|n|false)
      echo "已跳过可选 AI 助手 B；A 离线助手继续默认可用。"
      return 0
      ;;
    1|yes|y|true)
      answer="y"
      ;;
    *)
      if [[ ! -t 0 ]]; then
        echo "非交互安装：默认不安装可选 AI 助手 B（A 离线助手仍可用）。"
        return 0
      fi
      echo
      echo "可选：生成式 AI 助手 B 使用你自己的 DeepSeek API Key，需联网 clone 独立仓库。"
      echo "它会安装到：$AI_ASSISTANT_DIR"
      echo "A 离线助手与此无关，始终默认保留。"
      read -r -p "是否现在下载 AI 助手 B？默认不安装 [y/N] " answer || answer=""
      ;;
  esac

  if [[ ! "$answer" =~ ^[Yy]$ ]]; then
    echo "未安装 AI 助手 B；A 离线助手继续默认可用。"
    return 0
  fi

  mkdir -p "$STUDIO_DATA_DIR/extensions"
  chmod 700 "$STUDIO_DATA_DIR" "$STUDIO_DATA_DIR/extensions" 2>/dev/null || true
  local tmp_dir="${AI_ASSISTANT_DIR}.installing.$$"
  rm -rf -- "$tmp_dir"
  echo "正在下载 AI 助手 B（固定官方仓库，不读取或传递 API Key）…"
  if git clone --depth 1 "$AI_ASSISTANT_REPO" "$tmp_dir" && \
     [[ -f "$tmp_dir/assistant.py" && -f "$tmp_dir/README.md" ]] && \
     mv "$tmp_dir" "$AI_ASSISTANT_DIR"; then
    echo "AI 助手 B 已安装到：$AI_ASSISTANT_DIR"
    echo "主项目不会自动启动 B。请阅读 README，并手动运行："
    echo "  cd \"$AI_ASSISTANT_DIR\" && ./install.sh"
  else
    rm -rf -- "$tmp_dir"
    echo "警告：AI 助手 B 下载失败；主项目安装不受影响。"
    echo "可检查网络后重试："
    echo "  git clone --depth 1 \"$AI_ASSISTANT_REPO\" \"$AI_ASSISTANT_DIR\""
  fi
  return 0
}

optional_ai_assistant

# 可选：创建桌面入口（服务器上失败不影响启动）。
if [[ -x "$ROOT_DIR/scripts/install-desktop-entry.sh" ]]; then
  bash "$ROOT_DIR/scripts/install-desktop-entry.sh" "$ROOT_DIR" >/dev/null 2>&1 || true
fi

# 安装完成后自动启动 start。
exec bash "$ROOT_DIR/start.sh" "$@"
