"""Approval decisions and coordinator grants: who may decide what, and inside which scope.

The Kernel owns every approval. A person at the CLI may always decide. A coordinator may decide only
across the boundary the active profile declares:

* ``coordinator_autonomous`` (default) keeps the 2026-10-20 Maintainer decision: the bridge may
  approve, reject, accept and close, and every decision records its mode and scope in the ledger;
* ``coordinator_scoped`` requires a live grant created by a person, naming the coordinator, the
  decision actions, the project, the risk tiers, the approval kinds and an expiry;
* ``human_only`` refuses every coordinator decision at the Kernel, not only in the bridge.

Grants are append-only ledger facts. Revoking sets ``revoked_at`` once; a scope is immutable, so a
changed scope means a new grant. No MCP tool creates or revokes a grant: that stays a CLI action.
"""
from __future__ import annotations

from datetime import timezone
from typing import Any

from par import coordinators as registry
from par.core.clock import iso, parse_iso
from par.core.states import TaskStatus as T

DECISION_ACTIONS = ("approve", "reject", "accept", "close")
APPROVAL_KINDS = ("WRITE_PERMISSION", "WORKER_OVERRIDE", "BASE_DRIFT", "FALLBACK_SWITCH", "PERMISSION_RETRY")
RISK_TIERS = ("R0", "R1", "R2")
CANCEL_ON_REJECT = frozenset({"WRITE_PERMISSION", "WORKER_OVERRIDE", "BASE_DRIFT", "PERMISSION_RETRY"})


