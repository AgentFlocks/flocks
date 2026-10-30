"""File operations that never follow a link on the way (flocks.tool.nofollow)."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from flocks.tool import nofollow
from flocks.tool.nofollow import Blocked, LinkOnTheWay, StaleFile

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="links need privileges on Windows")


@pytest.fixture(params=["openat", "by-path"])
def mode(request, monkeypatch):
    """Every test runs with the ``*at`` calls and with the by-path fallback (Windows)."""
    if request.param == "openat" and not nofollow.SUPPORTED:
        pytest.skip("no *at calls here")
    monkeypatch.setattr(nofollow, "SUPPORTED", request.param == "openat")
    return request.param


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """work/report.txt (mine) and other_user/report.txt (theirs)."""
    root = tmp_path.resolve()
    (root / "work").mkdir()
    (root / "work" / "report.txt").write_text("mine\n", encoding="utf-8")
    (root / "other_user").mkdir()
    (root / "other_user" / "report.txt").write_text("theirs\n", encoding="utf-8")
    return root


def _across_devices():
    """Directory.rename failing with EXDEV between two directories, as across file systems."""
    real_rename = nofollow.Directory.rename

    def rename(self, name, destination, new_name):
        if destination is self:
            return real_rename(self, name, destination, new_name)
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))

    return rename


def _swap_for_link(root: Path, name: str = "work", to: str = "other_user") -> None:
    """Another process parks ``name`` and puts a link to ``to`` in its place."""
    (root / name).rename(root / f"{name}_parked")
    (root / name).symlink_to(root / to, target_is_directory=True)


def test_reads_the_file_and_names_it(tree: Path, mode: str) -> None:
    found = nofollow.read_text(str(tree / "work" / "report.txt"))

    assert found is not None and found.text == "mine\n"
    status = os.stat(tree / "work" / "report.txt")
    assert found.identity == (status.st_dev, status.st_ino)
    assert nofollow.read_text(str(tree / "work" / "missing.txt")) is None
    assert nofollow.read_text(str(tree / "nowhere" / "missing.txt")) is None
    with pytest.raises(IsADirectoryError):
        nofollow.read_text(str(tree / "work"))


@pytest.mark.parametrize("where", ["on-the-way", "at-the-end"])
def test_a_link_where_the_path_had_none_is_not_followed(tree: Path, mode: str, where: str) -> None:
    if where == "on-the-way":
        _swap_for_link(tree)
    else:
        (tree / "work" / "report.txt").rename(tree / "parked.txt")
        (tree / "work" / "report.txt").symlink_to(tree / "other_user" / "report.txt")
    path = str(tree / "work" / "report.txt")

    for attempt in (
        lambda: nofollow.read_text(path),
        lambda: nofollow.write_text(path, "x\n", expect=None),
        lambda: nofollow.remove(path),
        lambda: nofollow.makedirs(str(tree / "work" / "report.txt" / "sub")),
        lambda: nofollow.copy(path, str(tree / "copy.txt")),
        lambda: nofollow.move(path, str(tree / "moved.txt")),
    ):
        with pytest.raises(LinkOnTheWay) as raised:
            attempt()
        # The message names only the path that was walked.
        assert "theirs" not in str(raised.value) and "other_user" not in str(raised.value)

    assert (tree / "other_user" / "report.txt").read_text(encoding="utf-8") == "theirs\n"
    assert sorted(entry.name for entry in (tree / "other_user").iterdir()) == ["report.txt"]
    assert not (tree / "copy.txt").exists() and not (tree / "moved.txt").exists()


def test_a_directory_swapped_after_it_was_opened_keeps_the_operation(tree: Path) -> None:
    """The walk holds the directory it opened; a link put where it was is not followed."""
    if not nofollow.SUPPORTED:
        pytest.skip("no *at calls here")
    holder, name = nofollow.parent(str(tree / "work" / "report.txt"))
    with holder:
        _swap_for_link(tree)
        fd = holder.open(name, os.O_WRONLY | os.O_TRUNC)
        with open(fd, "w", encoding="utf-8") as stream:
            stream.write("written\n")
        holder.mkdir("made")

    assert (tree / "work_parked" / "report.txt").read_text(encoding="utf-8") == "written\n"
    assert (tree / "work_parked" / "made").is_dir()
    assert (tree / "other_user" / "report.txt").read_text(encoding="utf-8") == "theirs\n"
    assert sorted(entry.name for entry in (tree / "other_user").iterdir()) == ["report.txt"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_a_directory_that_can_be_searched_but_not_read_is_walked_through(tree: Path, mode: str) -> None:
    """Path resolution needs only search permission on the directories on the way; so does the walk."""
    (tree / "work" / "sub").mkdir()
    (tree / "work" / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    os.chmod(tree / "work", 0o300)
    try:
        found = nofollow.read_text(str(tree / "work" / "sub" / "a.txt"))
        nofollow.write_text(str(tree / "work" / "sub" / "b.txt"), "b\n", expect=None)
        nofollow.remove(str(tree / "work" / "sub" / "a.txt"))
    finally:
        os.chmod(tree / "work", 0o755)

    assert found is not None and found.text == "a\n"
    assert (tree / "work" / "sub" / "b.txt").read_text(encoding="utf-8") == "b\n"
    assert not (tree / "work" / "sub" / "a.txt").exists()


def test_write_creates_only_what_was_absent_and_keeps_to_the_file_read(tree: Path, mode: str) -> None:
    new = tree / "work" / "deep" / "new.txt"
    nofollow.write_text(str(new), "one\n", expect=None)
    assert new.read_text(encoding="utf-8") == "one\n"

    with pytest.raises(StaleFile):
        nofollow.write_text(str(new), "two\n", expect=None)

    before = nofollow.read_text(str(tree / "work" / "report.txt"))
    assert before is not None
    # Same path, another file: a hard link to theirs.
    (tree / "work" / "report.txt").unlink()
    (tree / "work" / "report.txt").hardlink_to(tree / "other_user" / "report.txt")
    with pytest.raises(StaleFile):
        nofollow.write_text(str(tree / "work" / "report.txt"), "mine again\n", expect=before.identity)
    with pytest.raises(StaleFile):
        nofollow.replace_text(str(tree / "work" / "report.txt"), "mine again\n", before=before)
    assert (tree / "other_user" / "report.txt").read_text(encoding="utf-8") == "theirs\n"


def test_replace_refuses_text_changed_since_it_was_read(tree: Path, mode: str) -> None:
    path = str(tree / "work" / "report.txt")
    before = nofollow.read_text(path, newline="")
    assert before is not None
    (tree / "work" / "report.txt").write_text("mine\nedited meanwhile\n", encoding="utf-8")

    with pytest.raises(StaleFile):
        nofollow.replace_text(path, "replaced\n", before=before, read_newline="")

    again = nofollow.read_text(path, newline="")
    assert again is not None
    nofollow.replace_text(path, "replaced\r\n", before=again, read_newline="", write_newline="")
    assert (tree / "work" / "report.txt").read_bytes() == b"replaced\r\n"


def test_tree_lists_links_as_links(tree: Path, mode: str) -> None:
    (tree / "work" / "sub").mkdir()
    (tree / "work" / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    (tree / "work" / "theirs").symlink_to(tree / "other_user", target_is_directory=True)

    assert nofollow.tree(str(tree / "work")) == ["", "report.txt", "sub", "sub/a.txt", "theirs"]
    assert nofollow.tree(str(tree / "work" / "report.txt")) == [""]
    assert nofollow.tree(str(tree / "missing" / "x")) == [""]
    with pytest.raises(LinkOnTheWay):
        nofollow.tree(str(tree / "work" / "theirs"))


def test_a_directory_swapped_between_listing_and_opening_stops_the_listing(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tree / "work" / "sub").mkdir()
    (tree / "other_user" / "their-private-name.txt").write_text("x", encoding="utf-8")
    real_subdirectory = nofollow.Directory.subdirectory

    def swap_then_open(self, name, **kwargs):
        if name == "sub" and not (tree / "work" / "sub").is_symlink():
            (tree / "work" / "sub").rename(tree / "parked")
            (tree / "work" / "sub").symlink_to(tree / "other_user", target_is_directory=True)
        return real_subdirectory(self, name, **kwargs)

    monkeypatch.setattr(nofollow.Directory, "subdirectory", swap_then_open)

    with pytest.raises(LinkOnTheWay):
        nofollow.tree(str(tree / "work"))


def test_copy_of_a_file_lands_at_or_inside_the_target(tree: Path, mode: str) -> None:
    source = str(tree / "work" / "report.txt")
    os.utime(source, ns=(1_000_000_000, 2_000_000_000))
    os.chmod(source, 0o640)

    assert nofollow.copy(source, str(tree / "out" / "copy.txt")) == str(tree / "out" / "copy.txt")
    assert nofollow.copy(source, str(tree / "out")) == str(tree / "out" / "report.txt")

    for copied in (tree / "out" / "copy.txt", tree / "out" / "report.txt"):
        assert copied.read_text(encoding="utf-8") == "mine\n"
        assert copied.stat().st_mtime_ns == 2_000_000_000
        assert copied.stat().st_mode & 0o777 == 0o640


def test_copy_merges_a_tree_links_as_links_and_writes_through_none(tree: Path, mode: str) -> None:
    src = tree / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "x.txt").write_text("new\n", encoding="utf-8")
    (src / "file.txt").write_text("file\n", encoding="utf-8")
    (src / "link").symlink_to(tree / "other_user", target_is_directory=True)
    dest = tree / "dest"
    dest.mkdir()
    # Links in the target where the source has a directory and a file.
    (dest / "sub").symlink_to(tree / "other_user", target_is_directory=True)
    (dest / "file.txt").symlink_to(tree / "other_user" / "report.txt")
    (dest / "kept.txt").write_text("kept\n", encoding="utf-8")

    assert nofollow.copy(str(src), str(dest)) == str(dest)

    assert (dest / "sub").is_dir() and not (dest / "sub").is_symlink()
    assert (dest / "sub" / "x.txt").read_text(encoding="utf-8") == "new\n"
    assert not (dest / "file.txt").is_symlink()
    assert (dest / "file.txt").read_text(encoding="utf-8") == "file\n"
    assert os.readlink(dest / "link") == str(tree / "other_user")
    assert (dest / "kept.txt").read_text(encoding="utf-8") == "kept\n"
    assert (tree / "other_user" / "report.txt").read_text(encoding="utf-8") == "theirs\n"
    assert sorted(entry.name for entry in (tree / "other_user").iterdir()) == ["report.txt"]


@pytest.mark.parametrize(
    ("source_kind", "target_kind", "reason"),
    [
        ("link", "dir", "is a directory where the source has a link"),
        ("file", "dir", "is a directory where the source has a file"),
        ("dir", "file", "is a file where the source has a directory"),
    ],
)
def test_copy_refuses_what_it_does_not_replace_before_writing(
    tree: Path, mode: str, source_kind: str, target_kind: str, reason: str,
) -> None:
    src = tree / "src"
    src.mkdir()
    (src / "a.txt").write_text("new\n", encoding="utf-8")
    entry = src / "zz"
    if source_kind == "link":
        entry.symlink_to(tree / "other_user", target_is_directory=True)
    elif source_kind == "file":
        entry.write_text("file\n", encoding="utf-8")
    else:
        entry.mkdir()
    dest = tree / "dest"
    dest.mkdir()
    (dest / "a.txt").write_text("old\n", encoding="utf-8")
    if target_kind == "dir":
        (dest / "zz").mkdir()
    else:
        (dest / "zz").write_text("there\n", encoding="utf-8")

    with pytest.raises(Blocked) as raised:
        nofollow.copy(str(src), str(dest))

    assert raised.value.reason == reason and raised.value.path == str(dest / "zz")
    # Refused before anything was written: no half-merged copy.
    assert (dest / "a.txt").read_text(encoding="utf-8") == "old\n"


def test_copy_refuses_the_source_itself(tree: Path, mode: str) -> None:
    """Truncating the target would empty the source: the same path, its own directory, a hard link."""
    source = tree / "work" / "report.txt"
    (tree / "work" / "same.txt").hardlink_to(source)
    for target in (source, tree / "work", tree / "work" / "same.txt"):
        with pytest.raises(Blocked) as raised:
            nofollow.copy(str(source), str(target))
        assert raised.value.reason == "is the source itself"
    with pytest.raises(Blocked):
        nofollow.copy(str(tree / "work"), str(tree / "work"))

    assert source.read_text(encoding="utf-8") == "mine\n"


def test_a_copy_made_inside_the_source_is_not_copied_into_itself(tree: Path, mode: str) -> None:
    (tree / "work" / "sub").mkdir()
    (tree / "work" / "sub" / "a.txt").write_text("a\n", encoding="utf-8")

    nofollow.copy(str(tree / "work"), str(tree / "work" / "backup"))
    nofollow.copy(str(tree / "work"), str(tree / "work" / "sub" / "deeper"))

    assert (tree / "work" / "backup" / "sub" / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert not (tree / "work" / "backup" / "backup").exists()
    assert (tree / "work" / "sub" / "deeper" / "report.txt").read_text(encoding="utf-8") == "mine\n"
    assert not (tree / "work" / "sub" / "deeper" / "sub" / "deeper").exists()


def test_a_repeated_copy_inside_the_source_is_not_blocked_by_its_own_copy(tree: Path, mode: str) -> None:
    nofollow.copy(str(tree / "work"), str(tree / "work" / "backup"))
    (tree / "work" / "backup" / "backup").write_text("a file named like the copy\n", encoding="utf-8")

    nofollow.copy(str(tree / "work"), str(tree / "work" / "backup"))

    assert (tree / "work" / "backup" / "backup").read_text(encoding="utf-8") == "a file named like the copy\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_copy_into_a_directory_that_can_be_searched_but_not_read(tree: Path, mode: str) -> None:
    """Its times and mode are set like copystat does, also through a search-only descriptor."""
    (tree / "src").mkdir()
    (tree / "src" / "a.txt").write_text("a\n", encoding="utf-8")
    os.chmod(tree / "src", 0o750)
    (tree / "dest").mkdir()
    os.chmod(tree / "dest", 0o300)
    try:
        nofollow.copy(str(tree / "src"), str(tree / "dest"))
        copied_mode = os.stat(tree / "dest").st_mode & 0o777
    finally:
        os.chmod(tree / "dest", 0o755)

    assert copied_mode == 0o750
    assert (tree / "dest" / "a.txt").read_text(encoding="utf-8") == "a\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_a_moved_tree_that_cannot_be_listed_in_full_goes_back(tree: Path, mode: str) -> None:
    (tree / "work" / "sub").mkdir()
    (tree / "work" / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    approved = nofollow.tree(str(tree / "work"))
    (tree / "work" / "sub" / "protected.txt").write_text("late\n", encoding="utf-8")
    os.chmod(tree / "work" / "sub", 0o300)
    try:
        with pytest.raises(StaleFile):
            nofollow.move(str(tree / "work"), str(tree / "moved"), entries=approved)
    finally:
        for sub in (tree / "work" / "sub", tree / "moved" / "sub"):
            if sub.exists():
                os.chmod(sub, 0o755)

    assert not (tree / "moved").exists()
    assert (tree / "work" / "sub" / "protected.txt").exists()


def test_copy_with_the_approved_entries_leaves_a_late_entry_behind(tree: Path, mode: str) -> None:
    (tree / "work" / "sub").mkdir()
    (tree / "work" / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    approved = nofollow.tree(str(tree / "work"))
    (tree / "work" / "late.txt").write_text("late\n", encoding="utf-8")
    (tree / "work" / "sub" / "late.txt").write_text("late\n", encoding="utf-8")

    nofollow.copy(str(tree / "work"), str(tree / "dest"), entries=approved)

    assert nofollow.tree(str(tree / "dest")) == approved


def _arrives_after_the_check(monkeypatch: pytest.MonkeyPatch, late: Path) -> None:
    """``late`` is put in the tree right after the tree was checked against the approval."""
    real_check = nofollow._listed_within

    def check_then_add(root, approved):
        within = real_check(root, approved)
        late.write_text("late\n", encoding="utf-8")
        return within

    monkeypatch.setattr(nofollow, "_listed_within", check_then_add)


def _names(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def test_remove_with_the_approved_entries_removes_nothing_from_a_tree_that_changed(tree: Path, mode: str) -> None:
    (tree / "work" / "sub").mkdir()
    (tree / "work" / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    approved = nofollow.tree(str(tree / "work"))
    (tree / "work" / "sub" / "late.txt").write_text("late\n", encoding="utf-8")

    with pytest.raises(StaleFile):
        nofollow.remove(str(tree / "work"), entries=approved)

    assert _names(tree / "work") == ["report.txt", "sub", "sub/a.txt", "sub/late.txt"]


def test_remove_with_the_approved_entries_leaves_a_late_entry_alone(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tree / "work" / "sub").mkdir()
    (tree / "work" / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    approved = nofollow.tree(str(tree / "work"))
    _arrives_after_the_check(monkeypatch, tree / "work" / "sub" / "late.txt")

    with pytest.raises(StaleFile):
        nofollow.remove(str(tree / "work"), entries=approved)

    assert _names(tree / "work") == ["sub", "sub/late.txt"]


def test_move_with_the_approved_entries_moves_only_those(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file put in the tree after it was checked stays where it was; the listed entries move."""
    (tree / "work" / "sub").mkdir()
    (tree / "work" / "sub" / "a.txt").write_text("a\n", encoding="utf-8")
    os.chmod(tree / "work" / "sub", 0o750)
    approved = nofollow.tree(str(tree / "work"))
    _arrives_after_the_check(monkeypatch, tree / "work" / "sub" / "late.txt")

    with pytest.raises(StaleFile):
        nofollow.move(str(tree / "work"), str(tree / "moved"), entries=approved)

    assert _names(tree / "moved") == ["report.txt", "sub", "sub/a.txt"]
    assert os.stat(tree / "moved" / "sub").st_mode & 0o777 == 0o750
    assert _names(tree / "work") == ["sub", "sub/late.txt"]


