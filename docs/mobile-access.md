# Android 访问 Linux 上的 DSH

目标：让 Android 手机通过同一可信 LAN/VPN 打开 Linux 主机上的 DSH Web GUI。不是公网穿透，不开放 `0.0.0.0`。

## 前置

```text
Linux 和 Android 在同一 Wi-Fi / 同一 VPN
Linux 防火墙允许一个明确的 TCP 端口（默认 3081）
DSH 已安装并已配置 profile
```

## 启动

```bash
cd /path/to/project
./scripts/dsh-mobile-access.sh
```

或指定地址和端口：

```bash
DSH_MOBILE_HOST=192.168.1.20 \
DSH_MOBILE_PORT=3081 \
./scripts/dsh-mobile-access.sh
```

脚本会打印：

```text
bind: http://<LAN-IP>:<port>/
public URL: ...
手机: 同一可信 Wi-Fi/VPN 下打开该 URL
```

DSH 启动时会打印带一次性 token 的认证 URL。手机首次打开该 URL，完成 token 交换后使用正常会话。

## HTTPS（可选）

如果有证书：

```bash
DSH_MOBILE_HOST=192.168.1.20 \
DSH_MOBILE_PORT=3081 \
DSH_MOBILE_TLS_CERT=/path/fullchain.pem \
DSH_MOBILE_TLS_KEY=/path/privkey.pem \
./scripts/dsh-mobile-access.sh
```

证书必须匹配手机访问的 host，且手机信任该证书。不要使用自签名证书冒充公网 HTTPS。

## 安全边界

- 只绑定具体 LAN IPv4，不使用 `0.0.0.0`；
- 不自动修改防火墙；
- 不做公网反向代理、内网穿透或端口转发；
- 不要将带 token 的启动 URL 发到群里；
- 不在不可信 Wi-Fi 上开启；
- 不需要时用 `Ctrl-C` 停止。

## Android 使用

1. 手机浏览器打开脚本打印的 URL；
2. 首次使用 DSH 启动时打印的带 token URL；
3. 登录后用浏览器「添加到主屏幕」，作为 PWA 使用；
4. 若页面打不开，先检查：
   - 手机和 Linux 是否同一网络；
   - `ss -ltnp | grep 3081` 是否监听在 LAN IP；
   - Linux 防火墙是否放行该 TCP 端口；
   - 是否误用了 `127.0.0.1`。

## 当前环境说明

当前开发执行环境中的 `/home/stevehim/.dsh` 是只读挂载；脚本只做参数和命令准备，不能在当前环境直接重启 DSH。请在实际运行 DSH 的 Linux 主机上执行。
