"""Bounded live step telemetry on top of the original Pro SOC data service."""
from __future__ import annotations

import math
import sys

NODES = {
    'stream_alert_denoise':('receive_alert','normalize','filter_logs','dedup_and_write'),
    'stream_alert_triage':('load_dedup_file','concurrent_triage','commit_cursor','summarize'),
}


def number(value):
    return value if type(value) in (int,float) and math.isfinite(value) and 0<=value<=2**53-1 else None


def augment(response):
    events = response.get('workflowEvents', [])
    selected = {}
    for event in events[:20]:
        prefix, separator, execution = str(event.get('eventId','')).partition(':')
        workflow = event.get('workflowId')
        if prefix=='workflow-execution' and separator and workflow in NODES and 0<len(execution)<=160:
            selected[execution] = event
    if not selected:
        return response
    memory = {}
    module = sys.modules.get('flocks.workflow.soc_diagnostics')
    reader = getattr(module, 'read_workflow_live_progress', None)
    if callable(reader):
        for workflow in NODES:
            memory.update(reader(workflow, [k for k,e in selected.items() if e['workflowId']==workflow]))
    persisted, durations = {}, {}
    try:
        from flocks.db.sync_query import connect
        from flocks.db_backend import backend_choice
        if backend_choice().backend=='postgres':
            with connect(readonly=True, timeout=.2) as db:
                marks=','.join('?' for _ in selected)
                if db.table_exists('workflow_executions'):
                    for row in db.execute(f'SELECT id,current_node_id,current_phase,current_step_index,step_count,started_at,finished_at FROM workflow_executions WHERE id IN ({marks})',tuple(selected)).fetchall():
                        persisted[row['id']]=dict(row)
                if db.table_exists('workflow_execution_steps'):
                    # At most 32 recent steps per exact execution, extracting a
                    # scalar from bounded payloads without returning their data.
                    values=','.join('(?)' for _ in selected)
                    query=f"SELECT keys.id,s.node_id,s.error,CASE WHEN octet_length(s.payload)<=65536 THEN s.payload::jsonb->>'duration_ms' END AS duration FROM (VALUES {values}) keys(id) CROSS JOIN LATERAL (SELECT node_id,error,payload FROM workflow_execution_steps WHERE exec_id=keys.id ORDER BY step_index DESC LIMIT 32) s"
                    seen=set()
                    for row in db.execute(query,tuple(selected)).fetchall():
                        key=(row['id'],row['node_id'])
                        event=selected[row['id']]
                        if key in seen or row['node_id'] not in NODES[event['workflowId']]:
                            continue
                        seen.add(key)
                        if row['error']:
                            continue
                        try:
                            value=float(row['duration'])
                        except (TypeError,ValueError):
                            continue
                        if number(value) is not None:
                            durations.setdefault(row['id'],{})[row['node_id']]=value
    except Exception:
        # Optional telemetry cannot break the original, permission-checked SOC
        # read. Never manufacture zero durations or a successful node.
        pass
    for execution,event in selected.items():
        row=persisted.get(execution,{})
        live={'nodeId':row.get('current_node_id'),'phase':row.get('current_phase'),
              'stepIndex':number(row.get('current_step_index')),'stepCount':number(row.get('step_count')),
              'metrics':{},'stepDurationsMs':durations.get(execution,{})}
        observed=memory.get(execution,{})
        live.update({k:v for k,v in observed.items() if k in {'nodeId','phase','stepIndex','stepCount','metrics'}})
        live['stepDurationsMs'].update(observed.get('stepDurationsMs',{}))
        event['live']=live
        started,finished=number(row.get('started_at')),number(row.get('finished_at'))
        if event.get('status')=='completed' and started is not None and finished is not None and finished>=started:
            event.setdefault('result',{})['durationMs']=finished-started
    return response
