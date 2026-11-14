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
    profile['packageVersion']='1.14.0'
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
    original['python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/soc_overview/src/index.tsx'] = b'original-overview'
    original['python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/workspace.json'] = b'original-pro-soc'
    original['python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/soc_cases/src/index.tsx'] = b'preserved-pro-cases'
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
    desired['python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/soc_overview/src/index.tsx'] = b'cached-overview'
    desired['python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/soc_alerts/src/queryCache.ts'] = b'query-cache'
    desired['python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/workspace.json'] = b'enhanced-pro-soc'
    desired['python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/soc_dashboard/src/dashboardCache.ts'] = b'cache-source'
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


def test_reissued_package_is_recorded_as_an_upgrade(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    manifest=i.package/'payload-manifest.json'
    payload=json.loads(manifest.read_text())
    payload['package_revision']='another-package'
    manifest.write_text(json.dumps(payload))
    installer.execute(i.root,'apply',run=i.run)
    state=next((i.package/'state').glob('*.json'))
    assert json.loads(state.read_text())['payloadManifestSha256']==installer.digest(manifest)
    assert (i.runtime/'webui-dist/index.html').read_bytes()==b'new-ui'
    installer.execute(i.root,'restore',run=i.run)
    assert (i.runtime/'webui-dist/index.html').read_bytes()==b'old-ui'


def publish_package(i,version,desired):
    profile_path=i.package/'target-profile.json'
    profile=json.loads(profile_path.read_text())
    profile['packageVersion']=version
    profile_path.write_text(json.dumps(profile))
    records=[]
    for name,data in desired.items():
        destination=i.package/'payload'/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data)
        records.append({'path':name,'sha256':hashlib.sha256(data).hexdigest(),
                        'before':hashlib.sha256(i.original[name]).hexdigest() if name in i.original else None})
    (i.package/'payload-manifest.json').write_text(json.dumps({'files':records}))


def state_file(i,state_root=None):
    return next(((state_root or i.package)/'state').glob('*.json'))


def runtime_tree(i):
    return {p.relative_to(i.runtime).as_posix():p.read_bytes() for p in i.runtime.rglob('*') if p.is_file()}


