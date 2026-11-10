"""``par task ...`` and ``par health``: the command line face of the Kernel.

Exit codes: 0 done (or a run that produced a result for review); 1 refused, or a run that ended in any other
state; 2 usage problem, unknown task, or a configuration/ledger problem.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import signal
import sqlite3
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from par.config import ProviderEntry, RuntimeConfig, load_config
from par.context.resolver import default_context
from par.core.draft import NEEDS_DECISION, READY, compile_draft
from par.core.contract import MAX_CONTRACT_BYTES
from par.core.kernel import Busy, Kernel, KernelError, NotAllowed, Refused, UnknownTask
from par.core.policy import PolicyViolation
from par.core.redact import redact_obj, redact_text
from par.core.states import NEEDS_YOU
from par.core.store import LedgerError
from par.paths import inspect_data_dir
from par.providers.antigravity import login_plan, remove_keychain_link
from par.providers.manifests import manifest_for
from par.providers.registry import AdapterRegistry, default_registry

OK_AFTER_RUN = frozenset({"REVIEW_REQUIRED", "QUEUED"})


def build_adapters(config: "RuntimeConfig | None" = None) -> AdapterRegistry:
    """The adapters this install can run: every enabled provider whose kind has an adapter manifest and that names its
    executable by absolute path. With no configuration there are none, so nothing external can ever be reached by default.
    The legacy Gemini CLI (`gemini_legacy`) never gets an adapter: it cannot be enabled and is never a fallback."""
    registry = default_registry()
    for entry in (config.providers.values() if config is not None else ()):
        manifest = manifest_for(entry.kind)
        if manifest is not None and not manifest.automatic:
            continue
        if manifest is not None and entry.enabled and entry.executable:
            models = tuple(w.model for w in config.workers.values() if w.provider == entry.id)
            registry.register(entry.id, lambda entry=entry, models=models, manifest=manifest: manifest.adapter_cls.from_entry(
                ProviderEntry(entry.id, entry.kind, entry.enabled, os.path.expanduser(entry.executable)), models=models),
                capabilities=manifest.capabilities, manifest=manifest)
    return registry


def _cli():
    from par import cli                      # imported late: cli imports this module
    return cli


def _error(message: str) -> None:
    _cli()._say_error(message)


def open_kernel(args: argparse.Namespace, *, allow_inspection: bool = False) -> Kernel:
    cli = _cli()
    data = cli._data_dir(args)
    status = inspect_data_dir(data)
    if not status.usable:
        why = "; ".join(status.problems) or "it does not exist"
        raise PolicyViolation(f"the data directory {data} is not ready ({why}); run `par init` first")
    registry = cli._registry(args)
    config_dir = data / "config"
    profiles = config_dir / "profiles.toml"
    providers = config_dir / "providers.toml"
    loaded = load_config(profiles if profiles.is_file() else None, providers if providers.is_file() else None)
    inspection_only = bool(loaded.errors and allow_inspection)
    if loaded.errors and not inspection_only:
        raise PolicyViolation("configuration: " + "; ".join(f"{i.where}: {i.message}" for i in loaded.errors))
    diagnostic = "worker configuration is invalid; inspection-only mode is active; run `par doctor` to review it"
    adapters = default_registry() if inspection_only else build_adapters(loaded.config)
    return Kernel(data, config=loaded.config, projects=registry.projects, adapters=adapters, home=Path.home(),
                  inspection_only=inspection_only,
                  inspection_diagnostic=diagnostic if inspection_only else None)


@contextlib.contextmanager
def session(args: argparse.Namespace, *, allow_inspection: bool = False):
    """Open the Kernel for one command and always close its ledger connection."""
    kernel = open_kernel(args, allow_inspection=allow_inspection)
    try:
        yield kernel
    finally:
        kernel.close()


def handled(fn):
    """Turn Kernel, policy and ledger exceptions into a clean message and the right exit code."""
    @functools.wraps(fn)
    def wrapper(args: argparse.Namespace) -> int:
        try:
            return fn(args)
        except UnknownTask as exc:
            _error(str(exc))
            return 2
        except KernelError as exc:
            _error(f"{exc.code}: " + (exc.problems[0] if len(exc.problems) == 1 else ""))
            for line in exc.problems if len(exc.problems) > 1 else []:
                print("  - " + redact_text(line), file=sys.stderr)
            return 1
        except (PolicyViolation, LedgerError, sqlite3.Error) as exc:
            _error(str(exc))
            return 2
    return wrapper


def clean(text) -> str:
    """Text from a Worker is shown to a person's terminal: show control characters, never obey them."""
    return "".join(ch if (ch in "\n\t" or (ord(ch) >= 32 and not 0x7F <= ord(ch) <= 0x9F)) else f"\\x{ord(ch):02x}"
                   for ch in str(text))


