"""Build the standalone Bash entry from the tested update engine."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

HEADER = r'''#!/usr/bin/env bash
# Single-file updater: Bash downloads; embedded Python validates and deploys.
set -euo pipefail

flocks_update_url="https://gitee.com/flocks/flocks/repository/archive/fix/process_11.12_en"
flocks_update_archive=""
flocks_update_directory="$HOME/.flocks/customizations"
flocks_update_workdir=""
flocks_update_expected=""
flocks_update_help=0

flocks_update_cleanup() {
    if [[ -n "$flocks_update_workdir" ]]; then
        rm -rf -- "$flocks_update_workdir"
    fi
}
trap flocks_update_cleanup EXIT

for flocks_update_arg in "$@"; do
    if [[ -n "$flocks_update_expected" ]]; then
        case "$flocks_update_expected" in
            url) flocks_update_url="$flocks_update_arg" ;;
            archive) flocks_update_archive="$flocks_update_arg" ;;
            directory) flocks_update_directory="$flocks_update_arg" ;;
        esac
        flocks_update_expected=""
        continue
    fi
    case "$flocks_update_arg" in
        --archive-url) flocks_update_expected=url ;;
        --archive-url=*) flocks_update_url="${flocks_update_arg#*=}" ;;
        --repository-archive) flocks_update_expected=archive ;;
        --repository-archive=*) flocks_update_archive="${flocks_update_arg#*=}" ;;
        --customizations-root) flocks_update_expected=directory ;;
        --customizations-root=*) flocks_update_directory="${flocks_update_arg#*=}" ;;
        --pro-root|--webui-host|--webui-port) flocks_update_expected=value ;;
        --pro-root=*|--webui-host=*|--webui-port=*) ;;
        --help|-h) flocks_update_help=1 ;;
        *) printf 'ERROR: 不支持的参数：%s\n' "$flocks_update_arg" >&2; exit 2 ;;
    esac
done
if [[ -n "$flocks_update_expected" ]]; then
    printf 'ERROR: 参数缺少值，请运行 bash update-flocks.sh --help。\n' >&2
    exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
    printf 'ERROR: 找不到 python3，请使用已有 Python 3 的 Pro 安装账号运行。\n' >&2
    exit 1
fi
if [[ "$flocks_update_help" -eq 0 && -z "$flocks_update_archive" ]]; then
    if ! command -v curl >/dev/null 2>&1; then
        printf 'ERROR: 找不到 curl，服务尚未停止。\n' >&2
        exit 1
    fi
    case "$flocks_update_url" in
        https://*) ;;
        *) printf 'ERROR: 下载地址必须使用 HTTPS，服务保持原样。\n' >&2; exit 1 ;;
    esac
    mkdir -p -- "$flocks_update_directory"
    flocks_update_workdir="$(mktemp -d "$flocks_update_directory/.update-download.XXXXXX")"
    printf '[flocks-update] 正在下载 Gitee 补丁分支……\n'
    curl --fail --location --silent --show-error --retry 2 \
        --connect-timeout 20 --max-time 300 --max-filesize 1073741824 \
        --proto '=https' --proto-redir '=https' \
        --output "$flocks_update_workdir/repository.zip" "$flocks_update_url"
    set -- "$@" --repository-archive "$flocks_update_workdir/repository.zip"
fi

# No separate .py file is required. Python 3 handles ZIP/JSON and the existing
# Pro installer protocol; Pro's original managed entry remains in use.
python3 - "$@" <<'FLOCKS_UPDATER_PY'
'''


def build(root=ROOT):
    source = (root/'update-flocks.py').read_text(encoding='utf-8')
    engine, separator, _ = source.rpartition("\nif __name__ == '__main__':\n")
    if not separator or '\nFLOCKS_UPDATER_PY\n' in source:
        raise ValueError('Updater source does not have the expected entry point')
    script = HEADER + engine + "\nsys.argv[0] = 'update-flocks.sh'\nraise SystemExit(main())\nFLOCKS_UPDATER_PY\n"
    path = root/'update-flocks.sh'
    path.write_text(script, encoding='utf-8')
    path.chmod(0o755)
    return path


if __name__ == '__main__':
    print(build())
