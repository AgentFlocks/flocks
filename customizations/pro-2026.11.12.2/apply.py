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


def allowed_path(name):
    relative(name)
    # Only these paired Pro SOC client resources are added outside the Core
    # and browser trees. No interpreter, native module or other Pro file is allowed.
    soc_root = 'python/share/flockspro/hub/soc-workspace/'
    soc_files = {'index.json', 'plugins/webuis/soc_ui/manifest.json',
                 'plugins/webuis/soc_ui/workspace.json',
                 *('plugins/webuis/soc_ui/soc_dashboard/src/' + file for file in
                   ('Page.tsx', 'stepCards.ts', 'dashboardCache.ts'))}
    if name.startswith(soc_root) and name[len(soc_root):] in soc_files:
        return
    if not name.startswith(('flocks/flocks/','flocks/.flocks/flockshub/','flocks/.flocks/plugins/','webui-dist/')):
        raise Refusal(f'Payload outside supported text trees: {name}')


def plan(runtime, manifest, package_manifest, state=None):
    signed = {entry['path']:entry for entry in manifest['files']}
    desired = {entry['path']:entry for entry in package_manifest['files']}
    if len(desired)!=len(package_manifest['files']):
        raise Refusal('Duplicate payload paths')
    old = {entry['path']:entry for entry in state['files']} if state and state['status']=='applied' else {}
    originals = {p:e for p,e in signed.items() if p.startswith('webui-dist/')}
    for name,entry in old.items():
        allowed_path(name)
        if entry['before']!=signed.get(name,{}).get('sha256'):
            raise Refusal(f'Installed enhancement baseline differs from signed release: {name}')
        if name in signed:
            originals[name]=signed[name]
    for name, entry in desired.items():
        allowed_path(name)
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
    for name in sorted(set(originals)|set(desired)|set(old)):
        path = ordinary(runtime,name)
        before = originals.get(name,{}).get('sha256')
        expected=old[name]['after'] if name in old else before
        if digest(path) != expected:
            raise Refusal(f'Local file differs from original Pro release: {name}; preserved')
        if path.exists() and native(path):
            raise Refusal(f'Protected native file in plan: {name}')
        mode = old[name]['mode'] if name in old else stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o444
        after=desired[name]['sha256'] if name in desired else None if name.startswith('webui-dist/') else before
        records.append({'path':name,'before':before,'after':after,'mode':mode})
    actual_frontend = {p.relative_to(runtime).as_posix() for p in (runtime/'webui-dist').rglob('*') if p.is_file()}
    expected_frontend=({p for p,e in old.items() if p.startswith('webui-dist/') and e['after'] is not None}
                       if old else {p for p in originals if p.startswith('webui-dist/')})
    if actual_frontend != expected_frontend:
        raise Refusal('Unrecognized files in original Pro WebUI; preserved')
    return records


def matches(runtime, records, *, mixed=False, restored=False):
    for entry in records:
        path = ordinary(runtime,entry['path'])
        allowed = {entry['before']} if restored else {entry['before'],entry['after']} if mixed else {entry['after']}
        if digest(path) not in allowed:
            raise Refusal(f"File changed since enhancement: {entry['path']}; preserved")


def verified_backup(journal, state_root=None):
    storage=Path(state_root) if state_root is not None else PACKAGE
    backup = Path(journal['backup'])
    if backup.resolve().parent != (storage/'backups').resolve():
        raise Refusal('Backup is outside this enhancement installation')
    for entry in journal['files']:
        if entry['before'] is not None and digest(ordinary(backup,entry['path'])) != entry['before']:
            raise Refusal(f"Backup failed verification: {entry['path']}")
    return backup


def rollback(runtime, journal, state_root=None):
    backup=verified_backup(journal,state_root)
    for entry in reversed(journal['files']):
        path = ordinary(runtime,entry['path'])
        content = None if entry['before'] is None else (backup/entry['path']).read_bytes()
        replace(path,content,entry['mode'])


def read_state(path,release):
    state=load(path) if path.exists() else None
    if state and (state.get('schemaVersion') not in (1,2) or state.get('status') not in {'prepared','applied','restored'}):
        raise Refusal('Unsupported enhancement state')
    if state and state['release']!=str(release):
        raise Refusal('Existing enhancement state belongs to another installation')
    return state


