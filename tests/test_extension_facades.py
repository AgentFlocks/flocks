import pytest

from flocks.audit import NullAuditSink, emit_audit_event, get_sink, register_sink
from flocks.auth.service import AuthService, LocalAuthBackend
from flocks.license import (
    AlwaysOkLicenseChecker,
    assert_license_active,
    get_checker,
    is_license_active,
    register_checker,
)


class IncompleteLicenseChecker:
    @classmethod
    async def is_active(cls, feature=None) -> bool:
        return True


class IncompleteAuditSink:
    pass


@pytest.mark.asyncio
async def test_oss_default_facades_remain_noop_and_local():
    AuthService.reset_backend()
    register_checker(AlwaysOkLicenseChecker)
    register_sink(NullAuditSink)

    assert AuthService.get_backend() is LocalAuthBackend
    assert await is_license_active(feature="session_create") is True
    await assert_license_active(feature="session_create")
    await emit_audit_event("test_event", {"ok": True})


def test_license_checker_contract_validation():
    register_checker(AlwaysOkLicenseChecker)
    with pytest.raises(ValueError, match="接口不完整"):
        register_checker(IncompleteLicenseChecker)
    assert get_checker() is AlwaysOkLicenseChecker


def test_audit_sink_contract_validation():
    register_sink(NullAuditSink)
    with pytest.raises(ValueError, match="接口不完整"):
        register_sink(IncompleteAuditSink)
    assert get_sink() is NullAuditSink
