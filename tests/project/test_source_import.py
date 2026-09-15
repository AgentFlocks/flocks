import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import UploadFile
from flocks.project import source_import as source


def archive_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buf.getvalue()


@pytest.mark.parametrize('name', ['../escape.py', '/tmp/escape.py', 'C:/escape.py', '..\\escape.py', '.git/config'])
def test_rejects_unsafe_zip_paths(tmp_path, name):
    archive = tmp_path / 'test.zip'
    archive.write_bytes(archive_bytes([(name, 'bad')]))
    target = tmp_path / 'target'
    target.mkdir()
    with pytest.raises(ValueError):
        source.extract_zip(archive, target)
    assert not list(target.iterdir())


def test_unwraps_source_archive_and_preserves_content(tmp_path):
    archive = tmp_path / 'test.zip'
    archive.write_bytes(archive_bytes([('demo/app.py', 'print(1)'), ('demo/readme.md', '# Demo')]))
    target = tmp_path / 'target'
    target.mkdir()
    root = source.extract_zip(archive, target)
    assert root == target / 'demo'
    assert (root / 'app.py').read_text() == 'print(1)'


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ext::command', '-option', 'https://user:secret@host/repo.git'])
def test_rejects_invalid_source_urls(url):
    with pytest.raises(ValueError):
        source.validate_source_url(url, git=True)


@pytest.mark.asyncio
async def test_upload_registers_project_and_failed_registration_cleans_files(tmp_path, monkeypatch):
    monkeypatch.setattr(source.Project, 'allowed_roots', lambda: [tmp_path])
    monkeypatch.setattr(source.WorkspaceManager, 'get_instance', lambda: SimpleNamespace(get_workspace_dir=lambda: tmp_path))
    def validate(value, **kwargs):
        Path(value).mkdir(parents=True, exist_ok=True)
        return value
    monkeypatch.setattr(source.Project, 'validate_worktree', validate)
    create = AsyncMock(return_value={'id': 'project'})
    monkeypatch.setattr(source.Project, 'create', create)
    data = archive_bytes([('demo/app.py', 'source')])
    result = await source.import_project(owner_id='owner', kind='zip', name='Demo', upload=UploadFile(io.BytesIO(data), filename='demo.zip'))
    assert result['id'] == 'project'
    root = Path(create.call_args.kwargs['worktree'])
    assert (root / 'app.py').read_text() == 'source'
    assert root.parent.parent.parent == tmp_path / 'code-audit' / 'imports'
    assert (root.parent.parent / 'archive' / 'source.zip').read_bytes() == data
    assert create.call_args.kwargs['owner_id'] == 'owner'
    before = set((tmp_path / 'code-audit' / 'imports').iterdir())
    create.side_effect = ValueError('duplicate')
    with pytest.raises(ValueError, match='duplicate'):
        await source.import_project(owner_id='owner', kind='zip', name='Demo', upload=UploadFile(io.BytesIO(data), filename='demo.zip'))
    assert set((tmp_path / 'code-audit' / 'imports').iterdir()) == before


@pytest.mark.asyncio
async def test_git_uses_argument_list_with_branch_and_no_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(source.Project, 'allowed_roots', lambda: [tmp_path])
    monkeypatch.setattr(source.WorkspaceManager, 'get_instance', lambda: SimpleNamespace(get_workspace_dir=lambda: tmp_path))
    def validate(value, **kwargs):
        Path(value).mkdir(parents=True, exist_ok=True)
        return value
    monkeypatch.setattr(source.Project, 'validate_worktree', validate)
    monkeypatch.setattr(source.Project, 'create', AsyncMock(return_value={'id': 'project'}))
    process = SimpleNamespace(wait=AsyncMock(return_value=0), returncode=0)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(source.asyncio, 'create_subprocess_exec', spawn)
    await source.import_project(owner_id='owner', kind='git', name=None, url='git@host:team/repo.git', branch='release')
    args = spawn.call_args.args
    assert '--branch' in args and args[args.index('--branch') + 1] == 'release'
    assert '--' in args and 'protocol.file.allow=never' in args


@pytest.mark.asyncio
async def test_import_rejects_workspace_outside_allowed_roots(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    monkeypatch.setattr(source.WorkspaceManager, 'get_instance', lambda: SimpleNamespace(get_workspace_dir=lambda: workspace))
    monkeypatch.setattr(source.Project, 'allowed_roots', lambda: [tmp_path / 'other'])
    with pytest.raises(ValueError, match='FLOCKS_PROJECT_ROOTS'):
        await source.import_project(owner_id='owner', kind='zip', name='Demo')
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_url_download_retains_zip_and_isolates_repeated_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(source.WorkspaceManager, 'get_instance', lambda: SimpleNamespace(get_workspace_dir=lambda: tmp_path))
    monkeypatch.setattr(source.Project, 'allowed_roots', lambda: [tmp_path])
    def validate(value, **kwargs):
        Path(value).mkdir(parents=True, exist_ok=True)
        return value
    monkeypatch.setattr(source.Project, 'validate_worktree', validate)
    create = AsyncMock(return_value={'id': 'project'})
    monkeypatch.setattr(source.Project, 'create', create)
    data = archive_bytes([('repo/main.py', 'source')])
    client_type = source.httpx.AsyncClient
    transport = source.httpx.MockTransport(lambda request: source.httpx.Response(200, content=data))
    monkeypatch.setattr(source.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=transport, **kwargs))
    for _ in range(2):
        await source.import_project(owner_id='owner', kind='url', name=None, url='https://source.example/repo.zip')
    roots = [Path(call.kwargs['worktree']) for call in create.call_args_list]
    assert roots[0] != roots[1]
    for root in roots:
        assert (root / 'main.py').read_text() == 'source'
        assert (root.parent.parent / 'archive/source.zip').read_bytes() == data
