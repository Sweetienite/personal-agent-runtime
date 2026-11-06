"""Read-only project facts every context adapter needs: one file's size/hash, and the Git baseline.

Nothing here writes, and nothing follows a path out of the project root; the callers decide which files
to ask about. A symlink or a path whose real location is outside the project is never read, not even
to hash it: a context file that points elsewhere is exactly how a repository could try to make the
Runtime read a credential."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from par.config import ProjectEntry
from par.context.types import FileFacts
from par.core.policy import is_sensitive_path
from par.harness import check as hc


def project_file(root: Path, rel: str) -> "Path | None":
    """The regular file at ``rel`` only when it is strictly inside ``root`` and is not a symlink."""
    p = root / rel
    if p.is_symlink() or not p.is_file() or is_sensitive_path(rel):
        return None
    try:
        real = os.path.realpath(p)
        root_real = os.path.realpath(root)
    except (OSError, ValueError):
        return None
    if real != root_real and not real.startswith(root_real + os.sep):
        return None
    return p


def outside(root: Path, path: Path) -> bool:
    try:
        real, root_real = Path(os.path.realpath(path)), Path(os.path.realpath(root))
    except (OSError, ValueError):
        return True
    return real != root_real and root_real not in real.parents


def thin_bridge(path: Path, *, max_bytes: int = 4_096) -> bool:
    try:
        if path.stat().st_size > max_bytes:
            return False
        return path.read_text(encoding="utf-8", errors="replace").strip() == "@AGENTS.md"
    except OSError:
        return False


def skipped_file(root: Path, rel: str) -> bool:
    """The path exists but will not be read (symlink, sensitive name, escape, or unreadable)."""
    raw = root / rel
    if raw.is_symlink():
        return True
    return raw.exists() and project_file(root, rel) is None


def file_facts(root: Path, rel: str) -> "FileFacts | None":
    p = project_file(root, rel)
    if p is None:
        return None
    try:
        return FileFacts(rel, p.stat().st_size, hc.sha256_file(p))
    except OSError:
        return None


def git_facts(project: ProjectEntry, root: Path, home: Path, notes: "list[str]") -> dict:
    declared, has_git = project.vcs, (root / ".git").exists()
    if declared == "none":
        if has_git:
            notes.append("vcs is declared 'none' but a .git exists; it is ignored")
        return {"is_repo": False}
    if declared == "git" and not has_git:
        notes.append("vcs is declared 'git' but no repository was found at the project root")
    if not has_git:
        return {"is_repo": False}
    facts = hc.git_facts(root, home)
    _, full = hc.git_read(root, home, "rev-parse", "HEAD")
    _, status = hc.git_read(root, home, "status", "--porcelain=v1", "--untracked-files=all")
    lines = sorted(status.splitlines()[:hc.STATUS_LINE_CAP])
    facts["head_full"] = full.strip()
    facts["dirty_digest"] = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return facts
