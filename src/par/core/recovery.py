"""Crash recovery: settle attempts whose Kernel is gone, without ever starting a second writer."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from par.core.attempt import private_dirs, write_private
from par.core.clock import parse_iso
from par.core.states import ACTIVE, TaskStatus as T
from par.execution import subprocesses as sp


@dataclass(frozen=True)
class RecoveryAction:
    task_id: str
    attempt: int
    action: str             # orphaned | alive | terminated | skipped
    detail: str


class RecoveryMixin:
    """Methods of :class:`par.core.kernel.Kernel` (kept apart: the Kernel is long enough)."""

    def recover(self, task_id: "str | None" = None, *, terminate: bool = False) -> "list[RecoveryAction]":
        """Settle attempts whose Kernel is gone. Never starts a second writer; never kills what it cannot identify."""
        actions: "list[RecoveryAction]" = []
        for attempt in self.store.unfinished_attempts():
            if task_id is not None and attempt["task_id"] != task_id:
                continue
            if attempt["kernel_id"] == self.kernel_id:
                continue
            age = (self.clock.now() - parse_iso(attempt["heartbeat_at"])).total_seconds()
            if age < self.settings.stale_after:
                if task_id is not None:
                    actions.append(RecoveryAction(attempt["task_id"], attempt["n"], "skipped",
                                                  f"its Kernel ({attempt['kernel_id']}) is alive (heartbeat {age:.0f}s ago)"))
                continue
            actions.append(self._recover_one(attempt, terminate))
        return actions

    def _recover_one(self, attempt: "dict[str, Any]", terminate: bool) -> RecoveryAction:
        task_id, n = attempt["task_id"], attempt["n"]
        pid, pgid = attempt["pid"], attempt["pgid"]
        alive = pgid is not None and sp.group_alive(pgid)
        leader_ok = bool(pid and attempt["proc_identity"] and sp.pid_alive(pid) and sp.process_identity(pid) == attempt["proc_identity"])
        if alive:
            if terminate and leader_ok:
                if not sp.terminate_group(pgid, grace=self.settings.grace):
                    return RecoveryAction(task_id, n, "alive", f"process group {pgid} did not stop")
                alive = False
            else:
                with self.store.tx():
                    task = self.store.get_task(task_id)
                    if task and T(task["status"]) in ACTIVE:
                        self.store.set_status(task_id, T.RECOVERY_REQUIRED, attempt_n=n,
                                              reason="its Kernel is gone but the Worker process is still running; no second writer will start")
                    self.store.add_event("RECOVERY_ALIVE", task_id=task_id, attempt_n=n, reason="orphaned process still alive",
                                         data={"pid": pid, "pgid": pgid, "leader_verified": leader_ok})
                how = (f"the leader is verified; run `par task recover {task_id} --terminate`" if leader_ok
                       else f"the leader is gone and the rest cannot be verified: check `ps -g {pgid}`")
                return RecoveryAction(task_id, n, "alive", f"process group {pgid} is still running; {how}")
        task = self._require(task_id)
        contract = self._contract_of(task)
        project = self.projects.get(task["project_id"])
        route = attempt["config"]["route"]
        capture = None
        if attempt["workspace_path"] and project is not None and Path(attempt["workspace_path"]).is_dir():
            capture = self.runner.capture_orphan(task, attempt, contract, project)
        outcome = self.runner.orphan_outcome(task, attempt, contract, route, capture,
                                             "terminated by recovery" if terminate and pgid else "its Kernel disappeared")
        private_dirs(self.runner.attempt_dir(task_id, n), self.data_dir)          # (a crash can come before the folder exists)
        write_private(self.runner.attempt_dir(task_id, n) / "result.json", json.dumps(outcome.envelope, indent=2, sort_keys=True, ensure_ascii=False))
        with self.store.tx():
            fresh = self.store.get_attempt(task_id, n)
            if fresh is None or fresh["state"] == "ENDED":
                return RecoveryAction(task_id, n, "skipped", "it was settled by someone else meanwhile")
            task = self._require(task_id)
            self.store.end_attempt(task_id, n, end_kind="ORPHANED", failure_class=None, stage="recovery", result=outcome.envelope)
            self.store.release_lock(task["project_id"], task_id)
            self.store.add_event("ATTEMPT_ENDED", task_id=task_id, attempt_n=n, reason=outcome.reason,
                                 data={"end_kind": "ORPHANED", "pid": pid, "pgid": pgid, "partial_work": bool(outcome.partial)})
            status = T(task["status"])
            if status == T.CANCEL_REQUESTED:
                self.store.set_status(task_id, T.CANCELED, attempt_n=n, reason="canceled; the Worker's Kernel was already gone")
            elif status in ACTIVE:
                self.store.set_status(task_id, T.RECOVERY_REQUIRED, attempt_n=n,
                                      reason="its Kernel disappeared mid-attempt; partial work (if any) was captured - review it, then retry or cancel")
        self._drop_scratch(task_id, n)
        return RecoveryAction(task_id, n, "terminated" if terminate and pgid else "orphaned", outcome.reason)
