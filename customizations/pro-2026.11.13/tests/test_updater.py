import argparse
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location('flocks_updater', Path(__file__).resolve().parents[1]/'update-flocks.py')
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
pytestmark = pytest.mark.no_database


def snapshot(directory, *, package_version='1.14.1', broken_checksum=False, extra_tar=None, protocol=2):
    profile = {'releaseId':'2026.11.12.2-linux-amd64', 'version':'2026.11.12.2', 'packageVersion':package_version,'installerProtocol':protocol}
    payload = b'ENHANCED = True\n'
    files = {'apply.py':b'# test installer\n', 'apply.sh':b'#!/bin/sh\n',
             'README.md':b'new package', 'target-profile.json':json.dumps(profile).encode(),
             'payload-manifest.json':json.dumps({'files':[{'path':'flocks/flocks/demo.py',
                 'sha256':hashlib.sha256(payload).hexdigest(),'before':None}]}).encode(),
             'payload/flocks/flocks/demo.py':payload}
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes,mode='w:gz') as archive:
        for name,data in files.items():
            info=tarfile.TarInfo('home-dashboard/'+name); info.size=len(data)
            archive.addfile(info,io.BytesIO(data))
        if extra_tar:
            info=tarfile.TarInfo(extra_tar);info.size=1;archive.addfile(info,io.BytesIO(b'x'))
    bundle=tar_bytes.getvalue()
    checksum=hashlib.sha256(bundle).hexdigest()
    record={'packageVersion':package_version,'target':profile['releaseId'],
            'archive':'releases/customizations.tar.gz','sha256':'0'*64 if broken_checksum else checksum}
    path=directory/'repository.zip'; prefix='flocks-fix-process_11.12_en/customizations/pro-2026.11.12.2/'
    with zipfile.ZipFile(path,'w') as archive:
        archive.comment=b'1'*40
        archive.writestr(prefix+'delivery.json',json.dumps(record))
        archive.writestr(prefix+record['archive'],bundle)
        archive.writestr(prefix+record['archive']+'.sha256',record['sha256']+'  customizations.tar.gz\n')
    return path,record


@pytest.fixture
def deployment(tmp_path,monkeypatch):
    root=tmp_path/'pro account'/'flockspro'
    release=root/'releases/2026.11.12.2-linux-amd64'; (release/'core').mkdir(parents=True)
    (root/'current').symlink_to(release)
    (release/'core/flockspro-go').write_text('fixture')
    (root/'state').mkdir();(root/'state/supervisor.json').write_text('{"mode":"daemon"}')
    descriptor={'release_id':release.name,'version':'2026.11.12.2'}
    (release/'release.json').write_text(json.dumps({'descriptor_b64':base64.b64encode(json.dumps(descriptor).encode()).decode()}))
    repository,record=snapshot(tmp_path)
    base=tmp_path/'pro account/.flocks/customizations'; current=base/'home-dashboard'
    (current/'state').mkdir(parents=True);(current/'backups/previous').mkdir(parents=True)
    (current/'backups/previous/original').write_text('preserve backup')
    (current/'config.json').write_text('{"user":"preserve configuration"}')
    (current/'apply.py').write_text('# previous installer')
    (current/'payload').mkdir();(current/'payload/old.py').write_text('old payload')
    calls=[]
    def call(args,*,capture=False):
        args=[str(a) for a in args];calls.append(args)
        if 'preflight' in args:
            return json.dumps({'action':'upgrade'})
        if args[-1]=='status':
            return '  core: state=running PID=123\n  flocks: state=healthy\n'
        if 'apply' in args:
            (current/'state'/f'{release.name}.json').write_text(json.dumps({'status':'applied','packageVersion':'1.14.1'}))
        return '' if capture else None
    monkeypatch.setattr(updater,'call',call)
    args=argparse.Namespace(pro_root=root,customizations_root=base,repository_archive=repository,
        archive_url=updater.DEFAULT_URL,webui_host='0.0.0.0',webui_port=None)
    return argparse.Namespace(root=root,release=release,base=base,current=current,args=args,calls=calls,record=record,call=call)


def test_gitee_zip_extracts_inner_tar_and_checks_hash(tmp_path):
    path,record=snapshot(tmp_path)
    archive=updater.RepositoryArchive(path)
    try:
        name,selected=archive.select('2026.11.12.2-linux-amd64')
        bundle=tmp_path/'bundle.tar.gz';archive.package(name,selected,bundle)
        assert archive.commit=='1'*40
        package,profile=updater.extract_package(bundle,tmp_path/'extracted')
        assert profile['packageVersion']=='1.14.1'
        assert (package/'payload/flocks/flocks/demo.py').read_bytes()==b'ENHANCED = True\n'
        assert updater.sha(bundle)==record['sha256']
    finally:
        archive.close()


