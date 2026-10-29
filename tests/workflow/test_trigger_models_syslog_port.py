"""``TriggerDefinition`` validates the syslog listener port range up front (WFL-0316)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flocks.workflow.triggers.models import SYSLOG_PORT_MAX, SYSLOG_PORT_MIN, TriggerDefinition


def _syslog(port):
    return {"type": "syslog", "source": {"protocol": "udp", "host": "127.0.0.1", "port": port}}


def test_port_bounds_are_the_tcp_udp_range() -> None:
    assert (SYSLOG_PORT_MIN, SYSLOG_PORT_MAX) == (1, 65535)


@pytest.mark.parametrize("port", [0, 65536, 70000, -1, "abc", True, 5140.5])
def test_out_of_range_or_non_integer_port_is_rejected(port) -> None:
    with pytest.raises(ValidationError) as excinfo:
        TriggerDefinition.model_validate(_syslog(port))
    assert "source.port must be an integer between 1 and 65535" in str(excinfo.value)
    assert f"got {port!r}" in str(excinfo.value)


@pytest.mark.parametrize(("port", "expected"), [(1, 1), (65535, 65535), ("5140", 5140), (5140.0, 5140)])
def test_valid_ports_are_normalised_to_int(port, expected) -> None:
    trigger = TriggerDefinition.model_validate(_syslog(port))
    assert trigger.source["port"] == expected and isinstance(trigger.source["port"], int)


def test_missing_port_keeps_downstream_default_and_other_types_are_untouched() -> None:
    assert "port" not in TriggerDefinition.model_validate({"type": "syslog", "source": {"protocol": "udp"}}).source
    other = TriggerDefinition.model_validate({"type": "custom_webhook", "source": {"path": "/x", "port": 70000}, "auth": {"type": "api_key", "apiKey": "k"}})
    assert other.source["port"] == 70000  # only syslog sources are listener ports
