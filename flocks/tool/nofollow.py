"""
File operations that never follow a link on the way to their path.

Tool paths are checked once resolved -- workspace, sandbox, permission
rules -- and come back canonical: no link anywhere on the way, and none
where they end. Opening one again later by path follows whatever link
another process has put on the way since (during a permission wait, between
two checks, while a tree is being copied) and sends the operation to
someone else's files.

Here a path is walked from the root one directory at a time, each opened
relative to the one before without following a link, and the operation
runs inside the directory reached. A link on the way, or where the path
ends, stops it with :class:`LinkOnTheWay`; a directory swapped after it was
opened no longer matters, the operation stays in the one it opened. Inside
a tree, links are listed, copied, moved and removed as links, never
followed.

Without the ``*at`` calls (Windows) each step is checked for a link and the
operation runs by path, which narrows the gap instead of closing it.
"""

from __future__ import annotations

import ctypes
import errno
import io
import os
import platform
import secrets
import shutil
import stat
import sys
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

Identity = tuple[int, int]

SUPPORTED = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and {
        os.open, os.stat, os.mkdir, os.unlink, os.rmdir, os.rename, os.symlink, os.readlink, os.utime,
    } <= os.supports_dir_fd
    and os.scandir in os.supports_fd
    and os.stat in os.supports_follow_symlinks
    and shutil.rmtree.avoids_symlink_attacks
)

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW | _CLOEXEC
# A directory that may be searched but not read is walked through like path
# resolution does: O_PATH on Linux, O_SEARCH on macOS. Such a descriptor
# serves the *at calls, not a listing (which needs read permission anyway).
_SEARCH = getattr(os, "O_PATH", 0) or getattr(os, "O_SEARCH", 0)
_DIRECTORY_SEARCH = _SEARCH | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW | _CLOEXEC if _SEARCH else None
_COPY_CHUNK = 1024 * 1024


def changed_message(path: str) -> str:
    return f"{path} changed after it was checked; check it again and retry."


class LinkOnTheWay(OSError):
    """A link stands where the checked path has a real directory, or where it ends.

    The message names only the path that was being walked: nothing found
    through the link is quoted.
    """

    def __init__(self, path: str) -> None:
        super().__init__(errno.ELOOP, changed_message(path))
        self.path = path

    def __str__(self) -> str:
        return self.strerror


class StaleFile(OSError):
    """The file at the path is not the one read before: replaced, changed, gone or new."""

    def __init__(self, path: str) -> None:
        super().__init__(errno.ESTALE, changed_message(path))
        self.path = path

    def __str__(self) -> str:
        return self.strerror


