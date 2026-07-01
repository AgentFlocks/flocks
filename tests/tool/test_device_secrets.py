from unittest.mock import MagicMock, patch

from flocks.tool.device.secrets import persist_fields


def test_persist_fields_strips_tdp_config_api_base_url():
    with patch("flocks.security.get_secret_manager", return_value=MagicMock()):
        fields = persist_fields(
            "device-1",
            "tdp_api_v3_3_10",
            {"base_url": "https://tdp.local/config/api"},
        )

    assert fields["base_url"] == "https://tdp.local"


def test_persist_fields_keeps_non_tdp_base_url_paths():
    with patch("flocks.security.get_secret_manager", return_value=MagicMock()):
        fields = persist_fields(
            "device-1",
            "proxy_device_v1",
            {"base_url": "https://proxy.local/config/api"},
        )

    assert fields["base_url"] == "https://proxy.local/config/api"


def test_persist_fields_deletes_secret_when_empty_string_is_submitted():
    secret_manager = MagicMock()
    prior = {
        "api_key": "{secret:device_device-1_api_key}",
        "base_url": "https://tdp.local",
    }

    with patch("flocks.security.get_secret_manager", return_value=secret_manager):
        fields = persist_fields(
            "device-1",
            "tdp_api_v3_3_10",
            {"api_key": ""},
            prior_db_fields=prior,
        )

    assert "api_key" not in fields
    assert fields["base_url"] == "https://tdp.local"
    secret_manager.delete.assert_called_once_with("device_device-1_api_key")


def test_persist_fields_keeps_secret_when_key_is_absent():
    prior = {
        "api_key": "{secret:device_device-1_api_key}",
        "base_url": "https://tdp.local",
    }

    with patch("flocks.security.get_secret_manager", return_value=MagicMock()):
        fields = persist_fields(
            "device-1",
            "tdp_api_v3_3_10",
            {"base_url": "https://tdp.local/config/api"},
            prior_db_fields=prior,
        )

    assert fields["api_key"] == "{secret:device_device-1_api_key}"
    assert fields["base_url"] == "https://tdp.local"
