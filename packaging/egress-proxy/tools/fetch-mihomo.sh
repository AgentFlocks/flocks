#!/usr/bin/env bash
# 在有外网的打包机上下载固定版本的 mihomo 二进制到 vendor/，并校验 sha256。
#   tools/fetch-mihomo.sh              # 下载 amd64（交付目标）
#   tools/fetch-mihomo.sh --arch arm64 # 下载 arm64（本地 ARM 机器测试用）
#   tools/fetch-mihomo.sh --arch all
# 可用 MIHOMO_DOWNLOAD_BASE 指到 GitHub 镜像，例如
#   MIHOMO_DOWNLOAD_BASE=https://ghfast.top/https://github.com/MetaCubeX/mihomo/releases/download
set -euo pipefail

MIHOMO_VERSION="v1.19.31"
BASE="${MIHOMO_DOWNLOAD_BASE:-https://github.com/MetaCubeX/mihomo/releases/download}"
# 压缩包 sha256（2026-09-18 从 GitHub Release 下载后记录）。amd64 用 compatible 构建（GOAMD64=v1，老 CPU / 虚拟机也能跑）
gz_name() { case "$1" in amd64) echo "mihomo-linux-amd64-compatible-${MIHOMO_VERSION}.gz" ;; arm64) echo "mihomo-linux-arm64-${MIHOMO_VERSION}.gz" ;; esac; }
gz_sha()  { case "$1" in amd64) echo "04cf9f09671704f839ddbee2e93069dc831a4123a75281e725d1d96ab9ac1afc" ;; arm64) echo "9e0f11afbf38426b8bd88fdc594678f8161c57eccb4e1b77acb12b493904f1d4" ;; esac; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$HERE/vendor"
ARCHES="amd64"
while [ $# -gt 0 ]; do
    case "$1" in
        --arch) case "$2" in all) ARCHES="amd64 arm64" ;; amd64|arm64) ARCHES="$2" ;; *) echo "不支持的架构 $2" >&2; exit 1 ;; esac; shift 2 ;;
        *) echo "未知参数 $1" >&2; exit 1 ;;
    esac
done

sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }

mkdir -p "$VENDOR"
for arch in $ARCHES; do
    gz="$(gz_name "$arch")"; want="$(gz_sha "$arch")"; url="$BASE/$MIHOMO_VERSION/$gz"; out="$VENDOR/mihomo-linux-$arch"
    if [ ! -f "$VENDOR/$gz" ] || [ "$(sha256 "$VENDOR/$gz")" != "$want" ]; then
        echo "下载 $url"
        curl -fSL --retry 3 --retry-delay 3 -o "$VENDOR/$gz.part" "$url"
        mv "$VENDOR/$gz.part" "$VENDOR/$gz"
    fi
    got="$(sha256 "$VENDOR/$gz")"
    [ "$got" = "$want" ] || { echo "sha256 不匹配: $gz  期望 $want  实际 $got" >&2; exit 1; }
    gunzip -c "$VENDOR/$gz" > "$out.part" && mv "$out.part" "$out" && chmod 0755 "$out"
    echo "OK  $out  ($(du -h "$out" | cut -f1))"
done

# 记录解压后二进制的校验和，setup-a.py 安装前会核对
: > "$VENDOR/SHA256SUMS"
for f in "$VENDOR"/mihomo-linux-*; do
    case "$f" in *.gz|*.part) continue ;; esac
    [ -f "$f" ] || continue
    printf '%s  %s\n' "$(sha256 "$f")" "$(basename "$f")" >> "$VENDOR/SHA256SUMS"
done
echo "已写入 $VENDOR/SHA256SUMS:"; cat "$VENDOR/SHA256SUMS"
