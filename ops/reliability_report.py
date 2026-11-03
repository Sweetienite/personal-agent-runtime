#!/usr/bin/env python3
"""A structured reliability report over every attempt the ledger has ever recorded, in place of a
hand-written "sample size N tasks" note in STATE.md that drifts the moment another task lands.

    .venv/bin/python ops/reliability_report.py                  # every task, every project
    .venv/bin/python ops/reliability_report.py --project par_runtime

Reads only (no `--apply`, nothing to confirm): `par task list --json`, then `par task status
<id> --json` for each task's `attempts`. Like ops/land_task.py, ops/start_task.py and
ops/cleanup_tasks.py, this never imports par.* - only shells out to the same `par` CLI a person
would type. The data directory is resolved the same documented way `par` itself does: --data-dir,
else PAR_DATA_DIR, else the default.

What it answers, with real counts instead of prose someone remembered to update:
- how often a first attempt succeeds outright, and when it does not, what failure_class it was;
- of the tasks whose first attempt did not succeed, how many still reached ACCEPTED/CLOSED after
  a retry, and how many did not;
- per-worker attempt counts and success rate;
- the attempts-per-task distribution.

A number here is only ever as good as what the ledger recorded - a task still PERMISSION_WAIT with
zero attempts contributes to "total tasks" but nothing else; an attempt a Kernel recovered from
(RECOVERY_REQUIRED) is counted by its own end_kind, not folded into some other category."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "PersonalAgentRuntime"
SUCCESS_STATUSES = frozenset({"ACCEPTED", "CLOSED"})

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class Refused(Exception):
    """Something the person must look at; this script only ever reads."""


def resolve_data_dir(override: "Optional[str]") -> Path:
    if override:
        return Path(override).expanduser()
    env = os.environ.get("PAR_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return DEFAULT_DATA_DIR


def par_cli(args: "list[str]", *, data_dir: Path, run: Run) -> str:
    done = run([sys.executable, "-m", "par", *args, "--data-dir", str(data_dir)], capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"`par {' '.join(args)}` 没有成功：{(done.stderr or done.stdout).strip()}")
    return done.stdout


def par_json(args: "list[str]", *, data_dir: Path, run: Run) -> Any:
    text = par_cli([*args, "--json"], data_dir=data_dir, run=run)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Refused(f"`par {' '.join(args)} --json` 没有返回合法 JSON：{exc}") from exc


def gather(*, data_dir: Path, project_id: "Optional[str]", run: Run) -> "list[dict[str, Any]]":
    """One row per task: its final status plus every attempt's end_kind/failure_class/worker_id."""
    rows = par_json(["task", "list"], data_dir=data_dir, run=run)
    tasks = [r for r in rows if isinstance(r, dict) and (project_id is None or r.get("project_id") == project_id)]
    out = []
    for row in tasks:
        detail = par_json(["task", "status", row["task_id"]], data_dir=data_dir, run=run)
        out.append({"task_id": row["task_id"], "project_id": row["project_id"], "status": detail["status"],
                    "risk": row.get("risk"), "attempts": detail.get("attempts") or []})
    return out


def report(tasks: "list[dict[str, Any]]", *, out: Callable[[str], None]) -> None:
    attempted = [t for t in tasks if t["attempts"]]
    all_attempts = [a for t in tasks for a in t["attempts"]]
    out(f"任务总数：{len(tasks)}（有过至少一次 attempt 的：{len(attempted)}） · attempt 总数：{len(all_attempts)}")
    out("")

    out("—— 第一次 attempt ——")
    first_ok = sum(1 for t in attempted if t["attempts"][0]["end_kind"] == "COMPLETED")
    out(f"一次就成（end_kind=COMPLETED）：{first_ok}/{len(attempted)}")
    first_fail_classes = Counter(a["attempts"][0]["failure_class"] or a["attempts"][0]["end_kind"]
                                 for a in [t for t in attempted if t["attempts"][0]["end_kind"] != "COMPLETED"])
    for cls, n in first_fail_classes.most_common():
        out(f"  第一次没成、原因 {cls}：{n} 个任务")
    out("")

    out("—— 没一次就成的任务，后续怎样了 ——")
    retried = [t for t in attempted if t["attempts"][0]["end_kind"] != "COMPLETED"]
    eventually_ok = sum(1 for t in retried if t["status"] in SUCCESS_STATUSES)
    out(f"最终进了 ACCEPTED/CLOSED：{eventually_ok}/{len(retried)}" if retried else "（没有任务需要看这个 - 全部一次就成）")
    if retried:
        still_stuck = Counter(t["status"] for t in retried if t["status"] not in SUCCESS_STATUSES)
        for status, n in still_stuck.most_common():
            out(f"  最终状态是 {status}：{n} 个任务")
    out("")

    out("—— 按 Worker ——")
    by_worker: "dict[str, Counter]" = {}
    for a in all_attempts:
        worker = a.get("worker_id") or "(未知)"
        by_worker.setdefault(worker, Counter())[a["end_kind"]] += 1
    for worker, counts in sorted(by_worker.items()):
        total = sum(counts.values())
        ok = counts.get("COMPLETED", 0)
        out(f"{worker}：{ok}/{total} 次 attempt 成功 ({', '.join(f'{k}={v}' for k, v in counts.most_common())})")
    out("")

    out("—— 每个任务用了几次 attempt ——")
    attempt_counts = Counter(len(t["attempts"]) for t in attempted)
    for n, count in sorted(attempt_counts.items()):
        out(f"{n} 次：{count} 个任务")


def main(argv: "Optional[list[str]]" = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default=None, help="只看这个项目的任务；不给就是全部已登记项目")
    p.add_argument("--data-dir", default=None, help="数据目录（默认按 par 自己的规则：--data-dir / PAR_DATA_DIR / 默认路径）")
    args = p.parse_args(argv)
    data_dir = resolve_data_dir(args.data_dir)
    try:
        tasks = gather(data_dir=data_dir, project_id=args.project, run=subprocess.run)
    except Refused as exc:
        print(f"没有报告：{exc}")
        return 1
    if not tasks:
        print("没有任务。" if args.project is None else f"项目 {args.project} 里没有任务。")
        return 0
    report(tasks, out=print)
    return 0


if __name__ == "__main__":
    sys.exit(main())
