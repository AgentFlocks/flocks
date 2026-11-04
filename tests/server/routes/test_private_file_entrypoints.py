"""One registered private path stays private through every file HTTP surface."""
from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from flocks.auth.context import AuthUser
from flocks.config.config import Config
from flocks.utils import http_file_read_guard as guard
from flocks.workspace.manager import WorkspaceManager


@pytest.fixture
def private_files(tmp_path, monkeypatch):
    from flocks.server.routes import session, workspace
    from flocks.session import files

    root, data, ws, context = (tmp_path / name for name in ('root', 'data', 'workspace', 'context'))
    for path in (root, data, ws, context, data / 'memory'):
        path.mkdir(parents=True, exist_ok=True)
    for name, value in {'FLOCKS_ROOT': root, 'FLOCKS_CONFIG_DIR': root / 'config', 'FLOCKS_DATA_DIR': data,
                        'FLOCKS_WORKSPACE_DIR': ws}.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setattr(Config, '_global_config', None)
    monkeypatch.setattr(WorkspaceManager, '_instance', None)
    monkeypatch.setattr(guard, '_private_path_providers', {})
    private = set()

    def add(directory, name='private.txt'):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private fixture bytes')
        private.add(path.resolve())
        return path

    guard.register_http_private_path_provider('fixture.private', lambda path: any(path == item or path.is_relative_to(item) for item in private))
    for directory in (ws, data / 'memory', context):
        add(directory)
        (directory / 'public.txt').write_text('public fixture bytes')
        directory.joinpath('private-alias.txt').symlink_to(directory / 'private.txt')
        hidden = directory / 'private-directory'
        hidden.mkdir()
        (hidden / 'child.txt').write_text('private fixture bytes')
        private.add(hidden.resolve())
    owner = AuthUser(id='usr_private', username='private', role='member', status='active')
    record = SimpleNamespace(id='ses_private', owner_user_id=owner.id, owner_username=owner.username,
                             project_id='default', directory=str(context), metadata={'contextFolders': [
                                 {'id': 'ctx_fixture', 'path': str(context), 'displayName': 'Context fixture'},
                             ]})
    monkeypatch.setattr(session, '_load_context_session', AsyncMock(return_value=(record, owner)))
    resources = {}
    for name in ('private.txt', 'public.txt'):
        resources[name] = files.ResolvedSessionFile(name, context / name, name, 'text/plain', 'user_upload', 'msg_fixture')
    actual_resolver = files.resolve_session_resource
    monkeypatch.setattr(files, 'resolve_session_resource', AsyncMock(side_effect=lambda _session, resource: resources[resource]))
    monkeypatch.setattr(files, '_messages_with_parts', AsyncMock(return_value=([], False, None)))
    monkeypatch.setattr(files, 'restore_session_uploads', AsyncMock())
    from flocks.session.features.todo import Todo
    monkeypatch.setattr(Todo, 'get_snapshot', AsyncMock(return_value=[]))
    app = FastAPI()
    app.include_router(workspace.router, prefix='/api/workspace')
    app.include_router(session.router, prefix='/api/session')
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, ws=ws, memory=data / 'memory', context=context, private=private,
                              add=add, record=record, resources=resources, session=session, actual_resolver=actual_resolver)


@pytest.mark.parametrize('area', ['', 'memory/'])
@pytest.mark.parametrize('endpoint', ['file', 'preview', 'download'])
@pytest.mark.parametrize('name', ['private.txt', 'private-alias.txt'])
def test_workspace_and_memory_direct_reads_deny_private_files(private_files, area, endpoint, name):
    response = private_files.client.get('/api/workspace/' + area + endpoint, params={'path': name})
    assert response.status_code == 403
    assert 'private fixture bytes' not in response.text


def test_workspace_zip_does_not_include_a_private_file_or_alias(private_files):
    response = private_files.client.post('/api/workspace/download/zip', json={'paths': ['public.txt', 'private.txt', 'private-alias.txt']})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ['public.txt']
        assert archive.read('public.txt') == b'public fixture bytes'


