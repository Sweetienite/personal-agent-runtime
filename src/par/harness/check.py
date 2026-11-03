"""``par harness check``: a read-only health report over the Harness Core and every Harness folder.

What it may run, and nothing else (enforced by ``_allowed``):

* read-only git subcommands with ``--no-optional-locks`` and fsmonitor off;
* the Core's own ``harness_hygiene.py`` and ``sync_runtime_core.py --check`` through ``python -B -I``.

File contents are read only for Harness text assets (``AGENTS.md``, ``CLAUDE.md``, ``.ai/STATE.md`` and
the Core's tracked files). Paths that look sensitive are counted, never listed. The snapshot stores
structure only - versions, hashes, sizes, counts - never section titles or project text: the Runtime
ledger must not become a second copy of project knowledge. The only thing this module can write is a
baseline file, and only inside the Runtime data directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from par.config import HarnessSettings
from par.core.policy import WriteGuard, is_sensitive_path, protected_roots
from par.core.redact import redact_text

SCHEMA_VERSION = 1
BEGIN = "<!-- AI_HARNESS_CORE_BEGIN -->"
END = "<!-- AI_HARNESS_CORE_END -->"
PRUNE = {"node_modules", "venv", "__pycache__", "site-packages", "Library"}
VOLATILE_KEYS = {"taken_at", "discovery.dirs_visited"}
STATUS_LINE_CAP = 100_000  # a pathological repo must not make the check unbounded
FAIL, WARN, INFO = "FAIL", "WARN", "INFO"
_ORDER = {FAIL: 0, WARN: 1, INFO: 2}
_GIT_READONLY = {"rev-parse", "log", "status", "rev-list", "config", "ls-files"}
_CORE_SCRIPTS = {"harness_hygiene.py", "sync_runtime_core.py"}
_GIT_PREFIX = ["-c", "core.fsmonitor=false", "-c", "core.quotePath=false", "--no-optional-locks", "-C"]


@dataclass(frozen=True)
class Finding:
    level: str
    subject: str
    message: str


# --- the only two ways this module starts a process --------------------------------------------------
def _allowed(argv: "list[str]") -> bool:
    git = shutil.which("git")
    if git and argv[0] == git and argv[1:7] == _GIT_PREFIX and len(argv) > 8:
        sub = argv[8]
        return sub in _GIT_READONLY and (sub != "config" or argv[9:10] == ["--get"])
    return (len(argv) > 3 and argv[0] == sys.executable and argv[1:3] == ["-B", "-I"]
            and Path(argv[3]).name in _CORE_SCRIPTS)


def _run(argv: "list[str]", home: Path, timeout: int = 180) -> "tuple[int, str, str]":
    if not _allowed(argv):
        raise ValueError("refusing to run a command outside the read-only allowlist")
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}
    env.update(HOME=str(home), GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, "", type(exc).__name__
    return cp.returncode, cp.stdout, cp.stderr


def _git(repo: Path, home: Path, *args: str) -> "tuple[int, str]":
    git = shutil.which("git")
    if not git:
        return 127, ""
    rc, out, _ = _run([git, *_GIT_PREFIX, str(repo), *args], home)
    return rc, out


git_read = _git  # public name for other modules: one allowlisted read-only git subcommand


# --- facts ------------------------------------------------------------------------------------------
def tilde(path: "str | os.PathLike[str]", home: Path) -> str:
    return redact_text(str(path), home=str(home))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


sha256_file = _sha256  # public name for other modules


def git_facts(repo: Path, home: Path) -> dict:
    if not (repo / ".git").exists():
        return {"is_repo": False}
    _, head = _git(repo, home, "rev-parse", "HEAD")
    _, branch = _git(repo, home, "rev-parse", "--abbrev-ref", "HEAD")
    # "all", not "normal": normal mode collapses an untracked folder into one "?? folder/" line and
    # would hide a sensitive path inside it.
    rc, status = _git(repo, home, "status", "--porcelain=v1", "--untracked-files=all")
    entries = status.splitlines()
    truncated = len(entries) > STATUS_LINE_CAP
    modified = staged = untracked = sensitive = 0
    for line in entries[:STATUS_LINE_CAP]:
        xy, path = line[:2], line[3:]
        sensitive += is_sensitive_path(path)  # counted, the path is never kept
        if xy == "??":
            untracked += 1
        else:
            staged += xy[0] not in " ?"
            modified += xy[1] not in " ?"
    ahead = behind = None
    rc_ab, ab = _git(repo, home, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if rc_ab == 0 and len(ab.split()) >= 2:
        ahead, behind = (int(x) for x in ab.split()[:2])
    _, hooks = _git(repo, home, "config", "--get", "core.hooksPath")
    return {"is_repo": True, "head": head.strip()[:12], "branch": branch.strip(), "status_ok": rc == 0,
            "status_truncated": truncated, "modified": modified, "staged": staged, "untracked": untracked,
            "sensitive_paths_in_status": sensitive, "ahead": ahead, "behind": behind,
            "hooks_path_set": bool(hooks.strip())}


def state_facts(path: Path) -> dict:
    raw = path.read_bytes()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    return {"bytes": len(raw), "lines": len(lines), "longest_line": max((len(x) for x in lines), default=0),
            "sha256": hashlib.sha256(raw).hexdigest()}


def task_dirs(root: Path) -> "dict[str, int]":
    t = root / ".ai" / "tasks"
    if not t.is_dir():
        return {}
    return {c.name: sum(1 for f in c.iterdir() if f.is_file() and f.suffix.lower() == ".md")
            for c in sorted(t.iterdir()) if c.is_dir()}


_SECTIONS_LIST = re.compile(r"(section\(s\) outside the Core list): .*? (\(ignore if)")


def _scrub(level: str, message: str) -> "Optional[list[str]]":
    """Drop project text (section titles, nested paths) from a hygiene line before it is stored."""
    if message.startswith("nested STATE: "):
        return None  # the preceding 'nested STATE.md files present: N' line carries the count
    return [level, _SECTIONS_LIST.sub(r"\1 \2", message)]


def hygiene(core: Path, project: Path, home: Path) -> dict:
    rc, out, _ = _run([sys.executable, "-B", "-I", str(core / "scripts" / "harness_hygiene.py"), str(project), "--strict"], home)
    findings = []
    for line in out.splitlines():
        level, sep, msg = line.partition(": ")
        if sep and level in {"WARN", "REVIEW", "INFO"}:
            item = _scrub(level, msg.strip())
            if item:
                findings.append(item)
    return {"exit": rc, "findings": findings}


def discover(root: Path, core: Path, max_depth: int) -> "tuple[list[Path], int]":
    """Top-level folders that carry ``.ai/STATE.md``; copies nested inside another one are left out."""
    found: "list[Path]" = []
    visited = 0
    base = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root, followlinks=False):
        visited += 1
        here = Path(dirpath)
        depth = len(here.parts) - base
        if (here / ".ai" / "STATE.md").is_file():
            found.append(here)
        dirnames[:] = [d for d in dirnames if depth < max_depth and d not in PRUNE and not d.startswith(".")
                       and not os.path.islink(os.path.join(dirpath, d))]
    found.sort()
    core_real = os.path.realpath(core)
    tops = [p for p in found if os.path.realpath(p) != core_real
            and not any(q != p and q in p.parents for q in found)]
    return tops, visited


def canonical_block(core: Path) -> "Optional[str]":
    try:
        t = (core / "templates" / "runtime" / "AGENTS.md").read_text(encoding="utf-8")
    except OSError:
        return None
    b, e = t.find(BEGIN), t.find(END)
    return t[b:e + len(END)] if 0 <= b < e else None


def inspect_project(project: Path, canonical: "Optional[str]", core: "Optional[Path]", home: Path) -> dict:
    d: dict = {}
    agents = project / "AGENTS.md"
    if agents.is_file():
        text = agents.read_text(encoding="utf-8", errors="replace")
        nb, ne = text.count(BEGIN), text.count(END)
        match = None
        if nb == 1 and ne == 1 and text.find(BEGIN) < text.find(END) and canonical:
            match = text[text.find(BEGIN):text.find(END) + len(END)] == canonical
        d["agents"] = {"bytes": len(text.encode("utf-8")), "lines": len(text.splitlines()),
                       "sha256": _sha256(agents), "markers": [nb, ne], "block_matches_core": match}
    claude = project / "CLAUDE.md"
    d["claude_thin_bridge"] = (claude.read_text(encoding="utf-8", errors="replace").strip() == "@AGENTS.md") if claude.is_file() else None
    d["state"] = state_facts(project / ".ai" / "STATE.md")
    d["task_dirs"] = task_dirs(project)
    d["git"] = git_facts(project, home)
    d["hygiene"] = hygiene(core, project, home) if core else None
    return d


def inspect_core(core: Path, home: Path) -> dict:
    out: dict = {"path": tilde(core, home), "present": core.is_dir()}
    if not out["present"]:
        return out
    def first(rx: str, text: str) -> "Optional[str]":
        m = re.search(rx, text, re.M)
        return m.group(1) if m else None
    try:
        manifest = json.loads((core / "CORE_MANIFEST.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        manifest = None
    def read(name: str) -> str:
        try:
            return (core / name).read_text(encoding="utf-8")
        except OSError:
            return ""
    out["versions"] = {"manifest": manifest, "readme": first(r"\*\*Version:\*\*\s*([0-9][0-9.]*)", read("README.md")),
                       "changelog": first(r"^##\s*\[?v?([0-9]+\.[0-9]+\.[0-9]+)", read("CHANGELOG.md"))}
    out["git"] = git_facts(core, home)
    _, files = _git(core, home, "ls-files", "-z")
    out["tracked"] = {f: _sha256(core / f) for f in files.split("\0") if f and (core / f).is_file()}
    return out


def run_check(settings: HarnessSettings, home: "str | os.PathLike[str] | None" = None) -> dict:
    """Build the structural snapshot. Reads only; see the module docstring for what may run."""
    home_p = Path(home) if home is not None else Path.home()
    core = settings.core_path
    core_ok = core.is_dir() and (core / "scripts" / "harness_hygiene.py").is_file()
    snap: dict = {"schema_version": SCHEMA_VERSION, "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "core": inspect_core(core, home_p)}
    if core_ok and (core / "scripts" / "sync_runtime_core.py").is_file():
        rc, out, _ = _run([sys.executable, "-B", "-I", str(core / "scripts" / "sync_runtime_core.py"), "--check"], home_p)
        snap["sync"] = {"ran": True, "exit": rc, "ok": sum(1 for l in out.splitlines() if l.startswith("OK:"))}
    else:
        snap["sync"] = {"ran": False}
    roots, visited = discover(settings.search_root, core, settings.max_depth) if settings.search_root.is_dir() else ([], 0)
    snap["discovery"] = {"root": tilde(settings.search_root, home_p), "max_depth": settings.max_depth,
                         "dirs_visited": visited, "projects": [tilde(p, home_p) for p in roots]}
    canon = canonical_block(core) if core_ok else None
    snap["projects"] = {tilde(p, home_p): inspect_project(p, canon, core if core_ok else None, home_p) for p in roots}
    return snap


# --- judgement --------------------------------------------------------------------------------------
def evaluate(snap: dict) -> "list[Finding]":
    f: "list[Finding]" = []
    core = snap["core"]
    if not core.get("present"):
        f.append(Finding(INFO, "core", f"Harness Core not found at {core['path']}; structural checks skipped"))
    else:
        versions = core.get("versions", {})
        if len({v for v in versions.values()}) != 1 or None in versions.values():
            f.append(Finding(FAIL, "core", "version strings disagree: " + ", ".join(f"{k}={v}" for k, v in versions.items())))
        g = core.get("git") or {}
        dirty = (g.get("modified") or 0) + (g.get("staged") or 0) + (g.get("untracked") or 0)
        if g.get("is_repo") and dirty:
            f.append(Finding(WARN, "core", f"working tree has uncommitted changes ({g['modified']} modified, {g['staged']} staged, {g['untracked']} untracked)"))
        sync = snap.get("sync", {})
        if sync.get("ran") and sync.get("exit") != 0:
            f.append(Finding(FAIL, "core", f"sync_runtime_core --check exited {sync['exit']}: managed block drift or error"))
    for path, p in snap["projects"].items():
        agents = p.get("agents") or {}
        markers = agents.get("markers")
        if markers and markers != [0, 0]:
            if markers != [1, 1]:
                f.append(Finding(FAIL, path, f"managed block markers are malformed (begin/end = {markers[0]}/{markers[1]})"))
            elif agents.get("block_matches_core") is False:
                f.append(Finding(FAIL, path, "managed block differs from the Core template"))
        g = p.get("git") or {}
        if g.get("is_repo"):
            if g.get("sensitive_paths_in_status"):
                f.append(Finding(WARN, path, f"{g['sensitive_paths_in_status']} sensitive-looking path(s) are visible to git (add ignore rules before any `git add -A`)"))
            dirty = [f"{g[k]} {k}" for k in ("modified", "staged", "untracked") if g.get(k)]
            if dirty:
                f.append(Finding(INFO, path, "uncommitted: " + ", ".join(dirty)))
            if g.get("ahead"):
                f.append(Finding(INFO, path, f"{g['ahead']} commit(s) ahead of upstream"))
            if g.get("behind"):
                f.append(Finding(WARN, path, f"{g['behind']} commit(s) behind upstream"))
        hy = p.get("hygiene")
        if hy is None:
            f.append(Finding(INFO, path, "structural check skipped (Harness Core scripts not available)"))
        else:
            for level, msg in hy["findings"]:
                f.append(Finding(INFO if level == "INFO" else WARN, path, ("review: " if level == "REVIEW" else "") + msg))
    return sorted(f, key=lambda x: (_ORDER[x.level], x.subject, x.message))


def _flatten(obj: Any, prefix: str = "") -> "Iterable[tuple[str, Any]]":
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        yield prefix, json.dumps(obj, ensure_ascii=False)
    else:
        yield prefix, obj


def compare(baseline: dict, current: dict) -> "list[str]":
    a, b = dict(_flatten(baseline)), dict(_flatten(current))
    out = []
    for k in sorted(set(a) | set(b)):
        if k in VOLATILE_KEYS:
            continue
        if k not in a:
            out.append(f"ADDED   {k} = {b[k]}")
        elif k not in b:
            out.append(f"REMOVED {k} (was {a[k]})")
        elif a[k] != b[k]:
            out.append(f"CHANGED {k}: {a[k]} -> {b[k]}")
    return out


# --- baseline file (the only thing that is ever written) -----------------------------------------------
def baseline_path(data_dir: "str | os.PathLike[str]") -> Path:
    return Path(data_dir) / "harness" / "baseline.json"


def load_baseline(data_dir: "str | os.PathLike[str]") -> "tuple[Optional[dict], Optional[str]]":
    p = baseline_path(data_dir)
    if not p.is_file():
        return None, None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
            return None, "baseline has an unsupported schema_version"
        return doc, None
    except (OSError, ValueError):
        return None, "baseline file is unreadable"


def save_baseline(data_dir: "str | os.PathLike[str]", snap: dict) -> Path:
    """Atomically replace the baseline (the previous one is kept as baseline.prev.json), mode 0600."""
    data_dir = Path(data_dir)
    guard = WriteGuard(allowed_roots=(data_dir,), protected=protected_roots())
    target = guard.check(baseline_path(data_dir))
    folder = target.parent
    if not folder.is_dir():
        os.mkdir(folder, 0o700)
        os.chmod(folder, 0o700)
    tmp = folder / "baseline.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1, sort_keys=True)
    if target.exists():
        os.replace(target, folder / "baseline.prev.json")
    os.replace(tmp, target)
    return target


# --- rendering --------------------------------------------------------------------------------------
def render_text(snap: dict, findings: "list[Finding]", diffs: "Optional[list[str]]", baseline_note: str,
                home: Path) -> str:
    core = snap["core"]
    lines = ["par harness check - read-only (read-only git, plus the Core's own check scripts; nothing is written)"]
    if core.get("present"):
        g, v = core.get("git") or {}, core.get("versions", {})
        sync = snap.get("sync", {})
        lines.append(f"Core      {core['path']}  v{v.get('manifest')}  git {g.get('branch')}@{g.get('head')}  "
                     f"sync: " + (f"exit {sync['exit']} ({sync['ok']} OK)" if sync.get("ran") else "not run"))
    else:
        lines.append(f"Core      {core['path']}  NOT FOUND")
    d = snap["discovery"]
    lines.append(f"Projects  {len(snap['projects'])} found under {d['root']} (depth <= {d['max_depth']}, {d['dirs_visited']} folders scanned)")
    for path, p in snap["projects"].items():
        s, g = p["state"], p.get("git") or {}
        gtxt = f"git {g.get('branch')}@{g.get('head')}" + (f" +{g['ahead']}/-{g['behind']}" if g.get("ahead") is not None else "") if g.get("is_repo") else "no git"
        tasks = " ".join(f"{k}={n}" for k, n in p["task_dirs"].items())
        lines.append(f"  {path}  STATE {s['bytes']} B/{s['lines']} L  tasks[{tasks}]  {gtxt}")
    lines.append("Findings")
    lines += [f"[{x.level}] {x.subject}: {x.message}" for x in findings] or ["(none)"]
    lines.append(baseline_note)
    if diffs is not None:
        lines += [f"  {x}" for x in diffs[:40]] + ([f"  ... and {len(diffs) - 40} more (use --json)"] if len(diffs) > 40 else [])
    counts = {lv: sum(1 for x in findings if x.level == lv) for lv in (FAIL, WARN, INFO)}
    lines.append(f"Summary: FAIL {counts[FAIL]} | WARN {counts[WARN]} | INFO {counts[INFO]}" + (f" | differences {len(diffs)}" if diffs is not None else ""))
    return redact_text("\n".join(lines), home=str(home))
