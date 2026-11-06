"""Read-only project facts every context adapter needs: one file's size/hash, and the Git baseline.

Nothing here writes, and nothing follows a path out of the project root; the callers decide which files
to ask about."""
from __future__ import annotations

import hashlib
from pathlib import Path

from par.config import ProjectEntry
from par.context.types import FileFacts
from par.harness import check as hc


def file_facts(root: Path, rel: str) -> "FileFacts | None":
    p = root / rel
    if not p.is_file():
        return None
    return FileFacts(rel, p.stat().st_size, hc.sha256_file(p))


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
