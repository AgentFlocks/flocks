from __future__ import annotations

from flocks.workflow.poller_manager import WorkflowPollerManager


def test_poller_config_supports_cron_expression() -> None:
    manager = WorkflowPollerManager()

    config = manager._normalize_config(  # noqa: SLF001 - focused unit test
        "wf-1",
        {
            "enabled": True,
            "cronExpression": "*/5 * * * *",
            "timeoutSeconds": 120,
        },
    )

    assert config["enabled"] is True
    assert config["cronExpression"] == "*/5 * * * *"
    assert config["intervalSeconds"] == 30


def test_poller_next_run_uses_cron_when_present() -> None:
    manager = WorkflowPollerManager()

    next_run_at = manager._compute_next_run_at_ms(  # noqa: SLF001 - focused unit test
        {
            "intervalSeconds": 30,
            "cronExpression": "*/5 * * * *",
        },
        base_ts_s=0,
    )

    assert next_run_at == 300000
