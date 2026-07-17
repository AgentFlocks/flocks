import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_START = REPO_ROOT / "scripts" / "container-start.sh"


def test_container_start_uses_supervised_service_commands(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "calls.log"
    flocks = bin_dir / "flocks"
    flocks.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$CALL_LOG"\n',
        encoding="utf-8",
    )
    flocks.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "CALL_LOG": str(call_log),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "ROOT_DIR": str(tmp_path),
        }
    )

    completed = subprocess.run(
        ["bash", str(CONTAINER_START)],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode == 0
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        "start --no-browser --skip-webui-build",
        "logs --follow",
        "stop",
    ]
