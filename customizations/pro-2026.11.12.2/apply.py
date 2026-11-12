#!/usr/bin/env python3
"""Install reversible text/UI enhancements into one verified Pro release."""
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
import shutil
import stat
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parent


class Refusal(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def load(path):
    return json.loads(path.read_text())


def envelope(path, field):
    return json.loads(base64.b64decode(load(path)[field], validate=True))


def relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or str(p) != name:
        raise Refusal(f'Unsafe package path: {name}')
    return p


def ordinary(root, name):
    p = root
    for part in relative(name).parts:
        p = p / part
        if p.is_symlink():
            raise Refusal(f'Symlink conflicts with enhancement: {name}')
        if p.exists() and p != root / name and not p.is_dir():
            raise Refusal(f'Parent is not a directory: {name}')
    if p.exists() and not p.is_file():
        raise Refusal(f'Target is not a regular file: {name}')
    return p


def native(path):
    if path.suffix.lower() in {'.so','.dylib','.dll','.pyd','.node','.exe'}:
        return True
    with path.open('rb') as f:
        magic = f.read(4)
    return magic.startswith((b'\x7fELF', b'MZ')) or magic in {
        b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xce',
        b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


@contextmanager
def writable_parent(path):
    parent = path.parent
    missing = []
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    original = stat.S_IMODE(parent.stat().st_mode)
    try:
        parent.chmod(original | stat.S_IWUSR)
        for directory in reversed(missing):
            directory.mkdir(mode=0o755)
        yield
    finally:
        for directory in missing:
            if directory.is_dir():
                directory.chmod(0o555)
        parent.chmod(original)


def replace(path, content, mode):
    with writable_parent(path):
        if content is None:
            path.unlink(missing_ok=True)
            return
        fd, temporary = tempfile.mkstemp(prefix='.zw-enhance-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
                os.fchmod(f.fileno(), mode)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def save(path, value):
    replace(path, (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode(), 0o600)


def verify_native(root, release, run=subprocess.run):
    run([str(release/'core/flockspro-go'),'upgrade','verify','-install-root',str(root),
         '-release',release.name], check=True, capture_output=True, text=True, timeout=180)


def target(root, profile, run=subprocess.run):
    root = root.expanduser().resolve()
    release = (root/'current').resolve(strict=True)
    if release.parent != root/'releases':
        raise Refusal('Pro current must name an installed release under releases/')
    descriptor = envelope(release/'release.json', 'descriptor_b64')
    if (descriptor.get('release_id'), descriptor.get('version')) != (profile['releaseId'],profile['version']):
        raise Refusal(f"This package requires Pro {profile['releaseId']}; active release is {descriptor.get('release_id')}")
    for key, expected in {'runtime_root':'runtime','runtime_manifest':'runtime-manifest.json',
                          'python':'runtime/python/bin/python3','webui_dist':'runtime/webui-dist'}.items():
        if descriptor.get(key) != expected:
            raise Refusal(f'Unsupported release layout: {key}')
    verify_native(root, release, run)
    manifest = envelope(release/'runtime-manifest.json','manifest_b64')
    if manifest.get('protect', 'all') != 'native':
        raise Refusal('This release protects all files and requires a signed custom release')
    build = json.loads(run([str(release/'core/flockspro-go'),'build-info'],check=True,
                          capture_output=True,text=True,timeout=30).stdout)
    if build.get('version') != profile['version'] or build.get('build_id') != profile['proCommit'][:7]:
        raise Refusal('Pro build does not match the verified source pair')
    return root, release, manifest


def stopped(release):
    # The installation command stops the managed service first. Refuse a
    # concurrent service restart instead of changing code under a live worker.
    if Path('/proc').is_dir():
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit() or int(proc.name) == os.getpid():
                continue
            try:
                command = (proc/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            except (OSError, PermissionError):
                continue
            if any(p in command for p in (str(release),str(release.parent.parent/'current'))) and ('flockspro-go' in command or '/runtime/python/' in command):
                raise Refusal('Stop the managed Flocks service before applying or restoring')


def check_pair(runtime, profile):
    data = load(runtime/'webui-dist/flocks-webui-manifest.json')
    for repo, sha in (('flocks',profile['coreCommit']),('flockspro',profile['proCommit'])):
        record = data.get('repositories',{}).get(repo,{})
        if record.get('sha') != sha or record.get('dirty') is not False or record.get('webui_source_sha256') != profile['frontendSources'][repo]:
            raise Refusal(f'Unexpected original Pro WebUI source: {repo}')


def installation_layout(release):
    return {'deployment_mode':'source-overlay', 'source_root':str(release/'runtime/flocks'),
            'webui_root':str(release/'runtime/webui-dist'), 'pro_entry':str(release/'core/flockspro-go'),
            'python_entry':str(release/'runtime/python/bin/python3')}


def print_layout(release):
    layout=installation_layout(release)
    for key,label in (('deployment_mode','Deployment mode'),('source_root','Source root'),
                      ('webui_root','WebUI root'),('pro_entry','Pro entry')):
        print(f'{label}: {layout[key]}')


def inspect_files(runtime, package_manifest):
    result=[]
    for entry in package_manifest['files']:
        name=entry['path']
        path=ordinary(runtime,name)
        actual=digest(path)
        result.append({'path':name,'actual_path':str(path),'kind':'source' if name.startswith('flocks/') else 'webui',
                       'expected_sha256':entry['sha256'],'actual_sha256':actual,'matches_payload':actual==entry['sha256']})
    return result


def plan(runtime, manifest, package_manifest):
    signed = {entry['path']:entry for entry in manifest['files']}
    desired = {entry['path']:entry for entry in package_manifest['files']}
    originals = {p:e for p,e in signed.items() if p.startswith('webui-dist/')}
    for name, entry in desired.items():
        relative(name)
        if not name.startswith(('flocks/flocks/','flocks/.flocks/flockshub/','flocks/.flocks/plugins/','webui-dist/')):
            raise Refusal(f'Payload outside supported text trees: {name}')
        payload = ordinary(PACKAGE/'payload',name)
        if not payload.is_file() or digest(payload) != entry['sha256'] or native(payload):
            raise Refusal(f'Invalid or native payload: {name}')
        if name in signed:
            originals[name] = signed[name]
            if not name.startswith('webui-dist/') and entry.get('before') != signed[name]['sha256']:
                raise Refusal(f'Packaged baseline differs from signed release: {name}')
        elif entry.get('before') is not None:
            raise Refusal(f'Missing signed baseline file: {name}')
    records = []
    for name in sorted(set(originals)|set(desired)):
        path = ordinary(runtime,name)
        before = originals.get(name,{}).get('sha256')
        if digest(path) != before:
            raise Refusal(f'Local file differs from original Pro release: {name}; preserved')
        if path.exists() and native(path):
            raise Refusal(f'Protected native file in plan: {name}')
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o444
        records.append({'path':name,'before':before,'after':desired.get(name,{}).get('sha256'),'mode':mode})
    actual_frontend = {p.relative_to(runtime).as_posix() for p in (runtime/'webui-dist').rglob('*') if p.is_file()}
    if actual_frontend != set(p for p in originals if p.startswith('webui-dist/')):
        raise Refusal('Unrecognized files in original Pro WebUI; preserved')
    return records


def matches(runtime, records, *, mixed=False, restored=False):
    for entry in records:
        path = ordinary(runtime,entry['path'])
        allowed = {entry['before']} if restored else {entry['before'],entry['after']} if mixed else {entry['after']}
        if digest(path) not in allowed:
            raise Refusal(f"File changed since enhancement: {entry['path']}; preserved")


def rollback(runtime, journal):
    backup = Path(journal['backup'])
    if backup.resolve().parent != (PACKAGE/'backups').resolve():
        raise Refusal('Backup is outside this enhancement installation')
    for entry in journal['files']:
        if entry['before'] is not None and digest(ordinary(backup,entry['path'])) != entry['before']:
            raise Refusal(f"Backup failed verification: {entry['path']}")
    for entry in reversed(journal['files']):
        path = ordinary(runtime,entry['path'])
        content = None if entry['before'] is None else (backup/entry['path']).read_bytes()
        replace(path,content,entry['mode'])


def execute(root, command, *, run=subprocess.run):
    profile, package_manifest = load(PACKAGE/'target-profile.json'), load(PACKAGE/'payload-manifest.json')
    root, release, manifest = target(root,profile,run)
    runtime = release/'runtime'
    state_dir = PACKAGE/'state'
    state_dir.mkdir(exist_ok=True)
    with (state_dir/'pro-enhancement.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state_path = state_dir/(profile['releaseId']+'.json')
        state = load(state_path) if state_path.exists() else None
        if state and state['release'] != str(release):
            raise Refusal('Existing enhancement state belongs to another installation')
        if command == 'inspect':
            print(json.dumps({**installation_layout(release),'release':str(release),
                'package_version':profile['packageVersion'],'state':state['status'] if state else 'original',
                'source':package_manifest.get('source'), 'files':inspect_files(runtime,package_manifest)},ensure_ascii=False,indent=2))
            return
        if command == 'status':
            if state:
                matches(runtime,state['files'],mixed=state['status']=='prepared',restored=state['status']=='restored')
            print(f"Pro {profile['version']}: {state['status'] if state else 'original'}")
            print_layout(release)
            return
        stopped(release)
        if state and state['status'] in {'prepared','applied'}:
            matches(runtime,state['files'],mixed=state['status']=='prepared')
            if command == 'apply' and state['status']=='applied':
                if state.get('packageVersion') != profile['packageVersion'] or state.get('payloadManifestSha256') != digest(PACKAGE/'payload-manifest.json'):
                    raise Refusal('A different enhancement package is installed; run restore before applying this package')
                print('Enhancement already applied and verified')
                print_layout(release)
                return
            if command == 'apply':
                raise Refusal('Previous installation was interrupted; run restore first')
            rollback(runtime,state)
            verify_native(root,release,run)
            state['status']='restored'
            save(state_path,state)
            print('Restored the original Pro release; enhancement data retained')
            print_layout(release)
            return
        if command == 'restore':
            print('No applied enhancement to restore')
            return
        check_pair(runtime,profile)
        records = plan(runtime,manifest,package_manifest)
        backup = PACKAGE/'backups'/('pro-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        backup.mkdir(parents=True,mode=0o700)
        for entry in records:
            if entry['before'] is not None:
                destination = backup/entry['path']
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(runtime/entry['path'],destination)
        journal = {'schemaVersion':1,'packageVersion':profile['packageVersion'], 'release':str(release),
                   'payloadManifestSha256':digest(PACKAGE/'payload-manifest.json'),
                   'status':'prepared','backup':str(backup),'files':records}
        save(state_path,journal)
        try:
            for entry in records:
                if (root/'current').resolve() != release:
                    raise Refusal('Active Pro release changed during installation')
                path = ordinary(runtime,entry['path'])
                content = (PACKAGE/'payload'/entry['path']).read_bytes() if entry['after'] is not None else None
                replace(path,content,entry['mode'])
            matches(runtime,records)
            verify_native(root,release,run)
            journal['status']='applied'
            save(state_path,journal)
        except BaseException:
            rollback(runtime,journal)
            journal['status']='restored'
            save(state_path,journal)
            raise
        print(f"Applied enhancement {profile['packageVersion']} to Pro {profile['version']}")
        print_layout(release)
        print(f'Backup: {backup}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pro-root',type=Path,default=Path.home()/'flockspro')
    parser.add_argument('command',choices=['apply','restore','status','inspect'])
    parser.add_argument('--install-deps',action='store_true',help='Compatibility flag; this package ships all pure Python/UI dependencies')
    args=parser.parse_args()
    try:
        execute(args.pro_root,args.command)
    except (Refusal,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
