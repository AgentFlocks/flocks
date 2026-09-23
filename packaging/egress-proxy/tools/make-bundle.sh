#!/usr/bin/env bash
# 打交付包（机器 A 侧：setup-a + 引擎二进制 + 文档；tests/ tools/ 不进包）。交付目标是 x86_64：
#   tools/make-bundle.sh              # → dist/flocks-egress-proxy-<版本>-linux-x86_64.tar.gz + 同名 .run 自解压单文件（给客户的就是这个）
#   tools/make-bundle.sh --arch arm64 # → dist/dev-arm64/…-linux-arm64.{tar.gz,run}，只给 Apple Silicon 开发机上的容器测试用，不交付
# 文件名用 x86_64（客户 uname -m 看到的就是它），引擎二进制在 vendor/ 里仍按 Go 的叫法 mihomo-linux-amd64
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH=amd64
while [ $# -gt 0 ]; do case "$1" in --arch) ARCH="$2"; shift 2 ;; *) echo "未知参数 $1" >&2; exit 1 ;; esac; done
case "$ARCH" in amd64) LABEL=x86_64; DIST="$HERE/dist" ;; arm64) LABEL=arm64; DIST="$HERE/dist/dev-arm64" ;; *) echo "不支持的架构 $ARCH（amd64 | arm64）" >&2; exit 1 ;; esac
VERSION="$(cat "$HERE/VERSION")"
grep -q "^VERSION = '$VERSION'$" "$HERE/setup-a.py" || { echo "VERSION 文件（$VERSION）和 setup-a.py 里的 VERSION 不一致" >&2; exit 1; }
NAME="flocks-egress-proxy-$VERSION-linux-$LABEL"
STAGE="$DIST/$NAME"

[ -f "$HERE/vendor/mihomo-linux-$ARCH" ] || "$HERE/tools/fetch-mihomo.sh" --arch "$ARCH"
[ -f "$HERE/README.pdf" ] || echo "提示: 没有 README.pdf，交付包里只有 README.md" >&2

mkdir -p "$DIST"
# 清掉上一次的同名 staging：只删我们自己放进去的那几个文件，目录里有别的东西就停下来让人看
STAGE_FILES="VERSION README.md README.pdf THIRD_PARTY_NOTICES.md THIRD_PARTY_NOTICES.pdf egress.conf.example setup-a.sh setup-a.py SHA256SUMS vendor/mihomo-linux-$ARCH vendor/SHA256SUMS"
if [ -d "$STAGE" ]; then
    for f in $STAGE_FILES; do rm -f "$STAGE/$f"; done
    rmdir "$STAGE/vendor" "$STAGE" 2>/dev/null || { echo "$STAGE 里有不是本脚本放的文件，请先手工清理" >&2; exit 1; }
fi
mkdir -p "$STAGE/vendor"
cp "$HERE/VERSION" "$HERE/README.md" "$HERE/THIRD_PARTY_NOTICES.md" "$HERE/egress.conf.example" "$HERE/setup-a.sh" "$HERE/setup-a.py" "$STAGE/"
[ -f "$HERE/README.pdf" ] && cp "$HERE/README.pdf" "$STAGE/"
[ -f "$HERE/THIRD_PARTY_NOTICES.pdf" ] && cp "$HERE/THIRD_PARTY_NOTICES.pdf" "$STAGE/"
cp "$HERE/vendor/mihomo-linux-$ARCH" "$STAGE/vendor/"
grep " mihomo-linux-$ARCH\$" "$HERE/vendor/SHA256SUMS" > "$STAGE/vendor/SHA256SUMS"
chmod 0755 "$STAGE/setup-a.sh" "$STAGE/setup-a.py" "$STAGE/vendor/mihomo-linux-$ARCH"

sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }
( cd "$STAGE" && find . -type f ! -name SHA256SUMS | sort | while read -r f; do printf '%s  %s\n' "$(sha256 "$f")" "${f#./}"; done ) > "$STAGE/SHA256SUMS"

TARBALL="$DIST/$NAME.tar.gz"
# 不带 macOS 的 xattr / 资源分叉，否则客户机上 GNU tar 会刷屏 "Ignoring unknown extended header keyword"
export COPYFILE_DISABLE=1
if tar --version 2>/dev/null | grep -q GNU; then
    tar -C "$DIST" --owner=0 --group=0 --numeric-owner --no-xattrs -czf "$TARBALL" "$NAME"
