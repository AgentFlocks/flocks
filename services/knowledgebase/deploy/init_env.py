"""Generate the knowledgebase service token for a new deployment.

RAGFlow is deployed separately. This helper does not create or rotate its credentials.
"""

import argparse
import os
import secrets
import sys
from pathlib import Path

SECRET_NAMES = ("KB_API_TOKEN",)


def generate_env(path: Path) -> None:
    token = secrets.token_urlsafe(48)
    text = "# Generated for a new knowledgebase API deployment. Keep private.\n"
    text += "# Set KB_RAGFLOW_BASE_URL and KB_RAGFLOW_API_KEY to the independent RAGFlow service.\n"
    text += "# Changing this file does not rotate an existing deployment.\n"
    text += f"KB_API_TOKEN={token}\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().with_name(".env"))
    args = parser.parse_args(argv)
    try:
        generate_env(args.output)
    except FileExistsError:
        print(
            "Refusing to overwrite an existing environment file; this helper is for new deployments only.",
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            "Could not create the private environment file; check the destination directory and permissions.",
            file=sys.stderr,
        )
        return 1
    print("Created a mode-0600 environment file. Credentials were not printed; keep the file private.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
