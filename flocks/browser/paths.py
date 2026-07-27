"""Runtime paths for browser daemon sessions."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def validate_name(name: str) -> str:
    """Validate and return a browser session name."""
    if not _NAME_RE.fullmatch(name or ""):
        raise ValueError(f"invalid BU_NAME {name!r}: must match [A-Za-z0-9_-]{{1,64}}")
    return name


def resolve_name(name: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Resolve the effective browser session name."""
    effective_env = os.environ if env is None else env
    if name is not None:
        return validate_name(name)
    return validate_name(effective_env.get("BU_NAME") or "default")


def flocks_root(env: Mapping[str, str] | None = None) -> Path:
    """Return the configured Flocks data root."""
    effective_env = os.environ if env is None else env
    return Path(effective_env.get("FLOCKS_ROOT", str(Path.home() / ".flocks"))).expanduser()


def browser_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the browser runtime directory without creating it."""
    return flocks_root(env) / "browser"


@dataclass(frozen=True)
class BrowserRuntimePaths:
    """All runtime artifacts owned by one browser daemon session."""

    root: Path
    name: str

    @classmethod
    def resolve(
        cls,
        name: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> BrowserRuntimePaths:
        return cls(root=browser_dir(env), name=resolve_name(name, env))

    @property
    def stem(self) -> str:
        return "bu" if self.name == "default" else f"bu-{self.name}"

    @property
    def socket(self) -> Path:
        return self.root / f"{self.stem}.sock"

    @property
    def port(self) -> Path:
        return self.root / f"{self.stem}.port"

    @property
    def pid(self) -> Path:
        return self.root / f"{self.stem}.pid"

    @property
    def log(self) -> Path:
        return self.root / f"{self.stem}.log"

    @property
    def lock(self) -> Path:
        return self.root / f"{self.stem}.lock"

    @property
    def screenshot(self) -> Path:
        return self.root / f"{self.stem}-shot.png"

    def debug_screenshot(self, sequence: int) -> Path:
        return self.root / f"{self.stem}-debug-click-{sequence}.png"

    def ensure_root(self) -> Path:
        """Create the private runtime directory when a write is required."""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        return self.root
