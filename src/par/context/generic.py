"""GenericContextAdapter: the context of an ordinary Git repository.

It reads only the files a repository already has - ``README.md``, ``AGENTS.md`` and ``CLAUDE.md`` - plus
the Git baseline. There is no ``.ai/STATE.md``, no parent contract and no write-scaffold here: a generic
project's execution contract is the task's own JSON contract, and its rollback is the Git commit.

The files are untrusted project data: they become context for the Worker, never a source of authority.
When ``AGENTS.md`` and ``CLAUDE.md`` both exist and disagree, the manifest lists both and says so; it
does not silently pick a winner."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from par.config import ProjectEntry
from par.context.facts import file_facts, git_facts, outside, project_file, skipped_file, thin_bridge
from par.context.rendering import read_inline as read_inline_entries, render_manifest
from par.context.types import (Blocker, ContextManifest, FileFacts, ManifestEntry, ProjectView, SCHEMA_VERSION,
                               WriteReadiness)
from par.core.redact import redact_text

INLINE_MAX_BYTES = 20_000
TOTAL_INLINE_MAX_BYTES = 60_000


class GenericContextAdapter:
    def __init__(self, project: ProjectEntry, home: "str | Path | None" = None, *,
                 inline_max_bytes: int = INLINE_MAX_BYTES, total_inline_max_bytes: int = TOTAL_INLINE_MAX_BYTES) -> None:
        self.project = project
        self.root = project.path
        self.home = Path(home) if home is not None else Path.home()
        self.inline_max = inline_max_bytes
        self.total_max = total_inline_max_bytes

    def _facts(self, rel: str) -> "FileFacts | None":
        return file_facts(self.root, rel)

    def view(self) -> ProjectView:
        shown = redact_text(str(self.root), home=str(self.home))
        if not self.root.is_dir():
            return ProjectView(self.project.id, shown, False, vcs_declared=self.project.vcs,
                               notes=("the project folder does not exist",))
        notes: "list[str]" = []
        bridge = None
        raw_claude = self.root / "CLAUDE.md"
        claude = project_file(self.root, "CLAUDE.md")
        if claude is not None:
            bridge = "thin" if thin_bridge(claude) else "other"
        elif raw_claude.is_symlink() or outside(self.root, raw_claude):
            notes.append("CLAUDE.md is a symlink or outside the project root; it was not read")
        return ProjectView(id=self.project.id, root=shown, exists=True, rules=self._facts("AGENTS.md"), bridge=bridge,
                           state=None, contracts=(), git=git_facts(self.project, self.root, self.home, notes),
                           vcs_declared=self.project.vcs, notes=tuple(notes))

    def manifest(self, view: "ProjectView | None" = None) -> ContextManifest:
        v = view or self.view()
        entries: "list[ManifestEntry]" = []
        warnings: "list[str]" = list(v.notes)

        def sized(kind: str, f: FileFacts) -> ManifestEntry:
            if f.bytes > self.inline_max:
                return ManifestEntry(kind, f.path, "pointer", f.bytes, f.sha256,
                                     f"{f.bytes} bytes exceeds the {self.inline_max}-byte inline limit")
            return ManifestEntry(kind, f.path, "inline", f.bytes, f.sha256)

        readme = self._facts("README.md")
        if readme:
            entries.append(sized("readme", readme))
        elif skipped_file(self.root, "README.md"):
            warnings.append("README.md is a symlink, outside the project root, or unreadable; it was not read")
        if v.rules:
            entries.append(sized("rules", v.rules))
        elif skipped_file(self.root, "AGENTS.md"):
            warnings.append("AGENTS.md is a symlink, outside the project root, or unreadable; it was not read")
        else:
            warnings.append("AGENTS.md is missing (generic context: README.md, when present, and the task contract are the context)")
        if v.bridge == "thin":
            entries.append(ManifestEntry("bridge", "CLAUDE.md", "skip", reason="thin '@AGENTS.md' bridge; nothing to add"))
        elif v.bridge == "other":
            if v.rules:
                warnings.append("AGENTS.md and CLAUDE.md both exist; this manifest lists both and does not decide which one wins")
            else:
                warnings.append("CLAUDE.md exists without AGENTS.md; it is listed as a pointer, not treated as an authority")
            entries.append(ManifestEntry("bridge", "CLAUDE.md", "pointer", reason="not the thin bridge: read it before relying on it"))

        total = sum(e.bytes or 0 for e in entries if e.mode == "inline")
        for i in range(len(entries) - 1, -1, -1):
            if total <= self.total_max:
                break
            e = entries[i]
            if e.mode == "inline":
                total -= e.bytes or 0
                entries[i] = ManifestEntry(e.kind, e.path, "pointer", e.bytes, e.sha256,
                                           f"total inline budget of {self.total_max} bytes exceeded", e.topic)

        g = v.git
        base = {"vcs": "git" if g.get("is_repo") else "none", "head": g.get("head_full") or None,
                "branch": g.get("branch") or None,
                "dirty": {k: g.get(k, 0) for k in ("modified", "staged", "untracked")},
                "dirty_digest": g.get("dirty_digest")}
        limits = {"inline_max_bytes": self.inline_max, "total_inline_max_bytes": self.total_max}
        canon = json.dumps({"project": v.id, "base": base, "limits": limits, "entries": [asdict(e) for e in entries]},
                           sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return ContextManifest(SCHEMA_VERSION, v.id, v.root, time.strftime("%Y-%m-%dT%H:%M:%S%z"), base, tuple(entries),
                               limits, tuple(warnings), 0, hashlib.sha256(canon.encode("utf-8")).hexdigest())

    def read_inline(self, manifest: ContextManifest) -> "list[tuple[ManifestEntry, str]]":
        return read_inline_entries(self.root, manifest)

    def render(self, manifest: ContextManifest) -> str:
        return render_manifest(self.root, manifest)

    def write_readiness(self, view: "ProjectView | None" = None, *, assume_enabled: bool = False) -> WriteReadiness:
        v = view or self.view()
        b: "list[Blocker]" = []
        if not self.project.write_enabled and not assume_enabled:
            b.append(Blocker("WRITE_DISABLED", "writes to this project are switched off: once the other checks pass you switch them on yourself "
                                               "with `par project enable-write ID` (a real project is written only by your decision)"))
        if not v.exists:
            b.append(Blocker("NO_PROJECT", "the project folder does not exist"))
            return WriteReadiness(False, tuple(b))
        g = v.git
        if not g.get("is_repo"):
            b.append(Blocker("NO_VCS", "no version-control rollback here: a write task would need an explicit backup or snapshot flow"))
        else:
            if g.get("sensitive_paths_in_status"):
                b.append(Blocker("SENSITIVE_VISIBLE", f"{g['sensitive_paths_in_status']} sensitive-looking path(s) are visible to git: add ignore rules first"))
            dirty = sum(g.get(k, 0) for k in ("modified", "staged", "untracked"))
            if dirty:
                b.append(Blocker("DIRTY_BASE", f"{dirty} uncommitted change(s): a write task needs a clean base or an explicit dirty manifest"))
        if self.project.vcs == "git" and not g.get("is_repo"):
            b.append(Blocker("VCS_MISMATCH", "the registry says 'git' but no repository was found"))
        return WriteReadiness(not b, tuple(b))
