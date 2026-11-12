"""
Root certificates for a Python whose OpenSSL looks for them elsewhere.

The Linux packages ship a python-build-standalone interpreter, whose OpenSSL
looks for the system's root certificates in ``/etc/ssl/cert.pem`` and in the
hashed ``/etc/ssl/certs`` directory.  RHEL 8 and the distributions built from
it (CentOS, Rocky and AlmaLinux 8, Anolis 8, Alibaba Cloud Linux 3) have
neither: their bundle is under ``/etc/pki``, and ``/etc/ssl/certs`` links to a
directory that holds it without the hashed names OpenSSL looks up.  Every
client that trusts the defaults -- ``ssl.create_default_context()``: the WeCom
SDK's WebSocket, aiohttp, urllib -- then fails with CERTIFICATE_VERIFY_FAILED,
while httpx, which brings certifi's bundle, works.  python-build-standalone
releases from 20260320 on fall back to ``/etc/pki/tls/cert.pem`` themselves,
but only in ``load_default_certs`` and only for that file.

``ensure()``, run when ``flocks`` is imported, points ``SSL_CERT_FILE`` at the
system's bundle -- or at certifi's, when the system has none -- when the
environment names no certificates of its own and OpenSSL's default locations
hold none.  OpenSSL reads the variable each time a context loads its default
locations, and the processes this one starts inherit it.  httpx reads it too
and then uses that bundle instead of certifi's, which is the same trust the
rest of the machine has.  Elsewhere (Debian, Ubuntu, RHEL 9 and later, macOS,
Windows) it changes nothing.
"""

from __future__ import annotations

import os
import re
import ssl
import sys
from typing import Iterable, MutableMapping, Optional

# Where Linux distributions keep their bundle of root certificates, in the order
# truststore and Go's crypto/x509 look: the first one present is the system's.
SYSTEM_BUNDLES: tuple[str, ...] = (
    "/etc/ssl/cert.pem",  # Alpine, Arch, Fedora 34+, RHEL 9+
    "/etc/pki/tls/cert.pem",  # RHEL 8 and older, Fedora 33 and older
    "/etc/pki/tls/certs/ca-bundle.crt",  # the same bundle, as RHEL 6 named it
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # what both point to since RHEL 7
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Gentoo
    "/etc/ssl/ca-bundle.pem",  # SUSE
)

# OpenSSL finds a certificate in a directory by the hash of its subject:
# ``<8 hex digits>.<n>`` (c_rehash, update-ca-certificates).
_HASHED_NAME = re.compile(r"^[0-9a-fA-F]{8}\.\d+$")

_ENV_NAMES = ("SSL_CERT_FILE", "SSL_CERT_DIR")


def _readable_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0 and os.access(path, os.R_OK)
    except OSError:
        return False


def _holds_hashed_certs(directory: str) -> bool:
    """Whether *directory* has a certificate under a hashed name (a link that resolves to one counts)."""
    try:
        with os.scandir(directory) as entries:
            return any(
                _HASHED_NAME.match(entry.name) and _readable_file(entry.path) for entry in entries
            )
    except OSError:
        return False


def defaults_hold_certs() -> bool:
    """Whether OpenSSL's default locations hold root certificates it can read.

    A file that is there but empty or unreadable, or a hashed name that is a
    dangling link, holds none: OpenSSL would load nothing from it.
    """
    paths = ssl.get_default_verify_paths()
    if paths.cafile and _readable_file(paths.cafile):  # cafile is None unless the file exists
        return True
    return bool(paths.capath) and _holds_hashed_certs(paths.capath)


def _certifi_bundle() -> Optional[str]:
    try:
        import certifi
    except ImportError:
        return None
    try:
        path = certifi.where()
    except Exception:
        return None
    return path if _readable_file(path) else None


def find_bundle(candidates: Optional[Iterable[str]] = None) -> Optional[str]:
    """The system's bundle of root certificates, else certifi's, else ``None``."""
    for path in SYSTEM_BUNDLES if candidates is None else candidates:
        if _readable_file(path):
            return path
    return _certifi_bundle()


def ensure(environ: Optional[MutableMapping[str, str]] = None) -> Optional[str]:
    """Point ``SSL_CERT_FILE`` at a bundle when OpenSSL's defaults hold no certificates.

    Returns the bundle it set, or ``None`` when it changed nothing: not Linux,
    the environment already names certificates, the defaults hold some, or no
    bundle was found.
    """
    if not sys.platform.startswith("linux"):
        return None
    env = os.environ if environ is None else environ
    if any(env.get(name) for name in _ENV_NAMES):
        return None
    if defaults_hold_certs():
        return None
    bundle = find_bundle()
    if bundle is None:
        return None
    env["SSL_CERT_FILE"] = bundle
    return bundle


__all__ = ["SYSTEM_BUNDLES", "defaults_hold_certs", "ensure", "find_bundle"]
