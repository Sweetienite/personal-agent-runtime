"""``par doctor``: a read-only environment report.

Guarantees (each one is enforced by a test):

* writes nothing - not even the data directory it reports on;
* executes no external program and opens no network connection;
* every line it prints has passed through redaction, and home directories are shown as ``~``.
"""
from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from par import PHASE, __version__
from par.config import LoadResult, load_config, load_projects_file
from par.core.policy import PolicyViolation, WriteGuard, protected_roots
from par.core.redact import redact_text
from par.paths import inspect_data_dir

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
MIN_PYTHON = (3, 11)
MIN_FREE_BYTES = 5 * 1024 ** 3
CONFIG_FILES = ("profiles", "providers", "projects")


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class Report:
    checks: "tuple[Check, ...]"

    @property
    def summary(self) -> "dict[str, int]":
        return {s: sum(1 for c in self.checks if c.status == s) for s in (PASS, WARN, FAIL, INFO)}

    def ok(self, strict: bool = False) -> bool:
        s = self.summary
        return s[FAIL] == 0 and not (strict and s[WARN])

    def to_dict(self) -> dict:
        return {
            "name": "par", "version": __version__, "phase": PHASE, "summary": self.summary,
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks],
        }


def _fmt_gib(n: int) -> str:
    return f"{n / 1024 ** 3:.0f} GiB"


def _issues(result_issues, limit: int = 4) -> str:
    shown = [f"{i.where}: {i.message}" for i in result_issues[:limit]]
    more = len(result_issues) - limit
    return "; ".join(shown) + (f"; ... and {more} more" if more > 0 else "")


