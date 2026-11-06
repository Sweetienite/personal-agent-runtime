"""HarnessContextAdapter: a read-only view of one registered Harness project, and the Context Manifest a
Worker is given instead of the project's whole state.

Rules (construction plan, section 4.4):

* ``AGENTS.md`` is the shared entry. The single active contract and a *healthy* ``STATE.md`` may be
  inlined; everything else is a pointer (path, size, hash).
* Nothing is ever truncated silently: an entry over a limit becomes a pointer, with the reason.
* An oversized ``STATE.md`` is never inlined, and it blocks write trials until it is reconciled.
* Every entry carries its source, size and sha256, so a later reader can tell that the context changed.
* Nothing here writes. Sensitive paths are counted, never listed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path

from par.config import ProjectEntry
from par.context.types import (Blocker, ContextChanged, ContextManifest, FileFacts, ManifestEntry, ProjectView,
                               SourcePointer, StateFacts, WriteReadiness)
from par.core.policy import PolicyViolation, is_sensitive_path
from par.core.redact import contains_secret, redact_text
from par.harness import check as hc

SCHEMA_VERSION = 1
STATE_INLINE_MAX_BYTES = 15_000  # the limit Core's hygiene applies to STATE.md
INLINE_MAX_BYTES = 20_000        # any other single entry
TOTAL_INLINE_MAX_BYTES = 60_000
MAX_POINTERS = 50
PARSE_MAX_BYTES = 5 * 1024 * 1024
BEGIN = "<!-- AI_HARNESS_CORE_BEGIN -->"
END = "<!-- AI_HARNESS_CORE_END -->"


_SECTION = re.compile(r"^##\s+Sources of Truth\s*$", re.M)
_ANY_SECTION = re.compile(r"^##\s+", re.M)
_POINTER = re.compile(r"^\s*[-*]\s*(?P<topic>[^`\n]*?)\s*:\s*`(?P<path>[^`\n]+)`", re.M)


class HarnessContextAdapter:
    def __init__(self, project: ProjectEntry, home: "str | os.PathLike[str] | None" = None, *,
                 inline_max_bytes: int = INLINE_MAX_BYTES, total_inline_max_bytes: int = TOTAL_INLINE_MAX_BYTES,
                 state_inline_max_bytes: int = STATE_INLINE_MAX_BYTES) -> None:
        self.project = project
        self.root = project.path
        self.home = Path(home) if home is not None else Path.home()
        self.inline_max = inline_max_bytes
        self.total_max = total_inline_max_bytes
        self.state_max = state_inline_max_bytes

    # --- facts ------------------------------------------------------------------------------------
    def _facts(self, rel: str) -> "FileFacts | None":
        p = self.root / rel
        if not p.is_file():
            return None
        return FileFacts(rel, p.stat().st_size, hc.sha256_file(p))

    def _state(self) -> "tuple[StateFacts | None, str]":
        p = self.root / ".ai" / "STATE.md"
        if not p.is_file():
            return None, ""
        size = p.stat().st_size
        raw = p.read_bytes() if size <= PARSE_MAX_BYTES else b""
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        sha = hashlib.sha256(raw).hexdigest() if raw else hc.sha256_file(p)
        facts = StateFacts(".ai/STATE.md", size, len(lines), max((len(x) for x in lines), default=0),
                           round(size / 2.5), sha, size > self.state_max)
        return facts, text

    def _contracts(self) -> "tuple[FileFacts, ...]":
        active = self.root / ".ai" / "tasks" / "active"
        if not active.is_dir():
            return ()
        names = sorted(f.name for f in active.iterdir() if f.is_file() and f.suffix.lower() == ".md" and not f.name.startswith("."))
        return tuple(f for f in (self._facts(f".ai/tasks/active/{n}") for n in names) if f)

    def _pointer(self, topic: str, raw: str) -> "SourcePointer | None":
        """None when the pointer must not be listed at all (it looks sensitive)."""
        rel = raw.strip().split("#", 1)[0].strip()
        if is_sensitive_path(rel) or contains_secret(rel):
            return None
        topic = redact_text(topic.strip())[:120]
        if not rel or re.match(r"^[a-z]+://", rel):
            return SourcePointer(topic, rel[:200], "not_a_path")
        if os.path.isabs(rel) or rel.startswith("~") or os.path.normpath(rel).startswith(".."):
            return SourcePointer(topic, rel[:200], "outside_project")
        target = self.root / os.path.normpath(rel)
        real, root_real = os.path.realpath(target), os.path.realpath(self.root)
        if real != root_real and not real.startswith(root_real + os.sep):
            return SourcePointer(topic, rel[:200], "outside_project")
        if os.path.isdir(real):
            return SourcePointer(topic, rel[:200], "directory")
        if not os.path.exists(real):
            return SourcePointer(topic, rel[:200], "missing")
        return SourcePointer(topic, rel[:200], "ok", os.path.getsize(real))

    def _sources(self, state_text: str) -> "tuple[tuple[SourcePointer, ...], int, int]":
        m = _SECTION.search(state_text)
        if not m:
            return (), 0, 0
        rest = state_text[m.end():]
        nxt = _ANY_SECTION.search(rest)
        section = rest[:nxt.start()] if nxt else rest
        listed: "list[SourcePointer]" = []
        sensitive = cut = 0
        for found in _POINTER.finditer(section):
            if len(listed) >= MAX_POINTERS:
                cut += 1
                continue
            sp = self._pointer(found.group("topic"), found.group("path"))
            if sp is None:
                sensitive += 1
            else:
                listed.append(sp)
        return tuple(listed), cut, sensitive

    def _git(self, notes: "list[str]") -> dict:
        declared, has_git = self.project.vcs, (self.root / ".git").exists()
        if declared == "none":
            if has_git:
                notes.append("vcs is declared 'none' but a .git exists; it is ignored")
            return {"is_repo": False}
        if declared == "git" and not has_git:
            notes.append("vcs is declared 'git' but no repository was found at the project root")
        if not has_git:
            return {"is_repo": False}
        facts = hc.git_facts(self.root, self.home)
        _, full = hc.git_read(self.root, self.home, "rev-parse", "HEAD")
        _, status = hc.git_read(self.root, self.home, "status", "--porcelain=v1", "--untracked-files=all")
        lines = sorted(status.splitlines()[:hc.STATUS_LINE_CAP])
        facts["head_full"] = full.strip()
        facts["dirty_digest"] = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
        return facts

    def view(self) -> ProjectView:
        shown = redact_text(str(self.root), home=str(self.home))
        if not self.root.is_dir():
            return ProjectView(self.project.id, shown, False, vcs_declared=self.project.vcs, notes=("the project folder does not exist",))
        notes: "list[str]" = []
        rules = self._facts("AGENTS.md")
        markers = None
        if rules:
            text = (self.root / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
            markers = (text.count(BEGIN), text.count(END))
        claude = self.root / "CLAUDE.md"
        bridge = None
        if claude.is_file():
            bridge = "thin" if claude.read_text(encoding="utf-8", errors="replace").strip() == "@AGENTS.md" else "other"
        state, state_text = self._state()
        sources, cut, sensitive = self._sources(state_text)
        return ProjectView(
            id=self.project.id, root=shown, exists=True, rules=rules, managed_markers=markers, bridge=bridge,
            state=state, contracts=self._contracts(), task_dirs=hc.task_dirs(self.root), sources=sources,
            sources_cut=cut, sensitive_sources=sensitive, git=self._git(notes), vcs_declared=self.project.vcs,
            notes=tuple(notes),
        )

    # --- the manifest -----------------------------------------------------------------------------
    def manifest(self, view: "ProjectView | None" = None) -> ContextManifest:
        v = view or self.view()
        entries: "list[ManifestEntry]" = []
        warnings: "list[str]" = list(v.notes)

        def sized(kind: str, f: FileFacts, limit: int, why_not: str = "") -> ManifestEntry:
            if why_not:
                return ManifestEntry(kind, f.path, "pointer", f.bytes, f.sha256, why_not)
            if f.bytes > limit:
                return ManifestEntry(kind, f.path, "pointer", f.bytes, f.sha256, f"{f.bytes} bytes exceeds the {limit}-byte inline limit")
            return ManifestEntry(kind, f.path, "inline", f.bytes, f.sha256)

        if v.rules:
            entries.append(sized("rules", v.rules, self.inline_max))
        else:
            warnings.append("AGENTS.md is missing")
        if v.bridge == "thin":
            entries.append(ManifestEntry("bridge", "CLAUDE.md", "skip", reason="thin '@AGENTS.md' bridge; nothing to add"))
        elif v.bridge == "other":
            warnings.append("CLAUDE.md is not the thin @AGENTS.md bridge")
            entries.append(ManifestEntry("bridge", "CLAUDE.md", "pointer", reason="not the thin bridge: read it before relying on it"))
        if v.state:
            if v.state.oversized:
                entries.append(ManifestEntry("state", v.state.path, "pointer", v.state.bytes, v.state.sha256,
                                             f"{v.state.bytes} bytes (about {v.state.approx_tokens} tokens) exceeds the {self.state_max}-byte limit: never inlined"))
                warnings.append("STATE.md is oversized: reconciliation is recommended before any write trial")
            else:
                entries.append(ManifestEntry("state", v.state.path, "inline", v.state.bytes, v.state.sha256))
        else:
            warnings.append(".ai/STATE.md is missing")
        if len(v.contracts) == 1:
            entries.append(sized("contract", v.contracts[0], self.inline_max))
        elif len(v.contracts) > 1:
            warnings.append(f"{len(v.contracts)} active contracts; the Core allows at most one")
            entries += [sized("contract", c, 0, "several active contracts: none is trusted as the execution contract") for c in v.contracts]
        for sp in v.sources:
            if sp.status == "ok":
                entries.append(ManifestEntry("source", sp.path, "pointer", sp.bytes, None, "fact source named by STATE.md", sp.topic))
            else:
                entries.append(ManifestEntry("source", sp.path, "skip", None, None, f"pointer is {sp.status}", sp.topic))
        if v.sources_cut:
            warnings.append(f"{v.sources_cut} more source pointer(s) were not listed (limit {MAX_POINTERS})")

        total = sum(e.bytes or 0 for e in entries if e.mode == "inline")
        for i in range(len(entries) - 1, -1, -1):  # demote from the end; never truncate
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
        limits = {"inline_max_bytes": self.inline_max, "total_inline_max_bytes": self.total_max,
                  "state_inline_max_bytes": self.state_max}
        canon = json.dumps({"project": v.id, "base": base, "limits": limits, "entries": [asdict(e) for e in entries]},
                           sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return ContextManifest(SCHEMA_VERSION, v.id, v.root, time.strftime("%Y-%m-%dT%H:%M:%S%z"), base, tuple(entries),
                               limits, tuple(warnings), v.sensitive_sources, hashlib.sha256(canon.encode("utf-8")).hexdigest())

    # --- reading what a manifest promised -----------------------------------------------------------
    def read_inline(self, manifest: ContextManifest) -> "list[tuple[ManifestEntry, str]]":
        """The text of every inline entry, verified against the recorded size and hash."""
        out = []
        root_real = os.path.realpath(self.root)
        for e in manifest.inline_entries():
            target = os.path.realpath(self.root / e.path)
            if is_sensitive_path(e.path) or not (target == root_real or target.startswith(root_real + os.sep)):
                raise PolicyViolation("a manifest entry points outside the project or at a sensitive path")
            data = Path(target).read_bytes()
            if len(data) != e.bytes or hashlib.sha256(data).hexdigest() != e.sha256:
                raise ContextChanged(e.path)
            out.append((e, data.decode("utf-8", errors="replace")))
        return out

    def render(self, manifest: ContextManifest) -> str:
        parts = [f"# Context for project {manifest.project_id} (manifest {manifest.digest[:12]})"]
        b = manifest.base
        parts.append(f"# base: {b['vcs']}" + (f" {b['head'][:12]} on {b['branch']}" if b.get("head") else "")
                     + (f", uncommitted: {sum(b['dirty'].values())}" if sum(b["dirty"].values()) else ""))
        for e, text in self.read_inline(manifest):
            parts.append(f"\n## {e.kind}: {e.path}  (sha256 {e.sha256[:12]}, {e.bytes} bytes)\n{text.rstrip()}")
        pointers = [e for e in manifest.entries if e.mode == "pointer"]
        if pointers:
            parts.append("\n## Pointers (not included; read on demand if your permissions allow)")
            parts += [f"- {e.kind}: {e.path}" + (f" ({e.bytes} bytes)" if e.bytes is not None else "")
                      + (f" - {e.reason}" if e.reason else "") for e in pointers]
        if manifest.skipped_sensitive:
            parts.append(f"\n({manifest.skipped_sensitive} sensitive-looking pointer(s) were left out.)")
        return "\n".join(parts) + "\n"

    # --- may a write task start here? ---------------------------------------------------------------
    def write_readiness(self, view: "ProjectView | None" = None, *, assume_enabled: bool = False) -> WriteReadiness:
        """``assume_enabled`` asks "would writes be allowed if the person switched them on?" (what `par project enable-write` checks)."""
        v = view or self.view()
        b: "list[Blocker]" = []
        if not self.project.write_enabled and not assume_enabled:
            b.append(Blocker("WRITE_DISABLED", "writes to this project are switched off: once the other checks pass you switch them on yourself "
                                               "with `par project enable-write ID` (a real project is written only by your decision)"))
        if not v.exists:
            b.append(Blocker("NO_PROJECT", "the project folder does not exist"))
            return WriteReadiness(False, tuple(b))
        if v.state is None:
            b.append(Blocker("NO_STATE", ".ai/STATE.md is missing"))
        elif v.state.oversized:
            b.append(Blocker("STATE_OVERSIZED", f"STATE.md is {v.state.bytes} bytes (limit {self.state_max}): get a reconciliation approved before any write trial"))
        if not v.contracts:
            b.append(Blocker("NO_ACTIVE_CONTRACT", "a write task needs an approved parent contract or an explicit ad-hoc flow"))
        elif len(v.contracts) > 1:
            b.append(Blocker("MULTIPLE_ACTIVE_CONTRACTS", f"{len(v.contracts)} active contracts; the Core allows at most one"))
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
