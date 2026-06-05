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
