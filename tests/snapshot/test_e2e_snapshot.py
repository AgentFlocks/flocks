"""
End-to-end tests for snapshot and session revert integration

Tests the complete workflow of:
1. Creating a session
2. Making file changes
3. Tracking snapshots
4. Reverting changes
"""

import os
import asyncio
import tempfile
import shutil
import pytest
from pathlib import Path

from flocks.snapshot import Snapshot, SnapshotPatch
from flocks.session import Session, SessionRevertManager, RevertInput
from flocks.config.config import Config


class TestE2ESnapshotWorkflow:
    """End-to-end tests for snapshot workflow"""
    
    def setup_method(self):
        """Set up test environment"""
        # Create temporary directory for tests
        self.test_dir = tempfile.mkdtemp(prefix="flocks_e2e_test_")
        self.project_id = "e2e_test_project"
        
        # Initialize a git repo in test directory
        os.system(f"cd {self.test_dir} && git init -q")
        
        # Create initial file structure
        self._create_file("src/main.py", "# Main module\nprint('Hello')")
        self._create_file("src/utils.py", "# Utils module\ndef helper(): pass")
        self._create_file("README.md", "# Test Project")
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    def _create_file(self, rel_path: str, content: str):
        """Helper to create a file with content"""
        full_path = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
    
    def _read_file(self, rel_path: str) -> str:
        """Helper to read file content"""
        full_path = os.path.join(self.test_dir, rel_path)
        with open(full_path, "r") as f:
            return f.read()
    
    @pytest.mark.asyncio
    async def test_complete_snapshot_workflow(self):
        """Test complete snapshot create -> modify -> revert workflow"""
        
        # Step 1: Track initial state
        initial_hash = await Snapshot.track(self.project_id, self.test_dir)
        assert initial_hash is not None
        
        # Step 2: Make changes to files
        self._create_file("src/main.py", "# Main module v2\nprint('Hello World!')")
        self._create_file("src/new_feature.py", "# New feature\ndef feature(): pass")
        
        # Step 3: Verify changes are detected
        patch = await Snapshot.patch(self.project_id, self.test_dir, initial_hash)
        assert len(patch.files) == 2  # main.py modified, new_feature.py added
        
        # Step 4: Get diff
        diff = await Snapshot.diff(self.project_id, self.test_dir, initial_hash)
        assert "main.py" in diff
        assert "Hello World" in diff
        
        # Step 5: Track new state
        new_hash = await Snapshot.track(self.project_id, self.test_dir)
        assert new_hash != initial_hash
        
        # Step 6: Make more changes
        self._create_file("src/main.py", "# Main module v3\nprint('Final version')")
        
        # Step 7: Restore to initial state
        success = await Snapshot.restore(self.project_id, self.test_dir, initial_hash)
        assert success
        
        # Step 8: Verify restoration
        content = self._read_file("src/main.py")
        assert "# Main module" in content
        assert "Hello World" not in content
    
    @pytest.mark.asyncio
    async def test_selective_file_revert(self):
        """Test reverting only specific files"""
        
        # Track initial state
        initial_hash = await Snapshot.track(self.project_id, self.test_dir)
        
        # Modify multiple files
        self._create_file("src/main.py", "# Modified main")
        self._create_file("src/utils.py", "# Modified utils")
        self._create_file("README.md", "# Modified README")
        
        # Revert only main.py
        main_path = os.path.join(self.test_dir, "src/main.py")
        patch = SnapshotPatch(hash=initial_hash, files=[main_path])
        await Snapshot.revert(self.project_id, self.test_dir, [patch])
        
        # Verify main.py reverted, others unchanged
        assert "# Main module" in self._read_file("src/main.py")
        assert "# Modified utils" in self._read_file("src/utils.py")
        assert "# Modified README" in self._read_file("README.md")
    
    @pytest.mark.asyncio
    async def test_multiple_snapshot_points(self):
        """Test tracking multiple snapshot points"""
        
        # Initial state
        hash1 = await Snapshot.track(self.project_id, self.test_dir)
        
        # First change
        self._create_file("src/main.py", "# Version 1")
        hash2 = await Snapshot.track(self.project_id, self.test_dir)
        
        # Second change
        self._create_file("src/main.py", "# Version 2")
        hash3 = await Snapshot.track(self.project_id, self.test_dir)
        
        # Verify all hashes are different
        assert hash1 != hash2
        assert hash2 != hash3
        assert hash1 != hash3
        
        # Restore to version 1
        await Snapshot.restore(self.project_id, self.test_dir, hash2)
        assert "# Version 1" in self._read_file("src/main.py")
        
        # Restore to original
        await Snapshot.restore(self.project_id, self.test_dir, hash1)
        assert "# Main module" in self._read_file("src/main.py")
    
    @pytest.mark.asyncio
    async def test_diff_full_between_snapshots(self):
        """Test getting full diffs between two snapshots"""
        
        # Initial state with content
        self._create_file("src/main.py", "line1\nline2\nline3")
        hash1 = await Snapshot.track(self.project_id, self.test_dir)
        
        # Modify file
        self._create_file("src/main.py", "line1\nmodified_line2\nline3\nline4")
        hash2 = await Snapshot.track(self.project_id, self.test_dir)
        
        # Get full diff
        diffs = await Snapshot.diff_full(self.project_id, self.test_dir, hash1, hash2)
        
        assert len(diffs) >= 1
        main_diff = next((d for d in diffs if "main.py" in d.file), None)
        assert main_diff is not None
        assert "line2" in main_diff.before
        assert "modified_line2" in main_diff.after


