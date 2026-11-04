import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".flocks" / "plugins" / "skills" / "tdp-use" / "scripts"


@pytest.fixture
def api_client_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("THREATBOOK_BASE_URL", "https://tdp.example.com")
    monkeypatch.setenv("THREATBOOK_COOKIE_FILE", str(SCRIPTS_DIR / "auth-state.json"))

    sys.path.insert(0, str(SCRIPTS_DIR))
    sys.modules.pop("api_client", None)
    sys.modules.pop("config", None)
    module = importlib.import_module("api_client")

    yield module

    sys.modules.pop("api_client", None)
    sys.modules.pop("config", None)
    try:
        sys.path.remove(str(SCRIPTS_DIR))
    except ValueError:
        pass


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Dict[str, Any] | None = None, text_data: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text_data

    def json(self) -> Dict[str, Any]:
        if self._json_data is None:
            raise json.JSONDecodeError("invalid json", self.text, 0)
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.headers: Dict[str, str] = {}
        self.request_calls: list[Dict[str, Any]] = []

    def request(self, **kwargs: Any) -> FakeResponse:
        self.request_calls.append(kwargs)
        return self.response

def test_request_uses_requests_session_and_cookie_header(
    api_client_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "sessionid", "value": "cookie-123", "domain": "tdp.example.com"},
                    {"name": "ignored", "value": "other", "domain": "other.example.com"},
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    fake_session = FakeSession(FakeResponse(status_code=200, json_data={"response_code": 0, "data": {"items": []}}))
    monkeypatch.setattr(api_client_module.requests, "Session", lambda: fake_session)

    client = api_client_module.ThreatBookClient(
        base_url="https://tdp.example.com",
        cookie_file=state_file,
    )
    result = client.request(
        "POST",
        "log/searchBySql",
        data={"sql": "threat.level = 'attack'"},
        params={"page": 1},
    )

    assert result["response_code"] == 0
    assert fake_session.headers["Accept"] == api_client_module.DEFAULT_HEADERS["Accept"]
    assert fake_session.headers["Cookie"] == "sessionid=cookie-123"
    assert len(fake_session.request_calls) == 1
    assert fake_session.request_calls[0]["method"] == "POST"
    assert fake_session.request_calls[0]["url"] == "https://tdp.example.com/api/web/log/searchBySql"
    assert fake_session.request_calls[0]["json"] == {"sql": "threat.level = 'attack'"}
    assert fake_session.request_calls[0]["params"] == {"page": 1}
    assert fake_session.request_calls[0]["timeout"] == api_client_module.TIMEOUT
    assert fake_session.request_calls[0]["verify"] == api_client_module.SSL_VERIFY


def test_request_refreshes_cookie_header_when_state_changes(
    api_client_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(
        json.dumps({"cookies": [{"name": "sessionid", "value": "old", "domain": "tdp.example.com"}], "origins": []}),
        encoding="utf-8",
    )

    fake_session = FakeSession(FakeResponse(status_code=200, json_data={"response_code": 0}))
    monkeypatch.setattr(api_client_module.requests, "Session", lambda: fake_session)

    client = api_client_module.ThreatBookClient(
        base_url="https://tdp.example.com",
        cookie_file=state_file,
    )

    assert fake_session.headers["Cookie"] == "sessionid=old"

    state_file.write_text(
        json.dumps({"cookies": [{"name": "sessionid", "value": "new", "domain": "tdp.example.com"}], "origins": []}),
        encoding="utf-8",
    )

    client.request("GET", "api/web/health")
    assert fake_session.headers["Cookie"] == "sessionid=new"
    assert fake_session.request_calls[0]["url"] == "https://tdp.example.com/api/web/health"