class Blocked(OSError):
    """A copy or move would put an entry where the target holds one it does not replace."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(errno.EEXIST, f"{path} {reason}")
        self.path = path
        self.reason = reason

    def __str__(self) -> str:
        return self.strerror


def _named(exc: OSError, path: str) -> OSError:
    """``exc`` naming the full ``path`` (calls relative to a directory name only the entry)."""
    if isinstance(exc, (LinkOnTheWay, StaleFile, Blocked)) or exc.errno is None:
        return exc
    return OSError(exc.errno, exc.strerror, path)


def _missing(path: str) -> FileNotFoundError:
    return FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)


def identity(status: os.stat_result) -> Identity:
    return (status.st_dev, status.st_ino)


# Reparse tags of a symbolic link and of a junction (``stat`` has them on Windows only).
_LINK_TAGS = (
    getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
)


def _kind(status: os.stat_result) -> str:
    mode = status.st_mode
    if stat.S_ISLNK(mode) or getattr(status, "st_reparse_tag", 0) in _LINK_TAGS:
        # Windows tells a junction by its reparse tag.
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


class Directory:
    """A real directory reached without following a link.

    Holds its descriptor (without the ``*at`` calls, just its path) and does
    everything relative to it. Close it, or use it as a context manager.
    ``search_only``: opened to be searched, not read (see ``_DIRECTORY_SEARCH``).
    """

    __slots__ = ("fd", "path", "search_only")

    def __init__(self, fd: Optional[int], path: str, search_only: bool = False) -> None:
        self.fd = fd
        self.path = path
        self.search_only = search_only

    def __enter__(self) -> "Directory":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _join(self, name: str) -> str:
        return os.path.join(self.path, name)

    def status(self) -> os.stat_result:
        return os.stat(self.path) if self.fd is None else os.fstat(self.fd)

    def lstat(self, name: str) -> os.stat_result:
        try:
            if self.fd is None:
                return os.lstat(self._join(name))
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except OSError as exc:
            raise _named(exc, self._join(name)) from None

    def kind(self, name: str) -> Optional[str]:
        """"dir", "file", "link" or "other" for ``name``; None when nothing is there."""
        try:
            return _kind(self.lstat(name))
        except FileNotFoundError:
            return None

    def scan(self) -> list[tuple[str, str]]:
        """(name, kind) of every entry, sorted by name."""
        found = []
        with os.scandir(self.path if self.fd is None else self.fd) as entries:
            for entry in entries:
                if entry.is_symlink() or entry.is_junction():
                    kind = "link"
                elif entry.is_dir(follow_symlinks=False):
                    kind = "dir"
                elif entry.is_file(follow_symlinks=False):
                    kind = "file"
                else:
                    kind = "other"
                found.append((entry.name, kind))
        return sorted(found)

    def names(self) -> list[str]:
        return [name for name, _kind in self.scan()]

    def _refused(self, name: str, exc: OSError) -> OSError:
        """What to raise for ``exc``, met opening ``name`` here without following a link."""
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            # O_NOFOLLOW met a link (EMLINK on the BSDs).
            return LinkOnTheWay(self._join(name))
        if exc.errno in (errno.ENOTDIR, errno.EEXIST):
            # O_DIRECTORY met a link (Linux and macOS), or O_EXCL did -- or a file.
            try:
                if _kind(self.lstat(name)) == "link":
                    return LinkOnTheWay(self._join(name))
            except OSError:
                pass
        return _named(exc, self._join(name))

    def open(self, name: str, flags: int, mode: int = 0o666) -> int:
        """Open ``name`` here; a link there raises :class:`LinkOnTheWay`."""
        if self.fd is None:
            if self.kind(name) == "link":
                raise LinkOnTheWay(self._join(name))
            return os.open(self._join(name), flags | _BINARY, mode)
        try:
            return os.open(name, flags | _NOFOLLOW | _CLOEXEC, mode, dir_fd=self.fd)
        except OSError as exc:
            raise self._refused(name, exc) from None

    def _open_directory(self, name: str) -> "Directory":
        path = self._join(name)
        try:
            return Directory(os.open(name, _DIRECTORY, dir_fd=self.fd), path)
        except PermissionError:
            if _DIRECTORY_SEARCH is None:
                raise
            return Directory(os.open(name, _DIRECTORY_SEARCH, dir_fd=self.fd), path, search_only=True)

    def subdirectory(self, name: str, *, create: bool = False) -> "Directory":
        """The real directory ``name`` here, made first when missing and ``create``."""
        path = self._join(name)
        if self.fd is None:
            kind = self.kind(name)
            if kind is None and create:
                try:
                    os.mkdir(path)
                except FileExistsError:
                    pass
                kind = self.kind(name)
            if kind == "link":
                raise LinkOnTheWay(path)
            if kind is None:
                raise _missing(path)
            if kind != "dir":
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
            return Directory(None, path)
        try:
            return self._open_directory(name)
        except FileNotFoundError as exc:
            if not create:
                raise _named(exc, path) from None
        except OSError as exc:
            raise self._refused(name, exc) from None
        try:
            os.mkdir(name, 0o777, dir_fd=self.fd)
        except FileExistsError:
            pass  # made meanwhile: opened below like any other
        except OSError as exc:
            raise _named(exc, path) from None
        try:
            return self._open_directory(name)
        except OSError as exc:
            raise self._refused(name, exc) from None

    def descend(self, relative: str, *, create: bool = False) -> "Directory":
        """The real directory ``relative`` (names joined by the separator) below this one."""
        current: Optional[Directory] = None
        try:
            for name in _relative_names(relative):
                child = (current or self).subdirectory(name, create=create)
                if current is not None:
                    current.close()
                current = child
        except BaseException:
            if current is not None:
                current.close()
            raise
        if current is not None:
            return current
        return Directory(None if self.fd is None else os.dup(self.fd), self.path, self.search_only)

    def mkdir(self, name: str) -> None:
        try:
            if self.fd is None:
                os.mkdir(self._join(name))
            else:
                os.mkdir(name, 0o777, dir_fd=self.fd)
        except OSError as exc:
            raise _named(exc, self._join(name)) from None

    def unlink(self, name: str) -> None:
        """Remove the file or link ``name``; a link is removed, never followed."""
        try:
            if self.fd is None:
                os.unlink(self._join(name))
            else:
                os.unlink(name, dir_fd=self.fd)
        except OSError as exc:
            raise _named(exc, self._join(name)) from None

    def rmdir(self, name: str) -> None:
        try:
            if self.fd is None:
                os.rmdir(self._join(name))
            else:
                os.rmdir(name, dir_fd=self.fd)
        except OSError as exc:
            raise _named(exc, self._join(name)) from None

    def rmtree(self, name: str) -> None:
        """Remove the directory ``name`` and everything below it, links as links."""
        if self.fd is None:
            if self.kind(name) == "link":
                raise LinkOnTheWay(self._join(name))
            shutil.rmtree(self._join(name))
        else:
            shutil.rmtree(name, dir_fd=self.fd)

    def symlink(self, target: str, name: str) -> None:
        try:
            if self.fd is None:
                os.symlink(target, self._join(name))
            else:
                os.symlink(target, name, dir_fd=self.fd)
        except OSError as exc:
            raise _named(exc, self._join(name)) from None

    def readlink(self, name: str) -> str:
        try:
            if self.fd is None:
                return os.readlink(self._join(name))
            return os.readlink(name, dir_fd=self.fd)
        except OSError as exc:
            raise _named(exc, self._join(name)) from None

    def rename(self, name: str, destination: "Directory", new_name: str) -> None:
        """Rename ``name`` here to ``new_name`` in ``destination``; links move as links."""
        if self.fd is None or destination.fd is None:
            os.rename(self._join(name), destination._join(new_name))
        else:
            os.rename(name, new_name, src_dir_fd=self.fd, dst_dir_fd=destination.fd)

    def child_cwd(self) -> str:
        """A working directory for a child process that is this very directory.

        On Linux the child changes into it through its descriptor, so a link
        put where its path was makes no difference; elsewhere it is the path.
        """
        if self.fd is not None and sys.platform.startswith("linux") and os.path.isdir("/proc/self/fd"):
            return f"/proc/self/fd/{self.fd}"
        return self.path


def _relative_names(relative: str) -> list[str]:
    if os.altsep:
        relative = relative.replace(os.altsep, os.sep)
    names = [name for name in relative.split(os.sep) if name and name != os.curdir]
    if os.path.isabs(relative) or os.pardir in names:
        raise ValueError(f"Not a path below the directory: {relative}")
    return names


def _split_absolute(path: str) -> tuple[str, list[str]]:
    """The root of the absolute, canonical ``path`` and the names below it."""
    if not os.path.isabs(path):
        raise ValueError(f"Not an absolute path: {path}")
    drive, rest = os.path.splitdrive(path)
    if os.altsep:
        rest = rest.replace(os.altsep, os.sep)
    names = [name for name in rest.split(os.sep) if name]
    if os.curdir in names or os.pardir in names:
        raise ValueError(f"Not a canonical path: {path}")
    return drive + os.sep, names


def directory(path: str, *, create: bool = False) -> Directory:
    """The real directory at the absolute ``path``, reached without following a link.

    With ``create``, missing directories on the way are made.
    """
    anchor, names = _split_absolute(path)
    if SUPPORTED:
        root = Directory(os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | _CLOEXEC), anchor)
    else:
        root = Directory(None, anchor)
    with root:
        return root.descend(os.sep.join(names), create=create)


def parent(path: str, *, create: bool = False) -> tuple[Directory, str]:
    """The directory holding ``path``, reached without following a link, and the name in it."""
    anchor, names = _split_absolute(path)
    if not names:
        raise ValueError(f"{path} has no parent directory")
    return directory(os.path.join(anchor, *names[:-1]), create=create), names[-1]


def open_file(path: str, flags: int, mode: int = 0o666, *, make_parents: bool = False) -> int:
    """Descriptor of ``path`` opened with ``flags``; a link on the way raises :class:`LinkOnTheWay`."""
    holder, name = parent(path, create=make_parents)
    with holder:
        return holder.open(name, flags, mode)


@dataclass(frozen=True)
class FileText:
    """A file's text as read, and which file it was."""

    text: str
    identity: Identity


