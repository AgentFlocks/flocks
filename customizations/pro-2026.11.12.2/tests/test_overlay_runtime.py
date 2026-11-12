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


def test_managed_sqlite_routes_monitoring_through_real_round_without_replacing_stores(tmp_path):
    source = tmp_path/'source'
    shutil.copytree(CORE/'flocks', source/'flocks', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (tmp_path/'python/share/flockspro').mkdir(parents=True)
    profile = json.loads((ROOT/'target-profile.json').read_text())
    for name in ('flocks/task/store.py','flocks/task/manager.py','flocks/task/executor.py',
                 'flocks/task/scheduler.py','flocks/session/session.py','flocks/workflow/store.py',
                 'flocks/workflow/poller_manager.py'):
        (source/name).write_bytes(subprocess.check_output(['git','-C',str(CORE),'show',profile['coreCommit']+':'+name]))
    env = {k:v for k,v in os.environ.items() if not k.startswith(('FLOCKS_', 'FLOCKSPRO_'))}
    env.update(FLOCKS_ROOT=str(tmp_path/'data'),FLOCKS_DATA_DIR=str(tmp_path/'data/data'),
               FLOCKS_CONFIG_DIR=str(tmp_path/'data/config'),FLOCKS_DB_BACKEND='sqlite',
               FLOCKS_HUB_ROOT=str(CORE/'.flocks/flockshub'),FLOCKS_WORKSPACE_DIR=str(tmp_path/'workspace'))
    code = '''
import asyncio,importlib.metadata,json,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
Path.home=staticmethod(lambda: Path(sys.argv[2]).parent/'home')
Path.home().mkdir(parents=True,exist_ok=True)
sys.path.insert(0,sys.argv[1])
original_entries=importlib.metadata.entry_points
def entries(**kwargs):
    items=importlib.metadata.EntryPoints(e for e in original_entries() if not e.group.startswith('flocks.') or e.dist is None or e.dist.name=='flocks')
    return items.select(**kwargs) if kwargs else items
importlib.metadata.entry_points=entries
from flocks.auth.context import AuthUser,set_current_auth_user
from flocks.storage.storage import Storage
from flocks.task.store import TaskStore
from flocks.task.manager import TaskManager
from flocks.task.executor import TaskExecutor
from flocks.task.models import SchedulerMode,TaskTrigger,TaskStatus,ExecutionTriggerType
from flocks.project.project import Project
from flocks.monitoring import runtime
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.sessions import ensure_daily
from flocks.monitoring.store import rows,write,encode
async def check():
    set_current_auth_user(AuthUser(id='owner',username='owner',role='admin'))
    await Storage.init();await TaskStore.init()
    disabled=await TaskManager.create_scheduler(title='disabled fixture',enabled=False)
    assert disabled.status.value=='disabled'
    directory=Path(sys.argv[2]);directory.mkdir()
    project=await Project.create(owner_id='owner',name='SQLite round fixture',worktree=str(directory))
    policy=MonitoringPolicy(owner='owner',project=project.id,directory=str(directory),devices=['fixture-xdr'])
    scheduler=await TaskManager.create_scheduler(title='monitor fixture',mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron='*/10 * * * *'),context={'monitoring':policy.model_dump()})
    await write('INSERT INTO monitor_installations(owner,scope,project,policy,ready,scheduler_id) VALUES(?,?,?,?,1,?)',
                (policy.owner,policy.scope,policy.project,encode(policy.model_dump()),scheduler.id))
    import flocks.hub.local as local
    local.get_record=lambda kind,key:SimpleNamespace(enabled=True) if kind=='component' else None
    import flocks.monitoring.agent_component as agent
    agent.resolve=AsyncMock()
    runtime.AuthService.get_user_by_id=AsyncMock(return_value=SimpleNamespace(status='active',to_auth_user=lambda:AuthUser(id='owner',username='owner',role='admin')))
    class Empty:
        def __init__(self,policy,session):self.policy=policy
        async def call(self,*args):return {'data':{'list':[],'total':0}}
    original=runtime.dispatch
    async def dispatch(execution,scheduler):return await original(execution,scheduler,adapter_factory=Empty)
    runtime.dispatch=dispatch
    execution=await TaskManager.create_execution_from_scheduler(scheduler,trigger_type=ExecutionTriggerType.RUN_ONCE,enqueue=False)
    result=await asyncio.wait_for(TaskExecutor.dispatch(execution,scheduler),10)
    assert result.status==TaskStatus.COMPLETED,result.error
    attempt=(await rows('SELECT * FROM monitor_attempts'))[0]
    assert attempt['summary']=='本次查询到 0 条符合条件的事件，本轮结束。'
    assert [x['tool'] for x in await rows('SELECT tool FROM monitor_steps')]==['查询 XDR 事件']
    one,_=await ensure_daily(policy,runtime.now());two,_=await ensure_daily(policy,runtime.now())
    assert one.id==two.id==execution.session_id
    # Ordinary tasks still use the original release dispatcher.
    TaskExecutor._create_task_session=AsyncMock(return_value=None)
    TaskExecutor._run_agent_session=AsyncMock(return_value='ordinary result')
    normal=await TaskManager.create_scheduler(title='ordinary fixture')
    ordinary=await TaskManager.create_execution_from_scheduler(normal,trigger_type=ExecutionTriggerType.RUN_ONCE,enqueue=False)
    result=await TaskExecutor.dispatch(ordinary,normal)
    assert result.status==TaskStatus.COMPLETED and result.result_summary=='ordinary result'
    assert TaskExecutor._run_agent_session.await_count==1
    local.get_record=lambda *args:None
    manager=await TaskManager.start(max_concurrent=1)
    assert manager._running
    await TaskManager.stop()
    await TaskStore.close()
async def main():
    try:
        await asyncio.wait_for(check(),20)
    finally:
        await TaskStore.close()
asyncio.run(main())
print('Original SQLite stores: monitor dispatch, empty round, daily session, disabled creation and ordinary dispatch passed')
'''
    result=subprocess.run([sys.executable,'-I','-B','-c',code,str(source),str(tmp_path/'workspace/project')],
                          cwd=tmp_path,env=env,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
