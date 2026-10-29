"""``GET /vcs`` must never hang on a stuck ``git``."""

from __future__ import annotations

import subprocess

import pytest

from flocks.server.routes import vcs as vcs_routes


@pytest.mark.asyncio
async def test_vcs_git_subprocess_has_timeout_and_falls_back(monkeypatch, tmp_path):
    seen: list[dict] = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(vcs_routes.subprocess, "run", fake_run)
    response = await vcs_routes.get_vcs(directory=str(tmp_path))

    assert response.type is None and response.branch is None and response.dirty is False
    assert seen and all(k.get("timeout") == vcs_routes._GIT_TIMEOUT_SECONDS for k in seen)
