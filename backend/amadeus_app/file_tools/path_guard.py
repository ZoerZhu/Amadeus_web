"""Unified path boundary, deny-list, and cross-workspace authorization for file tools."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Directories never read or listed.
DENIED_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__",
    "node_modules", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "dist", "build", ".next", ".nuxt",
})

# Filenames never read or written.
DENIED_FILE_NAMES = frozenset({".env"})

# Suffixes never read or written (secrets, caches, binaries handled elsewhere).
DENIED_SUFFIXES = frozenset({
    ".key", ".pem", ".crt", ".pfx", ".p12",
    ".sqlite", ".sqlite3", ".db",
    ".pyc", ".pyo", ".class",
    ".moc3",
})

# Suffixes refused for writes (caches/compiled artifacts).
WRITE_DENIED_SUFFIXES = frozenset({".pyc", ".pyo", ".class"})

_ENV_PATTERN = re.compile(r"^\.env($|\.)", re.IGNORECASE)


def compute_sha256(text: str) -> str:
    """Return the hex sha256 digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PathGuard:
    """Resolve and validate paths against a workspace root + optional external allow-list.

    Read tools may resolve paths inside the workspace OR inside an explicitly
    authorized external path (cross-workspace reads are allowed but logged).
    Write tools may only resolve paths inside the workspace unless the caller
    has obtained task-scoped authorization for an external path.
    """

    def __init__(
        self,
        workspace_root: str,
        allowed_external_paths: list[str] | None = None,
    ) -> None:
        self._workspace = Path(workspace_root or ".").expanduser().resolve()
        self._external_roots: list[Path] = []
        for raw in allowed_external_paths or []:
            if raw:
                root = Path(raw).expanduser().resolve()
                self._external_roots.append(root)

    @property
    def workspace_root(self) -> Path:
        return self._workspace

    @property
    def external_roots(self) -> list[Path]:
        return list(self._external_roots)

    def is_within_workspace(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._workspace)
            return True
        except ValueError:
            return False

    def is_external_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        for root in self._external_roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def resolve_read(self, path_text: str) -> Path:
        """Resolve a path for reading. Allows workspace + authorized external paths."""
        candidate = Path(path_text).expanduser()
        if not candidate.is_absolute():
            candidate = self._workspace / candidate
        resolved = candidate.resolve()
        if self.is_within_workspace(resolved):
            return resolved
        if self.is_external_allowed(resolved):
            return resolved
        raise ValueError(
            f"path '{path_text}' is outside the workspace and not authorized"
        )

    def resolve_write(self, path_text: str) -> Path:
        """Resolve a path for writing. Only workspace paths allowed by default.

        Cross-workspace writes require the external path to be present in the
        allow-list (set after task-scoped authorization).
        """
        candidate = Path(path_text).expanduser()
        if not candidate.is_absolute():
            candidate = self._workspace / candidate
        resolved = candidate.resolve()
        if self.is_within_workspace(resolved):
            return resolved
        if self.is_external_allowed(resolved):
            return resolved
        raise ValueError(
            f"write path '{path_text}' is outside the workspace and not authorized"
        )

    def ensure_not_denied(self, path: Path) -> None:
        """Reject denied directory names, filenames, and suffixes."""
        for parent in path.parents:
            if parent.name in DENIED_DIR_NAMES:
                raise ValueError(f"path denied: contains '{parent.name}'")
        if path.name in DENIED_FILE_NAMES:
            raise ValueError(f"file denied: '{path.name}'")
        if _ENV_PATTERN.match(path.name) and path.name != ".env.example":
            raise ValueError(f"file denied: '{path.name}'")
        if path.suffix.lower() in DENIED_SUFFIXES:
            raise ValueError(f"suffix denied: '{path.suffix}'")

    def ensure_writable_suffix(self, path: Path) -> None:
        """Reject suffixes that must never be written directly."""
        if path.suffix.lower() in WRITE_DENIED_SUFFIXES:
            raise ValueError(f"write denied for suffix: '{path.suffix}'")

    def relative_to_workspace(self, path: Path) -> str:
        """Return path relative to workspace (forward slashes), or absolute str.

        For external paths, returns the resolved absolute path with forward
        slashes so ``FileChangeTracker`` can resolve it back during rollback.
        """
        try:
            rel = path.resolve().relative_to(self._workspace)
            return str(rel).replace("\\", "/")
        except ValueError:
            return str(path.resolve()).replace("\\", "/")
