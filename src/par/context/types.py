"""The data a context implementation produces: a project view, the manifest built from it, and the
write-readiness verdict. These are intentionally plain frozen dataclasses - the Kernel and the prompt
builder consume them without knowing which context adapter produced them."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


class ContextChanged(Exception):
    """A file named in a manifest no longer matches the size/hash recorded in it."""


@dataclass(frozen=True)
class FileFacts:
    path: str  # relative to the project root
    bytes: int
    sha256: str


@dataclass(frozen=True)
class StateFacts:
    path: str
    bytes: int
    lines: int
    longest_line: int
    approx_tokens: int
    sha256: str
    oversized: bool


@dataclass(frozen=True)
class SourcePointer:
    topic: str
    path: str
    status: str  # ok | missing | directory | outside_project | not_a_path
    bytes: "int | None" = None


@dataclass(frozen=True)
class ProjectView:
    id: str
    root: str
    exists: bool
    rules: "FileFacts | None" = None
    managed_markers: "tuple[int, int] | None" = None
    bridge: "str | None" = None  # thin | other | None (absent)
    state: "StateFacts | None" = None
    contracts: "tuple[FileFacts, ...]" = ()
    task_dirs: "dict[str, int]" = field(default_factory=dict)
    sources: "tuple[SourcePointer, ...]" = ()
    sources_cut: int = 0
    sensitive_sources: int = 0
    git: "dict" = field(default_factory=lambda: {"is_repo": False})
    vcs_declared: str = "auto"
    notes: "tuple[str, ...]" = ()

    @property
    def contract_state(self) -> str:
        return {0: "none", 1: "one"}.get(len(self.contracts), "multiple")

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self), ensure_ascii=False))


@dataclass(frozen=True)
class ManifestEntry:
    kind: str  # rules | bridge | state | contract | source
    path: str
    mode: str  # inline | pointer | skip
    bytes: "int | None" = None
    sha256: "str | None" = None
    reason: str = ""
    topic: str = ""


@dataclass(frozen=True)
class ContextManifest:
    schema_version: int
    project_id: str
    root: str
    generated_at: str
    base: dict
    entries: "tuple[ManifestEntry, ...]"
    limits: dict
    warnings: "tuple[str, ...]"
    skipped_sensitive: int
    digest: str

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self), ensure_ascii=False))

    def inline_entries(self) -> "list[ManifestEntry]":
        return [e for e in self.entries if e.mode == "inline"]

    @property
    def base_git_commit(self) -> "str | None":
        return self.base.get("head")

    @property
    def state_revision(self) -> "str | None":
        return next((e.sha256 for e in self.entries if e.kind == "state"), None)

    @property
    def parent_contract_ref(self) -> "dict | None":
        found = [e for e in self.entries if e.kind == "contract"]
        return {"path": found[0].path, "sha256": found[0].sha256} if len(found) == 1 else None


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str


@dataclass(frozen=True)
class WriteReadiness:
    allowed: bool
    blockers: "tuple[Blocker, ...]"