def test_a_to_b_to_c_updates_removed_files_and_restores_original(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    b={name:data for name,data in i.desired.items() if name not in {
        'flocks/flocks/demo.py','flocks/flocks/enhancements/new.py','webui-dist/assets/new.js'}}
    b.update({'flocks/flocks/enhancements/b.py':b'B feature','webui-dist/assets/b.js':b'B asset',
              'webui-dist/index.html':b'B UI'})
    publish_package(i,'1.15.0',b)
    installer.execute(i.root,'apply',run=i.run)
    assert (i.runtime/'flocks/flocks/demo.py').read_bytes()==i.original['flocks/flocks/demo.py']
    assert not (i.runtime/'flocks/flocks/enhancements/new.py').exists()
    assert not (i.runtime/'webui-dist/assets/new.js').exists()
    assert (i.runtime/'flocks/flocks/enhancements/b.py').read_bytes()==b'B feature'
    c={name:data for name,data in b.items() if name not in {'flocks/flocks/enhancements/b.py','webui-dist/assets/b.js'}}
    c.update({'flocks/flocks/demo.py':b'C source','webui-dist/assets/c.js':b'C asset','webui-dist/index.html':b'C UI'})
    publish_package(i,'1.16.0',c)
    installer.execute(i.root,'apply',run=i.run)
    assert not (i.runtime/'flocks/flocks/enhancements/b.py').exists()
    assert not (i.runtime/'webui-dist/assets/b.js').exists()
    assert (i.runtime/'flocks/flocks/demo.py').read_bytes()==b'C source'
    installer.execute(i.root,'restore',run=i.run)
    assert runtime_tree(i)==i.original
    installer.execute(i.root,'status',run=i.run)


def test_failed_upgrade_restores_a_and_its_exact_state(installation,monkeypatch):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    before=runtime_tree(i)
    state=state_file(i)
    original_state=state.read_bytes()
    b={**i.desired,'flocks/flocks/demo.py':b'B source','webui-dist/index.html':b'B UI'}
    publish_package(i,'1.15.0',b)
    original_replace=installer.replace
    failed=False
    def interrupted(path,data,mode):
        nonlocal failed
        if not failed and path==i.runtime/'webui-dist/index.html':
            failed=True
            raise OSError('upgrade interrupted')
        return original_replace(path,data,mode)
    monkeypatch.setattr(installer,'replace',interrupted)
    with pytest.raises(OSError,match='upgrade interrupted'):
        installer.execute(i.root,'apply',run=i.run)
    assert runtime_tree(i)==before
    assert state.read_bytes()==original_state


def test_update_start_failure_can_roll_back_to_previous_enhancement(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    original=runtime_tree(i)
    saved_state=state_file(i).read_bytes()
    publish_package(i,'1.15.0',{**i.desired,'flocks/flocks/demo.py':b'B source','webui-dist/index.html':b'B UI'})
    installer.execute(i.root,'apply',run=i.run)
    installer.execute(i.root,'rollback-update',run=i.run)
    assert runtime_tree(i)==original
    assert state_file(i).read_bytes()==saved_state


def test_legacy_schema_one_updates_without_restore(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    state=state_file(i)
    legacy=json.loads(state.read_text())
    legacy['schemaVersion']=1
    for key in ('rollback','previousStateSha256','payloadManifestSha256'):
        legacy.pop(key,None)
    state.write_text(json.dumps(legacy))
    publish_package(i,'1.15.0',{**i.desired,'flocks/flocks/demo.py':b'B source'})
    installer.execute(i.root,'apply',run=i.run)
    assert json.loads(state.read_text())['schemaVersion']==2
    assert (i.runtime/'flocks/flocks/demo.py').read_bytes()==b'B source'
    installer.execute(i.root,'restore',run=i.run)
    assert runtime_tree(i)==i.original


def test_preflight_is_readonly_and_does_not_require_a_stop(installation,monkeypatch,capsys):
    i=installation
    storage=i.package.parent/'stable-home-dashboard'
    monkeypatch.setattr(installer,'stopped',lambda *_:pytest.fail('preflight must not require stopping'))
    before=runtime_tree(i)
    installer.execute(i.root,'preflight',run=i.run,state_root=storage)
    answer=json.loads(capsys.readouterr().out)
    assert answer['action']=='apply'
    assert answer['source_root']==str(i.runtime/'flocks')
    assert not storage.exists()
    assert runtime_tree(i)==before


def test_candidate_uses_stable_state_root_and_detects_idempotence(installation,capsys):
    i=installation
    storage=i.package.parent/'stable-home-dashboard'
    installer.execute(i.root,'apply',run=i.run,state_root=storage)
    capsys.readouterr()
    assert not (i.package/'state').exists()
    assert state_file(i,storage).is_file()
    installer.execute(i.root,'preflight',run=i.run,state_root=storage)
    assert json.loads(capsys.readouterr().out)['action']=='already-applied'
    installer.execute(i.root,'restore',run=i.run,state_root=storage)
    assert runtime_tree(i)==i.original


def test_upgrade_rejects_unknown_local_edits_before_modification(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    path=i.runtime/'flocks/flocks/demo.py'
    path.chmod(0o644)
    path.write_bytes(b'local edit after A')
    before=runtime_tree(i)
    saved_state=state_file(i).read_bytes()
    publish_package(i,'1.15.0',{**i.desired,'flocks/flocks/demo.py':b'B source'})
    with pytest.raises(installer.Refusal,match='changed since enhancement'):
        installer.execute(i.root,'preflight',run=i.run)
    with pytest.raises(installer.Refusal,match='changed since enhancement'):
        installer.execute(i.root,'apply',run=i.run)
    assert runtime_tree(i)==before
    assert state_file(i).read_bytes()==saved_state


def test_old_mirror_cannot_downgrade_an_installed_enhancement(installation):
    i=installation
    publish_package(i,'1.16.0',i.desired)
    installer.execute(i.root,'apply',run=i.run)
    before=runtime_tree(i)
    publish_package(i,'1.15.0',i.desired)
    with pytest.raises(installer.Refusal,match='older than installed'):
        installer.execute(i.root,'preflight',run=i.run)
    with pytest.raises(installer.Refusal,match='older than installed'):
        installer.execute(i.root,'apply',run=i.run)
    assert runtime_tree(i)==before


@pytest.mark.parametrize('recovery',['rollback-update','restore'])
def test_interrupted_upgrade_requires_explicit_verified_recovery(installation,monkeypatch,recovery):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    a=runtime_tree(i)
    old_state=state_file(i).read_bytes()
    publish_package(i,'1.15.0',{**i.desired,'flocks/flocks/demo.py':b'B source','webui-dist/index.html':b'B UI'})
    original_replace=installer.replace
    original_rollback=installer.rollback
    def power_loss(path,data,mode):
        if path==i.runtime/'webui-dist/index.html':
            raise OSError('simulated power loss')
        return original_replace(path,data,mode)
    monkeypatch.setattr(installer,'replace',power_loss)
    monkeypatch.setattr(installer,'rollback',lambda *_:(_ for _ in ()).throw(OSError('rollback interrupted')))
    with pytest.raises(OSError,match='rollback interrupted'):
        installer.execute(i.root,'apply',run=i.run)
    prepared=state_file(i).read_bytes()
    mixed=runtime_tree(i)
    assert json.loads(prepared)['status']=='prepared'
    monkeypatch.setattr(installer,'replace',original_replace)
    monkeypatch.setattr(installer,'rollback',original_rollback)
    with pytest.raises(installer.Refusal,match='interrupted'):
        installer.execute(i.root,'preflight',run=i.run)
    with pytest.raises(installer.Refusal,match='interrupted'):
        installer.execute(i.root,'apply',run=i.run)
    assert runtime_tree(i)==mixed and state_file(i).read_bytes()==prepared
    installer.execute(i.root,recovery,run=i.run)
    assert runtime_tree(i)==(a if recovery=='rollback-update' else i.original)
    if recovery=='rollback-update':
        assert state_file(i).read_bytes()==old_state


def test_upgrade_preflight_rejects_damaged_original_backup(installation):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    journal=json.loads(state_file(i).read_text())
    (Path(journal['backup'])/'flocks/flocks/demo.py').write_bytes(b'damaged backup')
    before=runtime_tree(i)
    publish_package(i,'1.15.0',{**i.desired,'flocks/flocks/demo.py':b'B source'})
    with pytest.raises(installer.Refusal,match='Backup failed verification'):
        installer.execute(i.root,'preflight',run=i.run)
    assert runtime_tree(i)==before


def test_interrupted_original_restore_can_continue_without_losing_the_baseline(installation,monkeypatch):
    i=installation
    installer.execute(i.root,'apply',run=i.run)
    publish_package(i,'1.15.0',{**i.desired,'flocks/flocks/demo.py':b'B source'})
    installer.execute(i.root,'apply',run=i.run)
    original_replace=installer.replace
    failed=False
    def interrupted(path,data,mode):
        nonlocal failed
        if not failed and path==i.runtime/'flocks/flocks/demo.py':
            failed=True
            raise OSError('restore interrupted')
        return original_replace(path,data,mode)
    monkeypatch.setattr(installer,'replace',interrupted)
    with pytest.raises(OSError,match='restore interrupted'):
        installer.execute(i.root,'restore',run=i.run)
    pending=json.loads(state_file(i).read_text())
    assert pending['status']=='prepared' and pending['restoreInProgress'] is True
    with pytest.raises(installer.Refusal,match='restore was interrupted'):
        installer.execute(i.root,'rollback-update',run=i.run)
    installer.execute(i.root,'restore',run=i.run)
    assert runtime_tree(i)==i.original


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
        if label == 'pro':
            soc=source/'hub/soc-workspace'
            for relative, value in {
                'index.json': {'plugins':[{'type':'webui','id':'soc_ui','version':'1.2.9'}]},
                'plugins/webuis/soc_ui/manifest.json': {'version':'1.2.9','permissions':{'tools':['soc_case_manage']}},
                'plugins/webuis/soc_ui/workspace.json': {'version':'1.2.9','defaultPageId':'soc-overview'},
            }.items():
                path=soc/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
        else:
            page=source/'.flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/src'
            page.mkdir(parents=True)
            for file in ('Page.tsx','stepCards.ts','dashboardCache.ts'):(page/file).write_text('fixture source')
            for name in ('soc_overview','soc_alerts'):
                client=page.parent.parent/name/'src';client.mkdir(parents=True)
                for file in ('index.tsx','queryCache.ts'):(client/file).write_text('fixture cache source')
        subprocess.run(['git','init','-q',str(source)],check=True)
        subprocess.run(['git','-C',str(source),'add',scope,'hub' if label=='pro' else '.flocks'],check=True)
        subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture baseline'],check=True)
        sources.append(source)
    core,pro=sources
    subprocess.run(['git','-C',str(core),'checkout','-qb','zw_dev'],check=True)
    profile={'packageVersion':'fixture','version':'2026.11.12.2','coreCommit':builder.git(core,'rev-parse','HEAD').decode().strip(),
             'proCommit':builder.git(pro,'rev-parse','HEAD').decode().strip(),
             'developmentBranch':'zw_dev','deploymentMode':'source-overlay',
             'frontendSources':{'flockspro':builder.source_hash(pro,'webui-overlay')}}
    (root/'target-profile.json').write_text(json.dumps(profile))
    for name in ('README.md','apply.py','apply.sh','update-flocks.py'):
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
        pro_ui=json.loads(package.extractfile('home-dashboard/payload/python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/manifest.json').read())
        assert pro_ui['version']=='1.18.2'
        assert pro_ui['permissions']['tools']==['soc_case_manage']
        assert not any('/soc_cases/' in name for name in package.getnames())
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


@pytest.mark.parametrize('name', [
    'python/bin/python3',
    'python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/soc_cases/src/index.tsx',
    'python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/soc_dashboard/api/handlers.py',
    'python/share/flockspro/hub/soc-workspace/plugins/webuis/soc_ui/soc_dashboard/src/native.so',
])
def test_pro_soc_resources_do_not_expand_permissions_to_other_pro_files(name):
    with pytest.raises(installer.Refusal, match='outside supported text trees'):
        installer.allowed_path(name)
