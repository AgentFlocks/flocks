"""Certificates and a local HTTPS gateway for the provider TLS tests.

Keys and certificates are made while the tests run, so none is kept in the
repository.
"""

from __future__ import annotations

import contextlib
import datetime
import ipaddress
import json
import ssl
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator, Optional


def assert_no_verify_context(verify) -> None:
    """``verify_ssl: false`` gives an httpx ``verify`` context that checks nothing."""
    assert isinstance(verify, ssl.SSLContext), verify
    assert verify.verify_mode == ssl.CERT_NONE
    assert verify.check_hostname is False


def describe_verify(verify):
    """``True`` for httpx's default checks, ``"no-verify"`` for the relaxed context."""
    if verify is True:
        return True
    if isinstance(verify, ssl.SSLContext) and verify.verify_mode == ssl.CERT_NONE:
        return "no-verify"
    return verify


@dataclass
class Credential:
    cert_pem: str
    key_pem: str


def _name(common_name: str):
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _make(subject: str, *, issuer: Optional[Credential] = None, ca: bool = False, key_size: int = 2048) -> Credential:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    now = datetime.datetime.now(datetime.timezone.utc)
    if issuer is None:
        issuer_name, signing_key = _name(subject), key
    else:
        issuer_cert = x509.load_pem_x509_certificate(issuer.cert_pem.encode())
        issuer_name = issuer_cert.subject
        signing_key = serialization.load_pem_private_key(issuer.key_pem.encode(), password=None)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
    else:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
    cert = builder.sign(signing_key, hashes.SHA256())
    return Credential(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_pem=key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
    )


def make_ca() -> Credential:
    return _make("Flocks Test Internal CA", ca=True)


def make_server(issuer: Optional[Credential] = None) -> Credential:
    """A gateway certificate for 127.0.0.1 / localhost: issued by *issuer*, or self-signed."""
    return _make("llm-gateway.test", issuer=issuer)


MODEL_ID = "gw-test-model"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def _json(self, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._json({"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        if request.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in (
                {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": MODEL_ID,
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Paris"}, "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": MODEL_ID,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ):
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.close_connection = True
            return
        self._json({
            "id": "c1", "object": "chat.completion", "created": 0, "model": MODEL_ID,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Paris"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


@contextlib.contextmanager
def https_gateway(
    credential: Credential,
    tmp_path,
    *,
    ciphers: Optional[str] = None,
    maximum_version: Optional[ssl.TLSVersion] = None,
) -> Iterator[str]:
    """Serve an OpenAI-style API over HTTPS on 127.0.0.1; yields its ``/v1`` base URL.

    *ciphers* / *maximum_version* make it an older gateway: e.g. RSA key
    exchange only, which CPython's default client settings refuse.
    """
    cert_file = tmp_path / "gateway.crt"
    key_file = tmp_path / "gateway.key"
    cert_file.write_text(credential.cert_pem)
    key_file.write_text(credential.key_pem)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_file), str(key_file))
    if maximum_version is not None:
        context.maximum_version = maximum_version
    if ciphers:
        context.set_ciphers(ciphers)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# Only RSA key exchange (no ECDHE/DHE) in TLS 1.2: what older appliances often
# offer, and what CPython's default client cipher list leaves out.
RSA_KEY_EXCHANGE_ONLY = "AES128-GCM-SHA256:AES256-GCM-SHA384:@SECLEVEL=0"
