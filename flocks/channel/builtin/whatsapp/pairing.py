"""QR pairing helpers for the WhatsApp channel."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from flocks.utils.log import Log

from .bridge_runtime import ensure_bridge_deps
from .config import default_bridge_dir, default_session_path, find_executable

log = Log.create(service="channel.whatsapp.pairing")


@dataclass
class PairingSession:
    id: str
    session_path: Path
    process: asyncio.subprocess.Process
    qr: Optional[str] = None
    status: str = "starting"
    error: Optional[str] = None
    user: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    _reader_task: Optional[asyncio.Task] = field(default=None, repr=False)


_sessions: dict[str, PairingSession] = {}
_active_session_paths: set[str] = set()


def _session_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path.expanduser())


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _cleanup_pairings(ttl_seconds: int = 600) -> None:
    now = time.time()
    stale = [
        pairing_id
        for pairing_id, pairing in _sessions.items()
        if pairing.finished_at is not None and now - pairing.finished_at > ttl_seconds
    ]
    for pairing_id in stale:
        _sessions.pop(pairing_id, None)


def _release_session(pairing: PairingSession) -> None:
    _active_session_paths.discard(_session_key(pairing.session_path))
    pairing.finished_at = pairing.finished_at or time.time()


def _backup_session_dir(session_path: Path) -> Optional[Path]:
    if not session_path.exists() or not any(session_path.iterdir()):
        return None
    backup_path = session_path.with_name(f"{session_path.name}.backup.{int(time.time())}.{uuid.uuid4().hex[:8]}")
    shutil.move(str(session_path), str(backup_path))
    log.info("whatsapp.pairing.session_backed_up", {
        "session_path": str(session_path),
        "backup_path": str(backup_path),
    })
    return backup_path


async def _read_pair_output(pairing: PairingSession) -> None:
    assert pairing.process.stdout is not None
    while True:
        line = await pairing.process.stdout.readline()
        if not line:
            break
        raw = line.decode("utf-8", errors="replace").strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = str(event.get("event") or "")
        if name == "qr":
            pairing.qr = str(event.get("qr") or "")
            pairing.status = "qr"
        elif name == "connected":
            pairing.user = event.get("user") if isinstance(event.get("user"), dict) else None
            pairing.status = "connected"
        elif name == "error":
            pairing.error = str(event.get("error") or "WhatsApp pairing failed")
            pairing.status = "error"
        elif name == "disconnected" and pairing.status not in {"qr", "connected"}:
            pairing.status = "starting"

    code = await pairing.process.wait()
    if pairing.status == "connected" and code == 0:
        pairing.status = "complete"
    elif pairing.status not in {"complete", "connected", "error", "cancelled"}:
        pairing.status = "error"
        pairing.error = f"WhatsApp pairing bridge exited with code {code}"
    _release_session(pairing)


async def start_pairing(
    *,
    session_path: Optional[str] = None,
    bridge_dir: Optional[str] = None,
    reset_session: bool = False,
) -> PairingSession:
    _cleanup_pairings()
    node = find_executable("node")
    if not node:
        raise RuntimeError("Node.js is required for WhatsApp QR pairing")

    bridge_root = Path(bridge_dir).expanduser() if bridge_dir else default_bridge_dir()
    script = bridge_root / "bridge.js"
    if not script.exists():
        raise RuntimeError(f"WhatsApp bridge script not found: {script}")
    await ensure_bridge_deps(bridge_root)

    sess = Path(session_path).expanduser() if session_path else default_session_path()
    key = _session_key(sess)
    if key in _active_session_paths:
        raise RuntimeError("WhatsApp pairing is already running for this session")
    pid_file = sess / "bridge.pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip().splitlines()[0])
        if _pid_exists(pid):
            raise RuntimeError("WhatsApp channel is already running for this session; stop it before pairing")
    except FileNotFoundError:
        pass
    except ValueError:
        pass

    if reset_session:
        _backup_session_dir(sess)
    sess.mkdir(parents=True, exist_ok=True)

    pairing_id = uuid.uuid4().hex
    env = os.environ.copy()
    env.setdefault("FLOCKS_WHATSAPP_PAIR_TIMEOUT_MS", "120000")

    proc = await asyncio.create_subprocess_exec(
        node,
        str(script),
        "--pair-only",
        "--pair-json",
        "--session",
        str(sess),
        cwd=str(bridge_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    _active_session_paths.add(key)
    pairing = PairingSession(
        id=pairing_id,
        session_path=sess,
        process=proc,
    )
    pairing._reader_task = asyncio.create_task(_read_pair_output(pairing))
    _sessions[pairing_id] = pairing
    log.info("whatsapp.pairing.started", {"pairing_id": pairing_id})
    return pairing


def get_pairing(pairing_id: str) -> Optional[PairingSession]:
    _cleanup_pairings()
    return _sessions.get(pairing_id)


async def cancel_pairing(pairing_id: str) -> bool:
    pairing = _sessions.pop(pairing_id, None)
    if pairing is None:
        return False
    pairing.status = "cancelled"
    _release_session(pairing)
    if pairing.process.returncode is None:
        pairing.process.terminate()
        try:
            await asyncio.wait_for(pairing.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            pairing.process.kill()
    if pairing._reader_task:
        pairing._reader_task.cancel()
    return True
