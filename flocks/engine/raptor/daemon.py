"""
Raptor Daemon — per-session hermes-agent tui_gateway subprocess lifecycle.

Design
------
One ``RaptorDaemon`` (= one tui_gateway subprocess) is kept alive per Flocks
session_id so hermes's prompt cache, checkpoints, and compaction work across
turns.

Storage policy
--------------
Flocks is the single source of truth for all session data (messages, tool calls,
results).  Hermes' own state directory is placed under Flocks' data root at
  ``~/.flocks/data/raptor/<session_id>/``
so hermes files (checkpoints, compaction cache) stay inside the Flocks home
directory and are never scattered under ``~/.hermes``.  MessageBridge writes
assistant responses back to Flocks SQLite after each turn.

LLM credentials
---------------
API key and base_url are read from ``~/.flocks/config/`` (``flocks.json`` +
``.secret.json``) and injected into the subprocess environment as standard
OpenAI-compatible vars (``OPENAI_API_KEY``, ``OPENAI_BASE_URL``).  No
credentials are stored inside the hermes home directory.

Lifecycle
---------
- Spawned lazily on the first ``RaptorEngine.run()`` for a session.
- Kept in ``RaptorDaemonManager._daemons`` (class-level dict).
- Destroyed when the Flocks session is closed or the daemon crashes.
- If the daemon crashes it is re-spawned and history is replayed from Flocks
  storage on the next turn (cold-restart path).

Environment
-----------
The daemon is spawned using the Python interpreter from the hermes-agent
virtualenv (``RAPTOR_HERMES_VENV`` env-var → ``<venv>/bin/python``).  If that
env-var is not set the subprocess falls back to the hermes-agent's own
``.venv/bin/python`` (auto-detected) or ``sys.executable``.

The hermes-agent source directory is taken from ``RAPTOR_HERMES_PATH``
(defaults to ``<this repo>/open_source/hermes-agent`` resolved relative to the
Flocks project root).
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flocks.utils.log import Log

log = Log.create(service="engine.raptor.daemon")

# Env-var that points to the hermes-agent checkout.
_ENV_HERMES_PATH = "RAPTOR_HERMES_PATH"
# Env-var that points to the hermes-agent virtualenv root (optional).
_ENV_HERMES_VENV = "RAPTOR_HERMES_VENV"


def _resolve_hermes_path() -> str:
    """Return the hermes-agent source directory."""
    if path := os.environ.get(_ENV_HERMES_PATH):
        return path
    # Default: sibling open_source/hermes-agent of the Flocks repo.
    # flocks/flocks/engine/raptor/daemon.py → up 5 levels → repo root
    this_dir = os.path.dirname(__file__)
    repo_root = os.path.normpath(os.path.join(this_dir, "..", "..", "..", "..", ".."))
    candidate = os.path.join(repo_root, "open_source", "hermes-agent")
    if os.path.isdir(candidate):
        return candidate
    raise RuntimeError(
        f"hermes-agent not found at {candidate!r}. "
        f"Set {_ENV_HERMES_PATH} to its path."
    )


def _resolve_python(hermes_path: str) -> str:
    """Return the Python interpreter to use for the hermes-agent subprocess.

    Priority:
    1. ``RAPTOR_HERMES_VENV`` env-var → ``<venv>/bin/python``
    2. ``<hermes_path>/.venv/bin/python``  (uv-managed venv, ≥3.11 required)
    3. ``sys.executable`` fallback (works in unified-venv dev setups)
    """
    if venv := os.environ.get(_ENV_HERMES_VENV):
        py = os.path.join(venv, "bin", "python")
        if os.path.isfile(py):
            return py
    # hermes-agent ships its own uv-managed venv
    local_py = os.path.join(hermes_path, ".venv", "bin", "python")
    if os.path.isfile(local_py):
        return local_py
    return sys.executable


# ── Flocks config helpers ──────────────────────────────────────────────────────

def _flocks_config_dir() -> Path:
    """Return ``~/.flocks/config`` (or override via FLOCKS_CONFIG_DIR)."""
    default = Path.home() / ".flocks" / "config"
    return Path(os.environ.get("FLOCKS_CONFIG_DIR", str(default)))


def _read_flocks_json() -> Dict[str, Any]:
    """Read ``~/.flocks/config/flocks.json``; return empty dict on any error."""
    p = _flocks_config_dir() / "flocks.json"
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_flocks_secrets() -> Dict[str, str]:
    """Read ``~/.flocks/config/.secret.json``; return empty dict on any error."""
    p = _flocks_config_dir() / ".secret.json"
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items() if isinstance(v, str)}
    except Exception:
        return {}


def _resolve_secret_ref(value: str, secrets: Dict[str, str]) -> str:
    """Expand ``{secret:KEY}`` placeholders using the flocks secret store."""
    import re
    def _sub(m: "re.Match[str]") -> str:
        return secrets.get(m.group(1), "")
    return re.sub(r"\{secret:([^}]+)\}", _sub, value)


def _resolve_flocks_llm_config() -> Dict[str, str]:
    """
    Read Flocks config and return LLM credentials for the hermes subprocess.

    Returns a dict with any of:
        ``openai_api_key``  — API key for the default LLM provider
        ``openai_base_url`` — Base URL (for OpenAI-compatible providers)
        ``hermes_model``    — Model name to use as hermes default

    Falls back gracefully: missing pieces are simply absent from the result.
    """
    cfg = _read_flocks_json()
    secrets = _read_flocks_secrets()

    result: Dict[str, str] = {}

    # --- default LLM model -------------------------------------------------
    default_models = cfg.get("default_models") or {}
    llm_default = default_models.get("llm") or {}
    if isinstance(llm_default, dict):
        provider_id = llm_default.get("provider_id", "")
        model_id = llm_default.get("model_id", "")
    else:
        provider_id = ""
        model_id = ""

    if model_id:
        result["hermes_model"] = model_id

    # --- provider options (base_url + api_key) -----------------------------
    if provider_id:
        providers = cfg.get("provider") or {}
        pconf = providers.get(provider_id) if isinstance(providers, dict) else {}
        if isinstance(pconf, dict):
            opts = pconf.get("options") or {}
            raw_url = opts.get("baseURL") or opts.get("base_url") or ""
            raw_key = opts.get("apiKey") or opts.get("api_key") or ""
            # Resolve {secret:...} references
            base_url = _resolve_secret_ref(raw_url, secrets)
            api_key = _resolve_secret_ref(raw_key, secrets)
            if base_url:
                result["openai_base_url"] = base_url
            if api_key:
                result["openai_api_key"] = api_key

    # --- fallback: look for provider_id_llm_key in secrets -----------------
    if not result.get("openai_api_key") and provider_id:
        fallback_key = secrets.get(f"{provider_id}_llm_key") or secrets.get(
            f"{provider_id}_api_key"
        )
        if fallback_key:
            result["openai_api_key"] = fallback_key

    return result


def _prepare_hermes_home(flocks_session_id: str, hermes_model: str = "") -> str:
    """
    Create and return the per-session hermes home directory.

    Path: ``~/.flocks/data/raptor/<session_id>/``

    Writes a minimal ``config.yaml`` so hermes uses the correct model and
    disables features that don't make sense in daemon mode (auto-title, TUI,
    checkpoint broadcast, etc.).
    """
    data_root = Path.home() / ".flocks" / "data" / "raptor" / flocks_session_id
    data_root.mkdir(parents=True, exist_ok=True)

    config_path = data_root / "config.yaml"
    if not config_path.exists():
        model_line = f"  default: {hermes_model}" if hermes_model else "  default: gpt-4o"
        config_yaml = (
            "# Auto-generated by Flocks Raptor engine — do not edit manually.\n"
            "model:\n"
            f"{model_line}\n"
            "\n"
            "# Disable TUI-only features in daemon mode.\n"
            "auto_title: false\n"
            "compact_threshold: 80\n"
        )
        config_path.write_text(config_yaml, encoding="utf-8")

    return str(data_root)


class RaptorDaemon:
    """
    One hermes-agent tui_gateway subprocess bound to a single Flocks session.

    Communication
    -------------
    - Write newline-delimited JSON requests to stdin.
    - Read newline-delimited JSON responses/events from stdout.
    - A background reader thread dispatches:
        * RPC responses  → resolves the matching ``Future`` in ``_pending``.
        * Event messages → calls the registered ``event_callback``.

    Thread-safety
    -------------
    ``send()`` is safe to call from any thread or asyncio task (uses a lock on
    stdin).  ``rpc()`` blocks the calling thread via a ``Future``.
    The ``run_rpc`` coroutine wraps ``rpc()`` for use from asyncio tasks.
    """

    def __init__(self, flocks_session_id: str) -> None:
        self.flocks_session_id = flocks_session_id
        self._hermes_session_id: Optional[str] = None  # set after session.create

        self._proc: Optional[subprocess.Popen] = None
        self._stdin_lock = threading.Lock()
        self._pending: Dict[str, Future] = {}
        self._pending_lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()

        # Registered callback for inbound events; replaced per turn.
        self._event_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._event_callback_lock = threading.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the tui_gateway subprocess and start the reader thread."""
        hermes_path = _resolve_hermes_path()
        python = _resolve_python(hermes_path)
        entry = os.path.join(hermes_path, "tui_gateway", "entry.py")

        # --- Flocks LLM credentials (read once at spawn time) ---------------
        llm_cfg = _resolve_flocks_llm_config()
        hermes_model = llm_cfg.get("hermes_model", "")

        # --- Per-session hermes home under ~/.flocks/data/raptor/ -----------
        hermes_home = _prepare_hermes_home(self.flocks_session_id, hermes_model)

        env = os.environ.copy()
        env["HERMES_PYTHON_SRC_ROOT"] = hermes_path
        # Redirect hermes state to Flocks-managed directory.
        env["HERMES_HOME"] = hermes_home
        # Inject LLM credentials as OpenAI-compatible env vars.
        if api_key := llm_cfg.get("openai_api_key"):
            env["OPENAI_API_KEY"] = api_key
        if base_url := llm_cfg.get("openai_base_url"):
            env["OPENAI_BASE_URL"] = base_url
        # Disable interactive/TUI features that assume a real terminal.
        env["HERMES_QUIET"] = "1"
        env["HERMES_NO_COLOR"] = "1"
        env["HERMES_NO_SOUND"] = "1"
        env["HERMES_DAEMON"] = "1"  # hint to hermes it runs as a daemon

        log.info("raptor.daemon.start", {
            "session_id": self.flocks_session_id,
            "hermes_path": hermes_path,
            "hermes_home": hermes_home,
            "hermes_model": hermes_model or "(not set)",
            "has_api_key": bool(llm_cfg.get("openai_api_key")),
            "has_base_url": bool(llm_cfg.get("openai_base_url")),
            "entry": entry,
        })

        self._proc = subprocess.Popen(
            [python, entry],
            cwd=hermes_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        self._stopped.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"raptor-reader-{self.flocks_session_id[:8]}",
            daemon=True,
        )
        self._reader_thread.start()

    def stop(self) -> None:
        """Terminate the subprocess and clean up."""
        self._stopped.set()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
        # Cancel all pending requests.
        with self._pending_lock:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("daemon stopped"))
            self._pending.clear()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── I/O ───────────────────────────────────────────────────────────────

    def send(self, msg: Dict[str, Any]) -> None:
        """Write a JSON-RPC message to the daemon's stdin."""
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._stdin_lock:
            if self._proc and self._proc.stdin:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()

    def rpc(self, method: str, params: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        """
        Send a JSON-RPC request and wait synchronously for the response.

        Returns the ``result`` dict on success.
        Raises ``RuntimeError`` on JSON-RPC error or timeout.
        """
        from .protocol import new_id
        rid = new_id()
        req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        fut: Future = Future()
        with self._pending_lock:
            self._pending[rid] = fut
        self.send(req)
        try:
            result = fut.result(timeout=timeout)
        except TimeoutError:
            raise RuntimeError(f"RPC timeout: {method}")
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(f"RPC error {method}: {result['error']}")
        return result

    async def run_rpc(
        self, method: str, params: Dict[str, Any], timeout: float = 30.0
    ) -> Dict[str, Any]:
        """Asyncio-friendly wrapper around ``rpc()``."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self.rpc(method, params, timeout)
        )

    # ── Event callback registration ────────────────────────────────────────

    def set_event_callback(self, cb: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        with self._event_callback_lock:
            self._event_callback = cb

    # ── Reader loop (background thread) ───────────────────────────────────

    def _reader_loop(self) -> None:
        """
        Read newline-delimited JSON from stdout and dispatch:
        - If it has an "id" that matches a pending request → resolve the Future.
        - If it has method=="event" → call the event_callback.
        """
        assert self._proc and self._proc.stdout
        for raw_line in self._proc.stdout:
            if self._stopped.is_set():
                break
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                msg = json.loads(raw_line)
            except json.JSONDecodeError:
                log.debug("raptor.daemon.invalid_json", {"line": raw_line[:200]})
                continue

            # RPC response
            if rid := msg.get("id"):
                with self._pending_lock:
                    fut = self._pending.pop(rid, None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_result({"error": msg["error"]})
                    else:
                        fut.set_result(msg.get("result", {}))
                continue

            # Event notification
            if msg.get("method") == "event":
                with self._event_callback_lock:
                    cb = self._event_callback
                if cb:
                    try:
                        cb(msg.get("params", {}))
                    except Exception as exc:
                        log.debug("raptor.daemon.event_callback_error", {"error": str(exc)})

        log.info("raptor.daemon.reader_exited", {"session_id": self.flocks_session_id})


# ── Manager ───────────────────────────────────────────────────────────────────

class RaptorDaemonManager:
    """
    Class-level registry of per-session daemons.

    Usage::

        daemon = await RaptorDaemonManager.get_or_create(session_id)
        daemon.send(...)
    """

    _daemons: Dict[str, RaptorDaemon] = {}
    _lock = threading.Lock()

    @classmethod
    def get_or_create(cls, flocks_session_id: str) -> RaptorDaemon:
        """Return the existing daemon for this session, or spawn a new one."""
        with cls._lock:
            daemon = cls._daemons.get(flocks_session_id)
            if daemon and daemon.alive:
                return daemon
            # Dead or missing — create fresh.
            if daemon:
                log.warning("raptor.daemon.crashed", {"session_id": flocks_session_id})
            daemon = RaptorDaemon(flocks_session_id)
            daemon.start()
            cls._daemons[flocks_session_id] = daemon
            return daemon

    @classmethod
    def release(cls, flocks_session_id: str) -> None:
        """Stop and remove the daemon for this session."""
        with cls._lock:
            daemon = cls._daemons.pop(flocks_session_id, None)
        if daemon:
            daemon.stop()

    @classmethod
    def stop_all(cls) -> None:
        """Stop all running daemons (called on Flocks server shutdown)."""
        with cls._lock:
            daemons = list(cls._daemons.values())
            cls._daemons.clear()
        for d in daemons:
            d.stop()