def _dump(obj) -> str:
    return json.dumps(redact_obj(obj), ensure_ascii=False, indent=2, default=str)


def _short(value, n=12) -> str:
    return (value or "-")[:n]


def render_status(d: dict, *, inspection_only: bool = False) -> str:
    base = d["base"] or {}
    worker = d["pinned_worker"] or d["worker_requested"] or (f"by role {d['role']}" if d["role"] else "default")
    lines = [f"{d['task_id']}  {d['status']}  {d['risk']}  {d['title']}",
             f"  project {d['project_id']}   worker {worker}   submitted by {d['submitted_by']}   attempts {d['attempt_count']}"
             + ("   AD-HOC" if d["ad_hoc"] else ""),
             f"  base    {base.get('vcs', '-')} {_short(base.get('commit'))}   config {_short(d['config_digest'], 8)}"]
    if base.get("candidate"):
        lines.append(f"  runs on candidate {base['candidate']}  (built on {_short(base.get('candidate_base'))}; never applied to the project)")
    if d["last_failure"]:
        lines.append(f"  last problem: {clean(d['last_failure'])}")
    if d["not_before"]:
        lines.append(f"  waiting until {d['not_before']}")
    if d["waiting_for"]:
        lines.append(f"  {d['waiting_for']}")
    for a in d["attempts"]:
        end = f"{a['end_kind']}" + (f"/{a['failure_class']}" if a["failure_class"] else "") + (f"@{a['stage']}" if a["stage"] else "") \
            if a["state"] == "ENDED" else a["state"]
        extra = f"  exit {a['exit_code']}" if a["exit_code"] is not None else (f"  signal {a['signal']}" if a["signal"] else "")
        extra += f"  timeout:{a['timeout_kind']}" if a["timeout_kind"] else ""
        lines.append(f"  attempt {a['n']}  {a['worker_id']} ({a['provider_id']}/{a['model_requested']})  {end}{extra}")
    for p in d["pending_approvals"]:
        detail = p["payload"]
        if p["kind"] == "FALLBACK_SWITCH":
            text = f"{detail.get('from')} -> {detail.get('to')}"
        elif p["kind"] == "BASE_DRIFT":
            text = "changed: " + ", ".join(detail.get("changed", []))
        elif p["kind"] == "WRITE_PERMISSION":
            text = "may change: " + ", ".join(detail.get("allowed_paths", []))
        else:
            text = json.dumps(detail, ensure_ascii=False)[:100]
        lines.append(f"  APPROVAL #{p['id']} {p['kind']}  {text}")
    if d["lock"]:
        lines.append(f"  project lock held by {d['lock']['task_id']}#{d['lock']['attempt_n']} ({d['lock']['kernel_id']})")
    lines.append("  next: inspection-only; actions unavailable" if inspection_only else
                 "  next: " + (" | ".join(d["next_actions"]) or "nothing - this task is finished"))
    return "\n".join(lines)


def render_event(e: dict) -> str:
    move = f"{e['from_status']} -> {e['to_status']}" if e["to_status"] else ""
    att = f"#{e['attempt']}" if e["attempt"] else ""
    return f"{e['id']:>5}  {e['ts']}  {e['type']:<20} {att:<3} {move}  {e['reason'] or ''}".rstrip()


