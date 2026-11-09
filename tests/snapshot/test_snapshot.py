"""
Tests for snapshot module

Tests Git-based snapshot functionality including tracking,
patching, restoring, and reverting file changes.
"""

import os
import asyncio
import tempfile
import shutil
import pytest
from pathlib import Path

from flocks.snapshot import Snapshot, SnapshotPatch, FileDiff


class TestSnapshotBasics:
    """Basic snapshot functionality tests"""
    
    def setup_method(self):
        """Set up test environment"""
        # Create temporary directory for tests
        self.test_dir = tempfile.mkdtemp(prefix="flocks_snapshot_test_")
        self.project_id = "test_project"
        
        # Initialize a git repo in test directory (required for snapshots)
        os.system(f"cd {self.test_dir} && git init -q")
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    @pytest.mark.asyncio
    async def test_track_creates_snapshot(self):
        """Test that track creates a snapshot hash"""
        # Create a test file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Hello, World!")
        
        # Track snapshot
        hash = await Snapshot.track(self.project_id, self.test_dir)
        
        assert hash is not None
        assert len(hash) == 40  # Git SHA-1 hash length
    
    @pytest.mark.asyncio
    async def test_track_returns_same_hash_for_unchanged_files(self):
        """Test that tracking unchanged files returns the same hash"""
        # Create a test file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Hello, World!")
        
        # Track twice
        hash1 = await Snapshot.track(self.project_id, self.test_dir)
        hash2 = await Snapshot.track(self.project_id, self.test_dir)
        
        assert hash1 == hash2
    
    @pytest.mark.asyncio
    async def test_track_returns_different_hash_for_changed_files(self):
        """Test that tracking changed files returns different hash"""
        # Create a test file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Hello, World!")
        
        # Track first snapshot
        hash1 = await Snapshot.track(self.project_id, self.test_dir)
        
        # Modify file
        with open(test_file, "w") as f:
            f.write("Hello, Modified World!")
        
        # Track second snapshot
        hash2 = await Snapshot.track(self.project_id, self.test_dir)
        
        assert hash1 != hash2


class TestSnapshotPatch:
    """Tests for patch functionality"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="flocks_snapshot_test_")
        self.project_id = "test_project"
        os.system(f"cd {self.test_dir} && git init -q")
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    @pytest.mark.asyncio
    async def test_patch_detects_changed_files(self):
        """Test that patch detects changed files"""
        # Create initial file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Initial content")
        
        # Track initial state
        initial_hash = await Snapshot.track(self.project_id, self.test_dir)
        
        # Modify file
        with open(test_file, "w") as f:
            f.write("Modified content")
        
        # Get patch
        patch = await Snapshot.patch(self.project_id, self.test_dir, initial_hash)
        
        assert patch.hash == initial_hash
        assert len(patch.files) == 1
        assert "test.txt" in patch.files[0]
    
    @pytest.mark.asyncio
    async def test_patch_detects_new_files(self):
        """Test that patch detects new files"""
        # Create initial file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Initial content")
        
        # Track initial state
        initial_hash = await Snapshot.track(self.project_id, self.test_dir)
        
        # Add new file
        new_file = os.path.join(self.test_dir, "new.txt")
        with open(new_file, "w") as f:
            f.write("New file content")
        
        # Get patch
        patch = await Snapshot.patch(self.project_id, self.test_dir, initial_hash)
        
        assert len(patch.files) == 1
        assert "new.txt" in patch.files[0]
    
    @pytest.mark.asyncio
    async def test_patch_empty_when_no_changes(self):
        """Test that patch is empty when no changes"""
        # Create file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Content")
        
        # Track state
        hash = await Snapshot.track(self.project_id, self.test_dir)
        
        # Get patch without changes
        patch = await Snapshot.patch(self.project_id, self.test_dir, hash)
        
        assert len(patch.files) == 0


class TestSnapshotDiff:
    """Tests for diff functionality"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="flocks_snapshot_test_")
        self.project_id = "test_project"
        os.system(f"cd {self.test_dir} && git init -q")
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    @pytest.mark.asyncio
    async def test_diff_shows_changes(self):
        """Test that diff shows file changes"""
        # Create initial file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Line 1\nLine 2\n")
        
        # Track initial state
        initial_hash = await Snapshot.track(self.project_id, self.test_dir)
        
        # Modify file
        with open(test_file, "w") as f:
            f.write("Line 1\nLine 2 Modified\n")
        
        # Get diff
        diff = await Snapshot.diff(self.project_id, self.test_dir, initial_hash)
        
        assert diff is not None
        assert "test.txt" in diff
        assert "-Line 2" in diff
        assert "+Line 2 Modified" in diff
    
    @pytest.mark.asyncio
    async def test_diff_empty_when_no_changes(self):
        """Test that diff is empty when no changes"""
        # Create file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Content")
        
        # Track state
        hash = await Snapshot.track(self.project_id, self.test_dir)
        
        # Get diff without changes
        diff = await Snapshot.diff(self.project_id, self.test_dir, hash)
        
        assert diff == ""


