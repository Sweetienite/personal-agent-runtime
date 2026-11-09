"""Read-only views of the ledger and the health of the current configuration."""
from __future__ import annotations

import json
from typing import Any, Mapping

from par.core.router import config_snapshot
from par.core.states import NEXT_ACTIONS, TaskStatus as T, derived_state
from par.execution import results as R


class ReadingMixin:
    """Methods of :class:`par.core.kernel.Kernel` (kept apart: the Kernel is long enough)."""

    def next_actions(self, task: "dict[str, Any]") -> "list[str]":
        status = T(task["status"])
        actions = list(NEXT_ACTIONS[status])
        pending = self._pending(task["task_id"])
        if pending:
            actions = [a for a in actions if a not in ("approve", "reject")] + ["approve", "reject"]
        else:
            actions = [a for a in actions if a != "approve"]
            if status != T.REVIEW_REQUIRED:
                actions = [a for a in actions if a != "reject"]
        if status == T.REVIEW_REQUIRED and self.accept_blockers(task["task_id"]):
            actions = [a for a in actions if a != "accept"]
        if status == T.PERMISSION_WAIT and self._retryable(task["task_id"], status):
            actions.insert(len(actions) - 1, "retry")                   # before "cancel"
        return actions

    def describe(self, task_id: str) -> "dict[str, Any]":
        task = self._require(task_id)
        attempts = []
        for a in self.store.attempts(task_id):
            attempts.append({k: a[k] for k in ("n", "worker_id", "provider_id", "model_requested", "state", "end_kind", "failure_class",
                                               "stage", "started_at", "ended_at", "exit_code", "signal", "timeout_kind", "pid", "pgid",
                                               "artifact_dir", "workspace_path")})
        contract = task["contract"]
        deps = [{"task_id": d, "status": (self.store.get_task(d) or {}).get("status", "missing")} for d in contract.get("dependencies", [])]
        pending = self._pending(task_id)
        return {
            "task_id": task_id, "project_id": task["project_id"], "title": task["title"], "risk": task["risk_tier"],
            "role": task["role"], "worker_requested": task["requested_worker"], "pinned_worker": task["pinned_worker"],
            "status": task["status"], "version": task["version"], "created_at": task["created_at"], "updated_at": task["updated_at"],
            "attempt_count": task["attempt_count"], "retry_budget": task["retry_budget"], "switch_budget": task["switch_budget"],
            "not_before": task["not_before"], "last_failure": task["last_failure"], "cancel_requested": bool(task["cancel_requested"]),
            "ad_hoc": bool(task["ad_hoc"]), "submitted_by": task["submitted_by"], "base": task["base"],
            "attempts": attempts, "pending_approvals": [{"id": a["id"], "kind": a["kind"], "payload": a["payload"],
                                                          "requested_at": a["requested_at"]} for a in pending],
            "derived_state": derived_state(task["status"], tuple(a["kind"] for a in pending)),
            "next_actions": self.next_actions(task), "dependencies": deps, "lock": self.store.get_lock(task["project_id"]),
            "config_digest": task["profile"].get("digest"), "waiting_for": self._dependency_problem(task),
        }

    def list_tasks(self, status: "str | None" = None, project_id: "str | None" = None) -> "list[dict[str, Any]]":
        return [{"task_id": t["task_id"], "project_id": t["project_id"], "title": t["title"], "risk": t["risk_tier"],
                 "status": t["status"], "attempts": t["attempt_count"], "updated_at": t["updated_at"]}
                for t in self.store.list_tasks(status, project_id)]

    def events(self, task_id: "str | None" = None, after_id: int = 0, limit: "int | None" = None) -> "list[dict[str, Any]]":
        if task_id is not None:
            self._require(task_id)
        return self.store.events(task_id, after_id, limit)

    def result(self, task_id: str, attempt: "int | None" = None) -> "dict[str, Any] | None":
        self._require(task_id)
        attempts = self.store.attempts(task_id)
        if attempt is not None:
            attempts = [a for a in attempts if a["n"] == attempt]
        return attempts[-1]["result"] if attempts else None

    def evidence(self, task_id: str, attempt: "int | None" = None) -> "dict[str, Any]":
        """The layered view of one result (Kernel measurements vs the Worker's claim vs review/integration).

        Derived on read: no new column, no schema bump, and an old envelope without the newer sections comes back
        as UNKNOWN/NOT_RUN rather than PASS."""
        task = self._require(task_id)
        contract = self._contract_of(task)
        attempts = self.store.attempts(task_id)
        chosen = attempt if attempt is not None else (attempts[-1]["n"] if attempts else None)
        changed: "list[str] | None" = None
        if chosen is not None:
            try:
                doc = json.loads((self.runner.attempt_dir(task_id, chosen) / "changes.json").read_text(encoding="utf-8"))
                allowed = doc.get("allowed")
                if isinstance(allowed, list):
                    changed = [str(c["path"]) for c in allowed if isinstance(c, Mapping) and isinstance(c.get("path"), str)]
            except (OSError, ValueError, TypeError):
                changed = None
        return R.evidence_summary(self.result(task_id, attempt), contract_checks=contract.checks, changed_paths=changed,
                                  worker_can_edit_checks=contract.writes, review=self._review_state(task_id, chosen),
                                  status=task["status"], integration=self._integration_state(task_id))

    def _integration_state(self, task_id: str) -> str:
        """How far this task's result has travelled: a verified candidate, only a failed one, or nowhere yet."""
        project_id = self._require(task_id)["project_id"]
        rows = [c for c in self.store.list_candidates(project_id) if task_id in c.get("requested", [])]
        if any(c["status"] == "VERIFIED" for c in rows):
            return "CANDIDATE_READY"
        return "CANDIDATE_FAILED" if rows else "NOT_INTEGRATED"

    def _review_state(self, task_id: str, attempt: "int | None") -> str:
        """Who, if anyone, decided on this attempt's result: the accept decision or a result rejection, latest wins."""
        decisions: "list[tuple[str, str]]" = []
        for a in self.store.approvals(task_id):
            payload = a.get("payload") if isinstance(a.get("payload"), Mapping) else {}
            if (a.get("kind") == "ACCEPT_RESULT" and a.get("status") in ("APPROVED", "REJECTED")
                    and payload.get("attempt") == attempt):
                decisions.append((str(a.get("decided_at") or ""), str(a.get("decided_by") or "")))
        for e in self.store.events(task_id):
            if e["type"] == "RESULT_REJECTED" and (attempt is None or e.get("attempt") in (None, attempt)):
                who = e.get("data", {}).get("by") if isinstance(e.get("data"), Mapping) else None
                decisions.append((str(e.get("ts") or ""), str(who or "")))
        if not decisions:
            return "NOT_REVIEWED"
        who = max(decisions)[1]
        return "HUMAN_REVIEW" if who == "user" else "COORDINATOR_REVIEW" if who == "coordinator" else "UNKNOWN"

    def diff(self, task_id: str, attempt: "int | None" = None) -> "bytes | None":
        self._require(task_id)
        attempts = self.store.attempts(task_id)
        if attempt is not None:
            attempts = [a for a in attempts if a["n"] == attempt]
        if not attempts:
            return None
        patch = self.runner.attempt_dir(task_id, attempts[-1]["n"]) / "work.patch"
        return patch.read_bytes() if patch.is_file() and not patch.is_symlink() else None

    def attempt_prompt(self, task_id: str, attempt: "int | None" = None) -> "str | None":
        self._require(task_id)
        attempts = self.store.attempts(task_id)
        if attempt is not None:
            attempts = [a for a in attempts if a["n"] == attempt]
        if not attempts:
            return None
        prompt = self.runner.attempt_dir(task_id, attempts[-1]["n"]) / "prompt.txt"
        return prompt.read_text(encoding="utf-8") if prompt.is_file() and not prompt.is_symlink() else None

    # ------------------------------------------------------------------------------------------------------
    # health overview (why can't my tasks run?)
    def health_report(self) -> "dict[str, Any]":
        from par.core.router import evaluate
        snapshot = config_snapshot(self.config)
        workers = []
        for wid in sorted(snapshot["workers"]):
            w = snapshot["workers"][wid]
            r1 = evaluate(snapshot, wid, risk="R1", adapters=self.adapters, health=self.health)
            r2 = evaluate(snapshot, wid, risk="R2", adapters=self.adapters, health=self.health)
            workers.append({"worker": wid, "provider": w["provider"], "model": w["model"], "capabilities": w["capabilities"],
                            "usable_read_only": r1.usable, "usable_write": r2.usable,
                            "reasons": sorted(set(r1.reasons) | set(r2.reasons))})
        return {"profile": snapshot["profile"], "policy": snapshot["worker_failure_policy"], "config_digest": snapshot["digest"],
                "roles": snapshot["roles"], "default_worker": snapshot["default_worker"], "workers": workers,
                "records": self.store.health_rows(), "adapters": self.adapters.ids()}

    def probe_providers(self) -> "list[dict[str, Any]]":
        """Ask every registered adapter what it can see about itself. Adapters probe offline: no model is called, no quota is used."""
        out: "list[dict[str, Any]]" = []
        for pid in self.adapters.ids():
            try:
                res = self.adapters.create(pid).probe()
                out.append({"provider": pid, "status": res.status.value, "detail": res.detail, "version": res.version,
                            "executable": res.executable, "facts": dict(res.facts)})
            except Exception as exc:                          # a broken adapter must not break the report
                out.append({"provider": pid, "status": "UNHEALTHY", "detail": f"the probe itself failed ({type(exc).__name__})",
                            "version": None, "executable": None, "facts": {}})
        return out

    def clear_health(self, provider_id: str, model: "str | None" = None) -> int:
        with self.store.tx():
            return self.health.clear(provider_id, model)
