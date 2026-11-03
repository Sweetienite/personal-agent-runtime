#!/usr/bin/env python3
"""Restore drill (plan P8: "a drill of restoring from a backup/export"): back up the Runtime's data directory the way docs/ROLLBACK.md says
(the whole folder, the ledger's three files together), restore it into a throw-away folder and prove that the copy is a working Runtime.
The source is only read; the copy is deleted at the end. No model call, no vendor program.

    python -B tests/tools/restore_drill.py [--data-dir DIR] [--rehearse-switch] [--keep]

On the copy it checks: the ledger passes SQLite's integrity check and holds the same tasks, events, attempts and approvals as the source; `par doctor`
passes; `par task list` shows the same tasks and states; `par task result` and `par task diff` read the stored evidence of an accepted task from the
NEW location; the projects and the configuration load. With --rehearse-switch it also switches the coordinator on the COPY (current -> the other ->
current) and starts real bridge processes: the replaced coordinator is refused when it submits and may still read, the new one may submit, and the
switch back works - on a copy, so the real ledger gets no "seen" heartbeats and no takeover packets.

It also says what a backup that copies only runtime.sqlite3 would have lost: the ledger runs in WAL mode, so the newest transactions sit in
runtime.sqlite3-wal until SQLite folds them in.

vendor/ (the private agy install, a few hundred MB, re-creatable) is not copied: after a real restore, `par health --probe` says what to reinstall.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from par.paths import default_data_dir      # noqa: E402

TABLES = ("tasks", "events", "attempts", "approvals")
failures = []


def check(ok, what, detail=""):
    print(("PASS  " if ok else "FAIL  ") + what + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(what)


def par(*argv, data):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1", "HOME": os.environ.get("HOME", "")}
    done = subprocess.run([sys.executable, "-B", "-m", "par", *argv, "--data-dir", str(data)], capture_output=True, text=True, env=env, cwd="/", timeout=300)
    return done.returncode, done.stdout, done.stderr


def ledger(db, *, readonly=False):
    """The ledger as SQLite itself reads it, so the -wal file counts. (Never `immutable=1`: that ignores the -wal and shows an old ledger.)"""
    if readonly:
        return sqlite3.connect("file:" + urllib.parse.quote(str(db)) + "?mode=ro", uri=True)
    return sqlite3.connect(db)


def snapshot(db, *, readonly=False):
    c = ledger(db, readonly=readonly)
    try:
        counts = {t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in TABLES}
        tasks = sorted(c.execute("select task_id, status from tasks").fetchall())
        return counts, tasks
    finally:
        c.close()


def bridge(copy, coordinator, *calls):
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "drill", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}]
    for n, (name, arguments) in enumerate(calls, 2):
        msgs.append({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-B", "-m", "par", "mcp", "serve", "--data-dir", str(copy), "--coordinator", coordinator],
                          input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True, env=env, cwd="/", timeout=120)
    replies = {}
    for line in done.stdout.splitlines():
        if line.strip():
            reply = json.loads(line)
            replies[reply.get("id")] = reply
    return done.returncode, replies


def tool_result(replies, n):
    return replies.get(n, {}).get("result", {})


def rehearse_switch(copy, project_id):
    code, out, _ = par("coordinator", "status", "--json", data=copy)
    status = json.loads(out) if code == 0 else {}
    start = (status.get("current") or {}).get("id")
    others = [c["id"] for c in status.get("coordinators", []) if c["id"] != start]
    check(bool(start) and bool(others), "the copy knows its current coordinator and at least one other", str(status)[:200])
    if not (start and others):
        return
    other = others[0]
    print(f"note  rehearsing {start} -> {other} -> {start} on the copy")
    code, out, err = par("coordinator", "use", other, "--note", "restore drill: rehearsal on a copy", "--json", data=copy)
    done = json.loads(out) if code == 0 else {}
    check(code == 0 and done.get("from") == start and done.get("to") == other and done.get("takeover_id"), f"`par coordinator use {other}` switches on the copy and writes a takeover packet", (out + err).strip()[-200:])
    contract = {"schema_version": 1, "project_id": project_id, "title": "restore drill", "description": "A read-only look, made by the restore drill on a copy.",
                "role": "analysis", "risk": "R1", "allowed_paths": [], "acceptance": {"criteria": ["none: this task only exists on a throw-away copy"], "checks": []},
                "idempotency_key": "restore-drill-1"}
    rc, replies = bridge(copy, start, ("submit_task", {"contract": contract}), ("list_tasks", {}))
    refused = tool_result(replies, 2)
    check(rc == 0 and refused.get("isError") is True and "not_the_coordinator" in json.dumps(refused), f"the replaced coordinator ({start}) is refused when it submits", json.dumps(refused)[:200])
    check(tool_result(replies, 3).get("isError") is False, f"the replaced coordinator ({start}) may still read (list_tasks)")
    rc, replies = bridge(copy, other, ("submit_task", {"contract": contract}))
    accepted = tool_result(replies, 2)
    check(rc == 0 and accepted.get("isError") is False, f"the new coordinator ({other}) can submit", json.dumps(accepted)[:300])
    code, out, err = par("coordinator", "use", start, "--note", "restore drill: back", "--json", data=copy)
    done = json.loads(out) if code == 0 else {}
    check(code == 0 and done.get("from") == other and done.get("to") == start, f"`par coordinator use {start}` switches back on the copy", (out + err).strip()[-200:])
    code, out, _ = par("coordinator", "status", "--json", data=copy)
    status = json.loads(out) if code == 0 else {}
    check((status.get("current") or {}).get("id") == start and len(status.get("history", [])) == 2, f"the copy ends with {start} as the coordinator and two recorded switches")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default=str(default_data_dir()))
    parser.add_argument("--rehearse-switch", action="store_true", help="also rehearse the coordinator switch on the copy")
    parser.add_argument("--keep", action="store_true", help="keep the copy (its path is printed)")
    args = parser.parse_args()
    source = Path(os.path.expanduser(args.data_dir)).resolve()
    if not (source / "runtime.sqlite3").is_file():
        print(f"no Runtime data directory at {source}", file=sys.stderr)
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="par-restore-drill-"))
    copy = tmp / "data"
    try:
        counts_before, tasks_before = snapshot(source / "runtime.sqlite3", readonly=True)
        # The backup: the whole folder, the ledger's three files together. Symlinks stay symlinks; vendor/ is re-creatable.
        shutil.copytree(source, copy, ignore=shutil.ignore_patterns("vendor", "agy_login_home"), symlinks=True)
        os.chmod(copy, 0o700)
        print(f"source: {source}\ncopy:   {copy}  (without vendor/)\nsource ledger: {counts_before}\n")
        db = copy / "runtime.sqlite3"
        wal = copy / "runtime.sqlite3-wal"
        print(f"note  the ledger's -wal file holds {wal.stat().st_size if wal.exists() else 0} bytes that are not yet in runtime.sqlite3")
        lone = tmp / "lone"
        lone.mkdir()
        shutil.copy2(source / "runtime.sqlite3", lone / "runtime.sqlite3")
        lost = snapshot(lone / "runtime.sqlite3")[0]
        print(f"note  a backup of runtime.sqlite3 ALONE would hold {lost['tasks']} of {counts_before['tasks']} tasks and {lost['events']} of {counts_before['events']} events\n")
        conn = ledger(db)
        try:
            check(conn.execute("pragma integrity_check").fetchone()[0] == "ok", "the restored ledger passes SQLite's integrity check")
        finally:
            conn.close()
        counts_copy, tasks_copy = snapshot(db)
        check(counts_copy == counts_before, "the copy holds the same number of tasks, events, attempts and approvals as the source", f"{counts_copy} vs {counts_before}")
        check(tasks_copy == tasks_before, "every task has the same status in the copy", f"{tasks_copy} vs {tasks_before}")
        code, out, err = par("doctor", data=copy)
        check(code == 0, "`par doctor` passes on the copy", (out + err).strip()[-200:])
        code, out, err = par("task", "list", "--json", data=copy)
        listed = sorted((t["task_id"], t["status"]) for t in json.loads(out)) if code == 0 else None
        check(listed == tasks_before, "`par task list` on the copy shows the same tasks and states", f"{listed} vs {tasks_before}")
        accepted = next((tid for tid, status in tasks_before if status in ("ACCEPTED", "CLOSED")), None)
        if accepted:
            code, out, err = par("task", "result", accepted, data=copy)
            check(code == 0 and f"{accepted} attempt" in out, f"`par task result {accepted}` reads the stored result from the new location", (out + err).strip()[:200])
            code, out, err = par("task", "diff", accepted, data=copy)
            check(code == 0 and out.startswith("diff --git"), f"`par task diff {accepted}` reads the stored patch from the new location", (out + err).strip()[:200])
        else:
            print("note  no accepted task in this ledger: the stored-evidence reads were skipped")
        code, out, err = par("project", "list", "--json", data=copy)
        projects = json.loads(out) if code == 0 else []
        check(code == 0 and len(projects) > 0, "the projects and the configuration load from the copy", (out + err).strip()[-200:])
        if args.rehearse_switch:
            ids = [p["id"] for p in projects]
            rehearse_switch(copy, "par_trial" if "par_trial" in ids else (ids[0] if ids else "fixture"))
        counts_after, tasks_after = snapshot(source / "runtime.sqlite3", readonly=True)
        check(counts_after == counts_before and tasks_after == tasks_before, "the source ledger is unchanged by the drill (a live coordinator may add heartbeats: run it again)", f"{counts_after} vs {counts_before}")
    finally:
        if args.keep:
            print(f"\nkept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED: " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