def run_doctor(data_dir: "str | os.PathLike[str]", home: "str | os.PathLike[str] | None" = None) -> Report:
    home_path = Path(home) if home is not None else Path.home()
    data_dir = Path(data_dir)
    checks: "list[Check]" = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append(Check(name, status, redact_text(detail, home=str(home_path))))

    # --- interpreter and platform -------------------------------------------------------------
    py = platform.python_version()
    if sys.version_info[:2] >= MIN_PYTHON:
        add("python", PASS, f"{py} (needs >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})")
    else:
        add("python", FAIL, f"{py} is too old (needs >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})")
    if sys.platform == "darwin":
        add("platform", INFO, platform.platform())
    else:
        add("platform", WARN, f"{platform.platform()}: designed for macOS, paths and permissions are untested here")
    add("package", INFO, f"par {__version__} (phase {PHASE}); doctor itself runs nothing external (the Codex adapter exists from P3, the Antigravity adapter from P4)")

    # --- configuration (loaded first: it tells us where the Core lives) -----------------------
    cfg_dir = data_dir / "config"
    found = {k: cfg_dir / f"{k}.toml" for k in CONFIG_FILES if (cfg_dir / f"{k}.toml").is_file()}
    result: LoadResult = load_config(found.get("profiles"), found.get("providers"))
    registry = load_projects_file(found.get("projects"), home=home_path)
    settings = registry.settings
    all_issues = tuple(result.issues) + tuple(registry.issues)
    errors = [i for i in all_issues if i.severity == "error"]
    warnings = [i for i in all_issues if i.severity == "warning"]

    # --- data directory -------------------------------------------------------------------------
    st = inspect_data_dir(data_dir)
    if not st.exists:
        add("data_dir", WARN, f"{data_dir} is not initialised yet (run `par init`)")
    elif st.problems:
        add("data_dir", FAIL, f"{data_dir} {'; '.join(st.problems)}")
    elif st.missing_subdirs:
        add("data_dir", WARN, f"{data_dir} mode {st.mode:04o}; missing subdirectories: {', '.join(st.missing_subdirs)} (run `par init`)")
    else:
        add("data_dir", PASS, f"{data_dir} mode {st.mode:04o}, owned by you, not a symlink")
    if st.free_bytes is None:
        add("disk_free", INFO, "free space could not be determined")
    elif st.free_bytes < MIN_FREE_BYTES:
        add("disk_free", WARN, f"{_fmt_gib(st.free_bytes)} free (less than {_fmt_gib(MIN_FREE_BYTES)})")
    else:
        add("disk_free", PASS, f"{_fmt_gib(st.free_bytes)} free")
    try:
        WriteGuard(allowed_roots=(data_dir,), protected=protected_roots(home_path, extra=[settings.core_path])).check(data_dir)
        add("data_dir_location", PASS, "outside every protected location (Harness Core, tool configs, shell files)")
    except PolicyViolation as exc:
        add("data_dir_location", FAIL, str(exc))

    # --- config result ----------------------------------------------------------------------------
    if errors:
        add("config", FAIL, _issues(errors))
    elif not found:
        add("config", PASS, "no user config; built-in conservative defaults in use (every external provider disabled)")
    else:
        names = ", ".join(f"{k}.toml" for k in found)
        add("config", WARN if warnings else PASS, f"{names} valid" + (f"; {_issues(warnings, 3)}" if warnings else ""))

    # --- providers: static facts only, nothing is executed ---------------------------------------
    cfg = result.config
    for pid, p in sorted(cfg.providers.items()):
        name = f"provider:{pid}"
        if p.kind == "gemini_legacy":
            add(name, INFO, "disabled_legacy: kept installed, can never be enabled, never a fallback for agy")
        elif not p.enabled:
            add(name, INFO, "disabled")
        else:
            found_exe = False
            exe = ""
            if p.executable:
                x = os.path.expanduser(p.executable)
                found_exe = os.path.isfile(x) and os.access(x, os.X_OK)
                exe = f"; executable {'found' if found_exe else 'NOT found'} at {p.executable}"
            if p.kind == "codex":
                if found_exe:
                    add(name, INFO, f"enabled; the Codex adapter is built in{exe} (only the file is looked at here: `par health --probe` checks signature, login and isolation, offline)")
                elif p.executable:
                    add(name, WARN, f"enabled in config, but the executable is missing{exe}")
                else:
                    add(name, WARN, "enabled in config, but no `executable` is named: give an absolute path (PATH is never searched)")
            elif p.kind == "antigravity":
                if found_exe:
                    add(name, INFO, f"enabled; the Antigravity adapter is built in{exe} (only the file is looked at here: `par health --probe` checks signature, sign-in and model list)")
                elif p.executable:
                    add(name, WARN, f"enabled in config, but the executable is missing{exe}")
                else:
                    add(name, WARN, "enabled in config, but no `executable` is named: give an absolute path (PATH is never searched)")
            else:
                add(name, WARN, f"enabled in config, but this build has no adapter for it yet and runs nothing external for it{exe}")
    if registry.projects:
        gone = sorted(pid for pid, p in registry.projects.items() if not p.path.is_dir())
        fixtures = sorted(pid for pid, p in registry.projects.items() if p.fixture)
        approved = sorted(f"{pid} (approved {p.write_approved})" for pid, p in registry.projects.items() if p.write_enabled and not p.fixture)
        modes = {m: sum(1 for p in registry.projects.values() if p.context_mode == m) for m in ("generic", "harness")}
        add("projects", WARN if gone else INFO,
            f"{len(registry.projects)} registered ({', '.join(sorted(registry.projects))}); "
            f"context: {modes['generic']} generic / {modes['harness']} harness; "
            + (f"writes are enabled for {', '.join(approved)} and disabled for the other real projects" if approved
               else "writes are disabled for all real projects")
            + (f"; fixture project(s) {', '.join(fixtures)} may be written" if fixtures else "")
            + (f"; folder not found: {', '.join(gone)}" if gone else ""))
    else:
        add("projects", INFO, "none registered (`par project add ID PATH`)")
    if cfg.workers:
        todo = sum(1 for w in cfg.workers.values() if not w.model_configured)
        add("workers", INFO, f"{len(cfg.workers)} configured; {todo} still have placeholder model slugs (verified at run time in P3/P4)")
    if cfg.active_profile:
        add("profile", INFO, f"active profile: {cfg.active_profile}")
    return Report(tuple(checks))


def render_text(report: Report) -> str:
    lines = ["par doctor - read-only: writes nothing, runs no external program, uses no network"]
    width = max(len(c.name) for c in report.checks)
    for c in report.checks:
        lines.append(f"[{c.status}] {c.name.ljust(width)}  {c.detail}")
    s = report.summary
    lines.append(f"Summary: PASS {s[PASS]} | WARN {s[WARN]} | FAIL {s[FAIL]} | INFO {s[INFO]}")
    return "\n".join(lines)
