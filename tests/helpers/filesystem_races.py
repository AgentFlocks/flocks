"""Another process changing the file system at the worst moment, for file tool tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import pytest


def swap_for_link(root: Path, name: str, to: Path) -> None:
    """Park ``root/name`` and put a link to ``to`` in its place (once)."""
    if not (root / name).is_symlink():
        (root / name).rename(root / f"{name}_parked")
        (root / name).symlink_to(to, target_is_directory=True)


def swap_after_listing(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
    entry: str,
    swap: Callable[[], object],
) -> None:
    """Run ``swap`` once ``directory`` was listed with ``entry`` in it, before anything goes into ``entry``.

    The listing may be by path (``os.listdir``) or through a descriptor
    (``os.scandir``); ``swap`` runs after each listing of ``directory`` until
    ``entry`` is a link.
    """
    identity = (directory.stat().st_dev, directory.stat().st_ino)
    list_directory, scan_directory = os.listdir, os.scandir

    def is_the_directory(path) -> bool:
        if isinstance(path, int):
            status = os.fstat(path)
            return (status.st_dev, status.st_ino) == identity
        return os.fspath(path) == str(directory)

    def swap_once() -> None:
        if not (directory / entry).is_symlink():
            swap()

    def list_then_swap(path="."):
        names = list_directory(path)
        if is_the_directory(path):
            swap_once()
        return names

    class Listed(list):
        def __enter__(self):
            return iter(self)

        def __exit__(self, *_exc) -> None:
            return None

    def scan_then_swap(path="."):
        with scan_directory(path) as entries:
            listed = Listed(entries)
        for found in listed:
            # What was seen while listing, kept as listed.
            found.is_symlink(), found.is_dir(follow_symlinks=False)
        if is_the_directory(path):
            swap_once()
        return listed

    monkeypatch.setattr(os, "listdir", list_then_swap)
    monkeypatch.setattr(os, "scandir", scan_then_swap)
