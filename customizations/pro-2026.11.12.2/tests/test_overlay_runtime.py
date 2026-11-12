"""Exercise the shipped overlay with the release's unchanged SQLite stores."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get('FLOCKS_TEST_CORE_SOURCE', ROOT/'.work/zw_dev'))
pytestmark = pytest.mark.no_database


def test_app_and_error_diagnostics_with_original_sqlite_store(tmp_path):
    source = tmp_path/'source'
    shutil.copytree(CORE/'flocks', source/'flocks', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    profile = json.loads((ROOT/'target-profile.json').read_text())
    # These are intentionally excluded from the Pro package. Test their actual
    # release versions, rather than the enhanced SQLite files in the Core tree.
    for name in ('flocks/task/store.py', 'flocks/task/manager.py', 'flocks/task/executor.py',
                 'flocks/task/scheduler.py', 'flocks/session/session.py',
                 'flocks/workflow/store.py', 'flocks/workflow/poller_manager.py'):
        original = subprocess.check_output(['git', '-C', str(CORE), 'show', profile['coreCommit']+':'+name])
        (source/name).write_bytes(original)
    env = {key:value for key,value in os.environ.items()
           if not key.startswith(('FLOCKS_', 'FLOCKSPRO_'))}
    env.update(HOME=str(tmp_path/'home'), FLOCKS_ROOT=str(tmp_path/'data'), FLOCKS_DB_BACKEND='sqlite')
    code = '''
import asyncio, importlib, json, sys
sys.path.insert(0, sys.argv[1])
from flocks.workflow.store import WorkflowStore
store = importlib.import_module('flocks.workflow.store')
assert not hasattr(store, 'sqlite_error_fields'), 'Regression must use the unchanged release store'
import flocks.server.app
from flocks.workflow import soc_diagnostics as diag
diag._ensure_writer = lambda: None
class Manager:
    @diag.traced('syslog.dispatch')
    async def execute(self, workflow_id):
        raise RuntimeError('private payload')
async def check():
    try:
        await Manager().execute(diag.WORKFLOWS[0])
    except RuntimeError as exc:
        assert str(exc) == 'private payload'
    else:
        raise AssertionError('The original error must propagate')
    assert diag._events[-1]['error_type'] == 'RuntimeError'
    assert 'private payload' not in json.dumps(list(diag._events))
asyncio.run(check())
print('Unchanged SQLite store: app import and error diagnostics passed')
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(source)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
