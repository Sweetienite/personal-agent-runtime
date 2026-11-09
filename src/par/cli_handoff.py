"""``par task handoff/import`` and ``par takeover ...``: manual relay and coordinator handover commands."""
from __future__ import annotations

from pathlib import Path

from par.cli_task import OK_AFTER_RUN, _dump, _error, handled, session


@handled
def cmd_handoff(args):
    with session(args) as kernel:
        d = kernel.handoff_export(args.task, to=args.to, note=args.note or "")
    if args.json:
        print(_dump(d))
        return 0
    print(f"{d['task_id']}  hand-off prepared for {d['to']}  mode: {d['mode']}  (the task is now {d['status']})")
    print(f"  packet folder : {d['folder']}")
    print(f"  steps (read)  : {d['readme']}")
    print(f"  prompt (paste): {d['prompt']}")
    for n, action in enumerate(d["next_actions"], 1):
        print(f"  {n}. {action}")
    return 0


@handled
def cmd_import(args):
    chosen = [x for x in (args.source, args.patch, args.text) if x]
    if len(chosen) != 1:
        _error("give exactly one of --from FOLDER, --patch FILE, --text FILE")
        return 2
    with session(args) as kernel:
        report = kernel.handoff_import(args.task, source=Path(args.source).expanduser() if args.source else None,
                                       patch=Path(args.patch).expanduser() if args.patch else None,
                                       text=Path(args.text).expanduser() if args.text else None, worker=args.worker, note=args.note or "")
    if args.json:
        print(_dump(report.__dict__))
    else:
        print(f"{report.task_id}  {report.status}  {report.reason}")
        if report.status == "REVIEW_REQUIRED":
            print(f"  review: par task result {report.task_id}   par task diff {report.task_id}   then par task accept {report.task_id}")
        else:
            print(f"  see: par task status {report.task_id}")
    return 0 if report.status in OK_AFTER_RUN else 1


@handled
def cmd_takeover(args):
    with session(args) as kernel:
        if args.takeover_command == "export":
            d = kernel.takeover_export(project_id=args.project)
            print(_dump(d) if args.json else
                  f"takeover packet {d['takeover_id']}: {d['tasks']} task(s), {d['finished']} finished, {d['needs_a_decision']} waiting for a decision\n"
                  f"  read : {d['markdown']}\n  json : {d['json']}\n"
                  f"  when you have chosen the new coordinator: par takeover confirm {d['takeover_id']} --as codex_manual_takeover")
        elif args.takeover_command == "confirm":
            d = kernel.takeover_confirm(args.takeover_id, coordinator=args.coordinator, note=args.note or "")
            print(_dump(d) if args.json else f"{d['coordinator']} coordinates from now on (was {d['previous']})")
        else:
            lease = kernel.coordinator_lease()
            print(_dump(lease) if args.json else f"coordinator: {lease['coordinator']}" +
                  (f"  (since {lease['since']}, takeover {lease['takeover_id']})" if lease["since"] else "  (as configured)"))
    return 0
