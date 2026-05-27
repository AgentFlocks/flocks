"""Tests for ``ToolOutputConfig`` and the runtime helpers in
``flocks.tool.tool_output_limits``.

PR #321 self-review caught that the original ``flocks.json.example``
documented camelCase keys (``readMaxLines`` …) while the Pydantic model
only had snake_case fields, so the example silently fell back to defaults
at runtime.  These tests pin the alias behaviour and the helper fallbacks
so the regression cannot recur.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from flocks.config.config import Config, ConfigInfo, ToolOutputConfig
from flocks.tool import tool_output_limits as limits_mod


# ---------------------------------------------------------------------------
# Pydantic model — alias acceptance
# ---------------------------------------------------------------------------


class TestToolOutputConfigParsing:
    def test_camel_case_keys_populate_snake_case_fields(self):
        cfg = ToolOutputConfig.model_validate(
            {
                "readMaxLines": 5000,
                "readMaxBytes": 102400,
                "readMaxLineLength": 4000,
            }
        )
        assert cfg.read_max_lines == 5000
        assert cfg.read_max_bytes == 102400
        assert cfg.read_max_line_length == 4000

    def test_snake_case_keys_also_accepted_via_populate_by_name(self):
        cfg = ToolOutputConfig.model_validate(
            {
                "read_max_lines": 5000,
                "read_max_bytes": 102400,
                "read_max_line_length": 4000,
            }
        )
        assert cfg.read_max_lines == 5000
        assert cfg.read_max_bytes == 102400
        assert cfg.read_max_line_length == 4000

    def test_partial_override_leaves_others_none(self):
        cfg = ToolOutputConfig.model_validate({"readMaxLines": 9999})
        assert cfg.read_max_lines == 9999
        assert cfg.read_max_bytes is None
        assert cfg.read_max_line_length is None

    def test_empty_object_yields_all_none(self):
        cfg = ToolOutputConfig.model_validate({})
        assert cfg.read_max_lines is None
        assert cfg.read_max_bytes is None
        assert cfg.read_max_line_length is None

    def test_zero_and_negative_values_rejected(self):
        # gt=0 must reject zero and negative values so callers don't end up
        # with a tool that returns an empty payload.
        with pytest.raises(ValidationError):
            ToolOutputConfig.model_validate({"readMaxLines": 0})
        with pytest.raises(ValidationError):
            ToolOutputConfig.model_validate({"readMaxBytes": -1})


class TestConfigInfoEmbedsToolOutput:
    def test_camel_case_in_flocks_json_round_trip(self):
        raw = {
            "toolOutput": {
                "readMaxLines": 7777,
                "readMaxBytes": 88888,
                "readMaxLineLength": 9999,
            }
        }
        cfg = ConfigInfo.model_validate(raw)
        assert cfg.tool_output is not None
        assert cfg.tool_output.read_max_lines == 7777
        assert cfg.tool_output.read_max_bytes == 88888
        assert cfg.tool_output.read_max_line_length == 9999

    def test_snake_case_at_outer_layer_also_accepted(self):
        raw = {
            "tool_output": {
                "read_max_lines": 1234,
            }
        }
        cfg = ConfigInfo.model_validate(raw)
        assert cfg.tool_output is not None
        assert cfg.tool_output.read_max_lines == 1234


# ---------------------------------------------------------------------------
# Runtime helper — read.py uses these getters
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """Drop ``Config._cached_config`` between tests so the helpers re-resolve."""
    saved = Config._cached_config
    Config._cached_config = None
    yield
    Config._cached_config = saved


class TestRuntimeLimitsHelpers:
    def test_defaults_when_no_config(self):
        # No cached config + no flocks.json on disk → defaults.
        assert limits_mod.get_read_max_lines() == limits_mod.DEFAULT_READ_MAX_LINES
        assert limits_mod.get_read_max_bytes() == limits_mod.DEFAULT_READ_MAX_BYTES
        assert (
            limits_mod.get_read_max_line_length()
            == limits_mod.DEFAULT_READ_MAX_LINE_LENGTH
        )

    def test_cached_config_overrides_defaults(self):
        cached = ConfigInfo.model_validate(
            {
                "toolOutput": {
                    "readMaxLines": 5000,
                    "readMaxBytes": 200_000,
                    "readMaxLineLength": 4000,
                }
            }
        )
        Config._cached_config = cached

        assert limits_mod.get_read_max_lines() == 5000
        assert limits_mod.get_read_max_bytes() == 200_000
        assert limits_mod.get_read_max_line_length() == 4000

    def test_partial_override_falls_back_to_defaults_per_field(self):
        cached = ConfigInfo.model_validate(
            {"toolOutput": {"readMaxLines": 9999}}
        )
        Config._cached_config = cached

        assert limits_mod.get_read_max_lines() == 9999
        # Untouched fields fall back to the per-field defaults.
        assert limits_mod.get_read_max_bytes() == limits_mod.DEFAULT_READ_MAX_BYTES
        assert (
            limits_mod.get_read_max_line_length()
            == limits_mod.DEFAULT_READ_MAX_LINE_LENGTH
        )

    def test_sync_fallback_reads_flocks_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When ``Config._cached_config`` is empty, the helper should still
        pick up overrides by parsing ``~/.flocks/flocks.json`` synchronously.
        This is the path exercised by CLI one-shot mode before
        ``Config.get()`` has been awaited.
        """
        fake_home = tmp_path / "home"
        flocks_dir = fake_home / ".flocks"
        flocks_dir.mkdir(parents=True)
        (flocks_dir / "flocks.json").write_text(
            json.dumps(
                {
                    "toolOutput": {
                        "readMaxLines": 4321,
                        "readMaxBytes": 65000,
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("HOME", str(fake_home))
        # Force the cached path to miss so the JSON fallback runs.
        Config._cached_config = None

        assert limits_mod.get_read_max_lines() == 4321
        assert limits_mod.get_read_max_bytes() == 65000

    def test_section_loader_swallows_internal_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Defensive contract: ``_load_tool_output_section`` must swallow any
        attribute / IO error and return ``None`` so the public getters can
        fall back to defaults instead of crashing the tool runtime.

        We poison the cached-config access path so it raises, then assert
        the loader returns ``None`` and the getters yield default values.
        """

        class _Boom:
            def __getattr__(self, _name):  # noqa: D401 — intentionally explosive
                raise RuntimeError("synthetic cache failure")

        monkeypatch.setattr(Config, "_cached_config", _Boom(), raising=False)
        # And make the JSON fallback miss by pointing HOME at an empty dir.
        monkeypatch.setenv("HOME", "/nonexistent/path")

        assert limits_mod._load_tool_output_section() is None
        assert limits_mod.get_read_max_lines() == limits_mod.DEFAULT_READ_MAX_LINES