class TestSnapshotRestore:
    """Tests for restore functionality"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="flocks_snapshot_test_")
        self.project_id = "test_project"
        os.system(f"cd {self.test_dir} && git init -q")
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    @pytest.mark.asyncio
    async def test_restore_reverts_file_content(self):
        """Test that restore reverts file to snapshot state"""
        # Create initial file
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("Initial content")
        
        # Track initial state
        initial_hash = await Snapshot.track(self.project_id, self.test_dir)
        
        # Modify file
        with open(test_file, "w") as f:
            f.write("Modified content")
        
        # Verify modification
        with open(test_file, "r") as f:
            assert f.read() == "Modified content"
        
        # Restore to initial state
        success = await Snapshot.restore(self.project_id, self.test_dir, initial_hash)
        
        assert success
        
        # Verify restoration
        with open(test_file, "r") as f:
            assert f.read() == "Initial content"


class TestSnapshotRevert:
    """Tests for revert functionality"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="flocks_snapshot_test_")
        self.project_id = "test_project"
        os.system(f"cd {self.test_dir} && git init -q")
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    @pytest.mark.asyncio
    async def test_revert_specific_files(self):
        """Test that revert only affects specific files"""
        # Create two files
        file1 = os.path.join(self.test_dir, "file1.txt")
        file2 = os.path.join(self.test_dir, "file2.txt")
        
        with open(file1, "w") as f:
            f.write("File 1 initial")
        with open(file2, "w") as f:
            f.write("File 2 initial")
        
        # Track initial state
        initial_hash = await Snapshot.track(self.project_id, self.test_dir)
        
        # Modify both files
        with open(file1, "w") as f:
            f.write("File 1 modified")
        with open(file2, "w") as f:
            f.write("File 2 modified")
        
        # Revert only file1
        patch = SnapshotPatch(hash=initial_hash, files=[file1])
        await Snapshot.revert(self.project_id, self.test_dir, [patch])
        
        # Verify file1 reverted, file2 unchanged
        with open(file1, "r") as f:
            assert f.read() == "File 1 initial"
        with open(file2, "r") as f:
            assert f.read() == "File 2 modified"


class TestSnapshotModel:
    """Tests for Pydantic models"""
    
    def test_snapshot_patch_model(self):
        """Test SnapshotPatch model"""
        patch = SnapshotPatch(
            hash="abc123" * 6 + "ab",  # 40 char hash
            files=["/path/to/file1.txt", "/path/to/file2.txt"]
        )
        
        assert patch.hash == "abc123" * 6 + "ab"
        assert len(patch.files) == 2
    
    def test_file_diff_model(self):
        """Test FileDiff model"""
        diff = FileDiff(
            file="test.txt",
            before="old content",
            after="new content",
            additions=5,
            deletions=3
        )
        
        assert diff.file == "test.txt"
        assert diff.before == "old content"
        assert diff.after == "new content"
        assert diff.additions == 5
        assert diff.deletions == 3
    
    def test_file_diff_defaults(self):
        """Test FileDiff default values"""
        diff = FileDiff(file="test.txt")
        
        assert diff.before == ""
        assert diff.after == ""
        assert diff.additions == 0
        assert diff.deletions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [None, False])
async def test_disabled_capture_never_runs_git(tmp_path, monkeypatch, enabled):
    from unittest.mock import AsyncMock
    from flocks.config.config import Config, ConfigInfo

    monkeypatch.setattr(Config, "get", AsyncMock(return_value=ConfigInfo(snapshot=enabled)))
    run = AsyncMock()
    monkeypatch.setattr(Snapshot, "_run_git", run)
    assert await Snapshot.track("disabled", str(tmp_path)) is None
    assert not (await Snapshot.patch("disabled", str(tmp_path), "old")).files
    assert await Snapshot.diff("disabled", str(tmp_path), "old") == ""
    run.assert_not_called()
    assert not (Config.get_data_path() / "snapshot").exists()


