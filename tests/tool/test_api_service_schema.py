from flocks.tool.schema.api_service_schema import _build_api_service_credential_schema


def test_credential_schema_preserves_internal_fields() -> None:
    fields = _build_api_service_credential_schema(
        "webcli_device",
        {
            "credential_fields": [
                {
                    "key": "base_url",
                    "label": "Base URL",
                    "storage": "config",
                    "config_key": "base_url",
                },
                {
                    "key": "auth_state",
                    "label": "Auth State",
                    "storage": "secret",
                    "config_key": "auth_state",
                    "internal": True,
                },
                {
                    "key": "legacy_cookie",
                    "label": "Legacy Cookie",
                    "storage": "secret",
                    "config_key": "legacy_cookie",
                    "hidden": True,
                },
            ],
        },
    )

    by_key = {field.key: field for field in fields}
    assert by_key["base_url"].internal is False
    assert by_key["auth_state"].internal is True
    assert by_key["auth_state"].storage == "secret"
    assert by_key["legacy_cookie"].internal is True