def render_result(res: dict, evidence: "dict | None" = None, *, inspection_only: bool = False) -> str:
    lines = [f"{res['task_id']} attempt {res['attempt']}: {res['outcome']}"
             + (f" ({res['failure_class']} at {res['stage']})" if res["failure_class"] else "")]
    if evidence:
        c = evidence["checks"]
        checks = c["status"] + (f" ({c['passed']}/{c['total']})" if c["total"] else "")
        line = (f"  evidence  {evidence['level']} · boundary {evidence['boundary']['status']} · checks {checks}"
                f" · review {evidence['review']} · integration {evidence['integration']} · not applied")
        changed = c.get("test_files_changed") or []
        if changed:
            line += " · tests changed: " + ", ".join(changed[:3])
        lines.append(line)
    lines += [f"  worker {res['worker']} ({res['provider']}, asked for {res['model_requested']}, vendor reported {res['model_observed'] or 'nothing'})",
              f"  worker's own summary (NOT verified): {clean(res['summary'])}"]
    ds = res["diff_stat"]
    lines.append(f"  change  {ds['files']} file(s) +{ds['additions']} -{ds['deletions']}" + (f"   patch: {res['diff_artifact']}" if res["diff_artifact"] else "")
                 + ("   [PARTIAL]" if res["partial"] else ""))
    u = res.get("usage")
    if u:
        parts = [f"input {u['input_tokens']}" + (f" (cached {u['cached_input_tokens']})" if "cached_input_tokens" in u else "")] if "input_tokens" in u else []
        parts += [f"output {u['output_tokens']}" + (f" (reasoning {u['reasoning_output_tokens']})" if "reasoning_output_tokens" in u else "")] if "output_tokens" in u else []
        lines.append("  usage   " + (", ".join(parts) or "reported") + " tokens, as the vendor counted them (not an estimate, not a bill)")
    b = res["boundary"]
    lines.append(f"  boundary  {b['violations']} violation(s), {b['sensitive']} protected" + (f": {clean(', '.join(b['paths'][:8]))}" if b["paths"] else ""))
    for t in res["tests"]:
        lines.append(f"  check {t['name']}: {t['status']} (exit {t['exit_code']}, {t['duration_s']}s)")
    for issue in res["remaining_issues"]:
        lines.append(f"  note: {clean(issue)}")
    lines.append("  next: inspection-only; actions unavailable" if inspection_only else f"  next: {res['next_action']}")
    return "\n".join(lines)


def _task_line(t: dict) -> str:
    return f"{t['task_id']:<16} {t['status']:<18} {t['risk']}  attempts {t['attempts']}  {t['title']}"


# --- commands -----------------------------------------------------------------------------------------------------
def _read_contract(path: str) -> str:
    if path == "-":
        data = sys.stdin.read(MAX_CONTRACT_BYTES + 1)
    else:
        with open(Path(path).expanduser(), "r", encoding="utf-8") as f:
            data = f.read(MAX_CONTRACT_BYTES + 1)
    return data


@handled
def cmd_submit(args):
    try:
        text = _read_contract(args.contract)
    except OSError as exc:
        _error(f"cannot read the contract: {exc.strerror or exc}")
        return 2
    if args.check and getattr(args, "candidate", None):
        _error("--candidate and --check do not go together: check the contract first, then submit it with the candidate")
        return 2
    if args.check:
        with session(args) as kernel:
            res = kernel.validate(text, submitted_by=args.by)
        if args.json:
            print(_dump(res.__dict__))
        else:
            print("contract passes validation; nothing was submitted")
            for w in res.warnings:
                print("  note: " + w)
            for kind in res.would_approvals:
                print(f"  would need approval: {kind}")
        return 0
    with session(args) as kernel:
        res = kernel.submit(text, submitted_by=args.by, candidate=getattr(args, "candidate", None))
    if args.json:
        print(_dump(res.__dict__))
    elif res.deduplicated:
        print(f"already submitted as {res.task_id} ({res.status}): same idempotency key and same contract")
    else:
        print(f"submitted {res.task_id}  {res.status}")
        for w in res.warnings:
            print("  note: " + w)
        if res.pending_approvals:
            print(f"  waiting for your approval: par task approve {res.task_id}")
    return 0