def _read_stream(fd: int, newline: Optional[str]) -> io.TextIOWrapper:
    return open(fd, "r", encoding="utf-8", errors="replace", newline=newline)


def _read_all(fd: int, newline: Optional[str]) -> str:
    """The text of the open file ``fd`` from its start; ``fd`` stays open."""
    os.lseek(fd, 0, os.SEEK_SET)
    with open(fd, "r", encoding="utf-8", errors="replace", newline=newline, closefd=False) as stream:
        return stream.read()


def read_text(path: str, *, newline: Optional[str] = None) -> Optional[FileText]:
    """The text of the file at ``path`` (utf-8, errors replaced); None when there is none.

    ``newline`` is passed on to :func:`open`.
    """
    try:
        fd = open_file(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        status = os.fstat(fd)
        if stat.S_ISDIR(status.st_mode):
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), path)
        stream = _read_stream(fd, newline)
    except BaseException:
        os.close(fd)
        raise
    with stream:
        return FileText(stream.read(), identity(status))


def write_text(path: str, text: str, *, expect: Optional[Identity], newline: Optional[str] = None) -> None:
    """Write ``text`` to the file at ``path``, which must still be the one read before.

    ``expect`` is that file's identity, or None when there was none: the file
    is then created and must not exist by now. Anything else raises
    :class:`StaleFile`. Missing directories on the way are made.
    """
    holder, name = parent(path, create=True)
    with holder:
        if expect is None:
            try:
                fd = holder.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                raise StaleFile(path) from None
        else:
            try:
                fd = holder.open(name, os.O_WRONLY)
            except FileNotFoundError:
                raise StaleFile(path) from None
    try:
        if expect is not None:
            if identity(os.fstat(fd)) != expect:
                raise StaleFile(path)
            os.ftruncate(fd, 0)
    except BaseException:
        os.close(fd)
        raise
    with open(fd, "w", encoding="utf-8", newline=newline) as stream:
        stream.write(text)


def replace_text(
    path: str,
    text: str,
    *,
    before: FileText,
    read_newline: Optional[str] = None,
    write_newline: Optional[str] = None,
) -> None:
    """Replace the file read as ``before`` with ``text``, unless it changed since.

    The file is opened once: the one checked is the one written. A different
    file there, or different text in it, raises :class:`StaleFile`.
    """
    holder, name = parent(path)
    with holder:
        try:
            fd = holder.open(name, os.O_RDWR)
        except FileNotFoundError:
            raise StaleFile(path) from None
    try:
        if identity(os.fstat(fd)) != before.identity:
            raise StaleFile(path)
        if _read_all(fd, read_newline) != before.text:
            raise StaleFile(path)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
    except BaseException:
        os.close(fd)
        raise
    with open(fd, "w", encoding="utf-8", newline=write_newline) as stream:
        stream.write(text)


def makedirs(path: str) -> None:
    """Make the directory ``path`` and any missing on the way."""
    directory(path, create=True).close()


def listdir(path: str) -> list[str]:
    with directory(path) as found:
        return found.names()


def remove(path: str, *, expect: Optional[Identity] = None, entries: Optional[list[str]] = None) -> None:
    """Remove the file or tree at ``path``; links inside a tree are removed as links.

    With ``expect``, what is there must still be that entry (:class:`StaleFile`).
    With ``entries`` (the tree's relative paths as approved), only those are
    removed: anything put in the tree since stays, with the directories
    holding it, and :class:`StaleFile` reports that the tree changed.
    """
    holder, name = parent(path)
    with holder:
        status = holder.lstat(name)
        if _kind(status) == "link":
            raise LinkOnTheWay(path)
        if expect is not None and identity(status) != expect:
            raise StaleFile(path)
        if not stat.S_ISDIR(status.st_mode):
            if expect is None:
                holder.unlink(name)
            elif not _remove_verified(holder, name, lambda found: identity(found) == expect):
                raise StaleFile(path)
        elif entries is None:
            holder.rmtree(name)
        else:
            approved = set(entries)
            with holder.subdirectory(name) as root:
                if not _listed_within(root, approved):
                    raise StaleFile(path)  # nothing removed
                emptied = _remove_entries(root, "", approved)
            if not (emptied and _removed_directory(holder, name)):
                raise StaleFile(path)


def _listed_within(root: Directory, approved: set) -> bool:
    """Whether the tree at ``root`` holds nothing but ``approved`` entries, listed in full."""
    found = [""]
    try:
        _list_strict(root, "", found)
    except OSError:
        return False
    return set(found) <= approved


def _signature(status: os.stat_result) -> tuple:
    """Which entry it is and whether it was written since: identity, size and modification time.

    The change time is left out: renaming the entry changes it.
    """
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


