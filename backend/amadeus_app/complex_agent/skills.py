"""Skills system: import (ZIP/Git), validate, load on demand.

A skill package is a directory with the following structure::

    skill-id/
        SKILL.md            # main prompt / instructions (markdown)
        manifest.json       # metadata (id, name, version, triggers, ...)
        references/         # optional reference docs (markdown/text)
        assets/             # optional binary assets (images, etc.)

Skills are stored under ``userData/skills/`` (configurable via
``AMADEUS_SKILLS_DIR``). Each skill is unpacked into a subdirectory named
after the skill id.

Security:
- ZIP imports reject absolute paths and ``..`` traversal entries.
- Git imports clone with ``--depth 1`` and only accept the configured
  subdirectory.
- Skills cannot execute install scripts; only ``SKILL.md`` and
  ``references/`` content is loaded into the agent context.
- Tool allowlists are enforced by the unified tool registry at call time.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from .domain import SkillManifest, SkillPackageInfo

_log = get_logger(__name__)


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SKILLS_DIR = Path(os.getenv("AMADEUS_SKILLS_DIR", str(ROOT_DIR / "agent_state" / "skills")))

# A safe skill id: lowercase, alphanumeric + dash/underscore, 1..64 chars
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Maximum unpacked skill size (50 MB) to avoid zip bombs
MAX_SKILL_UNPACKED_BYTES = 50 * 1024 * 1024

# Files that must never be loaded as skill content (executable / hidden)
BLOCKED_FILE_NAMES = {".DS_Store", "Thumbs.db", "__pycache__"}


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def is_safe_relative_path(path: str | Path, base: Path) -> bool:
    """Return True if ``path`` resolves to a location inside ``base``."""
    try:
        resolved = (base / path).resolve()
    except (OSError, RuntimeError):
        return False
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        return False
    return True


def validate_skill_id(skill_id: str) -> bool:
    return bool(SKILL_ID_RE.match(skill_id))


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def load_manifest(skill_dir: Path) -> SkillManifest:
    manifest_path = skill_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"skill manifest not found: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid manifest.json: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError("manifest.json must be a JSON object")
    skill_id = str(raw.get("id", "")).strip()
    if not validate_skill_id(skill_id):
        raise ValueError(f"invalid skill id in manifest: {skill_id!r}")
    raw["id"] = skill_id
    return SkillManifest.model_validate(raw)


def find_skill_md(skill_dir: Path) -> Path | None:
    for candidate in (skill_dir / "SKILL.md", skill_dir / "skill.md"):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def list_reference_files(skill_dir: Path) -> list[Path]:
    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir():
        return []
    result: list[Path] = []
    for entry in sorted(refs_dir.rglob("*")):
        if not entry.is_file():
            continue
        if entry.name in BLOCKED_FILE_NAMES:
            continue
        if not is_safe_relative_path(entry.relative_to(refs_dir), refs_dir):
            continue
        result.append(entry)
    return result


def list_asset_files(skill_dir: Path) -> list[Path]:
    assets_dir = skill_dir / "assets"
    if not assets_dir.is_dir():
        return []
    result: list[Path] = []
    for entry in sorted(assets_dir.rglob("*")):
        if not entry.is_file():
            continue
        if entry.name in BLOCKED_FILE_NAMES:
            continue
        if not is_safe_relative_path(entry.relative_to(assets_dir), assets_dir):
            continue
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Skill context (loaded on demand into the agent)
# ---------------------------------------------------------------------------


@dataclass
class LoadedSkillContext:
    """The minimal context injected into the agent when a skill is active."""

    skill_id: str
    name: str
    description: str
    skill_md: str
    references: dict[str, str]
    manifest: SkillManifest


def load_skill_context(skill_dir: Path, *, max_reference_bytes: int = 200_000) -> LoadedSkillContext:
    manifest = load_manifest(skill_dir)
    skill_md_path = find_skill_md(skill_dir)
    skill_md = ""
    if skill_md_path is not None:
        skill_md = skill_md_path.read_text(encoding="utf-8", errors="replace")
    references: dict[str, str] = {}
    total = 0
    for ref in list_reference_files(skill_dir):
        try:
            content = ref.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if total + len(content) > max_reference_bytes:
            content = content[: max(0, max_reference_bytes - total)] + "\n...[truncated]"
        references[str(ref.relative_to(skill_dir))] = content
        total += len(content)
    return LoadedSkillContext(
        skill_id=manifest.id,
        name=manifest.name,
        description=manifest.description,
        skill_md=skill_md,
        references=references,
        manifest=manifest,
    )


# ---------------------------------------------------------------------------
# Import: ZIP
# ---------------------------------------------------------------------------


def import_skill_from_zip(zip_path: Path, skills_dir: Path | None = None) -> SkillPackageInfo:
    """Unpack a ZIP file into ``skills_dir/<skill_id>`` and return its info.

    The ZIP must contain a ``manifest.json`` at its root or in a single
    top-level directory.
    """
    target_root = (skills_dir or DEFAULT_SKILLS_DIR).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        # Security: reject path traversal and absolute paths
        for member in zf.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                raise ValueError(f"unsafe zip entry: {member}")
        # Find manifest.json to determine the skill id and target directory
        names = zf.namelist()
        manifest_name = next((n for n in names if n.endswith("manifest.json") and "/" not in n.rstrip("/").split("/")[-1]), None)
        if manifest_name is None:
            # Try nested single top-level dir
            top_levels = {n.split("/")[0] for n in names if n}
            if len(top_levels) == 1:
                top = next(iter(top_levels))
                candidate = f"{top}/manifest.json"
                if candidate in names:
                    manifest_name = candidate
        if manifest_name is None:
            raise ValueError("zip must contain manifest.json at root or in a single top-level directory")
        try:
            manifest_raw = json.loads(zf.read(manifest_name).decode("utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid manifest.json in zip: {error}") from error
        skill_id = str(manifest_raw.get("id", "")).strip()
        if not validate_skill_id(skill_id):
            raise ValueError(f"invalid skill id in manifest: {skill_id!r}")
        target_dir = target_root / skill_id
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True)
        # Determine the prefix to strip when unpacking
        prefix = ""
        if manifest_name != "manifest.json":
            prefix = manifest_name.rsplit("/", 1)[0] + "/"
        total_bytes = 0
        for member in zf.namelist():
            if member.endswith("/"):
                continue
            if member in BLOCKED_FILE_NAMES:
                continue
            rel = member[len(prefix):] if prefix else member
            if not rel or not is_safe_relative_path(rel, target_dir):
                continue
            dest = target_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(member)
            total_bytes += len(data)
            if total_bytes > MAX_SKILL_UNPACKED_BYTES:
                raise ValueError("unpacked skill exceeds size limit")
            dest.write_bytes(data)
    return build_package_info(target_dir, source="zip")


# ---------------------------------------------------------------------------
# Import: Git
# ---------------------------------------------------------------------------


async def import_skill_from_git(
    git_url: str,
    *,
    branch: str = "main",
    subdirectory: str = "",
    skills_dir: Path | None = None,
) -> SkillPackageInfo:
    """Clone a git repo (shallow) and copy a subdirectory into skills_dir."""
    import asyncio

    target_root = (skills_dir or DEFAULT_SKILLS_DIR).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="amadeus-skill-git-") as tmp:
        tmp_path = Path(tmp)
        cmd = ["git", "clone", "--depth", "1", "--branch", branch, git_url, str(tmp_path)]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"git clone failed: {(stderr or b'').decode('utf-8', errors='replace').strip()}"
            )
        src_dir = tmp_path / subdirectory if subdirectory else tmp_path
        if not src_dir.is_dir():
            raise ValueError(f"subdirectory not found in repo: {subdirectory}")
        manifest_path = src_dir / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("git repo must contain manifest.json in the target subdirectory")
        try:
            manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid manifest.json: {error}") from error
        skill_id = str(manifest_raw.get("id", "")).strip()
        if not validate_skill_id(skill_id):
            raise ValueError(f"invalid skill id in manifest: {skill_id!r}")
        target_dir = target_root / skill_id
        if target_dir.exists():
            shutil.rmtree(target_dir)
        # Copy only safe files (no .git, no hidden config)
        target_dir.mkdir(parents=True)
        total_bytes = 0
        for entry in src_dir.rglob("*"):
            if not entry.is_file():
                continue
            rel = entry.relative_to(src_dir)
            parts = rel.parts
            if any(p.startswith(".git") for p in parts):
                continue
            if entry.name in BLOCKED_FILE_NAMES:
                continue
            if not is_safe_relative_path(rel, target_dir):
                continue
            dest = target_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = entry.read_bytes()
            total_bytes += len(data)
            if total_bytes > MAX_SKILL_UNPACKED_BYTES:
                raise ValueError("unpacked skill exceeds size limit")
            dest.write_bytes(data)
    return build_package_info(target_dir, source="git")


# ---------------------------------------------------------------------------
# Package info / persistence helpers
# ---------------------------------------------------------------------------


def build_package_info(skill_dir: Path, *, source: str = "local") -> SkillPackageInfo:
    manifest = load_manifest(skill_dir)
    return SkillPackageInfo(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        enabled=True,
        source=source,  # type: ignore[arg-type]
        path=str(skill_dir),
        description=manifest.description,
        triggers=manifest.triggers,
        required_mcp_servers=manifest.mcp_servers,
        tool_allowlist=manifest.tool_allowlist,
        roles=manifest.roles,
        budgets=manifest.budgets,
    )


def remove_skill_files(skill_dir: Path) -> None:
    if skill_dir.exists() and skill_dir.is_dir():
        shutil.rmtree(skill_dir, ignore_errors=True)


def match_skills_by_trigger(
    skills: list[SkillPackageInfo],
    text: str,
) -> list[SkillPackageInfo]:
    """Return skills whose triggers appear in ``text`` (case-insensitive)."""
    lowered = text.lower()
    matched: list[SkillPackageInfo] = []
    for skill in skills:
        if not skill.enabled:
            continue
        for trigger in skill.triggers:
            if trigger and trigger.lower() in lowered:
                matched.append(skill)
                break
    return matched
