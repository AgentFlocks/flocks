#!/usr/bin/env python3
"""Bring the data of a previous Flocks installation into the offline install.

install.sh runs this with the bundled Python (stdlib only):

  env-root FILE        print the FLOCKS_ROOT an existing flocks.env gives the service (plain FLOCKS_ROOT=
                       lines only, as systemd and the env merge read it); refuse a relative one
  plan --target DIR    refuse a data dir that is "/" or holds things that are not Flocks data; then
                       print the data directory to bring over, or nothing: FLOCKS_OFFLINE_MIGRATE_FROM
                       first, then the FLOCKS_ROOT the installer was started with, then the default
                       ~/.flocks of the local users; several defaults with data → stop and ask
  check --source --target
                       refuse while a process still has files open under the source, when the data
                       dir is a mount point (it cannot be swapped) or its filesystem cannot hold a copy
  copy --source --target --stamp [--owner USER]
                       copy next to the target, fold in whatever the target already had, hand the copy
                       to USER, rename it into place (the single commit point) and point hub records /
                       config paths at the new place

The source directory is only read. A top-level directory of the source that is a symlink to
somewhere else (data moved to another disk) is copied as a real directory, so the new instance
never shares files with the old one. Nothing is deleted: an existing target without user data is
renamed to <target>.pre-migration-<stamp>. On failure the command prints the reason on stdout and
exits non-zero, so install.sh can hand the text to fail().
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pwd
import re
import shutil
import stat
import sys
from pathlib import Path

# files that only exist once an instance has been started (ensure_config_files / Storage.init)
USER_DATA_MARKERS = ("config/flocks.json", "config/flocks.jsonc", "data/flocks.db")
# what a Flocks data dir may hold at its top level (plus lost+found: a fresh ext4 disk mounted on it);
# anything else means it is not (only) Flocks data, which must never be renamed or chowned
FLOCKS_TOP_LEVEL = frozenset({
    "config", "data", "run", "logs", "plugins", "workspace", "workflow", "flockspro", "flockspro_audit.db",
    "cache", "browser", "projects", "version", "mcp", "plans", "rules", "skills", "skill", "sandboxes",
    "truncated", "flockshub", "flocks.json", ".storage_migrated", "lost+found",
    "chrome-debug-profile", "chromium-debug-profile", "edge-debug-profile", "brave-debug-profile",
})
# JSON / JSONC that stores absolute paths into the data directory: user config (flocks.jsonc and the
# legacy config.json are merged by Config too) and hub install records
PATH_REWRITE_FILES = ("config/flocks.json", "config/flocks.jsonc", "config/config.json", "config/mcp_list.json")
PATH_REWRITE_DIRS = ("data/hub",)
SPACE_MARGIN_BYTES = 200 * 1024 * 1024


class MigrationError(Exception):
    """A condition install.sh must stop on; the message is shown to the operator."""


def has_user_data(root: Path) -> bool:
    return any((root / marker).is_file() for marker in USER_DATA_MARKERS)


def _overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def _prefix(root: Path) -> str:
    """realpath of *root* with exactly one trailing separator ("/" stays "/")."""
    return os.path.realpath(root).rstrip(os.sep) + os.sep


def _inside(path: Path, root: Path) -> bool:
    real = os.path.realpath(path)
    return real == os.path.realpath(root) or real.startswith(_prefix(root))


def configured_root(env_file: Path) -> str:
    """The FLOCKS_ROOT the service gets from *env_file*, read the way systemd and install.sh's env merge
    read it: only plain FLOCKS_ROOT= lines count and the last one wins. An `export FLOCKS_ROOT=` line is
    not an assignment for either (the merge then appends the template's FLOCKS_ROOT=), so it is ignored."""
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    value = ""
    for line in lines:
        key, sep, raw = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key == "FLOCKS_ROOT":
            value = raw.strip().strip("\"'").strip()
        elif key.split() == ["export", "FLOCKS_ROOT"]:
            print(
                f"警告: {env_file} 里的「{line.strip()}」不会生效（systemd 不认 export，安装时还会补一行 FLOCKS_ROOT=），"
                "要改数据目录请写成 FLOCKS_ROOT=<绝对路径>",
                file=sys.stderr,
            )
    if not value:
        return ""
    if not value.startswith("/"):
        raise MigrationError(
            f"{env_file} 里的 FLOCKS_ROOT={value} 不是绝对路径，服务和安装脚本会用到不同的目录；"
            "请改成绝对路径后重跑。本次未做任何改动"
        )
    return value.rstrip("/") or "/"


def validate_target(target: Path) -> None:
    """Refuse a data dir the installer must not create dirs in, chown or rename."""
    real = target.resolve()
    if real == Path("/"):
        raise MigrationError("数据目录不能是 /，请检查 flocks.env 里的 FLOCKS_ROOT。本次未做任何改动")
    if not real.exists() or has_user_data(real):
        return
    if not real.is_dir():
        raise MigrationError(f"数据目录 {target} 不是目录（它取自 flocks.env 的 FLOCKS_ROOT，没配时是默认位置）。本次未做任何改动")
    foreign = sorted(entry.name for entry in real.iterdir() if entry.name not in FLOCKS_TOP_LEVEL)
    if foreign:
        shown = "、".join(foreign[:8]) + ("……" if len(foreign) > 8 else "")
        raise MigrationError(
            f"数据目录 {target} 已存在，但里面有不属于 Flocks 的内容（{shown}），安装脚本不会接管它。"
            "数据目录取自 flocks.env 的 FLOCKS_ROOT（没配时是默认位置）：请让它指向一个空目录或子目录"
            "（比如 <挂载点>/flocks），或者先把这些内容挪走。本次未做任何改动"
        )


def default_candidates(sudo_user: str) -> list[Path]:
    """~/.flocks of the invoking user, root and every local account (the pre-FLOCKS_ROOT default)."""
    override = os.environ.get("FLOCKS_OFFLINE_HOME_CANDIDATES")  # tests only: ':'-separated .flocks dirs
    if override is not None:
        return [Path(item) for item in override.split(":") if item]
    homes: list[str] = []
    if sudo_user and sudo_user != "root":
        try:
            homes.append(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    homes.append("/root")
    homes.extend(sorted(entry.pw_dir for entry in pwd.getpwall()))
    return [Path(home) / ".flocks" for home in homes if home.startswith("/")]


def _describe(root: Path) -> str:
    db = root / "data" / "flocks.db"
    marker = db if db.is_file() else next((root / m for m in USER_DATA_MARKERS if (root / m).is_file()), root)
    changed = dt.datetime.fromtimestamp(marker.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return f"{root}（最后写入 {changed}）"


def plan(target: Path, explicit: str, env_root: str, sudo_user: str) -> Path | None:
    validate_target(target)
    if has_user_data(target):
        if explicit and explicit.lower() != "none":
            raise MigrationError(
                f"数据目录 {target} 已经有数据，不会用 FLOCKS_OFFLINE_MIGRATE_FROM={explicit} 覆盖它。"
                "确实要换成旧数据，请先把现有数据目录改名挪开再安装。本次未做任何改动"
            )
        return None
    if explicit:
        if explicit.lower() == "none":
            return None
        source = Path(explicit)
        if not source.is_absolute():
            raise MigrationError(f"FLOCKS_OFFLINE_MIGRATE_FROM 要写绝对路径：{explicit}")
        if not has_user_data(source):
            raise MigrationError(f"FLOCKS_OFFLINE_MIGRATE_FROM={explicit} 下没有 Flocks 数据（config/flocks.json 或 data/flocks.db）")
        if _overlaps(source, target):
            raise MigrationError(f"FLOCKS_OFFLINE_MIGRATE_FROM={explicit} 和新数据目录 {target} 重叠")
        return source
    if env_root:
        source = Path(env_root)
        if source.is_absolute() and has_user_data(source) and not _overlaps(source, target):
            return source
    found: dict[Path, Path] = {}
    for candidate in default_candidates(sudo_user):
        try:
            if not candidate.is_dir() or not has_user_data(candidate) or _overlaps(candidate, target):
                continue
            found.setdefault(candidate.resolve(), candidate)
        except OSError:
            continue
    if len(found) > 1:
        listing = "\n".join(f"  {_describe(path)}" for path in found.values())
        raise MigrationError(
            "发现多个旧的 Flocks 数据目录，无法判断该带哪一个：\n"
            f"{listing}\n"
            "请用 sudo FLOCKS_OFFLINE_MIGRATE_FROM=<目录> bash flocks-offline.run 指定，"
            "或 FLOCKS_OFFLINE_MIGRATE_FROM=none 表示不迁移。本次未做任何改动"
        )
    return next(iter(found.values()), None)


def external_dir_links(source: Path) -> dict[str, Path]:
    """Top-level entries of *source* that are symlinks to directories outside it (moved to another disk)."""
    links: dict[str, Path] = {}
    for entry in sorted(source.iterdir()):
        if entry.is_symlink() and entry.is_dir() and not _inside(entry, source):
            links[entry.name] = Path(os.path.realpath(entry))
    return links


def _staging_inside_external(external: dict[str, Path], real_target: Path) -> list[str]:
    """External dirs that contain the staging area (next to the data dir): copying them would copy the copy."""
    return [f"{name} → {real}" for name, real in external.items() if _inside(real_target.parent, real)]


def open_file_holders(roots: list[Path], proc_root: Path = Path("/proc")) -> list[tuple[int, str]]:
    """Processes with a file open under any of *roots* (empty where there is no /proc)."""
    if not proc_root.is_dir():
        return []
    prefixes = tuple(_prefix(root) for root in roots)
    holders: list[tuple[int, str]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith(prefixes):
                try:
                    cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                except OSError:
                    cmdline = ""
                holders.append((int(entry.name), cmdline))
                break
    return holders


def tree_size(root: Path) -> int:
    total = 0
    for current, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                info = os.lstat(os.path.join(current, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def _existing_ancestor(path: Path) -> Path:
    while not path.exists():
        path = path.parent
    return path


def check(source: Path, target: Path, proc_root: Path = Path("/proc")) -> str:
    external = external_dir_links(source)
    real_target = target.resolve()
    looping = _staging_inside_external(external, real_target)
    if looping:
        raise MigrationError(
            f"旧数据目录里的软链 {'、'.join(looping)} 指向的目录包含新数据目录 {target}，照拷会把拷贝本身再拷进去。"
            "请把新数据目录（flocks.env 的 FLOCKS_ROOT）放到这些目录之外。本次未做任何改动"
        )
    holders = open_file_holders([source, *external.values()], proc_root)
    if holders:
        listing = "\n".join(f"  PID {pid}: {cmd or '?'}" for pid, cmd in holders)
        raise MigrationError(
            f"旧的 Flocks 还在使用 {source}：\n{listing}\n"
            "请先停掉它（以原来的用户执行 flocks stop）再安装，否则拷出来的数据库可能不完整。本次未做任何改动"
        )
    if os.path.ismount(real_target):
        raise MigrationError(
            f"数据目录 {target} 是挂载点，迁移时没法整目录替换它；"
            "请把 flocks.env 里的 FLOCKS_ROOT 改成挂载点下的子目录（比如 <挂载点>/flocks）再安装。本次未做任何改动"
        )
    need = tree_size(source) + sum(tree_size(path) for path in external.values())
    # the copy is staged next to the real data dir, so that is the filesystem that must hold it
    fs = os.statvfs(_existing_ancestor(real_target.parent))
    free = fs.f_bavail * fs.f_frsize
    if free < need + SPACE_MARGIN_BYTES:
        raise MigrationError(
            f"磁盘空间不足：拷贝 {source} 需要 {need // 2**20 + 1} MB，"
            f"{real_target.parent} 所在分区只剩 {free // 2**20} MB（另需留 {SPACE_MARGIN_BYTES // 2**20} MB）。本次未做任何改动"
        )
    return f"{need // 2**20 + 1} MB"


def _special_file_filter(base: Path, skipped: list[str], extra: frozenset[str] = frozenset()):
    def ignore(directory: str, names: list[str]) -> set[str]:
        dropped = set(extra) if os.path.realpath(directory) == os.path.realpath(base) else set()
        for name in names:
            try:
                mode = os.lstat(os.path.join(directory, name)).st_mode
            except OSError:
                continue
            if stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
                dropped.add(name)
                skipped.append(os.path.relpath(os.path.join(directory, name), base))
        return dropped

    return ignore


def _copy_tree(source: Path, staging: Path, external: dict[str, Path], skipped: list[str]) -> None:
    # symlinks inside the tree stay symlinks; the top-level ones leaving the tree become real copies
    shutil.copytree(source, staging, symlinks=True, ignore=_special_file_filter(source, skipped, frozenset(external)))
    for name, real in external.items():
        shutil.copytree(real, staging / name, symlinks=True, ignore=_special_file_filter(real, skipped))


def _fold_in_existing(existing: Path, staging: Path) -> list[str]:
    """Copy entries that only the existing target has into the staged copy (source wins on clashes)."""
    kept: list[str] = []
    for current, dirs, files in os.walk(existing, followlinks=False):
        rel = os.path.relpath(current, existing)
        dest_dir = staging / rel if rel != "." else staging
        for name in files + [d for d in dirs if os.path.islink(os.path.join(current, d))]:
            dest = dest_dir / name
            if os.path.lexists(dest):
                continue
            # never create or write through a symlink of the staged copy into another directory
            if not _inside(_existing_ancestor(dest_dir), staging):
                continue
            src = os.path.join(current, name)
            dest_dir.mkdir(parents=True, exist_ok=True)
            if os.path.islink(src):
                os.symlink(os.readlink(src), dest)
            else:
                shutil.copy2(src, dest)
            kept.append(os.path.relpath(src, existing))
    return kept


def _replace_prefix(value, mapping: tuple[tuple[str, str], ...]):
    if isinstance(value, str):
        for old, new in mapping:
            if value == old or value.startswith(old + "/"):
                return new + value[len(old):], 1
        return value, 0
    if isinstance(value, list):
        total = 0
        items = []
        for item in value:
            new_item, count = _replace_prefix(item, mapping)
            items.append(new_item)
            total += count
        return items, total
    if isinstance(value, dict):
        total = 0
        result = {}
        for key, item in value.items():
            new_item, count = _replace_prefix(item, mapping)
            result[key] = new_item
            total += count
        return result, total
    return value, 0


# a // or /* */ comment, or a JSON string literal followed by the ":" that makes it an object key
_JSONC_LEXEME = re.compile(r'//[^\n]*|/\*.*?\*/|"((?:[^"\\\n]|\\.)*)"(\s*:)?', re.S)


def rewrite_json_text(text: str, mapping: tuple[tuple[str, str], ...]) -> tuple[str, int]:
    """Rewrite string values (not keys, not comments) that start with an old path; JSONC-safe, format kept.
    Values are compared decoded, so escaped forms (\\uXXXX from ensure_ascii, \\/) match as well."""
    count = 0

    def replace(match: re.Match) -> str:
        nonlocal count
        inner = match.group(1)
        if inner is None or match.group(2):  # a comment, or an object key
            return match.group(0)
        try:
            value = json.loads('"' + inner + '"')
        except json.JSONDecodeError:
            return match.group(0)
        for old, new in mapping:
            if value == old or value.startswith(old + "/"):
                count += 1
                return json.dumps(new + value[len(old):], ensure_ascii=False)
        return match.group(0)

    return _JSONC_LEXEME.sub(replace, text), count


def _keep_owner(info: os.stat_result, path: Path) -> None:
    try:
        os.chown(path, info.st_uid, info.st_gid, follow_symlinks=False)
    except PermissionError:
        pass  # not root: files stay with the user running this, which already owns the originals


def rewrite_paths(root: Path, mapping: tuple[tuple[str, str], ...]) -> tuple[list[str], list[str]]:
    """Point absolute paths into the old data directory at the new one; returns (changed, unreadable)."""
    files = [root / name for name in PATH_REWRITE_FILES]
    for name in PATH_REWRITE_DIRS:
        base = root / name
        if base.is_dir() and _inside(base, root):
            files.extend(sorted(base.rglob("*.json")))
    changed: list[str] = []
    unreadable: list[str] = []
    for path in files:
        if path.is_symlink() or not path.is_file() or not _inside(path, root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unreadable.append(str(path.relative_to(root)))
            continue
        new_text, count = rewrite_json_text(text, mapping)
        if not count:
            continue
        info = path.stat()
        tmp = path.with_name(path.name + ".migrating")
        tmp.write_text(new_text, encoding="utf-8")
        shutil.copymode(path, tmp)
        _keep_owner(info, tmp)
        os.replace(tmp, path)
        changed.append(f"{path.relative_to(root)}（{count} 处）")
    return changed, unreadable


def _retarget_symlinks(root: Path, mapping: tuple[tuple[str, str], ...]) -> int:
    count = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in files + dirs:
            link = Path(current) / name
            if not link.is_symlink():
                continue
            new_target, hit = _replace_prefix(os.readlink(link), mapping)
            if hit:
                info = os.lstat(link)
                tmp = link.with_name(name + ".migrating")
                os.symlink(new_target, tmp)
                _keep_owner(info, tmp)
                os.replace(tmp, link)
                count += 1
    return count


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    os.chown(root, uid, gid, follow_symlinks=False)
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            os.chown(os.path.join(current, name), uid, gid, follow_symlinks=False)


def copy(source: Path, target: Path, stamp: str, owner: str = "") -> list[str]:
    if not has_user_data(source):
        raise MigrationError(f"{source} 下没有 Flocks 数据，不迁移")
    if has_user_data(target):
        raise MigrationError(f"{target} 已经有数据，不会用 {source} 覆盖它。本次未做任何改动")
    if _overlaps(source, target):
        raise MigrationError(f"旧数据目录 {source} 和新数据目录 {target} 重叠")
    # a data dir that is a symlink (e.g. onto a data disk) keeps being one: the copy goes where it points
    real_target = target.resolve()
    staging = real_target.with_name(f"{real_target.name}.migrating-{stamp}")
    parked = real_target.with_name(f"{real_target.name}.pre-migration-{stamp}")
    if os.path.lexists(staging) or os.path.lexists(parked):
        raise MigrationError(f"{staging} 或 {parked} 已存在，请换个时间重试")
    uid = gid = -1
    if owner:
        try:
            entry = pwd.getpwnam(owner)
        except KeyError:
            raise MigrationError(f"服务用户 {owner} 不存在，无法把迁移过来的数据交给它。本次未做任何改动") from None
        uid, gid = entry.pw_uid, entry.pw_gid
    external = external_dir_links(source)
    if _staging_inside_external(external, real_target):
        raise MigrationError(f"旧数据目录里有软链指向包含 {target} 的目录，照拷会把拷贝本身再拷进去。本次未做任何改动")
    real_target.parent.mkdir(parents=True, exist_ok=True)

    skipped: list[str] = []
    try:
        _copy_tree(source, staging, external, skipped)
    except (OSError, shutil.Error) as exc:
        raise MigrationError(
            f"拷贝 {source} 失败：{exc}\n旧数据目录没有改动；已拷贝的部分在 {staging}，确认后可自行删除再重试"
        ) from exc

    lines = [f"已拷贝 {source} → {target}（{tree_size(staging) // 2**20 + 1} MB，旧目录原样保留）"]
    if external:
        lines.append("这些目录原来是指向别处的软链，已拷成实体目录，新旧实例不共用：" + "、".join(
            f"{name} → {real}" for name, real in external.items()))
    if skipped:
        lines.append(f"跳过 {len(skipped)} 个 socket/管道等特殊文件（运行时文件，不需要迁移）")
    try:
        existing = os.path.lexists(real_target)
        kept = _fold_in_existing(real_target, staging) if existing else []
        # the rename below is the commit point: from then on the data dir must already be usable by the
        # service, so a failure after it (or a re-run) never leaves root-owned files behind
        if owner:
            _chown_tree(staging, uid, gid)
        if existing:
            os.rename(real_target, parked)
            lines.append(
                f"原来的 {real_target} 没有用户数据，已改名为 {parked}"
                + (f"，其中 {len(kept)} 个文件已并入新目录" if kept else "")
            )
        os.rename(staging, real_target)
    except (OSError, shutil.Error) as exc:
        raise MigrationError(
            f"把拷贝好的数据放到 {real_target} 时失败：{exc}\n旧数据目录没有改动；拷贝在 {staging}"
            + (f"，原目录在 {parked}" if os.path.lexists(parked) else "")
        ) from exc

    new_root = str(target)
    pairs = {str(source).rstrip("/"): new_root, os.path.realpath(source): new_root}
    for name, real in external.items():
        pairs[str(real)] = f"{new_root}/{name}"
    mapping = tuple(sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True))
    try:
        # links first: a directory link that still pointed into the old tree must not lead the rewrite there
        relinked = _retarget_symlinks(real_target, mapping)
        changed, unreadable = rewrite_paths(real_target, mapping)
        if changed:
            lines.append("已把指向旧目录的路径改成新目录：" + "、".join(changed))
        if unreadable:
            lines.append("这些文件不是 UTF-8 文本，没有改写其中的旧路径，请手动检查：" + "、".join(unreadable))
        if relinked:
            lines.append(f"已把 {relinked} 个指向旧目录的符号链接改到新目录")

        record = real_target / "run" / f"offline-migration-{stamp}.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        if _inside(record.parent, real_target):
            record.write_text(
                json.dumps(
                    {
                        "from": str(source),
                        "to": new_root,
                        "stamp": stamp,
                        "migrated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "materialized_links": {name: str(real) for name, real in external.items()},
                        "rewritten": changed,
                        "not_rewritten_unreadable": unreadable,
                        "skipped_special_files": skipped,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            if owner:
                os.chown(record, uid, gid, follow_symlinks=False)
    except OSError as exc:
        raise MigrationError(
            f"数据已拷到 {target}，但把里面指向 {source} 的路径改成新目录时出错：{exc}\n"
            f"旧数据目录没有改动；可以对照 {source} 手动改 config 下的 flocks.json(c)、mcp_list.json 和 data/hub/installed.json 后重跑"
        ) from exc
    return lines


def main(argv: list[str] | None = None) -> int:
    # every message is Chinese; an operator's non-UTF-8 locale must not turn printing it into a crash
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_env = sub.add_parser("env-root")
    p_env.add_argument("env_file", type=Path)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--target", type=Path, required=True)
    p_plan.add_argument("--explicit", default="")
    p_plan.add_argument("--env-root", default="")
    p_plan.add_argument("--sudo-user", default="")
    p_check = sub.add_parser("check")
    p_check.add_argument("--source", type=Path, required=True)
    p_check.add_argument("--target", type=Path, required=True)
    p_check.add_argument("--proc-root", type=Path, default=Path("/proc"))
    p_copy = sub.add_parser("copy")
    p_copy.add_argument("--source", type=Path, required=True)
    p_copy.add_argument("--target", type=Path, required=True)
    p_copy.add_argument("--stamp", required=True)
    p_copy.add_argument("--owner", default="")
    args = parser.parse_args(argv)

    try:
        if args.command == "env-root":
            value = configured_root(args.env_file)
            if value:
                print(value)
        elif args.command == "plan":
            source = plan(args.target, args.explicit.strip(), args.env_root.strip(), args.sudo_user.strip())
            if source is not None:
                print(source)
        elif args.command == "check":
            print(check(args.source, args.target, args.proc_root))
        else:
            for line in copy(args.source, args.target, args.stamp, args.owner.strip()):
                print(line)
    except MigrationError as exc:
        print(exc)
        return 1
    except OSError as exc:
        print(f"迁移旧数据时出错：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
