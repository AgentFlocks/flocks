"""
Proxy settings, bypass rules and the per-URL routing decision.

Configuration lives in ``flocks.json``::

    "network": {
      "proxy": {
        "mode": "manual",                      // off | system | manual
        "http": "http://127.0.0.1:7890",       // for http:// targets
        "https": "http://127.0.0.1:7890",      // for https:// targets
        "all": "socks5h://127.0.0.1:7891",     // fallback when http / https are empty
        "username": "",                        // optional proxy credentials
        "password": "{secret:network_proxy_password}",
        "bypass": ["*.corp.example", "10.0.0.0/8", "gitlab.internal:8443"],
        "bypassPrivate": true                  // loopback / link-local / RFC1918 / ULA go direct
      }
    }

``mode``:

- ``off``     — never use a proxy, and hide ``HTTP_PROXY``-style variables from the
                process so libraries that trust the environment stay direct too.
- ``system``  — default. Follow ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY`` /
                ``NO_PROXY`` exactly as the operator exported them.
- ``manual``  — use the URLs above; they are also exported to the environment so
                every trust_env library and child process sees the same thing.

Environment overrides (handy for systemd units and containers, no JSON editing):
``FLOCKS_PROXY_MODE``, ``FLOCKS_PROXY_URL`` (applies to http, https and all),
``FLOCKS_PROXY_BYPASS`` (comma separated rules).

Bypass rules (``bypass`` / ``noProxy``), checked against the request host before
any DNS lookup:

- ``*``                       everything goes direct
- ``<local>``                 hosts without a dot (``intranet-wiki``)
- ``localhost`` / ``host``    exact host, case-insensitive
- ``.corp.example`` / ``*.corp.example``  the domain and every subdomain
- ``host:8443``               only that port
- ``10.1.2.3`` / ``[::1]``    literal address
- ``10.0.0.0/8`` / ``fd00::/8``  CIDR block (literal addresses only)

Loopback (``localhost`` / ``*.localhost`` / ``127.0.0.0/8`` / ``::1`` / ``0.0.0.0``)
always goes direct: a proxy's loopback is a different machine, so proxying it can
only break the local services flocks talks to (its own server, Chrome's CDP port,
the WhatsApp bridge, workflow services). Chrome's ``<-loopback>`` rule opts out of
that for tests and exotic setups.

With ``bypassPrivate`` on (default) every link-local, RFC1918 and IPv6 ULA address
plus ``*.local`` / ``*.internal`` / ``*.lan`` / ``*.home.arpa`` goes direct too,
without listing them.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

from flocks.utils.log import Log

log = Log.create(service="net.proxy")

PROXY_MODES = ("off", "system", "manual")
#: What every stack we drive can actually speak: httpx has no SOCKS4, python-socks
#: (aiohttp) has no https-to-proxy TLS, so the common denominator is these four.
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
_DEFAULT_PROXY_PORTS = {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080}

#: Variables every HTTP stack understands. Upper and lower case both exist in the
#: wild (curl reads ``http_proxy`` only in lower case), so we write both.
PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

#: Always direct unless the list contains ``<-loopback>`` (Chrome's opt-out syntax).
LOOPBACK_BYPASS_RULES: Tuple[str, ...] = (
    "localhost",
    "*.localhost",
    "127.0.0.1",
    "127.0.0.0/8",
    "::1",
    "0.0.0.0",
)

#: Applied on top of the user's list when ``bypassPrivate`` is on.
PRIVATE_BYPASS_RULES: Tuple[str, ...] = (
    "*.local",
    "*.internal",
    "*.lan",
    "*.home.arpa",
    "169.254.0.0/16",
    "fe80::/10",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)

#: Everything that ``bypassPrivate`` implies (loopback included, for display).
DEFAULT_BYPASS_RULES: Tuple[str, ...] = LOOPBACK_BYPASS_RULES + PRIVATE_BYPASS_RULES

LOOPBACK_OPT_OUT = "<-loopback>"

_CONFIG_CHECK_INTERVAL = 1.0  # seconds between stat() calls on flocks.json
_RESOLVED_TTL = 2.0  # seconds a resolved ``system`` decision is reused before re-reading env / OS
_resolved_cache: Dict[Tuple[Any, Tuple[Optional[str], ...]], Tuple[float, "ProxySettings"]] = {}


# ---------------------------------------------------------------------------
# bypass rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    kind: str  # all | local | host | suffix | port | ip | cidr
    value: Any = None
    port: Optional[int] = None

    def matches(self, host: str, port: Optional[int]) -> bool:
        if self.kind == "all":
            return True
        if self.kind == "local":
            return "." not in host and not _as_ip(host)
        if self.port is not None and port is not None and port != self.port:
            return False
        if self.port is not None and port is None:
            return False
        if self.kind == "host":
            return host == self.value
        if self.kind == "suffix":
            return host == self.value or host.endswith("." + self.value)
        if self.kind == "ip":
            addr = _as_ip(host)
            return addr is not None and addr == self.value
        if self.kind == "cidr":
            addr = _as_ip(host)
            return addr is not None and addr in self.value
        return False


def _as_ip(host: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address(host.strip("[]").split("%")[0])
    except ValueError:
        return None


def _split_host_port(raw: str) -> Tuple[str, Optional[int]]:
    """``host``, ``host:port``, ``[v6]``, ``[v6]:port`` → (host, port)."""
    text = raw.strip()
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return text.lower(), None
        host = text[1:end]
        rest = text[end + 1 :]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else None
        return host.lower(), port
    if text.count(":") == 1:
        host, _, port_text = text.partition(":")
        if port_text.isdigit():
            return host.lower(), int(port_text)
    return text.lower(), None


_warned_rules: set = set()


def _pad_ipv4_cidr(text: str) -> str:
    """``169.254/16`` (macOS System Preferences shorthand) → ``169.254.0.0/16``."""
    network, _, prefix = text.partition("/")
    if ":" in network or not prefix.isdigit():
        return text
    octets = network.split(".")
    if 1 <= len(octets) < 4 and all(o.isdigit() for o in octets):
        network = ".".join(octets + ["0"] * (4 - len(octets)))
    return f"{network}/{prefix}"


def parse_bypass_rule(raw: str) -> Optional[_Rule]:
    """Turn one user-written rule into a matcher; ``None`` for blanks."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text == "*":
        return _Rule("all")
    if text == "<local>":
        return _Rule("local")
    if text == LOOPBACK_OPT_OUT:
        return None  # handled by ProxySettings.build; not a matcher
    if "/" in text and not text.startswith("*"):
        try:
            return _Rule("cidr", ipaddress.ip_network(_pad_ipv4_cidr(text), strict=False))
        except ValueError:
            if text not in _warned_rules:
                _warned_rules.add(text)
                log.warn("net.proxy.bypass.invalid_cidr", {"rule": raw})
            return None
    host, port = _split_host_port(text)
    if host.startswith("*."):
        host = host[2:]
        return _Rule("suffix", host.rstrip("."), port)
    if host.startswith("."):
        return _Rule("suffix", host.strip("."), port)
    addr = _as_ip(host)
    if addr is not None:
        return _Rule("ip", addr, port)
    return _Rule("host", host.rstrip("."), port)