class Stranded(OSError):
    """An entry taken aside to be checked could not go back: something new took its name."""

    def __init__(self, path: str, kept_at: str) -> None:
        super().__init__(errno.EEXIST, f"{path} changed meanwhile; what was there is kept at {kept_at}")
        self.path = path
        self.kept_at = kept_at

    def __str__(self) -> str:
        return self.strerror


def _load_rename_noreplace() -> Optional[Callable[[int, bytes, int, bytes], int]]:
    """The C call that renames without replacing: renameat2 (Linux), renameatx_np (macOS)."""
    if not SUPPORTED:
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None
    for symbol, flag in (("renameat2", 1), ("renameatx_np", 0x4)):  # RENAME_NOREPLACE, RENAME_EXCL
        call = getattr(libc, symbol, None)
        if call is None:
            continue
        call.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        call.restype = ctypes.c_int
        return lambda old_fd, old, new_fd, new, call=call, flag=flag: call(old_fd, old, new_fd, new, flag)
    # A glibc older than 2.28 has no wrapper; the Linux system call is there since 3.15.
    number = {"x86_64": 316, "aarch64": 276, "arm64": 276}.get(platform.machine())
    syscall = getattr(libc, "syscall", None)
    if sys.platform.startswith("linux") and number is not None and syscall is not None:
        return lambda old_fd, old, new_fd, new: syscall(
            ctypes.c_long(number), ctypes.c_int(old_fd), ctypes.c_char_p(old),
            ctypes.c_int(new_fd), ctypes.c_char_p(new), ctypes.c_uint(1),
        )
    return None


_RENAME_NOREPLACE = _load_rename_noreplace()


def _rename_noreplace(
    source: Directory, name: str, target: Directory, new_name: str, *, or_replace: bool = False,
) -> None:
    """Rename ``name`` in ``source`` to ``new_name`` in ``target``; FileExistsError if anything is there.

    The check and the rename are one step (on Windows ``os.rename`` already
    refuses an existing target). Where the file system cannot rename without
    replacing, this refuses (ENOTSUP) -- unless ``or_replace``, for a target
    directory this operation made itself, where a plain rename is used: it
    still moves exactly what was there and removes nothing at the source.
    """
    path = target._join(new_name)
    if source.fd is None or target.fd is None:
        # Windows refuses an existing target itself; elsewhere this path runs only in tests.
        if os.name != "nt" and os.path.lexists(path):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
        os.rename(source._join(name), path)
        return
    code = errno.ENOSYS
    if _RENAME_NOREPLACE is not None:
        if _RENAME_NOREPLACE(source.fd, os.fsencode(name), target.fd, os.fsencode(new_name)) == 0:
            return
        code = ctypes.get_errno()
        if code not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise OSError(code, os.strerror(code), path)
    if not or_replace:
        raise OSError(errno.ENOTSUP, "the file system cannot rename without replacing", path)
    source.rename(name, target, new_name)


def _remove_verified(found: Directory, name: str, check: Callable[[os.stat_result], bool]) -> bool:
    """Remove ``name`` from ``found`` only if ``check`` accepts it; whether it was removed.

    The entry is first renamed to a private name in the same directory, so
    what is checked is what is removed: a newer version saved under the
    name meanwhile is either the one taken aside -- and goes back, without
    replacing anything put there since -- or arrives after and is left alone.
    A directory is removed only if it is empty. :class:`Stranded` when the
    entry taken aside cannot go back.
    """
    # A short name of its own, whatever the length of the entry's name.
    aside = f".flocks-aside-{secrets.token_hex(8)}"
    try:
        found.rename(name, found, aside)
    except FileNotFoundError:
        return False
    status = found.lstat(aside)
    if check(status):
        try:
            if stat.S_ISDIR(status.st_mode):
                found.rmdir(aside)
            else:
                found.unlink(aside)
            return True
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise
    try:
        _rename_noreplace(found, aside, found, name)
    except OSError:
        raise Stranded(found._join(name), found._join(aside)) from None
    return False


def _removed_directory(holder: Directory, name: str) -> bool:
    """Remove the directory ``name`` if it is empty; False when something is in it."""
    try:
        holder.rmdir(name)
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
            return False
        raise
    return True


def _remove_entries(found: Directory, prefix: str, entries: set, copied: Optional[dict] = None) -> bool:
    """Remove what ``found`` holds of ``entries``; whether nothing else was left in it.

    An entry not named stays, and so does the directory holding it. With
    ``copied`` (what a copy recorded, see :func:`_merge`), an entry is removed
    only if it is still the very one that was copied: a file replaced or
    written since, or a directory replaced, stays.
    """
    emptied = True
    for name, kind in found.scan():
        relative = os.path.join(prefix, name) if prefix else name
        if relative not in entries:
            emptied = False
            continue
        if kind == "dir":
            with found.subdirectory(name) as below:
                below_emptied = _remove_entries(below, relative, entries, copied)
            if not below_emptied:
                emptied = False
            elif copied is None:
                emptied = _removed_directory(found, name) and emptied
            else:
                expected = copied.get(relative)
                emptied = _remove_verified(
                    found, name, lambda status: stat.S_ISDIR(status.st_mode) and ("dir", identity(status)) == expected,
                ) and emptied
            continue
        if copied is None:
            found.unlink(name)
            continue
        expected = copied.get(relative)
        if not _remove_verified(
            found, name,
            lambda status: not stat.S_ISDIR(status.st_mode) and ("entry", _signature(status)) == expected,
        ):
            emptied = False
    return emptied


