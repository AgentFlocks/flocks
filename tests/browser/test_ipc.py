from pathlib import Path

from flocks.browser import _ipc as ipc


def test_browser_ipc_files_live_under_flocks_browser_dir() -> None:
    expected_dir = Path.home() / ".flocks" / "browser"

    assert Path(ipc.BU_TMP_DIR) == expected_dir
    assert ipc.log_path("default") == expected_dir / "bu.log"
    assert ipc.pid_path("default") == expected_dir / "bu.pid"
    assert ipc.port_path("default") == expected_dir / "bu.port"
    if ipc.IS_WINDOWS:
        assert ipc.sock_addr("default") == "tcp:bu"
    else:
        assert ipc.sock_addr("default") == str(expected_dir / "bu.sock")
