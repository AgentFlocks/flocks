import base64
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import tarfile
from types import SimpleNamespace

import pytest
import apply as installer

pytestmark = pytest.mark.no_database


def encoded(path, key, value):
    path.write_text(json.dumps({key:base64.b64encode(json.dumps(value).encode()).decode()}))


@pytest.fixture
def installation(tmp_path, monkeypatch):
    package = tmp_path/'home-dashboard'
    package.mkdir()
    monkeypatch.setattr(installer, 'PACKAGE', package)
    profile = json.loads((Path(__file__).resolve().parents[1]/'target-profile.json').read_text())
    (package/'target-profile.json').write_text(json.dumps(profile))
    root = tmp_path/'flockspro'
    release = root/'releases'/profile['releaseId']
    runtime = release/'runtime'
    runtime.mkdir(parents=True)
    (root/'current').symlink_to(release)
    encoded(release/'release.json','descriptor_b64',{'release_id':profile['releaseId'],'version':profile['version'],
        'runtime_root':'runtime','runtime_manifest':'runtime-manifest.json','python':'runtime/python/bin/python3','webui_dist':'runtime/webui-dist'})
    original = {'flocks/flocks/demo.py':b'original\n','webui-dist/index.html':b'old-ui',
                'webui-dist/assets/old.js':b'old-js','flocks/native.so':b'\x7fELFfixture'}
    original['webui-dist/flocks-webui-manifest.json'] = json.dumps({'repositories':{
        repo:{'sha':profile['coreCommit'] if repo=='flocks' else profile['proCommit'], 'dirty':False,'webui_source_sha256':sha}
        for repo,sha in profile['frontendSources'].items()}}).encode()
    files = []
    for name,data in original.items():
        path = runtime/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o444)
        files.append({'path':name,'sha256':hashlib.sha256(data).hexdigest(),'mode':0o444})
    encoded(release/'runtime-manifest.json','manifest_b64',{'protect':'native','files':files})
    desired = {'flocks/flocks/demo.py':b'enhanced\n','flocks/flocks/enhancements/new.py':b'feature',
               'webui-dist/index.html':b'new-ui','webui-dist/assets/new.js':b'new-js',
               'webui-dist/flocks-webui-manifest.json':b'{"enhanced":true}'}
    payload=[]
    for name,data in desired.items():
        path=package/'payload'/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        payload.append({'path':name,'sha256':hashlib.sha256(data).hexdigest(),
                        'before':hashlib.sha256(original[name]).hexdigest() if name in original else None})
    (package/'payload-manifest.json').write_text(json.dumps({'files':payload}))
    for directory in sorted((p for p in runtime.rglob('*') if p.is_dir()),reverse=True):
        directory.chmod(0o555)
    calls=[]
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({'version':profile['version'],'build_id':profile['proCommit'][:7]}))
    return SimpleNamespace(root=root,release=release,runtime=runtime,package=package,original=original,desired=desired,run=run,calls=calls)


