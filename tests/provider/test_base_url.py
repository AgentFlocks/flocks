"""validate_provider_base_url: the single gate for user-supplied provider / API-service base URLs."""
from __future__ import annotations

import pytest

from flocks.provider import base_url as base_url_mod
from flocks.provider.base_url import BaseURLValidationError, validate_provider_base_url


@pytest.mark.parametrize(
    "value",
    ["", "   ", "ftp://host/v1", "not a url", "https:///v1", "http://:8080/v1", "http://[fe80::1/v1", "http://host:99999/v1"],
)
def test_rejects_malformed_or_non_http(value: str) -> None:
    with pytest.raises(BaseURLValidationError):
        validate_provider_base_url(value)


@pytest.mark.parametrize(
    "value",
    ["http://169.254.169.254/latest/meta-data", "http://[fe80::1]/v1", "http://0.0.0.0/v1", "http://metadata.google.internal/x", "http://metadata/x"],
)
def test_rejects_metadata_and_link_local_targets(value: str) -> None:
    with pytest.raises(BaseURLValidationError):
        validate_provider_base_url(value)


@pytest.mark.parametrize("value", ["http://127.0.0.1:11434/v1", "http://10.1.2.3/v1", "http://localhost:8000", "https://gw.corp.internal/v1", "http://[::1]:8000/v1"])
def test_private_and_loopback_are_allowed_with_warning(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    warnings: list = []
    monkeypatch.setattr(base_url_mod.log, "warning", lambda msg, extra=None: warnings.append((msg, extra)))
    assert validate_provider_base_url(value) == value
    assert warnings and warnings[0][0] == "provider.base_url.private_network"


def test_public_url_is_returned_stripped_without_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings: list = []
    monkeypatch.setattr(base_url_mod.log, "warning", lambda msg, extra=None: warnings.append((msg, extra)))
    assert validate_provider_base_url("  https://api.openai.com/v1  ") == "https://api.openai.com/v1"
    assert warnings == []


def test_error_message_names_the_field() -> None:
    with pytest.raises(BaseURLValidationError, match="baseURL must use http or https"):
        validate_provider_base_url("ftp://x", field="baseURL")