@handled
def cmd_draft(args):
    try:
        text = _read_contract(args.draft)
    except OSError as exc:
        _error(f"cannot read the draft: {exc.strerror or exc}")
        return 2
    try:
        draft = json.loads(text)
    except json.JSONDecodeError as exc:
        _error(f"the draft is not valid JSON: {exc}")
        return 2
    with session(args) as kernel:
        project = kernel.projects.get(draft.get("project_id")) if isinstance(draft, Mapping) else None
        state = None
        if project is not None and project.context_mode == "harness":
            state = default_context(project, kernel.home).view().contract_state
        result = compile_draft(draft, config=kernel.config, projects=kernel.projects, contract_state=state)
    if args.json:
        print(json.dumps({"status": result.status, "contract": result.contract, "questions": list(result.questions),
                          "warnings": list(result.warnings)}, ensure_ascii=False, indent=2))
    elif result.status == READY:
        print(result.contract_json())
        for warning in result.warnings:
            print("note: " + warning, file=sys.stderr)
        print("not submitted: review allowed_paths and checks, then `par task submit --contract FILE`", file=sys.stderr)
    else:
        print(f"needs a decision ({len(result.questions)}):")
        for n, question in enumerate(result.questions, 1):
            print(f"  {n}. {question}")
        for warning in result.warnings:
            print("note: " + warning)
    return 0 if result.status == READY else 2


@handled
def cmd_list(args):
    with session(args, allow_inspection=True) as kernel:
        tasks = kernel.list_tasks(args.status, args.project)
        if kernel.inspection_diagnostic:
            print(kernel.inspection_diagnostic, file=sys.stderr)
    if args.waiting:
        tasks = [t for t in tasks if t["status"] in NEEDS_YOU]
    print(_dump(tasks) if args.json else ("\n".join(_task_line(t) for t in tasks) or "no tasks"))
    return 0


@handled
def cmd_status(args):
    with session(args, allow_inspection=True) as kernel:
        d = kernel.describe(args.task)
        if kernel.inspection_only:
            d["next_actions"] = []
            print(kernel.inspection_diagnostic, file=sys.stderr)
    print(_dump(d) if args.json else render_status(d, inspection_only=kernel.inspection_only))
    return 0


@handled
def cmd_events(args):
    with session(args, allow_inspection=True) as kernel:
        events = kernel.events(args.task, args.after, args.limit)
        if kernel.inspection_diagnostic:
            print(kernel.inspection_diagnostic, file=sys.stderr)
    print(_dump(events) if args.json else ("\n".join(render_event(e) for e in events) or "no events"))
    return 0


@handled
def cmd_result(args):
    with session(args, allow_inspection=True) as kernel:
        res = kernel.result(args.task, args.attempt)
        evidence = kernel.evidence(args.task, args.attempt) if res is not None else None
        if res is not None and kernel.inspection_only:
            res = dict(res)
            res["next_action"] = "none"
        if kernel.inspection_diagnostic:
            print(kernel.inspection_diagnostic, file=sys.stderr)
    if res is None:
        print("no result yet: the task has no finished attempt")
        return 1
    if args.json:
        out = dict(res)
        out["evidence_summary"] = evidence
        print(_dump(out))
    else:
        print(render_result(res, evidence, inspection_only=kernel.inspection_only))
    return 0


@handled
def cmd_diff(args):
    with session(args, allow_inspection=True) as kernel:
        patch = kernel.diff(args.task, args.attempt)
        if kernel.inspection_diagnostic:
            print(kernel.inspection_diagnostic, file=sys.stderr)
    if patch is None:
        print("this attempt produced no patch", file=sys.stderr)
        return 1
    text = patch.decode("utf-8", errors="replace")
    sys.stdout.write(clean(redact_text(text) if args.redact else text))
    return 0


def _graceful_signals():
    """SIGTERM/SIGHUP become KeyboardInterrupt so a stopped `par task run` leaves a clean ledger instead of an orphan."""
    def raise_interrupt(signum, frame):
        raise KeyboardInterrupt
    old = {s: signal.signal(s, raise_interrupt) for s in (signal.SIGTERM, signal.SIGHUP)}
    return lambda: [signal.signal(s, h) for s, h in old.items()]


