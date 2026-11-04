import json
import socket
import threading
import time

import httpx
import pytest

from flocks.updater import handoff_progress


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _statuses(snapshot: dict) -> dict[str, str]:
    return {step["id"]: step["status"] for step in snapshot["steps"]}


def test_progress_marks_finished_and_skipped_steps_when_a_later_step_starts() -> None:
    changes: list[dict] = []
    progress = handoff_progress.UpgradeProgress(
        ["stop", "source_replace", "check_runtime", "sync_dependencies", "build_webui", "finalize", "start"],
        on_change=changes.append,
    )

    progress.start("stop")
    progress.start("source_replace")
    progress.start("sync_dependencies")  # check_runtime never ran (no WebUI in this tree)

    snapshot = progress.snapshot()
    assert snapshot["phase"] == "running"
    assert snapshot["step"] == "sync_dependencies"
    assert _statuses(snapshot) == {
        "stop": "done",
        "source_replace": "done",
        "check_runtime": "skipped",
        "sync_dependencies": "running",
        "build_webui": "pending",
        "finalize": "pending",
        "start": "pending",
    }
    assert [change["step"] for change in changes] == ["stop", "source_replace", "sync_dependencies"]


def test_progress_failure_keeps_step_and_hides_error_unless_asked() -> None:
    progress = handoff_progress.UpgradeProgress(["stop", "build_webui", "start"])
    progress.start("stop")
    progress.start("build_webui")

    progress.fail("Frontend build failed: npm ERR! code ELIFECYCLE")

    snapshot = progress.snapshot()
    assert snapshot["phase"] == "failed"
    assert snapshot["failed_step"] == "build_webui"
    assert snapshot["error"].startswith("Frontend build failed")
    assert _statuses(snapshot) == {"stop": "done", "build_webui": "failed", "start": "pending"}
    assert "error" not in progress.snapshot(include_error=False)


def test_progress_finish_marks_running_step_done() -> None:
    progress = handoff_progress.UpgradeProgress(["start"])
    progress.start("start")

    progress.finish()

    assert progress.snapshot()["phase"] == "done"
    assert _statuses(progress.snapshot()) == {"start": "done"}


def test_summarize_error_masks_url_credentials_and_truncates() -> None:
    error = "npm ERR! 401 https://deploy:s3cr3t@registry.example.com/-/npm\n" + "x" * 2000

    summary = handoff_progress.summarize_error(error)

    assert "s3cr3t" not in summary
    assert "https://***@registry.example.com" in summary
    assert "\n" not in summary
    assert len(summary) == handoff_progress.ERROR_MAX_CHARS


def test_responder_serves_progress_and_only_shares_errors_with_the_token() -> None:
    port = _free_port()
    progress = handoff_progress.UpgradeProgress(["stop", "build_webui", "start"])
    progress.start("build_webui")
    responder = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok-123")
    assert responder.start() is True
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5.0) as client:
            running = client.get("/api/health")
            progress.fail("npm run build exited with code 1")
            anonymous = client.get("/api/health")
            assert not responder.failure_delivered.is_set()
            trusted = client.get("/", headers={handoff_progress.UPGRADE_PROGRESS_TOKEN_HEADER: "tok-123"})
            wrong = client.get("/api/health", headers={handoff_progress.UPGRADE_PROGRESS_TOKEN_HEADER: "nope"})
    finally:
        responder.stop()

    assert running.status_code == 503
    assert running.headers["content-type"].startswith("application/json")
    assert running.json()["status"] == "upgrading"
    assert running.json()["upgrade"]["step"] == "build_webui"
    assert anonymous.json()["upgrade"]["phase"] == "failed"
    assert "error" not in anonymous.json()["upgrade"]
    assert "error" not in wrong.json()["upgrade"]
    assert trusted.json()["upgrade"]["error"] == "npm run build exited with code 1"
    assert responder.failure_delivered.is_set()


def test_responder_releases_the_port_on_stop_and_skips_a_busy_port() -> None:
    port = _free_port()
    progress = handoff_progress.UpgradeProgress(["start"])
    responder = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok")
    assert responder.start() is True
    busy = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok")
    assert busy.start() is False

    responder.stop()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_responder_lets_clients_close_first_so_the_service_can_bind_the_port() -> None:
    port = _free_port()
    progress = handoff_progress.UpgradeProgress(["build_webui"])
    progress.start("build_webui")
    responder = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok")
    assert responder.start() is True
    for _ in range(3):
        with httpx.Client(trust_env=False, timeout=5.0) as client:
            response = client.get(f"http://127.0.0.1:{port}/api/health")
        assert response.status_code == 503
        assert response.headers["connection"] == "close"

    responder.stop()

    # `flocks start` probes the port without SO_REUSEADDR on Windows (and where lsof is
    # missing); connections closed from the responder's side would sit in TIME_WAIT on
    # the port and fail that bind for minutes.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", port))


