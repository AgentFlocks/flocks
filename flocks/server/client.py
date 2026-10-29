"""
Flocks client SDK, over the service's own HTTP API.

The ACP bridge (``flocks acp``) talks to a running Flocks service through
this client, exactly as the WebUI and the TUI do: the same routes, the same
authentication, the same per-user access rules.  Nothing runs in the client:
a prompt is accepted by the service, which streams its progress as events and
says when the turn is over; a cancel is the service's own abort.

Authentication, in this order:

* ``session_id`` -- a login session (the ``flocks_session`` cookie), so the
  calls carry that user's identity and permissions;
* ``username`` / ``password`` -- logged in on first use, giving a session;
* ``api_token`` -- the service's API token (local tooling on the machine
  that runs the service).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import quote

import httpx

from flocks.utils.log import Log


log = Log.create(service="flocks.client")

SESSION_COOKIE = "flocks_session"
DIRECTORY_HEADER = "x-flocks-directory"

#: How often a prompt's turn is checked for completion.
TURN_POLL_INTERVAL_S = 0.5
#: A turn's receipt says it is over (flocks.session.turn_receipt).
TURN_OVER = frozenset({"done", "failed", "removed"})
#: The errors a reply carries when its run was stopped.
ABORT_ERRORS = frozenset({"MessageAbortedError", "AbortedError", "CancelledError"})


class ServiceUnavailable(RuntimeError):
    """The Flocks service did not answer, or refused the credentials."""


class TurnFailed(RuntimeError):
    """The service ran the turn and it failed (its receipt says why)."""


class _Session(dict):
    """A session as the service returns it, readable as attributes (``.id``, ``.time.created``)."""

    def __getattr__(self, name: str) -> Any:
        try:
            value = self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc
        return _Session(value) if isinstance(value, dict) else value

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class _Service:
    """One HTTP connection to the service, with its credentials."""

    def __init__(
        self,
        base_url: str,
        *,
        api_token: Optional[str] = None,
        session_id: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_token = api_token  # secret-guard: allow 误报：参数传递，不是凭证值
        self._session_id = session_id
        self._username = username
        self._password = password
        self._transport = transport
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        self._login_lock: Optional[asyncio.Lock] = None
        #: Sessions whose abort the service accepted (stopped or requested).
        self.aborted: set[str] = set()

    def _http(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._client_loop is not loop:
            # A non-browser client: no cookies of its own beyond the login session.
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                transport=self._transport,
                timeout=self._timeout,
                trust_env=False,
                headers={"User-Agent": "flocks-acp"},
            )
            self._client_loop = loop
        return self._client

    async def _login(self) -> None:
        if self._session_id or not (self._username and self._password):
            return
        if self._login_lock is None:
            self._login_lock = asyncio.Lock()
        async with self._login_lock:
            if self._session_id:
                return
            response = await self._http().post(
                "/api/auth/login", json={"username": self._username, "password": self._password},
            )
            if response.status_code != 200:
                raise ServiceUnavailable(f"login refused by {self.base_url} ({response.status_code})")
            session_id = response.cookies.get(SESSION_COOKIE)
            if not session_id:
                raise ServiceUnavailable(f"{self.base_url} answered the login without a session")
            self._session_id = session_id

    def _auth(self) -> tuple[dict, dict]:
        if self._session_id:
            return {}, {SESSION_COOKIE: self._session_id}
        if self._api_token:
            return {"Authorization": f"Bearer {self._api_token}"}, {}
        return {}, {}

    async def request(self, method: str, path: str, *, directory: Optional[str] = None, **kwargs: Any) -> Any:
        """One call to the service; *directory* is the project it is about.

        The service reads the project from ``x-flocks-directory`` and falls
        back to its own working directory without one -- the editor's project
        must travel with every call that is about it.
        """
        await self._login()
        headers, cookies = self._auth()
        if directory:
            headers = {**headers, DIRECTORY_HEADER: quote(directory, safe="/")}
        try:
            response = await self._http().request(
                method, path, headers={**headers, **kwargs.pop("headers", {})}, cookies=cookies, **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(f"Flocks service at {self.base_url} did not answer: {exc}") from exc
        if response.status_code in (401, 403):
            raise ServiceUnavailable(f"{self.base_url} refused the credentials ({response.status_code}): {response.text[:200]}")
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """The service's event stream, one event at a time, until it ends."""
        await self._login()
        headers, cookies = self._auth()
        async with self._http().stream(
            "GET", "/api/event", headers={**headers, "Accept": "text/event-stream"}, cookies=cookies,
            timeout=httpx.Timeout(self._timeout, read=None),
        ) as response:
            if response.status_code in (401, 403):
                raise ServiceUnavailable(f"{self.base_url} refused the event stream ({response.status_code})")
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class SessionClient:
    """Sessions, prompts, commands and aborts."""

    def __init__(self, service: _Service):
        self._service = service

    async def create(self, title: str, directory: str) -> _Session:
        return _Session(await self._service.request("POST", "/api/session", json={"title": title}, directory=directory))

    async def get(self, session_id: str, directory: str) -> Optional[_Session]:
        try:
            return _Session(await self._service.request("GET", f"/api/session/{session_id}", directory=directory))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def messages(self, session_id: str, directory: str) -> List[Dict[str, Any]]:
        return list(await self._service.request("GET", f"/api/session/{session_id}/message", directory=directory) or [])

    async def _replies(self, session_id: str, directory: str, message_id: Optional[str]) -> List[Dict[str, Any]]:
        """The assistant replies to *message_id* (a turn's user message), oldest first.

        None when the turn left no user message, or it is gone (``/clear``).
        """
        if not message_id:
            return []
        found: List[Dict[str, Any]] = []
        cursor: Optional[str] = message_id
        while True:
            params: Dict[str, Any] = {"limit": 100, "page": "true", "after": cursor}
            try:
                page = await self._service.request(
                    "GET", f"/api/session/{session_id}/message", params=params, directory=directory,
                ) or {}
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return found
                raise
            items = page.get("items") or []
            found.extend(
                item.get("info") or {} for item in items
                if (item.get("info") or {}).get("role") == "assistant"
                and (item.get("info") or {}).get("parentID") == message_id
            )
            cursor = page.get("nextAfter")
            if not (cursor and page.get("hasNewer") and items):
                return found

    async def _reply(self, session_id: str, directory: str, reply_id: str) -> List[Dict[str, Any]]:
        """The one reply *reply_id* (none when it is gone)."""
        try:
            found = await self._service.request(
                "GET", f"/api/session/{session_id}/message/{reply_id}", directory=directory,
            ) or {}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        info = found.get("info") if isinstance(found, dict) else None
        return [info] if isinstance(info, dict) else []

    async def _wait_for_turn(self, session_id: str, directory: str, turn_id: str) -> str:
        """Until the service says this turn is over; how it ended.

        The service keeps a receipt per turn (flocks.session.turn_receipt),
        written by whichever worker runs it: nothing about the session as a
        whole -- idle, its newest message -- says when *this* turn is over.
        """
        while True:
            receipt = await self._service.request(
                "GET", f"/api/session/{session_id}/turn/{turn_id}", directory=directory,
            ) or {}
            state = receipt.get("state")
            if state in TURN_OVER:
                return await self._stop_reason(session_id, directory, receipt)
            if state == "lost":
                self._service.aborted.discard(session_id)
                raise ServiceUnavailable("the service stopped running this turn before it finished")
            await asyncio.sleep(TURN_POLL_INTERVAL_S)

    async def _stop_reason(self, session_id: str, directory: str, receipt: Dict[str, Any]) -> str:
        """``cancelled`` for a turn that was stopped: its own reply says so, or it never ran.

        A reply carrying an abort error is this turn's, whichever answer
        arrives first; an abort the service accepted counts only for a turn
        whose replies did not finish normally.  A turn that failed for any
        other reason raises TurnFailed with what its receipt says.
        """
        aborted = session_id in self._service.aborted
        self._service.aborted.discard(session_id)
        if receipt.get("state") == "removed" or receipt.get("error") == "cancelled":
            return "cancelled"
        if receipt.get("state") == "failed":
            if aborted:
                return "cancelled"
            raise TurnFailed(str(receipt.get("error") or "the turn failed"))
        if receipt.get("replyID"):
            # The reply that answered this turn, as the service saw it (with
            # messages taken together, the one to the latest of them).
            replies = await self._reply(session_id, directory, receipt["replyID"])
        else:
            replies = await self._replies(session_id, directory, receipt.get("messageID"))
        for reply in replies:
            error = reply.get("error") or {}
            if isinstance(error, dict) and error.get("name") in ABORT_ERRORS:
                return "cancelled"
        finished = any((reply.get("time") or {}).get("completed") and not reply.get("error") for reply in replies)
        if aborted and not finished:
            return "cancelled"
        return "end_turn"

    async def prompt(
        self,
        session_id: str,
        model: Dict[str, str],
        parts: List[Dict[str, Any]],
        agent: str,
        directory: str,
    ) -> str:
        """Send a prompt and wait for its turn to end; returns the stop reason.

        No agent or model given: the session's, as the service sets them.
        """
        self._service.aborted.discard(session_id)
        turn_id = uuid.uuid4().hex
        body: Dict[str, Any] = {"parts": parts, "turnID": turn_id}
        if agent:
            body["agent"] = agent
        if model and model.get("providerID") and model.get("modelID"):
            body["model"] = {"providerID": model["providerID"], "modelID": model["modelID"]}
        accepted = await self._service.request(
            "POST", f"/api/session/{session_id}/prompt_async", json=body, directory=directory,
        ) or {}
        return await self._wait_for_turn(session_id, directory, accepted.get("turnID") or turn_id)

    async def command(
        self,
        session_id: str,
        command: str,
        arguments: str,
        model: str,
        agent: str,
        directory: str,
        arguments_json: Optional[Any] = None,
    ) -> str:
        """Run a slash command in the session and wait for it; returns the stop reason."""
        self._service.aborted.discard(session_id)
        turn_id = uuid.uuid4().hex
        body: Dict[str, Any] = {"command": command, "arguments": arguments, "turnID": turn_id}
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model
        if arguments_json is not None:
            body["argumentsJson"] = arguments_json
        accepted = await self._service.request(
            "POST", f"/api/session/{session_id}/command", json=body, directory=directory,
        ) or {}
        return await self._wait_for_turn(session_id, directory, accepted.get("turnID") or turn_id)

    async def summarize(self, session_id: str, directory: str, provider_id: str, model_id: str) -> None:
        await self._service.request(
            "POST", f"/api/session/{session_id}/summarize", json={"providerID": provider_id, "modelID": model_id},
            directory=directory,
        )

    async def abort(self, session_id: str, directory: str) -> Dict[str, Any]:
        """Stop the session's run wherever it runs; what the abort came to.

        A prompt waiting on this session ends ``cancelled`` when its reply was
        stopped, or when the service stopped (or was asked to stop) the run
        and it did not finish; ``unconfirmed`` and ``not_running`` change
        nothing.
        """
        outcome = await self._service.request("POST", f"/api/session/{session_id}/abort", directory=directory) or {}
        if outcome.get("status") in {"stopped", "requested"}:
            self._service.aborted.add(session_id)
        return outcome


