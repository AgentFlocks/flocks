"""
TLS settings of the HTTP clients that talk to LLM providers.

A provider's options in flocks.json (``provider.<id>.options``) may hold:

``verify_ssl`` (bool, default true; ``ssl_verify`` is the older spelling)
    False turns off the certificate and host name checks for that provider.
    It also lets the client speak the older handshakes internal gateways often
    still do: RSA key exchange and CBC-SHA1 suites, TLS 1.0 and 1.1, servers
    without secure renegotiation.  The packaged Python (python-build-standalone)
    keeps CPython's own TLS defaults, stricter than the RHEL system Python that
    older installs ran on, so such a gateway would fail the handshake with the
    checks off unless the handshake is relaxed too.

``ca_cert`` (string)
    A CA certificate in PEM, or the path of a PEM file or of a hashed
    certificate directory on the server.  It is trusted besides the default
    bundle and the checks stay on.  Not used while ``verify_ssl`` is false.

``httpx_verify()`` turns them into the ``verify`` argument of an httpx client.
With neither set it returns ``True``, which leaves the client exactly as httpx
builds it on its own.
"""

from __future__ import annotations

import os
import ssl
from typing import Any, Iterable, Mapping, Optional, Union

VERIFY_SSL_KEY = "verify_ssl"
LEGACY_VERIFY_SSL_KEY = "ssl_verify"
CA_CERT_KEY = "ca_cert"
TLS_OPTION_KEYS = (VERIFY_SSL_KEY, LEGACY_VERIFY_SSL_KEY, CA_CERT_KEY)

# PEM text pasted into the settings; a bundle of a few hundred CAs fits.
MAX_CA_CERT_CHARS = 1024 * 1024

# OpenSSL's own default list at security level 0: RSA key exchange, CBC and
# SHA-1 suites, small keys and TLS 1.0 / 1.1 signatures are allowed again.
_COMPAT_CIPHERS = "DEFAULT:@SECLEVEL=0"

TLS_CERTIFICATE_FAILED = "tls_certificate_failed"
TLS_HANDSHAKE_FAILED = "tls_handshake_failed"


class CACertError(ValueError):
    """A ``ca_cert`` value that cannot be loaded; the message is shown to the user."""


def coerce_bool(value: Any, default: bool) -> bool:
    """Coerce loosely-typed config values to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def resolve_verify_ssl(settings: Any, default: bool = True) -> bool:
    """Whether certificates are checked, from provider options (``verify_ssl`` or ``ssl_verify``)."""
    if not isinstance(settings, Mapping):
        return default
    if VERIFY_SSL_KEY in settings:
        return coerce_bool(settings.get(VERIFY_SSL_KEY), default)
    if LEGACY_VERIFY_SSL_KEY in settings:
        return coerce_bool(settings.get(LEGACY_VERIFY_SSL_KEY), default)
    return default


def resolve_ca_cert(settings: Any) -> Optional[str]:
    """The configured ``ca_cert`` (PEM text or a path), or ``None``."""
    if not isinstance(settings, Mapping):
        return None
    value = settings.get(CA_CERT_KEY)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def has_custom_tls(settings: Any) -> bool:
    """Whether the options change anything from the default checks."""
    return not resolve_verify_ssl(settings) or resolve_ca_cert(settings) is not None


def is_pem_text(value: str) -> bool:
    return "-----BEGIN" in value


def _default_verify_locations(trust_env: bool) -> dict:
    """The trust httpx gives ``verify=True``: SSL_CERT_FILE / SSL_CERT_DIR, else certifi."""
    if trust_env and os.environ.get("SSL_CERT_FILE"):
        return {"cafile": os.environ["SSL_CERT_FILE"]}
    if trust_env and os.environ.get("SSL_CERT_DIR"):
        return {"capath": os.environ["SSL_CERT_DIR"]}
    import certifi

    return {"cafile": certifi.where()}


def _load_ca(context: ssl.SSLContext, ca_cert: str) -> None:
    if "PRIVATE KEY-----" in ca_cert:
        raise CACertError("CA 证书里只填证书（-----BEGIN CERTIFICATE-----），不要粘贴私钥。")
    if is_pem_text(ca_cert):
        if len(ca_cert) > MAX_CA_CERT_CHARS:
            raise CACertError("CA 证书内容太长，请只保留需要信任的 CA 证书。")
        try:
            context.load_verify_locations(cadata=ca_cert)
        except (ssl.SSLError, ValueError) as exc:
            raise CACertError(f"CA 证书无法解析，请粘贴完整的 PEM 格式证书：{exc}") from exc
        return
    path = os.path.expanduser(ca_cert)
    if not os.path.isabs(path):
        raise CACertError("CA 证书路径要写服务器上的绝对路径，或者直接粘贴 PEM 格式的证书内容。")
    try:
        if os.path.isdir(path):
            context.load_verify_locations(capath=path)
        else:
            context.load_verify_locations(cafile=path)
    except FileNotFoundError as exc:
        raise CACertError(f"找不到 CA 证书文件：{path}") from exc
    except PermissionError as exc:
        raise CACertError(f"没有权限读取 CA 证书文件：{path}") from exc
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise CACertError(f"CA 证书文件无法加载：{path}（{exc}）") from exc


def validate_ca_cert(value: Optional[str]) -> Optional[str]:
    """Check that *value* loads at least one certificate; returns it stripped, or ``None`` when empty."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    _load_ca(probe, value)
    # A hashed directory is read lazily, certificate by certificate, so its
    # count says nothing until a handshake looks a certificate up.
    is_directory = not is_pem_text(value) and os.path.isdir(os.path.expanduser(value))
    if not is_directory and not probe.cert_store_stats().get("x509"):
        raise CACertError("CA 证书里没有找到证书，请粘贴 -----BEGIN CERTIFICATE----- 开头的完整内容。")
    return value


