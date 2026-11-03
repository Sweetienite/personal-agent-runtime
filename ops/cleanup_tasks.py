#!/usr/bin/env python3
"""Free the disk space a CLOSED task's own artifacts leave behind: the private-copy workspace,
its scratch HOME, and every hand-off export are removed; the small, load-bearing evidence
(result.json, work.patch, prompt.txt, changes.json, check logs) stays.

    .venv/bin/python ops/cleanup_tasks.py                             # dry run: every CLOSED task, reports sizes, deletes nothing
    .venv/bin/python ops/cleanup_tasks.py --apply                      # ...actually deletes
    .venv/bin/python ops/cleanup_tasks.py par_runtime-0001 --apply     # just this one task (it still must be CLOSED)

Only ever deletes paths matching a fixed whitelist, computed from a task id this script itself
got from a CLOSED row of `par task list --json`: tasks/<id>/attempts/*/{workspace,home,tmp} and
the task's entire tasks/<id>/handoff tree. Never a glob over "anything old", never anything
outside a single task's own subtree, never a task whose status is not exactly CLOSED (a FAILED
task might still be retried; this only ever touches a task whose lifecycle is fully finished).

Like ops/land_task.py and ops/start_task.py, this never imports par.* - only shells out to the
same `par` CLI a person would type. The data directory is resolved the same documented way `par`
itself does: --data-dir, else PAR_DATA_DIR, else the default."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "PersonalAgentRuntime"
REMOVE_FROM_ATTEMPT = ("workspace", "home", "tmp")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class Refused(Exception):
    """Something the person must look at; nothing was deleted."""


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


def closed_task_ids(*, data_dir: Path, run: Run) -> "list[str]":
    text = par_cli(["task", "list", "--json"], data_dir=data_dir, run=run)
    try:
        rows = json.loads(text)
    except ValueError as exc:
        raise Refused(f"`par task list --json` 没有返回合法 JSON：{exc}") from exc
    return [r["task_id"] for r in rows if isinstance(r, dict) and r.get("status") == "CLOSED"]


def task_status(task_id: str, *, data_dir: Path, run: Run) -> str:
    text = par_cli(["task", "status", task_id, "--json"], data_dir=data_dir, run=run)
    try:
        return json.loads(text).get("status", "")
    except ValueError as exc:
        raise Refused(f"`par task status {task_id} --json` 没有返回合法 JSON：{exc}") from exc


def du(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    if not path.is_dir():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda exc: None):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "K", "M"):
        if size < 1024:
            return (f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}")
        size /= 1024
    return f"{size:.1f}G"


def candidates(task_id: str, data_dir: Path) -> "list[Path]":
    """The fixed whitelist of what this task's own CLOSED cleanup may remove - nothing else."""
    task_dir = data_dir / "tasks" / task_id
    paths: "list[Path]" = []
    attempts_dir = task_dir / "attempts"
    if attempts_dir.is_dir():
        for attempt_dir in sorted(attempts_dir.iterdir()):
            if not attempt_dir.is_dir():
                continue
            for name in REMOVE_FROM_ATTEMPT:
                p = attempt_dir / name
                if p.exists():
                    paths.append(p)
    handoff_dir = task_dir / "handoff"
    if handoff_dir.exists():
        paths.append(handoff_dir)
    return paths


def cleanup(
    task_ids: "list[str]",
    *,
    data_dir: Path,
    apply_flag: bool,
    par_run: Run = subprocess.run,
    out: Callable[[str], None] = print,
) -> int:
    try:
        if task_ids:
            for task_id in task_ids:
                status = task_status(task_id, data_dir=data_dir, run=par_run)
                if status != "CLOSED":
                    raise Refused(f"任务 {task_id} 现在是「{status}」，不是「CLOSED」，不会清理。")
            targets = list(task_ids)
        else:
            targets = closed_task_ids(data_dir=data_dir, run=par_run)
    except Refused as exc:
        out(f"没有改动：{exc}")
        return 1

    if not targets:
        out("没有已关闭的任务，没有什么可清理的。")
        return 0

    total = 0
    touched = 0
    for task_id in targets:
        task_dir = data_dir / "tasks" / task_id
        if not task_dir.is_dir():
            out(f"跳过 {task_id}：数据目录里没有这个任务的文件夹（{task_dir}）。")
            continue
        paths = candidates(task_id, data_dir)
        if not paths:
            continue
        size = sum(du(p) for p in paths)
        total += size
        touched += 1
        if not apply_flag:
            out(f"{task_id}：会释放 {human(size)}（{len(paths)} 处）。")
            continue
        failed = []
        for p in paths:
            try:
                shutil.rmtree(p)
            except OSError as exc:
                failed.append((p, exc))
        if failed:
            for p, exc in failed:
                out(f"{task_id}：删除 {p} 失败：{exc}")
        else:
            out(f"{task_id}：已删除 {human(size)}。")

    if not touched:
        out("已关闭的任务里没有可以清理的东西（可能都已经清过了）。")
        return 0

    out(f"合计{'已释放' if apply_flag else '会释放'} {human(total)}，涉及 {touched} 个任务。")
    if not apply_flag:
        out("没有实际删除任何文件。确认无误后加 --apply 重新运行。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("task_ids", nargs="*", help="只清理这些任务（必须都是 CLOSED）；不给就清理所有已关闭的任务")
    p.add_argument("--apply", action="store_true", help="实际删除；不加时只试运行、只报告")
    p.add_argument("--data-dir", default=None, help="数据目录（默认按 par 自己的规则：--data-dir / PAR_DATA_DIR / 默认路径）")
    return p


def main(argv: "Optional[list[str]]" = None) -> int:
    args = build_parser().parse_args(argv)
    return cleanup(args.task_ids, data_dir=resolve_data_dir(args.data_dir), apply_flag=args.apply)


if __name__ == "__main__":
    sys.exit(main())
