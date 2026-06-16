"""Shadow-git snapshots for conversation rewind."""

import asyncio
import fnmatch
import hashlib
import os
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from flocks.utils.log import Log
from flocks.config.config import Config

log = Log.create(service="snapshot")

# Constants
PRUNE_DAYS = "7.days"
CLEANUP_INTERVAL_HOURS = 1
DEFAULT_EXCLUDES = [
    "node_modules/",
    "dist/",
    "build/",
    ".env",
    ".env.*",
    ".env.local",
    ".env.*.local",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "*.log",
    ".cache/",
    ".next/",
    ".nuxt/",
    "coverage/",
    ".pytest_cache/",
    ".venv/",
    "venv/",
    ".git/",
]
GIT_TIMEOUT_SECONDS = max(10, min(60, int(os.getenv("FLOCKS_SNAPSHOT_TIMEOUT", "30"))))
MAX_SNAPSHOT_FILES = max(1, int(os.getenv("FLOCKS_SNAPSHOT_MAX_FILES", "50000")))
COMMIT_HASH_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")


class SnapshotPatch(BaseModel):
    """Snapshot patch information"""
    hash: str = Field(..., description="Checkpoint commit hash")
    files: List[str] = Field(default_factory=list, description="Changed file paths")


class FileDiff(BaseModel):
    """File diff information"""
    file: str = Field(..., description="File path relative to worktree")
    before: str = Field("", description="Content before change")
    after: str = Field("", description="Content after change")
    additions: int = Field(0, description="Lines added")
    deletions: int = Field(0, description="Lines deleted")