def test_move_with_the_approved_entries_puts_back_a_tree_that_carried_more(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = nofollow.tree(str(tree / "work"))
    (tree / "work" / "late.txt").write_text("late\n", encoding="utf-8")

    with pytest.raises(StaleFile):
        nofollow.move(str(tree / "work"), str(tree / "moved"), entries=approved)

    assert not (tree / "moved").exists()
    assert sorted(entry.name for entry in (tree / "work").iterdir()) == ["late.txt", "report.txt"]
    assert nofollow.move(str(tree / "work"), str(tree / "moved"), entries=nofollow.tree(str(tree / "work")))
    assert (tree / "moved" / "late.txt").exists()


def test_a_move_across_file_systems_keeps_a_source_saved_while_it_was_copied(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An editor replaces the file while it is copied: the newer version must not be the one removed."""
    real_copy_data = nofollow._copy_data
    saved_while_copying = []

    def copy_then_save(source_fd, target_fd):
        real_copy_data(source_fd, target_fd)
        while saved_while_copying:
            saved = saved_while_copying.pop()
            fresh = saved.with_name(saved.name + ".tmp")
            fresh.write_text("saved\n", encoding="utf-8")
            os.replace(fresh, saved)

    monkeypatch.setattr(nofollow.Directory, "rename", _across_devices())
    monkeypatch.setattr(nofollow, "_copy_data", copy_then_save)
    (tree / "src").mkdir()
    (tree / "src" / "a.txt").write_text("a\n", encoding="utf-8")
    (tree / "src" / "b.txt").write_text("b\n", encoding="utf-8")

    saved_while_copying.append(tree / "work" / "report.txt")
    with pytest.raises(StaleFile):
        nofollow.move(str(tree / "work" / "report.txt"), str(tree / "moved.txt"))
    # a.txt is copied first and saved again right after.
    saved_while_copying.append(tree / "src" / "a.txt")
    with pytest.raises(StaleFile):
        nofollow.move(str(tree / "src"), str(tree / "moved"))

    assert (tree / "work" / "report.txt").read_text(encoding="utf-8") == "saved\n"
    assert not (tree / "moved.txt").exists()
    # The tree was copied; what was saved after its copy stays in the source.
    assert sorted(entry.name for entry in (tree / "src").iterdir()) == ["a.txt"]
    assert (tree / "src" / "a.txt").read_text(encoding="utf-8") == "saved\n"
    assert (tree / "moved" / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert (tree / "moved" / "b.txt").read_text(encoding="utf-8") == "b\n"


@pytest.mark.parametrize("overlap", ["hard-link", "ancestor"])
def test_copy_refuses_a_target_that_is_part_of_the_source(tree: Path, mode: str, overlap: str) -> None:
    """Writing there would change the source: a hard link to one of its files, or one of its directories."""
    if overlap == "hard-link":
        source, target = tree / "src", tree / "dst"
        source.mkdir()
        (source / "a").write_text("A", encoding="utf-8")
        (source / "b").write_text("B", encoding="utf-8")
        target.mkdir()
        (target / "a").hardlink_to(source / "b")
        reason, guarded = "is a file of the source", source / "b"
    else:
        source, target = tree / "src" / "sub", tree / "src"
        (source / "sub").mkdir(parents=True)
        (source / "file.txt").write_text("ROOT", encoding="utf-8")
        (source / "sub" / "file.txt").write_text("NESTED", encoding="utf-8")
        reason, guarded = "is a directory of the source", source / "file.txt"
    before = guarded.read_text(encoding="utf-8")

    with pytest.raises(Blocked) as raised:
        nofollow.copy(str(source), str(target))

    assert raised.value.reason == reason
    assert guarded.read_text(encoding="utf-8") == before


def test_a_rename_without_replacing_never_replaces(tree: Path, mode: str) -> None:
    (tree / "work" / "a.txt").write_text("a\n", encoding="utf-8")
    (tree / "work" / "d1").mkdir()
    (tree / "work" / "d2").mkdir()
    with nofollow.directory(str(tree / "work")) as found:
        with pytest.raises(FileExistsError):
            nofollow._rename_noreplace(found, "a.txt", found, "report.txt")
        with pytest.raises(FileExistsError):
            nofollow._rename_noreplace(found, "d1", found, "d2")
        nofollow._rename_noreplace(found, "a.txt", found, "b.txt")

    assert (tree / "work" / "report.txt").read_text(encoding="utf-8") == "mine\n"
    assert (tree / "work" / "b.txt").read_text(encoding="utf-8") == "a\n"
    assert (tree / "work" / "d1").is_dir() and (tree / "work" / "d2").is_dir()


def test_an_entry_taken_aside_and_rejected_goes_back_without_replacing(tree: Path, mode: str) -> None:
    """What is checked is what is removed; a newer version saved meanwhile is never lost."""
    with nofollow.directory(str(tree / "work")) as found:
        assert nofollow._remove_verified(found, "report.txt", lambda _status: False) is False
        assert (tree / "work" / "report.txt").read_text(encoding="utf-8") == "mine\n"

        def saved_meanwhile(_status):
            (tree / "work" / "report.txt").write_text("saved\n", encoding="utf-8")
            return False

        with pytest.raises(nofollow.Stranded) as raised:
            nofollow._remove_verified(found, "report.txt", saved_meanwhile)

    assert (tree / "work" / "report.txt").read_text(encoding="utf-8") == "saved\n"
    assert Path(raised.value.kept_at).read_text(encoding="utf-8") == "mine\n"


def test_a_move_across_file_systems_keeps_a_directory_replaced_meanwhile(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tree / "src" / "empty").mkdir(parents=True)
    (tree / "src" / "a.txt").write_text("a\n", encoding="utf-8")
    monkeypatch.setattr(nofollow.Directory, "rename", _across_devices())
    real_remove = nofollow._remove_entries

    def replace_then_remove(found, prefix, entries, copied=None):
        if prefix == "" and copied is not None and not (tree / "parked").exists():
            # Parked rather than removed, so the new directory cannot get its inode number.
            (tree / "src" / "empty").rename(tree / "parked")
            (tree / "src" / "empty").mkdir()
        return real_remove(found, prefix, entries, copied)

    monkeypatch.setattr(nofollow, "_remove_entries", replace_then_remove)

    with pytest.raises(StaleFile):
        nofollow.move(str(tree / "src"), str(tree / "moved"))

    assert _names(tree / "src") == ["empty"]
    assert _names(tree / "moved") == ["a.txt", "empty"]


def test_copy_refuses_a_source_file_moved_into_the_target_after_the_check(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moved rather than linked, the source file has a single name: it is still the source's."""
    (tree / "src").mkdir()
    (tree / "src" / "a").write_text("A", encoding="utf-8")
    (tree / "src" / "b").write_text("B", encoding="utf-8")
    (tree / "dst").mkdir()
    real_check = nofollow._check_merge

    def check_then_move(*args, **kwargs):
        real_check(*args, **kwargs)
        if (tree / "src" / "b").exists():
            os.rename(tree / "src" / "b", tree / "dst" / "a")

    monkeypatch.setattr(nofollow, "_check_merge", check_then_move)
    (tree / "dst" / "placeholder").write_text("x", encoding="utf-8")

    with pytest.raises(Blocked) as raised:
        nofollow.copy(str(tree / "src"), str(tree / "dst"))

    assert raised.value.reason == "is a file of the source"
    assert (tree / "dst" / "a").read_text(encoding="utf-8") == "B"


def test_without_an_atomic_rename_nothing_is_replaced(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``link`` then ``unlink`` by name: where the file system cannot refuse to replace, this refuses."""
    if not nofollow.SUPPORTED:
        pytest.skip("no *at calls here")
    monkeypatch.setattr(nofollow, "_RENAME_NOREPLACE", None)
    (tree / "work" / "a.txt").write_text("a\n", encoding="utf-8")
    with nofollow.directory(str(tree / "work")) as found:
        with pytest.raises(OSError) as raised:
            nofollow._rename_noreplace(found, "a.txt", found, "b.txt")
        assert raised.value.errno == errno.ENOTSUP
        nofollow._rename_noreplace(found, "a.txt", found, "b.txt", or_replace=True)

    assert (tree / "work" / "b.txt").read_text(encoding="utf-8") == "a\n"


def test_an_entry_that_cannot_go_back_is_reported_where_it_is_kept(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(nofollow, "_rename_noreplace", refuse)
    long_name = "n" * 240 + ".txt"
    (tree / "work" / long_name).write_text("long\n", encoding="utf-8")
    with nofollow.directory(str(tree / "work")) as found:
        with pytest.raises(nofollow.Stranded) as raised:
            nofollow._remove_verified(found, long_name, lambda _status: False)

    assert Path(raised.value.kept_at).read_text(encoding="utf-8") == "long\n"
    assert len(Path(raised.value.kept_at).name) < 40


def test_a_listed_file_turned_into_a_directory_before_its_move_goes_back(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tree / "work" / "f").write_text("f\n", encoding="utf-8")
    approved = nofollow.tree(str(tree / "work"))
    real_rename = nofollow._rename_noreplace

    def swap_then_rename(source, name, target, new_name, **kwargs):
        if name == "f" and (tree / "work" / "f").is_file():
            (tree / "work" / "f").unlink()
            (tree / "work" / "f").mkdir()
            (tree / "work" / "f" / "unapproved.txt").write_text("late\n", encoding="utf-8")
        return real_rename(source, name, target, new_name, **kwargs)

    monkeypatch.setattr(nofollow, "_rename_noreplace", swap_then_rename)

    with pytest.raises(StaleFile):
        nofollow.move(str(tree / "work"), str(tree / "moved"), entries=approved)

    assert (tree / "work" / "f" / "unapproved.txt").exists()
    assert not (tree / "moved" / "f").exists()
    assert (tree / "moved" / "report.txt").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux") or os.geteuid() == 0, reason="O_PATH on Linux, as a user")
def test_a_search_only_target_directory_gets_the_extended_attributes(tree: Path) -> None:
    (tree / "src").mkdir()
    (tree / "src" / "a.txt").write_text("a\n", encoding="utf-8")
    try:
        os.setxattr(tree / "src", "user.flocks", b"kept")
    except OSError:
        pytest.skip("no user extended attributes here")
    (tree / "dest").mkdir()
    os.chmod(tree / "dest", 0o300)
    try:
        nofollow.copy(str(tree / "src"), str(tree / "dest"))
    finally:
        os.chmod(tree / "dest", 0o755)

    assert os.getxattr(tree / "dest", "user.flocks") == b"kept"


def test_a_directory_swapped_while_copying_brings_nothing_of_theirs(
    tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tree / "src"
    (src / "a").mkdir(parents=True)
    (src / "a" / "one.txt").write_text("one\n", encoding="utf-8")
    (src / "b").mkdir()
    (src / "b" / "report.txt").write_text("mine\n", encoding="utf-8")
    real_merge = nofollow._merge

    def merge_then_swap(source, target, *args, **kwargs):
        # Once the first directory is copied, src/b becomes a link to their files.
        if source.path == str(src / "a") and not (src / "b").is_symlink():
            (src / "b").rename(tree / "parked")
            (src / "b").symlink_to(tree / "other_user", target_is_directory=True)
        return real_merge(source, target, *args, **kwargs)

    monkeypatch.setattr(nofollow, "_merge", merge_then_swap)

    with pytest.raises(LinkOnTheWay):
        nofollow.copy(str(src), str(tree / "dest"))

    assert (tree / "dest" / "a" / "one.txt").read_text(encoding="utf-8") == "one\n"
    assert not (tree / "dest" / "b" / "report.txt").exists()


def test_move_renames_lands_inside_and_refuses_what_shutil_refuses(tree: Path, mode: str) -> None:
    (tree / "box").mkdir()
    assert nofollow.move(str(tree / "work" / "report.txt"), str(tree / "box")) == str(tree / "box" / "report.txt")
    assert nofollow.move(str(tree / "box" / "report.txt"), str(tree / "renamed.txt")) == str(tree / "renamed.txt")
    assert (tree / "renamed.txt").read_text(encoding="utf-8") == "mine\n"

    (tree / "box" / "renamed.txt").write_text("already\n", encoding="utf-8")
    with pytest.raises(OSError, match="already exists"):
        nofollow.move(str(tree / "renamed.txt"), str(tree / "box"))
    (tree / "work" / "inner").mkdir()
    with pytest.raises(OSError, match="into itself"):
        nofollow.move(str(tree / "work"), str(tree / "work" / "inner"))


def test_move_across_file_systems_copies_links_as_links(tree: Path, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tree / "src"
    src.mkdir()
    (src / "a.txt").write_text("a\n", encoding="utf-8")
    (src / "link").symlink_to(tree / "other_user", target_is_directory=True)

    monkeypatch.setattr(nofollow.Directory, "rename", _across_devices())

    assert nofollow.move(str(src), str(tree / "moved")) == str(tree / "moved")
    assert nofollow.move(str(tree / "work" / "report.txt"), str(tree / "moved" / "r.txt")) == str(tree / "moved" / "r.txt")

    assert not src.exists() and not (tree / "work" / "report.txt").exists()
    assert (tree / "moved" / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert os.readlink(tree / "moved" / "link") == str(tree / "other_user")
    assert (tree / "moved" / "r.txt").read_text(encoding="utf-8") == "mine\n"
    assert (tree / "other_user" / "report.txt").read_text(encoding="utf-8") == "theirs\n"


def test_remove_takes_links_inside_a_tree_as_links(tree: Path, mode: str) -> None:
    (tree / "work" / "theirs").symlink_to(tree / "other_user", target_is_directory=True)

    status = os.lstat(tree / "work" / "report.txt")
    with pytest.raises(StaleFile):
        nofollow.remove(str(tree / "work" / "report.txt"), expect=(status.st_dev, status.st_ino + 1))
    nofollow.remove(str(tree / "work"))

    assert not (tree / "work").exists()
    assert (tree / "other_user" / "report.txt").read_text(encoding="utf-8") == "theirs\n"


def test_search_helpers_reach_only_real_files(tree: Path, mode: str) -> None:
    (tree / "work" / "theirs").symlink_to(tree / "other_user", target_is_directory=True)
    (tree / "work" / "file_link").symlink_to(tree / "other_user" / "report.txt")
    (tree / "work" / "node_modules").mkdir()
    (tree / "work" / "node_modules" / "skip.txt").write_text("x", encoding="utf-8")

    with nofollow.directory(str(tree / "work")) as root:
        walked = [
            relative
            for relative, _holder, _name in nofollow.walk_files(root, skip_directory=lambda name: name == "node_modules")
        ]
        assert walked == ["report.txt"]
        assert nofollow.file_status(root, "report.txt") is not None
        assert nofollow.file_status(root, "file_link") is None
        assert nofollow.file_status(root, "theirs/report.txt") is None
        with pytest.raises(LinkOnTheWay):
            nofollow.open_below(root, "theirs/report.txt")
        fd = nofollow.open_below(root, "report.txt")
        os.close(fd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux changes into a directory by descriptor")
def test_a_child_process_works_in_the_directory_opened_not_where_its_path_leads(tree: Path) -> None:
    with nofollow.directory(str(tree / "work")) as root:
        _swap_for_link(tree)
        listed = subprocess.run(["ls"], cwd=root.child_cwd(), capture_output=True, text=True, check=True)

    assert listed.stdout.split() == ["report.txt"]
    assert (tree / "work_parked" / "report.txt").exists()
