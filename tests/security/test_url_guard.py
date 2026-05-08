import pytest

from flocks.security.url_guard import GuardedResolver, guarded_tcp_connector, validate_public_http_url


class _FakeResolver:
    def __init__(self, host: str) -> None:
        self.host = host

    async def resolve(self, hostname: str, port: int = 0, family=0):
        return [{"hostname": hostname, "host": self.host, "port": port, "family": family, "proto": 0, "flags": 0}]

    async def close(self) -> None:
        return None


def test_url_guard_allows_public_https_url():
    assert validate_public_http_url("https://example.com/path") is None


def test_url_guard_blocks_loopback_urls():
    assert validate_public_http_url("http://127.0.0.1:8000/") is not None
    assert validate_public_http_url("http://[::1]/") is not None
    assert validate_public_http_url("http://localhost:8000/") is not None


def test_url_guard_blocks_legacy_ipv4_loopback_urls():
    assert validate_public_http_url("http://127.1:8000/") is not None
    assert validate_public_http_url("http://2130706433:8000/") is not None
    assert validate_public_http_url("http://017700000001:8000/") is not None
    assert validate_public_http_url("http://0x7f000001:8000/") is not None


def test_url_guard_blocks_private_and_metadata_urls():
    assert validate_public_http_url("http://10.1.2.3/") is not None
    assert validate_public_http_url("http://192.168.1.10/") is not None
    assert validate_public_http_url("http://169.254.169.254/latest/meta-data/") is not None
    assert validate_public_http_url("http://metadata.google.internal/computeMetadata/v1/") is not None


@pytest.mark.asyncio
async def test_guarded_resolver_blocks_domain_resolving_to_loopback():
    resolver = GuardedResolver(_FakeResolver("127.0.0.1"))

    with pytest.raises(OSError, match="restricted network"):
        await resolver.resolve("evil.example", 80)


@pytest.mark.asyncio
async def test_guarded_resolver_allows_domain_resolving_to_public_ip():
    resolver = GuardedResolver(_FakeResolver("93.184.216.34"))

    result = await resolver.resolve("example.com", 80)

    assert result[0]["host"] == "93.184.216.34"


@pytest.mark.asyncio
async def test_guarded_connector_blocks_literal_loopback_before_connect():
    connector = guarded_tcp_connector()
    try:
        with pytest.raises(OSError, match="restricted network|not allowed"):
            await connector._resolve_host("127.0.0.1", 80)
    finally:
        await connector.close()