else
    tar -C "$DIST" --uid 0 --gid 0 --numeric-owner --no-xattrs --no-mac-metadata -czf "$TARBALL" "$NAME"
fi
printf '%s  %s\n' "$(sha256 "$TARBALL")" "$(basename "$TARBALL")" > "$TARBALL.sha256"

# 自解压单文件：sudo bash xxx.run [--proxy URL ...]  —— 头部是一段 shell，__ARCHIVE_BELOW__ 之后就是上面的 tar.gz
RUNFILE="$DIST/$NAME.run"
sed "s/@NAME@/$NAME/g; s/@VERSION@/$VERSION/g; s/@ARCH@/$LABEL/g" > "$RUNFILE" <<'HEADER'
#!/usr/bin/env bash
# flocks-egress 自解压安装包 v@VERSION@（@ARCH@）。机器 A 上：
#   sudo bash @NAME@.run                    # 用机器上已有的代理设置（dnf.conf / 环境变量），没有就问一遍地址
#   sudo bash @NAME@.run 10.0.0.5:3128      # 直接给代理地址（IP:端口 = HTTP 代理；也可以 http:// https:// socks5://）
#   sudo bash @NAME@.run --proxy http://<代理IP>:<端口> --proxy-user u --proxy-password p
#   sudo bash @NAME@.run check | status | rollback
# 只解压到临时目录、跑完就清掉；装好后的日常命令是 sudo flocks-egress ...
set -euo pipefail
NAME="@NAME@"
case "${1:-}" in
    -h|--help|help) echo "用法: sudo bash $0 [代理地址] [--proxy-user u --proxy-password p] | check | status | reconfigure | rollback"; exit 0 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "请用 sudo 运行: sudo bash $0" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo '缺少 python3（CentOS / RHEL 9 自带）' >&2; exit 1; }
command -v tar >/dev/null 2>&1 || { echo '缺少 tar' >&2; exit 1; }
line="$(awk '/^__ARCHIVE_BELOW__$/ { print NR + 1; exit }' "$0")"
# 解压到 /opt 下（不用 /tmp：常见的 noexec 挂载会让引擎自检跑不起来，tmpfs 也可能装不下 60 MB）
tmp="$(mktemp -d /opt/.flocks-egress-run.XXXXXX 2>/dev/null || mktemp -d /var/tmp/flocks-egress-run.XXXXXX)"
cleanup() {
    # 只删自己解出来的那些文件，再删空目录
    [ -f "$tmp/.list" ] && while IFS= read -r f; do [ -f "$tmp/$f" ] && rm -f "$tmp/$f"; done < "$tmp/.list"
    [ -f "$tmp/.list" ] && sort -r "$tmp/.list" | while IFS= read -r d; do [ -d "$tmp/$d" ] && rmdir "$tmp/$d" 2>/dev/null; done
    rm -f "$tmp/.list"; rmdir "$tmp" 2>/dev/null || true
}
trap cleanup EXIT
tail -n +"$line" "$0" | tar -tzf - > "$tmp/.list"
tail -n +"$line" "$0" | tar -xzf - -C "$tmp"
( cd "$tmp/$NAME" && sha256sum -c --quiet SHA256SUMS ) || { echo '安装包校验失败，文件可能损坏' >&2; exit 1; }
# 第一个参数不是子命令（没给、代理地址、--proxy 之类的选项）就都当 install
args=("$@")
case "${1:-}" in
    install|reconfigure|check|status|rollback|uninstall|_rules-reload|--version) ;;
    *) args=(install "$@") ;;
esac
bash "$tmp/$NAME/setup-a.sh" "${args[@]}"
exit $?
__ARCHIVE_BELOW__
HEADER
cat "$TARBALL" >> "$RUNFILE"
chmod 0755 "$RUNFILE"
printf '%s  %s\n' "$(sha256 "$RUNFILE")" "$(basename "$RUNFILE")" > "$RUNFILE.sha256"

[ "$ARCH" = amd64 ] || echo "提示: $LABEL 包只用于开发机测试，交付给客户的是 x86_64 那份" >&2
echo "交付包: $TARBALL ($(du -h "$TARBALL" | cut -f1))"; cat "$TARBALL.sha256"
echo "单文件: $RUNFILE ($(du -h "$RUNFILE" | cut -f1))"; cat "$RUNFILE.sha256"
echo "内容:"; tar -tzf "$TARBALL" | sed 's/^/  /'
