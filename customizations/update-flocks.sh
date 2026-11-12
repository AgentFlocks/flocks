#!/usr/bin/env bash
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
#!/usr/bin/env python3
"""Download and apply the newest compatible Pro customization from Gitee."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

DEFAULT_URL = 'https://gitee.com/flocks/flocks/repository/archive/fix/process_11.12_en'
MAX_ARCHIVE = 1024 * 1024 * 1024
METADATA_FILES = ('apply.py', 'apply.sh', 'target-profile.json', 'payload-manifest.json',
                  'README.md', 'verification.json', 'pg-verification-summary.json', 'update-flocks.py')

# The initial 1.14.0 package predates staged installers. Use its original
# verification functions for first install/repeat; upgrades use protocol 2.
LEGACY_PREFLIGHT = '''
import importlib.util,json,sys
from pathlib import Path
package,root,storage=map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location("legacy_enhancement",package/"apply.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.PACKAGE=package
profile=m.load(package/"target-profile.json")
payload=m.load(package/"payload-manifest.json")
root,release,manifest=m.target(root,profile)
runtime=release/"runtime"
state_path=storage/"state"/(profile["releaseId"]+".json")
state=m.load(state_path) if state_path.is_file() else None
if state and state["release"]!=str(release):
    raise m.Refusal("Existing enhancement belongs to another installation")
if state and state["status"]=="prepared":
    raise m.Refusal("Previous installation was interrupted; recover before updating")
if state and state["status"]=="applied":
    m.matches(runtime,state["files"])
    if state["packageVersion"]!=profile["packageVersion"] or state.get("payloadManifestSha256")!=m.digest(package/"payload-manifest.json"):
        raise m.Refusal("This mirror package predates automatic upgrades; wait for the compatible updated package")
    action="already-applied"
else:
    m.check_pair(runtime,profile)
    m.plan(runtime,manifest,payload)
    action="apply"
print(json.dumps({"action":action,"legacy":True}))
'''


class UpdateError(RuntimeError):
    pass


def say(message):
    print(message, flush=True)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def version(value):
    if not re.fullmatch(r'\d+(?:\.\d+)*', str(value)):
        raise UpdateError(f'不支持的补丁版本格式：{value}')
    return tuple(int(part) for part in value.split('.'))


def safe_name(name):
    path = PurePosixPath(name.rstrip('/'))
    if not str(path) or path.is_absolute() or '..' in path.parts or '\\' in name:
        raise UpdateError(f'压缩包路径无效：{name}')
    return path


def wrapper_installation(path):
    """Read literal arguments; never execute or evaluate a shell wrapper."""
    try:
        if path.stat().st_size > 16384:
            return None
        words = shlex.split(path.read_text(encoding='utf-8'), comments=True)
        if 'flocks' not in words or '-install-root' not in words:
            return None
        root = words[words.index('-install-root') + 1]
        if not Path(root).is_absolute() or '$' in root or '`' in root:
            return None
        return Path(root).resolve(), ['-user'] if '-user' in words else []
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


def find_installation(explicit=None):
    wrappers = [Path.home()/'.local/bin/flocks']
    if shutil.which('flocks'):
        wrappers.insert(0, Path(shutil.which('flocks')))
    parsed = [item for p in wrappers if (item := wrapper_installation(p))]
    if explicit is not None:
        root = explicit.expanduser().resolve()
        matching = next((flags for candidate, flags in parsed if candidate == root), None)
        flags = matching if matching is not None else []
    elif parsed:
        roots = {p for p, _ in parsed}
        if len(roots) != 1:
            raise UpdateError('发现多个 Pro 安装，请使用 --pro-root 指定实际运行目录。')
        root, flags = parsed[0]
        matching = flags
    else:
        roots = [p.resolve() for p in (Path.home()/'flockspro', Path.home()/'.flocks/flockspro', Path('/opt/flockspro'))
                 if (p/'current/release.json').is_file()]
        if len(set(roots)) != 1:
            raise UpdateError('无法唯一确认 Pro 安装，请使用 --pro-root 指定实际运行目录。')
        root, flags = roots[0], []
        matching = None
    if matching is None and not (root/'state/supervisor.json').is_file():
        raise UpdateError('无法确认此安装的服务模式。请使用该安装原有的受管 flocks 入口，避免误操作系统服务。')
    release = (root/'current').resolve(strict=True)
    if release.parent != root/'releases':
        raise UpdateError('Pro current 不在所选安装的 releases 目录中。')
    descriptor = json.loads(base64.b64decode(load(release/'release.json')['descriptor_b64'], validate=True))
    if not (release/'core/flockspro-go').is_file():
        raise UpdateError('所选目录没有 Pro 管理入口。')
    return root, release, descriptor, flags


def download(url, destination):
    if not url.startswith('https://'):
        raise UpdateError('下载地址必须使用 HTTPS。离线验收可使用 --repository-archive。')
    # Gitee serves its interactive archive page to urllib, but provides the
    # downloadable ZIP to the standard curl client. No browser login is used.
    curl = shutil.which('curl')
    if curl:
        subprocess.run([curl, '--fail', '--location', '--silent', '--show-error', '--retry', '2',
                        '--connect-timeout', '20', '--max-time', '300', '--max-filesize', str(MAX_ARCHIVE),
                        '--proto', '=https', '--proto-redir', '=https', '--output', str(destination), url],
                       check=True, timeout=360)
        if destination.stat().st_size > MAX_ARCHIVE:
            raise UpdateError('仓库压缩包超过下载大小限制。')
        return
    with urllib.request.urlopen(url, timeout=60) as response, destination.open('wb') as output:
        size = 0
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > MAX_ARCHIVE:
                raise UpdateError('仓库压缩包超过下载大小限制。')
            output.write(block)


class RepositoryArchive:
    def __init__(self, path):
        if not zipfile.is_zipfile(path):
            raise UpdateError('下载内容不是有效仓库 ZIP；可能是登录页面或下载错误。服务尚未停止。')
        self.archive = zipfile.ZipFile(path)
        self.entries = {}
        for entry in self.archive.infolist():
            name = str(safe_name(entry.filename))
            if stat.S_ISLNK(entry.external_attr >> 16) or name in self.entries:
                raise UpdateError('仓库 ZIP 包含符号链接或重复路径。')
            self.entries[name] = entry
        comment = self.archive.comment.decode('ascii', errors='ignore').strip()
        self.commit = comment if re.fullmatch(r'[0-9a-f]{40}', comment) else None

    def close(self):
        self.archive.close()

    def read(self, name, limit=2 * 1024 * 1024):
        entry = self.entries[name]
        if entry.file_size > limit:
            raise UpdateError(f'仓库文件超过大小限制：{name}')
        return self.archive.read(entry)

    def select(self, release_id):
        candidates = []
        for name in self.entries:
            if re.search(r'(?:^|/)customizations/pro-[^/]+/delivery\.json$', name):
                record = json.loads(self.read(name))
                if record.get('target') == release_id:
                    candidates.append((version(record['packageVersion']), name, record))
        if not candidates:
            raise UpdateError(f'该分支没有适配当前 Pro {release_id} 的增强包；服务保持原样。')
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            raise UpdateError('仓库中存在多个同版本匹配包，无法唯一选择。')
        _, name, record = candidates[0]
        return name, record

    def package(self, metadata_name, record, destination):
        relative = safe_name(record['archive'])
        if relative.parts[0] != 'releases' or len(relative.parts) != 2:
            raise UpdateError('delivery.json 中的补丁路径无效。')
        name = str(PurePosixPath(metadata_name).parent / relative)
        expected = record['sha256']
        if not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise UpdateError('delivery.json 缺少有效的 SHA-256。')
        if self.read(name+'.sha256', 4096).decode().split()[0] != expected:
            raise UpdateError('仓库校验文件与 delivery.json 不一致。')
        entry = self.entries[name]
        if entry.file_size > MAX_ARCHIVE:
            raise UpdateError('补丁压缩包超过大小限制。')
        with self.archive.open(entry) as source, destination.open('wb') as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        if sha(destination) != expected:
            raise UpdateError('补丁压缩包 SHA-256 校验失败；服务尚未停止。')


def extract_package(archive, directory):
    seen, total = set(), 0
    with tarfile.open(archive, 'r:gz') as source:
        for entry in source:
            name = safe_name(entry.name)
            if name.parts[0] != 'home-dashboard' or name in seen or not (entry.isfile() or entry.isdir()):
                raise UpdateError('补丁压缩包目录结构或文件类型无效。')
            seen.add(name)
            if len(name.parts) > 1 and name.parts[1] in {'state', 'backups', 'config'}:
                raise UpdateError('补丁不能覆盖已有配置、状态或备份目录。')
            total += entry.size
            if total > MAX_ARCHIVE:
                raise UpdateError('解压内容超过大小限制。')
            path = directory.joinpath(*name.parts)
            if entry.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(entry) as stream, path.open('wb') as output:
                    shutil.copyfileobj(stream, output)
                path.chmod(0o755 if path.name == 'apply.sh' else 0o644)
    package = directory/'home-dashboard'
    profile, manifest = load(package/'target-profile.json'), load(package/'payload-manifest.json')
    declared = set()
    for entry in manifest['files']:
        name = str(safe_name(entry['path']))
        if name in declared or sha(package/'payload'/name) != entry['sha256']:
            raise UpdateError(f'补丁源码校验失败：{name}')
        declared.add(name)
    actual = {p.relative_to(package/'payload').as_posix() for p in (package/'payload').rglob('*') if p.is_file()}
    if actual != declared:
        raise UpdateError('补丁包含未登记的部署文件。')
    return package, profile


def call(args, *, capture=False):
    result = subprocess.run([str(a) for a in args], check=True, text=True,
                            capture_output=capture, timeout=360)
    return result.stdout if capture else None


def activate_package(package, current, temporary):
    """Replace installer payload/metadata while keeping state and backups fixed."""
    current.mkdir(parents=True, exist_ok=True)
    if current.is_symlink() or (current/'payload').is_symlink():
        raise UpdateError('现有增强目录或 payload 是符号链接，已保留。')
    if (current/'payload').exists() and not (current/'payload').is_dir():
        raise UpdateError('现有 payload 不是目录，已保留。')
    old = {}
    for name in METADATA_FILES:
        path = current/name
        if path.is_symlink():
            raise UpdateError(f'现有安装器文件是符号链接，已保留：{name}')
        if path.exists() and not path.is_file():
            raise UpdateError(f'现有安装器文件不是普通文件，已保留：{name}')
        old[name] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.is_file() else None
    prepared, previous = temporary/'activated-payload', temporary/'previous-payload'
    shutil.copytree(package/'payload', prepared)
    moved = False
    try:
        if (current/'payload').exists():
            os.replace(current/'payload', previous)
        os.replace(prepared, current/'payload')
        moved = True
        for name in METADATA_FILES:
            if (package/name).is_file():
                atomic_write(current/name, (package/name).read_bytes(), 0o755 if name == 'apply.sh' else 0o644)
    except BaseException:
        if moved:
            shutil.rmtree(current/'payload')
        if previous.exists():
            os.replace(previous, current/'payload')
        for name, value in old.items():
            if value is None:
                (current/name).unlink(missing_ok=True)
            else:
                atomic_write(current/name, *value)
        raise


def atomic_write(path, content, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.update-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def listen_snapshot(root):
    path = root/'listen'
    if path.is_symlink() or path.exists() and not path.is_file():
        raise UpdateError('Pro listen 配置不是普通文件，已保留。')
    return (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.is_file() else None


def restore_listen(root, previous):
    path = root/'listen'
    if path.is_symlink():
        raise UpdateError('监听配置出现符号链接冲突，已保留；服务保持停止。')
    if previous is None:
        path.unlink(missing_ok=True)
    elif not path.is_file() or (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) != previous:
        atomic_write(path, *previous)


@contextmanager
def update_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'.update-flocks.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError('另一个增强更新正在执行，请等待它完成。') from None
        yield


def update(args):
    root, release, descriptor, flags = find_installation(args.pro_root)
    base = args.customizations_root.expanduser().resolve()
    current = base/'home-dashboard'
    if current.is_symlink():
        raise UpdateError('现有 home-dashboard 是符号链接，已保留。')
    cli = [root/'current/core/flockspro-go', 'flocks', '-install-root', root, *flags, '--']
    say(f'Pro：{descriptor["release_id"]}；安装目录：{root}')
    with update_lock(base), tempfile.TemporaryDirectory(prefix='.update-', dir=base) as workspace:
        temporary = Path(workspace)
        repository = args.repository_archive
        if repository is None:
            repository = temporary/'repository.zip'
            say('下载 Gitee 分支压缩包……')
            download(args.archive_url, repository)
        archive = RepositoryArchive(repository)
        try:
            metadata_name, record = archive.select(descriptor['release_id'])
            state_file = current/'state'/(descriptor['release_id']+'.json')
            if state_file.is_file():
                state = load(state_file)
                if state.get('status') == 'applied' and version(state['packageVersion']) > version(record['packageVersion']):
                    say(f'镜像补丁 {record["packageVersion"]} 早于已安装 {state["packageVersion"]}，保持当前版本与服务。')
                    return
            bundle = temporary/'package.tar.gz'
            archive.package(metadata_name, record, bundle)
            repository_commit = archive.commit
        finally:
            archive.close()
        package, profile = extract_package(bundle, temporary/'incoming')
        if (profile['releaseId'], profile['version'], profile['packageVersion']) != (
                descriptor['release_id'], descriptor['version'], record['packageVersion']):
            raise UpdateError('补丁目标、版本与实际 Pro 或 delivery.json 不匹配。')
        legacy = profile.get('installerProtocol', 1) < 2
        installer = [sys.executable, package/'apply.py', '--pro-root', root, '--state-root', current]
        say(f'补丁 {profile["packageVersion"]} 下载与文件校验完成，正在检查当前源码……')
        try:
            command = ([sys.executable, '-c', LEGACY_PREFLIGHT, package, root, current] if legacy else [*installer, 'preflight'])
            check = json.loads(call(command, capture=True))
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or '').strip()
            raise UpdateError('安装前检查未通过，服务保持原样。'+detail) from error
        if check['action'] == 'already-applied':
            activate_package(package, current, temporary)
            say(f'补丁 {profile["packageVersion"]} 已安装且源码校验通过，无需重启。')
            return
        status = call([*cli, 'status'], capture=True)
        if 'core: state=running' not in status and 'core: state=stopped' not in status:
            raise UpdateError('无法确认原服务状态，已保留当前服务；请检查 Pro 管理入口。')
        was_running = 'core: state=running' in status
        previous_listen = listen_snapshot(root)
        if legacy:
            activate_package(package, current, temporary)
            installer = [sys.executable, current/'apply.py', '--pro-root', root]
        start = [*cli, 'start', '--no-browser', '--webui-host', args.webui_host]
        if args.webui_port is not None:
            start += ['--webui-port', str(args.webui_port)]
        stopped, applied = False, False
        try:
            say('停止 Pro 服务并部署增强源码……')
            call([*cli, 'stop'])
            stopped = True
            call([*installer, 'apply', '--install-deps'])
            applied = True
            say('通过原 Pro 入口启动并等待就绪……')
            call(start)
            if not legacy:
                activate_package(package, current, temporary)
        except BaseException:
            if applied:
                say('部署后检查失败，正在回退本次源码更新……')
                call([*cli, 'stop'])
            restore_listen(root, previous_listen)
            if applied:
                call([*installer, 'restore' if legacy else 'rollback-update'])
            recovery_state = load(state_file) if state_file.is_file() else None
            if recovery_state and recovery_state.get('status') == 'prepared':
                raise UpdateError('安装及自动恢复未完成，服务保持停止；请保留备份并使用 rollback-update 或 restore 恢复。')
            if stopped and was_running:
                say('重新启动此前的 Pro 服务……')
                call([*cli, 'start', '--no-browser'])
            raise
        receipt = {'packageVersion':profile['packageVersion'], 'releaseId':profile['releaseId'],
                   'archiveUrl':args.archive_url, 'repositoryCommit':repository_commit,
                   'sha256':record['sha256'], 'updatedAt':datetime.now(timezone.utc).isoformat()}
        atomic_write(current/'state/update-receipt.json', (json.dumps(receipt, ensure_ascii=False, indent=2)+'\n').encode())
        say(f'完成：补丁 {profile["packageVersion"]} 已生效。以后仍运行本脚本即可更新。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-url', default=DEFAULT_URL)
    parser.add_argument('--repository-archive', type=Path, help='Use an already downloaded repository ZIP')
    parser.add_argument('--pro-root', type=Path, help='Defaults to the installation named by the managed flocks command')
    parser.add_argument('--customizations-root', type=Path, default=Path.home()/'.flocks/customizations')
    parser.add_argument('--webui-host', default='0.0.0.0')
    parser.add_argument('--webui-port', type=int)
    args = parser.parse_args()
    try:
        update(args)
    except (UpdateError, OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


sys.argv[0] = 'update-flocks.sh'
raise SystemExit(main())
FLOCKS_UPDATER_PY