def test_download_uses_standard_curl_without_shell(tmp_path,monkeypatch):
    destination=tmp_path/'repository.zip';calls=[]
    monkeypatch.setattr(updater.shutil,'which',lambda name:'/usr/bin/curl' if name=='curl' else None)
    def run(args,**kwargs):
        calls.append(args)
        assert kwargs.get('check') is True and not kwargs.get('shell')
        Path(args[args.index('--output')+1]).write_bytes(b'fixture')
    monkeypatch.setattr(updater.subprocess,'run',run)
    updater.download(updater.DEFAULT_URL,destination)
    assert calls[0][-1]==updater.DEFAULT_URL
    assert '--location' in calls[0] and '--fail' in calls[0]


def test_updates_using_managed_pro_entry_and_keeps_user_data(deployment):
    d=deployment; updater.update(d.args)
    assert (d.current/'config.json').read_text()=='{"user":"preserve configuration"}'
    assert (d.current/'backups/previous/original').read_text()=='preserve backup'
    assert not (d.current/'payload/old.py').exists()
    assert (d.current/'payload/flocks/flocks/demo.py').is_file()
    service=[c for c in d.calls if 'flocks' in c]
    assert all(c[:4]==[str(d.root/'current/core/flockspro-go'),'flocks','-install-root',str(d.root)] for c in service)
    assert [c[-1] for c in service[:2]]==['status','stop']
    assert service[-1][-4:]==['start','--no-browser','--webui-host','0.0.0.0']
    assert any('--state-root' in c and str(d.current) in c for c in d.calls)
    receipt=json.loads((d.current/'state/update-receipt.json').read_text())
    assert receipt['repositoryCommit']=='1'*40 and receipt['sha256']==d.record['sha256']


def test_repeat_skips_stop_start_and_repairs_installer_files(deployment,monkeypatch):
    d=deployment
    monkeypatch.setattr(updater,'call',lambda args,**kwargs: json.dumps({'action':'already-applied'}) if 'preflight' in args else pytest.fail('Service must remain running'))
    updater.update(d.args)
    assert (d.current/'apply.py').read_text()=='# test installer\n'


def test_original_1140_first_install_uses_stable_backup_directory(deployment,monkeypatch,tmp_path):
    d=deployment; d.args.repository_archive,_=snapshot(tmp_path,package_version='1.14.0',protocol=1)
    def call(args,**kwargs):
        if '-c' in args:
            d.calls.append([str(a) for a in args]);return json.dumps({'action':'apply','legacy':True})
        return d.call(args,**kwargs)
    monkeypatch.setattr(updater,'call',call)
    updater.update(d.args)
    apply=next(c for c in d.calls if 'apply' in c)
    assert apply[1]==str(d.current/'apply.py') and '--state-root' not in apply
    assert (d.current/'backups/previous/original').is_file()


def test_stale_mirror_cannot_downgrade_or_stop_installed_patch(deployment,monkeypatch):
    d=deployment
    (d.current/'state'/f'{d.release.name}.json').write_text(json.dumps({'status':'applied','packageVersion':'1.15.0'}))
    monkeypatch.setattr(updater,'call',lambda *a,**k: pytest.fail('No service or installer call expected'))
    updater.update(d.args)
    assert (d.current/'apply.py').read_text()=='# previous installer'


def test_corrupt_download_is_rejected_before_stopping_service(deployment,tmp_path):
    d=deployment;d.args.repository_archive,_=snapshot(tmp_path,broken_checksum=True)
    with pytest.raises(updater.UpdateError,match='SHA-256'):
        updater.update(d.args)
    assert d.calls==[]


def test_html_response_is_rejected_before_stopping_service(deployment,tmp_path):
    d=deployment; html=tmp_path/'login.html';html.write_text('<html>login required</html>');d.args.repository_archive=html
    with pytest.raises(updater.UpdateError,match='不是有效仓库 ZIP'):
        updater.update(d.args)
    assert d.calls==[]


