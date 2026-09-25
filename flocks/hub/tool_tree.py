"""Metadata-only tool discovery: one walk per tree, shared across project catalogs.

No payload contents are opened. Recursive directory symlinks are not followed;
canonical linked plugin roots are inspected explicitly by local discovery.
"""
from collections import OrderedDict
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
from pathlib import Path
import stat
import threading


_lock = threading.Lock()
_epoch = 0
_cache = OrderedDict()
_flights = {}
_checkpoint = ContextVar("tool_discovery_checkpoint", default=lambda: None)


@contextmanager
def checking(callback):
    token = _checkpoint.set(callback)
    try:
        yield
    finally:
        _checkpoint.reset(token)


class Superseded(Exception):
    """A refresh invalidated work while it was scanning."""


def _stamp(path):
    try:
        value = path.stat()
        return (value.st_dev, value.st_ino, value.st_mode, value.st_mtime_ns, value.st_ctime_ns)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _finish(future, value=None, error=None):
    try:
        if error is None:
            future.set_result(value)
        else:
            future.set_exception(error)
    except InvalidStateError:
        pass


def clear():
    global _epoch
    with _lock:
        _epoch += 1
        _cache.clear()
        old = list(_flights.values())
        _flights.clear()
    for future in old:
        _finish(future)


@dataclass
class Tree:
    root: Path
    children: dict
    files: list
    payload: dict
    stamps: dict
    links: set

    def valid(self, checkpoint):
        for path, before in self.stamps.items():
            checkpoint()
            if _stamp(path) != before:
                return False
        return True

    @property
    def signature(self):
        digest = hashlib.sha256(repr(tuple(self.stamps.items())).encode()).hexdigest()
        return (str(self.root / '.hub-tool-tree'), int(digest[:15], 16), len(self.stamps))


def _scan(root, checkpoint):
    tree = Tree(root, {}, [], {}, {}, set())
    # Iterative traversal avoids recursion depth limits. A post-order pass
    # computes subtree payload presence, replacing per-file parent.rglob().
    stack = [root]
    order = []
    while stack:
        checkpoint()
        directory = stack.pop()
        stamp = _stamp(directory)
        tree.stamps[directory] = stamp
        if stamp is None or not stat.S_ISDIR(stamp[2]):
            continue
        order.append(directory)
        children = []
        direct = False
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    checkpoint()
                    path = directory / entry.name
                    linked = entry.is_symlink()
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file()  # file symlinks retain existing semantics
                    children.append((path, is_dir or (linked and entry.is_dir()), is_file))
                    if linked:
                        tree.links.add(path)
                        tree.stamps[path] = _stamp(path)
                    if is_file and path.suffix in {'.yaml', '.yml', '.py'}:
                        tree.files.append(path)
                        if path.suffix == '.yaml' or (path.suffix == '.py' and path.name != '__init__.py'):
                            direct = True
        except (FileNotFoundError, NotADirectoryError):
            raise Superseded() from None  # changed mid-scan; never publish half a tree
        stack.extend(path for path, is_dir, _ in reversed(children) if is_dir and path not in tree.links)
        tree.children[directory] = children
        tree.payload[directory] = direct
    for directory in reversed(order):
        tree.payload[directory] |= any(tree.payload.get(path, False)
                                       for path, is_dir, _ in tree.children[directory] if is_dir)
    return tree


def get(root, checkpoint=None):
    """Reuse only after validating directory metadata; no TTL/stale fallback.

    A separate single-flight per root also shares global installs between
    catalogs for different daily sessions/project roots. File edits do not
    affect presence, while adds/deletes/renames and permission changes do.
    """
    checkpoint = checkpoint or _checkpoint.get()
    root = Path(root)
    retries = 0
    while True:
        checkpoint()
        with _lock:
            epoch = _epoch
            key = (epoch, root)
            future = _flights.get(key)
            owner = future is None
            if owner:
                future = Future()
                _flights[key] = future
            previous = _cache.get(root)
        if not owner:
            value = future.result()
            checkpoint()
            if value is not None and epoch == _epoch:
                return value
            continue
        def current():
            checkpoint()
            if epoch != _epoch:
                raise Superseded()
        try:
            reusable = previous is not None and previous.valid(current)
            value = previous if reusable else _scan(root, current)
            if not reusable and not value.valid(current):
                raise Superseded()
            current()
            with _lock:
                if epoch != _epoch:
                    raise Superseded()
                _cache[root] = value
                _cache.move_to_end(root)
                while len(_cache) > 16:
                    _cache.popitem(last=False)
                _flights.pop(key, None)
            _finish(future, value)
            return value
        except Superseded:
            with _lock:
                _flights.pop(key, None)
            _finish(future)
            checkpoint()
            retries += 1
            if retries >= 3:
                raise RuntimeError('Tool installation tree changed repeatedly during discovery') from None
        except BaseException as exc:
            with _lock:
                _flights.pop(key, None)
            _finish(future, error=exc)
            raise
