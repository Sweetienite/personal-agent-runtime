"""``par candidate ...`` and ``par task rebase``: candidate integration commands."""
from __future__ import annotations

from par.cli_task import _dump, clean, handled, session


@handled
def cmd_rebase(args):
    with session(args) as kernel:
        d = kernel.task_rebase(args.task, args.candidate, note=args.note or "")
    if args.json:
        print(_dump(d))
    else:
        base = d.get("base") or {}
        print(f"{d['task_id']}  {d['status']}  base {str(base.get('commit'))[:12]}  (candidate {base.get('candidate')})")
        print("  runs on the candidate's base; nothing was applied to the real project")
    return 0


@handled
def cmd_candidate_create(args):
    with session(args) as kernel:
        d = kernel.candidate_create(args.task, note=args.note or "")
    if args.json:
        print(_dump(d))
        return 0 if d["status"] == "VERIFIED" else 1
    print(f"{d['candidate_id']}  {d['status']}  base {d['base_commit'][:12]}  commit {(d.get('commit_sha') or '-')[:12]}")
    for t in d["tasks"]:
        print(f"  {t['task_id']}#{t['attempt']}  patch {t['patch_sha256'][:12]}  commit {t['commit'][:12]}")
    for c in d["checks"]:
        print(f"  check {c['task_id']} {c['name']}: {c['status']}")
    if d.get("reason"):
        print(f"  reason: {clean(d['reason'])}")
    print(f"  repo: {d['repo']}")
    print("  nothing was applied to the real project")
    if d["status"] == "VERIFIED":
        print(f"  use it: par task submit --contract FILE --candidate {d['candidate_id']}")
        print(f"          or: par task rebase TASK --candidate {d['candidate_id']}")
    return 0 if d["status"] == "VERIFIED" else 1


@handled
def cmd_candidate_show(args):
    with session(args) as kernel:
        d = kernel.candidate_show(args.candidate)
    if args.json:
        print(_dump(d))
        return 0
    print(f"{d['candidate_id']}  {d['status']}  project {d['project_id']}")
    print(f"  base   {d['base_commit']}")
    print(f"  commit {d.get('commit_sha') or '-'}")
    print(f"  asked  {', '.join(d.get('requested', [])) or '-'}")
    for t in d["tasks"]:
        print(f"  {t['task_id']}#{t['attempt']}  patch {t['patch_sha256'][:12]}  commit {t['commit'][:12]}")
    for c in d["checks"]:
        print(f"  check {c['task_id']} {c['name']}: {c['status']}")
    if d.get("reason"):
        print(f"  reason: {clean(d['reason'])}")
    print(f"  repo   {d['repo']}  (never applied to the real project)")
    return 0


@handled
def cmd_candidate_list(args):
    with session(args) as kernel:
        rows = kernel.candidate_list(args.project)
    if args.json:
        print(_dump(rows))
        return 0
    if not rows:
        print("no candidates")
        return 0
    for d in rows:
        print(f"{d['candidate_id']:<32} {d['status']:<9} {d['project_id']:<12} base {d['base_commit'][:12]}  {len(d['tasks'])} task(s)")
    return 0
