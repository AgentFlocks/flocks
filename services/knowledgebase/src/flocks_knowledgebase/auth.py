import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import KBError

_bearer = HTTPBearer(auto_error=False)


async def require_service(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Accept only the deployment service token. Callers are not a product role."""
    expected = request.app.state.settings.api_token.get_secret_value()
    actual = credentials.credentials if credentials is not None else ""
    if not actual or not secrets.compare_digest(actual.encode(), expected.encode()):
        raise KBError(401, "unauthorized", "A valid service credential is required.")