class ConfigClient:
    def __init__(self, service: _Service):
        self._service = service

    async def get(self, directory: str) -> Dict[str, Any]:
        return await self._service.request("GET", "/api/config", directory=directory) or {}

    async def providers(self, directory: str) -> List[Dict[str, Any]]:
        """Configured providers and their models, in the shape the ACP agent lists."""
        listed = await self._service.request("GET", "/api/provider", directory=directory)
        items = listed.get("all", []) if isinstance(listed, dict) else (listed or [])
        providers = []
        for item in items:
            if not isinstance(item, dict):
                continue
            models = item.get("models") or {}
            if isinstance(models, list):
                models = {model.get("id"): model for model in models if isinstance(model, dict)}
            providers.append({
                "id": item.get("id"),
                "name": item.get("name") or item.get("id"),
                "models": {
                    model_id: {"id": model_id, "name": (model or {}).get("name") or model_id, "providerID": item.get("id")}
                    for model_id, model in models.items()
                },
            })
        return providers


class AppClient:
    def __init__(self, service: _Service):
        self._service = service

    async def agents(self, directory: str = "") -> List[Dict[str, Any]]:
        agents = await self._service.request("GET", "/api/agent", directory=directory) or []
        return [
            {
                "name": agent.get("name"),
                "description": agent.get("description"),
                "mode": agent.get("mode"),
                "hidden": agent.get("hidden", False),
                "native": agent.get("native", False),
            }
            for agent in agents
            if isinstance(agent, dict)
        ]


