#!/usr/bin/env python3
"""Land an ACCEPTED `par` task's diff onto the real repository: apply it, run this project's full
test suite against the real tree, and - only if that passes - commit, then close the task. Part
of a coordinator-automation effort: this script never touches `.ai/STATE.md` - that stays a
separate, coordinator-written step.

    .venv/bin/python ops/land_task.py par_runtime-0003              # dry run: shows what it would do, changes nothing
    .venv/bin/python ops/land_task.py par_runtime-0003 --apply        # applies, runs the full suite, commits if it passes

Preconditions checked before anything is written: the task must be ACCEPTED; the real repo's
current commit must match the task's recorded base commit exactly (otherwise the repo moved since
the task was submitted, and applying blindly would be wrong); the working tree must be clean. If
the patch applies but the full suite then fails, the apply is reverted (`git apply -R`) and
nothing is committed - the repo is left exactly as it was found.

This is an ops script a person or the coordinator runs; `par` itself still never applies a patch
to a project - this script only ever talks to the Runtime through the same `par task status` /
`par task diff` CLI output a person would read, never by importing its internals.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[1]
TEST_CMD = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."]
DIFF_HEADER = re.compile(r"^diff --git a/(.*) b/(.*)$")
TEST_COUNT = re.compile(r"Ran (\d+) tests?")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class Refused(Exception):
    """Something the person must look at; nothing was applied, tested, or committed."""


def git(cwd: Path, *args: str, run: Run = subprocess.run, input_text: Optional[str] = None) -> "subprocess.CompletedProcess[str]":
    return run(["git", "-C", str(cwd), *args], input=input_text, capture_output=True, text=True)


def par_cli(args: "list[str]", *, cwd: Path, run: Run) -> str:
    done = run([sys.executable, "-m", "par", *args], cwd=str(cwd), capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"`par {' '.join(args)}` 没有成功：{(done.stderr or done.stdout).strip()}")
    return done.stdout


def task_status(task_id: str, *, cwd: Path, run: Run) -> "dict[str, Any]":
    text = par_cli(["task", "status", task_id, "--json"], cwd=cwd, run=run)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Refused(f"`par task status {task_id} --json` 没有返回合法 JSON：{exc}") from exc


def task_diff(task_id: str, *, cwd: Path, run: Run) -> str:
    return par_cli(["task", "diff", task_id], cwd=cwd, run=run)


def diff_paths(diff_text: str) -> "list[str]":
    return [m.group(2) for line in diff_text.splitlines() if (m := DIFF_HEADER.match(line))]


def test_summary(output: str) -> Optional[str]:
    m = TEST_COUNT.search(output)
    return f"full suite: {m.group(1)} tests" if m else None


def commit_message(task_id: str, task: "dict[str, Any]", paths: "list[str]", summary: Optional[str]) -> str:
    title = task.get("title") or task_id
    worker = task.get("pinned_worker") or task.get("worker_requested") or "unknown"
    lines = [
        f"land: {title}",
        "",
        f"Applied, verified, and closed {task_id} (worker: {worker}).",
        "Files: " + ", ".join(paths) if paths else "Files: (none)",
    ]
    if summary:
        lines.append(summary)
    return "\n".join(lines)


def land(
    task_id: str,
    *,
    cwd: Path,
    apply_flag: bool,
    par_run: Run = subprocess.run,
    git_run: Run = subprocess.run,
    test_run: Run = subprocess.run,
    out: Callable[[str], None] = print,
) -> int:
    cwd = Path(cwd)
    try:
        task = task_status(task_id, cwd=cwd, run=par_run)
        status = task.get("status")
        if status != "ACCEPTED":
            raise Refused(f"任务 {task_id} 现在是「{status}」，不是「ACCEPTED」，不会落地。")
        base_commit = (task.get("base") or {}).get("commit") or ""
        if not base_commit:
            raise Refused(f"任务 {task_id} 没有记录基线 commit，无法核对，不会落地。")
        head = git(cwd, "rev-parse", "HEAD", run=git_run).stdout.strip()
        if not head:
            raise Refused("读不到真实仓库当前的 commit（`git rev-parse HEAD` 失败）。")
        if head != base_commit:
            raise Refused(
                f"真实仓库现在在 {head[:12]}，和任务基线 {base_commit[:12]} 不一致——"
                "仓库可能在委派之后又变了，不会盲目落地，请先核对。"
            )
        dirty = git(cwd, "status", "--porcelain", run=git_run).stdout.strip()
        if dirty:
            raise Refused("真实仓库工作区不干净（有未提交或未跟踪的改动），不会落地。")
        diff_text = task_diff(task_id, cwd=cwd, run=par_run)
        if not diff_text.strip():
            raise Refused(f"任务 {task_id} 没有补丁内容，没有什么可落地的。")
        paths = diff_paths(diff_text)
        checked = git(cwd, "apply", "--check", input_text=diff_text, run=git_run)
        if checked.returncode != 0:
            raise Refused(f"补丁套不上真实仓库：{checked.stderr.strip()}")
    except Refused as exc:
        out(f"没有改动：{exc}")
        return 1

    out(f"试运行通过：{task_id}「{task.get('title', '')}」会改动 {len(paths)} 个文件：{', '.join(paths)}")
    if not apply_flag:
        out("没有实际改动任何文件。确认无误后加 --apply 重新运行。")
        return 0

    applied = git(cwd, "apply", input_text=diff_text, run=git_run)
    if applied.returncode != 0:
        out(f"没有改动：补丁没能应用（{applied.stderr.strip()}），虽然刚才的 --check 通过了。")
        return 1

    out("已应用补丁，正在跑全量测试……")
    result = test_run(TEST_CMD, cwd=str(cwd), capture_output=True, text=True)
    combined = (result.stdout or "") + (result.stderr or "")
    summary = test_summary(combined)
    if result.returncode != 0:
        reverted = git(cwd, "apply", "-R", input_text=diff_text, run=git_run)
        if reverted.returncode != 0:
            out(
                "测试没有通过，而且撤销补丁也失败了——真实仓库现在处于补丁已应用但未提交的状态，"
                f"需要你手动处理（`git -C {cwd} status`）。撤销时的错误：{reverted.stderr.strip()}"
            )
            return 1
        tail = "\n".join(combined.splitlines()[-20:])
        out(f"测试没有通过，已撤销刚才应用的补丁（真实仓库恢复原样，什么都没提交）。测试输出的最后几行：\n{tail}")
        return 1

    staged = git(cwd, "add", "-A", run=git_run)
    if staged.returncode != 0:
        out(f"没有改动：补丁已应用且测试通过，但暂存改动失败（{staged.stderr.strip()}）——真实仓库现在是脏的，需要你手动处理。")
        return 1
    message = commit_message(task_id, task, paths, summary)
    committed = git(cwd, "commit", "-q", "-m", message, run=git_run)
    if committed.returncode != 0:
        out(f"没有改动：补丁已应用且测试通过，但提交失败（{committed.stderr.strip()}）——真实仓库现在是脏的，需要你手动处理。")
        return 1
    commit_hash = git(cwd, "rev-parse", "HEAD", run=git_run).stdout.strip()
    out(f"已提交：{commit_hash[:12]}（{len(paths)} 个文件{'，' + summary if summary else ''}）。")
    try:
        par_cli(["task", "close", task_id], cwd=cwd, run=par_run)
    except Refused as exc:
        out(f"提交已经成功，但没能 close 任务：{exc}")
        return 1
    out(f"任务 {task_id} 已 close（仍不会碰 STATE.md，那一步还是你/协调者单独做）。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("task_id", help="一个状态是 ACCEPTED 的 par 任务编号，例如 par_runtime-0003")
    p.add_argument("--apply", action="store_true", help="实际应用补丁、跑全量测试、提交；不加时只试运行，不改动任何文件")
    p.add_argument("--repo", default=str(REPO), help="真实仓库的路径（默认是这个脚本所在仓库的根目录）")
    return p


def main(argv: "Optional[list[str]]" = None) -> int:
    args = build_parser().parse_args(argv)
    return land(args.task_id, cwd=Path(args.repo), apply_flag=args.apply)


if __name__ == "__main__":
    sys.exit(main())
