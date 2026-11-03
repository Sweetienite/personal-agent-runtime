#!/usr/bin/env python3
"""Submit a par task contract and get it past the approval gate the Kernel puts in front of a
coordinator-submitted task - but only when that is safe to do without guessing.

    .venv/bin/python ops/start_task.py contract.json              # submit + auto-approve if it matches exactly; stop there
    .venv/bin/python ops/start_task.py contract.json --run         # ...then actually run one attempt (spends real quota)

Submits with `--by coordinator` (this is unattended automation, not a human at a keyboard typing
the command - see docs/OPERATIONS.md and .ai/tasks/archive/TASK-003-*.md for why that matters: it
is what makes a pinned `worker` require a WORKER_OVERRIDE approval, the gate that exists
specifically so a person signs off on the AI picking a specific model).

After submitting, a pending approval is approved only when it is exactly one of:
  - WRITE_PERMISSION whose `allowed_paths` (as a set) equal the contract's own `allowed_paths`;
  - WORKER_OVERRIDE whose `worker` equals the contract's own `worker`.
Any other pending approval - a different kind, a payload that does not match exactly, or
anything beyond those two kinds - is left pending and reported; this script never calls
`par task reject` (that would cancel the task, which is not its call to make) and never approves
anything it has not verified against the exact contract file it just submitted.

Without --run, stops once submitted (and approved, if applicable) and says running it for real
needs --run. With --run: prints which worker is about to be invoked, runs one attempt, waits for
it, and reports the resulting terminal state - once. It never retries, never switches workers,
and never looks at any approval that appears after that point; all of that stays a separate,
deliberate decision for a person or the coordinator.

When that one attempt ends in REVIEW_REQUIRED, this also prints `par task result` and
`par task diff` right there - the point of chaining submit/approve/run together is to get
straight to "here is what to review", not to make the review itself go away. Accepting the
result and landing it (ops/land_task.py) stay separate, deliberate steps this script never takes
on its own - accept is a judgment call, not something a script gets to make.

Like ops/land_task.py, this never imports par.* - only shells out to the same `par` CLI a person
would type."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[1]
AUTO_APPROVE_KINDS = ("WRITE_PERMISSION", "WORKER_OVERRIDE")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class Refused(Exception):
    """Something the person must look at; nothing beyond what is reported was submitted, approved, or run."""


def par_cli(args: "list[str]", *, cwd: Path, run: Run) -> str:
    done = run([sys.executable, "-m", "par", *args], cwd=str(cwd), capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"`par {' '.join(args)}` 没有成功：{(done.stderr or done.stdout).strip()}")
    return done.stdout


def par_json(args: "list[str]", *, cwd: Path, run: Run) -> "dict[str, Any]":
    text = par_cli([*args, "--json"], cwd=cwd, run=run)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Refused(f"`par {' '.join(args)} --json` 没有返回合法 JSON：{exc}") from exc


def matches(approval: "dict[str, Any]", contract: "dict[str, Any]") -> bool:
    kind = approval.get("kind")
    payload = approval.get("payload") or {}
    if kind == "WRITE_PERMISSION":
        return set(payload.get("allowed_paths") or []) == set(contract.get("allowed_paths") or [])
    if kind == "WORKER_OVERRIDE":
        return bool(contract.get("worker")) and payload.get("worker") == contract.get("worker")
    return False


def describe_approval(a: "dict[str, Any]") -> str:
    return f"#{a.get('id')} {a.get('kind')} {json.dumps(a.get('payload'), ensure_ascii=False)}"


def start(
    contract_path: Path,
    *,
    cwd: Path,
    run_flag: bool,
    par_run: Run = subprocess.run,
    out: Callable[[str], None] = print,
) -> int:
    cwd = Path(cwd)
    contract_path = Path(contract_path)
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out(f"没有改动：读不到或解不开契约文件 {contract_path}：{exc}")
        return 1

    try:
        submitted = par_json(
            ["task", "submit", "--contract", str(contract_path), "--by", "coordinator"], cwd=cwd, run=par_run
        )
        task_id = submitted.get("task_id")
        if not task_id:
            raise Refused(f"提交没有返回 task_id：{submitted}")
        task = par_json(["task", "status", task_id], cwd=cwd, run=par_run)
        pending = task.get("pending_approvals") or []
        unmatched = [a for a in pending if a.get("kind") not in AUTO_APPROVE_KINDS or not matches(a, contract)]
        if unmatched:
            lines = "；".join(describe_approval(a) for a in unmatched)
            out(
                f"已提交：{task_id}，但有批准事项不会自动处理（{lines}）——"
                f"不是没识别的类型，就是和契约内容不完全一致，不会自动批准，也不会拒绝。"
                f"请你自己 `par task status {task_id}` 看一下，用 `par task approve/reject` 决定。"
            )
            return 1
        for a in pending:
            par_cli(["task", "approve", task_id, "--id", str(a["id"])], cwd=cwd, run=par_run)
        task = par_json(["task", "status", task_id], cwd=cwd, run=par_run)
    except Refused as exc:
        out(f"没有改动：{exc}")
        return 1

    out(f"已提交并批准：{task_id}「{task.get('title', '')}」，现在是「{task.get('status')}」。")
    if not run_flag:
        out("没有实际运行任何 Worker，也没有花额度。确认无误后加 --run 重新运行，会调用真实模型。")
        return 0

    worker = task.get("pinned_worker") or task.get("worker_requested") or "按角色/默认路由选择"
    out(f"即将真实调用：worker {worker}。")
    ran = par_run([sys.executable, "-m", "par", "task", "run", task_id], cwd=str(cwd), capture_output=True, text=True)
    final = par_json(["task", "status", task_id], cwd=cwd, run=par_run)
    status = final.get("status")
    out(
        f"跑完一次 attempt（`par task run` 退出码 {ran.returncode}）：任务 {task_id} 现在是「{status}」。"
        "只跑了这一次，不会自动重试，也不会接着处理后面可能出现的批准。"
    )
    if status == "REVIEW_REQUIRED":
        # result and diff are fetched independently: a read-only (R1) or no-change attempt has no
        # patch at all and `par task diff` exits non-zero for that ("this attempt produced no
        # patch") - that must not also hide a result that was read just fine.
        out("")
        out("=== 结果（par task result） ===")
        try:
            out(par_cli(["task", "result", task_id], cwd=cwd, run=par_run).rstrip("\n"))
        except Refused as exc:
            out(f"（读取 result 失败：{exc}；自己跑一下 par task result {task_id}）")
        out("")
        out("=== 补丁（par task diff） ===")
        try:
            out(par_cli(["task", "diff", task_id], cwd=cwd, run=par_run).rstrip("\n"))
        except Refused as exc:
            out(f"（没有补丁，或读取失败：{exc}）")
        out("")
        out(f"review 完如果没问题：par task accept {task_id}，再 .venv/bin/python ops/land_task.py {task_id} --apply")
    return ran.returncode


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("contract", help="任务契约 JSON 文件的路径")
    p.add_argument("--run", action="store_true", help="批准之后真的跑一次 attempt；会调用真实模型、花真实额度")
    p.add_argument("--repo", default=str(REPO), help="在哪个目录下调用 par（默认是这个脚本所在仓库的根目录）")
    return p


def main(argv: "Optional[list[str]]" = None) -> int:
    args = build_parser().parse_args(argv)
    return start(Path(args.contract), cwd=Path(args.repo), run_flag=args.run)


if __name__ == "__main__":
    sys.exit(main())