@handled
def cmd_run(args):
    if bool(args.task) == bool(args.next):
        _error("name a task, or pass --next")
        return 2
    with session(args) as kernel:
        restore = _graceful_signals()
        try:
            report = kernel.run(args.task) if args.task else kernel.run_next()
        except KeyboardInterrupt:
            print("interrupted: the Worker was stopped and the attempt is recorded as INTERRUPTED", file=sys.stderr)
            return 1
        finally:
            restore()
    if report is None:
        print("nothing to run: no queued task is ready")
        return 0
    if args.json:
        print(_dump(report.__dict__))
    else:
        print(f"{report.task_id}  {report.status}  {report.reason}")
        if report.status == "REVIEW_REQUIRED":
            print(f"  review: par task result {report.task_id}   par task diff {report.task_id}   then par task accept {report.task_id}")
        elif report.status != "QUEUED":
            print(f"  see: par task status {report.task_id}")
    return 0 if report.status in OK_AFTER_RUN else 1


def _simple(action: str, doing):
    @handled
    def command(args):
        with session(args) as kernel:
            d = doing(kernel, args)
        print(_dump(d) if getattr(args, "json", False) else f"{action}: {d['task_id']}  {d['status']}")
        return 0
    command.__name__ = f"cmd_{action}"
    return command


cmd_cancel = _simple("cancel", lambda k, a: k.cancel(a.task, reason=a.reason or ""))
cmd_approve = _simple("approve", lambda k, a: k.approve(a.task, a.id, note=a.note or ""))
cmd_reject = _simple("reject", lambda k, a: k.reject(a.task, a.id, note=a.note or ""))
cmd_accept = _simple("accept", lambda k, a: k.accept(a.task, note=a.note or ""))
cmd_close = _simple("close", lambda k, a: k.close_task(a.task))
cmd_retry = _simple("retry", lambda k, a: k.retry(a.task, worker=a.worker, force=a.force, fresh=a.fresh))
cmd_pause = _simple("pause", lambda k, a: k.pause(a.task, reason=a.reason or ""))
cmd_resume = _simple("resume", lambda k, a: k.resume(a.task))


@handled
def cmd_recover(args):
    with session(args) as kernel:
        actions = kernel.recover(args.task, terminate=args.terminate)
    if args.json:
        print(_dump([a.__dict__ for a in actions]))
    else:
        for a in actions:
            print(f"{a.task_id}#{a.attempt}  {a.action}  {a.detail}")
        if not actions:
            print("nothing to recover: no attempt has lost its Kernel")
    return 0


# --- parser ---------------------------------------------------------------------------------------------------------
def _common(c: argparse.ArgumentParser, *, json_flag: bool = True) -> None:
    c.add_argument("--data-dir", metavar="PATH", help="data directory (default: the standard one)")
    if json_flag:
        c.add_argument("--json", action="store_true", help="machine-readable output")


