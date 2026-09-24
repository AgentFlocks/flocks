"""Check and freeze PostgreSQL settings before stopping an installed service.

This helper intentionally imports no application modules: checking a candidate
installation must not bootstrap its storage or load an existing runtime root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import sys
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

DB_KEYS = {"FLOCKS_DB_BACKEND", "FLOCKS_DB_DSN", "FLOCKS_DB_DSN_FILE"}


class ConfigurationError(ValueError):
    pass


def _environment(path: Path) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if key not in DB_KEYS or not separator:
            continue
        value = value.strip()
        if re.fullmatch(r'"(?:[^"\\]|\\.)*"', value):
            # shlex deliberately keeps \$ and \` inside double quotes, while
            # both supported environment loaders treat these as literals.
            values[key] = re.sub(r'\\(["\\$`])', r'\1', value[1:-1])
            continue
        try:
            words = shlex.split(value, comments=False, posix=True)
        except ValueError:
            raise ConfigurationError("数据库环境文件的引号格式无效") from None
        if len(words) > 1:
            raise ConfigurationError("数据库配置必须是一个值；含空格的路径请使用引号")
        values[key] = words[0] if words else ""
    return values


def resolve(env_file: Path) -> str:
    values = _environment(env_file)
    supplied = {key: os.environ[key] for key in DB_KEYS if os.environ.get(key)}
    if supplied.get("FLOCKS_DB_DSN") or supplied.get("FLOCKS_DB_DSN_FILE"):
        values.pop("FLOCKS_DB_DSN", None)
        values.pop("FLOCKS_DB_DSN_FILE", None)
    values.update(supplied)
    if values.get("FLOCKS_DB_BACKEND", "postgres").strip().lower() not in {"postgres", "postgresql"}:
        raise ConfigurationError("此版本仅支持 PostgreSQL，请先迁移旧 SQLite 数据")
    dsn = values.get("FLOCKS_DB_DSN", "").strip()
    secret = values.get("FLOCKS_DB_DSN_FILE", "").strip()
    if dsn and secret:
        raise ConfigurationError("FLOCKS_DB_DSN 与 FLOCKS_DB_DSN_FILE 只能配置一项")
    if secret:
        path = Path(secret)
        if not path.is_absolute():
            raise ConfigurationError("FLOCKS_DB_DSN_FILE 必须使用绝对路径")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027:
                    raise ConfigurationError("数据库凭证必须是普通文件，且禁止其他用户读取或组写入（建议 chmod 600）")
                data = stream.read(16385)
                if len(data) > 16384:
                    raise ConfigurationError("数据库凭证文件超过 16 KiB")
                dsn = data.decode("utf-8").strip()
        except (OSError, UnicodeError):
            raise ConfigurationError("无法安全读取 FLOCKS_DB_DSN_FILE") from None
    if not dsn:
        raise ConfigurationError("安装前必须配置 FLOCKS_DB_DSN 或 FLOCKS_DB_DSN_FILE")
    if any(character in dsn for character in ("\n", "\r", "\x00")):
        raise ConfigurationError("数据库连接字符串必须是单行文本")
    try:
        valid = urlsplit(dsn).scheme in {"postgres", "postgresql"}
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("数据库连接字符串必须使用 postgresql:// 或 postgres://")
    return dsn


async def check_connection(dsn: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(dsn, timeout=5, command_timeout=5)
    try:
        async with connection.transaction(readonly=True):
            assert await connection.fetchval("SELECT 1", timeout=5) == 1
    finally:
        await connection.close(timeout=5)


def prepare(args) -> None:
    dsn = resolve(args.env_file)
    # Standalone bundled Python has no third-party packages. Load only the
    # candidate package's vendored venv, before the payload is moved into place.
    for directory in sorted((args.payload_root / "flocks" / ".venv" / "lib").glob("python*/site-packages")):
        sys.path.insert(0, str(directory))
    asyncio.run(check_connection(dsn))
    destination = args.snapshot / "dsn"
    with destination.open("x", encoding="utf-8") as stream:
        os.chmod(destination, 0o600)
        stream.write(dsn + "\n")
    print("PostgreSQL 连接预检通过（只读检查，未修改数据库）")


def _quote(value: str) -> str:
    # Compatible with both systemd EnvironmentFile and the fallback shell
    # loader. Never let credential-path characters become shell expressions.
    if any(character in value for character in ("\n", "\r", "\x00")):
        raise ConfigurationError("数据库凭证路径必须是单行文本")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def _install_file(path: Path, content: bytes, *, group: int) -> None:
    if path.exists():
        if path.read_bytes() == content:
            os.chmod(path, 0o640)
            os.chown(path, os.geteuid(), group)
            return
        backup = path.with_name(path.name + ".pg-bak-" + uuid4().hex)
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".flocks-pg-", delete=False) as stream:
        pending = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(pending, 0o640)
    os.chown(pending, os.geteuid(), group)
    os.replace(pending, path)


def install(args) -> None:
    group = pwd.getpwnam(args.service_user).pw_gid
    dsn_file = args.env_file.with_name(args.env_file.name + ".postgres-dsn")
    credential = (args.snapshot / "dsn").read_bytes()
    _install_file(dsn_file, credential, group=group)
    lines = [line for line in args.env_file.read_text(encoding="utf-8").splitlines()
             if line.partition("=")[0].strip() not in DB_KEYS]
    lines.extend(("FLOCKS_DB_BACKEND=postgres", "FLOCKS_DB_DSN_FILE=" + _quote(str(dsn_file))))
    _install_file(args.env_file, ("\n".join(lines) + "\n").encode(), group=group)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "install"))
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--payload-root", type=Path)
    parser.add_argument("--service-user", default="flocks")
    args = parser.parse_args()
    try:
        os.umask(0o077)
        if args.operation == "prepare":
            prepare(args)
        else:
            install(args)
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        # Drivers often include hosts or credentials in exception messages.
        print(f"PostgreSQL 预检或凭证保存失败（{type(error).__name__}）；请检查连接与文件权限", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
