from unittest.mock import MagicMock, patch

from flocks.provider.provider import ProviderConfig
from flocks.provider.sdk.openai import OpenAIProvider


class TestOpenAIProviderConfiguration:
    @patch("httpx.AsyncClient")
    @patch("openai.AsyncOpenAI")
    def test_get_client_respects_verify_ssl_false(self, mock_async_openai, mock_http_client):
        provider = OpenAIProvider()
        provider.configure(
            ProviderConfig(
                provider_id=provider.id,
                api_key="test-api-key",
                base_url="https://gateway.internal/v1",
                custom_settings={"verify_ssl": False},
            )
        )

        http_client = MagicMock()
        mock_http_client.return_value = http_client
        mock_async_openai.return_value = MagicMock()

        provider._get_client()

        # Granular timeout supports multimodal payloads; verify fields
        # semantically so minor adjustments to non-critical values don't break.
        assert mock_http_client.call_count == 1
        kwargs = mock_http_client.call_args.kwargs
        assert kwargs["trust_env"] is True
        assert kwargs["verify"] is False
        timeout_arg = kwargs["timeout"]
        assert getattr(timeout_arg, "connect", None) == 30.0
        assert getattr(timeout_arg, "read", None) == 600.0
        assert getattr(timeout_arg, "write", None) == 600.0

        mock_async_openai.assert_called_once_with(
            api_key="test-api-key",
            base_url="https://gateway.internal/v1",
            http_client=http_client,
        )
