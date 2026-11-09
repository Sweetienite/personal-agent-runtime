"""Result review: what blocks acceptance, accepting, and closing.

Acceptance records a result; it never applies a patch to the project. A coordinator may accept (and
close) only when the active profile's approval mode and any live grant allow it; the Kernel checks
that here, not in the bridge.
"""
from __future__ import annotations

from typing import Any

from par.core.states import TaskStatus as T
from par.execution import results as R


class ReviewMixin:
    """Methods of :class:`par.core.kernel.Kernel` (kept apart: the Kernel is long enough)."""

    def accept_blockers(self, task_id: str, *, by: str = "user") -> "list[str]":
        task = self._require(task_id)
        contract = self._contract_of(task)
        attempts = self.store.attempts(task_id)
        approved = any(a["kind"] == "WRITE_PERMISSION" and a["status"] == "APPROVED" for a in self.store.approvals(task_id))
        return R.acceptance_problems(status=task["status"], attempt=attempts[-1] if attempts else None,
                                     contract_checks=contract.checks, needs_write_approval=contract.writes,
                                     write_approved=approved, by=by)

    def accept(self, task_id: str, *, by: str = "user", note: str = "", actor: "str | None" = None) -> "dict[str, Any]":
        from par.core.kernel import Refused
        task = self._require(task_id)
        self._decision_authorization("accept", by=by, actor=actor, task=task)
        problems = self.accept_blockers(task_id, by=by)
        if problems:
            raise Refused("cannot_accept", problems)
        with self.store.tx():
            task = self._require(task_id)
            auth = self._decision_authorization("accept", by=by, actor=actor, task=task)
            approval = self.store.add_approval(task_id, "ACCEPT_RESULT", {"attempt": task["attempt_count"]})
            self.store.decide_approval(approval, "APPROVED", by, note)
            self.store.set_status(task_id, T.ACCEPTED, expected_version=task["version"],
                                  reason=f"the result of attempt {task['attempt_count']} was accepted by {by}")
            self.store.add_event("ACCEPT_AUTHORIZED", task_id=task_id, reason=f"acceptance recorded by {by}",
                                 data={"decision_mode": auth["mode"], "decision_grant": auth["grant_id"],
                                       "decision_coordinator": auth["coordinator"], "attempt": task["attempt_count"]})
        return self.describe(task_id)

    def close_task(self, task_id: str, *, by: str = "user", actor: "str | None" = None) -> "dict[str, Any]":
        from par.core.kernel import NotAllowed
        task = self._require(task_id)
        if task["status"] != T.ACCEPTED.value:
            raise NotAllowed("not_accepted", f"{task_id} is {task['status']}; only ACCEPTED tasks can be closed")
        auth = self._decision_authorization("close", by=by, actor=actor, task=task)
        with self.store.tx():
            self.store.set_status(task_id, T.CLOSED, expected_version=task["version"], reason="closed")
            self.store.add_event("TASK_CLOSED", task_id=task_id, reason=f"closed by {by}",
                                 data={"decision_mode": auth["mode"], "decision_grant": auth["grant_id"],
                                       "decision_coordinator": auth["coordinator"]})
        return self.describe(task_id)