class Snapshot:
    """
    Snapshot namespace for git-based file tracking.

    Uses a shadow git repository per worktree. The repository is stored under
    the Flocks data directory and is accessed through GIT_DIR/GIT_WORK_TREE, so
    no git metadata is written into the user's project.
    """
    
    # Class-level state
    _initialized: bool = False
    _cleanup_task: Optional[asyncio.Task] = None
    
    @classmethod
    def init(cls, project_id: str, worktree: str) -> None:
        """
        Initialize snapshot system with cleanup scheduler
        
        Args:
            project_id: Project ID for snapshot storage
            worktree: Working tree directory
        """
        if cls._initialized:
            return
        
        cls._initialized = True
        log.info("snapshot.init", {"project_id": project_id})
    
    @classmethod
    async def cleanup(cls, project_id: str, worktree: str) -> None:
        """
        Clean up old snapshots using git gc
        
        Args:
            project_id: Project ID
            worktree: Working tree directory
        """
        cfg = await Config.get()
        if cfg.snapshot is False:
            return
        
        git_dir = cls._gitdir(project_id, worktree)
        
        # Check if git directory exists
        if not os.path.exists(git_dir):
            return
        
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "gc",
                f"--prune={PRUNE_DAYS}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=worktree,
                env=cls._git_env(git_dir, worktree),
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                log.warn("snapshot.cleanup.failed", {
                    "exit_code": process.returncode,
                    "stderr": stderr.decode("utf-8", errors="replace"),
                })
                return
            
            log.info("snapshot.cleanup", {"prune": PRUNE_DAYS})
            
        except Exception as e:
            log.error("snapshot.cleanup.error", {"error": str(e)})
    
    @classmethod
    async def track(cls, project_id: str, worktree: str, vcs: str = "git") -> Optional[str]:
        """
        Track current state and return a checkpoint commit hash.
        
        Args:
            project_id: Project ID
            worktree: Working tree directory
            vcs: VCS type (only 'git' supported)
            
        Returns:
            Checkpoint commit hash or None if failed
        """
        if vcs != "git":
            return None
        
        cfg = await Config.get()
        if cfg.snapshot is False:
            return None
        
        worktree_path = cls._normalize_worktree(worktree)
        if not cls._is_worktree_safe(worktree_path):
            log.debug("snapshot.track.skipped_broad_worktree", {"worktree": str(worktree_path)})
            return None
        if cls._dir_file_count(worktree_path) > MAX_SNAPSHOT_FILES:
            log.debug("snapshot.track.skipped_too_many_files", {
                "worktree": str(worktree_path),
                "max_files": MAX_SNAPSHOT_FILES,
            })
            return None

        git_dir = cls._gitdir(project_id, str(worktree_path))
        if not await cls._init_repo(git_dir, str(worktree_path)):
            return None

        if await cls._run_git(["add", "-A"], git_dir, str(worktree_path)) is None:
            return None

        head = await cls._current_head(git_dir, str(worktree_path))
        if head:
            changed = await cls._has_staged_changes(git_dir, str(worktree_path), head)
            if not changed:
                log.debug("snapshot.track.reusing_head", {
                    "hash": head,
                    "worktree": str(worktree_path),
                })
                return head

        message = "snapshot checkpoint"
        if await cls._run_git(
            ["commit", "-m", message, "--allow-empty", "--no-gpg-sign"],
            git_dir,
            str(worktree_path),
            timeout=GIT_TIMEOUT_SECONDS * 2,
        ) is None:
            return None

        commit_hash = await cls._current_head(git_dir, str(worktree_path))
        if not commit_hash:
            return None

        log.info("snapshot.tracking", {"hash": commit_hash, "worktree": str(worktree_path)})
        return commit_hash
    
    @classmethod
    async def patch(cls, project_id: str, worktree: str, hash: str) -> SnapshotPatch:
        """
        Get list of changed files since checkpoint
        
        Args:
            project_id: Project ID
            worktree: Working tree directory
            hash: Checkpoint commit hash to compare against
            
        Returns:
            Patch information with changed files
        """
        git_dir = cls._gitdir(project_id, worktree)
        if not cls._valid_commit_hash(hash):
            log.warn("snapshot.patch.invalid_hash", {"hash": hash})
            return SnapshotPatch(hash=hash, files=[])

        await cls._run_git(["add", "-A"], git_dir, worktree)
        result = await cls._run_git(
            ["-c", "core.autocrlf=false", "diff", "--cached", "--no-ext-diff", "--name-only", hash, "--", "."],
            git_dir, worktree
        )
        
        if result is None:
            log.warn("snapshot.patch.failed", {"hash": hash})
            return SnapshotPatch(hash=hash, files=[])
        
        files = [
            str(cls._display_worktree(worktree) / f.strip())
            for f in result.strip().split("\n")
            if f.strip()
        ]
        
        return SnapshotPatch(hash=hash, files=files)
    
    @classmethod
    async def restore(cls, project_id: str, worktree: str, snapshot: str) -> bool:
        """
        Restore files to a snapshot state
        
        Args:
            project_id: Project ID
            worktree: Working tree directory
            snapshot: Git tree hash to restore
            
        Returns:
            True if successful
        """
        log.info("snapshot.restore", {"snapshot": snapshot})
        git_dir = cls._gitdir(project_id, worktree)
        if not cls._valid_commit_hash(snapshot):
            log.error("snapshot.restore.invalid_hash", {"snapshot": snapshot})
            return False

        await cls._create_pre_restore_checkpoint(git_dir, worktree, snapshot)
        result = await cls._run_git(["checkout", snapshot, "--", "."], git_dir, worktree)
        if result is None:
            log.error("snapshot.restore.checkout.failed", {"snapshot": snapshot})
            return False
        
        return True
    
    @classmethod
    async def revert(cls, project_id: str, worktree: str, patches: List[SnapshotPatch]) -> None:
        """
        Revert specific files to their snapshot states
        
        Args:
            project_id: Project ID
            worktree: Working tree directory
            patches: List of patches to revert
        """
        reverted_files = set()
        git_dir = cls._gitdir(project_id, worktree)
        
        for patch in patches:
            if not cls._valid_commit_hash(patch.hash):
                log.warn("snapshot.revert.invalid_hash", {"hash": patch.hash})
                continue
            for file in patch.files:
                if file in reverted_files:
                    continue
                
                log.info("snapshot.reverting", {"file": file, "hash": patch.hash})

                relative_path = cls._relative_path(file, worktree)
                if not relative_path:
                    log.warn("snapshot.revert.path_outside_worktree", {"file": file})
                    continue

                exists_at_snapshot = await cls._file_exists_at_commit(
                    git_dir,
                    worktree,
                    patch.hash,
                    relative_path,
                )
                if exists_at_snapshot:
                    result = await cls._run_git(
                        ["checkout", patch.hash, "--", relative_path],
                        git_dir, worktree
                    )
                    if result is None:
                        log.info("snapshot.revert.file_existed_but_failed", {"file": file})
                else:
                    log.info("snapshot.revert.deleting", {"file": file})
                    try:
                        Path(worktree, relative_path).unlink()
                    except OSError:
                        pass

                reverted_files.add(file)
    
    @classmethod
    async def diff(cls, project_id: str, worktree: str, hash: str) -> str:
        """
        Get diff between snapshot and current state
        
        Args:
            project_id: Project ID
            worktree: Working tree directory
            hash: Git tree hash to compare against
            
        Returns:
            Diff text
        """
        git_dir = cls._gitdir(project_id, worktree)
        if not cls._valid_commit_hash(hash):
            log.warn("snapshot.diff.invalid_hash", {"hash": hash})
            return ""

        await cls._run_git(["add", "-A"], git_dir, worktree)
        result = await cls._run_git(
            ["-c", "core.autocrlf=false", "diff", "--cached", "--no-ext-diff", hash, "--", "."],
            git_dir, worktree
        )
        
        if result is None:
            log.warn("snapshot.diff.failed", {"hash": hash})
            return ""
        
        return result.strip()
    
    @classmethod
    async def diff_full(
        cls,
        project_id: str,
        worktree: str,
        from_hash: str,
        to_hash: str
    ) -> List[FileDiff]:
        """
        Get full file diffs between two snapshots
        
        Args:
            project_id: Project ID
            worktree: Working tree directory
            from_hash: Starting Git tree hash
            to_hash: Ending Git tree hash
            
        Returns:
            List of file diffs with before/after content
        """
        result: List[FileDiff] = []
        git_dir = cls._gitdir(project_id, worktree)
        if not cls._valid_commit_hash(from_hash) or not cls._valid_commit_hash(to_hash):
            return result
        
        # Get numstat diff
        numstat = await cls._run_git(
            ["-c", "core.autocrlf=false", "diff", "--no-ext-diff", "--no-renames",
             "--numstat", from_hash, to_hash, "--", "."],
            git_dir, worktree
        )
        
        if numstat is None:
            return result
        
        for line in numstat.strip().split("\n"):
            if not line:
                continue
            
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            
            additions_str, deletions_str, file_path = parts[0], parts[1], parts[2]
            
            # Handle binary files
            is_binary = additions_str == "-" and deletions_str == "-"
            
            if is_binary:
                before = ""
                after = ""
                additions = 0
                deletions = 0
            else:
                # Get file content at each revision
                before = await cls._run_git(
                    ["show", f"{from_hash}:{file_path}"],
                    git_dir, worktree
                ) or ""
                
                after = await cls._run_git(
                    ["show", f"{to_hash}:{file_path}"],
                    git_dir, worktree
                ) or ""
                
                try:
                    additions = int(additions_str)
                    deletions = int(deletions_str)
                except ValueError:
                    additions = 0
                    deletions = 0
            
            result.append(FileDiff(
                file=file_path,
                before=before,
                after=after,
                additions=additions,
                deletions=deletions,
            ))
        
        return result
    
    @classmethod
    def _gitdir(cls, project_id: str, worktree: Optional[str] = None) -> str:
        """
        Get shadow git directory path for a worktree.
        
        Args:
            project_id: Project ID, used only as a compatibility fallback
            
        Returns:
            Path to git directory
        """
        data_path = Config.get_data_path()
        if worktree:
            normalized = str(cls._normalize_worktree(worktree))
            repo_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        else:
            repo_id = project_id
        return str(data_path / "snapshot" / repo_id)

    @classmethod
    def _normalize_worktree(cls, worktree: str) -> Path:
        return Path(worktree).expanduser().resolve()

    @classmethod
    def _display_worktree(cls, worktree: str) -> Path:
        return Path(worktree).expanduser().absolute()

    @classmethod
    def _is_worktree_safe(cls, worktree: Path) -> bool:
        return worktree.exists() and worktree.is_dir() and worktree != Path("/") and worktree != Path.home()

    @classmethod
    def _git_env(cls, git_dir: str, worktree: str) -> dict[str, str]:
        env = os.environ.copy()
        env["GIT_DIR"] = git_dir
        env["GIT_WORK_TREE"] = str(cls._normalize_worktree(worktree))
        env.pop("GIT_INDEX_FILE", None)
        env.pop("GIT_NAMESPACE", None)
        env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        return env

    @classmethod
    async def _init_repo(cls, git_dir: str, worktree: str) -> bool:
        if os.path.exists(os.path.join(git_dir, "HEAD")):
            return True

        os.makedirs(git_dir, exist_ok=True)
        if await cls._run_git(["init"], git_dir, worktree) is None:
            return False

        await cls._run_git(["config", "user.email", "flocks@local"], git_dir, worktree)
        await cls._run_git(["config", "user.name", "Flocks Snapshot"], git_dir, worktree)
        await cls._run_git(["config", "core.autocrlf", "false"], git_dir, worktree)
        await cls._run_git(["config", "commit.gpgsign", "false"], git_dir, worktree)
        await cls._run_git(["config", "tag.gpgSign", "false"], git_dir, worktree)

        info_dir = Path(git_dir) / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        (info_dir / "exclude").write_text("\n".join(DEFAULT_EXCLUDES) + "\n", encoding="utf-8")
        (Path(git_dir) / "FLOCKS_WORKDIR").write_text(
            str(cls._normalize_worktree(worktree)) + "\n",
            encoding="utf-8",
        )
        log.info("snapshot.initialized", {"git_dir": git_dir, "worktree": worktree})
        return True

    @classmethod
    async def _current_head(cls, git_dir: str, worktree: str) -> Optional[str]:
        result = await cls._run_git(
            ["rev-parse", "--verify", "HEAD"],
            git_dir,
            worktree,
            allowed_returncodes={1, 128},
        )
        return result.strip() if result else None

    @classmethod
    async def _has_staged_changes(cls, git_dir: str, worktree: str, base_hash: str) -> bool:
        _, _, returncode = await cls._run_git_result(
            ["diff", "--cached", "--quiet", base_hash, "--"],
            git_dir,
            worktree,
            allowed_returncodes={1},
        )
        return returncode == 1

    @classmethod
    async def _file_exists_at_commit(
        cls,
        git_dir: str,
        worktree: str,
        commit_hash: str,
        relative_path: str,
    ) -> bool:
        _, _, returncode = await cls._run_git_result(
            ["cat-file", "-e", f"{commit_hash}:{relative_path}"],
            git_dir,
            worktree,
            allowed_returncodes={1, 128},
        )
        return returncode == 0

    @classmethod
    async def _create_pre_restore_checkpoint(cls, git_dir: str, worktree: str, snapshot: str) -> None:
        await cls._run_git(["add", "-A"], git_dir, worktree)
        head = await cls._current_head(git_dir, worktree)
        if head and not await cls._has_staged_changes(git_dir, worktree, head):
            return
        await cls._run_git(
            ["commit", "-m", f"pre-restore snapshot {snapshot[:8]}", "--allow-empty", "--no-gpg-sign"],
            git_dir,
            worktree,
            timeout=GIT_TIMEOUT_SECONDS * 2,
        )

    @classmethod
    def _valid_commit_hash(cls, commit_hash: str) -> bool:
        return bool(commit_hash and COMMIT_HASH_RE.match(commit_hash) and not commit_hash.startswith("-"))

    @classmethod
    def _relative_path(cls, file_path: str, worktree: str) -> Optional[str]:
        worktree_path = cls._normalize_worktree(worktree)
        path = Path(file_path)
        if not path.is_absolute():
            path = worktree_path / path
        try:
            return str(path.resolve().relative_to(worktree_path))
        except ValueError:
            return None

    @classmethod
    def _dir_file_count(cls, worktree: Path) -> int:
        count = 0
        try:
            for root, dirs, files in os.walk(worktree):
                dirs[:] = [
                    name
                    for name in dirs
                    if not cls._is_default_excluded(name, is_dir=True)
                ]
                count += len(files)
                if count > MAX_SNAPSHOT_FILES:
                    return count
        except (OSError, PermissionError):
            return count
        return count

    @classmethod
    def _is_default_excluded(cls, name: str, *, is_dir: bool) -> bool:
        candidate = f"{name}/" if is_dir else name
        return any(
            fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(name, pattern.rstrip("/"))
            for pattern in DEFAULT_EXCLUDES
        )
    
    @classmethod
    async def _run_git(
        cls,
        args: List[str],
        git_dir: str,
        worktree: str,
        timeout: float = float(GIT_TIMEOUT_SECONDS),
        allowed_returncodes: Optional[Set[int]] = None,
    ) -> Optional[str]:
        """
        Run a Git command with custom git-dir and work-tree
        
        Args:
            args: Git command arguments
            git_dir: Git directory path
            worktree: Working tree path
            timeout: Command timeout in seconds
            
        Returns:
            Command output or None if failed
        """
        stdout, _, returncode = await cls._run_git_result(
            args,
            git_dir,
            worktree,
            timeout=timeout,
            allowed_returncodes=allowed_returncodes,
        )
        if returncode != 0:
            return None
        return stdout

    @classmethod
    async def _run_git_result(
        cls,
        args: List[str],
        git_dir: str,
        worktree: str,
        timeout: float = float(GIT_TIMEOUT_SECONDS),
        allowed_returncodes: Optional[Set[int]] = None,
    ) -> Tuple[str, str, int]:
        allowed_returncodes = allowed_returncodes or set()
        normalized_worktree = cls._normalize_worktree(worktree)
        try:
            cmd = ["git"] + args
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(normalized_worktree),
                env=cls._git_env(git_dir, str(normalized_worktree)),
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
            returncode = process.returncode or 0
            if returncode != 0 and returncode not in allowed_returncodes:
                log.debug("snapshot.git.error", {
                    "args": args,
                    "stderr": stderr_text,
                    "returncode": returncode,
                })
            return stdout_text, stderr_text, returncode

        except asyncio.TimeoutError:
            log.warn("snapshot.git.timeout", {"args": args})
            return "", "timeout", -1
        except FileNotFoundError:
            log.warn("snapshot.git.not_found")
            return "", "git not found", -1
        except Exception as e:
            log.error("snapshot.git.error", {"args": args, "error": str(e)})
            return "", str(e), -1