def _opts_out_of_loopback(rules: Iterable[str]) -> bool:
    return any(piece.strip().lower() == LOOPBACK_OPT_OUT for raw in rules if isinstance(raw, str) for piece in raw.split(","))


def parse_bypass_rules(rules: Iterable[str]) -> Tuple[_Rule, ...]:
    parsed = []
    for raw in rules:
        if not isinstance(raw, str):
            continue
        for piece in raw.split(","):
            rule = parse_bypass_rule(piece)
            if rule is not None:
                parsed.append(rule)
    return tuple(parsed)


# ---------------------------------------------------------------------------
# OS proxy settings (only consulted when the environment says nothing)
# ---------------------------------------------------------------------------


def _os_proxy_settings() -> Tuple[Optional[str], Optional[str], List[str]]:
    """``(http, https, bypass)`` from macOS SystemConfiguration / the Windows registry.

    The platform readers are called directly rather than through
    ``urllib.request.getproxies()``: that one returns early on *any* proxy variable
    in the environment, including a lone ``NO_PROXY=*`` left by ``off`` mode. The
    ``socks`` entry is ignored on purpose: macOS reports it as ``http://host:port``
    and there is no way to tell socks4 from socks5 from that.
    """
    import urllib.request

    try:
        if sys.platform == "darwin":
            proxies = urllib.request.getproxies_macosx_sysconf()  # type: ignore[attr-defined]
        elif sys.platform == "win32":
            proxies = urllib.request.getproxies_registry()  # type: ignore[attr-defined]
        else:
            proxies = {}
    except Exception:  # pragma: no cover - platform helpers can raise on odd configs
        return None, None, []
    http = proxies.get("http")
    https = proxies.get("https")  # no fallback to http: urllib-based libraries do not do it either
    bypass: List[str] = []
    try:
        import _scproxy  # macOS only

        settings = _scproxy._get_proxy_settings() or {}
        bypass = [str(item) for item in settings.get("exceptions", ()) if str(item).strip()]
        if settings.get("exclude_simple"):
            bypass.append("<local>")
    except Exception:
        pass
    return http, https, bypass


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def _normalise_proxy_url(value: Any) -> Optional[str]:
    """Accept ``host:port`` shorthand and validate the scheme."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in PROXY_SCHEMES:
        raise ValueError(f"unsupported proxy scheme {parts.scheme!r} (use http, https, socks5 or socks5h)")
    if not parts.hostname:
        raise ValueError(f"proxy url {value!r} has no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"proxy url {value!r} has an invalid port") from exc
    netloc = parts.netloc
    if port is None:
        # python-socks insists on a port; make the conventional one explicit everywhere
        netloc = f"{netloc}:{_DEFAULT_PROXY_PORTS[scheme]}"
    return urlunsplit((scheme, netloc, parts.path or "", "", ""))


def _with_credentials(url: Optional[str], username: Optional[str], password: Optional[str]) -> Optional[str]:
    if not url or not username:
        return url
    parts = urlsplit(url)
    if parts.username:
        return url  # credentials already inline
    auth = quote(username, safe="") + (":" + quote(password or "", safe="") if password is not None else "")
    hostport = parts.hostname or ""
    if ":" in hostport:
        hostport = f"[{hostport}]"
    if parts.port:
        hostport += f":{parts.port}"
    return urlunsplit((parts.scheme, f"{auth}@{hostport}", parts.path, parts.query, parts.fragment))


def _redact(url: Optional[str]) -> Optional[str]:
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.username:
        return url
    hostport = parts.hostname or ""
    if ":" in hostport:
        hostport = f"[{hostport}]"
    if parts.port:
        hostport += f":{parts.port}"
    return urlunsplit((parts.scheme, f"{parts.username}:***@{hostport}", parts.path, parts.query, parts.fragment))


@dataclass(frozen=True)
class ProxySettings:
    mode: str = "system"
    http: Optional[str] = None
    https: Optional[str] = None
    all: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    bypass: Tuple[str, ...] = ()
    bypass_private: bool = True
    source: str = "default"  # default | config | env | os
    _rules: Tuple[_Rule, ...] = field(default=(), repr=False, compare=False)

    # -- construction -----------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        mode: str = "system",
        http: Any = None,
        https: Any = None,
        all: Any = None,
        username: Any = None,
        password: Any = None,
        bypass: Any = None,
        bypass_private: bool = True,
        source: str = "config",
    ) -> "ProxySettings":
        if mode not in PROXY_MODES:
            raise ValueError(f"unsupported proxy mode {mode!r} (use off, system or manual)")
        rules_raw: List[str] = []
        if isinstance(bypass, str):
            rules_raw = [bypass]
        elif isinstance(bypass, (list, tuple)):
            rules_raw = [str(item) for item in bypass if isinstance(item, str)]
        effective_rules = list(rules_raw)
        if bypass_private:
            effective_rules = list(PRIVATE_BYPASS_RULES) + effective_rules
        if not _opts_out_of_loopback(rules_raw):
            effective_rules = list(LOOPBACK_BYPASS_RULES) + effective_rules
        return cls(
            mode=mode,
            http=_normalise_proxy_url(http),
            https=_normalise_proxy_url(https),
            all=_normalise_proxy_url(all),
            username=(username or None) if isinstance(username, str) else None,
            password=(password or None) if isinstance(password, str) else None,
            bypass=tuple(r.strip() for r in rules_raw if r and r.strip()),
            bypass_private=bool(bypass_private),
            source=source,
            _rules=parse_bypass_rules(effective_rules),
        )

    @classmethod
    def from_environment(cls, env: Optional[Mapping[str, str]] = None, *, bypass_private: bool = True) -> "ProxySettings":
        """What ``mode: system`` resolves to right now."""
        env = os.environ if env is None else env

        def pick(*names: str) -> Optional[str]:
            for name in names:
                value = env.get(name)
                if value:
                    return value
            return None

        no_proxy = pick("NO_PROXY", "no_proxy") or ""
        http = pick("HTTP_PROXY", "http_proxy")
        https = pick("HTTPS_PROXY", "https_proxy")
        everything = pick("ALL_PROXY", "all_proxy")
        bypass = [part for part in no_proxy.split(",") if part.strip()]
        source = "env"
        if not (http or https or everything) and env is os.environ:
            # nothing exported: fall back to the OS proxy settings (macOS System
            # Preferences / Windows registry), which is what every trust_env library
            # does through urllib anyway — this keeps our explicit clients in step.
            http, https, bypass = _os_proxy_settings()
            if http or https:
                source = "os"
        try:
            return cls.build(
                mode="manual" if (http or https or everything) else "off",
                http=http,
                https=https,
                all=everything,
                bypass=bypass,
                bypass_private=bypass_private,
                source=source,
            )
        except ValueError as exc:
            log.warn("net.proxy.env.invalid", {"error": str(exc)})
            return cls.build(mode="off", bypass_private=bypass_private, source=source)

    # -- decisions --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.mode == "manual" and bool(self.http or self.https or self.all)

    def resolved(self) -> "ProxySettings":
        """``system`` mode collapses to whatever the environment (or the OS) says.

        Called on every routing decision, so the answer is memoised per
        (settings, proxy variables) for a couple of seconds: reading the OS proxy
        settings and re-parsing the rules costs ~0.15 ms, which adds up on busy
        request paths.
        """
        if self.mode != "system":
            return self
        env_key = tuple(os.environ.get(name) for name in PROXY_ENV_NAMES)
        now = time.monotonic()
        with _lock:
            cached = _resolved_cache.get((self, env_key))
            if cached is not None and now - cached[0] < _RESOLVED_TTL:
                return cached[1]
        env_settings = ProxySettings.from_environment(bypass_private=self.bypass_private)
        merged_rules = list(env_settings.bypass) + list(self.bypass)
        result = ProxySettings.build(
            mode=env_settings.mode,
            http=env_settings.http,
            https=env_settings.https,
            all=env_settings.all,
            bypass=merged_rules,
            bypass_private=self.bypass_private,
            source=env_settings.source,
        )
        with _lock:
            _resolved_cache.clear()
            _resolved_cache[(self, env_key)] = (now, result)
        return result

    def is_bypassed(self, host: Optional[str], port: Optional[int] = None) -> bool:
        if not host:
            return True
        name = host.strip().strip("[]").rstrip(".").lower()
        return any(rule.matches(name, port) for rule in self._rules)

    def proxy_url_for(self, url: str) -> Optional[str]:
        """The proxy to use for ``url`` or ``None`` for a direct connection."""
        settings = self.resolved()
        if settings.mode != "manual":
            return None
        parts = urlsplit(url if "://" in url else "http://" + url)
        scheme = (parts.scheme or "http").lower()
        host = parts.hostname
        port = parts.port
        if port is None:
            port = {"http": 80, "ws": 80, "https": 443, "wss": 443}.get(scheme)
        if settings.is_bypassed(host, port):
            return None
        if scheme in ("https", "wss"):
            chosen = settings.https or settings.all
        else:
            chosen = settings.http or settings.all
        return _with_credentials(chosen, settings.username, settings.password)

    def env_map(self) -> Dict[str, str]:
        """The variables a child process (or a trust_env library) should see."""
        settings = self.resolved()
        result: Dict[str, str] = {}
        if settings.mode == "manual":
            http = _with_credentials(settings.http or settings.all, settings.username, settings.password)
            https = _with_credentials(settings.https or settings.all, settings.username, settings.password)
            everything = _with_credentials(settings.all, settings.username, settings.password)
            if http:
                result["HTTP_PROXY"] = http
            if https:
                result["HTTPS_PROXY"] = https
            if everything:
                result["ALL_PROXY"] = everything
            no_proxy = settings.no_proxy_value()
            if no_proxy:
                result["NO_PROXY"] = no_proxy
        elif settings.mode == "off":
            result["NO_PROXY"] = "*"
        for name in list(result):
            result[name.lower()] = result[name]
        return result

    def no_proxy_value(self) -> str:
        """``NO_PROXY`` in the format curl / Go / requests understand (CIDRs included)."""
        rules: List[str] = []
        if not _opts_out_of_loopback(self.bypass):
            rules.extend(LOOPBACK_BYPASS_RULES)
        if self.bypass_private:
            rules.extend(PRIVATE_BYPASS_RULES)
        rules.extend(self.bypass)
        seen: List[str] = []
        for rule in rules:
            text = rule.strip()
            if text.startswith("*."):
                text = text[1:]  # NO_PROXY convention is ".example.com"
            if text in ("<local>", LOOPBACK_OPT_OUT):
                continue
            if text and text not in seen:
                seen.append(text)
        return ",".join(seen)

    def public_dict(self) -> Dict[str, Any]:
        """For API responses and logs: credentials never leave the process."""
        resolved = self.resolved()
        active = resolved.mode == "manual" and bool(resolved.http or resolved.https or resolved.all)
        return {
            "mode": self.mode,
            "http": _redact(self.http),
            "https": _redact(self.https),
            "all": _redact(self.all),
            "username": self.username,
            "hasPassword": bool(self.password),
            "bypass": list(self.bypass),
            "bypassPrivate": self.bypass_private,
            "source": self.source,
            "effective": {
                "active": active,
                "http": _redact(resolved.http or resolved.all) if active else None,
                "https": _redact(resolved.https or resolved.all) if active else None,
                "bypass": list(resolved.bypass),
                # where the decision came from: config | env | os (OS proxy settings) | default
                "source": resolved.source,
            },
        }


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _section(raw: Mapping[str, Any]) -> Dict[str, Any]:
    network = raw.get("network")
    if not isinstance(network, Mapping):
        return {}
    proxy = network.get("proxy")
    return dict(proxy) if isinstance(proxy, Mapping) else {}


def _pick(section: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in section:
            return section[name]
    return None


def load_proxy_settings(raw: Optional[Mapping[str, Any]], *, env: Optional[Mapping[str, str]] = None) -> ProxySettings:
    """Build settings from a raw ``flocks.json`` dict plus ``FLOCKS_PROXY_*`` overrides."""
    env = os.environ if env is None else env
    section = _section(raw or {})
    source = "config" if section else "default"

    mode = _pick(section, "mode")
    if not isinstance(mode, str) or mode not in PROXY_MODES:
        mode = "system"
    http = _pick(section, "http", "httpProxy", "http_proxy")
    https = _pick(section, "https", "httpsProxy", "https_proxy")
    all_ = _pick(section, "all", "allProxy", "all_proxy", "url")
    username = _pick(section, "username", "user")
    password = _pick(section, "password")
    bypass = _pick(section, "bypass", "noProxy", "no_proxy", "whitelist")
    bypass_private = _pick(section, "bypassPrivate", "bypass_private")
    if bypass_private is None:
        bypass_private = True

    if isinstance(password, str) and "{secret:" in password:
        try:
            # not Config.replace_secret_refs: that one JSON-escapes the value (it is
            # meant for text that gets parsed again), which would mangle passwords
            # containing quotes or backslashes
            from flocks.security import resolve_secret_refs

            password = resolve_secret_refs(password)
        except Exception as exc:  # pragma: no cover - secret store unavailable
            log.warn("net.proxy.password.unresolved", {"error": str(exc)})
            password = None

    override_mode = env.get("FLOCKS_PROXY_MODE")
    override_url = env.get("FLOCKS_PROXY_URL")
    override_bypass = env.get("FLOCKS_PROXY_BYPASS")
    if override_mode and override_mode in PROXY_MODES:
        mode = override_mode
        source = "env"
    if override_url:
        http = https = None
        all_ = override_url
        mode = "manual"
        source = "env"
    if override_bypass is not None:
        bypass = [part for part in override_bypass.split(",") if part.strip()]
        source = "env"

    try:
        return ProxySettings.build(
            mode=mode,
            http=http,
            https=https,
            all=all_,
            username=username,
            password=password,
            bypass=bypass,
            bypass_private=bool(bypass_private),
            source=source,
        )
    except ValueError as exc:
        log.error("net.proxy.config.invalid", {"error": str(exc)})
        return ProxySettings.build(mode="off", bypass_private=bool(bypass_private), source=source)


_lock = threading.Lock()
_cached: Optional[ProxySettings] = None
_cached_mtime: Optional[float] = None
_cached_check: float = 0.0
_override: Optional[ProxySettings] = None


def _config_path():
    from flocks.config.config_writer import ConfigWriter

    return ConfigWriter._get_config_path()


def _read_raw() -> Dict[str, Any]:
    from flocks.config.config_writer import ConfigWriter

    try:
        return ConfigWriter._read_raw()
    except Exception as exc:  # pragma: no cover - unreadable config
        log.warn("net.proxy.config.unreadable", {"error": str(exc)})
        return {}


def get_proxy_settings() -> ProxySettings:
    """Current settings; re-reads ``flocks.json`` when its mtime changes (stat at most once a second)."""
    global _cached, _cached_mtime, _cached_check
    if _override is not None:
        return _override
    now = time.monotonic()
    with _lock:
        if _cached is not None and now - _cached_check < _CONFIG_CHECK_INTERVAL:
            return _cached
        _cached_check = now
        try:
            mtime = os.stat(_config_path()).st_mtime
        except OSError:
            mtime = None
        if _cached is not None and mtime == _cached_mtime:
            return _cached
        _cached = load_proxy_settings(_read_raw())
        _cached_mtime = mtime
        return _cached


def _drop_cache() -> None:
    global _cached, _cached_mtime, _cached_check
    with _lock:
        _cached = None
        _cached_mtime = None
        _cached_check = 0.0
        _resolved_cache.clear()


def invalidate_proxy_settings() -> ProxySettings:
    """Drop the cache (after a config write) and return the fresh settings."""
    _drop_cache()
    return get_proxy_settings()


def set_proxy_settings_override(settings: Optional[ProxySettings]) -> None:
    """Tests and one-off tools: pin the settings instead of reading flocks.json."""
    global _override
    _override = settings


def proxy_url_for(url: str, settings: Optional[ProxySettings] = None) -> Optional[str]:
    return (settings or get_proxy_settings()).proxy_url_for(url)


def is_bypassed(host: Optional[str], port: Optional[int] = None, settings: Optional[ProxySettings] = None) -> bool:
    return (settings or get_proxy_settings()).resolved().is_bypassed(host, port)


# ---------------------------------------------------------------------------
# hand-offs to the other stacks
# ---------------------------------------------------------------------------


#: The proxy variables the process was started with, taken the first time
#: :func:`apply_process_environment` touches ``os.environ``. ``system`` mode puts
#: them back, so ``off → system`` (or ``manual → system``) does not leave the
#: ``NO_PROXY=*`` / manual URLs of the previous mode behind.
_startup_env: Optional[Dict[str, str]] = None

#: The snapshot travels to the processes flocks starts itself (``flocks start`` →
#: supervisor → backend, ``flocks tui`` → server) in this variable: they inherit an
#: environment the CLI already rewrote, so their own look at ``os.environ`` would
#: capture the *decision*, not what the operator exported. ``FLOCKS_`` names pass
#: the managed launcher's allow-list.
STARTUP_ENV_VAR = "FLOCKS_PROXY_STARTUP_ENV"


def _remember_startup_env() -> Dict[str, str]:
    global _startup_env
    if _startup_env is None:
        snapshot: Optional[Dict[str, str]] = None
        raw = os.environ.get(STARTUP_ENV_VAR)
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            if isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
                snapshot = {name: value for name, value in data.items() if name in PROXY_ENV_NAMES}
        if snapshot is None:
            snapshot = {name: os.environ[name] for name in PROXY_ENV_NAMES if name in os.environ}
            os.environ[STARTUP_ENV_VAR] = json.dumps(snapshot, sort_keys=True)
        _startup_env = snapshot
    return _startup_env


def apply_process_environment(settings: Optional[ProxySettings] = None, env: Optional[MutableMapping[str, str]] = None) -> Dict[str, str]:
    """Push the decision into the environment (call at startup and after a config change).

    ``manual`` writes HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / NO_PROXY; ``off`` removes
    them and sets ``NO_PROXY=*``; ``system`` restores whatever the operator exported
    at startup. Returns the variables that ended up set.
    """
    settings = settings or get_proxy_settings()
    env = os.environ if env is None else env
    startup = _remember_startup_env() if env is os.environ else None
    if settings.mode == "system":
        if startup is not None:
            for name in PROXY_ENV_NAMES:
                if name in startup:
                    env[name] = startup[name]
                else:
                    env.pop(name, None)
        return {name: env[name] for name in PROXY_ENV_NAMES if name in env}
    values = settings.env_map()
    for name in PROXY_ENV_NAMES:
        if name in values:
            env[name] = values[name]
        else:
            env.pop(name, None)
    log.info(
        "net.proxy.environment.applied",
        {"mode": settings.mode, "https": _redact(values.get("HTTPS_PROXY")), "no_proxy": values.get("NO_PROXY", "")[:200]},
    )
    return values


def _is_socks(url: Optional[str]) -> bool:
    return bool(url) and url.lower().startswith("socks")


def child_env(
    base: Optional[Mapping[str, str]] = None,
    settings: Optional[ProxySettings] = None,
    *,
    socks_capable: bool = True,
) -> Dict[str, str]:
    """A copy of ``base`` (default ``os.environ``) with the proxy variables set the same way.

    ``socks_capable=False`` is for children whose HTTP stack only understands http
    proxies (Bun's and Node's ``fetch`` raise on a ``socks://`` URL — verified with
    bun 1.4 / node 26): a SOCKS entry is dropped so that child goes direct instead
    of failing every request. ``system`` mode leaves the environment untouched.
    """
    result = dict(os.environ if base is None else base)
    settings = settings or get_proxy_settings()
    if settings.mode == "system":
        return result
    values = settings.env_map()
    if not socks_capable:
        dropped = [name for name, value in values.items() if name.lower() != "no_proxy" and _is_socks(value)]
        for name in dropped:
            values.pop(name)
        if dropped:
            log.warn("net.proxy.child.socks_unsupported", {"dropped": sorted(dropped)})
    for name in PROXY_ENV_NAMES:
        if name in values:
            result[name] = values[name]
        else:
            result.pop(name, None)
    return result


def container_env(settings: Optional[ProxySettings] = None, *, gateway: str = "host.docker.internal") -> Dict[str, str]:
    """Proxy variables for a container: a loopback proxy on the host becomes ``gateway``.

    Mirrors what the workflow runtime does for its docker services; ``NO_PROXY`` is
    passed through unchanged (it names targets, not the proxy).
    """
    settings = (settings or get_proxy_settings()).resolved()
    if settings.mode != "manual":
        # system mode: whatever the host process has, translated the same way
        values = {name: os.environ[name] for name in PROXY_ENV_NAMES if os.environ.get(name)}
    else:
        values = settings.env_map()
    result: Dict[str, str] = {}
    for name, value in values.items():
        if name.upper() == "NO_PROXY":
            result[name] = value
            continue
        parts = urlsplit(value if "://" in value else "http://" + value)
        if (parts.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}:
            hostport = gateway + (f":{parts.port}" if parts.port else "")
            auth = f"{parts.username}:{parts.password}@" if parts.username and parts.password else (f"{parts.username}@" if parts.username else "")
            value = urlunsplit((parts.scheme, auth + hostport, parts.path, parts.query, parts.fragment))
        result[name] = value
    return result


def requests_proxies(url: str, settings: Optional[ProxySettings] = None) -> Dict[str, Optional[str]]:
    """``proxies=`` for ``requests``; an explicit ``None`` per scheme means "direct, ignore env"."""
    proxy = proxy_url_for(url, settings)
    if proxy is None:
        return {"http": None, "https": None}
    return {"http": proxy, "https": proxy}


def chromium_proxy_args(settings: Optional[ProxySettings] = None) -> List[str]:
    """``--proxy-server`` / ``--proxy-bypass-list`` for headless Chrome (socks5:// included)."""
    settings = (settings or get_proxy_settings()).resolved()
    if settings.mode != "manual":
        return []
    server = settings.https or settings.http or settings.all
    if not server:
        return []
    parts = urlsplit(server)
    scheme = parts.scheme.replace("socks5h", "socks5")  # Chrome resolves through SOCKS5 proxies anyway
    hostport = parts.hostname or ""
    if ":" in hostport:
        hostport = f"[{hostport}]"
    if parts.port:
        hostport += f":{parts.port}"
    args = [f"--proxy-server={scheme}://{hostport}"]
    # Chrome understands "<local>", "<-loopback>", "*.example.com", "10.0.0.0/8" and exact hosts.
    bypass = [part for part in settings.no_proxy_value().split(",") if part]
    if settings.bypass_private:
        bypass.append("<local>")
    if _opts_out_of_loopback(settings.bypass):
        bypass.append(LOOPBACK_OPT_OUT)
    if bypass:
        args.append("--proxy-bypass-list=" + ";".join(bypass))
    if parts.username:
        log.warn("net.proxy.chromium.credentials_unsupported", {"proxy": _redact(server)})
    return args



_HOST_RE = re.compile(r"^[a-z0-9.\-_\[\]:*<>]+$")


def validate_bypass_rules(rules: Iterable[str]) -> List[str]:
    """Return the offending entries (for API validation)."""
    bad: List[str] = []
    for raw in rules:
        text = (raw or "").strip().lower()
        if not text:
            continue
        if text == LOOPBACK_OPT_OUT:
            continue
        if "/" in text and not text.startswith("*"):
            try:
                ipaddress.ip_network(_pad_ipv4_cidr(text), strict=False)
            except ValueError:
                bad.append(raw)
            continue
        if not _HOST_RE.match(text) or parse_bypass_rule(text) is None:
            bad.append(raw)
    return bad


def describe_for_log(settings: Optional[ProxySettings] = None) -> Dict[str, Any]:
    settings = settings or get_proxy_settings()
    public = settings.public_dict()
    return {"mode": public["mode"], "source": public["source"], "effective": public["effective"]}



__all__ = [
    "DEFAULT_BYPASS_RULES",
    "LOOPBACK_BYPASS_RULES",
    "LOOPBACK_OPT_OUT",
    "PRIVATE_BYPASS_RULES",
    "PROXY_ENV_NAMES",
    "PROXY_MODES",
    "PROXY_SCHEMES",
    "STARTUP_ENV_VAR",
    "ProxySettings",
    "apply_process_environment",
    "child_env",
    "chromium_proxy_args",
    "container_env",
    "describe_for_log",
    "get_proxy_settings",
    "invalidate_proxy_settings",
    "is_bypassed",
    "load_proxy_settings",
    "parse_bypass_rule",
    "parse_bypass_rules",
    "proxy_url_for",
    "requests_proxies",
    "set_proxy_settings_override",
    "validate_bypass_rules",
]
