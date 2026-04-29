"""
Config schema: explicit declaration + extra=allow + permission field.
"""

from __future__ import annotations

from flocks.config.config import (
    ConfigInfo,
    EvolutionConfig,
    EvolutionLayerConfig,
    PermissionConfig,
)


def test_evolution_section_parses_full_settings():
    payload = {
        "evolution": {
            "enabled": True,
            "acquirer": {"enabled": True, "use": "builtin"},
            "author":   {"enabled": True, "use": "builtin"},
            "tracker":  {"enabled": True, "use": "builtin"},
            "curator": {
                "enabled": True,
                "use": "builtin",
                "settings": {
                    "min_idle_hours": 24,
                    "stale_after_days": 14,
                    "archive_after_days": 60,
                },
            },
        }
    }
    cfg = ConfigInfo(**payload)
    assert isinstance(cfg.evolution, EvolutionConfig)
    assert cfg.evolution.enabled is True
    assert isinstance(cfg.evolution.curator, EvolutionLayerConfig)
    assert cfg.evolution.curator.settings == {
        "min_idle_hours": 24,
        "stale_after_days": 14,
        "archive_after_days": 60,
    }


def test_evolution_section_accepts_extra_keys():
    payload = {
        "evolution": {
            "enabled": False,
            "experimental_flag": "yes",  # extra=allow on EvolutionConfig
        }
    }
    cfg = ConfigInfo(**payload)
    assert cfg.evolution.enabled is False


def test_permission_skill_manage_field_present():
    perm = PermissionConfig(skill_manage="ask")
    assert perm.skill_manage == "ask"


def test_evolution_layer_disabled_round_trips():
    payload = {
        "evolution": {
            "enabled": True,
            "curator": {"enabled": False},
        }
    }
    cfg = ConfigInfo(**payload)
    assert cfg.evolution.curator.enabled is False


def test_missing_evolution_key_does_not_break_config():
    # Existing installs without an evolution section must still parse.
    cfg = ConfigInfo()
    assert cfg.evolution is None