def register(sub) -> None:
    from par import cli_candidate, cli_handoff, cli_health   # imported late: they import this module's helpers
    p = sub.add_parser("provider", help="one-time provider set-up (sign in)")
    actions = p.add_subparsers(dest="provider_command", required=True, metavar="ACTION")
    c = actions.add_parser("login", help="sign in to the Antigravity CLI once, in a Runtime-owned home (never touches ~/.gemini)")
    c.add_argument("provider", nargs="?", default="antigravity", help="provider id in providers.toml (default: antigravity)")
    c.add_argument("--print-only", action="store_true", help="show what would run; create nothing and start nothing")
    c.add_argument("--data-dir", metavar="PATH")
    c.set_defaults(func=cli_health.cmd_provider_login)

    p = sub.add_parser("candidate", help="integrate accepted patches on one verified baseline (a preview; never applied to the project)")
    actions = p.add_subparsers(dest="candidate_command", required=True, metavar="ACTION")
    c = actions.add_parser("create", help="apply accepted tasks' own patches to one clean baseline and run their checks")
    c.add_argument("--task", action="append", required=True, metavar="TASK", help="an accepted R2 task (repeat for A, then B)")
    c.add_argument("--note", metavar="TEXT")
    _common(c)
    c.set_defaults(func=cli_candidate.cmd_candidate_create)
    c = actions.add_parser("show", help="one candidate: its base, commit, tasks, patches and checks")
    c.add_argument("candidate")
    _common(c)
    c.set_defaults(func=cli_candidate.cmd_candidate_show)
    c = actions.add_parser("list", help="candidates, oldest first")
    c.add_argument("--project")
    _common(c)
    c.set_defaults(func=cli_candidate.cmd_candidate_list)

    p = sub.add_parser("task", help="submit, run, review and control tasks (the Kernel's work queue)")
    actions = p.add_subparsers(dest="task_command", required=True, metavar="ACTION")

    c = actions.add_parser("submit", help="validate a task contract and put it in the queue")
    c.add_argument("--contract", required=True, metavar="FILE", help="contract JSON file, or - for standard input")
    c.add_argument("--by", choices=("user", "coordinator"), default="user",
                   help="who is submitting; a coordinator's worker override needs your approval (default: user)")
    c.add_argument("--check", action="store_true",
                   help="only validate the contract; nothing is submitted, no task/event/approval is written")
    c.add_argument("--candidate", metavar="ID",
                   help="run on a verified candidate's baseline (see `par candidate create`); its dependencies must be included")
    _common(c)
    c.set_defaults(func=cmd_submit)

    c = actions.add_parser("rebase", help="point a queued task at a verified candidate before its first attempt")
    c.add_argument("task")
    c.add_argument("--candidate", required=True, metavar="ID")
    c.add_argument("--note", metavar="TEXT")
    _common(c)
    c.set_defaults(func=cli_candidate.cmd_rebase)

    c = actions.add_parser("draft", help="compile a short draft into a strict contract (prints it; submits nothing)")
    c.add_argument("--draft", required=True, metavar="FILE", help="draft JSON file, or - for standard input")
    _common(c)
    c.set_defaults(func=cmd_draft)

    c = actions.add_parser("list", help="list tasks")
    c.add_argument("--status")
    c.add_argument("--project")
    c.add_argument("--waiting", action="store_true",
                   help="only tasks where nothing moves until you decide (approval, review, recovery, quota/login/rate-limit stops)")
    _common(c)
    c.set_defaults(func=cmd_list)

    c = actions.add_parser("status", help="everything about one task: state, attempts, approvals, what you can do next")
    c.add_argument("task")
    _common(c)
    c.set_defaults(func=cmd_status)

    c = actions.add_parser("events", help="the append-only event log of a task")
    c.add_argument("task")
    c.add_argument("--after", type=int, default=0, metavar="ID")
    c.add_argument("--limit", type=int, metavar="N")
    _common(c)
    c.set_defaults(func=cmd_events)

    c = actions.add_parser("result", help="the Result Envelope of the latest (or a given) attempt")
    c.add_argument("task")
    c.add_argument("--attempt", type=int, metavar="N")
    _common(c)
    c.set_defaults(func=cmd_result)

    c = actions.add_parser("diff", help="print the patch an attempt produced (nothing is applied anywhere)")
    c.add_argument("task")
    c.add_argument("--attempt", type=int, metavar="N")
    c.add_argument("--redact", action="store_true", help="mask secret-looking text in the output")
    _common(c, json_flag=False)
    c.set_defaults(func=cmd_diff)

    c = actions.add_parser("run", help="run one attempt of a queued task (in the foreground; Ctrl-C stops it cleanly)")
    c.add_argument("task", nargs="?")
    c.add_argument("--next", action="store_true", help="run the oldest queued task that is ready")
    _common(c)
    c.set_defaults(func=cmd_run)

    c = actions.add_parser("handoff", help="prepare a manual hand-off: a packet and a private copy for a person to work on in a desktop app (WorkBuddy, TRAE ...)")
    c.add_argument("task")
    c.add_argument("--to", default="manual", metavar="APP", help="the application's name, e.g. workbuddy or trae (default: manual)")
    c.add_argument("--note", metavar="TEXT", help="a note for the packet's README")
    _common(c)
    c.set_defaults(func=cli_handoff.cmd_handoff)

    c = actions.add_parser("import", help="take in the result a person brought back from a hand-off and verify it like any Worker's output")
    c.add_argument("task")
    c.add_argument("--from", dest="source", metavar="FOLDER", help="the exported workspace folder after the work")
    c.add_argument("--patch", metavar="FILE", help="a patch against the project's base commit instead of a folder")
    c.add_argument("--text", metavar="FILE", help="a text answer (for read-only and text-only tasks)")
    c.add_argument("--worker", metavar="APP", help="which application produced it (default: the one the hand-off named)")
    c.add_argument("--note", metavar="TEXT")
    _common(c)
    c.set_defaults(func=cli_handoff.cmd_import)

    for name, helptext, func in (("cancel", "cancel a task (stops a running Worker)", cmd_cancel),
                                 ("approve", "grant a pending permission or confirmation", cmd_approve),
                                 ("reject", "refuse a pending permission - or reject a result under review", cmd_reject),
                                 ("accept", "accept a result under review (records it; applies nothing to the project)", cmd_accept),
                                 ("close", "close an accepted task", cmd_close),
                                 ("retry", "a NEW attempt (adopts the current configuration; continues the stopped attempt's verified work unless --fresh)", cmd_retry),
                                 ("pause", "hold a task that is not running", cmd_pause),
                                 ("resume", "release a paused task", cmd_resume)):
        c = actions.add_parser(name, help=helptext)
        c.add_argument("task")
        if name in ("approve", "reject"):
            c.add_argument("--id", type=int, metavar="N", help="which pending approval (required when there are several)")
        if name in ("approve", "reject", "accept"):
            c.add_argument("--note", metavar="TEXT")
        if name in ("cancel", "pause"):
            c.add_argument("--reason", metavar="TEXT")
        if name == "retry":
            c.add_argument("--worker", metavar="ID", help="run on this worker (your explicit choice)")
            c.add_argument("--force", action="store_true", help="clear the health record that is keeping the worker out")
            c.add_argument("--fresh", action="store_true",
                           help="start from the project baseline; by default a file-changing task continues the verified work of its stopped attempt")
        _common(c)
        c.set_defaults(func=func)

    c = actions.add_parser("recover", help="settle attempts whose Kernel is gone (never kills what it cannot identify)")
    c.add_argument("task", nargs="?")
    c.add_argument("--terminate", action="store_true", help="stop a still-running Worker group once its leader is verified")
    _common(c)
    c.set_defaults(func=cmd_recover)

    p = sub.add_parser("takeover", help="hand the coordinator role to someone else (a read-only packet, then an explicit confirmation)")
    tactions = p.add_subparsers(dest="takeover_command", required=True)
    c = tactions.add_parser("export", help="write a takeover packet: the task tree and facts, no conversation, no worker text")
    c.add_argument("--project", metavar="ID", help="only this project")
    _common(c)
    c.set_defaults(func=cli_handoff.cmd_takeover)
    c = tactions.add_parser("confirm", help="record who coordinates from now on (the newest packet only)")
    c.add_argument("takeover_id")
    c.add_argument("--as", dest="coordinator", required=True, metavar="NAME", help="claude_desktop or codex_manual_takeover")
    c.add_argument("--note", metavar="TEXT")
    _common(c)
    c.set_defaults(func=cli_handoff.cmd_takeover)
    c = tactions.add_parser("status", help="who coordinates now")
    _common(c)
    c.set_defaults(func=cli_handoff.cmd_takeover)

    p = sub.add_parser("health", help="which workers can run right now, and what the Kernel has seen go wrong")
    p.add_argument("--json", action="store_true")
    p.add_argument("--probe", action="store_true", help="also ask each registered adapter about itself (no model is called and no quota is spent)")
    p.add_argument("--data-dir", metavar="PATH")
    p.set_defaults(func=cli_health.cmd_health, health_command=None)
    hactions = p.add_subparsers(dest="health_command", metavar="ACTION")
    c = hactions.add_parser("clear", help="forget a health record (for example after you signed in again)")
    c.add_argument("provider")
    c.add_argument("--model")
    c.add_argument("--data-dir", metavar="PATH")
    c.set_defaults(func=cli_health.cmd_health)