def verifying_context(ca_cert: str, *, trust_env: bool = True) -> ssl.SSLContext:
    """Default trust plus *ca_cert*; certificate and host name checks stay on."""
    context = ssl.create_default_context(**_default_verify_locations(trust_env))
    _load_ca(context, ca_cert)
    # The pasted certificate may be an intermediate, or the gateway's own
    # self-signed certificate: let a chain end at any certificate trusted here.
    partial_chain = getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    if partial_chain:
        context.verify_flags |= partial_chain
    return context


def compat_context() -> ssl.SSLContext:
    """No certificate checks, and the older handshakes accepted (see the module docstring)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    except (ValueError, ssl.SSLError):  # pragma: no cover - build without the floor control
        pass
    try:
        context.set_ciphers(_COMPAT_CIPHERS)
    except ssl.SSLError:  # pragma: no cover - OpenSSL without security levels
        pass
    context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
    return context


def httpx_verify(settings: Any, *, trust_env: bool = True) -> Union[bool, ssl.SSLContext]:
    """The ``verify`` argument for an httpx client built from provider options.

    Raises :class:`CACertError` when ``ca_cert`` cannot be loaded, so a wrong
    certificate fails loudly instead of quietly checking against the default
    bundle only.
    """
    if not resolve_verify_ssl(settings):
        return compat_context()
    ca_cert = resolve_ca_cert(settings)
    if ca_cert is None:
        return True
    return verifying_context(ca_cert, trust_env=trust_env)


def _chain(exc: BaseException) -> Iterable[BaseException]:
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(seen) < 16:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


_HANDSHAKE_MARKERS = (
    "HANDSHAKE_FAILURE",
    "ALERT_PROTOCOL_VERSION",
    "UNSUPPORTED_PROTOCOL",
    "WRONG_VERSION_NUMBER",
    "NO_PROTOCOLS_AVAILABLE",
    "NO_CIPHERS_AVAILABLE",
    "NO_SHARED_CIPHER",
    "UNSAFE_LEGACY_RENEGOTIATION",
    "DH_KEY_TOO_SMALL",
    "EE_KEY_TOO_SMALL",
    "CA_MD_TOO_WEAK",
    "UNEXPECTED_EOF_WHILE_READING",
)


def classify_tls_error(exc: BaseException) -> Optional[str]:
    """``tls_certificate_failed`` / ``tls_handshake_failed`` when TLS caused *exc*, else ``None``.

    Clients wrap the ``ssl`` error (httpcore -> httpx -> the provider SDK), so
    the whole cause chain is read, and the message as well, since some
    wrappers keep only the text.
    """
    for error in _chain(exc):
        if isinstance(error, CACertError):
            return TLS_CERTIFICATE_FAILED
        if isinstance(error, ssl.SSLCertVerificationError):
            return TLS_CERTIFICATE_FAILED
        text = str(error)
        if "CERTIFICATE_VERIFY_FAILED" in text:
            return TLS_CERTIFICATE_FAILED
        if isinstance(error, ssl.SSLError) or any(marker in text for marker in _HANDSHAKE_MARKERS):
            return TLS_HANDSHAKE_FAILED
    return None


TLS_ERROR_HINTS = {
    TLS_CERTIFICATE_FAILED: (
        "SSL 证书校验失败：网关用的是自签名或内部 CA 签发的证书。"
        "可以在模型服务设置里填写 CA 证书，或关闭「校验 SSL 证书」。"
    ),
    TLS_HANDSHAKE_FAILED: (
        "TLS 握手失败：网关的 TLS 版本或加密套件较旧，或地址的 http/https 写错了。"
        "确认地址无误后，可以在模型服务设置里关闭「校验 SSL 证书」，同时会兼容旧版 TLS。"
    ),
}


def tls_error_hint(exc: BaseException) -> Optional[str]:
    """A message telling the user what to change, when TLS caused *exc*."""
    code = classify_tls_error(exc)
    return TLS_ERROR_HINTS.get(code) if code else None


__all__ = [
    "CA_CERT_KEY",
    "CACertError",
    "LEGACY_VERIFY_SSL_KEY",
    "TLS_CERTIFICATE_FAILED",
    "TLS_ERROR_HINTS",
    "TLS_HANDSHAKE_FAILED",
    "TLS_OPTION_KEYS",
    "VERIFY_SSL_KEY",
    "classify_tls_error",
    "coerce_bool",
    "compat_context",
    "has_custom_tls",
    "httpx_verify",
    "resolve_ca_cert",
    "resolve_verify_ssl",
    "tls_error_hint",
    "validate_ca_cert",
    "verifying_context",
]