def prepared_records(state):
    if state['status']=='prepared' and state.get('rollback') and not state.get('restoreInProgress'):
        return state['rollback']['files']
    return state['files']


def check_installation(runtime,manifest,package_manifest,profile,state,state_root):
    if state and state['status']=='prepared':
        raise Refusal('Previous installation was interrupted; run rollback-update or restore first')
    active=bool(state and state['status']=='applied')
    if active:
        def version(value):
            parts=str(value).split('.')
            if len(parts)!=3 or not all(part.isdigit() for part in parts):
                raise Refusal('Enhancement versions must use major.minor.patch for automatic updates')
            return tuple(map(int,parts))
        if version(profile['packageVersion'])<version(state['packageVersion']):
            raise Refusal(f"Available enhancement {profile['packageVersion']} is older than installed {state['packageVersion']}; current enhancement preserved")
        matches(runtime,state['files'])
        backup=verified_backup(state,state_root)
        check_pair(backup,profile)
    else:
        check_pair(runtime,profile)
    records=plan(runtime,manifest,package_manifest,state)
    same=bool(active and state.get('packageVersion')==profile['packageVersion'] and (
        state.get('payloadManifestSha256')==digest(PACKAGE/'payload-manifest.json') or
        not state.get('payloadManifestSha256') and {e['path']:e['after'] for e in state['files']}=={e['path']:e['after'] for e in records}))
    return records,'already-applied' if same else 'upgrade' if active else 'apply'


def snapshot(runtime,records,backup,sources=None):
    backup.mkdir(parents=True,mode=0o700)
    for entry in records:
        if entry['before'] is not None:
            name=entry['path']
            source=sources[name] if sources and name in sources else ordinary(runtime,name)
            destination=backup/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,destination)
            if digest(destination)!=entry['before']:
                raise Refusal(f'File changed while taking enhancement backup: {name}')


def previous_state(journal,state_root):
    operation=verified_backup(journal['rollback'],state_root)
    path=operation/'previous-state.json'
    if digest(path)!=journal.get('previousStateSha256'):
        raise Refusal('Previous enhancement state backup failed verification')
    return load(path)


def install(root,release,runtime,manifest,package_manifest,profile,state,state_path,state_root,run):
    records,action=check_installation(runtime,manifest,package_manifest,profile,state,state_root)
    if action=='already-applied':
        print('Enhancement already applied and verified')
        print_layout(release)
        return
    stamp='pro-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=state_root/'backups'/(stamp+'-baseline')
    old={e['path']:e for e in state['files']} if state and state['status']=='applied' else {}
    sources={name:Path(state['backup'])/name for name,e in old.items() if e['before'] is not None}
    snapshot(runtime,records,backup,sources)
    immediate=[{'path':e['path'],'before':digest(ordinary(runtime,e['path'])),'after':e['after'],
                'mode':stat.S_IMODE((runtime/e['path']).stat().st_mode) if (runtime/e['path']).is_file() else e['mode']} for e in records]
    operation=state_root/'backups'/(stamp+'-previous') if old else backup
    if old:
        snapshot(runtime,immediate,operation)
    previous=operation/'previous-state.json'
    save(previous,state)
    journal={'schemaVersion':2,'packageVersion':profile['packageVersion'],'release':str(release),
             'payloadManifestSha256':digest(PACKAGE/'payload-manifest.json'),'status':'prepared',
             'backup':str(backup),'files':records,'rollback':{'backup':str(operation),'files':immediate},
             'previousStateSha256':digest(previous)}
    save(state_path,journal)
    desired={e['path']:e for e in package_manifest['files']}
    try:
        for entry in records:
            if (root/'current').resolve()!=release:
                raise Refusal('Active Pro release changed during installation')
            name=entry['path']
            path=ordinary(runtime,name)
            content=(PACKAGE/'payload'/name).read_bytes() if name in desired else (backup/name).read_bytes() if entry['after'] is not None else None
            replace(path,content,entry['mode'])
        matches(runtime,records)
        verify_native(root,release,run)
        journal['status']='applied'
        save(state_path,journal)
    except BaseException:
        matches(runtime,immediate,mixed=True)
        rollback(runtime,journal['rollback'],state_root)
        verify_native(root,release,run)
        if state is None:
            state_path.unlink(missing_ok=True)
        else:
            save(state_path,state)
        raise
    print(f"{'Upgraded to' if action=='upgrade' else 'Applied'} enhancement {profile['packageVersion']} on Pro {profile['version']}")
    print_layout(release)
    print(f'Backup: {backup}')


