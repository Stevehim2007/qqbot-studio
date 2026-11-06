#!/usr/bin/env bash
# B 方案：AstrBot 容器以 root 写配置后，把插件配置文件交还给宿主机 Studio 用户。
# 在插件安装后、AstrBot 重启后运行；不改变容器运行方式。
set -Eeuo pipefail

CONTAINER="${ASTRBOT_CONTAINER:-qqbot-bot-1-astrbot}"
HOST_UID="${HOST_UID:-1000}"
HOST_GID="${HOST_GID:-1001}"

if ! command -v docker >/dev/null 2>&1; then
  echo "未找到 docker" >&2
  exit 2
fi
if ! sudo docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "找不到容器：$CONTAINER" >&2
  exit 2
fi

sudo docker exec \
  -e HOST_UID="$HOST_UID" \
  -e HOST_GID="$HOST_GID" \
  "$CONTAINER" sh -lc '
    set -eu
    find /AstrBot/data/config -maxdepth 1 -type f -name "qqbot_*_config.json" \
      | while IFS= read -r file; do
          chown "$HOST_UID:$HOST_GID" "$file"
          chmod 600 "$file"
        done
  '
echo "已修复 $CONTAINER 的 qqbot_* 插件配置权限：$HOST_UID:$HOST_GID 0600"
