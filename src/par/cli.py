"""Command line entry point: ``par``."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

from par import PHASE, RELEASE_STATUS, __version__
from par.config import PROJECT_CONTEXT_MODES, PROJECT_VCS, HarnessSettings, load_harness_settings
from par.context.harness import ContextManifest, ProjectView, WriteReadiness
from par.context.resolver import default_context
from par.core.policy import PolicyViolation
from par.core.redact import redact_obj, redact_text
from par.doctor import render_text, run_doctor
from par.fixtures import create_fixture
from par.harness import check as hc
from par.paths import default_data_dir, ensure_data_dir, inspect_data_dir
from par.registry import add_project, load_registry, log_write_decision, missing_harness_files, set_project_write


def _data_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "data_dir", None):
        p = Path(os.path.expanduser(args.data_dir))
        if not p.is_absolute():
            raise PolicyViolation("--data-dir must be an absolute path")
        return p
    return default_data_dir()


def _say_error(message: str) -> None:
    print("error: " + redact_text(message, home=str(Path.home())), file=sys.stderr)


def _cmd_version(args: argparse.Namespace) -> int:
    info = {
        "name": "par",
        "version": __version__,
        "phase": PHASE,
        "release_status": RELEASE_STATUS,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(f"par {info['version']} (phase {info['phase']})")
        print(f"release status: {info['release_status']}")
        print(f"python {info['python']} on {info['platform']}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    try:
        report = run_doctor(_data_dir(args))
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 1
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    else:
        print(render_text(report))
    return 0 if report.ok(strict=args.strict) else 1


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        target = _data_dir(args)
        existed = target.exists()
        status = ensure_data_dir(target)
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 1
    where = redact_text(str(status.path), home=str(Path.home()))
    print(f"data directory ready: {where} (mode {status.mode:04o})" if existed else f"created data directory: {where} (mode {status.mode:04o})")
    print("approval mode: coordinator_autonomous (built-in default) - an MCP coordinator may approve, reject, accept and close "
          "inside the active coordinator lease; set approval_mode = coordinator_scoped or human_only in profiles.toml to keep "
          "decisions with a person (see docs/SECURITY.md)")
    return 0


def _abs_path(value: str, flag: str) -> Path:
    p = Path(os.path.expanduser(value))
    if not p.is_absolute():
        raise PolicyViolation(f"{flag} must be an absolute path")
    return p


def _cmd_harness_check(args: argparse.Namespace) -> int:
    home = Path.home()
    try:
        data = _data_dir(args)
        cfg = data / "config" / "projects.toml"
        settings, issues = load_harness_settings(cfg if cfg.is_file() else None)
        bad = [i for i in issues if i.severity == "error"]
        if bad:
            _say_error("projects.toml: " + "; ".join(f"{i.where}: {i.message}" for i in bad))
            return 2
        depth = args.max_depth if args.max_depth is not None else settings.max_depth
        if not 1 <= depth <= 8:
            raise PolicyViolation("--max-depth must be between 1 and 8")
        settings = HarnessSettings(
            _abs_path(args.core, "--core") if args.core else settings.core_path,
            _abs_path(args.root, "--root") if args.root else settings.search_root,
            depth,
        )
        snap = hc.run_check(settings)
        findings = hc.evaluate(snap)
        baseline, baseline_error = (None, None) if args.no_baseline else hc.load_baseline(data)
        if baseline_error:
            findings.append(hc.Finding(hc.WARN, "baseline", baseline_error))
        diffs = hc.compare(baseline, snap) if baseline else None
        if args.save_baseline:
            if not inspect_data_dir(data).usable:
                raise PolicyViolation("the data directory is missing or unsafe; run `par init` first")
            saved = hc.save_baseline(data, snap)
            note = f"Baseline saved: {redact_text(str(saved), home=str(home))} (previous one kept as baseline.prev.json)"
        elif baseline:
            note = f"Baseline from {baseline.get('taken_at')}: {len(diffs)} difference(s)"
        else:
            note = "Baseline: none yet (use --save-baseline to record this state)"
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 2
    if args.json:
        doc = {"snapshot": snap, "findings": [vars(f) for f in findings], "differences": diffs, "baseline": note}
        print(json.dumps(redact_obj(doc, home=str(home)), ensure_ascii=False))
    else:
        print(hc.render_text(snap, findings, diffs, note, home))
    failed = any(f.level == hc.FAIL for f in findings)
    changed = bool(diffs) and not args.save_baseline
    return 1 if failed or changed else 0


def _registry(args: argparse.Namespace):
    """The registry, or an error message when the file has errors (a half-valid registry is never used)."""
    reg = load_registry(_data_dir(args))
    errors = [i for i in reg.issues if i.severity == "error"]
    if errors:
        raise PolicyViolation("projects.toml: " + "; ".join(f"{i.where}: {i.message}" for i in errors))
    return reg


def _git_line(g: dict) -> str:
    if not g.get("is_repo"):
        return "no git"
    dirty = sum(g.get(k, 0) for k in ("modified", "staged", "untracked"))
    track = f" +{g['ahead']}/-{g['behind']}" if g.get("ahead") is not None else ""
    return f"git {g.get('branch')}@{(g.get('head_full') or g.get('head') or '')[:12]}{track}, " + (f"{dirty} uncommitted" if dirty else "clean")


def _render_project(v: ProjectView, w: WriteReadiness, m: "ContextManifest | None", show_entries: bool,
                   context_mode: str = "harness") -> str:
    L = [f"Project   {v.id}  {v.root}  vcs: {v.vcs_declared}"]
    L.append("Context   " + ("generic (README/AGENTS/CLAUDE + Git facts; no parent contract)" if context_mode == "generic"
                             else "harness (AGENTS.md + .ai/STATE.md + active contract)"))
    if not v.exists:
        L.append("          the folder does not exist")
    if v.rules:
        mk = f"{v.managed_markers[0]}/{v.managed_markers[1]} markers" if v.managed_markers else "?"
        L.append(f"Rules     AGENTS.md  {v.rules.bytes} B  sha256 {v.rules.sha256[:12]}  Core managed block: {mk}")
    if v.bridge:
        L.append(f"Bridge    CLAUDE.md: {'thin @AGENTS.md' if v.bridge == 'thin' else 'NOT the thin bridge'}")
    if v.state:
        s = v.state
        L.append(f"STATE     {s.path}  {s.bytes} B / {s.lines} lines / longest line {s.longest_line}  ~{s.approx_tokens} tokens"
                 + ("  OVERSIZED: pointer only, never inlined" if s.oversized else "  healthy: may be inlined"))
    tasks = " ".join(f"{k}={n}" for k, n in v.task_dirs.items())
    L.append(f"Contract  {v.contract_state} active  |  tasks[{tasks}]")
    by = {}
    for sp in v.sources:
        by[sp.status] = by.get(sp.status, 0) + 1
    if v.sources:
        L.append(f"Sources   {len(v.sources)} listed (" + ", ".join(f"{n} {k}" for k, n in sorted(by.items())) + ")"
                 + (f"; {v.sensitive_sources} sensitive-looking left out" if v.sensitive_sources else "")
                 + (f"; {v.sources_cut} more not listed" if v.sources_cut else ""))
    else:
        L.append("Sources   none listed (no '## Sources of Truth' pointers were read)"
                 + (f"; {v.sensitive_sources} sensitive-looking left out" if v.sensitive_sources else ""))
    L.append(f"Base      {_git_line(v.git)}" + (f"  dirty-digest {v.git['dirty_digest'][:12]}" if v.git.get("dirty_digest") else ""))
    for n in v.notes:
        L.append(f"Note      {n}")
    L.append("Writes    " + ("ALLOWED" if w.allowed else "BLOCKED"))
    if w.allowed and v.git.get("is_repo"):
        L.append("          (a real project is only written after `par project enable-write`; it was switched on by you)")
    L += [f"  - {b.code}: {b.message}" for b in w.blockers]
    if m is not None:
        modes = {k: sum(1 for e in m.entries if e.mode == k) for k in ("inline", "pointer", "skip")}
        inline_bytes = sum(e.bytes or 0 for e in m.entries if e.mode == "inline")
        L.append(f"Manifest  digest {m.digest[:12]}  inline {modes['inline']} ({inline_bytes} B) | pointer {modes['pointer']} | skip {modes['skip']}")
        L += [f"  warning: {x}" for x in m.warnings]
        if show_entries:
            L += [f"  [{e.mode:7}] {e.kind:8} {e.path}" + (f" ({e.bytes} B)" if e.bytes is not None else "")
                  + (f"  - {e.reason}" if e.reason else "") for e in m.entries]
    return "\n".join(L)


def _cmd_project_add(args: argparse.Namespace) -> int:
    created = missing_harness_files(args.path) if args.scaffold_harness else ()
    mode = args.context_mode or ("harness" if args.scaffold_harness else "generic")
    try:
        entry = add_project(_data_dir(args), args.id, args.path, vcs=args.vcs, scaffold=args.scaffold_harness,
                            context_mode=mode)
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 1
    msg = f"registered {entry.id}: {entry.path} (vcs {entry.vcs}; context {entry.context_mode}; writes disabled)"
    if created:
        msg += f"; created minimal {' and '.join(created)}"
    print(redact_text(msg, home=str(Path.home())))
    return 0


def _cmd_project_enable_write(args: argparse.Namespace) -> int:
    try:
        reg = _registry(args)
        entry = reg.projects.get(args.id)
        if entry is None:
            raise PolicyViolation(f"no registered project {args.id!r}; known: {', '.join(sorted(reg.projects)) or 'none'}")
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 2
    home = str(Path.home())
    if entry.fixture:
        print(f"{args.id} is a fixture project: it is always writable, nothing to switch")
        return 0
    if entry.write_enabled:
        print(f"writes to {args.id} are already on (approved {entry.write_approved}); `par project disable-write {args.id}` switches them off")
        return 0
    a = default_context(entry, Path.home())
    v = a.view()
    w = a.write_readiness(v, assume_enabled=True)
    print(redact_text(f"{args.id}  {v.root}  {_git_line(v.git)}", home=home))
    if not w.allowed:
        print("NOT READY - writes stay off until every one of these is fixed (by you, in the project):")
        for b in w.blockers:
            print(redact_text(f"  - {b.code}: {b.message}", home=home))
        return 1
    print("ready: the base is clean, git is the rollback, nothing sensitive is visible to git"
          + ("; STATE.md is healthy and exactly one contract is active" if entry.context_mode == "harness" else ""))
    if args.confirm != args.id:
        print("\nSwitching writes on means: a Worker may be asked to change files in a private COPY of this project (never in the project itself),")
        print("only inside the paths of a task you approve one by one; every result is a patch that nothing applies for you; AGENTS.md, CLAUDE.md and .ai/ stay off limits.")
        print(f"To switch writes on, type the project id again:  par project enable-write {args.id} --confirm {args.id}")
        return 0
    try:
        new, backup = set_project_write(_data_dir(args), args.id, True)
        log_write_decision(_data_dir(args), args.id, True, head=str(v.git.get("head_full") or ""))
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 1
    print(redact_text(f"writes to {new.id} are ON (approved {new.write_approved}); the old file is kept as {backup.name}", home=home))
    print(f"switch them off again at any time: par project disable-write {new.id}")
    return 0


def _cmd_project_disable_write(args: argparse.Namespace) -> int:
    try:
        reg = _registry(args)
        entry = reg.projects.get(args.id)
        if entry is None:
            raise PolicyViolation(f"no registered project {args.id!r}; known: {', '.join(sorted(reg.projects)) or 'none'}")
        if not entry.fixture and not entry.write_enabled:
            print(f"writes to {args.id} are already off")
            return 0
        new, backup = set_project_write(_data_dir(args), args.id, False)
        head = str(default_context(new, Path.home()).view().git.get("head_full") or "")
        log_write_decision(_data_dir(args), args.id, False, head=head)
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 1
    print(redact_text(f"writes to {new.id} are OFF; the old file is kept as {backup.name}", home=str(Path.home())))
    return 0


def _cmd_fixture_create(args: argparse.Namespace) -> int:
    try:
        info = create_fixture(_data_dir(args), args.id)
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 1
    print(redact_text(f"created fixture project {info.entry.id}: {info.entry.path}", home=str(Path.home())))
    print(f"  commit {info.commit[:12]}; a throw-away project with one deliberate bug in app/calc.py, holding no real data")
    print("  fixtures are always writable; a real project stays read-only until you run `par project enable-write ID`")
    print(f"  look at it: par project show {info.entry.id}")
    return 0


def _cmd_project_list(args: argparse.Namespace) -> int:
    try:
        reg = _registry(args)
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 2
    rows = []
    for pid, entry in sorted(reg.projects.items()):
        a = default_context(entry, Path.home())
        v = a.view()
        w = a.write_readiness(v)
        rows.append((entry, v, w))
    if args.json:
        doc = [{"id": v.id, "root": v.root, "exists": v.exists, "state_bytes": v.state.bytes if v.state else None,
                "state_oversized": bool(v.state and v.state.oversized), "contract_state": v.contract_state,
                "context_mode": entry.context_mode,
                "git": {k: v.git.get(k) for k in ("is_repo", "branch", "head_full", "ahead", "behind")},
                "write_allowed": w.allowed, "blockers": [b.code for b in w.blockers]} for entry, v, w in rows]
        print(json.dumps(redact_obj(doc, home=str(Path.home())), ensure_ascii=False))
        return 0
    print(f"Registered projects ({len(rows)}) - real projects are read-only until you run `par project enable-write ID`")
    for entry, v, w in rows:
        st = f"STATE {v.state.bytes} B" + (" OVERSIZED" if v.state.oversized else "") if v.state else "no STATE"
        writes = ("ALLOWED" + (" (fixture)" if entry.fixture else "")) if w.allowed else f"BLOCKED ({len(w.blockers)})"
        print(redact_text(f"  {v.id:<14} {v.root}  {entry.context_mode}  {st}  contract: {v.contract_state}  {_git_line(v.git)}  writes: {writes}", home=str(Path.home())))
    if not rows:
        print("  (none yet: `par project add ID PATH`)")
    return 0


def _cmd_project_show(args: argparse.Namespace) -> int:
    try:
        reg = _registry(args)
        if args.id not in reg.projects:
            raise PolicyViolation(f"no registered project {args.id!r}; known: {', '.join(sorted(reg.projects)) or 'none'}")
    except PolicyViolation as exc:
        _say_error(str(exc))
        return 2
    a = default_context(reg.projects[args.id], Path.home())
    v = a.view()
    w = a.write_readiness(v)
    m = a.manifest(v)
    home = str(Path.home())
    if args.json:
        doc = {"view": v.to_dict(), "write_readiness": {"allowed": w.allowed, "blockers": [vars(b) for b in w.blockers]},
               "manifest": m.to_dict()}
        print(json.dumps(redact_obj(doc, home=home), ensure_ascii=False))
    else:
        entry = reg.projects[args.id]
        text = _render_project(v, w, m, args.manifest, entry.context_mode)
        if entry.context_mode == "generic" and (entry.path / ".ai" / "STATE.md").is_file():
            text += ("\nNote      .ai/STATE.md is present but ignored in generic mode "
                     "(--context-mode harness uses it)")
        print(redact_text(text, home=home))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="par", description=f"Personal Agent Runtime: local multi-model task Kernel (phase {PHASE})."
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("version", help="print the version")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=_cmd_version)

    p = sub.add_parser("doctor", help="read-only environment report (writes nothing, runs nothing external)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--strict", action="store_true", help="exit 1 on warnings as well as failures")
    p.add_argument("--data-dir", metavar="PATH", help="inspect this data directory instead of the default")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("init", help="create the Runtime data directory (mode 0700)")
    p.add_argument("--data-dir", metavar="PATH", help="create this directory instead of the default")
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser("harness", help="Harness health tools")
    actions = p.add_subparsers(dest="harness_command", required=True, metavar="ACTION")
    c = actions.add_parser("check", help="read-only health report over the Harness Core and every Harness folder")
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.add_argument("--data-dir", metavar="PATH", help="data directory holding config and the baseline")
    c.add_argument("--core", metavar="PATH", help="Harness Core location (default: from projects.toml)")
    c.add_argument("--root", metavar="PATH", help="folder to search for Harness projects (default: from projects.toml)")
    c.add_argument("--max-depth", type=int, metavar="N", help="search depth, 1-8 (default: from projects.toml)")
    c.add_argument("--save-baseline", action="store_true", help="after checking, record this state as the new baseline")
    c.add_argument("--no-baseline", action="store_true", help="do not compare with the stored baseline")
    c.set_defaults(func=_cmd_harness_check)

    p = sub.add_parser("project", help="Harness project registry (read-only view of registered projects)")
    actions = p.add_subparsers(dest="project_command", required=True, metavar="ACTION")
    c = actions.add_parser("add", help="register a project (an ordinary Git repository or a Harness project)")
    c.add_argument("id", help="short id: lowercase letters, digits, underscore")
    c.add_argument("path", help="project folder; an ordinary Git repository needs no AGENTS.md or .ai/STATE.md")
    c.add_argument("--vcs", choices=sorted(PROJECT_VCS), default="auto",
                   help="'none' when the project's own rules forbid a repository at its root")
    c.add_argument("--context-mode", choices=sorted(PROJECT_CONTEXT_MODES), default=None,
                   help="generic reads README/AGENTS/CLAUDE and the Git facts (default); harness requires AGENTS.md and "
                        ".ai/STATE.md and applies the parent-contract checks")
    c.add_argument("--scaffold-harness", action="store_true",
                   help="create a minimal, generic AGENTS.md / .ai/STATE.md for whichever is missing, then register "
                        "in the harness context mode (never overwrites one that already exists)")
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    c.set_defaults(func=_cmd_project_add)
    c = actions.add_parser("enable-write", help="switch writes on for a REAL project, after its readiness checks pass and you type its id again")
    c.add_argument("id")
    c.add_argument("--confirm", metavar="ID", help="type the project id again to switch writes on (without it the command only checks and explains)")
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    c.set_defaults(func=_cmd_project_enable_write)
    c = actions.add_parser("disable-write", help="switch writes off for a project again (always allowed)")
    c.add_argument("id")
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    c.set_defaults(func=_cmd_project_disable_write)
    c = actions.add_parser("list", help="registered projects with their state at a glance")
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    c.set_defaults(func=_cmd_project_list)
    c = actions.add_parser("show", help="facts, write readiness and the Context Manifest of one project")
    c.add_argument("id")
    c.add_argument("--manifest", action="store_true", help="also list every manifest entry")
    c.add_argument("--json", action="store_true", help="machine-readable output (full manifest)")
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    c.set_defaults(func=_cmd_project_show)
    p = sub.add_parser("fixture", help="throw-away test projects (always writable; never real data)")
    actions = p.add_subparsers(dest="fixture_command", required=True, metavar="ACTION")
    c = actions.add_parser("create", help="make a small fixture project (git repo, Harness files, one deliberate bug) and register it")
    c.add_argument("id", nargs="?", default="fixture", help="project id (default: fixture)")
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    c.set_defaults(func=_cmd_fixture_create)
    from par import cli_coordinator, cli_mcp, cli_task
    cli_task.register(sub)
    cli_coordinator.add_parser(sub)
    cli_mcp.add_parser(sub)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
