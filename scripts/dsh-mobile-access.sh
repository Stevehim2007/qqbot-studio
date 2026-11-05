#!/usr/bin/env bash
# Start a LAN-accessible DSH Web instance for a phone on the same trusted network.
# This intentionally does NOT expose a wildcard address or a public tunnel.
set -Eeuo pipefail

PORT="${DSH_MOBILE_PORT:-3081}"
PROFILE="${DSH_MOBILE_PROFILE:-web}"
HOST="${DSH_MOBILE_HOST:-}"
PUBLIC_URL="${DSH_MOBILE_PUBLIC_URL:-}"
CERT="${DSH_MOBILE_TLS_CERT:-}"
KEY="${DSH_MOBILE_TLS_KEY:-}"
DRY_RUN="${DSH_MOBILE_DRY_RUN:-0}"

if [[ -z "$HOST" ]]; then
  HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
if [[ -z "$HOST" ]]; then
  echo "无法自动发现 LAN IPv4；请设置 DSH_MOBILE_HOST=<本机LAN IP>" >&2
  exit 2
fi
if [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" ]]; then
  echo "拒绝绑定通配地址；请指定一个具体的本机 LAN IP" >&2
  exit 2
fi
if [[ "$HOST" == *:* ]]; then
  echo "当前脚本只接受 IPv4；IPv6 请手动按 DSH README 的 --host/--public-url 参数启动" >&2
  exit 2
fi
if ! command -v dsh >/dev/null 2>&1; then
  echo "未找到 dsh 命令" >&2
  exit 2
fi
if [[ -n "$CERT" && -z "$KEY" ]] || [[ -z "$CERT" && -n "$KEY" ]]; then
  echo "DSH_MOBILE_TLS_CERT 与 DSH_MOBILE_TLS_KEY 必须同时设置" >&2
  exit 2
fi

SCHEME="http"
if [[ -n "$CERT" ]]; then SCHEME="https"; fi
if [[ -z "$PUBLIC_URL" ]]; then
  PUBLIC_URL="${SCHEME}://${HOST}:${PORT}/"
fi

ARGS=(--profile "$PROFILE" --host "$HOST" --port "$PORT" --trusted-host "$HOST" --public-url "$PUBLIC_URL" --no-open)
if [[ -n "$CERT" ]]; then
  ARGS+=(--tls-cert "$CERT" --tls-key "$KEY")
fi

cat <<EOF
DSH mobile LAN access
  bind:        ${SCHEME}://${HOST}:${PORT}/
  public URL:  ${PUBLIC_URL}
  profile:     ${PROFILE}
  safety:      only a concrete LAN IP; no wildcard bind; no public tunnel
  手机:        同一可信 Wi-Fi/VPN 下打开上面的 URL；首次使用 DSH 启动时打印的带 token URL
  停止:        Ctrl-C
EOF
printf 'command: dsh'; printf ' %q' "${ARGS[@]}"; printf '\n'

if [[ "$DRY_RUN" == "1" ]]; then
  exit 0
fi
exec dsh "${ARGS[@]}"
