"""Manual hand-off (plan 5.5): a person does the task in a desktop app (WorkBuddy, TRAE ...) and brings the result back to be verified.

``handoff_export`` writes a packet for the person - the exact prompt, a private copy of the project to open in the app, instructions - and
changes no state except holding a queued task. ``handoff_import`` takes what the person brings back (the folder, a patch, or a text answer),
treats it exactly like a Worker's output (nothing is believed: the Kernel compares files itself, enforces the boundary and runs the contract's
checks) and settles the attempt as provider ``manual_handoff``. No program of the app is ever started by the Runtime, no credential is touched,
and nothing is applied to the real project.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional

from par.core.attempt import private_dirs, remove_scratch, write_private
from par.core.contract import Contract
from par.core.prompt import build_prompt
from par.core.states import TaskStatus as T
from par.execution import gitops, workspace as ws

MARKER = ".par-handoff.json"
LABEL = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
EXPORT_NAME = re.compile(r"manual-[0-9]{1,4}\Z")
MAX_TEXT_BYTES = 64 * 1024
MAX_PATCH_BYTES = 8 * 1024 * 1024
EXPORTABLE = frozenset({T.QUEUED, T.PAUSED, T.FAILED, T.INTERRUPTED, T.RATE_LIMITED, T.QUOTA_EXHAUSTED, T.AUTH_REQUIRED, T.BLOCKED_NO_WORKER})
APPS = {"workbuddy": "WorkBuddy", "trae": "TRAE", "codebuddy": "CodeBuddy", "qoder": "Qoder"}
MODE_WORKSPACE = "workspace"      # the task changes files: the app edits a copy, and a folder (or patch) comes back
MODE_TEXT = "text"                # the task answers in words: only a text answer comes back

FOLDER_CHECK = (
    f"## Before you start\nOpen `{MARKER}` in this folder's root and compare its `task_id` with the task number in the title above. "
    "If the file is missing, or the id does not match, you are not looking at the right folder: stop immediately, say so, and touch no files - "
    "do not fall back to a project you already had open."
)

ENVIRONMENT_NOTE = (
    "You are a coding assistant inside a desktop application, working on a private copy of the project that the person opened for you. "
    "Change files only under the allowed paths above; everything else in this folder is read-only. Do not run git commands, do not push or "
    "publish anything, do not install packages, do not delete anything outside the allowed paths, and do not open or edit any file outside this "
    "folder. The Kernel runs the checks listed above itself after the person brings the result back: do not claim you ran them, and never change "
    "a test or a check to make it pass."
)


def _readme(*, task_id: str, export: str, title: str, to: str, folder: Path, contract: Contract, mode: str,
           has_workspace: bool, relay: bool, note: str, project_name: str, base_commit: "str | None") -> str:
    app = APPS.get(to, to)
    allowed = "、".join(contract.allowed_paths) if contract.allowed_paths else "（无：这是只读/文字任务）"
    workspace = folder / "workspace"
    short_base = (base_commit or "")[:12]
    lines = [f"# 人工交接包：{task_id}（{export}）", "",
             f"任务：{title}",
             f"交给：{app}（由你在它的界面里操作；Runtime 不会启动或操控这个应用）",
             f"模式：{mode}" + ("（会改文件：只收文件夹或补丁）" if mode == MODE_WORKSPACE else "（只收文字答案）") +
             (f"    基线：{short_base}" if short_base and mode == MODE_WORKSPACE else ""), ""]
    if note:
        lines += [f"备注：{note}", ""]

    lines += ["## 三句话"]
    n = 1
    if has_workspace:
        lines += [f"{n}. 在 {app} 里打开 `workspace/` 这个**新文件夹**（不是你平时用的真实项目）。"]
        n += 1
    lines += [f"{n}. 把 `PROMPT.md` 全文粘贴给它的 Agent，等它明确结束。"]
    n += 1
    if mode == MODE_WORKSPACE:
        lines += [f"{n}. 回到终端运行：`par task import {task_id} --from \"{workspace}\"`（只给了补丁就用 `--patch 补丁文件`）。"]
    else:
        lines += [f"{n}. 把它的文字答案存成一个纯文本文件，运行：`par task import {task_id} --text 答案文件`。"]
    lines += ["", "Runtime 会自己对比文件、检查边界、运行合同里的检查；是否接受由你决定。", ""]

    lines += ["## 先看这一条：数据会去哪里",
              f"你在 {app} 里打开 `workspace/` 后，应用会把里面的文件内容发送给它自己的云端模型服务。Runtime 已把疑似敏感的文件（.env、密钥、账户文件等）"
              "排除在这个副本之外，但**是否可以把这个项目交给该厂商，由你决定**。" if has_workspace else
              "这是文字任务，没有项目文件需要打开；只有 PROMPT.md 里的文字会被你粘贴给应用。", ""]

    lines += ["## 步骤"]
    n = 1
    if has_workspace:
        lines += [f"{n}. 在 {app} 里打开这个文件夹（不要在已有的项目/会话里继续，哪怕看着熟悉——那不是这份副本）：",
                  f"   `{workspace}`"]
        if to == "workbuddy":
            lines += ["   以下菜单名按 WorkBuddy 桌面版 2026-10 的界面记录；版本更新后可能改名，认准「打开本地文件夹」这个动作即可。",
                      f"   WorkBuddy 按「空间」工作，一个空间是对某个真实文件夹的长期绑定；打开一个新路径不会自动新建或切换空间"
                      f"（实测 `open -a WorkBuddy 路径` 对已在运行的实例没有任何效果）。所以**不能**直接在侧边栏里点一个已有的空间继续，哪怕它就叫 "
                      f"`{project_name}`——那绑定的是真实项目，不是这份副本。正确做法：点「新建任务」→ 左下角「选择工作空间」→"
                      f"「打开本地文件夹」→ 在弹出的系统对话框里选中上面这个文件夹。",
                      "   Runtime 不会替你点这些菜单，也不会替你启动或操控 WorkBuddy。"]
        n += 1
    lines += [f"{n}. 打开 `PROMPT.md`，把全文粘贴给应用里的 Agent。"]
    n += 1
    if mode == MODE_WORKSPACE:
        lines += [f"{n}. 只允许修改这些路径：{allowed}。其他文件保持不动。"]
        n += 1
    elif has_workspace:
        lines += [f"{n}. 这个副本只是给它读的参考：不要修改里面的任何文件。即使改了，Runtime 也只收文字答案，文件改动不会被采用。"]
        n += 1
    if mode == MODE_WORKSPACE:
        lines += [f"{n}. 完成后，在终端运行 `par task import {task_id} --from \"{workspace}\"`；"
                  f"或者应用只给了补丁的话用 `par task import {task_id} --patch 补丁文件`。"]
    else:
        lines += [f"{n}. 完成后，把应用的文字答案存成文件，在终端运行：`par task import {task_id} --text 答案文件`"]
    n += 1
    lines += [f"{n}. Runtime 会自己对比文件、检查边界、运行合同里的检查；结果见 `par task result {task_id}`，是否接受由你决定。", ""]
    if relay:
        lines += ["## 说明", "这个副本已经包含上一次尝试**经 Kernel 验证过的改动**（见 PROMPT.md 的 Handoff 一节）；请在此基础上继续，不要从头来。", ""]
    lines += ["## 注意",
              "- 不管用哪个应用：动手之前确认一眼你面前的是上面那个新文件夹，不是你平时已经在用的那个项目——名字像甚至一样都不算数。",
              "- 不要让应用对真实项目目录做任何修改；这份副本之外的文件 Runtime 一概不认。",
              "- 应用在文件夹里**新生成**的配置目录（如 `.trae/`、`.workbuddy/`、`.codebuddy/`、`.cursor/`）里的**新增文件**会被忽略，不算越界；"
              "这些目录里**原本就有的文件被修改或删除，照常算越界**。忽略规则只对新增文件生效，不会给已有文件打掩护。",
              "- Runtime 不监视这个文件夹，也不会猜应用是否结束；只有你运行 `par task import` 时它才读取结果。",
              "- 应用或模型自己说“测试通过”不算数；Runtime 只相信自己运行的检查。",
              "- 这个交接包是任务级的临时文件，不是项目的 STATE，也不会被写进 Harness。", ""]
    return "\n".join(lines)


def _next_actions(*, task_id: str, app: str, folder: Path, mode: str, has_workspace: bool) -> "list[str]":
    """The person's remaining steps, in order, each short enough to copy. Paths are absolute on purpose."""
    workspace = folder / "workspace"
    steps: "list[str]" = []
    if has_workspace:
        steps.append(f"open in {app}: {workspace}")
    steps.append(f"paste the whole prompt: {folder / 'PROMPT.md'}")
    if mode == MODE_WORKSPACE:
        steps.append(f"when the app has finished: par task import {task_id} --from \"{workspace}\"")
    else:
        steps.append(f"save the answer to a plain file, then: par task import {task_id} --text <file>")
    steps.append(f"review what the Kernel measured: par task result {task_id}")
    return steps