def kind(path: str) -> Optional[str]:
    """What stands at ``path`` -- "dir", "file", "link" or "other" -- reached without following a link.

    None when nothing does, the directories on the way included.
    """
    try:
        holder, name = parent(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    with holder:
        return holder.kind(name)


def status(path: str) -> Optional[os.stat_result]:
    """Status of what stands at ``path`` (not following it), reached without following a link; None when nothing does.

    A link standing there -- a canonical path never ends in one -- raises :class:`LinkOnTheWay`.
    """
    try:
        holder, name = parent(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    with holder:
        try:
            found = holder.lstat(name)
        except FileNotFoundError:
            return None
    if _kind(found) == "link":
        raise LinkOnTheWay(path)
    return found


def tree(path: str) -> list[str]:
    """Relative paths of ``path`` ("") and of every entry below it, links listed as links.

    Each directory is listed through its own descriptor, so a directory
    swapped for a link after it was opened is not read through; one swapped
    before raises :class:`LinkOnTheWay`, and so does a link where ``path`` ends.
    """
    try:
        holder, name = parent(path)
    except (FileNotFoundError, NotADirectoryError):
        return [""]
    with holder:
        kind = holder.kind(name)
        if kind == "link":
            raise LinkOnTheWay(path)
        entries = [""]
        if kind == "dir":
            with holder.subdirectory(name) as root:
                _list_below(root, "", entries)
        return entries


def _list_below(found: Directory, prefix: str, entries: list[str]) -> None:
    try:
        listed = found.scan()
    except OSError:
        return  # unreadable: nothing below it is listed
    for name, kind in listed:
        relative = os.path.join(prefix, name) if prefix else name
        entries.append(relative)
        if kind != "dir":
            continue
        try:
            below = found.subdirectory(name)
        except LinkOnTheWay:
            raise
        except OSError:
            continue  # gone or unreadable since it was listed
        with below:
            _list_below(below, relative, entries)


def _list_strict(found: Directory, prefix: str, entries: list) -> None:
    """Like :func:`_list_below`, but a directory that cannot be listed raises instead of being passed over."""
    for name, kind in found.scan():
        relative = os.path.join(prefix, name) if prefix else name
        entries.append(relative)
        if kind == "dir":
            with found.subdirectory(name) as below:
                _list_strict(below, relative, entries)


def walk_files(
    root: Directory,
    *,
    skip_directory: Callable[[str], bool] = lambda _name: False,
    prefix: str = "",
) -> Iterator[tuple[str, Directory, str]]:
    """(relative path, directory holding it, name) of each regular file below ``root``.

    Links are skipped, not followed; so are directories ``skip_directory``
    names, and those that cannot be read. The directory yielded stays open
    until the next item.
    """
    try:
        listed = root.scan()
    except OSError:
        return
    for name, kind in listed:
        relative = os.path.join(prefix, name) if prefix else name
        if kind == "file":
            yield relative, root, name
        elif kind == "dir" and not skip_directory(name):
            try:
                below = root.subdirectory(name)
            except OSError:
                continue
            with below:
                yield from walk_files(below, skip_directory=skip_directory, prefix=relative)


def file_status(root: Directory, relative: str) -> Optional[os.stat_result]:
    """Status of the regular file ``relative`` below ``root``, reached without a link; else None."""
    head, name = os.path.split(relative)
    try:
        with root.descend(head) as holder:
            status = holder.lstat(name)
    except (OSError, ValueError):
        return None
    return status if _kind(status) == "file" else None


def open_below(root: Directory, relative: str, flags: int = os.O_RDONLY) -> int:
    """Descriptor of ``relative`` below ``root``, opened without following a link."""
    head, name = os.path.split(relative)
    with root.descend(head) as holder:
        return holder.open(name, flags)


# -- copying and moving --------------------------------------------------------


def _copy_data(source_fd: int, target_fd: int) -> None:
    while True:
        chunk = os.read(source_fd, _COPY_CHUNK)
        if not chunk:
            return
        view = memoryview(chunk)
        while view:
            view = view[os.write(target_fd, view):]


def _copy_xattrs(source_fd: int, target_fd) -> None:
    """Extended attributes, as ``copystat`` copies them on Linux."""
    if not hasattr(os, "listxattr"):
        return
    try:
        names = os.listxattr(source_fd)
    except OSError as exc:
        if exc.errno not in (errno.ENOTSUP, errno.ENODATA, errno.EINVAL):
            raise
        return
    for name in names:
        try:
            os.setxattr(target_fd, name, os.getxattr(source_fd, name))
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.ENODATA, errno.EINVAL, errno.EACCES):
                raise


def _copy_status(
    source: os.stat_result,
    target_fd: Optional[int],
    target_path: str,
    source_fd: Optional[int] = None,
    *,
    search_only: bool = False,
) -> None:
    """Times and permission bits of ``source`` onto the target, like ``copystat``."""
    times = (source.st_atime_ns, source.st_mtime_ns)
    if target_fd is None or os.utime not in os.supports_fd:
        os.utime(target_path, ns=times)
        os.chmod(target_path, stat.S_IMODE(source.st_mode))
        return
    if search_only and _SEARCH == getattr(os, "O_PATH", None):
        # An O_PATH descriptor takes no fchmod, futimens or fsetxattr; its
        # /proc link leads to the very directory it holds, wherever its path
        # now leads.
        held = f"/proc/self/fd/{target_fd}"
        os.utime(held, ns=times)
        if source_fd is not None:
            _copy_xattrs(source_fd, held)
        os.chmod(held, stat.S_IMODE(source.st_mode))
        return
    os.utime(target_fd, ns=times)
    if source_fd is not None:
        _copy_xattrs(source_fd, target_fd)
    os.chmod(target_fd, stat.S_IMODE(source.st_mode))


def _copy_link_status(source: Directory, target: Directory, name: str) -> None:
    """Times (and, where links have them, permission bits) of the link ``name``."""
    if target.fd is None or source.fd is None:
        return
    status = source.lstat(name)
    try:
        os.utime(name, ns=(status.st_atime_ns, status.st_mtime_ns), dir_fd=target.fd, follow_symlinks=False)
    except (NotImplementedError, OSError):
        pass
    if os.chmod in os.supports_dir_fd and os.chmod in os.supports_follow_symlinks:
        try:
            os.chmod(name, stat.S_IMODE(status.st_mode), dir_fd=target.fd, follow_symlinks=False)
        except (NotImplementedError, OSError):
            pass


def _write_copy(
    source_fd: int,
    status: os.stat_result,
    target: Directory,
    name: str,
    source_files: Optional[set] = None,
) -> Identity:
    """Copy the open file ``source_fd`` to ``name`` in ``target``, replacing a file or link there.

    A file of the source there -- this very one, or another file of the tree
    being copied (``source_files``), reached through a hard link -- is refused
    before anything is written: truncating it would change the source. Returns
    the identity of the file written.
    """
    path = target._join(name)
    kind = target.kind(name)
    if kind == "dir":
        raise Blocked(path, "is a directory where the source has a file")
    if kind == "other":
        raise Blocked(path, "is not a regular file")
    if kind == "link":
        target.unlink(name)
    try:
        # Made here: a new file, whatever inode number it got.
        fd = target.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        existing = False
    except FileExistsError:
        fd = target.open(name, os.O_WRONLY)
        existing = True
    try:
        written = identity(os.fstat(fd))
        if written == identity(status):
            raise Blocked(path, "is the source itself")
        if existing and source_files is not None and written in source_files:
            raise Blocked(path, "is a file of the source")
        os.ftruncate(fd, 0)
        _copy_data(source_fd, fd)
        _copy_status(status, fd, path, source_fd)
        return written
    finally:
        os.close(fd)


@dataclass
class _CopyPlan:
    """What a tree copy goes by.

    ``entries``: the relative paths approved (None: all). ``skip``: the copy's
    own directory, met in the source when the copy is made inside it.
    ``source_files`` / ``source_dirs``: identities in the source tree, which
    the copy must never write into. ``copied``: relative path -> what was
    copied, ("entry", signature) for a file or link, ("dir", identity) for a
    directory -- to remove the source as it was copied.
    """

    entries: Optional[set] = None
    skip: Optional[Identity] = None
    source_files: set = None  # type: ignore[assignment]
    source_dirs: set = None  # type: ignore[assignment]
    copied: Optional[dict] = None


def _listed(relative: str, entries: Optional[set]) -> bool:
    return entries is None or relative in entries


def _source_identities(source: Directory, plan: _CopyPlan, prefix: str = "") -> None:
    """Record the identity of every file and directory of the source tree in ``plan``."""
    plan.source_dirs.add(identity(source.status()))
    for name, kind in source.scan():
        relative = os.path.join(prefix, name) if prefix else name
        if not _listed(relative, plan.entries):
            continue
        if kind == "file":
            plan.source_files.add(identity(source.lstat(name)))
        elif kind == "dir":
            with source.subdirectory(name) as below:
                if identity(below.status()) != plan.skip:
                    _source_identities(below, plan, relative)


def _check_merge(source: Directory, target: Directory, plan: _CopyPlan, prefix: str = "") -> None:
    """Raise :class:`Blocked` if merging ``source`` into ``target`` would meet an entry it cannot replace.

    Nothing is written: a refused copy leaves no half-merged tree behind.
    That includes a target entry that is part of the source -- a hard link
    to one of its files, or one of its directories when the copy goes to an
    ancestor -- which the copy would otherwise write into.
    """
    for name, kind in source.scan():
        relative = os.path.join(prefix, name) if prefix else name
        if not _listed(relative, plan.entries):
            continue
        if kind == "dir" and plan.skip is not None:
            with source.subdirectory(name) as below_source:
                if identity(below_source.status()) == plan.skip:
                    continue  # the copy being made, as _merge skips it
        there = target.kind(name)
        if kind not in ("dir", "file", "link"):
            raise Blocked(source._join(name), "is not a regular file, directory or link")
        if there is None or there == "link":
            continue  # nothing there, or a link, which the entry replaces
        if kind == "link" and there == "dir":
            raise Blocked(target._join(name), "is a directory where the source has a link")
        if kind == "file" and there == "dir":
            raise Blocked(target._join(name), "is a directory where the source has a file")
        if kind == "file" and there == "other":
            raise Blocked(target._join(name), "is not a regular file")
        if kind == "file" and identity(target.lstat(name)) in plan.source_files:
            raise Blocked(target._join(name), "is a file of the source")
        if kind == "dir":
            if there != "dir":
                raise Blocked(target._join(name), "is a file where the source has a directory")
            with source.subdirectory(name) as below_source, target.subdirectory(name) as below_target:
                if identity(below_target.status()) in plan.source_dirs:
                    raise Blocked(target._join(name), "is a directory of the source")
                _check_merge(below_source, below_target, plan, relative)


def _merge(source: Directory, target: Directory, plan: _CopyPlan, prefix: str = "") -> None:
    """Copy the listed entries of ``source`` into ``target``, links as links (see :class:`_CopyPlan`)."""
    for name, kind in source.scan():
        relative = os.path.join(prefix, name) if prefix else name
        if not _listed(relative, plan.entries):
            continue
        if kind == "link":
            status = source.lstat(name)
            text = source.readlink(name)
            there = target.kind(name)
            if there == "dir":
                raise Blocked(target._join(name), "is a directory where the source has a link")
            if there is not None:
                target.unlink(name)
            target.symlink(text, name)
            _copy_link_status(source, target, name)
            if plan.copied is not None:
                plan.copied[relative] = ("entry", _signature(status))
        elif kind == "dir":
            with source.subdirectory(name) as below_source:
                if identity(below_source.status()) == plan.skip:
                    continue
                there = target.kind(name)
                if there == "link":
                    target.unlink(name)
                elif there not in (None, "dir"):
                    raise Blocked(target._join(name), "is a file where the source has a directory")
                with target.subdirectory(name, create=True) as below_target:
                    # A directory made just now is not one of the source's,
                    # whatever inode number it got.
                    if there == "dir" and identity(below_target.status()) in plan.source_dirs:
                        raise Blocked(target._join(name), "is a directory of the source")
                    _merge(below_source, below_target, plan, relative)
                    _copy_status(
                        below_source.status(), below_target.fd, below_target.path, below_source.fd,
                        search_only=below_target.search_only,
                    )
                if plan.copied is not None:
                    plan.copied[relative] = ("dir", identity(below_source.status()))
        elif kind == "file":
            fd = source.open(name, os.O_RDONLY)
            try:
                status = os.fstat(fd)
                if not stat.S_ISREG(status.st_mode):
                    raise Blocked(source._join(name), "is not a regular file, directory or link")
                _write_copy(fd, status, target, name, plan.source_files)
            finally:
                os.close(fd)
            if plan.copied is not None:
                plan.copied[relative] = ("entry", _signature(status))
        else:
            raise Blocked(source._join(name), "is not a regular file, directory or link")


def _copy_tree_into(
    source: Directory,
    holder: Directory,
    name: str,
    *,
    merge: bool,
    entries: Optional[set] = None,
    copied: Optional[dict] = None,
) -> None:
    """Copy the tree ``source`` to ``name`` in ``holder``: merged into a directory there when ``merge``."""
    path = holder._join(name)
    kind = holder.kind(name)
    if kind == "link":
        raise LinkOnTheWay(path)
    if kind is not None and (not merge or kind != "dir"):
        if kind == "dir":
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
        raise Blocked(path, "is a file where the source has a directory")
    plan = _CopyPlan(entries=entries, source_files=set(), source_dirs=set(), copied=copied)
    if kind == "dir":
        with holder.subdirectory(name) as existing:
            plan.skip = identity(existing.status())
            if plan.skip == identity(source.status()):
                raise Blocked(path, "is the source itself")
            _source_identities(source, plan)
            if plan.skip in plan.source_dirs:
                raise Blocked(path, "is a directory of the source")
            _check_merge(source, existing, plan)
    with holder.subdirectory(name, create=True) as target:
        if plan.skip is None:
            plan.skip = identity(target.status())
            _source_identities(source, plan)
        if identity(target.status()) != plan.skip:
            raise StaleFile(path)  # replaced since it was checked
        _merge(source, target, plan)
        _copy_status(source.status(), target.fd, target.path, source.fd, search_only=target.search_only)


def copy(source: str, target: str, *, entries: Optional[list[str]] = None) -> str:
    """Copy ``source`` to ``target`` without following a link on either side; returns where it landed.

    A file goes to ``target``, or inside it when that is an existing
    directory (like ``copy2``); the source itself there is refused. A
    directory is merged into ``target`` (like ``copytree(symlinks=True,
    dirs_exist_ok=True)``): links are copied as links, and an entry of the
    source takes the place of a file or link the target holds at its place,
    which is never written through. A directory there is not replaced, nor
    is a file where the source has a directory, nor anything that is part
    of the source (a hard link to one of its files, one of its directories):
    :class:`Blocked`, raised before anything is written. With ``entries``
    (the tree's relative paths as approved) only those are copied. A copy
    made inside the source is not copied into itself.
    """
    source_holder, source_name = parent(source)
    with source_holder:
        kind = source_holder.kind(source_name)
        if kind is None:
            raise _missing(source)
        if kind == "link":
            raise LinkOnTheWay(source)
        if kind == "dir":
            with source_holder.subdirectory(source_name) as tree_root:
                target_holder, target_name = parent(target, create=True)
                with target_holder:
                    _copy_tree_into(
                        tree_root, target_holder, target_name, merge=True,
                        entries=None if entries is None else set(entries),
                    )
            return target
        if kind != "file":
            raise Blocked(source, "is not a regular file, directory or link")
        fd = source_holder.open(source_name, os.O_RDONLY)
    try:
        status = os.fstat(fd)
        target_holder, target_name = parent(target, create=True)
        with target_holder:
            there = target_holder.kind(target_name)
            if there == "link":
                raise LinkOnTheWay(target)
            if there != "dir":
                _write_copy(fd, status, target_holder, target_name)
                return target
            with target_holder.subdirectory(target_name) as inside:
                _write_copy(fd, status, inside, source_name)
            return os.path.join(target, source_name)
    finally:
        os.close(fd)


def _inside(path: str, directory_path: str) -> bool:
    """Whether ``path`` is ``directory_path`` or below it (as ``shutil.move`` checks)."""
    path = os.path.normcase(os.path.abspath(path))
    directory_path = os.path.normcase(os.path.abspath(directory_path))
    return path == directory_path or path.startswith(directory_path.rstrip(os.sep) + os.sep)


def move(source: str, target: str, *, entries: Optional[list[str]] = None) -> str:
    """Move ``source`` to ``target`` like ``shutil.move``, without following a link; returns where it landed.

    The source lands inside ``target`` when that is an existing directory.
    Links move as links. With ``entries`` (a tree's relative paths as
    approved) only those move: the directory is made anew where it lands and
    each listed entry is renamed into it, so anything put in the tree since
    the approval stays where it was, and :class:`StaleFile` reports it.
    Across file systems entries are copied (links as links) and the source
    is then removed as it was copied: whatever was replaced or written
    meanwhile stays.
    """
    source_holder, source_name = parent(source)
    with source_holder:
        source_status = source_holder.lstat(source_name)
        kind = _kind(source_status)
        if kind == "link":
            raise LinkOnTheWay(source)
        approved = None if entries is None or kind != "dir" else set(entries)
        target_holder, target_name = parent(target, create=True)
        with target_holder:
            there = target_holder.kind(target_name)
            if there == "link":
                raise LinkOnTheWay(target)
            if there == "dir" and identity(target_holder.lstat(target_name)) != identity(source_status):
                with target_holder.subdirectory(target_name) as inside:
                    landing = os.path.join(target, source_name)
                    if inside.kind(source_name) is not None:
                        raise shutil.Error(f"Destination path '{landing}' already exists")
                    _move_entry(source_holder, source_name, source_status, inside, source_name, source, landing, approved)
                return landing
            # The same entry (another letter case on a case-insensitive
            # system) is renamed in place, as shutil.move does.
            if there == "dir":
                approved = None
            _move_entry(source_holder, source_name, source_status, target_holder, target_name, source, target, approved)
            return target


def _move_entry(
    source_holder: Directory,
    source_name: str,
    source_status: os.stat_result,
    target_holder: Directory,
    target_name: str,
    source: str,
    landing: str,
    approved: Optional[set],
) -> None:
    kind = _kind(source_status)
    if kind == "dir" and _inside(landing, source) and os.path.normcase(landing) != os.path.normcase(source):
        raise shutil.Error(f"Cannot move a directory '{source}' into itself '{landing}'.")
    if kind == "dir" and approved is not None:
        if source_status.st_dev != target_holder.status().st_dev:
            _move_across(source_holder, source_name, kind, target_holder, target_name, source, approved)
        else:
            _move_listed(source_holder, source_name, target_holder, target_name, source, approved)
        return
    try:
        source_holder.rename(source_name, target_holder, target_name)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise _named(exc, source) from None
        _move_across(source_holder, source_name, kind, target_holder, target_name, source, approved)


def _move_listed(
    source_holder: Directory,
    source_name: str,
    target_holder: Directory,
    target_name: str,
    source: str,
    approved: set,
) -> None:
    """Move the listed entries of the directory ``source_name`` into a new directory ``target_name``.

    The landing directory is made here (it must not exist), each listed
    file and link is renamed into it without replacing anything, and each
    listed directory is made anew there with the same mode and times.
    Source directories left empty are removed; anything not listed stays in
    the source, with the directories holding it, and :class:`StaleFile`
    reports it.
    """
    with source_holder.subdirectory(source_name) as tree_root:
        if not _listed_within(tree_root, approved):
            raise StaleFile(source)  # nothing moved
        root_status = tree_root.status()
        target_holder.mkdir(target_name)
        with target_holder.subdirectory(target_name) as landed:
            emptied = _move_entries(tree_root, landed, "", approved)
            _copy_status(root_status, landed.fd, landed.path, tree_root.fd, search_only=landed.search_only)
    if not (emptied and _removed_directory(source_holder, source_name)):
        raise StaleFile(source)


def _move_entries(source: Directory, target: Directory, prefix: str, approved: set) -> bool:
    """Rename the listed entries of ``source`` into ``target``; whether ``source`` was left empty."""
    emptied = True
    for name, kind in source.scan():
        relative = os.path.join(prefix, name) if prefix else name
        if relative not in approved:
            emptied = False
            continue
        if kind == "dir":
            with source.subdirectory(name) as below_source:
                status = below_source.status()
                target.mkdir(name)
                with target.subdirectory(name) as below_target:
                    below_emptied = _move_entries(below_source, below_target, relative, approved)
                    _copy_status(status, below_target.fd, below_target.path, below_source.fd,
                                 search_only=below_target.search_only)
            if not (below_emptied and _removed_directory(source, name)):
                emptied = False
            continue
        try:
            _rename_noreplace(source, name, target, name, or_replace=True)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # A mount point inside the tree: this entry is copied, then
            # removed as it was copied.
            if not _move_entry_across(source, name, kind, target, name):
                emptied = False
            continue
        if target.kind(name) == "dir":
            # Listed as a file or link, it became a directory before the
            # rename took it: none of what it holds was approved, so it goes
            # back where it was.
            try:
                _rename_noreplace(target, name, source, name)
            except OSError:
                raise Stranded(source._join(name), target._join(name)) from None
            emptied = False
    return emptied


def _move_entry_across(source: Directory, name: str, kind: str, target: Directory, new_name: str) -> bool:
    """Copy a file or link to ``new_name`` in ``target`` and remove it from ``source`` as copied.

    False (and nothing removed) when the source was replaced or written
    meanwhile; a copy this move made of the older version is then removed.
    """
    if kind == "link":
        status = source.lstat(name)
        existed = target.kind(new_name) is not None
        if existed:
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), target._join(new_name))
        target.symlink(source.readlink(name), new_name)
        written = identity(target.lstat(new_name))
    else:
        fd = source.open(name, os.O_RDONLY)
        try:
            status = os.fstat(fd)
            existed = target.kind(new_name) is not None
            written = _write_copy(fd, status, target, new_name)
        finally:
            os.close(fd)
    if _remove_verified(source, name, lambda found: _signature(found) == _signature(status)):
        return True
    if not existed:
        _remove_verified(target, new_name, lambda found: identity(found) == written)
    return False


def _move_across(
    source_holder: Directory,
    source_name: str,
    kind: str,
    target_holder: Directory,
    target_name: str,
    source: str,
    approved: Optional[set],
) -> None:
    """Move to another file system: copy, then remove the source as it was copied."""
    if kind == "dir":
        copied: dict = {}
        with source_holder.subdirectory(source_name) as tree_root:
            root_identity = identity(tree_root.status())
            _copy_tree_into(tree_root, target_holder, target_name, merge=False, entries=approved, copied=copied)
            emptied = _remove_entries(tree_root, "", set(copied), copied)
        if not (emptied and _remove_verified(
            source_holder, source_name,
            lambda status: stat.S_ISDIR(status.st_mode) and identity(status) == root_identity,
        )):
            raise StaleFile(source)
        return
    if not _move_entry_across(source_holder, source_name, kind, target_holder, target_name):
        raise StaleFile(source)
