import pytest

from flocks.knowledgebase.runtime import get_client, start_from_environment, stop


@pytest.fixture(autouse=True)
async def reset_client():
    await stop()
    yield
    await stop()


@pytest.mark.asyncio
async def test_startup_stays_disabled_and_does_not_require_a_remote(monkeypatch):
    monkeypatch.delenv("FLOCKS_KNOWLEDGEBASE_URL", raising=False)
    monkeypatch.delenv("FLOCKS_KNOWLEDGEBASE_API_TOKEN", raising=False)
    status = await start_from_environment(environment={})
    assert status["status"] == "disabled"
    assert get_client() is None


@pytest.mark.asyncio
async def test_configured_startup_does_not_open_a_remote_request():
    status = await start_from_environment(environment={
        "FLOCKS_KNOWLEDGEBASE_URL": "http://kb.local",
        "FLOCKS_KNOWLEDGEBASE_API_TOKEN": "t" * 32,
    })
    assert status["status"] == "configured"
    client = get_client()
    assert client is not None
    assert client._http.trust_env is False