def test_failed_apply_restarts_previous_service_without_activating_files(deployment,monkeypatch):
    d=deployment
    def call(args,**kwargs):
        if 'apply' in args:
            d.calls.append([str(a) for a in args]);raise subprocess.CalledProcessError(1,args)
        return d.call(args,**kwargs)
    monkeypatch.setattr(updater,'call',call)
    with pytest.raises(subprocess.CalledProcessError):updater.update(d.args)
    assert d.calls[-1][-2:]==['start','--no-browser']
    assert not any('rollback-update' in c for c in d.calls)
    assert (d.current/'apply.py').read_text()=='# previous installer'


def test_failed_start_rolls_back_update_then_restarts_previous_service(deployment,monkeypatch):
    d=deployment; starts=0
    def call(args,**kwargs):
        nonlocal starts
        if 'start' in args:
            starts+=1
            if starts==1:
                d.calls.append([str(a) for a in args]);raise subprocess.CalledProcessError(1,args)
        return d.call(args,**kwargs)
    monkeypatch.setattr(updater,'call',call)
    with pytest.raises(subprocess.CalledProcessError):updater.update(d.args)
    assert any('rollback-update' in c for c in d.calls)
    assert d.calls[-1][-2:]==['start','--no-browser']
    assert (d.current/'apply.py').read_text()=='# previous installer'


def test_failed_automatic_recovery_never_starts_mixed_source(deployment,monkeypatch):
    d=deployment
    def call(args,**kwargs):
        if 'apply' in args:
            d.calls.append([str(a) for a in args])
            (d.current/'state'/f'{d.release.name}.json').write_text(json.dumps({'status':'prepared','packageVersion':'1.14.1'}))
            raise subprocess.CalledProcessError(1,args)
        return d.call(args,**kwargs)
    monkeypatch.setattr(updater,'call',call)
    with pytest.raises(updater.UpdateError,match='服务保持停止'):updater.update(d.args)
    assert not any('start' in c for c in d.calls)


@pytest.mark.parametrize('existing',[True,False])
def test_failed_start_restores_listen_before_restarting_a(deployment,monkeypatch,existing):
    d=deployment; listen=d.root/'listen'; original=b'{"host":"127.0.0.1","port":"5173"}\n'
    if existing:
        listen.write_bytes(original);listen.chmod(0o640)
    starts=0
    def call(args,**kwargs):
        nonlocal starts
        if 'start' in args:
            starts+=1
            if starts==1:
                listen.write_bytes(b'{"host":"0.0.0.0","port":"9999"}')
                raise subprocess.CalledProcessError(1,args)
            assert listen.read_bytes()==original if existing else not listen.exists()
        return d.call(args,**kwargs)
    monkeypatch.setattr(updater,'call',call)
    with pytest.raises(subprocess.CalledProcessError):updater.update(d.args)
    if existing:assert listen.stat().st_mode & 0o777==0o640


def test_tar_traversal_is_rejected(tmp_path):
    path,_=snapshot(tmp_path,extra_tar='home-dashboard/../../escape')
    archive=updater.RepositoryArchive(path)
    try:
        name,record=archive.select('2026.11.12.2-linux-amd64');bundle=tmp_path/'tar.gz';archive.package(name,record,bundle)
        with pytest.raises(updater.UpdateError,match='路径无效'):updater.extract_package(bundle,tmp_path/'unpacked')
        assert not (tmp_path/'escape').exists()
    finally:archive.close()


def test_literal_user_wrapper_is_parsed_without_execution(tmp_path):
    root=tmp_path/'Pro Login Account/flockspro';wrapper=tmp_path/'flocks'
    wrapper.write_text(f"#!/bin/sh\nexec '/opt/core/flockspro-go' flocks -user -install-root '{root}' -- \"$@\"\n")
    assert updater.wrapper_installation(wrapper)==(root.resolve(),['-user'])


def test_unknown_pro_release_is_rejected_before_stopping_service(deployment):
    d=deployment
    descriptor={'release_id':'another-release','version':'2026.11.13'}
    (d.release/'release.json').write_text(json.dumps({'descriptor_b64':base64.b64encode(json.dumps(descriptor).encode()).decode()}))
    with pytest.raises(updater.UpdateError,match='没有适配当前 Pro'):updater.update(d.args)
    assert d.calls==[]


def test_unknown_service_mode_is_not_guessed(deployment,monkeypatch):
    d=deployment;(d.root/'state/supervisor.json').unlink()
    monkeypatch.setattr(updater,'wrapper_installation',lambda p:None)
    with pytest.raises(updater.UpdateError,match='无法确认此安装的服务模式'):
        updater.find_installation(d.root)
