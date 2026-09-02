"""Workshop HTTP client — JWKS fetch with TTL cache (PoC, optional).

In production, Workshop exposes ``GET /api/auth/jwks`` with the public keys
used to sign per-team JWTs. Flocks caches the keyset for ``WORKSHOP_JWKS_TTL``
seconds and refreshes lazily on cache miss or kid miss.

The PoC backend (``backend.py``) defaults to HS256 with the shared
``WORKSHOP_JWT_SECRET`` so it can run without a Workshop facade running.
Switch to RS256/JWKS by setting ``WORKSHOP_JWT_ALG=RS256`` and
``WORKSHOP_JWKS_URL=https://.../api/auth/jwks``.

This client is intentionally minimal — no retries, no circuit breaker.
Production hardening lives in the facade-side client.
"""

from __future__ import annotations

import time
from typing import Any

_log_prefix = "workshop_auth.client"


class _JWKSCache:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._fetched_at: float = 0.0
        self._keys: dict[str, Any] = {}

    def get(self, kid: str | None) -> Any | None:
        if not self._keys:
            return None
        if time.monotonic() - self._fetched_at > self._ttl:
            return None  # expired; caller should refresh
        if kid is None:
            return next(iter(self._keys.values()))
        return self._keys.get(kid)

    def store(self, keys: dict[str, Any]) -> None:
        self._keys = keys
        self._fetched_at = time.monotonic()


_cache = _JWKSCache()


async def fetch_jwks(url: str, *, refresh: bool = False) -> dict[str, Any]:
    """Fetch and cache the JWKS document. Returns the key dict keyed by kid."""
    if not refresh:
        cached = _cache.get(kid=None)
        if cached is not None and time.monotonic() - _cache._fetched_at <= _cache._ttl:
            return _cache._keys
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            doc = resp.json()
    except Exception as exc:
        # In PoC we don't fail loudly — the backend has a HS256 fallback path.
        # In production, raise; the caller decides retry policy.
        print(f"[{_log_prefix}] JWKS fetch failed: {exc!r}", flush=True)
        return _cache._keys
    keys = {k["kid"]: k for k in doc.get("keys", []) if "kid" in k}
    _cache.store(keys)
    return keys


async def public_key_for(kid: str | None, jwks_url: str) -> Any | None:
    """Resolve a JWK to a verify-key object usable by pyjwt."""
    jwks = await fetch_jwks(jwks_url)
    jwk = _cache.get(kid=kid) or (next(iter(jwks.values())) if jwks else None)
    if jwk is None:
        return None
    try:
        from jwt.algorithms import RSAAlgorithm

        return RSAAlgorithm.from_jwk(jwk)
    except Exception as exc:
        print(f"[{_log_prefix}] JWK parse failed: {exc!r}", flush=True)
        return None