class TestE2ESessionRevert:
    """End-to-end tests for session revert with snapshots"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="flocks_e2e_session_test_")
        self.project_id = "e2e_session_project"
        os.system(f"cd {self.test_dir} && git init -q")
        
        # Create initial files
        self._create_file("code.py", "# Original code")
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    def _create_file(self, rel_path: str, content: str):
        full_path = os.path.join(self.test_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
    
    def _read_file(self, rel_path: str) -> str:
        full_path = os.path.join(self.test_dir, rel_path)
        with open(full_path, "r") as f:
            return f.read()
    
    @pytest.mark.asyncio
    async def test_session_with_snapshot_tracking(self):
        """Test session operations with snapshot tracking"""
        
        # Create session
        session = await Session.create(
            project_id=self.project_id,
            directory=self.test_dir,
            title="Test Session"
        )
        assert session is not None
        
        # Track snapshot
        hash = await Snapshot.track(self.project_id, self.test_dir)
        assert hash is not None
        
        # Make file changes (simulating agent work)
        self._create_file("code.py", "# Modified by agent")
        
        # Verify changes
        diff = await Snapshot.diff(self.project_id, self.test_dir, hash)
        assert "Modified by agent" in diff
        
        # Set revert state on session
        await Session.set_revert(
            project_id=self.project_id,
            session_id=session.id,
            message_id="msg_001",
            snapshot=hash,
            diff=diff
        )
        
        # Verify revert state
        updated_session = await Session.get(self.project_id, session.id)
        assert updated_session.revert is not None
        assert updated_session.revert.snapshot == hash


class TestCLISnapshotCommands:
    """Test CLI snapshot commands work correctly"""
    
    def setup_method(self):
        """Set up test environment"""
        self.test_dir = tempfile.mkdtemp(prefix="flocks_cli_test_")
        self.project_id = Path(self.test_dir).name
        os.system(f"cd {self.test_dir} && git init -q")
        
        # Create test file
        with open(os.path.join(self.test_dir, "test.txt"), "w") as f:
            f.write("Test content")
    
    def teardown_method(self):
        """Clean up test environment"""
        if hasattr(self, 'test_dir') and os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    @pytest.mark.asyncio
    async def test_snapshot_track_command(self):
        """Test that snapshot track produces valid output"""
        hash = await Snapshot.track(self.project_id, self.test_dir)
        assert hash is not None
        assert len(hash) == 40  # SHA-1 hash
    
    @pytest.mark.asyncio
    async def test_snapshot_cleanup_command(self):
        """Test that cleanup runs without error"""
        # First create some snapshots
        await Snapshot.track(self.project_id, self.test_dir)
        
        # Run cleanup
        await Snapshot.cleanup(self.project_id, self.test_dir)
        # Should not raise


@pytest.mark.asyncio
async def test_failed_unrevert_preserves_state(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    session = await Session.create("test", str(tmp_path), title="restore failure")
    await Session.set_revert("test", session.id, message_id="msg_001", snapshot="missing")
    before = await Session.get("test", session.id)
    clear = AsyncMock()
    monkeypatch.setattr(Session, "clear_revert", clear)
    with pytest.raises(ValueError, match="Failed to restore"):
        await SessionRevertManager.unrevert("test", session.id, str(tmp_path))
    clear.assert_not_called()
    after = await Session.get("test", session.id)
    assert after.revert == before.revert


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
