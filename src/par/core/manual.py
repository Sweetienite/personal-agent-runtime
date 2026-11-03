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
from typing import Any, Optional

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

ENVIRONMENT_NOTE = (
    "You are a coding assistant inside a desktop application, working on a private copy of the project that the person opened for you. "
    f"First, open `{MARKER}` in this folder's root and check its task_id against the task number in this prompt's title. If the file is "
    "missing, or its task_id does not match, you are not looking at the right folder: stop immediately, say so, and do not touch any files - "
    "do not fall back to a project you already had open. "
    "Change files only under the allowed paths above. Do not run git commands, do not push or publish anything, do not install packages, do not delete "
    "anything outside the allowed paths, and do not open or edit any file outside this folder. You cannot run the project's checks for the person: the "
    "Kernel runs the checks listed above afterwards.")


def _readme(*, task_id: str, title: str, to: str, folder: Path, contract: Contract, has_workspace: bool, relay: bool, note: str,
           project_name: str) -> str:
    app = APPS.get(to, to)
    allowed = "、".join(contract.allowed_paths) if contract.allowed_paths else "（无：这是只读/文字任务）"
    lines = [f"# 人工交接包：{task_id}", "", f"任务：{title}", f"交给：{app}（由你在它的界面里操作；Runtime 不会替你启动或操控这个应用）", ""]
    if note:
        lines += [f"备注：{note}", ""]
    lines += ["## 先看这一条：数据会去哪里",
              f"你在 {app} 里打开 `workspace/` 文件夹后，应用会把里面的文件内容发送给它自己的云端模型服务。Runtime 已把疑似敏感的文件（.env、密钥、账户文件等）"
              "排除在这个副本之外，但**是否可以把这个项目交给该厂商，由你决定**。" if has_workspace else
              "这是文字任务，没有项目文件需要打开；只有 PROMPT.md 里的文字会被你粘贴给应用。", ""]
    lines += ["## 步骤"]
    n = 1
    if has_workspace:
        lines += [f"{n}. 在 {app} 里打开这个文件夹（不要在已有的项目/会话里继续，哪怕看着熟悉——那不是这份副本）：",
                  f"   `{folder / 'workspace'}`"]
        if to == "workbuddy":
            lines += [f"   WorkBuddy 按「空间」工作，一个空间是对某个真实文件夹的长期绑定；打开一个新路径不会自动新建或切换空间"
                      f"（实测 `open -a WorkBuddy 路径` 没有任何效果）。所以**不能**直接在侧边栏里点一个已有的空间继续，哪怕它就叫 "
                      f"`{project_name}`——那绑定的是真实项目，不是这份副本。正确做法：点「新建任务」→ 左下角「选择工作空间」→"
                      f"「打开本地文件夹」→ 在弹出的系统对话框里选中上面这个文件夹。"]
        n += 1
    lines += [f"{n}. 打开 `PROMPT.md`，把全文粘贴给应用里的 Agent。"]
    n += 1
    if contract.writes:
        lines += [f"{n}. 只允许修改这些路径：{allowed}。其他文件保持不动。"]
        n += 1
    if has_workspace:
        lines += [f"{n}. 完成后，在终端运行 `par task import {task_id} --from \"{folder / 'workspace'}\"`；"
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
              "- 应用在文件夹里自动生成的配置目录（如 `.trae/`、`.workbuddy/`）会被忽略，不算越界。",
              "- 应用或模型自己说“测试通过”不算数；Runtime 只相信自己运行的检查。",
              "- 这个交接包是任务级的临时文件，不是项目的 STATE，也不会被写进 Harness。", ""]
    return "\n".join(lines)


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
        return [{"export": e["data"].get("export"), "to": e["data"].get("to"), "workspace": bool(e["data"].get("workspace")), "at": e["ts"]}
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
        prompt = build_prompt(contract, task_id=task_id, attempt=n, context_text=context_text, workspace_has_files=contract.risk != "R0",
                              handoff_text=seed.section if seed else None).rstrip("\n") + "\n\n## Environment\n" + ENVIRONMENT_NOTE + "\n"
        handoff_dir = self.data_dir / "tasks" / task_id / "handoff"
        private_dirs(handoff_dir, self.data_dir)
        index = 1
        while (handoff_dir / f"manual-{index}").exists():
            index += 1
        name = f"manual-{index}"
        folder = handoff_dir / name
        folder.mkdir(mode=0o700)
        has_workspace = contract.risk != "R0"
        try:
            if has_workspace:
                self._export_workspace(folder, project.path, task["base"].get("commit"), contract, seed, task_id, name)
            write_private(folder / "PROMPT.md", prompt)
            write_private(folder / "README.md", _readme(task_id=task_id, title=contract.title, to=to, folder=folder, contract=contract,
                                                         has_workspace=has_workspace, relay=seed is not None, note=note,
                                                         project_name=project.path.name))
            manifest_doc = {"schema": 1, "kind": "manual_handoff", "task_id": task_id, "export": name, "to": to, "attempt": n,
                            "risk": contract.risk, "base": task["base"].get("commit"), "allowed_paths": list(contract.allowed_paths),
                            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "workspace": has_workspace,
                            "relay_from": seed.from_attempt if seed else None}
            write_private(folder / "manifest.json", json.dumps(manifest_doc, indent=2, sort_keys=True, ensure_ascii=False))
        except BaseException:
            remove_scratch(folder, handoff_dir)                    # a half-written packet must not look like a real one
            raise
        with self.store.tx():
            self.store.add_event("HANDOFF_EXPORTED", task_id=task_id, reason=f"prepared a manual hand-off to {to} ({name})",
                                 data={"export": name, "to": to, "prompt_sha256": manifest_doc["prompt_sha256"], "workspace": has_workspace})
        if T(self._require(task_id)["status"]) == T.QUEUED:       # nobody else may start this task while a person is working on it
            self.pause(task_id, reason=f"handed off to {to}; waiting for the result (par task import {task_id} ...)")
        return {"task_id": task_id, "export": name, "folder": str(folder), "workspace": str(folder / "workspace") if has_workspace else None,
                "prompt": str(folder / "PROMPT.md"), "readme": str(folder / "README.md"), "to": to, "status": self._require(task_id)["status"]}

    def _export_workspace(self, folder: Path, source: Path, base: "Optional[str]", contract: Contract, seed: Any, task_id: str, name: str) -> None:
        build = folder / "_build"
        build.mkdir(mode=0o700)
        prepared = ws.prepare(self.git, source=source, base_commit=base, attempt_dir=build, risk=contract.risk, with_git=False)
        target = folder / "workspace"
        os.rename(prepared.workspace, target)
        remove_scratch(build, folder)
        if seed is not None:
            ws.apply_seed(self.git, target, seed.patch)
        write_private(target / MARKER, json.dumps({"schema": 1, "task_id": task_id, "export": name, "base": base}, sort_keys=True))
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
            incoming["source"] = self._checked_source(Path(source), task_id, known)
        elif patch is not None:
            incoming["patch"] = self._read_small(Path(patch), MAX_PATCH_BYTES, "the patch")
        else:
            incoming["text"] = self._read_small(Path(text), MAX_TEXT_BYTES, "the text answer").decode("utf-8", errors="replace")
        project = self.projects.get(task["project_id"])
        if project is None:
            raise Refused("unknown_project", f"project {task['project_id']!r} is no longer registered")
        self._to_queued(task_id)
        task = self._require(task_id)
        if task["status"] != T.QUEUED.value:
            raise NotAllowed("not_queued", f"{task_id} is {task['status']}; the hand-off can be imported once the task is queued (is a permission still pending?)")
        missing = self._missing_approvals(task, contract)
        if missing:
            raise Refused("missing_approval", [f"{task_id} has never been approved for: {', '.join(missing)}"])
        n = task["attempt_count"] + 1
        prompt = self._exported_prompt(task_id, known, incoming.get("source"))
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

    def _exported_prompt(self, task_id: str, known: "dict[str, Any]", source: "Optional[Path]") -> str:
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

    def _checked_source(self, source: Path, task_id: str, known: "dict[str, Any]") -> Path:
        from par.core.kernel import Refused
        if source.is_symlink() or not source.is_dir():
            raise Refused("bad_source", "the result folder must be a real directory (not a link)")
        real = Path(os.path.realpath(source))
        data = Path(os.path.realpath(self.data_dir))
        if real == data or data in real.parents and not (data / "tasks" / task_id / "handoff") in real.parents:
            raise Refused("bad_source", "the result folder may not be inside the Runtime's own data directory (use the exported workspace folder)")
        marker_path = real / MARKER
        try:
            if marker_path.is_symlink() or not marker_path.is_file() or marker_path.stat().st_size > 4096:
                raise ValueError
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if marker.get("task_id") != task_id or marker.get("export") not in known or not EXPORT_NAME.match(str(marker.get("export"))):
                raise ValueError
        except (OSError, ValueError):
            raise Refused("wrong_folder", f"this folder is not an exported hand-off workspace of {task_id} (its {MARKER} is missing or belongs to another task); "
                                          "bring back the exported folder, or use --patch") from None
        return real

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