def test_linger_stops_shortly_after_the_dialog_read_the_failure() -> None:
    port = _free_port()
    progress = handoff_progress.UpgradeProgress(["build_webui"])
    progress.start("build_webui")
    progress.fail("boom")
    responder = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok")
    assert responder.start() is True

    def read_failure() -> None:
        time.sleep(0.2)
        httpx.get(
            f"http://127.0.0.1:{port}/api/health",
            headers={handoff_progress.UPGRADE_PROGRESS_TOKEN_HEADER: "tok"},
            trust_env=False,
            timeout=5.0,
        )

    reader = threading.Thread(target=read_failure)
    reader.start()
    started = time.monotonic()
    responder.linger_for_failure(timeout=30.0, grace=0.1)
    reader.join(timeout=5.0)

    assert time.monotonic() - started < 5.0
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://127.0.0.1:{port}/api/health", trust_env=False, timeout=1.0)


def test_linger_gives_up_when_nobody_reads_the_failure() -> None:
    port = _free_port()
    progress = handoff_progress.UpgradeProgress(["build_webui"])
    progress.fail("boom", step="build_webui")
    responder = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok")
    assert responder.start() is True

    started = time.monotonic()
    responder.linger_for_failure(timeout=0.3, grace=0.1)

    assert 0.25 <= time.monotonic() - started < 3.0
    assert json.loads(json.dumps(progress.snapshot()))["phase"] == "failed"


def _recv_some(sock: socket.socket) -> bytes:
    chunk = sock.recv(4096)
    if not chunk:
        raise EOFError("connection closed before the whole response arrived")
    return chunk


def _read_one_response(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        data += _recv_some(sock)
    head, _, body = data.partition(b"\r\n\r\n")
    length = int(next(line.split(b":")[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length")))
    while len(body) < length:
        body += _recv_some(sock)
    return body


def test_stop_waits_for_a_client_that_closes_late_before_freeing_the_port() -> None:
    port = _free_port()
    progress = handoff_progress.UpgradeProgress(["build_webui"])
    progress.start("build_webui")
    responder = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok")
    assert responder.start() is True
    client = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    client.sendall(b"GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert json.loads(_read_one_response(client))["status"] == "upgrading"
    closer = threading.Timer(0.8, client.close)
    closer.start()

    started = time.monotonic()
    responder.stop()

    assert time.monotonic() - started >= 0.7
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
    closer.join()


def test_stop_gives_up_on_a_client_that_keeps_the_connection_busy() -> None:
    port = _free_port()
    progress = handoff_progress.UpgradeProgress(["build_webui"])
    responder = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok")
    assert responder.start() is True
    client = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    client.sendall(b"GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n")
    _read_one_response(client)
    stop_chatter = threading.Event()

    def chatter() -> None:
        while not stop_chatter.is_set():
            try:
                client.sendall(b"x")
            except OSError:
                return
            time.sleep(0.3)

    writer = threading.Thread(target=chatter)
    writer.start()
    try:
        started = time.monotonic()
        responder.stop()
        elapsed = time.monotonic() - started
    finally:
        stop_chatter.set()
        writer.join()
        client.close()

    assert handoff_progress.CLIENT_CLOSE_WAIT_SECONDS - 0.5 <= elapsed < handoff_progress.CLIENT_CLOSE_WAIT_SECONDS + 1.5


def test_stop_cuts_off_a_client_that_never_finishes_its_request() -> None:
    port = _free_port()
    progress = handoff_progress.UpgradeProgress(["build_webui"])
    responder = handoff_progress.ProgressResponder(progress, "127.0.0.1", port, "tok")
    assert responder.start() is True
    client = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    client.sendall(b"GET /api/health HTTP/1.1\r\nHost: localhost\r\nX-Slow: ")
    stop_trickle = threading.Event()

    def trickle() -> None:
        # one header byte a second, well inside the per-read timeout, for at most 8 seconds
        for _ in range(8):
            if stop_trickle.wait(1.0):
                return
            try:
                client.sendall(b"a")
            except OSError:
                return

    writer = threading.Thread(target=trickle)
    writer.start()
    try:
        time.sleep(0.3)
        started = time.monotonic()
        responder.stop()
        elapsed = time.monotonic() - started
    finally:
        stop_trickle.set()
        writer.join()
        client.close()

    # stop() gives open connections 3 seconds, then cuts them off
    assert elapsed < 4.5
