"""Run feature tests through a real, isolated Pro Go database core."""
import os
from pathlib import Path
import sys
import tempfile

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
PRO = Path(os.environ.get('FLOCKS_PRO_SOURCE', '/tmp/flocks-enhancement-11122-pro'))
sys.path.insert(0, str(PRO / 'tests'))
os.environ['FLOCKS_DB_BACKEND'] = 'postgres'
os.environ.setdefault('FLOCKS_ROOT', tempfile.mkdtemp(prefix='zw-enhancement-tests-'))


@pytest_asyncio.fixture(autouse=True)
async def postgres_test_schema(tmp_path, monkeypatch, request):
    if request.node.get_closest_marker('no_database'):
        yield None
        return
    from uuid import uuid4
    from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl
    import asyncpg
    from pg_core_fixture import running_database_core
    dsn = os.environ['FLOCKS_TEST_PG_DSN']
    schema = 'zw_test_' + uuid4().hex
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await conn.close()
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query))
    query['search_path'] = schema + ',public'
    scoped = urlunsplit(parts._replace(query=urlencode(query)))
    for name, directory in {'FLOCKS_ROOT':'root','FLOCKS_CONFIG_DIR':'config',
                            'FLOCKS_DATA_DIR':'data','FLOCKS_WORKSPACE_DIR':'workspace',
                            'FLOCKSPRO_DATA_DIR':'pro'}.items():
        monkeypatch.setenv(name, str(tmp_path / directory))
    async with running_database_core(Path(os.environ['FLOCKSPRO_DATABASE_TESTCORE']), scoped, monkeypatch, tmp_path / 'go-core-evidence'):
        yield scoped
