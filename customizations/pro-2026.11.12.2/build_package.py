#!/usr/bin/env python3
"""Publish an enhancement from the exact, tested Core/Pro source pair."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args])


def source_hash(root, scope):
    paths=git(root,'ls-files','-z','--cached','--others','--exclude-standard','--',scope).split(b'\0')
    digest=hashlib.sha256()
    for name in sorted(set(paths)-{b''}):
        path=root/name.decode()
        digest.update(name+b'\0')
        if path.is_symlink():
            digest.update(b'symlink\0'+os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(b'file\0'+bytes.fromhex(sha(path.read_bytes())))
        else:
            digest.update(b'missing\0')
        digest.update(b'\0')
    return digest.hexdigest()


def build(core,pro,frontend):
    profile=json.loads((ROOT/'target-profile.json').read_text())
    base=profile['coreCommit']
    head=git(core,'rev-parse','HEAD').decode().strip()
    branch=git(core,'branch','--show-current').decode().strip()
    if profile.get('sourceCommit') and head!=profile['sourceCommit']:
        raise ValueError('Customization source commit does not match the declared package source')
    if subprocess.run(['git','-C',str(core),'merge-base','--is-ancestor',base,head],capture_output=True).returncode:
        raise ValueError('The verified Core release baseline is not an ancestor of the customization branch')
    if branch != profile.get('developmentBranch','zw_dev'):
        raise ValueError('Build from the declared customization development branch')
    if git(pro,'rev-parse','HEAD').decode().strip()!=profile['proCommit']:
        raise ValueError('Source commits do not match the verified target')
    built=json.loads((frontend/'flocks-webui-manifest.json').read_text())
    if built['edition']!='flockspro' or built['repositories']['flockspro']['webui_source_sha256']!=profile['frontendSources']['flockspro']:
        raise ValueError('Frontend must be built with the matching Pro extension registry')
    for name,source,scope in (('flocks',core,'webui'),('flockspro',pro,'webui-overlay')):
        record=built['repositories'][name]
        allowed={base,head} if name=='flocks' else {profile['proCommit']}
        if record['sha'] not in allowed or record['webui_source_sha256'] != source_hash(source,scope):
            raise ValueError(f'{name} frontend sources changed after the production build; rebuild before packaging')
        if git(source,'status','--porcelain','--untracked-files=normal').strip():
            raise ValueError(f'{name} source worktree must be clean before packaging; commit the intended changes first')
    for path in (ROOT/'runtime').rglob('*'):
        if path.is_file() and path.name!='.DS_Store' and '__pycache__' not in path.parts and path.suffix!='.pyc':
            name=path.relative_to(ROOT/'runtime').as_posix()
            source=core/name
            if not source.is_file() or source.read_bytes()!=path.read_bytes():
                raise ValueError(f'Runtime source is missing or differs from the committed development branch: {name}')
    for entry in built['files']:
        if sha((frontend/entry['path']).read_bytes())!=entry['sha256']:
            raise ValueError('Frontend changed after production build')
    staging=ROOT/'.work/package/home-dashboard'
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for name in ('apply.py','apply.sh','update-flocks.py','target-profile.json','README.md'):
        shutil.copyfile(ROOT/name,staging/name)
    for name in ('verification.json','pg-verification-summary.json'):
        if (ROOT/name).is_file():
            shutil.copyfile(ROOT/name,staging/name)
    (staging/'apply.sh').chmod(0o755)
    (staging/'update-flocks.py').chmod(0o755)
    changed=set(git(core,'diff','--name-only',base,head,'--','.flocks','flocks').decode().splitlines())
    # The native provider still owns these modules. Its task/ownership hooks
    # live in the added pure-Python enhancement, not these SQLite counterparts.
    replaced={'flocks/task/store.py','flocks/task/manager.py','flocks/task/executor.py','flocks/task/scheduler.py',
              'flocks/session/session.py','flocks/workflow/store.py','flocks/workflow/poller_manager.py'}
    source_only={'flocks/updater/source_branch.py','flocks/updater/deploy.py','flocks/updater/updater.py',
                 'flocks/cli/commands/update.py'}
    changed-=replaced|source_only
    paths={name:core/name for name in changed if (core/name).is_file()}
    files=[]
    for name,source in sorted(paths.items()):
        if not name.startswith(('flocks/','.flocks/flockshub/','.flocks/plugins/')):
            continue
        if source.is_symlink():
            raise ValueError(f'Unexpected source symlink: {name}')
        destination=staging/'payload/flocks'/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
        result=subprocess.run(['git','-C',str(core),'show',profile['coreCommit']+':'+name],capture_output=True)
        files.append({'path':'flocks/'+name,'sha256':sha(source.read_bytes()),'before':sha(result.stdout) if result.returncode==0 else None})
    # Pro lists a separate SOC package on PostgreSQL. Patch its exact paired
    # page clients too, preserving Pro's cases pages, dependencies and policies.
    pro_soc = pro/'hub/soc-workspace'
    pro_changes = {}
    for relative in ('index.json', 'plugins/webuis/soc_ui/manifest.json', 'plugins/webuis/soc_ui/workspace.json'):
        value = json.loads((pro_soc/relative).read_text())
        if relative == 'index.json':
            for item in value['plugins']:
                if item['type'] == 'webui' and item['id'] == 'soc_ui': item['version'] = '1.18.1'
        else:
            value['version'] = '1.18.1'
            if relative.endswith('workspace.json'): value['defaultPageId'] = 'soc-dashboard'
        pro_changes[relative] = (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()
    for file in ('Page.tsx','stepCards.ts','dashboardCache.ts'):
        relative = 'plugins/webuis/soc_ui/soc_dashboard/src/'+file
        pro_changes[relative] = (core/'.flocks/flockshub'/relative).read_bytes()
    for relative, data in pro_changes.items():
        name = 'python/share/flockspro/hub/soc-workspace/'+relative
        destination = staging/'payload'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        baseline = pro_soc/relative
        files.append({'path':name, 'sha256':sha(data),
                      'before':sha(baseline.read_bytes()) if baseline.is_file() else None})
    for path in sorted(frontend.rglob('*')):
        if not path.is_file():continue
        if path.is_symlink() or path.suffix=='.map':raise ValueError('Unexpected frontend symlink/source map')
        name='webui-dist/'+path.relative_to(frontend).as_posix()
        destination=staging/'payload'/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,destination)
        files.append({'path':name,'sha256':sha(path.read_bytes())})
    (staging/'payload-manifest.json').write_text(json.dumps({'schemaVersion':1,'deploymentMode':'source-overlay',
        'source':{'branch':branch,'head':head,'base':base}, 'sourceRoot':'runtime/flocks',
        'webuiRoot':'runtime/webui-dist','proEntry':'core/flockspro-go',
        'excludedSourcePaths':[{'path':name,'reason':'The Pro native provider owns this module; its SQLite counterpart is not overlaid'} for name in sorted(replaced)]+
                              [{'path':name,'reason':'Source-only Git updater guard; the managed Pro updater entry is retained'} for name in sorted(source_only)],
        'files':files},indent=2)+'\n')
    output=ROOT/'releases'/f"flocks-customizations-{profile['packageVersion']}-pro-{profile['version']}-full.tar.gz"
    output.parent.mkdir(exist_ok=True)
    with tarfile.open(output,'w:gz') as archive:
        archive.add(staging,arcname='home-dashboard')
    (output.with_suffix(output.suffix+'.sha256')).write_text(sha(output.read_bytes())+'  '+output.name+'\n')
    print(output)
    print('payload files:',len(files),'archive bytes:',output.stat().st_size)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core',type=Path,default=ROOT/'.work/zw_dev')
    parser.add_argument('--pro',type=Path,default=Path('/tmp/flocks-enhancement-11122-pro'))
    parser.add_argument('--frontend',type=Path,default=ROOT/'.work/pro-webui-candidate')
    args=parser.parse_args()
    build(args.core,args.pro,args.frontend)