class ManualHandoffMixin:
    """Methods of :class:`par.core.kernel.Kernel` (kept in their own file: the Kernel is long enough)."""

    # ------------------------------------------------------------------------------------------------------
    def handoff_blockers(self, task_id: str) -> "list[str]":
        """Why a hand-off cannot be prepared for this task right now (empty: it can). The console shows these as the reason of a disabled button."""
        task = self._require(task_id)
        status = T(task["status"])
        if status not in EXPORTABLE:
            return [f"the task is {status.value}; a hand-off can be prepared while it is queued, paused or stopped"]
        missing = self._missing_approvals(task, self._contract_of(task))
        if missing:
            return [f"approve {', '.join(m.lower().replace('_', ' ') for m in missing)} first: nothing is handed to anyone before it is granted"]
        return []

    def handoff_exports(self, task_id: str) -> "list[dict[str, Any]]":
        """The hand-offs prepared for a task, oldest first: [{"export": "manual-1", "to": "workbuddy", "workspace": bool}]."""
        self._require(task_id)
        return [{"export": e["data"].get("export"), "to": e["data"].get("to"), "mode": e["data"].get("mode"),
                 "workspace": bool(e["data"].get("workspace")), "at": e["ts"]}
                for e in self.store.events(task_id) if e["type"] == "HANDOFF_EXPORTED"]

    def handoff_workspace(self, task_id: str, export: "str | None" = None) -> Path:
        """The exported workspace folder of one hand-off (the latest by default). Raises Refused when there is none."""
        from par.core.kernel import Refused
        exports = [e for e in self.handoff_exports(task_id) if e["workspace"]]
        if export is not None:
            exports = [e for e in exports if e["export"] == export]
        if not exports or not EXPORT_NAME.match(str(exports[-1]["export"])):
            raise Refused("no_handoff", "no hand-off with a workspace has been prepared for this task (prepare one first)")
        return self.data_dir / "tasks" / task_id / "handoff" / exports[-1]["export"] / "workspace"

    def handoff_folder(self, task_id: str, export: "str | None" = None) -> Path:
        """One hand-off's own folder (the latest by default; has a workspace or not). Raises Refused when there is none."""
        from par.core.kernel import Refused
        exports = self.handoff_exports(task_id)
        if export is not None:
            exports = [e for e in exports if e["export"] == export]
        if not exports or not EXPORT_NAME.match(str(exports[-1]["export"])):
            raise Refused("no_handoff", "no hand-off has been prepared for this task (prepare one first)")
        return self.data_dir / "tasks" / task_id / "handoff" / exports[-1]["export"]

    def handoff_prompt(self, task_id: str, export: "str | None" = None) -> str:
        """The exact text of one hand-off's PROMPT.md (the latest by default), for a person to copy elsewhere. Raises Refused when there is none."""
        return (self.handoff_folder(task_id, export) / "PROMPT.md").read_text(encoding="utf-8")

    def handoff_export(self, task_id: str, *, to: str = "manual", note: str = "") -> "dict[str, Any]":
        from par.core.kernel import NotAllowed, Refused
        if not LABEL.match(to or ""):
            raise Refused("bad_label", "the name of the application must be lowercase letters, digits, - or _ (at most 32 characters)")
        note = (note or "").strip()[:200]
        task = self._require(task_id)
        status = T(task["status"])
        if status not in EXPORTABLE:
            raise NotAllowed("not_exportable", f"{task_id} is {status.value}; a hand-off can be prepared for: {', '.join(sorted(s.value for s in EXPORTABLE))}")
        contract = self._contract_of(task)
        missing = self._missing_approvals(task, contract)
        if missing:
            raise Refused("missing_approval", [f"{task_id} has never been approved for: {', '.join(missing)}; approve it before handing it to anyone"])
        project = self.projects.get(task["project_id"])
        if project is None:
            raise Refused("unknown_project", f"project {task['project_id']!r} is no longer registered")
        context = self._context(project)
        manifest = context.manifest()
        drift = self._check_drift(task, manifest)
        if drift is not None:
            raise NotAllowed("base_drift", drift.reason)
        n = task["attempt_count"] + 1
        seed = self._relay_seed(task, contract, n)
        context_text = context.render(manifest)
        has_workspace = contract.risk != "R0"
        mode = MODE_WORKSPACE if contract.writes else MODE_TEXT
        prompt = build_prompt(contract, task_id=task_id, attempt=n, context_text=context_text, workspace_has_files=has_workspace,
                              handoff_text=seed.section if seed else None,
                              preface=FOLDER_CHECK if has_workspace else None).rstrip("\n") + "\n\n## Environment\n" + ENVIRONMENT_NOTE + "\n"
        handoff_dir = self.data_dir / "tasks" / task_id / "handoff"
        private_dirs(handoff_dir, self.data_dir)
        index = 1
        while (handoff_dir / f"manual-{index}").exists():
            index += 1
        name = f"manual-{index}"
        folder = Path(os.path.abspath(handoff_dir / name))
        folder.mkdir(mode=0o700)
        base_commit = task["base"].get("commit")
        try:
            if has_workspace:
                self._export_workspace(folder, project.path, base_commit, contract, seed, task_id, name, mode)
            write_private(folder / "PROMPT.md", prompt)
            write_private(folder / "README.md", _readme(task_id=task_id, export=name, title=contract.title, to=to, folder=folder,
                                                         contract=contract, mode=mode, has_workspace=has_workspace,
                                                         relay=seed is not None, note=note, project_name=project.path.name,
                                                         base_commit=base_commit))
            manifest_doc = {"schema": 1, "kind": "manual_handoff", "task_id": task_id, "export": name, "to": to, "attempt": n,
                            "mode": mode, "risk": contract.risk, "base": base_commit, "allowed_paths": list(contract.allowed_paths),
                            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "workspace": has_workspace,
                            "relay_from": seed.from_attempt if seed else None}
            write_private(folder / "manifest.json", json.dumps(manifest_doc, indent=2, sort_keys=True, ensure_ascii=False))
        except BaseException:
            remove_scratch(folder, handoff_dir)                    # a half-written packet must not look like a real one
            raise
        with self.store.tx():
            self.store.add_event("HANDOFF_EXPORTED", task_id=task_id, reason=f"prepared a manual hand-off to {to} ({name})",
                                 data={"export": name, "to": to, "mode": mode, "prompt_sha256": manifest_doc["prompt_sha256"],
                                       "workspace": has_workspace})
        if T(self._require(task_id)["status"]) == T.QUEUED:       # nobody else may start this task while a person is working on it
            self.pause(task_id, reason=f"handed off to {to}; waiting for the result (par task import {task_id} ...)")
        prompt_path, readme_path, manifest_path = folder / "PROMPT.md", folder / "README.md", folder / "manifest.json"
        workspace_path = folder / "workspace" if has_workspace else None
        return {"task_id": task_id, "export_id": name, "export": name, "to": to, "mode": mode,
                "status": self._require(task_id)["status"], "folder": str(folder),
                "workspace_absolute_path": str(workspace_path) if workspace_path else None,
                "workspace": str(workspace_path) if workspace_path else None,
                "prompt_path": str(prompt_path), "prompt": str(prompt_path),
                "readme_path": str(readme_path), "readme": str(readme_path),
                "manifest_path": str(manifest_path), "base_commit": base_commit,
                "allowed_paths": list(contract.allowed_paths),
                "next_actions": _next_actions(task_id=task_id, app=APPS.get(to, to), folder=folder, mode=mode, has_workspace=has_workspace)}

    def _export_workspace(self, folder: Path, source: Path, base: "Optional[str]", contract: Contract, seed: Any, task_id: str, name: str,
                          mode: str) -> None:
        build = folder / "_build"
        build.mkdir(mode=0o700)
        prepared = ws.prepare(self.git, source=source, base_commit=base, attempt_dir=build, risk=contract.risk, with_git=False)
        target = folder / "workspace"
        os.rename(prepared.workspace, target)
        remove_scratch(build, folder)
        if seed is not None:
            ws.apply_seed(self.git, target, seed.patch)
        write_private(target / MARKER, json.dumps({"schema": 1, "task_id": task_id, "export": name, "base": base, "mode": mode},
                                                  sort_keys=True))
        os.chmod(target, 0o700)

    # ------------------------------------------------------------------------------------------------------
    def handoff_import(self, task_id: str, *, source: "Path | None" = None, patch: "Path | None" = None, text: "Path | None" = None,
                       worker: "str | None" = None, note: str = "") -> Any:
        from par.core.kernel import NotAllowed, Refused
        given = [x for x in (source, patch, text) if x is not None]
        if len(given) != 1:
            raise Refused("one_input", "give exactly one of: the result folder, a patch file, a text answer")
        task = self._require(task_id)
        contract = self._contract_of(task)
        if contract.writes and text is not None:
            raise Refused("needs_files", "a file-changing task needs the changed folder or a patch, not a text answer")
        if not contract.writes and (source is not None or patch is not None):
            raise Refused("needs_text", "this task changes no files: bring back a text answer")
        if contract.risk == "R0" and source is not None:
            raise Refused("needs_text", "this task has no project files: bring back a text answer")
        exports = [e for e in self.store.events(task_id) if e["type"] == "HANDOFF_EXPORTED"]
        known = {e["data"].get("export"): e["data"] for e in exports}
        label = worker or (exports[-1]["data"].get("to") if exports else None) or "manual"
        if not LABEL.match(label):
            raise Refused("bad_label", "the name of the application must be lowercase letters, digits, - or _ (at most 32 characters)")
        incoming: "dict[str, Any]" = {}
        if source is not None:
            incoming["source"], incoming["_base"] = self._checked_source(Path(source), task, known)
        elif patch is not None:
            incoming["patch"] = self._read_small(Path(patch), MAX_PATCH_BYTES, "the patch")
        else:
            incoming["text"] = self._read_small(Path(text), MAX_TEXT_BYTES, "the text answer").decode("utf-8", errors="replace")
        project = self.projects.get(task["project_id"])
        if project is None:
            raise Refused("unknown_project", f"project {task['project_id']!r} is no longer registered")
        self._to_queued(task_id)
        task = self._require(task_id)
        if source is not None and incoming.get("_base") != task["base"].get("commit"):
            raise Refused("stale_base", f"the project's base changed between the hand-off and now ({incoming.get('_base')} -> "
                                        f"{task['base'].get('commit')}); prepare a new hand-off against the current base")
        if task["status"] != T.QUEUED.value:
            raise NotAllowed("not_queued", f"{task_id} is {task['status']}; the hand-off can be imported once the task is queued (is a permission still pending?)")
        missing = self._missing_approvals(task, contract)
        if missing:
            raise Refused("missing_approval", [f"{task_id} has never been approved for: {', '.join(missing)}"])
        incoming.pop("_base", None)
        n = task["attempt_count"] + 1
        prompt = self._exported_prompt(task_id, incoming.get("source"))
        route = {"worker": label, "provider": "manual_handoff", "model": "manual", "source": "manual hand-off",
                 "explain": f"the result was produced by a person using {APPS.get(label, label)} and brought back by hand",
                 "policy": task["profile"].get("worker_failure_policy", "ask"), "snapshot_digest": task["profile"].get("digest")}
        started_at = self._start(task, n, contract, route, None)
        with self.store.tx():                                      # there is no process to wait for: the person's work is already done
            self.store.update_attempt(task_id, n, state="RUNNING")
            current = self.store.get_task(task_id)
            if current and current["status"] == T.STARTING.value:
                self.store.set_status(task_id, T.RUNNING, reason=f"a result from {label} is being checked", attempt_n=n)
            self.store.add_event("HANDOFF_IMPORTED", task_id=task_id, attempt_n=n, reason=f"result brought back from {label}",
                                 data={"kind": next(iter(incoming)), "note": (note or "")[:200]})
        return self._run_attempt(task_id, n, contract, project, route, prompt, started_at, None, manual=incoming)

    # ---- helpers -----------------------------------------------------------------------------------------
    def _to_queued(self, task_id: str) -> None:
        from par.core.kernel import NotAllowed
        status = T(self._require(task_id)["status"])
        if status == T.QUEUED:
            return
        if status == T.PAUSED:
            self.resume(task_id)
        elif self._retryable(task_id, status):
            self.retry(task_id)
        else:
            raise NotAllowed("not_importable", f"{task_id} is {status.value}; a hand-off result can be imported into a queued, paused or stopped task")

    def _exported_prompt(self, task_id: str, source: "Optional[Path]") -> str:
        """The prompt the person was given (so the record shows what they worked from); a patch or text import without an export has a short stand-in."""
        if source is not None:
            marker = json.loads((source / MARKER).read_text(encoding="utf-8"))
            prompt_file = self.data_dir / "tasks" / task_id / "handoff" / marker["export"] / "PROMPT.md"
            try:
                if not prompt_file.is_symlink() and prompt_file.is_file():
                    return prompt_file.read_text(encoding="utf-8")
            except OSError:
                pass
        return f"(manual hand-off: a person produced this result outside the Runtime; task {task_id})\n"

    def _checked_source(self, source: Path, task: "Mapping[str, Any]", known: "dict[str, Any]") -> "tuple[Path, Optional[str]]":
        """Check that ``source`` is the very workspace this Runtime exported for this task, on this task's base.

        The marker in the folder is not trusted on its own: it must agree with the ledger (task and export), with the task's
        recorded base, and with the packet the Runtime wrote under its own data directory. A folder that was copied somewhere
        else is refused by name, because a copy is exactly what a stale result looks like."""
        from par.core.kernel import Refused
        task_id = task["task_id"]
        if source.is_symlink() or not source.is_dir():
            raise Refused("bad_source", "the result folder must be a real directory (not a link)")
        real = Path(os.path.realpath(source))
        data = Path(os.path.realpath(self.data_dir))
        handoff_root = Path(os.path.realpath(data / "tasks" / task_id / "handoff"))
        if real == data or (data in real.parents and handoff_root not in real.parents):
            raise Refused("bad_source", "the result folder may not be inside the Runtime's own data directory (use the exported workspace folder)")
        marker_path = real / MARKER
        try:
            if marker_path.is_symlink() or not marker_path.is_file() or marker_path.stat().st_size > 4096:
                raise ValueError
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            export = marker.get("export")
            if marker.get("task_id") != task_id or export not in known or not EXPORT_NAME.match(str(export)):
                raise ValueError
        except (OSError, ValueError):
            raise Refused("wrong_folder", f"this folder is not an exported hand-off workspace of {task_id} (its {MARKER} is missing or belongs to another task); "
                                          "bring back the exported folder, or use --patch") from None
        base = task["base"].get("commit")
        if marker.get("base") != base:
            raise Refused("stale_base", "this folder was exported for a different base commit than the task runs on now; "
                                        "prepare a new hand-off, or bring the result back as --patch against the current base")
        packet = data / "tasks" / task_id / "handoff" / export
        try:
            if (packet / "manifest.json").is_symlink() or (packet / "manifest.json").stat().st_size > 64 * 1024:
                raise ValueError
            doc = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
            prompt_file = packet / "PROMPT.md"
            if (doc.get("task_id") != task_id or doc.get("export") != export or doc.get("base") != base
                    or not doc.get("workspace") or prompt_file.is_symlink() or not prompt_file.is_file()
                    or prompt_file.stat().st_size > 2 * 1024 * 1024
                    or hashlib.sha256(prompt_file.read_bytes()).hexdigest() != doc.get("prompt_sha256")):
                raise ValueError
        except (OSError, ValueError):
            raise Refused("old_packet", f"the hand-off packet behind this folder is incomplete, modified or from an older version ({export}); "
                                        "prepare a new hand-off and import that folder") from None
        if real != Path(os.path.realpath(packet / "workspace")):
            raise Refused("stale_copy", "this folder is not the workspace the Runtime exported for this hand-off (a copy or a moved folder); "
                                        "import the exported folder itself, or bring the result back as --patch against the base commit")
        latest = [d for d in known.values() if d.get("workspace")]
        if latest and latest[-1].get("export") != export:
            raise Refused("stale_export", f"{export} is not the newest hand-off of {task_id} ({latest[-1].get('export')} is); "
                                          f"if you worked in {export}, bring it back as a patch, or prepare a new hand-off and work there")
        return real, base

    @staticmethod
    def _read_small(path: Path, limit: int, what: str) -> bytes:
        from par.core.kernel import Refused
        try:
            if path.is_symlink() or not path.is_file():
                raise Refused("bad_input", f"{what} must be a regular file")
            if path.stat().st_size > limit:
                raise Refused("bad_input", f"{what} is larger than {limit // 1024} KiB")
            return path.read_bytes()
        except OSError:
            raise Refused("bad_input", f"{what} cannot be read") from None