@pytest.mark.parametrize('endpoint', ['list', 'tree', 'memory/list'])
def test_workspace_and_memory_listing_hides_private_paths(private_files, endpoint):
    response = private_files.client.get('/api/workspace/' + endpoint)
    assert response.status_code == 200
    assert 'private.txt' not in response.text and 'private-alias.txt' not in response.text
    assert 'private-directory' not in response.text
    assert 'public.txt' in response.text


@pytest.mark.parametrize('endpoint', ['list', 'tree'])
def test_private_directory_itself_cannot_be_enumerated(private_files, endpoint):
    response = private_files.client.get('/api/workspace/' + endpoint, params={'path': 'private-directory'})
    assert response.status_code == 403


@pytest.mark.parametrize('endpoint', ['content', 'preview', 'download', 'metadata'])
def test_session_resource_routes_deny_private_files(private_files, endpoint):
    response = private_files.client.get('/api/session/ses_private/context/files/private.txt/' + endpoint)
    assert response.status_code == 403


@pytest.mark.parametrize('endpoint', ['content', 'preview', 'download'])
def test_session_context_root_reads_deny_private_files(private_files, endpoint):
    response = private_files.client.get('/api/session/ses_private/context/roots/ctx_fixture/' + endpoint,
                                        params={'path': 'private.txt'})
    assert response.status_code == 403


def test_session_context_root_list_filters_private_entries(private_files):
    response = private_files.client.get('/api/session/ses_private/context/roots/ctx_fixture/list')
    assert response.status_code == 200
    assert [item['name'] for item in response.json()['items']] == ['public.txt']
    response = private_files.client.get('/api/session/ses_private/context/roots/ctx_fixture/list',
                                        params={'path': 'private-directory'})
    assert response.status_code == 403


def test_session_context_summary_does_not_publish_private_resources(private_files, monkeypatch):
    from flocks.session import files
    monkeypatch.setattr(files, '_messages_with_parts', AsyncMock(return_value=([SimpleNamespace(parts=[], info=SimpleNamespace(id='msg_fixture'))], False, None)))
    monkeypatch.setattr(files, '_message_resources', lambda *_args: list(private_files.resources.values()))
    response = private_files.client.get('/api/session/ses_private/context')
    assert response.status_code == 200
    assert [item['displayName'] for item in response.json()['contextFiles']] == ['public.txt']
    private_files.private.add(private_files.context.resolve())
    response = private_files.client.get('/api/session/ses_private/context')
    assert response.status_code == 200
    assert response.json()['roots'] == []


def test_session_upload_is_denied_before_restoring_shared_bytes(private_files, monkeypatch):
    from flocks.session import files
    from flocks.session.message import FilePart, Message
    upload = private_files.add(files.session_uploads_dir(private_files.record.id))
    part = FilePart(id='prt_private', sessionID=private_files.record.id, messageID='msg_fixture',
                    mime='text/plain', filename=upload.name, url=upload.as_uri())
    message = SimpleNamespace(info=SimpleNamespace(id='msg_fixture', role='user', time={'created': 1}), parts=[part])
    monkeypatch.setattr(files, 'resolve_session_resource', private_files.actual_resolver)
    monkeypatch.setattr(Message, 'get_with_parts_lazy', AsyncMock(return_value=message))
    restore = AsyncMock()
    monkeypatch.setattr(files, 'restore_upload', restore)
    resource = files.public_resource_id('msg_fixture', 'prt_private')
    assert private_files.client.get('/api/session/ses_private/context/files/' + resource + '/download').status_code == 403
    restore.assert_not_awaited()


def test_safe_reads_and_existing_owner_gate_stay_in_place(private_files, monkeypatch):
    assert private_files.client.get('/api/workspace/file', params={'path': 'public.txt'}).json()['content'] == 'public fixture bytes'
    assert private_files.client.get('/api/session/ses_private/context/files/public.txt/content').json()['content'] == 'public fixture bytes'
    visitor = AuthUser(id='usr_other', username='other', role='member', status='active')
    monkeypatch.setattr(private_files.session, '_load_context_session', AsyncMock(return_value=(private_files.record, visitor)))
    assert private_files.client.get('/api/session/ses_private/context/roots/ctx_fixture/content', params={'path': 'public.txt'}).status_code == 403


