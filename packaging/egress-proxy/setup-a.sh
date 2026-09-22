#!/usr/bin/env bash
# flocks-egress 入口：检查 root 与 python3 后转给同目录的 setup-a.py。
#   sudo bash setup-a.sh install [代理地址] [--proxy-user 用户 --proxy-password 密码]   # 地址可以只写 IP:端口；不给就找机器上现成的，再没有就问
#   sudo bash setup-a.sh install --config ./egress.conf
#   bash setup-a.sh --help
set -euo pipefail
case "${1:-}" in
    help) set -- --help ;;
    -h|--help|--version|'') ;;
    *) if [ "$(id -u)" -ne 0 ]; then
           echo '请用 sudo 运行: sudo bash setup-a.sh install [代理地址]' >&2; exit 1
       fi ;;
esac
if ! command -v python3 >/dev/null 2>&1; then
    echo '缺少 python3（CentOS / RHEL 9 自带；若被卸载: dnf install -y python3）' >&2; exit 1
fi
script_dir="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
exec python3 "$script_dir/setup-a.py" "$@"
