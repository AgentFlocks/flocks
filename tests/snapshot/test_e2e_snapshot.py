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
from flocks.session import Message, MessageRole, PatchPart, Session, SessionRevertManager, RevertInput
from flocks.session.lifecycle.rewind import SessionRewind
from flocks.session.runner import SessionRunner, StepResult
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
        self.project_id = f"e2e_session_project_{Path(self.test_dir).name}"
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

    @pytest.mark.asyncio
    async def test_revert_restores_files_from_typed_patch_part(self):
        """Test session revert recognizes typed PatchPart records."""
        session = await Session.create(
            project_id=self.project_id,
            directory=self.test_dir,
            title="Typed Patch Revert",
        )
        user = await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content="Modify code.py",
        )
        start_hash = await Snapshot.track(self.project_id, self.test_dir)

        assistant = await Message.create(
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            content="Updated code.py",
            parentID=user.id,
            modelID="test-model",
            providerID="test-provider",
        )
        self._create_file("code.py", "# Modified by assistant")
        self._create_file("new_file.py", "# Added by assistant")
        patch = await Snapshot.patch(self.project_id, self.test_dir, start_hash)
        await Message.store_part(
            session.id,
            assistant.id,
            PatchPart(
                sessionID=session.id,
                messageID=assistant.id,
                hash=start_hash,
                files=patch.files,
            ),
        )

        await SessionRevertManager.revert(
            project_id=self.project_id,
            input=RevertInput(sessionID=session.id, messageID=user.id),
            worktree=self.test_dir,
        )

        assert self._read_file("code.py") == "# Original code"
        assert not os.path.exists(os.path.join(self.test_dir, "new_file.py"))

    @pytest.mark.asyncio
    async def test_rewind_restores_files_from_latest_user_turn(self):
        """Test conversation rewind applies revert and restores files."""
        session = await Session.create(
            project_id=self.project_id,
            directory=self.test_dir,
            title="Conversation Rewind",
        )
        user = await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content="Modify code.py",
        )
        start_hash = await Snapshot.track(self.project_id, self.test_dir)

        assistant = await Message.create(
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            content="Updated code.py",
            parentID=user.id,
            modelID="test-model",
            providerID="test-provider",
        )
        self._create_file("code.py", "# Modified by assistant")
        patch = await Snapshot.patch(self.project_id, self.test_dir, start_hash)
        await Message.store_part(
            session.id,
            assistant.id,
            PatchPart(
                sessionID=session.id,
                messageID=assistant.id,
                hash=start_hash,
                files=patch.files,
            ),
        )

        result = await SessionRewind.rewind(session.id)

        assert result.target_message.id == user.id
        assert result.session.revert is not None
        assert result.session.revert.message_id == user.id
        assert self._read_file("code.py") == "# Original code"

    @pytest.mark.asyncio
    async def test_rewind_candidates_list_user_turns_newest_first(self):
        """Test bare /rewind can present selectable user-turn candidates."""
        session = await Session.create(
            project_id=self.project_id,
            directory=self.test_dir,
            title="Conversation Rewind Candidates",
        )
        first = await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content="First change",
        )
        await Message.create(
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            content="First answer",
            parentID=first.id,
            modelID="test-model",
            providerID="test-provider",
        )
        second = await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content="Second change",
        )

        candidates = await SessionRewind.candidates(session.id)

        assert [candidate.message.id for candidate in candidates] == [second.id, first.id]
        assert candidates[0].index == 1
        assert candidates[0].preview == "Second change"

    @pytest.mark.asyncio
    async def test_runner_records_patch_parts_for_rewind(self):
        """Test runner records snapshot and patch parts for a step."""
        session = await Session.create(
            project_id=self.project_id,
            directory=self.test_dir,
            title="Runner Patch Recording",
        )
        user = await Message.create(
            session_id=session.id,
            role=MessageRole.USER,
            content="Modify code.py",
        )
        assistant = await Message.create(
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            content="Updated code.py",
            parentID=user.id,
            modelID="test-model",
            providerID="test-provider",
        )
        runner = SessionRunner(session)

        start_snapshot = await runner._record_step_start_snapshot(assistant)
        self._create_file("code.py", "# Modified by runner")
        await runner._record_step_finish_snapshot(
            assistant,
            start_snapshot,
            StepResult(action="stop", content="done"),
        )

        parts = await Message.parts(assistant.id, session.id)
        part_types = [part.type for part in parts]
        patch_part = next(part for part in parts if part.type == "patch")

        assert "step-start" in part_types
        assert "step-finish" in part_types
        assert "patch" in part_types
        assert os.path.join(self.test_dir, "code.py") in patch_part.files


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


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