def test_install_repeat_restore_preserves_native_files_and_modes(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    for name,data in i.desired.items():
        assert (i.runtime/name).read_bytes()==data
        assert stat.S_IMODE((i.runtime/name).stat().st_mode)==0o444
    assert not (i.runtime/'webui-dist/assets/old.js').exists()
    assert (i.runtime/'flocks/native.so').read_bytes()==i.original['flocks/native.so']
    assert stat.S_IMODE((i.runtime/'flocks/flocks').stat().st_mode)==0o555
    installer.execute(i.root,'apply',run=i.run)
    assert len(list((i.package/'backups').iterdir()))==1
    installer.execute(i.root,'restore',run=i.run)
    for name,data in i.original.items():
        assert (i.runtime/name).read_bytes()==data
    assert not (i.runtime/'flocks/flocks/enhancements/new.py').exists()
    installer.execute(i.root,'status',run=i.run)
    assert sum('verify' in c for c in i.calls)>=5


def test_local_edit_is_preserved_before_any_runtime_write(installation):
    i=installation
    path=i.runtime/'flocks/flocks/demo.py'
    path.chmod(0o644)
    path.write_bytes(b'local edit')
    with pytest.raises(installer.Refusal,match='Local file differs'):
        installer.execute(i.root,'apply',run=i.run)
    assert path.read_bytes()==b'local edit'
    assert (i.runtime/'webui-dist/index.html').read_bytes()==b'old-ui'
    assert not (i.package/'backups').exists()


def test_mid_install_failure_rolls_back_the_whole_plan(installation,monkeypatch):
    i=installation
    original=installer.replace
    failed=False
    def interrupted(path,data,mode):
        nonlocal failed
        if not failed and path==i.runtime/'webui-dist/index.html':
            failed=True
            raise OSError('simulated write failure')
        return original(path,data,mode)
    monkeypatch.setattr(installer,'replace',interrupted)
    with pytest.raises(OSError,match='simulated'):
        installer.execute(i.root,'apply',run=i.run)
    for name,data in i.original.items():
        assert (i.runtime/name).read_bytes()==data
    assert not (i.runtime/'flocks/flocks/enhancements/new.py').exists()


def test_full_protection_refuses_changes(installation):
    i=installation
    path=i.release/'runtime-manifest.json'
    data=installer.envelope(path,'manifest_b64')
    data['protect']='all'
    encoded(path,'manifest_b64',data)
    with pytest.raises(installer.Refusal,match='protects all files'):
        installer.execute(i.root,'apply',run=i.run)
    assert (i.runtime/'webui-dist/index.html').read_bytes()==b'old-ui'


def test_unknown_edit_after_apply_blocks_restore(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    path=i.runtime/'flocks/flocks/demo.py'
    path.chmod(0o644)
    path.write_bytes(b'new local edit')
    with pytest.raises(installer.Refusal,match='changed since enhancement'):
        installer.execute(i.root,'restore',run=i.run)
    assert path.read_bytes()==b'new local edit'
    assert (i.runtime/'webui-dist/index.html').read_bytes()==b'new-ui'


def test_new_package_cannot_masquerade_as_already_applied(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    manifest=i.package/'payload-manifest.json'
    payload=json.loads(manifest.read_text())
    payload['package_revision']='another-package'
    manifest.write_text(json.dumps(payload))
    with pytest.raises(installer.Refusal,match='different enhancement package'):
        installer.execute(i.root,'apply',run=i.run)
    assert (i.runtime/'webui-dist/index.html').read_bytes()==b'new-ui'
    installer.execute(i.root,'restore',run=i.run)
    assert (i.runtime/'webui-dist/index.html').read_bytes()==b'old-ui'


def test_source_overlay_reports_the_real_source_and_entry_hashes(installation,capsys):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    applied=capsys.readouterr().out
    assert f'Source root: {i.runtime}/flocks' in applied
    assert f'WebUI root: {i.runtime}/webui-dist' in applied
    assert f'Pro entry: {i.release}/core/flockspro-go' in applied
    installer.execute(i.root,'inspect',run=i.run)
    result=json.loads(capsys.readouterr().out)
    assert result['deployment_mode']=='source-overlay'
    assert result['source_root']==str(i.runtime/'flocks')
    assert result['pro_entry']==str(i.release/'core/flockspro-go')
    entry=next(e for e in result['files'] if e['path']=='flocks/flocks/demo.py')
    assert entry['actual_path']==str(i.runtime/'flocks/flocks/demo.py')
    assert entry['actual_sha256']==hashlib.sha256(b'enhanced\n').hexdigest()
    assert entry['matches_payload'] is True


@pytest.fixture
def build_sources(tmp_path,monkeypatch):
    import build_package as builder
    root=tmp_path/'publisher'
    root.mkdir()
    monkeypatch.setattr(builder,'ROOT',root)
    sources=[]
    for label,scope in (('core','webui'),('pro','webui-overlay')):
        source=tmp_path/label
        (source/scope).mkdir(parents=True)
        (source/scope/'entry.ts').write_text('export const fixture = true;\n')
        subprocess.run(['git','init','-q',str(source)],check=True)
        subprocess.run(['git','-C',str(source),'add',scope],check=True)
        subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture baseline'],check=True)
        sources.append(source)
    core,pro=sources
    subprocess.run(['git','-C',str(core),'checkout','-qb','zw_dev'],check=True)
    profile={'packageVersion':'fixture','version':'2026.11.12.2','coreCommit':builder.git(core,'rev-parse','HEAD').decode().strip(),
             'proCommit':builder.git(pro,'rev-parse','HEAD').decode().strip(),
             'developmentBranch':'zw_dev','deploymentMode':'source-overlay',
             'frontendSources':{'flockspro':builder.source_hash(pro,'webui-overlay')}}
    (root/'target-profile.json').write_text(json.dumps(profile))
    for name in ('README.md','apply.py','apply.sh'):
        (root/name).write_text('fixture\n')
    frontend=tmp_path/'webui-dist'
    frontend.mkdir()
    (frontend/'index.html').write_bytes(b'fixture UI')
    (frontend/'flocks-webui-manifest.json').write_text(json.dumps({'edition':'flockspro','repositories':{
        'flocks':{'sha':profile['coreCommit'],'webui_source_sha256':builder.source_hash(core,'webui')},
        'flockspro':{'sha':profile['proCommit'],'webui_source_sha256':profile['frontendSources']['flockspro']}},
        'files':[{'path':'index.html','sha256':hashlib.sha256(b'fixture UI').hexdigest()}]}))
    return builder,root,core,pro,frontend


def test_build_refuses_stale_frontend_sources(build_sources):
    builder,root,core,pro,frontend=build_sources
    (core/'webui/entry.ts').write_text('export const changed_after_build = true;\n')
    with pytest.raises(ValueError,match='frontend sources changed'):
        builder.build(core,pro,frontend)
    assert not (root/'.work/package').exists()


def test_build_includes_committed_customization_branch_runtime_source(build_sources):
    builder,root,core,pro,frontend=build_sources
    (core/'flocks').mkdir()
    (core/'flocks/new_feature.py').write_bytes(b'NEW_FEATURE = True\n')
    subprocess.run(['git','-C',str(core),'add','flocks/new_feature.py'],check=True)
    subprocess.run(['git','-C',str(core),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','feature'],check=True)
    builder.build(core,pro,frontend)
    archive=next((root/'releases').glob('*.tar.gz'))
    with tarfile.open(archive) as package:
        assert package.extractfile('home-dashboard/payload/flocks/flocks/new_feature.py').read()==b'NEW_FEATURE = True\n'
        manifest=json.load(package.extractfile('home-dashboard/payload-manifest.json'))
        assert manifest['source']['branch']=='zw_dev'
        assert manifest['source']['head']==builder.git(core,'rev-parse','HEAD').decode().strip()
        assert manifest['source']['base']!=manifest['source']['head']


def test_build_refuses_uncommitted_runtime_changes(build_sources):
    builder,root,core,pro,frontend=build_sources
    (core/'flocks').mkdir()
    (core/'flocks/uncommitted.py').write_text('uncommitted = True\n')
    with pytest.raises(ValueError,match='worktree must be clean'):
        builder.build(core,pro,frontend)
    assert not (root/'.work/package').exists()


def test_build_requires_the_pinned_customization_commit(build_sources):
    builder,root,core,pro,frontend=build_sources
    path=root/'target-profile.json'
    profile=json.loads(path.read_text())
    profile['sourceCommit']='0'*40
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError,match='source commit does not match'):
        builder.build(core,pro,frontend)