class ApprovalMixin:
    """Methods of :class:`par.core.kernel.Kernel` (kept apart: the Kernel is long enough)."""

    # ------------------------------------------------------------------------------------------------------
    # grants
    def approval_mode(self) -> str:
        from par.config import DEFAULT_APPROVAL_MODE
        profile = self.config.profiles.get(self.config.active_profile) if self.config.active_profile else None
        return profile.approval_mode if profile is not None else DEFAULT_APPROVAL_MODE

    def grant_create(self, coordinator: str, *, actions: "list[str] | tuple[str, ...]", project_id: "str | None" = None,
                     approval_kinds: "list[str] | tuple[str, ...]" = (), risk_tiers: "list[str] | tuple[str, ...]" = RISK_TIERS,
                     expires_at: "str | None" = None, note: str = "", by: str = "user") -> "dict[str, Any]":
        from par.core.kernel import Refused
        if by != "user":
            raise Refused("grant_by_human", "only a person can create a coordinator grant (there is no bridge tool for it)")
        cid = registry.normalize(coordinator)
        if cid is None:
            raise Refused("unknown_coordinator", f"{coordinator!r} is not a known coordinator ({', '.join(sorted(registry.COORDINATORS))})")
        actions = tuple(actions)
        if not actions or any(a not in DECISION_ACTIONS for a in actions):
            raise Refused("bad_grant_actions", f"actions must be a non-empty subset of {list(DECISION_ACTIONS)}")
        approval_kinds = tuple(approval_kinds)
        if any(k not in APPROVAL_KINDS for k in approval_kinds):
            raise Refused("bad_grant_kinds", f"approval kinds must be a subset of {list(APPROVAL_KINDS)}")
        if project_id is not None and project_id not in self.projects:
            raise Refused("unknown_project", f"project {project_id!r} is not registered")
        risk_tiers = tuple(risk_tiers)
        if not risk_tiers or any(r not in RISK_TIERS for r in risk_tiers):
            raise Refused("bad_grant_risks", f"risk tiers must be a non-empty subset of {list(RISK_TIERS)}")
        if expires_at is not None:
            try:
                moment = parse_iso(expires_at)
            except ValueError:
                raise Refused("bad_grant_expiry", "expires_at must be an ISO-8601 time") from None
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            if moment <= self.clock.now():
                raise Refused("bad_grant_expiry", "expires_at must be in the future")
            expires_at = iso(moment)
        with self.store.tx():
            grant_id = self.store.add_grant(cid, list(actions), approval_kinds=list(approval_kinds), project_id=project_id,
                                            risk_tiers=list(risk_tiers), expires_at=expires_at, note=note)
            self.store.add_event("COORDINATOR_GRANTED", reason=f"{cid} may {', '.join(actions)} within a scoped grant",
                                 data={"grant_id": grant_id, "coordinator": cid, "actions": list(actions),
                                       "approval_kinds": list(approval_kinds), "project_id": project_id,
                                       "risk_tiers": list(risk_tiers), "expires_at": expires_at})
        return self.grant_show(grant_id)

    def grant_show(self, grant_id: int) -> "dict[str, Any]":
        from par.core.kernel import Refused
        row = next((g for g in self.store.grants() if g["id"] == grant_id), None)
        if row is None:
            raise Refused("unknown_grant", f"no coordinator grant #{grant_id}")
        return self._grant_view(row)

    def grant_list(self, *, include_inactive: bool = False) -> "list[dict[str, Any]]":
        rows = [self._grant_view(g) for g in self.store.grants()]
        return rows if include_inactive else [g for g in rows if g["active"]]

    def grant_revoke(self, grant_id: int, *, note: str = "", by: str = "user") -> "dict[str, Any]":
        from par.core.kernel import Refused
        if by != "user":
            raise Refused("grant_by_human", "only a person can revoke a coordinator grant")
        row = next((g for g in self.store.grants() if g["id"] == grant_id), None)
        if row is None:
            raise Refused("unknown_grant", f"no coordinator grant #{grant_id}")
        with self.store.tx():
            try:
                self.store.revoke_grant(grant_id, self.clock.iso())
            except Exception as exc:
                from par.core.store import NotFound
                if isinstance(exc, NotFound):
                    raise Refused("already_revoked", f"grant #{grant_id} is already revoked") from None
                raise
            self.store.add_event("COORDINATOR_GRANT_REVOKED", reason=f"grant #{grant_id} revoked",
                                 data={"grant_id": grant_id, "coordinator": row["coordinator"], "note": note})
        return self.grant_show(grant_id)

    def _grant_view(self, row: "dict[str, Any]") -> "dict[str, Any]":
        expired = bool(row["expires_at"]) and parse_iso(row["expires_at"]) <= self.clock.now()
        active = row["revoked_at"] is None and not expired
        return {"grant_id": row["id"], "coordinator": row["coordinator"],
                "actions": list(row["actions"]), "approval_kinds": list(row["approval_kinds"]),
                "project_id": row["project_id"], "risk_tiers": list(row["risk_tiers"]),
                "expires_at": row["expires_at"], "revoked_at": row["revoked_at"], "note": row["note"],
                "created_at": row["created_at"], "active": active, "expired": expired}

    def _matching_grant(self, coordinator: str, action: str, task: "dict[str, Any]",
                        approval_kind: "str | None") -> "dict[str, Any] | None":
        for row in self.store.grants(coordinator):
            if row["revoked_at"] is not None:
                continue
            if row["expires_at"] and parse_iso(row["expires_at"]) <= self.clock.now():
                continue
            if action not in row["actions"]:
                continue
            if row["project_id"] not in (None, "", task["project_id"]):
                continue
            risk = task.get("risk_tier", task.get("risk"))
            if risk not in row["risk_tiers"]:
                continue
            if action in ("approve", "reject") and row["approval_kinds"] and approval_kind not in row["approval_kinds"]:
                continue
            return row
        return None

    def _decision_authorization(self, action: str, *, by: str, actor: "str | None", task: "dict[str, Any]",
                                approval_kind: "str | None" = None) -> "dict[str, Any]":
        """Allow or refuse one decision, and return the audit fields the ledger event must carry."""
        from par.core.kernel import Refused
        if by == "user":
            return {"mode": "human", "allowed": True, "grant_id": None, "coordinator": None,
                    "reason": "a person decided at the CLI"}
        if by != "coordinator":
            raise Refused("decision_actor", f"{by!r} is not a decision actor; people use by='user' and the bridge uses by='coordinator'")
        mode = self.approval_mode()
        cid = registry.normalize(actor) if actor else None
        if mode == "human_only":
            raise Refused("approval_mode", "approval_mode is human_only: coordinator decisions are refused; "
                                           "run the decision at the CLI, or change the profile after deciding it is safe")
        if mode == "coordinator_autonomous":
            return {"mode": mode, "allowed": True, "grant_id": None, "coordinator": cid,
                    "reason": "coordinator_autonomous: the person enabled coordinator decisions in this profile"}
        if cid is None:
            raise Refused("coordinator_identity", "approval_mode is coordinator_scoped: the bridge must name its coordinator")
        grant = self._matching_grant(cid, action, task, approval_kind)
        if grant is None:
            raise Refused("no_grant", f"approval_mode is coordinator_scoped and {cid} has no live grant for {action}"
                                      + (f" of {approval_kind}" if approval_kind else "")
                                      + f" in project {task['project_id']} at risk {task['risk_tier']}; "
                                        "a person can create one with `par coordinator grant`")
        return {"mode": mode, "allowed": True, "grant_id": grant["id"], "coordinator": cid,
                "reason": f"coordinator_scoped grant #{grant['id']}"}

    def decision_view(self, coordinator: "str | None", task: "dict[str, Any]") -> "dict[str, Any]":
        """What this coordinator may decide for one task now, without deciding anything (used by the bridge)."""
        mode = self.approval_mode()
        cid = registry.normalize(coordinator) if coordinator else None
        actions: "list[str]" = []
        reasons: "list[str]" = []
        if mode == "human_only":
            reasons.append("human_only: only a person can decide")
        elif mode == "coordinator_scoped" and cid is None:
            reasons.append("coordinator_scoped: the bridge has no coordinator identity")
        else:
            status = T(task["status"])
            pending = self._pending(task["task_id"])
            if pending:
                for action in ("approve", "reject"):
                    if mode == "coordinator_autonomous":
                        actions.append(action)
                    elif any(self._matching_grant(cid, action, task, a["kind"]) for a in pending):
                        actions.append(action)
                    else:
                        reasons.append(f"no live grant for {action}")
            if status == T.REVIEW_REQUIRED:
                if mode == "coordinator_autonomous" or (cid is not None and self._matching_grant(cid, "accept", task, None)):
                    actions.append("accept")
                else:
                    reasons.append("no live grant for accept")
            if status == T.ACCEPTED:
                if mode == "coordinator_autonomous" or (cid is not None and self._matching_grant(cid, "close", task, None)):
                    actions.append("close")
                else:
                    reasons.append("no live grant for close")
        return {"mode": mode, "coordinator": cid, "allowed_actions": actions,
                "reason": "; ".join(reasons) if reasons else "allowed by this profile"}

    # ------------------------------------------------------------------------------------------------------
    # decisions
    def _decide_target(self, task_id: str, approval_id: "int | None") -> "dict[str, Any]":
        from par.core.kernel import NotAllowed
        pending = self._pending(task_id)
        if approval_id is None:
            if len(pending) != 1:
                raise NotAllowed("which_approval", [f"{task_id} has {len(pending)} pending approval(s); name one with --id"] +
                                 [f"  #{a['id']} {a['kind']}" for a in pending])
            return pending[0]
        found = next((a for a in pending if a["id"] == approval_id), None)
        if found is None:
            raise NotAllowed("no_such_approval", f"{task_id} has no pending approval #{approval_id}")
        return found

    def approve(self, task_id: str, approval_id: "int | None" = None, *, by: str = "user", note: str = "",
                actor: "str | None" = None) -> "dict[str, Any]":
        from par.core.kernel import NotAllowed, Refused
        task = self._require(task_id)
        target = self._decide_target(task_id, approval_id)
        kind = target["kind"]
        auth = self._decision_authorization("approve", by=by, actor=actor, task=task, approval_kind=kind)
        if kind == "BASE_DRIFT" and (task["base"] or {}).get("candidate"):
            raise Refused("candidate_base", "this task runs on an immutable candidate; re-create the candidate against the current project state instead")
        columns: "dict[str, Any]" = {}
        extra_events: "list[tuple[str, str, dict]]" = []
        if kind == "BASE_DRIFT":
            project = self.projects.get(task["project_id"])
            if project is None:
                raise Refused("unknown_project", f"project {task['project_id']!r} is no longer registered")
            manifest = self._context(project).manifest()
            base = self._base_record(manifest, task["risk_tier"])
            columns["base_json"] = base
            extra_events.append(("BASE_REFRESHED", "re-based on the current project state after approval", {"commit": base["commit"]}))
        elif kind == "FALLBACK_SWITCH":
            columns["pinned_worker"] = target["payload"]["to"]
        with self.store.tx():
            task = self._require(task_id)
            if not any(a["id"] == target["id"] for a in self._pending(task_id)):
                raise NotAllowed("already_decided", f"approval #{target['id']} was decided meanwhile")
            self.store.decide_approval(target["id"], "APPROVED", by, note)
            self.store.add_event("APPROVAL_DECIDED", task_id=task_id, reason=f"{kind} approved by {by}",
                                 data={"approval_id": target["id"], "kind": kind, "decision": "APPROVED",
                                       "decision_mode": auth["mode"], "decision_grant": auth["grant_id"],
                                       "decision_coordinator": auth["coordinator"]})
            if columns:
                self.store.update_task(task_id, "TASK_UPDATED", reason=f"effect of the approved {kind}", columns=columns)
            for etype, reason, data in extra_events:
                self.store.add_event(etype, task_id=task_id, reason=reason, data=data)
            remaining = self._pending(task_id)
            status = T(self.store.get_task(task_id)["status"])
            if not remaining and status in (T.PERMISSION_WAIT, T.RATE_LIMITED, T.QUOTA_EXHAUSTED, T.AUTH_REQUIRED,
                                            T.BLOCKED_NO_WORKER, T.INTERRUPTED, T.FAILED):
                if kind in ("FALLBACK_SWITCH", "PERMISSION_RETRY") or status == T.PERMISSION_WAIT:
                    self.store.set_status(task_id, T.QUEUED, reason=f"{kind} approved: ready to run again")
        return self.describe(task_id)

    def reject(self, task_id: str, approval_id: "int | None" = None, *, by: str = "user", note: str = "",
               actor: "str | None" = None) -> "dict[str, Any]":
        from par.core.kernel import NotAllowed
        task = self._require(task_id)
        if task["status"] == T.REVIEW_REQUIRED.value and approval_id is None and not self._pending(task_id):
            auth = self._decision_authorization("reject", by=by, actor=actor, task=task)
            with self.store.tx():
                self.store.add_event("RESULT_REJECTED", task_id=task_id, reason=f"result rejected by {by}",
                                     data={"by": by, "attempt": task["attempt_count"], "note": note[:200],
                                           "decision_mode": auth["mode"], "decision_grant": auth["grant_id"],
                                           "decision_coordinator": auth["coordinator"]})
                self.store.set_status(task_id, T.FAILED, reason=f"the result was rejected by {by}: {note[:200]}".strip(": "))
            return self.describe(task_id)
        target = self._decide_target(task_id, approval_id)
        auth = self._decision_authorization("reject", by=by, actor=actor, task=task, approval_kind=target["kind"])
        with self.store.tx():
            if not any(a["id"] == target["id"] for a in self._pending(task_id)):
                raise NotAllowed("already_decided", f"approval #{target['id']} was decided meanwhile")
            self.store.decide_approval(target["id"], "REJECTED", by, note)
            self.store.add_event("APPROVAL_DECIDED", task_id=task_id, reason=f"{target['kind']} rejected by {by}",
                                 data={"approval_id": target["id"], "kind": target["kind"], "decision": "REJECTED",
                                       "decision_mode": auth["mode"], "decision_grant": auth["grant_id"],
                                       "decision_coordinator": auth["coordinator"]})
            status = T(self.store.get_task(task_id)["status"])
            if target["kind"] in CANCEL_ON_REJECT and status in _cancellable_direct():
                self.store.set_status(task_id, T.CANCELED, reason=f"{target['kind']} was refused")
            elif status == T.PERMISSION_WAIT and not self._pending(task_id):
                self.store.set_status(task_id, T.PAUSED, reason=f"{target['kind']} was declined; resume or retry when ready")
        return self.describe(task_id)

    def _supersede_pending(self, task_id: str, kinds: "tuple[str, ...]", why: str) -> None:
        for a in self._pending(task_id):
            if a["kind"] in kinds:
                self.store.decide_approval(a["id"], "REJECTED", "kernel", why)
                self.store.add_event("APPROVAL_DECIDED", task_id=task_id, reason=f"{a['kind']} superseded: {why}",
                                     data={"approval_id": a["id"], "kind": a["kind"], "decision": "REJECTED"})


def _cancellable_direct() -> frozenset:
    from par.core.kernel import CANCELLABLE_DIRECT
    return CANCELLABLE_DIRECT
