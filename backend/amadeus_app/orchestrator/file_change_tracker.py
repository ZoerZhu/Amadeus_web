from __future__ import annotations

import asyncio
import difflib
import shutil
from pathlib import Path
from typing import Any

from . import storage as orch_storage


class FileChangeTracker:
    """Tracks file changes during a task and supports rollback.
    
    Dual-track strategy:
    - Git workspaces: record baseline ref, collect diff at task end
    - Non-Git workspaces: snapshot old content per file_write/file_edit
    """

    MAX_SNAPSHOT_SIZE = 1_048_576  # 1MB

    def __init__(
        self,
        storage,
        workspace_path: str,
        task_id: str,
        *,
        external_roots: list[str] | None = None,
    ) -> None:
        self._storage = storage
        self._workspace = Path(workspace_path or ".").expanduser().resolve()
        self._task_id = task_id
        self.is_git_repo: bool = False
        self.git_baseline_ref: str | None = None
        self.git_baseline_dirty_paths: set[str] = set()
        self.git_baseline_untracked_paths: set[str] = set()
        self._seq = 0
        self._backup_store: Any = None
        self._external_roots: list[Path] = [
            Path(p).expanduser().resolve()
            for p in (external_roots or [])
            if p
        ]

    def add_external_root(self, path: str) -> None:
        """Add an external root authorized for this task (for rollback)."""
        if not path:
            return
        root = Path(path).expanduser().resolve()
        if root not in self._external_roots:
            self._external_roots.append(root)

    def attach_backup_store(self, store: Any) -> None:
        """Attach a FileBackupStore so rollback can restore deleted/moved files."""
        self._backup_store = store

    def cleanup_backups(self) -> None:
        """Remove all on-disk backups for this task."""
        if self._backup_store is not None:
            try:
                self._backup_store.cleanup()
            except Exception:  # noqa: BLE001
                pass

    async def initialize(self) -> None:
        """Called at task start. Detects Git and records baseline."""
        self.is_git_repo = await self._detect_git()
        if self.is_git_repo:
            self.git_baseline_ref = await self._git_head_ref()
            self.git_baseline_dirty_paths = await self._git_dirty_paths()
            self.git_baseline_untracked_paths = await self._git_untracked_paths()

    async def _detect_git(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--is-inside-work-tree",
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    async def _git_head_ref(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "HEAD",
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() if proc.returncode == 0 else None
        except Exception:  # noqa: BLE001
            return None

    async def _git_dirty_paths(self) -> set[str]:
        if not self.git_baseline_ref:
            return set()
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--no-color", "--name-only", self.git_baseline_ref,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return set()
            return {line.strip() for line in stdout.decode().splitlines() if line.strip()}
        except Exception:  # noqa: BLE001
            return set()

    async def _git_untracked_paths(self) -> set[str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "ls-files", "--others", "--exclude-standard",
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return set()
            return {line.strip() for line in stdout.decode().splitlines() if line.strip()}
        except Exception:  # noqa: BLE001
            return set()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def record_write(
        self,
        rel_path: str,
        old_content: str | None,
        new_content: str,
        source: str = "file_write",
    ) -> dict[str, Any] | None:
        """Record a file write. Called after file_write/file_edit."""
        if self.is_git_repo:
            # Git mode: skip per-write recording, collect at end
            return None

        change_type = "created" if old_content is None else "modified"

        # Generate diff for non-Git mode
        diff_text = ""
        if old_content is not None and len(old_content) <= self.MAX_SNAPSHOT_SIZE:
            diff_lines = difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
            diff_text = "".join(diff_lines)

        # Store old snapshot (only if small enough)
        old_snapshot = old_content if (old_content and len(old_content) <= self.MAX_SNAPSHOT_SIZE) else ""

        return await orch_storage.create_file_change(
            self._storage,
            task_id=self._task_id,
            seq=self._next_seq(),
            relative_path=rel_path,
            change_type=change_type,
            diff_text=diff_text,
            old_snapshot=old_snapshot,
            source=source,
        )

    async def record_delete(
        self,
        rel_path: str,
        old_content: str | None = None,
        source: str = "file_delete",
    ) -> dict[str, Any] | None:
        """Record a file deletion (non-git mode)."""
        if self.is_git_repo:
            return None
        old_snapshot = old_content if (old_content and len(old_content) <= self.MAX_SNAPSHOT_SIZE) else ""
        return await orch_storage.create_file_change(
            self._storage,
            task_id=self._task_id,
            seq=self._next_seq(),
            relative_path=rel_path,
            change_type="deleted",
            diff_text="",
            old_snapshot=old_snapshot,
            source=source,
        )

    async def record_move(
        self,
        src_rel: str,
        dst_rel: str,
        old_content: str | None = None,
        source: str = "file_move",
    ) -> list[dict[str, Any]]:
        """Record a move as a delete of src + create of dst (non-git mode)."""
        if self.is_git_repo:
            return []
        changes: list[dict[str, Any]] = []
        delete_change = await self.record_delete(src_rel, old_content=old_content, source=source)
        if delete_change:
            changes.append(delete_change)
        create_change = await orch_storage.create_file_change(
            self._storage,
            task_id=self._task_id,
            seq=self._next_seq(),
            relative_path=dst_rel,
            change_type="created",
            diff_text="",
            old_snapshot="",
            source=source,
        )
        if create_change:
            changes.append(create_change)
        return changes

    async def record_patch(
        self,
        rel_path: str,
        old_content: str | None,
        new_content: str,
        source: str = "file_patch",
    ) -> dict[str, Any] | None:
        """Record a file patch (alias for record_write with file_patch source)."""
        return await self.record_write(rel_path, old_content, new_content, source=source)

    async def record_mkdir(
        self,
        rel_path: str,
        source: str = "file_mkdir",
    ) -> dict[str, Any] | None:
        """Record a directory creation (non-git mode) so rollback can remove it."""
        if self.is_git_repo:
            return None
        return await orch_storage.create_file_change(
            self._storage,
            task_id=self._task_id,
            seq=self._next_seq(),
            relative_path=rel_path,
            change_type="mkdir",
            diff_text="",
            old_snapshot="",
            source=source,
        )

    async def collect_git_diff(self) -> list[dict[str, Any]]:
        """Called at task end in Git mode. Collects all changes via git diff."""
        if not self.is_git_repo or not self.git_baseline_ref:
            return []
        if await orch_storage.list_file_changes(self._storage, self._task_id):
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--no-color", "--name-status", self.git_baseline_ref,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
        except Exception:  # noqa: BLE001
            return []

        changes: list[dict[str, Any]] = []
        for line in stdout.decode().splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status_code = parts[0].strip()
            file_path = parts[1].strip()
            if file_path in self.git_baseline_dirty_paths:
                continue

            if status_code.startswith("A"):
                change_type = "created"
            elif status_code.startswith("D"):
                change_type = "deleted"
            else:
                change_type = "modified"

            # Get the diff for this file
            diff_text = await self._git_file_diff(file_path)

            change = await orch_storage.create_file_change(
                self._storage,
                task_id=self._task_id,
                seq=self._next_seq(),
                relative_path=file_path,
                change_type=change_type,
                diff_text=diff_text,
                old_snapshot="",  # Git mode doesn't need snapshots
                source="git_diff",
            )
            changes.append(change)

        current_untracked = await self._git_untracked_paths()
        for file_path in sorted(current_untracked - self.git_baseline_untracked_paths):
            change = await orch_storage.create_file_change(
                self._storage,
                task_id=self._task_id,
                seq=self._next_seq(),
                relative_path=file_path,
                change_type="created",
                diff_text="",
                old_snapshot="",
                source="git_untracked",
            )
            changes.append(change)

        return changes

    async def _git_file_diff(self, file_path: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--no-color", self.git_baseline_ref, "--", file_path,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode() if proc.returncode == 0 else ""
        except Exception:  # noqa: BLE001
            return ""

    async def rollback(self) -> dict[str, Any]:
        """Rollback the workspace to pre-task state."""
        if self.is_git_repo:
            return await self._rollback_git()
        return await self._rollback_snapshots()

    def restore_baseline(
        self,
        ref: str,
        *,
        dirty_paths: list[str] | set[str] | None = None,
        untracked_paths: list[str] | set[str] | None = None,
    ) -> None:
        """Restore a previously persisted git baseline ref without re-detecting HEAD.

        Use this when the tracker is recreated after the task has already run,
        so we don't capture the post-task HEAD instead of the pre-task baseline.
        """
        self.is_git_repo = True
        self.git_baseline_ref = ref
        self.git_baseline_dirty_paths = set(dirty_paths or [])
        self.git_baseline_untracked_paths = set(untracked_paths or [])

    def baseline_state(self) -> dict[str, Any]:
        return {
            "git_baseline_ref": self.git_baseline_ref,
            "git_baseline_dirty_paths": sorted(self.git_baseline_dirty_paths),
            "git_baseline_untracked_paths": sorted(self.git_baseline_untracked_paths),
        }

    def _change_path(self, relative_path: str) -> Path | None:
        # External paths: stored as absolute forward-slash strings.
        if Path(relative_path).is_absolute():
            try:
                target = Path(relative_path).resolve()
            except OSError:
                return None
            for root in self._external_roots:
                try:
                    target.relative_to(root)
                    return target
                except ValueError:
                    continue
            return None
        # Workspace-relative paths
        target = (self._workspace / relative_path).resolve()
        try:
            target.relative_to(self._workspace)
        except ValueError:
            return None
        return target

    async def _rollback_git(self) -> dict[str, Any]:
        if not self.git_baseline_ref:
            return {"ok": False, "reason": "No git baseline ref recorded."}

        changes = await orch_storage.list_file_changes(self._storage, self._task_id)
        if not changes:
            return {
                "ok": False,
                "reason": "No tracked file changes recorded for this task.",
                "method": "git",
                "baselineRef": self.git_baseline_ref,
            }

        restored = 0
        errors: list[str] = []
        for change in reversed(changes):
            rel_path = str(change.get("relativePath") or "")
            if not rel_path:
                continue
            target_path = self._change_path(rel_path)
            if target_path is None:
                errors.append(f"{rel_path}: path escapes workspace")
                continue
            change_type = change.get("changeType")
            if change_type == "created":
                try:
                    if target_path.is_dir():
                        shutil.rmtree(target_path)
                    else:
                        target_path.unlink(missing_ok=True)
                    restored += 1
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{rel_path}: {error}")
                continue

            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "checkout", self.git_baseline_ref, "--", rel_path,
                    cwd=str(self._workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr_bytes = await proc.communicate()
                if proc.returncode != 0:
                    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                    errors.append(f"{rel_path}: git checkout failed (exit {proc.returncode}): {stderr_text}")
                else:
                    restored += 1
            except Exception as error:  # noqa: BLE001
                errors.append(f"{rel_path}: {error}")

        result = {
            "ok": len(errors) == 0,
            "method": "git",
            "baselineRef": self.git_baseline_ref,
            "restored": restored,
            "errors": errors,
        }
        if errors:
            result["reason"] = "; ".join(errors[:3])
        return result

    def _rmdir_empty_chain(self, start_path: Path) -> int:
        """Remove ``start_path`` and any now-empty parent directories up to
        (but not including) the workspace root or an external root.

        Returns the number of directories removed. ``rmdir`` only succeeds on
        empty directories, so pre-existing content is naturally preserved.
        """
        workspace = self._workspace.resolve()
        roots = [workspace]
        for root in self._external_roots:
            try:
                roots.append(Path(root).resolve())
            except Exception:  # noqa: BLE001
                pass

        removed = 0
        current = start_path.resolve()
        while True:
            if current in roots or current == current.parent:
                break
            try:
                current.rmdir()
            except OSError:
                break
            removed += 1
            current = current.parent
            try:
                resolved_parent = current.resolve()
            except Exception:  # noqa: BLE001
                break
            if resolved_parent in roots:
                break
            current = resolved_parent
        return removed

    async def _rollback_snapshots(self) -> dict[str, Any]:
        changes = await orch_storage.list_file_changes(self._storage, self._task_id)
        restored = 0
        errors: list[str] = []

        for change in reversed(changes):
            full_path = self._change_path(change["relativePath"])
            if full_path is None:
                errors.append(f"{change['relativePath']}: path escapes workspace")
                continue
            try:
                if change["changeType"] == "created":
                    if full_path.is_dir():
                        shutil.rmtree(full_path, ignore_errors=True)
                    else:
                        full_path.unlink(missing_ok=True)
                    restored += 1
                elif change["changeType"] == "mkdir":
                    # Remove the created directory if empty (files rolled back first)
                    if full_path.is_dir():
                        removed = self._rmdir_empty_chain(full_path)
                        if removed:
                            restored += removed
                    restored += 1
                elif change["changeType"] == "modified" and change["oldSnapshot"]:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(change["oldSnapshot"], encoding="utf-8")
                    restored += 1
                elif change["changeType"] == "deleted" and change.get("oldSnapshot"):
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(change["oldSnapshot"], encoding="utf-8")
                    restored += 1
            except Exception as error:  # noqa: BLE001
                errors.append(f"{change['relativePath']}: {error}")

        return {
            "ok": len(errors) == 0,
            "method": "snapshot",
            "restored": restored,
            "errors": errors,
        }
