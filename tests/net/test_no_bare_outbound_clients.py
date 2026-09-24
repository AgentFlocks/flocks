"""Every outbound HTTP / WebSocket client in ``flocks/`` goes through the proxy policy.

The policy (``flocks/net``) only covers clients that are built through it — a
bare ``httpx.AsyncClient()`` added next year silently ignores the whitelist (and
SOCKS). This test walks the package's AST and fails on such calls unless the call
is visibly one of:

- routed: ``async_client()`` / ``sync_client()`` / ``make_session()`` / ``session_kwargs()``
  (``**session_kwargs(...)`` spread into ``aiohttp.ClientSession``), ``requests.*(..., proxies=...)``,
  ``websockets.connect(..., proxy=...)``;
- loopback-only: ``httpx.*(..., trust_env=False)``, or the line carries the marker
  ``# outbound: loopback`` (talks to this host) / ``# outbound: env`` (urllib-style
  fallback that follows the exported variables).

Two things to know when it complains about code that *is* routed: the marker has to
sit on the call's first line or the line above it, and ``transport=`` / ``connector=``
have to name the policy's own type right there (``transport=ProxyRoutingTransport()``,
not ``transport=t`` assigned earlier) — otherwise add the marker.

The ``build_opener`` rule matches the literal ``ProxyHandler({})`` on the call, so a
spaced-out ``ProxyHandler( {} )`` needs the marker as well.

Blind spots, by design (a guard that reads source can only see so much): ``getattr``
and other dynamic dispatch, ``hx = httpx`` value aliases, subclasses of a client,
and clients a third-party SDK builds inside itself (openai, anthropic, ... — those
follow the variables ``apply_process_environment`` exports).

Same idea as ``tests/test_pro_boundary.py``: a convention that CI enforces.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "flocks"
EXEMPT_DIRS = ("net",)  # the policy implementation itself

HTTPX_CALLS = {"AsyncClient", "Client", "get", "post", "put", "patch", "delete", "head", "options", "request", "stream"}
REQUESTS_CALLS = {"get", "post", "put", "patch", "delete", "head", "options", "request", "Session"}
MARKER = re.compile(r"#\s*outbound:\s*(loopback|env)\b")


def _import_aliases(tree: ast.AST) -> dict:
    """Local name → canonical dotted name, so ``from httpx import AsyncClient as C`` is still seen."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _bound_here(scope: ast.AST) -> set:
    """Names this scope binds itself: assignments, ``for``, ``with``, parameters.

    Nested functions are not descended into — each scope gets its own set, so a
    parameter named ``requests`` in one function does not silence ``requests.get()``
    in the next one.
    """
    names = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = scope.args
        names.update(
            arg.arg
            for group in (args.posonlyargs, args.args, args.kwonlyargs, [args.vararg], [args.kwarg])
            for arg in group
            if arg is not None
        )
    stack = list(ast.iter_child_nodes(scope))  # for a Lambda this is its args + body expression
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue  # its own scope
        targets = []
        if isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Constant) and node.value.value is None:
                # ``except ImportError: httpx = None`` is an availability fallback, not a
                # local of that name — the calls guarded by it are still the real client
                stack.extend(ast.iter_child_nodes(node))
                continue
            targets = node.targets
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [node.target]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [item.optional_vars for item in node.items if item.optional_vars]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _scope_shadows(tree: ast.AST) -> dict:
    """Call node → the names shadowed where it sits (its own scope plus the enclosing ones)."""
    shadows = {}

    def visit(scope: ast.AST, inherited: set) -> None:
        here = inherited | _bound_here(scope)
        stack = list(ast.iter_child_nodes(scope))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                visit(node, here)
                continue
            if isinstance(node, ast.Call):
                shadows[node] = here
            stack.extend(ast.iter_child_nodes(node))

    visit(tree, set())
    return shadows


