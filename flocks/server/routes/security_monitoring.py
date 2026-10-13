"""Owner-scoped monitor projections and explicit start/pause controls."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import asyncio
import json
from uuid import UUID
from typing import Literal
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from flocks.server.auth import require_user
from flocks.monitoring.models import COMPONENT_ID
from flocks.monitoring.store import rows
from flocks.monitoring.reports import snapshot, newest_timeline_content
from flocks.monitoring.mailflow import MailSettingsRequest
from flocks.monitoring.mail_manual import ManualMailRequest
from flocks.monitoring.mail_retry import RetryMailRequest
from flocks.monitoring.configuration import ConfigurationRequest
from flocks.monitoring.disposition import DispositionRequest
from flocks.monitoring.automatic import AutomaticRequest

router = APIRouter(prefix='/monitoring/host-security-monitor')


async def _disposition_action(action):
    try:
        return await action
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        raise HTTPException(503, '处置请求未完成，请刷新后回查状态，勿重复写回') from None


@router.post('/dispositions')
async def confirm_disposition(body: DispositionRequest, user=Depends(require_user)):
    raise HTTPException(409, '请在邮件跟进的对应告警中记录人工处理结果；仅发信失败或尚无回信时开放')


@router.post('/dispositions/{request_id}/recheck')
async def recheck_disposition(request_id: UUID, user=Depends(require_user)):
    from flocks.monitoring.disposition import recheck
    return await _disposition_action(recheck(user.id, str(request_id)))


@router.get('/diagnostics')
async def diagnostics(user=Depends(require_user)):
    from flocks.monitoring.diagnostics import export_bundle, safe_record
    try:
        bundle = await asyncio.wait_for(asyncio.to_thread(export_bundle, user.id, COMPONENT_ID), timeout=15)
    except (TimeoutError, RuntimeError, OSError):
        raise HTTPException(503, '诊断日志暂时无法读取，请稍后重试') from None
    from flocks.monitoring.mailflow import diagnostic_state
    from flocks.monitoring.store import read_only_diagnostics
    try:
        with read_only_diagnostics():
            bundle['mail'] = await asyncio.wait_for(diagnostic_state(user.id), timeout=5)
    except Exception as exc:
        bundle['mail'] = {'snapshot_available': False, **safe_record({'error_type': type(exc).__name__})}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return JSONResponse(bundle, headers={
        'Content-Disposition': f'attachment; filename="security-monitor-diagnostics-{stamp}.json"',
        'Cache-Control': 'no-store',
    })


async def resolve_day(owner, requested=None):
    if requested:
        try:
            return date.fromisoformat(requested).isoformat()
        except ValueError:
            raise HTTPException(422, '日期格式必须为 YYYY-MM-DD') from None
    records = await rows('SELECT policy FROM monitor_installations WHERE owner=? AND scope=?', (owner, COMPONENT_ID))
    tz = json.loads(records[0]['policy'])['timezone'] if records else 'Asia/Shanghai'
    return datetime.now(timezone.utc).astimezone(ZoneInfo(tz)).date().isoformat()


@router.get('/overview')
async def overview(day: str | None = None, user=Depends(require_user)):
    day = await resolve_day(user.id, day)
    return await snapshot(user.id, COMPONENT_ID, day)


async def _event_list_read(operation):
    try:
        return await asyncio.wait_for(operation, timeout=15)
    except (FileNotFoundError, LookupError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        raise HTTPException(503, '事件记录暂时无法读取，请稍后重试；不能据此判断没有事件') from None


@router.get('/events')
async def event_list(
    start_date: str = Query(min_length=10, max_length=10),
    end_date: str = Query(min_length=10, max_length=10),
    window_hours: int = Query(default=24, ge=1, le=24),
    group: bool = True,
    include_backlog: bool = True,
    device: str | None = Query(default=None, max_length=200),
    risk: Literal['risk', 'unknown', 'ignored', 'benign'] | None = None,
    handling: Literal['open', 'closed', 'ignored', 'contained'] | None = None,
    assessment: Literal['risk_open', 'risk_closed', 'benign', 'insufficient'] | None = None,
    search: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0, le=1000),
    limit: int = Query(default=10, ge=1, le=100),
    user=Depends(require_user),
):
    from flocks.monitoring.event_list import list_events
    return await _event_list_read(list_events(user.id, COMPONENT_ID,
        start_date=start_date, end_date=end_date, window_hours=window_hours,
        group=group, include_backlog=include_backlog, device=device, risk=risk,
        handling=handling, assessment=assessment, search=search, offset=offset, limit=limit))


@router.get('/events/detail')
async def event_detail(event_key: str = Query(min_length=1, max_length=1000), user=Depends(require_user)):
    from flocks.monitoring.event_list import detail
    return await _event_list_read(detail(user.id, COMPONENT_ID, event_key))


async def _control(action, owner):
    from flocks.monitoring import diagnostics as diag
    operation = {'start_monitoring': 'start', 'pause_monitoring': 'pause'}.get(getattr(action, '__name__', ''), 'other')
    async with diag.trace_scope(owner, COMPONENT_ID, 'control:' + operation) as trace:
        try:
            with diag.span('control.action', control_action=operation):
                await action(owner)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception:
            raise HTTPException(500, '监测操作未完成，请刷新状态后重试') from None
        from flocks.server.routes.event import publish_event
        with diag.span('control.publish', control_action=operation):
            await publish_event('monitor.control.changed', {'owner': owner})
        with diag.span('control.snapshot', control_action=operation):
            response = await snapshot(owner, COMPONENT_ID, await resolve_day(owner))
        trace.outcome = 'ok'
        diag.event('control.result', control_action=operation, outcome='ok')
        return response


@router.get('/configuration')
async def monitoring_configuration(user=Depends(require_user)):
    from flocks.monitoring.configuration import snapshot
    try:
        return await snapshot(user.id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        raise HTTPException(503, '监测配置暂时无法读取，请稍后重试') from None


@router.put('/configuration')
async def save_monitoring_configuration(body: ConfigurationRequest, user=Depends(require_user)):
    from flocks.monitoring.configuration import configure
    try:
        result = await configure(user.id, body)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        raise HTTPException(503, '监测配置保存未完成，请刷新配置后核对') from None
    from flocks.server.routes.event import publish_event
    await publish_event('monitor.control.changed', {'owner': user.id})
    return result


@router.get('/mail')
async def mail_history(offset: int = Query(default=0, ge=0), tab: Literal['sent', 'received'] | None = None, user=Depends(require_user)):
    from flocks.monitoring.mailflow import history
    return await history(user.id, offset=offset, tab=tab)


@router.put('/mail/settings')
async def mail_settings(body: MailSettingsRequest, user=Depends(require_user)):
    from flocks.monitoring.mailflow import configure
    from flocks.monitoring import diagnostics as diag
    async def change(owner):
        async with diag.trace_scope(owner, COMPONENT_ID, 'mail-settings'):
            with diag.span('mail.configure'):
                await configure(owner, body)
    return await _control(change, user.id)


async def _manual_mail_action(action, owner):
    result = await _disposition_action(action)
    from flocks.monitoring.runtime import publish
    await publish('monitor.control.changed', {'owner': owner})
    return result


@router.post('/mail/notices/{notice_id}/manual-status')
async def manual_mail_status(notice_id: UUID, body: ManualMailRequest, user=Depends(require_user)):
    from flocks.monitoring.mail_manual import change
    return await _manual_mail_action(change(user.id, str(notice_id), body), user.id)


@router.post('/mail/notices/{notice_id}/retry')
async def retry_mail(notice_id: UUID, body: RetryMailRequest, user=Depends(require_user)):
    from flocks.monitoring.mail_retry import retry
    try:
        result = await retry(user.id, str(notice_id), body)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        raise HTTPException(503, '重试发送结果未确认，请刷新记录；重试请求应沿用原请求编号') from None
    from flocks.monitoring.runtime import publish
    await publish('monitor.control.changed', {'owner': user.id})
    return result


@router.post('/mail/notices/{notice_id}/manual-status/recheck')
async def recheck_manual_mail(notice_id: UUID, user=Depends(require_user)):
    from flocks.monitoring.mail_manual import recheck
    return await _manual_mail_action(recheck(user.id, str(notice_id)), user.id)


@router.put('/automatic-status')
async def automatic_status(body: AutomaticRequest, user=Depends(require_user)):
    from flocks.monitoring.automatic import configure
    async def change(owner):
        await configure(owner, body.enabled)
    return await _control(change, user.id)


@router.post('/start')
async def start(user=Depends(require_user)):
    from flocks.monitoring.lifecycle import start_monitoring
    return await _control(start_monitoring, user.id)


@router.post('/pause')
async def pause(user=Depends(require_user)):
    from flocks.monitoring.lifecycle import pause_monitoring
    return await _control(pause_monitoring, user.id)


class InvestigationEngineRequest(BaseModel):
    engine: Literal['agent-v1']


@router.put('/investigation-engine')
async def investigation_engine(body: InvestigationEngineRequest, user=Depends(require_user)):
    from flocks.monitoring.lifecycle import set_investigation_engine
    async def change(owner):
        await set_investigation_engine(owner, body.engine)
    return await _control(change, user.id)


@router.get('/state')
async def state(user=Depends(require_user)):
    day = await resolve_day(user.id)
    from flocks.hub import local
    record = local.get_record('component', COMPONENT_ID)
    if not record or not record.enabled:
        return {'owner': user.id, 'latest': None}
    latest = await rows('SELECT a.sequence,a.session_id,a.message_id,a.started_at,a.business_date,a.execution_id FROM monitor_attempts a JOIN monitor_installations i ON i.owner=a.owner AND i.scope=a.scope WHERE a.owner=? AND a.scope=? AND a.business_date=? AND i.installed=1 AND a.navigation_confirmed=1 ORDER BY a.sequence DESC LIMIT 1', (user.id, COMPONENT_ID, day))
    return {'owner': user.id, 'latest': latest[0] if latest else None}


@router.get('/reports/{day}', response_class=PlainTextResponse)
async def report(day: str, kind: str = 'timeline', user=Depends(require_user)):
    day = await resolve_day(user.id, day)
    if kind not in ('timeline', 'summary'):
        raise HTTPException(422, '报告类型必须为 timeline 或 summary')
    result = await rows('SELECT content,summary_content FROM monitor_reports WHERE owner=? AND scope=? AND business_date=?', (user.id, COMPONENT_ID, day))
    content = result[0]['summary_content' if kind == 'summary' else 'content'] if result else None
    if content is None:
        raise HTTPException(404, '当日报告尚未生成')
    if kind == 'timeline':
        content = newest_timeline_content(content)
    return PlainTextResponse(content, headers={'Content-Disposition': f'inline; filename="security-monitor-{day}.md"'})
