"""
Pytest configuration and global fixtures
"""

import os
from pathlib import Path

import pytest

_API_KEY_MARKERS = {
    "requires_anthropic_key": ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY not set"),
    "requires_openai_key": ("OPENAI_API_KEY", "OPENAI_API_KEY not set"),
    "requires_threatbook_key": ("THREATBOOK_API_KEY", "THREATBOOK_API_KEY not set"),
    "requires_google_key": ("GOOGLE_API_KEY", "GOOGLE_API_KEY not set"),
    "requires_glm_key": ("GLM_API_KEY", "GLM_API_KEY not set"),
}


def pytest_configure(config):
    """Configure custom markers"""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test (may require external services)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running (>30s per test)"
    )
    config.addinivalue_line(
        "markers",
        "live: mark test as requiring live services "
        "(set FLOCKS_LIVE_TEST=1 and start dev server to enable)"
    )
    for marker_name, (_, reason) in _API_KEY_MARKERS.items():
        config.addinivalue_line("markers", f"{marker_name}: skip if {reason}")


def pytest_runtest_setup(item):
    """Auto-skip tests whose required API keys are not set."""
    for marker_name, (env_var, reason) in _API_KEY_MARKERS.items():
        if item.get_closest_marker(marker_name) and not os.getenv(env_var):
            pytest.skip(reason)


@pytest.fixture(autouse=True)
def _home_honors_env(monkeypatch):
    """Make ``Path.home()`` follow the ``HOME`` env var on every platform.

    Many suites isolate their filesystem by ``monkeypatch.setenv("HOME", ...)``
    and then rely on production code that resolves ``Path.home() / ".flocks"``
    (hub installs, plugin roots, the WebUI contract store, ...). That works on
    Linux CI, where ``Path.home()`` honors ``$HOME`` — but on Windows
    ``Path.home()`` reads ``%USERPROFILE%`` and ignores ``HOME`` entirely, so
    those tests silently leak into (and assert against) the *real* user home.

    This autouse fixture patches ``pathlib.Path.home`` to honor ``HOME`` (then
    ``USERPROFILE``) at call time, aligning Windows with Linux CI. It is
    behavior-preserving when ``HOME`` already points at the real home (the
    default), so tests that never touch ``HOME`` see no change.
    """
    def _home() -> Path:
        candidate = os.environ.get("HOME") or os.environ.get("USERPROFILE")
        if candidate:
            return Path(candidate)
        return Path(os.path.expanduser("~"))

    monkeypatch.setattr(Path, "home", staticmethod(_home))

    # ``Path.home()`` covers explicit home lookups, but ``~``/``~user`` tilde
    # expansion goes through ``os.path.expanduser`` (and ``Path.expanduser``),
    # which on Windows keys off ``%USERPROFILE%``/``%HOMEDRIVE%%HOMEPATH%`` and
    # likewise ignores ``HOME``. Route a bare ``~`` / ``~/...`` through the same
    # ``HOME``-aware root so tilde-based tests are hermetic too. Anything else
    # (``~otheruser``) falls back to the real implementation untouched.
    _real_expanduser = os.path.expanduser

    def _expanduser(path):
        text = os.fspath(path)
        # Only rewrite plain ``str`` tilde paths; leave bytes and ``~user``
        # forms to the real implementation so we never change its type
        # contract or raise on a bytes path.
        if isinstance(text, str):
            home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
            if home and (text == "~" or text.startswith("~/") or text.startswith("~\\")):
                return home + text[1:]
        return _real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", _expanduser)