class CommandClient:
    def __init__(self, service: _Service):
        self._service = service

    async def list(self, directory: str) -> List[Dict[str, Any]]:
        try:
            commands = await self._service.request("GET", "/command", directory=directory)
        except httpx.HTTPStatusError:
            commands = None
        listed = [
            {"name": item.get("name"), "description": item.get("description", "")}
            for item in (commands or [])
            if isinstance(item, dict) and item.get("name")
        ]
        return listed or [{"name": "compact", "description": "Compact the session"}]


class PermissionClient:
    def __init__(self, service: _Service):
        self._service = service

    async def reply(self, request_id: str, reply: str, directory: str) -> None:
        """Answer a permission the service asked: ``once``, ``always`` or ``reject``."""
        await self._service.request(
            "POST", f"/api/permission/{request_id}/reply",
            json={"allow": reply in ("once", "always"), "always": reply == "always"},
            directory=directory,
        )


class McpClient:
    def __init__(self, service: _Service):
        self._service = service

    async def add(self, directory: str, name: str, config: Dict[str, Any]) -> None:
        await self._service.request("POST", "/api/mcp", json={"name": name, "config": config}, directory=directory)


class GlobalClient:
    def __init__(self, service: _Service):
        self._service = service

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """Service events as ``{"payload": event}``; ends when the stream does.

        The caller reconnects (the ACP agent's subscription loop does): events
        published while it was away are not replayed, which is why a prompt's
        completion is read from the session's status, not from an event.
        """
        async for event in self._service.events():
            yield {"payload": event}


class FlocksClient:
    """
    Flocks SDK client over HTTP.

    Provides an interface similar to the TypeScript FlocksClient.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        api_token: Optional[str] = None,
        session_id: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self._base_url = base_url
        self._service = _Service(
            base_url, api_token=api_token, session_id=session_id,  # secret-guard: allow 误报：参数传递，不是凭证值
            username=username, password=password, transport=transport,
        )
        self.session = SessionClient(self._service)
        self.config = ConfigClient(self._service)
        self.app = AppClient(self._service)
        self.command = CommandClient(self._service)
        self.permission = PermissionClient(self._service)
        self.mcp = McpClient(self._service)
        self.global_ = GlobalClient(self._service)

    async def check(self) -> None:
        """Fail now, clearly, when the service cannot be used with these credentials."""
        try:
            response = await self._service._http().get("/api/health")
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(f"no Flocks service answered at {self._base_url}: {exc}") from exc
        if response.status_code != 200:
            raise ServiceUnavailable(f"Flocks service at {self._base_url} is not healthy ({response.status_code})")
        await self._service.request("GET", "/api/agent")

    async def close(self) -> None:
        await self._service.close()


__all__ = ["FlocksClient", "ServiceUnavailable"]