def test_provider_failure_cannot_turn_into_a_read_or_listing(private_files, monkeypatch):
    def failed(_path):
        raise RuntimeError('do not disclose this implementation detail')
    monkeypatch.setattr(guard, '_private_path_providers', {'broken': failed})
    for url in ['/api/workspace/file?path=public.txt', '/api/workspace/list',
                '/api/session/ses_private/context/files/public.txt/download']:
        response = private_files.client.get(url)
        assert response.status_code == 403
        assert 'implementation detail' not in response.text


@pytest.mark.parametrize('endpoint', ['assets/private.txt', 'bundle.js'])
def test_webui_page_file_routes_honor_the_private_provider(private_files, monkeypatch, endpoint):
    from flocks.server.routes import webui
    monkeypatch.setattr(webui, '_require_page_access', AsyncMock())
    monkeypatch.setattr(webui, '_store', SimpleNamespace(
        validate_page_id=lambda value: value,
        asset_path=lambda *_args: private_files.context / 'private.txt',
        bundle_path=lambda *_args: private_files.context / 'private.txt',
    ))
    app = FastAPI()
    app.include_router(webui.router)
    with TestClient(app) as client:
        assert client.get('/contracts/webui/pages/fixture/' + endpoint).status_code == 403


def test_webui_page_export_omits_private_entries(private_files, monkeypatch):
    from flocks.server.routes import webui
    monkeypatch.setattr(webui, '_require_page_access', AsyncMock())
    monkeypatch.setattr(webui, '_store', SimpleNamespace(page_dir=lambda *_args: private_files.context,
                                                       validate_page_id=lambda value: value))
    app = FastAPI()
    app.dependency_overrides[webui.require_admin] = lambda: object()
    app.include_router(webui.router)
    with TestClient(app) as client:
        response = client.get('/contracts/webui/pages/fixture/export')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ['fixture/public.txt']


def test_explicit_public_favicon_still_obeys_registered_private_paths(private_files, monkeypatch):
    from flocks.server.routes import config
    monkeypatch.setattr(config.Config, 'get', AsyncMock(return_value=object()))
    monkeypatch.setattr(config, '_configured_favicon_path', lambda _config: private_files.context / 'private.txt')
    app = FastAPI()
    app.include_router(config.router, prefix='/config')
    with TestClient(app) as client:
        assert client.get('/config/ui-favicon').status_code == 403


def test_log_reads_and_listing_hide_a_protected_log_path(private_files, monkeypatch):
    from flocks.server.routes import logs
    private_files.add(private_files.context, 'backend.log')
    (private_files.context / 'webui.log').write_text('ordinary log')
    monkeypatch.setattr(logs, 'get_log_dir', lambda: private_files.context)
    app = FastAPI()
    app.include_router(logs.router, prefix='/logs')
    with TestClient(app) as client:
        assert client.get('/logs/backend.log').status_code == 403
        assert [item['name'] for item in client.get('/logs').json()['files']] == ['webui.log']
        assert client.get('/logs/latest').json()['content'] == 'ordinary log'


def test_project_folder_browser_hides_and_refuses_private_directories(private_files, monkeypatch):
    from flocks.server.routes import project
    monkeypatch.setattr(project, 'require_user', lambda _request: object())
    monkeypatch.setattr(project.Project, 'allowed_roots', lambda: [private_files.context])
    app = FastAPI()
    app.include_router(project.router, prefix='/project')
    with TestClient(app) as client:
        response = client.get('/project/folders', params={'path': str(private_files.context)})
        assert response.status_code == 200
        assert 'private-directory' not in response.text
        assert client.get('/project/folders', params={'path': str(private_files.context / 'private-directory')}).status_code == 403
