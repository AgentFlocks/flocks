import pytest

from flocks.auth.service import AuthService, LocalAuthBackend


class DummyBackend(LocalAuthBackend):
    @classmethod
    async def has_users(cls) -> bool:
        return False


class IncompleteBackend:
    @classmethod
    async def has_users(cls) -> bool:
        return False


@pytest.mark.asyncio
async def test_auth_service_facade_can_swap_backend():
    AuthService.register_backend(DummyBackend)
    assert await AuthService.has_users() is False

    AuthService.reset_backend()
    assert AuthService.get_backend() is LocalAuthBackend


def test_auth_service_rejects_incomplete_backend():
    with pytest.raises(ValueError, match="接口不完整"):
        AuthService.register_backend(IncompleteBackend)
    assert AuthService.get_backend() is LocalAuthBackend
