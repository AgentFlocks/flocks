from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Deployment supplies the service token and an already-running RAGFlow."""

    model_config = SettingsConfigDict(env_prefix="KB_", extra="ignore")

    api_token: SecretStr
    ragflow_base_url: str
    ragflow_api_key: SecretStr
    host: str = "127.0.0.1"
    port: int = Field(default=8767, ge=1, le=65535)
    request_timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_upload_bytes: int = Field(default=32 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)

    @field_validator("api_token", "ragflow_api_key")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 32 or any(ord(character) < 33 or ord(character) > 126 for character in raw):
            raise ValueError("credentials must contain at least 32 non-whitespace ASCII characters")
        return value

    @field_validator("ragflow_base_url")
    @classmethod
    def valid_upstream(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("a valid HTTP(S) knowledge engine URL is required")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("the knowledge engine URL cannot contain credentials, query or fragment")
        return value.rstrip("/")
