"""Project registry: the ``[projects.<id>]`` tables of ``<data dir>/config/projects.toml``.

Registering a project is read-only with respect to the project itself: it records only an id, a path,
the vcs setting, the context mode and ``write_enabled = false``. A generic project (the default) needs no
Harness files; a Harness project must carry ``AGENTS.md`` and ``.ai/STATE.md``. The only file written is
the Runtime's own projects.toml, and only after the whole resulting file has passed the same validation
the loader applies.

``add_project(..., scaffold=True)`` is the one exception, and only for the Harness mode: for a project
that is missing one or both of those two files, it creates a minimal starter for exactly the missing ones
(never overwriting one that already exists) before registering.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from par.config import (FIXTURES_DIR, ID_PATTERN, PROJECT_CONTEXT_MODES, PROJECT_VCS, ProjectEntry, ProjectsFile,
                        load_projects_file, parse_projects_text)
from par.core.policy import PolicyViolation, WriteGuard, protected_roots
from par.paths import inspect_data_dir

HEADER = "schema_version = 1\n"

HARNESS_FILES = ("AGENTS.md", os.path.join(".ai", "STATE.md"))

_AGENTS_MD_SCAFFOLD = (
    "# Agent Entry\n\n"
    "- Project truth comes from this repository's own files, not chat memory or an assistant's notes.\n"
    "- For non-trivial work, read `.ai/STATE.md` first.\n"
    "- If `.ai/tasks/active/` holds a task file, treat it as the current contract: once work on it has\n"
    "  started, do not edit its Goal/Scope/Acceptance to make the work easier.\n"
    "- Keep `.ai/STATE.md` accurate as work finishes; propose updates rather than rewriting it unasked.\n"
)
_STATE_MD_SCAFFOLD = (
    "# Current State\n\n"
    "**Last updated:** {date}\n\n"
    "## Baseline\n\n"
    "Just registered with the Runtime; nothing recorded yet.\n\n"
    "## Current Focus\n\n"
    "Nothing in progress.\n\n"
    "## Next\n\n"
    "Nothing queued.\n"
)


def missing_harness_files(path: "str | os.PathLike[str]") -> "tuple[str, ...]":
    """Which of ``HARNESS_FILES`` the folder at ``path`` lacks. Read-only; never touches anything."""
    root = Path(os.path.normpath(os.path.expanduser(os.fspath(path))))
    return tuple(need for need in HARNESS_FILES if not (root / need).is_file())


def _scaffold_text(need: str) -> str:
    if need == "AGENTS.md":
        return _AGENTS_MD_SCAFFOLD
    return _STATE_MD_SCAFFOLD.format(date=time.strftime("%Y-%m-%d"))


def projects_file(data_dir: "str | os.PathLike[str]") -> Path:
    return Path(data_dir) / "config" / "projects.toml"


def load_registry(data_dir: "str | os.PathLike[str]", home: "str | os.PathLike[str] | None" = None) -> ProjectsFile:
    p = projects_file(data_dir)
    return load_projects_file(p if p.is_file() else None, home)


def _display(root: Path, home: Path) -> str:
    s, h = str(root), str(home)
    return "~" + s[len(h):] if s == h or s.startswith(h + os.sep) else s


def add_project(data_dir: "str | os.PathLike[str]", project_id: str, path: "str | os.PathLike[str]",
                vcs: str = "auto", home: "str | os.PathLike[str] | None" = None, *, fixture: bool = False,
                scaffold: bool = False, context_mode: str = "harness") -> ProjectEntry:
    """Register a project. Raises ``PolicyViolation`` with a plain reason when it must not.

    ``fixture=True`` (only ``par fixture create`` passes it) registers a throw-away fixture project as writable; the
    loader independently re-checks that it really is one, so a hand-edited entry gains nothing.

    ``context_mode="generic"`` registers an ordinary repository: no ``AGENTS.md`` / ``.ai/STATE.md`` is required and
    nothing is written into the project. ``"harness"`` (the default for callers that predate the field) keeps the old
    requirement.

    ``scaffold=True`` creates a minimal starter for whichever of ``HARNESS_FILES`` is missing, before the check
    below - never overwriting one that is already there - so a project that never had this Runtime's Harness
    convention can adopt it in one step instead of being refused with no way forward. It implies the Harness mode."""
    home_p = Path(home) if home is not None else Path.home()
    data_dir = Path(data_dir)
    if not ID_PATTERN.match(project_id):
        raise PolicyViolation("project id must match [a-z][a-z0-9_]*")
    if vcs not in PROJECT_VCS:
        raise PolicyViolation(f"vcs must be one of {sorted(PROJECT_VCS)}")
    if context_mode not in PROJECT_CONTEXT_MODES:
        raise PolicyViolation(f"context_mode must be one of {sorted(PROJECT_CONTEXT_MODES)}")
    if scaffold and context_mode != "harness":
        raise PolicyViolation("--scaffold-harness writes the Harness files, so the project must use context_mode 'harness'")
    root = Path(os.path.normpath(os.path.expanduser(os.fspath(path))))
    if not root.is_absolute():
        raise PolicyViolation("the project path must be absolute (a leading ~ is allowed)")
    if not root.is_dir():
        raise PolicyViolation("the project folder does not exist")
    missing = [need for need in HARNESS_FILES if not (root / need).is_file()] if context_mode == "harness" else []
    if missing and scaffold:
        guard = WriteGuard(allowed_roots=(root,), protected=protected_roots(home_p))
        targets = [(need, guard.check(root / need)) for need in missing]  # validate all before writing any
        for need, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                continue  # created by someone else between the check above and now: leave it alone
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_scaffold_text(need))
        missing = [need for need in HARNESS_FILES if not (root / need).is_file()]
    if missing:
        hint = "" if scaffold else " (pass scaffold=True / `par project add --scaffold-harness` to create a minimal starter)"
        raise PolicyViolation(f"not a Harness project: {missing[0]} is missing{hint}")
    if not inspect_data_dir(data_dir).usable:
        raise PolicyViolation("the data directory is missing or unsafe; run `par init` first")
    target = projects_file(data_dir)
    WriteGuard(allowed_roots=(data_dir,), protected=protected_roots(home_p)).check(target)

    existing = target.read_text(encoding="utf-8") if target.is_file() else HEADER
    fixtures_root = data_dir / FIXTURES_DIR
    known, _ = parse_projects_text(existing, home_p, fixtures_root)
    if project_id in known:
        raise PolicyViolation(f"project id {project_id!r} is already registered")
    block = (f"\n[projects.{project_id}]\n"
             f"path = {json.dumps(_display(root, home_p), ensure_ascii=False)}\n"
             f'vcs = "{vcs}"\n'
             + ("fixture = true\nwrite_enabled = true\n" if fixture else "write_enabled = false\n")
             + (f'context_mode = "{context_mode}"\n' if context_mode != "harness" else ""))
    new_text = existing.rstrip("\n") + "\n" + block
    projects, issues = parse_projects_text(new_text, home_p, fixtures_root)
    errors = [i for i in issues if i.severity == "error"]
    if errors or project_id not in projects:
        raise PolicyViolation("; ".join(f"{i.where}: {i.message}" for i in errors) or "the project was not accepted")

    tmp = target.with_name("projects.toml.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(tmp, target)
    return projects[project_id]


# --- switching writes on or off for a REAL project ----------------------------------------------------------------------------
_TABLE_LINE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*(#.*)?$")
_SIMPLE_BOOL = re.compile(r"^(?P<head>\s*write_enabled\s*=\s*)(true|false)(?P<tail>\s*(#.*)?)$")


def _table_span(lines: "list[str]", project_id: str) -> "tuple[int, int]":
    """(first body line, end) of the one ``[projects.<id>]`` table; refuses anything that is not exactly one plain table."""
    heads = [i for i, ln in enumerate(lines) if (m := _TABLE_LINE.match(ln.strip())) and m.group("name").strip() == f"projects.{project_id}"]
    if len(heads) != 1:
        raise PolicyViolation(f"projects.toml does not have exactly one [projects.{project_id}] table: edit it by hand")
    end = next((i for i in range(heads[0] + 1, len(lines)) if _TABLE_LINE.match(lines[i].strip())), len(lines))
    return heads[0] + 1, end


def set_project_write(data_dir: "str | os.PathLike[str]", project_id: str, enabled: bool, *, day: "str | None" = None,
                      home: "str | os.PathLike[str] | None" = None) -> "tuple[ProjectEntry, Path]":
    """Switch writes on (recording ``day`` as the approval date) or off for one real project. Only that project's table changes (its
    ``write_enabled`` line, and ``write_approved`` next to it); the whole resulting file must pass the loader with every other entry
    unchanged; the old file is kept beside it as projects.toml.par-backup-<time>. Returns (the new entry, the backup)."""
    home_p = Path(home) if home is not None else Path.home()
    data_dir = Path(data_dir)
    if not inspect_data_dir(data_dir).usable:
        raise PolicyViolation("the data directory is missing or unsafe; run `par init` first")
    target = projects_file(data_dir)
    WriteGuard(allowed_roots=(data_dir,), protected=protected_roots(home_p)).check(target)
    if not target.is_file():
        raise PolicyViolation("there is no projects.toml yet: register the project first")
    text = target.read_text(encoding="utf-8")
    fixtures_root = data_dir / FIXTURES_DIR
    before, _ = parse_projects_text(text, home_p, fixtures_root)
    entry = before.get(project_id)
    if entry is None:
        raise PolicyViolation(f"no registered project {project_id!r}")
    if entry.fixture:
        raise PolicyViolation(f"{project_id} is a fixture project: it is always writable and nothing needs switching")
    if enabled == entry.write_enabled:
        raise PolicyViolation(f"writes to {project_id} are already {'on' if enabled else 'off'}")
    approved = day or time.strftime("%Y-%m-%d")
    lines = text.splitlines(keepends=True)
    start, end = _table_span(lines, project_id)
    body = [ln for ln in lines[start:end] if not re.match(r"^\s*write_approved\s*=", ln)]
    flag_at = [i for i, ln in enumerate(body) if re.match(r"^\s*write_enabled\s*=", ln)]
    if len(flag_at) > 1:
        raise PolicyViolation(f"[projects.{project_id}] has more than one write_enabled line: edit projects.toml by hand")
    word = "true" if enabled else "false"
    if flag_at:
        m = _SIMPLE_BOOL.match(body[flag_at[0]].rstrip("\n"))
        if not m:
            raise PolicyViolation(f"the write_enabled line of [projects.{project_id}] is not in the plain form `write_enabled = true|false`: edit it by hand")
        body[flag_at[0]] = m.group("head") + word + m.group("tail") + "\n"
        at = flag_at[0] + 1
    else:
        while body and not body[-1].strip():
            body.pop()
        body.append(f"write_enabled = {word}\n")
        at = len(body)
    if enabled:
        body.insert(at, f'write_approved = "{approved}"\n')
    if body and not body[-1].endswith("\n"):
        body[-1] += "\n"
    new_text = "".join(lines[:start] + body + lines[end:])
    after, issues = parse_projects_text(new_text, home_p, fixtures_root)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise PolicyViolation("; ".join(f"{i.where}: {i.message}" for i in errors))
    changed = after.get(project_id)
    if changed is None or changed.write_enabled != enabled or any(after.get(k) != v for k, v in before.items() if k != project_id) \
            or set(after) != set(before):
        raise PolicyViolation("the edit would change more than this project's write switch; nothing was written")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    for n in range(100):                                    # two switches within one second must not overwrite each other's backup
        backup = target.with_name(f"projects.toml.par-backup-{stamp}" + (f"-{n}" if n else ""))
        try:
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            continue
    else:
        raise PolicyViolation("too many backups of projects.toml for this second: try again")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    tmp = target.with_name("projects.toml.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.replace(tmp, target)
    return changed, backup


def log_write_decision(data_dir: "str | os.PathLike[str]", project_id: str, enabled: bool, *, head: str, by: str = "cli") -> None:
    """One line in <data dir>/logs/project_writes.jsonl for every switch: when, which project, on or off, at which commit. No other content."""
    folder = Path(data_dir) / "logs"
    folder.mkdir(mode=0o700, exist_ok=True)
    line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "project": project_id, "writes": "on" if enabled else "off",
                       "head": head, "by": by}, sort_keys=True)
    fd = os.open(folder / "project_writes.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line + "\n")