@pytest.mark.asyncio
async def test_global_capacity_guard_refuses_all_staging(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from flocks.config.config import Config
    from flocks.snapshot import snapshot as module

    other = Config.get_data_path() / "snapshot" / "other-project"
    other.mkdir(parents=True)
    sentinel = other / "object"
    sentinel.write_bytes(b"1234")
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 4)
    run = AsyncMock()
    monkeypatch.setattr(Snapshot, "_run_git", run)
    assert await Snapshot.track("new", str(tmp_path)) is None
    assert not (await Snapshot.patch("new", str(tmp_path), "old")).files
    assert await Snapshot.diff("new", str(tmp_path), "old") == ""
    run.assert_not_called()
    assert sentinel.read_bytes() == b"1234"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,renames,allowed", [(5, 1, True), (4, 1, False), (5, 3, False)])
async def test_capacity_scan_retries_concurrent_object_rename(tmp_path, monkeypatch, limit, renames, allowed):
    from flocks.config.config import Config
    from flocks.snapshot import snapshot as module

    directory = Config.get_data_path() / "snapshot" / "other" / "objects"
    directory.mkdir(parents=True)
    current = directory / "tmp_obj"
    current.write_bytes(b"1234")
    real_walk = os.walk
    renamed = 0

    def racing_walk(*args, **kwargs):
        nonlocal current, renamed
        for path, dirs, files in real_walk(*args, **kwargs):
            if Path(path) == directory and current.name in files and renamed < renames:
                destination = directory / f"object_{renamed}"
                current.rename(destination)
                current = destination
                renamed += 1
            yield path, dirs, files

    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", limit)
    monkeypatch.setattr(os, "walk", racing_walk)
    assert await Snapshot._capture_allowed() is allowed
    assert renamed == renames


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["track", "patch", "diff"])
async def test_failed_add_stops_capture(tmp_path, monkeypatch, operation):
    from unittest.mock import AsyncMock

    git_dir = Path(Snapshot._gitdir("test"))
    git_dir.mkdir(parents=True)
    run = AsyncMock(return_value=None)
    monkeypatch.setattr(Snapshot, "_run_git", run)
    args = ("test", str(tmp_path)) if operation == "track" else ("test", str(tmp_path), "old")
    await getattr(Snapshot, operation)(*args)
    run.assert_awaited_once_with(["add", "."], str(git_dir), str(tmp_path))


@pytest.mark.asyncio
async def test_missing_tree_does_not_unlink(tmp_path):
    file = tmp_path / "keep.txt"
    file.write_text("keep me")
    await Snapshot.revert("missing", str(tmp_path), [SnapshotPatch(hash="missing", files=[str(file)])])
    assert file.read_text() == "keep me"


@pytest.mark.asyncio
@pytest.mark.parametrize("check_result", [None, "100644 blob abc\\tkeep.txt\\n"])
async def test_failed_checkout_does_not_unlink_on_uncertain_tree(tmp_path, monkeypatch, check_result):
    from unittest.mock import AsyncMock

    file = tmp_path / "keep.txt"
    file.write_text("keep me")
    monkeypatch.setattr(Snapshot, "_run_git", AsyncMock(side_effect=[None, check_result]))
    await Snapshot.revert("test", str(tmp_path), [SnapshotPatch(hash="old", files=[str(file)])])
    assert file.read_text() == "keep me"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("ignore_terminate", [False, True])
@pytest.mark.parametrize("inherited_pipe", [False, pytest.param(True, marks=pytest.mark.skipif(os.name != "posix", reason="POSIX process groups"))])
async def test_git_child_reaped_on_timeout_or_cancel(tmp_path, monkeypatch, cancel, ignore_terminate, inherited_pipe):
    if ignore_terminate and os.name != "posix":
        pytest.skip("Ignoring SIGTERM requires POSIX")
    import sys
    from unittest.mock import Mock

    create = asyncio.create_subprocess_exec
    ready = tmp_path / "ready"
    code = (
        "import signal, time; from pathlib import Path; "
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_terminate else "")
        + ("import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); " if inherited_pipe else "")
        + f"Path({str(ready)!r}).touch(); time.sleep(60)"
    )
    child = await create(sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                         start_new_session=(os.name == "posix"))
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        child.terminate = Mock(wraps=child.terminate)
        child.kill = Mock(wraps=child.kill)

        async def spawn(*args, **kwargs):
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(Snapshot._run_git(["status"], str(tmp_path / "git"), str(tmp_path), timeout=0.05))
        if cancel:
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
        else:
            assert await asyncio.wait_for(task, timeout=5) is None
        assert child.returncode is not None
        child.terminate.assert_called_once()
        assert child.kill.call_count == int(ignore_terminate or inherited_pipe)
    finally:
        if inherited_pipe:
            try:
                os.killpg(child.pid, 9)
            except ProcessLookupError:
                pass
        if child.returncode is None:
            child.kill()
        await child.wait()


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
