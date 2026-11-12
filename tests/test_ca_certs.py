"""
Tests for flocks/ca_certs.py: root certificates where OpenSSL's defaults find none.

The file system layouts are built under tmp_path and stand in for the real
ones; OpenSSL's default locations are what ``ssl.get_default_verify_paths``
reports, patched to point there.
"""

from __future__ import annotations

import os
import ssl
import sys
import types
from pathlib import Path

import pytest

from flocks import ca_certs

PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


def _clear_cert_env(monkeypatch) -> None:
    """No SSL_CERT_* now, and none left behind by what the test sets (delenv alone records nothing to undo)."""
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def _defaults(cafile: Path, capath: Path) -> ssl.DefaultVerifyPaths:
    """What get_default_verify_paths returns when OpenSSL was built to look at *cafile* and *capath*."""
    return ssl.DefaultVerifyPaths(
        str(cafile) if cafile.is_file() else None,
        str(capath) if capath.is_dir() else None,
        "SSL_CERT_FILE",
        str(cafile),
        "SSL_CERT_DIR",
        str(capath),
    )


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A Linux machine whose OpenSSL looks in <root>/etc/ssl, and whose bundles are under <root>."""
    root = tmp_path / "root"
    (root / "etc/ssl").mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "linux")
    _clear_cert_env(monkeypatch)
    monkeypatch.setattr(
        ssl,
        "get_default_verify_paths",
        lambda: _defaults(root / "etc/ssl/cert.pem", root / "etc/ssl/certs"),
    )
    monkeypatch.setattr(
        ca_certs,
        "SYSTEM_BUNDLES",
        tuple(str(root / path.lstrip("/")) for path in ca_certs.SYSTEM_BUNDLES),
    )
    # certifi's bundle, unless a test takes it away.
    certifi_bundle = tmp_path / "certifi" / "cacert.pem"
    certifi_bundle.parent.mkdir()
    certifi_bundle.write_text(PEM)
    import certifi

    monkeypatch.setattr(certifi, "where", lambda: str(certifi_bundle))
    return types.SimpleNamespace(root=root, certifi=certifi_bundle)


def _rhel8(root: Path) -> None:
    """RHEL 8's layout: no /etc/ssl/cert.pem; /etc/ssl/certs links to a directory without hashed names."""
    extracted = root / "etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
    extracted.parent.mkdir(parents=True)
    extracted.write_text(PEM)
    certs = root / "etc/pki/tls/certs"
    certs.mkdir(parents=True)
    (certs / "ca-bundle.crt").symlink_to(extracted)
    (certs / "ca-bundle.trust.crt").write_text(PEM)
    (root / "etc/pki/tls/cert.pem").symlink_to(extracted)
    (root / "etc/ssl/certs").symlink_to(certs)


def test_rhel8_layout_gets_the_system_bundle(machine):
    _rhel8(machine.root)
    environ: dict[str, str] = {}

    chosen = ca_certs.ensure(environ)

    assert chosen == str(machine.root / "etc/pki/tls/cert.pem")
    assert environ == {"SSL_CERT_FILE": chosen}


def test_sets_the_process_environment_by_default(machine):
    _rhel8(machine.root)

    chosen = ca_certs.ensure()

    assert chosen is not None
    assert os.environ["SSL_CERT_FILE"] == chosen


def test_default_cafile_present_changes_nothing(machine):
    _rhel8(machine.root)
    (machine.root / "etc/ssl/cert.pem").write_text(PEM)  # RHEL 9, Alpine, macOS
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) is None
    assert environ == {}


def test_hashed_default_directory_changes_nothing(machine):
    certs = machine.root / "etc/ssl/certs"  # Debian and Ubuntu: no cert.pem, a hashed directory
    certs.mkdir()
    (certs / "ca-certificates.crt").write_text(PEM)
    (certs / "5ad8a5d6.0").symlink_to(certs / "ca-certificates.crt")
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) is None
    assert environ == {}


def test_empty_default_cafile_holds_no_certificates(machine):
    _rhel8(machine.root)
    (machine.root / "etc/ssl/cert.pem").write_text("")  # there, but OpenSSL loads nothing from it
    environ: dict[str, str] = {}

    # The empty default is skipped as a candidate too.
    assert ca_certs.ensure(environ) == str(machine.root / "etc/pki/tls/cert.pem")


def test_unreadable_default_cafile_holds_no_certificates(machine, monkeypatch):
    _rhel8(machine.root)
    default = machine.root / "etc/ssl/cert.pem"
    default.write_text(PEM)
    real_access = os.access
    # As for a service account the file's mode leaves out (chmod does not stop root, who runs CI).
    monkeypatch.setattr(ca_certs.os, "access", lambda path, mode: path != str(default) and real_access(path, mode))
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) == str(machine.root / "etc/pki/tls/cert.pem")


@pytest.mark.parametrize("broken", ["dangling link", "empty file", "directory"])
def test_hashed_names_that_hold_nothing_do_not_count(machine, broken):
    _rhel8(machine.root)
    certs = machine.root / "etc/pki/tls/certs"  # what /etc/ssl/certs links to
    hashed = certs / "5ad8a5d6.0"
    if broken == "dangling link":
        hashed.symlink_to(certs / "removed.pem")
    elif broken == "empty file":
        hashed.write_text("")
    else:
        hashed.mkdir()
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) == str(machine.root / "etc/pki/tls/cert.pem")


@pytest.mark.parametrize("name", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_certificates_the_environment_names_win(machine, name):
    _rhel8(machine.root)
    # Even a path that is not there: the operator's choice, not ours to replace.
    environ = {name: "/srv/missing-bundle.pem"}

    assert ca_certs.ensure(environ) is None
    assert environ == {name: "/srv/missing-bundle.pem"}


def test_no_system_bundle_falls_back_to_certifi(machine):
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) == str(machine.certifi)
    assert environ["SSL_CERT_FILE"] == str(machine.certifi)


def test_empty_bundles_are_passed_over(machine):
    debian = machine.root / "etc/ssl/certs/ca-certificates.crt"
    debian.parent.mkdir(parents=True)
    debian.write_text("")  # ca-certificates installed but never generated
    suse = machine.root / "etc/ssl/ca-bundle.pem"
    suse.write_text(PEM)
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) == str(suse)


def test_nothing_to_point_at_changes_nothing(machine, monkeypatch):
    import certifi

    monkeypatch.setattr(certifi, "where", lambda: str(machine.root / "no-such-cacert.pem"))
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) is None
    assert environ == {}


def test_without_certifi_installed_changes_nothing(machine, monkeypatch):
    monkeypatch.setitem(sys.modules, "certifi", None)  # import certifi raises ImportError
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) is None
    assert environ == {}


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_other_platforms_are_left_alone(machine, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    environ: dict[str, str] = {}

    assert ca_certs.ensure(environ) is None
    assert environ == {}


def test_bundle_it_sets_is_what_a_default_context_loads(tmp_path, monkeypatch):
    """End to end through OpenSSL: a default context created afterwards trusts the bundle."""
    import certifi

    real_bundle = certifi.where()
    monkeypatch.setattr(sys, "platform", "linux")
    _clear_cert_env(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: _defaults(empty / "cert.pem", empty))
    monkeypatch.setattr(ca_certs, "SYSTEM_BUNDLES", ())

    assert ca_certs.ensure() == real_bundle

    stats = ssl.create_default_context().cert_store_stats()
    expected = ssl.create_default_context(cafile=real_bundle).cert_store_stats()
    assert stats["x509_ca"] >= expected["x509_ca"] > 0
