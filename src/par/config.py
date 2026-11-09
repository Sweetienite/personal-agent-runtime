"""Runtime configuration: schema, deterministic validation and conservative built-in defaults.

The validator turns the user's fixed decisions into mechanical rules, so a config file cannot quietly
undo them: the legacy Gemini CLI can never be enabled, no extra paid credits, at most two workers at a time (one by default),
no secrets in config, no PATH lookups. Nothing here executes anything or contacts any service.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from par.core.policy import inside_protected, protected_roots
from par.core.redact import contains_secret, is_sensitive_key

SUPPORTED_SCHEMA = 1
PROVIDER_KINDS = frozenset({"codex", "antigravity", "claude_host", "manual_handoff", "gemini_legacy"})
WORKER_PROVIDER_KINDS = frozenset({"codex", "antigravity", "manual_handoff"})
DEFAULT_PROVIDER_IDS = ("codex", "antigravity", "claude_host", "manual_handoff", "gemini_legacy")
CAPABILITIES = frozenset({"read", "edit", "review"})
# How a role is reached. The role name is not tied to a vendor: a worker id names a provider/model pair, and the
# surface says which mechanism reaches it. "acp" is accepted in descriptors but has no adapter yet.
SURFACES = frozenset({"native_cli", "acp", "manual", "programmatic_session"})
PROVIDER_SURFACE = {"codex": "native_cli", "antigravity": "native_cli", "manual_handoff": "manual",
                    "claude_host": "programmatic_session", "gemini_legacy": "native_cli"}
FAILURE_POLICIES = frozenset({"ask", "pause", "safe_auto"})
# Who may decide a task's approvals and acceptance. ``coordinator_autonomous`` keeps the 2026-10-20
# Maintainer decision (the bridge may decide, always recorded); ``coordinator_scoped`` requires a
# ledger grant with a matching scope; ``human_only`` refuses every coordinator decision.
APPROVAL_MODES = frozenset({"coordinator_autonomous", "coordinator_scoped", "human_only"})
DEFAULT_APPROVAL_MODE = "coordinator_autonomous"

from par.coordinators import known_names as _known_coordinator_names
KNOWN_COORDINATORS = _known_coordinator_names()        # claude_desktop, codex (and the older name codex_manual_takeover): see par/coordinators.py
MAX_PARALLEL_WORKERS_ALLOWED = 2  # the most attempts that may run at once (a profile asks for 1 or 2; 1 is the default)
DEFAULT_PARALLEL_WORKERS = 1
WRITE_APPROVED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ID = re.compile(r"[a-z][a-z0-9_]*\Z")


@dataclass(frozen=True)
class ConfigIssue:
    severity: str  # "error" | "warning"
    where: str
    message: str


@dataclass(frozen=True)
class ProviderEntry:
    id: str
    kind: str
    enabled: bool = False
    executable: "str | None" = None

    @property
    def surface(self) -> str:
        return PROVIDER_SURFACE.get(self.kind, "native_cli")


@dataclass(frozen=True)
class WorkerEntry:
    id: str
    provider: str
    model: str
    capabilities: "tuple[str, ...]" = ("read",)
    surface: str = "native_cli"      # native_cli | manual (derived from the provider kind; never a vendor name)

    @property
    def model_configured(self) -> bool:
        return not is_placeholder(self.model)


@dataclass(frozen=True)
class RoleEntry:
    primary: str
    fallback: "tuple[str, ...]" = ()


@dataclass(frozen=True)
class ProfileEntry:
    id: str
    coordinator: str
    fallback_coordinator: "str | None"
    default_worker: str
    max_parallel_workers: int
    worker_failure_policy: str
    additional_paid_credits: bool
    roles: "dict[str, RoleEntry]" = field(default_factory=dict)
    approval_mode: str = DEFAULT_APPROVAL_MODE


@dataclass(frozen=True)
class RuntimeConfig:
    schema_version: int = SUPPORTED_SCHEMA
    active_profile: "str | None" = None
    profiles: "dict[str, ProfileEntry]" = field(default_factory=dict)
    workers: "dict[str, WorkerEntry]" = field(default_factory=dict)
    providers: "dict[str, ProviderEntry]" = field(default_factory=dict)


@dataclass(frozen=True)
class LoadResult:
    config: RuntimeConfig
    issues: "tuple[ConfigIssue, ...]"
    sources: "tuple[str, ...]" = ()

    @property
    def errors(self) -> "tuple[ConfigIssue, ...]":
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> "tuple[ConfigIssue, ...]":
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors


def is_placeholder(text: str) -> bool:
    t = text.strip()
    return t.startswith("<") and t.endswith(">")


def builtin_defaults() -> RuntimeConfig:
    """Everything external is disabled. This is what runs when no config file exists."""
    return RuntimeConfig(providers={p: ProviderEntry(p, p, False, None) for p in DEFAULT_PROVIDER_IDS})


class _Issues:
    def __init__(self) -> None:
        self.items: "list[ConfigIssue]" = []

    def err(self, where: str, message: str) -> None:
        self.items.append(ConfigIssue("error", where, message))

    def warn(self, where: str, message: str) -> None:
        self.items.append(ConfigIssue("warning", where, message))


def _read_toml(path: "str | os.PathLike[str]", c: _Issues) -> "dict | None":
    label = Path(path).name
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        c.err(label, "file not found")
    except tomllib.TOMLDecodeError as exc:
        c.err(label, f"invalid TOML: {exc}")
    except UnicodeDecodeError:
        c.err(label, "file is not valid UTF-8")
    except OSError as exc:
        c.err(label, f"cannot read file: {exc.strerror or exc}")
    return None


def _scan_secrets(node: Any, where: str, c: _Issues) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            w = f"{where}.{k}" if where else str(k)
            if is_sensitive_key(k):
                c.err(w, "config must not contain secrets: this key name invites one (log in through the vendor CLI instead)")
            _scan_secrets(v, w, c)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scan_secrets(v, f"{where}[{i}]", c)
    elif isinstance(node, str) and contains_secret(node):
        c.err(where, "value looks like a secret or a personal identifier and must not be stored in config")


def _unknown_keys(table: dict, allowed: Iterable[str], where: str, c: _Issues) -> None:
    for k in table:
        if k not in allowed:
            c.warn(f"{where}.{k}" if where else str(k), "unknown key (typo?) - ignored")


def _check_schema(raw: dict, label: str, c: _Issues) -> None:
    v = raw.get("schema_version")
    if isinstance(v, bool) or v != SUPPORTED_SCHEMA:
        c.err(f"{label}.schema_version", f"must be {SUPPORTED_SCHEMA} (found {v!r})")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_providers(raw: "dict | None", c: _Issues) -> "dict[str, ProviderEntry]":
    providers = dict(builtin_defaults().providers)
    if raw is None:
        return providers
    _unknown_keys(raw, {"schema_version", "providers"}, "", c)
    _check_schema(raw, "providers", c)
    table = raw.get("providers", {})
    if not isinstance(table, dict):
        c.err("providers", "must be a table of providers")
        return providers
    for pid, entry in table.items():
        where = f"providers.{pid}"
        if not _ID.match(str(pid)):
            c.err(where, "id must match [a-z][a-z0-9_]*")
            continue
        if not isinstance(entry, dict):
            c.err(where, "must be a table")
            continue
        _unknown_keys(entry, {"kind", "enabled", "executable"}, where, c)
        kind = entry.get("kind", pid if pid in PROVIDER_KINDS else None)
        if kind not in PROVIDER_KINDS:
            c.err(f"{where}.kind", f"unknown provider kind {kind!r}; expected one of {sorted(PROVIDER_KINDS)}")
            continue
        enabled = entry.get("enabled", False)
        if not isinstance(enabled, bool):
            c.err(f"{where}.enabled", "must be true or false")
            enabled = False
        if kind == "gemini_legacy" and enabled:
            c.err(f"{where}.enabled", "the legacy Gemini CLI is a disabled legacy provider and can never be enabled")
            enabled = False
        executable = entry.get("executable") or None
        if executable is not None:
            if not isinstance(executable, str) or "\0" in executable:
                c.err(f"{where}.executable", "must be a path string")
                executable = None
            elif not os.path.isabs(os.path.expanduser(executable)):
                c.err(f"{where}.executable", "must be an absolute path; PATH lookups are not allowed")
                executable = None
        providers[pid] = ProviderEntry(pid, kind, enabled, executable)
    return providers


def _parse_workers(raw: Any, providers: "dict[str, ProviderEntry]", c: _Issues) -> "dict[str, WorkerEntry]":
    workers: "dict[str, WorkerEntry]" = {}
    if raw is None:
        return workers
    if not isinstance(raw, dict):
        c.err("workers", "must be a table of workers")
        return workers
    for wid, w in raw.items():
        where = f"workers.{wid}"
        if not _ID.match(str(wid)):
            c.err(where, "id must match [a-z][a-z0-9_]*")
            continue
        if not isinstance(w, dict):
            c.err(where, "must be a table")
            continue
        _unknown_keys(w, {"provider", "model", "capabilities"}, where, c)
        prov = w.get("provider")
        if not isinstance(prov, str) or prov not in providers:
            c.err(f"{where}.provider", f"unknown provider {prov!r}")
            continue
        kind = providers[prov].kind
        if kind == "gemini_legacy":
            c.err(f"{where}.provider", "the legacy Gemini CLI can never be a worker (the Google worker is antigravity)")
            continue
        if kind not in WORKER_PROVIDER_KINDS:
            c.err(f"{where}.provider", f"a {kind!r} provider cannot run as a worker")
            continue
        model = w.get("model")
        if not isinstance(model, str) or not model.strip():
            c.err(f"{where}.model", "must be a non-empty model slug")
            continue
        if is_placeholder(model):
            c.warn(f"{where}.model", "model slug is still a placeholder; it is verified at run time before use")
        caps = w.get("capabilities", ["read"])
        if not isinstance(caps, list) or not all(isinstance(x, str) for x in caps):
            c.err(f"{where}.capabilities", "must be a list of strings")
            continue
        bad = sorted(set(caps) - CAPABILITIES)
        if bad:
            c.err(f"{where}.capabilities", f"unknown capability {bad}; allowed: {sorted(CAPABILITIES)}")
            continue
        workers[wid] = WorkerEntry(wid, prov, model, tuple(caps), providers[prov].surface)
    return workers


def _parse_roles(raw: Any, workers: "dict[str, WorkerEntry]", where: str, c: _Issues) -> "dict[str, RoleEntry]":
    roles: "dict[str, RoleEntry]" = {}
    if raw is None:
        return roles
    if not isinstance(raw, dict):
        c.err(where, "must be a table of roles")
        return roles
    for rname, r in raw.items():
        rw = f"{where}.{rname}"
        if not _ID.match(str(rname)):
            c.err(rw, "role name must match [a-z][a-z0-9_]*")
            continue
        if not isinstance(r, dict):
            c.err(rw, "must be a table")
            continue
        _unknown_keys(r, {"primary", "fallback"}, rw, c)
        primary = r.get("primary")
        if not isinstance(primary, str) or primary not in workers:
            c.err(f"{rw}.primary", f"unknown worker {primary!r}")
            continue
        fb = r.get("fallback", [])
        if not isinstance(fb, list) or not all(isinstance(x, str) for x in fb):
            c.err(f"{rw}.fallback", "must be a list of worker ids")
            continue
        ok = True
        for x in fb:
            if x not in workers:
                c.err(f"{rw}.fallback", f"unknown worker {x!r}")
                ok = False
        if len(set(fb)) != len(fb):
            c.err(f"{rw}.fallback", "contains duplicates")
            ok = False
        if primary in fb:
            c.err(f"{rw}.fallback", "must not contain the primary worker")
            ok = False
        if ok:
            roles[rname] = RoleEntry(primary, tuple(fb))
    return roles


def _parse_profiles(raw: Any, workers: "dict[str, WorkerEntry]", c: _Issues) -> "dict[str, ProfileEntry]":
    profiles: "dict[str, ProfileEntry]" = {}
    if raw is None:
        return profiles
    if not isinstance(raw, dict):
        c.err("profiles", "must be a table of profiles")
        return profiles
    for pid, p in raw.items():
        where = f"profiles.{pid}"
        if not _ID.match(str(pid)):
            c.err(where, "id must match [a-z][a-z0-9_]*")
            continue
        if not isinstance(p, dict):
            c.err(where, "must be a table")
            continue
        _unknown_keys(p, {"coordinator", "fallback_coordinator", "default_worker", "max_parallel_workers",
                          "worker_failure_policy", "additional_paid_credits", "roles", "approval_mode"}, where, c)
        bad = False
        coord = p.get("coordinator")
        if not isinstance(coord, str) or not coord.strip():
            c.err(f"{where}.coordinator", "must be a non-empty string")
            bad = True
        elif coord not in KNOWN_COORDINATORS:
            c.warn(f"{where}.coordinator", f"unrecognised coordinator {coord!r}; known: {sorted(KNOWN_COORDINATORS)}")
        fb_coord = p.get("fallback_coordinator")
        if fb_coord is not None and not isinstance(fb_coord, str):
            c.err(f"{where}.fallback_coordinator", "must be a string")
            bad = True
        dw = p.get("default_worker")
        if not isinstance(dw, str) or dw not in workers:
            c.err(f"{where}.default_worker", f"unknown worker {dw!r}")
            bad = True
        mpw = p.get("max_parallel_workers", DEFAULT_PARALLEL_WORKERS)
        if not _is_int(mpw) or not 1 <= mpw <= MAX_PARALLEL_WORKERS_ALLOWED:
            c.err(f"{where}.max_parallel_workers",
                  f"must be a whole number from 1 to {MAX_PARALLEL_WORKERS_ALLOWED} (found {mpw!r}); the same project is never worked on twice at once")
            bad = True
        policy = p.get("worker_failure_policy", "ask")
        if policy not in FAILURE_POLICIES:
            c.err(f"{where}.worker_failure_policy", f"must be one of {sorted(FAILURE_POLICIES)} (found {policy!r})")
            bad = True
        paid = p.get("additional_paid_credits", False)
        if paid is not False:
            c.err(f"{where}.additional_paid_credits", "must be false: V1 never buys credits or switches to metered billing")
            bad = True
        approval_mode = p.get("approval_mode", DEFAULT_APPROVAL_MODE)
        if approval_mode not in APPROVAL_MODES:
            c.err(f"{where}.approval_mode", f"must be one of {sorted(APPROVAL_MODES)} (found {approval_mode!r})")
            bad = True
        roles = _parse_roles(p.get("roles"), workers, f"{where}.roles", c)
        if not bad:
            profiles[pid] = ProfileEntry(pid, coord, fb_coord, dw, mpw, policy, False, roles, approval_mode)
    return profiles


def build_config(profiles_raw: "dict | None" = None, providers_raw: "dict | None" = None) -> LoadResult:
    """Validate already-parsed TOML tables. Missing tables mean 'use the conservative defaults'."""
    c = _Issues()
    for raw in (profiles_raw, providers_raw):
        if raw is not None:
            _scan_secrets(raw, "", c)  # paths are relative to the file root: "providers.codex.api_key"
    providers = _parse_providers(providers_raw, c)
    workers: "dict[str, WorkerEntry]" = {}
    profiles: "dict[str, ProfileEntry]" = {}
    active = None
    if profiles_raw is not None:
        _unknown_keys(profiles_raw, {"schema_version", "active_profile", "profiles", "workers"}, "", c)
        _check_schema(profiles_raw, "profiles", c)
        workers = _parse_workers(profiles_raw.get("workers"), providers, c)
        profiles = _parse_profiles(profiles_raw.get("profiles"), workers, c)
        active = profiles_raw.get("active_profile")
        if profiles or active is not None:
            if not isinstance(active, str) or active not in profiles:
                c.err("profiles.active_profile", f"must name a valid profile (found {active!r})")
                active = None
    cfg = RuntimeConfig(SUPPORTED_SCHEMA, active, profiles, workers, providers)
    return LoadResult(cfg, tuple(c.items))


def load_config(profiles_path: "str | os.PathLike[str] | None" = None,
                providers_path: "str | os.PathLike[str] | None" = None) -> LoadResult:
    """Load and validate config files. With no paths this returns the built-in defaults."""
    c = _Issues()
    sources = []
    raws: "dict[str, dict | None]" = {"profiles": None, "providers": None}
    for key, path in (("profiles", profiles_path), ("providers", providers_path)):
        if path is not None:
            raws[key] = _read_toml(path, c)
            sources.append(str(path))
    if any(i.severity == "error" for i in c.items):
        return LoadResult(builtin_defaults(), tuple(c.items), tuple(sources))
    result = build_config(raws["profiles"], raws["providers"])
    return LoadResult(result.config, tuple(c.items) + result.issues, tuple(sources))


# --- Harness discovery settings (projects.toml) -------------------------------------------------------
@dataclass(frozen=True)
class HarnessSettings:
    core_path: Path
    search_root: Path
    max_depth: int = 4


def default_harness_settings(home: "str | os.PathLike[str] | None" = None) -> HarnessSettings:
    h = Path(home) if home is not None else Path.home()
    return HarnessSettings(h / "Documents" / "AI_Harness_Core", h / "Documents", 4)


ID_PATTERN = _ID  # public alias: project ids follow the same rule as every other id
PROJECT_VCS = frozenset({"auto", "git", "none"})
PROJECT_CONTEXT_MODES = frozenset({"harness", "generic"})


FIXTURE_MARKER = ".par-fixture.json"
FIXTURES_DIR = "fixtures"


@dataclass(frozen=True)
class ProjectEntry:
    """A registered project. Registration records where it is, never what is in it."""

    id: str
    path: Path
    vcs: str = "auto"            # auto | git | none ("none": the project's own rules forbid a repository at its root)
    write_enabled: bool = False  # a real project is written only after `par project enable-write` (readiness checked, the person typed its id)
    fixture: bool = False        # a throw-away project the Runtime made itself (`par fixture create`): always writable
    write_approved: str = ""     # YYYY-MM-DD: the day the person switched writes on for a real project (empty for fixtures and read-only projects)
    context_mode: str = "harness"  # harness | generic; entries written before this field existed keep the Harness behaviour


def fixture_problem(root: Path, project_id: str, fixtures_root: "Path | None") -> "str | None":
    """Why ``root`` is not a genuine fixture project, or None. A fixture is a direct child of <data dir>/fixtures/ whose
    folder name is its id and which carries the marker ``par fixture create`` writes. Nothing else is ever writable."""
    if fixtures_root is None:
        return "fixture projects are only recognised inside the Runtime's own data directory"
    try:
        if os.path.islink(root) or not root.is_dir():
            return "the fixture folder does not exist or is a symlink"
        real, base = Path(os.path.realpath(root)), Path(os.path.realpath(fixtures_root))
        if real.parent != base or real.name != project_id:
            return "must be <data dir>/fixtures/<id>, a direct child named like the project id"
        marker = root / FIXTURE_MARKER
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
            return f"the {FIXTURE_MARKER} marker is missing"
        doc = json.loads(marker.read_text(encoding="utf-8"))
        if not (isinstance(doc, dict) and doc.get("kind") == "par-fixture" and doc.get("schema_version") == 1 and doc.get("id") == project_id):
            return f"the {FIXTURE_MARKER} marker is not a valid fixture marker for {project_id!r}"
    except (OSError, ValueError):
        return f"the {FIXTURE_MARKER} marker cannot be read"
    return None


@dataclass(frozen=True)
class ProjectsFile:
    settings: HarnessSettings
    projects: "dict[str, ProjectEntry]"
    issues: "tuple[ConfigIssue, ...]"


def _parse_harness_table(raw: dict, default: HarnessSettings, c: _Issues) -> HarnessSettings:
    table = raw.get("harness", {})
    if not isinstance(table, dict):
        c.err("projects.harness", "must be a table")
        return default
    _unknown_keys(table, {"core_path", "search_root", "max_depth"}, "projects.harness", c)
    values = {"core_path": default.core_path, "search_root": default.search_root, "max_depth": default.max_depth}
    for key in ("core_path", "search_root"):
        if key in table:
            v = table[key]
            expanded = os.path.expanduser(v) if isinstance(v, str) else ""
            if not expanded or not os.path.isabs(expanded):
                c.err(f"projects.harness.{key}", "must be an absolute path (a leading ~ is allowed)")
            else:
                values[key] = Path(expanded)
    if "max_depth" in table:
        d = table["max_depth"]
        if not _is_int(d) or not 1 <= d <= 8:
            c.err("projects.harness.max_depth", "must be an integer between 1 and 8")
        else:
            values["max_depth"] = d
    return HarnessSettings(values["core_path"], values["search_root"], values["max_depth"])


def build_projects(raw: dict, c: _Issues, home: "str | os.PathLike[str] | None" = None,
                   fixtures_root: "Path | None" = None) -> "dict[str, ProjectEntry]":
    """Validate the ``[projects.<id>]`` tables. An invalid entry is dropped, never half-registered."""
    table = raw.get("projects", {})
    projects: "dict[str, ProjectEntry]" = {}
    if not isinstance(table, dict):
        c.err("projects", "must be a table of projects")
        return projects
    protected = protected_roots(home)
    seen: "dict[str, str]" = {}
    for pid, p in table.items():
        where = f"projects.{pid}"
        if not _ID.match(str(pid)):
            c.err(where, "id must match [a-z][a-z0-9_]*")
            continue
        if not isinstance(p, dict):
            c.err(where, "must be a table")
            continue
        before = len(c.items)
        _scan_secrets(p, where, c)  # an entry that invites a secret is dropped whole
        if any(i.severity == "error" for i in c.items[before:]):
            continue
        _unknown_keys(p, {"path", "vcs", "write_enabled", "fixture", "write_approved", "context_mode"}, where, c)
        raw_path = p.get("path")
        expanded = os.path.expanduser(raw_path) if isinstance(raw_path, str) else ""
        if not expanded or not os.path.isabs(expanded):
            c.err(f"{where}.path", "must be an absolute path (a leading ~ is allowed)")
            continue
        root = Path(os.path.normpath(expanded))
        if inside_protected(root, protected) is not None:
            c.err(f"{where}.path", "is inside a protected location: the Harness Core, tool configs and shell files are never projects")
            continue
        vcs = p.get("vcs", "auto")
        if not isinstance(vcs, str) or vcs not in PROJECT_VCS:
            c.err(f"{where}.vcs", f"must be one of {sorted(PROJECT_VCS)}")
            continue
        mode = p.get("context_mode", "harness")
        if not isinstance(mode, str) or mode not in PROJECT_CONTEXT_MODES:
            c.err(f"{where}.context_mode", f"must be one of {sorted(PROJECT_CONTEXT_MODES)}")
            continue
        fixture, write = p.get("fixture", False), p.get("write_enabled", False)
        if not isinstance(fixture, bool) or not isinstance(write, bool):
            c.err(f"{where}.fixture" if not isinstance(fixture, bool) else f"{where}.write_enabled", "must be true or false")
            continue
        approved = p.get("write_approved", "")
        if fixture:
            why = fixture_problem(root, str(pid), fixtures_root)
            if why:
                c.err(f"{where}.fixture", why)
                continue
            if approved:
                c.err(f"{where}.write_approved", "is for a real project; a fixture project is always writable")
                continue
        elif write:
            if not isinstance(approved, str) or not WRITE_APPROVED.fullmatch(approved):
                c.err(f"{where}.write_approved", 'a real project may be written only with write_approved = "YYYY-MM-DD" next to write_enabled = true '
                                                 "(the day you approved it): switch writes on with `par project enable-write ID`")
                continue
            if vcs != "git":
                c.err(f"{where}.vcs", 'a real project may be written only with vcs = "git": the git baseline is the rollback')
                continue
        elif approved:
            c.err(f"{where}.write_approved", "is set while write_enabled is false: remove it, or switch writes on with `par project enable-write ID`")
            continue
        real = os.path.realpath(root)
        if real in seen:
            c.err(f"{where}.path", f"is already registered as {seen[real]!r}")
            continue
        seen[real] = pid
        projects[pid] = ProjectEntry(pid, root, vcs, write, fixture, approved if isinstance(approved, str) else "", mode)
    return projects


def fixtures_root_of(projects_toml: "str | os.PathLike[str]") -> "Path | None":
    """<data dir>/fixtures for a projects.toml that lives in <data dir>/config/; None for a file anywhere else."""
    p = Path(projects_toml)
    return p.parent.parent / FIXTURES_DIR if p.parent.name == "config" else None


def load_projects_file(path: "str | os.PathLike[str] | None" = None,
                       home: "str | os.PathLike[str] | None" = None) -> ProjectsFile:
    """Read projects.toml: the ``[harness]`` discovery settings and the ``[projects.<id>]`` registry."""
    default = default_harness_settings(home)
    if path is None:
        return ProjectsFile(default, {}, ())
    c = _Issues()
    raw = _read_toml(path, c)
    if raw is None:
        return ProjectsFile(default, {}, tuple(c.items))
    _scan_secrets({k: v for k, v in raw.items() if k != "projects"}, "", c)  # each project entry is scanned on its own
    _unknown_keys(raw, {"schema_version", "harness", "projects"}, "", c)
    _check_schema(raw, "projects", c)
    settings = _parse_harness_table(raw, default, c)
    projects = build_projects(raw, c, home, fixtures_root_of(path))
    return ProjectsFile(settings, projects, tuple(c.items))


def load_harness_settings(path: "str | os.PathLike[str] | None" = None,
                          home: "str | os.PathLike[str] | None" = None) -> "tuple[HarnessSettings, tuple[ConfigIssue, ...]]":
    """The ``[harness]`` table of projects.toml; absent file or keys fall back to the defaults."""
    pf = load_projects_file(path, home)
    return pf.settings, pf.issues


def parse_projects_text(text: str, home: "str | os.PathLike[str] | None" = None,
                        fixtures_root: "Path | None" = None) -> "tuple[dict[str, ProjectEntry], tuple[ConfigIssue, ...]]":
    """Validate the registry exactly as the loader would, but from text: used before anything is written."""
    c = _Issues()
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        c.err("projects.toml", f"invalid TOML: {exc}")
        return {}, tuple(c.items)
    _scan_secrets({k: v for k, v in raw.items() if k != "projects"}, "", c)
    return build_projects(raw, c, home, fixtures_root), tuple(c.items)
