#!/usr/bin/env bash
# e2e 用的测试夹具：在「机器 B」容器里用包里的 mihomo 起一个带认证的 HTTP + SOCKS5 代理（mixed 端口），
# 扮演客户现成的 SOCKS5 代理，配合 squid 覆盖 socks5:// 这种写法。只在测试里用；客户 B 上什么都不装。
#   socks5-proxy-on-b.sh <mihomo 二进制> <端口> <用户> <密码>
# 起的是 systemd 单元 fe-socks5，e2e 用 journalctl -u fe-socks5 数「经它出去的连接」。
set -euo pipefail
bin="$1"; port="$2"; user="$3"; pass="$4"
install -d -m 0755 /opt/fe-socks5
install -m 0755 "$bin" /opt/fe-socks5/mihomo
cat > /opt/fe-socks5/config.yaml <<YAML
mixed-port: $port
allow-lan: true
bind-address: "*"
mode: direct
log-level: info
ipv6: false
geodata-mode: false
geo-auto-update: false
find-process-mode: "off"
profile:
  store-selected: false
dns:
  enable: false
authentication:
  - "$user:$pass"
YAML
cat > /etc/systemd/system/fe-socks5.service <<UNIT
[Unit]
Description=e2e fixture: HTTP+SOCKS5 proxy on machine B (mihomo)
After=network-online.target
[Service]
ExecStart=/opt/fe-socks5/mihomo -d /opt/fe-socks5 -f /opt/fe-socks5/config.yaml
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now fe-socks5.service
! systemctl is-active --quiet firewalld || firewall-cmd --add-port="$port/tcp" >/dev/null
for _ in $(seq 1 15); do ss -ltn "sport = :$port" | grep -q LISTEN && break; sleep 1; done
systemctl is-active --quiet fe-socks5.service || { journalctl -u fe-socks5 --no-pager -n 20; echo "fe-socks5 没起来" >&2; exit 1; }
# 本机经它走一次，确认账号和出网都通
curl -sS -m 15 -x "socks5://127.0.0.1:$port" --proxy-user "$user:$pass" -o /dev/null -w "fe-socks5 就绪：0.0.0.0:$port（HTTP+SOCKS5，认证），自检 http=%{http_code}\n" https://www.baidu.com/