def execute(root, command, *, run=subprocess.run, state_root=None):
    profile, package_manifest = load(PACKAGE/'target-profile.json'), load(PACKAGE/'payload-manifest.json')
    root, release, manifest = target(root,profile,run)
    runtime = release/'runtime'
    storage=Path(state_root).expanduser().resolve() if state_root is not None else PACKAGE
    state_dir=storage/'state'
    state_path=state_dir/(profile['releaseId']+'.json')
    if command in {'check','preflight'}:
        state=read_state(state_path,release)
        records,action=check_installation(runtime,manifest,package_manifest,profile,state,storage)
        print(json.dumps({**installation_layout(release),'action':action,'package_version':profile['packageVersion'],
                          'installed_version':state['packageVersion'] if state and state['status']=='applied' else None,
                          'managed_files':len(records)},ensure_ascii=False))
        return
    state_dir.mkdir(parents=True,exist_ok=True)
    with (state_dir/'pro-enhancement.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=read_state(state_path,release)
        if command == 'inspect':
            print(json.dumps({**installation_layout(release),'release':str(release),
                'package_version':profile['packageVersion'],'state':state['status'] if state else 'original',
                'source':package_manifest.get('source'), 'files':inspect_files(runtime,package_manifest)},ensure_ascii=False,indent=2))
            return
        if command == 'status':
            if state:
                matches(runtime,prepared_records(state),mixed=state['status']=='prepared',restored=state['status']=='restored')
            print(f"Pro {profile['version']}: {state['status'] if state else 'original'}")
            print_layout(release)
            return
        stopped(release)
        if command=='rollback-update':
            if not state or state['status']=='restored' or not state.get('rollback'):
                raise Refusal('No automatic update rollback is available for this installation')
            if state.get('restoreInProgress'):
                raise Refusal('An original-release restore was interrupted; continue restore first')
            matches(runtime,prepared_records(state) if state['status']=='prepared' else state['files'],mixed=state['status']=='prepared')
            previous=previous_state(state,storage)
            if previous and previous['release']!=str(release):
                raise Refusal('Previous enhancement belongs to a different release')
            state['status']='prepared'
            save(state_path,state)
            rollback(runtime,state['rollback'],storage)
            verify_native(root,release,run)
            if previous is None:
                state_path.unlink(missing_ok=True)
            else:
                save(state_path,previous)
            print('Rolled back the last update to the previous enhancement')
            print_layout(release)
            return
        if state and state['status'] in {'prepared','applied'}:
            if command=='apply' and state['status']=='prepared':
                raise Refusal('Previous installation was interrupted; run restore first')
            if command=='restore':
                matches(runtime,prepared_records(state) if state['status']=='prepared' else state['files'],mixed=state['status']=='prepared')
                verified_backup(state,storage)
                state.update(status='prepared',restoreInProgress=True)
                save(state_path,state)
                rollback(runtime,state,storage)
                verify_native(root,release,run)
                state.update(status='restored',restoreInProgress=False)
                save(state_path,state)
                print('Restored the original Pro release; enhancement data retained')
                print_layout(release)
                return
        if command == 'restore':
            print('No applied enhancement to restore')
            return
        install(root,release,runtime,manifest,package_manifest,profile,state,state_path,storage,run)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pro-root',type=Path,default=Path.home()/'flockspro')
    parser.add_argument('--state-root',type=Path,default=None,help='Stable enhancement directory holding state/ and backups/')
    parser.add_argument('command',choices=['apply','restore','status','inspect','check','preflight','rollback-update'])
    parser.add_argument('--install-deps',action='store_true',help='Compatibility flag; this package ships all pure Python/UI dependencies')
    args=parser.parse_args()
    try:
        execute(args.pro_root,args.command,state_root=args.state_root)
    except (Refusal,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
