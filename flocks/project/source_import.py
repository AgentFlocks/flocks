"""Import source trees without executing repository or archive contents."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from flocks.project.project import Project
from flocks.workspace.manager import WorkspaceManager

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_FILES = 30_000


def validate_source_url(value: str, *, git: bool = False) -> str:
    value = value.strip()
    if not value or any(ord(char) < 32 for char in value) or value.startswith('-'):
        raise ValueError('请输入有效的源码地址')
    if git and re.fullmatch(r'[\w.-]+@[\w.-]+:[\w./~-]+', value):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in ({'https', 'http', 'ssh'} if git else {'https', 'http'}) or not parsed.hostname:
        raise ValueError('Git 支持 HTTPS/SSH 地址；源码 URL 仅支持 HTTP(S) ZIP 下载地址')
    if parsed.password or (parsed.username and parsed.scheme != 'ssh'):
        raise ValueError('请使用服务器配置的凭据，不要在 URL 中填写用户名或密码')
    return value


def extract_zip(archive: Path, destination: Path) -> Path:
    """Reject traversal, links, duplicate paths, and oversized archives before writing."""
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > MAX_FILES or sum(item.file_size for item in entries) > MAX_EXTRACTED_BYTES:
            raise ValueError('ZIP 解压后不能超过 500 MB 或 30000 个条目')
        seen = set()
        for item in entries:
            path = PurePosixPath(item.filename.replace('\\', '/'))
            mode = item.external_attr >> 16
            if (path.is_absolute() or '..' in path.parts or ':' in str(path) or '\x00' in item.filename
                    or not path.parts or any(part.casefold() == '.git' for part in path.parts)
                    or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError('ZIP 包含不安全的路径、链接或文件类型')
            key = str(path).casefold()
            if key in seen:
                raise ValueError('ZIP 包含重复的文件路径')
            seen.add(key)
        written = 0
        for item in entries:
            target = destination.joinpath(*PurePosixPath(item.filename.replace('\\', '/')).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as reader, target.open('xb') as writer:
                while chunk := reader.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_EXTRACTED_BYTES:
                        raise ValueError('ZIP 解压内容超过 500 MB')
                    writer.write(chunk)
    children = list(destination.iterdir())
    if not children:
        raise ValueError('ZIP 不包含源码文件')
    # Source-host download archives commonly wrap the tree in one directory.
    return children[0] if len(children) == 1 and children[0].is_dir() else destination


async def import_project(*, owner_id: str, kind: str, name: str | None, url: str = '', branch: str = '', upload=None):
    workspace = WorkspaceManager.get_instance().get_workspace_dir().resolve()
    parent = workspace / 'code-audit' / 'imports'
    roots = Project.allowed_roots()
    if not roots or not any(parent.resolve().is_relative_to(root) for root in roots):
        raise ValueError('代码审计导入目录不在允许的项目路径中，请将 Flocks workspace 加入 FLOCKS_PROJECT_ROOTS')
    parent = Path(Project.validate_worktree(str(parent), create_if_missing=True))
    container = parent / uuid4().hex
    container.mkdir(mode=0o700)
    target = container / 'source'
    try:
        if kind == 'git':
            url = validate_source_url(url, git=True)
            if branch.startswith('-') or any(ord(c) < 32 for c in branch):
                raise ValueError('请输入有效的 Git 分支名')
            args = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never',
                    'clone', '--depth', '1', '--single-branch']
            if branch.strip():
                args += ['--branch', branch.strip()]
            args += ['--', url, str(target)]
            env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_LFS_SKIP_SMUDGE': '1', 'GIT_SSH_COMMAND': os.environ.get('GIT_SSH_COMMAND', 'ssh -oBatchMode=yes')}
            process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env)
            try:
                await asyncio.wait_for(process.wait(), timeout=180)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise
            if process.returncode:
                raise ValueError('Git 导入失败，请检查仓库、分支及服务器凭据')
            worktree = target
        else:
            archive_dir = container / 'archive'
            archive_dir.mkdir(mode=0o700)
            archive = archive_dir / 'source.zip'
            size = 0
            with archive.open('wb') as writer:
                if kind == 'zip':
                    if upload is None or not callable(getattr(upload, 'read', None)):
                        raise ValueError('请选择 ZIP 文件')
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            raise ValueError('ZIP 文件不能超过 100 MB')
                        writer.write(chunk)
                elif kind == 'url':
                    url = validate_source_url(url)
                    async with httpx.AsyncClient(timeout=60, follow_redirects=True, max_redirects=5) as client:
                        async with client.stream('GET', url) as response:
                            response.raise_for_status()
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                size += len(chunk)
                                if size > MAX_ARCHIVE_BYTES:
                                    raise ValueError('源码下载不能超过 100 MB')
                                writer.write(chunk)
                else:
                    raise ValueError('不支持的源码类型')
            target.mkdir()
            extraction = asyncio.create_task(asyncio.to_thread(extract_zip, archive, target))
            try:
                worktree = await asyncio.shield(extraction)
            except asyncio.CancelledError:
                await extraction
                raise
        label = Path(urlsplit(url).path.rstrip('/')).name if url else Path(getattr(upload, 'filename', '') or '').name
        label = re.sub(r'\.(git|zip)$', '', label, flags=re.IGNORECASE)
        return await Project.create(owner_id=owner_id, name=name or label or worktree.name, worktree=str(worktree), source_kind=kind, scope="code-security")
    except BaseException:
        shutil.rmtree(container, ignore_errors=True)
        raise
