"""Routes of the Flocks Pro page (flocks/server/routes/enterprise_upgrade.py).

Case list (编号 | 场景):
  ER1 administrators only, every route
  ER2 the situation, and Console's refusal as a 409 with its words
  ER3 the start streams its steps as server-sent events, ending in done with the state, or in
      error with the reason; the progress route reads the last run
  ER4 an install the enterprise core runs says so instead of offering anything
  ER5 the application for a licence: the form checked as the page of 2026.10.19 checked it, an
      application id that is not one refused before anything runs, refusals as a 409 with their
      words, and taking a licence answers with the machine's new situation
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.server.routes import enterprise_upgrade as routes
from flocks.updater import enterprise, enterprise_application

pytestmark = pytest.mark.asyncio


def _admin(_request) -> AuthUser:
    return AuthUser(id="usr_admin", username="admin", role="admin", status="active", must_reset_password=False)


def _member(_request):
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")


def events(body: str) -> list[dict]:
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


async def test_er1_administrators_only(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "require_admin", _member)
    for method, path in (("get", ""), ("post", "/check"), ("post", "/start"), ("get", "/progress"),
                         ("get", "/applications"), ("post", "/applications"),
                         ("post", "/applications/req_1/withdraw"), ("post", "/applications/req_1/take")):
        resp = await getattr(client, method)(f"/api/enterprise-upgrade{path}")
        assert resp.status_code == status.HTTP_403_FORBIDDEN, path


async def test_er2_the_situation_and_consoles_refusal(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "require_admin", _admin)
    monkeypatch.setattr(enterprise, "situation", lambda: {"available": True, "previous": {"license_id": "lic-old-7"}})
    resp = await client.get("/api/enterprise-upgrade")
    assert resp.status_code == 200 and resp.json()["previous"]["license_id"] == "lic-old-7"

    async def refuse():
        raise enterprise.EnterpriseUpgradeError("Console 还没有给授权 lic-old-7 的新企业版安装包")

    monkeypatch.setattr(enterprise, "available", lambda: True)
    monkeypatch.setattr(enterprise, "fetch_offer", refuse)
    resp = await client.post("/api/enterprise-upgrade/check")
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "lic-old-7" in json.dumps(resp.json(), ensure_ascii=False)

    async def offer():
        return enterprise.InstallerOffer(url="https://c/r.run", sha256="a" * 64, release_id="r1", version="2026.11.5")

    monkeypatch.setattr(enterprise, "fetch_offer", offer)
    resp = await client.post("/api/enterprise-upgrade/check")
    assert resp.status_code == 200 and resp.json()["offer"]["filename"] == "flockspro-r1.run"


async def test_er3_the_start_streams_its_steps(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "require_admin", _admin)
    monkeypatch.setattr(enterprise, "available", lambda: True)

    async def upgrade(progress):
        await progress({"stage": "downloading", "version": "2026.11.5"})
        # Byte counts within the same second as the last event: the page needs about one a second.
        await progress({"stage": "downloading", "received": 1, "total": 2})
        await progress({"stage": "downloading", "received": 2, "total": 2})
        await progress({"stage": "downloaded", "installer": "/x/flockspro-r1.run"})
        return {"phase": "installing", "pid": 42}

    monkeypatch.setattr(enterprise, "upgrade", upgrade)
    resp = await client.post("/api/enterprise-upgrade/start")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    got = events(resp.text)
    assert [e["stage"] for e in got] == ["downloading", "downloaded", "done"]
    assert got[0] == {"stage": "downloading", "version": "2026.11.5"}
    assert got[-1]["state"] == {"phase": "installing", "pid": 42}

    async def refused(progress):
        raise enterprise.EnterpriseUpgradeError("下载的安装包校验不对")

    monkeypatch.setattr(enterprise, "upgrade", refused)
    got = events((await client.post("/api/enterprise-upgrade/start")).text)
    assert got == [{"stage": "error", "message": "下载的安装包校验不对"}]

    monkeypatch.setattr(enterprise, "read_state", lambda: {"phase": "failed", "exit_code": 3})
    resp = await client.get("/api/enterprise-upgrade/progress")
    assert resp.json() == {"upgrade": {"phase": "failed", "exit_code": 3}}


async def test_er4_an_enterprise_install_offers_nothing(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "require_admin", _admin)
    monkeypatch.setattr(enterprise, "available", lambda: False)
    resp = await client.post("/api/enterprise-upgrade/check")
    assert resp.status_code == status.HTTP_409_CONFLICT
    got = events((await client.post("/api/enterprise-upgrade/start")).text)
    assert got == [{"stage": "error", "message": "这台机器已经是企业版"}]


async def test_er5_the_application_for_a_licence(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes, "require_admin", _admin)
    sent: list[tuple[dict, str]] = []

    async def apply(form, node_id):
        sent.append((form, node_id))
        return {"request_id": "req_1", "status": "pending"}

    monkeypatch.setattr(enterprise_application, "apply", apply)
    form = {"license_type": "poc", "company": " Acme ", "applicant_name": "Li Lei",
            "applicant_email": "li@acme.example", "applicant_phone": "+86 138 0000 0000"}
    resp = await client.post("/api/enterprise-upgrade/applications", json=form)
    assert resp.status_code == 200 and resp.json()["application"]["request_id"] == "req_1"
    assert sent[0][0]["company"] == "Acme" and sent[0][1] == "admin"

    for field, bad in (("applicant_email", "li@acme"), ("applicant_phone", "12-34"), ("applicant_phone", "1+3800000000"),
                       ("company", "   "), ("license_type", "forever")):
        resp = await client.post("/api/enterprise-upgrade/applications", json={**form, field: bad})
        assert resp.status_code == 422, (field, bad)
    assert len(sent) == 1

    async def refuse(*_args):
        raise enterprise.EnterpriseUpgradeError("申请要先登录 Console：点「登录 Console」后再提交")

    monkeypatch.setattr(enterprise_application, "withdraw", refuse)
    resp = await client.post("/api/enterprise-upgrade/applications/req_1/withdraw")
    assert resp.status_code == status.HTTP_409_CONFLICT and "登录 Console" in json.dumps(resp.json(), ensure_ascii=False)
    # Not an application id: refused before anything runs.
    resp = await client.post("/api/enterprise-upgrade/applications/..%2Fx/withdraw")
    assert resp.status_code in (404, 422)

    async def take(request_id):
        return {"request_id": request_id, "status": "activated", "license_id": "lic_1"}

    monkeypatch.setattr(enterprise_application, "take", take)
    monkeypatch.setattr(enterprise, "situation", lambda: {"available": True, "previous": {"license_id": "lic_1"}})
    resp = await client.post("/api/enterprise-upgrade/applications/req_1/take")
    assert resp.status_code == 200
    assert resp.json()["application"]["license_id"] == "lic_1" and resp.json()["situation"]["previous"]["license_id"] == "lic_1"

    async def listing():
        return {"items": [{"request_id": "req_1", "status": "pending"}], "note": None}

    monkeypatch.setattr(enterprise_application, "applications", listing)
    resp = await client.get("/api/enterprise-upgrade/applications")
    assert resp.status_code == 200 and resp.json()["items"][0]["status"] == "pending"
