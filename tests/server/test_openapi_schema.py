"""
Regression tests for OpenAPI schema generation.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.server.routes.session import router as session_router


def test_session_router_openapi_schema_generates_successfully():
    """The session router should not break `/openapi.json` generation."""
    app = FastAPI()
    app.include_router(session_router, prefix="/api/session", tags=["Session"])

    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert "/api/session/{sessionID}/prompt_async" in schema["paths"]
    assert "/api/session/{sessionID}/status" in schema["paths"]
    operation = schema["paths"]["/api/session/{sessionID}/status"]["get"]
    assert operation["operationId"] == "session.statusById"
    response_schema = schema["components"]["schemas"]["SessionRuntimeStatusResponse"]
    assert response_schema["properties"]["lifecycleStatus"]["enum"] == ["active", "archived"]
