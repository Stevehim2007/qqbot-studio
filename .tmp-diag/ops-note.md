NapCat 掉线恢复（bot-11 / QQ 871247845）
1) 看状态：docker ps | grep napcat；容器内 ps -eo cmd | grep /opt/QQ/qq
2) 删陈旧码：docker exec qqbot-bot-11-napcat rm -f /app/napcat/cache/qrcode.png
3) 重启：docker restart qqbot-bot-11-napcat
4) 取码：POST /api/auth/login {hash: sha256(token + ".napcat")} → Credential
        POST /api/QQLogin/GetQQLoginQrcode -H "Authorization: Bearer <Credential>"
        或直接看 /app/napcat/cache/qrcode.png
5) 判登录：POST /api/QQLogin/CheckLoginStatus → isLogin=true / loginPhase=ready
