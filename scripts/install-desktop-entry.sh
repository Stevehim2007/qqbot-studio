#!/usr/bin/env bash
# Install a clickable desktop entry for the current user.
set -Eeuo pipefail
ROOT_DIR="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
ROOT_DIR="$(cd -- "$ROOT_DIR" && pwd)"
APPS_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${APPS_DIR}/qqbot-studio.desktop"
mkdir -p "$APPS_DIR"
chmod +x "$ROOT_DIR/start.sh" 2>/dev/null || true

# Desktop Entry Exec= arguments containing spaces must be quoted.  Escape
# backslashes, double quotes and percent signs as required by the spec.
EXEC_PATH="${ROOT_DIR}/console.sh"
EXEC_ESCAPED="${EXEC_PATH//\\/\\\\}"
EXEC_ESCAPED="${EXEC_ESCAPED//\"/\\\"}"
EXEC_ESCAPED="${EXEC_ESCAPED//%/%%}"

cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=QQ 机器人控制台
Name[en]=QQ Bot Studio
Comment=管理 AstrBot + SnowLuma + DeepSeek QQ 机器人实例
Comment[en]=Manage QQ Bot Studio instances
Exec="${EXEC_ESCAPED}"
Path=${ROOT_DIR}
Icon=${ROOT_DIR}/assets/qqbot-studio.svg
Terminal=true
Categories=Network;Utility;Development;
Keywords=QQ;AstrBot;SnowLuma;OneBot;DeepSeek;Bot;
StartupNotify=true
DESKTOP
chmod +x "$DESKTOP_FILE"
echo "已安装桌面启动项：$DESKTOP_FILE"
