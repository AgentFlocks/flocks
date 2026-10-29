"""Git status must stay responsive and preserve literal file names."""

import asyncio
import subprocess
import threading

from flocks.utils.file import File


async def test_status_batches_numstat_and_preserves_special_names(monkeypatch):
    names = [f"file {i}.txt" for i in range(40)] + ["中文\t文件\n.txt"]
    status = b"".join(b" M " + name.encode() + b"\0" for name in names)
    status += b"R  renamed.txt\0old.txt\0?? new.txt\0 D deleted.txt\0"
    stats = b"".join(b"2\t1\t" + name.encode() + b"\0" for name in names)
    stats += b"0\t0\t\0old.txt\0renamed.txt\0-\t-\tdeleted.txt\0"
    calls = []

    def git(args, **kwargs):
        calls.append(args)
        # The old porcelain parser deliberately gets ordinary text so this
        # regression fails on batching, rather than on a mock type mismatch.
        if args[1] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, ".git\n", "")
        if args[1] == "status":
            output = "\n".join(f" M {name}" for name in names) if kwargs.get("text") else status
        else:
            output = "2\t1\tfile.txt\n" if kwargs.get("text") else stats
        return subprocess.CompletedProcess(args, 0, output, b"")

    monkeypatch.setattr(subprocess, "run", git)
    result = {entry.path: entry for entry in await File.status()}

    assert len(calls) == 2
    assert set(result) == set(names) | {"renamed.txt", "new.txt", "deleted.txt"}
    assert result[names[-1]].added == 2
    assert result[names[-1]].removed == 1
    assert result["renamed.txt"].added == 0
    assert result["new.txt"].status == "added"
    assert result["deleted.txt"].status == "deleted"


async def test_status_git_work_does_not_block_event_loop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    main_thread = threading.get_ident()
    git_threads = []

    def git(args, **kwargs):
        git_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(1), "the event loop was blocked by Git"
        output = b"" if not kwargs.get("text") else ""
        return subprocess.CompletedProcess(args, 0, output, output)

    monkeypatch.setattr(subprocess, "run", git)
    task = asyncio.create_task(File.status())
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.005)
        assert entered.is_set()
        assert not task.done()
        assert git_threads and main_thread not in git_threads
    finally:
        release.set()
        await task


async def test_status_non_git_directory_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert await File.status() == []


async def test_status_unborn_repository_keeps_staged_and_untracked_files(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True, timeout=5)
    (tmp_path / "staged.txt").write_text("first\nsecond\n")
    (tmp_path / "untracked.txt").write_text("new\n")
    subprocess.run(["git", "add", "--", "staged.txt"], cwd=tmp_path, check=True, timeout=5)
    monkeypatch.chdir(tmp_path)

    result = {entry.path: entry for entry in await File.status()}

    assert result["staged.txt"].status == "added"
    assert result["staged.txt"].added == 2
    assert result["untracked.txt"].status == "added"


async def test_status_keeps_files_when_diff_times_out(monkeypatch):
    def git(args, **kwargs):
        if args[1] == "status":
            return subprocess.CompletedProcess(args, 0, b" M file.txt\0", b"")
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", git)
    result = await File.status()
    assert [entry.path for entry in result] == ["file.txt"]
    assert result[0].added == result[0].removed == 0
