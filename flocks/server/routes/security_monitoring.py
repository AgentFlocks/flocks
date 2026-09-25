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
from flocks.monitoring.reports import snapshot

router = APIRouter(prefix='/monitoring/host-security-monitor')
from flocks.monitoring.disposition import DispositionRequest
from flocks.monitoring.automatic import AutomaticRequest


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
    from flocks.monitoring.disposition import confirm
    return await _disposition_action(confirm(user.id, body))


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
    try:
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


async def _control(action, owner):
    try:
        await action(owner)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        raise HTTPException(500, '监测操作未完成，请刷新状态后重试') from None
    from flocks.server.routes.event import publish_event
    await publish_event('monitor.control.changed', {'owner': owner})
    return await snapshot(owner, COMPONENT_ID, await resolve_day(owner))


from flocks.monitoring.mailflow import MailSettingsRequest


@router.get('/mail')
async def mail_history(offset: int = Query(default=0, ge=0), user=Depends(require_user)):
    from flocks.monitoring.mailflow import history
    return await history(user.id, offset=offset)


@router.put('/mail/settings')
async def mail_settings(body: MailSettingsRequest, user=Depends(require_user)):
    from flocks.monitoring.mailflow import configure
    from flocks.monitoring import diagnostics as diag
    async def change(owner):
        async with diag.trace_scope(owner, COMPONENT_ID, 'mail-settings'):
            with diag.span('mail.configure'):
                await configure(owner, body)
    return await _control(change, user.id)


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
    engine: Literal['rules', 'agent-v1']


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
    return PlainTextResponse(content, headers={'Content-Disposition': f'inline; filename="security-monitor-{day}.md"'})