def _root_name(node: ast.AST) -> str:
    """The identifier the attribute chain starts from (``a`` in ``a.b.c()``)."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _dotted(node: ast.AST, aliases: dict) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(aliases.get(node.id, node.id))
        return ".".join(reversed(parts))
    return ""


def _kwarg_names(call: ast.Call) -> set:
    return {kw.arg for kw in call.keywords if kw.arg}


def _spread_sources(call: ast.Call, source: str) -> str:
    return " ".join(ast.get_source_segment(source, kw.value) or "" for kw in call.keywords if kw.arg is None)


def _kwarg_source(call: ast.Call, source: str, arg: str) -> str:
    for kw in call.keywords:
        if kw.arg == arg:
            return ast.get_source_segment(source, kw.value) or ""
    return ""


POLICY_TRANSPORTS = ("ProxyRoutingTransport", "SyncProxyRoutingTransport")
POLICY_CONNECTORS = ("proxy_connector", "ProxyConnector", "session_kwargs")


def _classify(call: ast.Call, source: str, lines: list, aliases: dict, shadowed: set) -> str | None:
    """Return a complaint for a bare outbound client call, or None when it is fine."""
    if _root_name(call.func) in shadowed:
        return None  # a local of that name shadows the import (``requests = {...}``)
    name = _dotted(call.func, aliases)
    if not name:
        return None
    kwargs = _kwarg_names(call)
    line = lines[call.lineno - 1]
    above = lines[call.lineno - 2] if call.lineno >= 2 else ""
    marked = MARKER.search(line) or MARKER.search(above)

    if name.startswith("httpx.") and name.split(".", 1)[1] in HTTPX_CALLS:
        routed = any(t in _kwarg_source(call, source, "transport") for t in POLICY_TRANSPORTS) or "mounts" in kwargs
        if routed or _literal_false(call, "trust_env") or marked:
            return None
        return f"{name}(): use flocks.net.http.async_client()/sync_client(), or trust_env=False for a loopback target"
    if name in ("aiohttp.ClientSession", "aiohttp.client.ClientSession"):
        routed = "session_kwargs" in _spread_sources(call, source) or any(
            c in _kwarg_source(call, source, "connector") for c in POLICY_CONNECTORS
        )
        if routed or marked:
            return None
        return f"{name}(): use flocks.net.aio.make_session(target_url=...) or **session_kwargs(target_url=...)"
    if name == "urllib.request.build_opener" and "ProxyHandler({})" not in (ast.get_source_segment(source, call) or ""):
        if marked:
            return None
        return f"{name}(): the default opener follows HTTP_PROXY by name only; use build_opener(ProxyHandler({{}})) for loopback or sync_client()"
    if name.startswith("requests.") and name.split(".", 1)[1] in REQUESTS_CALLS:
        if "proxies" in kwargs or marked:
            return None
        return f"{name}(): pass proxies=flocks.net.requests_proxies(url)"
    if name in ("urllib.request.urlopen", "urlopen", "request.urlopen"):
        if marked:
            return None
        return f"{name}(): use flocks.net.http.sync_client() (urllib cannot speak SOCKS)"
    if name in ("websockets.connect", "websockets.asyncio.client.connect", "websockets.sync.client.connect"):
        if "proxy" in kwargs or marked:
            return None
        return f"{name}(): pass proxy=flocks.net.proxy_url_for(url)"
    return None


def _literal_false(call: ast.Call, arg: str) -> bool:
    for kw in call.keywords:
        if kw.arg == arg:
            return isinstance(kw.value, ast.Constant) and kw.value.value is False
    return False


def _scan(path: Path, show_root: Path | None = None) -> list:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
        return []
    lines = source.splitlines()
    try:
        shown = path.relative_to(show_root or PACKAGE.parent)
    except ValueError:
        shown = path
    found = []
    aliases = _import_aliases(tree)
    shadows = _scope_shadows(tree)  # per scope: a local of that name is not the import
    for node in ast.walk(tree):  # ast.walk is breadth-first: sort by line afterwards
        if isinstance(node, ast.Call):
            complaint = _classify(node, source, lines, aliases, shadows.get(node, frozenset()))
            if complaint:
                found.append((node.lineno, f"{shown}:{node.lineno}: {complaint}"))
    return [text for _, text in sorted(found)]


def scan_package(package: Path, exempt_dirs: tuple = (), show_root: Path | None = None) -> list:
    """Every complaint in ``package`` — also used by flockspro's copy of this test.

    ``show_root`` is what paths are printed relative to (defaults to the package's parent).
    """
    problems = []
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package)
        if rel.parts[0] in exempt_dirs:
            continue
        problems.extend(_scan(path, show_root or package.parent))
    return problems


COMPLAINT_HINT = (
    "bare outbound client(s) bypass network.proxy (whitelist + SOCKS); route them through flocks.net, "
    "or mark the call's first line (or the line above) `# outbound: loopback` when it only talks to this "
    "host / `# outbound: env` for an env-following fallback. transport= and connector= only count as "
    "routed when the policy's own type is visible on the call itself:\n  "
)


def test_outbound_clients_go_through_the_proxy_policy():
    problems = scan_package(PACKAGE, EXEMPT_DIRS)
    assert not problems, (
        COMPLAINT_HINT + "\n  ".join(problems)
    )


def _complaints(tmp_path, body: str) -> list:
    sample = tmp_path / "sample.py"
    sample.write_text(body, encoding="utf-8")
    return [p.split(": ", 1)[1].split("(")[0] for p in _scan(sample)]


def test_guard_catches_bare_clients_including_aliased_imports(tmp_path):
    assert _complaints(
        tmp_path,
        "import httpx, aiohttp, requests, websockets, urllib.request\n"
        "from httpx import AsyncClient as HC\n"
        "import httpx as _hx\n"
        "from websockets.sync.client import connect as ws_connect\n"
        "async def f(url):\n"
        "    async with httpx.AsyncClient(timeout=3) as c:\n"
        "        await c.get(url)\n"
        "    HC()\n"
        "    _hx.Client()\n"
        "    aiohttp.ClientSession()\n"
        "    aiohttp.ClientSession(trust_env=True, connector=aiohttp.TCPConnector(ssl=None))\n"
        "    httpx.AsyncClient(transport=httpx.AsyncHTTPTransport())\n"
        "    requests.get(url)\n"
        "    await websockets.connect(url)\n"
        "    ws_connect(url)\n"
        "    urllib.request.build_opener().open(url)\n",
    ) == [
        "httpx.AsyncClient",
        "httpx.AsyncClient",
        "httpx.Client",
        "aiohttp.ClientSession",
        "aiohttp.ClientSession",
        "httpx.AsyncClient",
        "requests.get",
        "websockets.connect",
        "websockets.sync.client.connect",
        "urllib.request.build_opener",
    ]


def test_guard_ignores_locals_that_shadow_an_import(tmp_path):
    assert _complaints(
        tmp_path,
        "import requests, aiohttp\n"
        "def f(session):\n"
        "    requests = {'get': None}\n"
        "    requests.get('x')\n"
        "    session.get('x')\n"
        "    aiohttp.client.ClientSession()\n",
    ) == ["aiohttp.client.ClientSession"]


def test_guard_shadowing_is_per_scope(tmp_path):
    """A local in one function must not silence the real thing next door, nor an ImportError fallback the file."""
    assert _complaints(
        tmp_path,
        "try:\n"
        "    import httpx\n"
        "except ImportError:\n"
        "    httpx = None\n"
        "import requests\n"
        "def a(requests):\n"
        "    requests.get('x')\n"
        "def b(url):\n"
        "    requests.get(url)\n"
        "    httpx.get(url)\n",
    ) == ["requests.get", "httpx.get"]


def test_guard_accepts_routed_and_marked_calls(tmp_path):
    assert (
        _complaints(
            tmp_path,
            "import httpx, aiohttp, requests, websockets, urllib.request\n"
            "from flocks.net.aio import session_kwargs, proxy_connector\n"
            "from flocks.net.http import ProxyRoutingTransport\n"
            "from flocks.net import proxy_url_for, requests_proxies\n"
            "async def f(url):\n"
            "    httpx.get(url, trust_env=False)\n"
            "    httpx.AsyncClient(transport=ProxyRoutingTransport())\n"
            "    aiohttp.ClientSession(**session_kwargs(target_url=url))\n"
            "    aiohttp.ClientSession(connector=proxy_connector(url))\n"
            "    requests.get(url, proxies=requests_proxies(url))\n"
            "    await websockets.connect(url, proxy=proxy_url_for(url))\n"
            "    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url)\n"
            "    aiohttp.ClientSession()  # outbound: loopback\n"
            "    # outbound: env\n"
            "    urllib.request.urlopen(url)\n",
        )
        == []
    )
