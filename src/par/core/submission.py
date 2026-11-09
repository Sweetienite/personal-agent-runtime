"""Task submission: contract validation, deduplication, and the one transaction that queues a task."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from par import coordinators as registry
from par.config import ProjectEntry
from par.context.types import ContextManifest
from par.core.candidate import INTEGRATION_MODE
from par.core.contract import Contract, ContractError, parse_contract
from par.core.router import config_snapshot
from par.core.states import TaskStatus as T
from par.core.store import IdempotencyConflict


@dataclass(frozen=True)
class SubmitResult:
    task_id: str
    status: str
    created: bool
    deduplicated: bool
    warnings: "tuple[str, ...]" = ()
    pending_approvals: "tuple[int, ...]" = ()


@dataclass(frozen=True)
class ValidateResult:
    """What `submit()` would do, without doing it: no task, no event, no approval is recorded."""
    warnings: "tuple[str, ...]" = ()
    would_approvals: "tuple[str, ...]" = ()


class SubmissionMixin:
    """Methods of :class:`par.core.kernel.Kernel` (kept apart: the Kernel is long enough)."""

    def submit(self, source: "str | bytes | Mapping[str, Any]", *, submitted_by: str = "user",
               coordinator_id: "str | None" = None, candidate: "str | None" = None) -> SubmitResult:
        from par.core.kernel import Refused
        if submitted_by not in ("user", "coordinator"):
            raise ValueError("submitted_by must be 'user' or 'coordinator'")
        if submitted_by == "coordinator" and coordinator_id is not None:
            holder = self.coordinator_lease()["coordinator"]          # two coordinators must not schedule against one ledger (plan 6.3)
            coordinator_id = registry.normalize(coordinator_id) or coordinator_id
            if holder is not None and coordinator_id != holder:
                raise Refused("not_the_coordinator", f"{coordinator_id!r} is not the coordinator any more ({holder!r} took over); "
                                                     "the person who switched can confirm a new takeover")
        try:
            contract = parse_contract(source)
        except ContractError as exc:
            raise Refused("invalid_contract", exc.problems) from None
        project = self.projects.get(contract.project_id)
        if project is None:
            raise Refused("unknown_project", f"project {contract.project_id!r} is not registered (par project add ...)")
        again = self._deduplicate(contract)
        if again is not None:
            return again

        problems: "list[str]" = []
        for dep in contract.dependencies:
            row = self.store.get_task(dep)
            if row is None:
                problems.append(f"dependency {dep} does not exist")
            elif row["project_id"] != contract.project_id:
                problems.append(f"dependency {dep} belongs to another project")
        context = self._context(project)
        view = context.view()
        manifest = context.manifest(view)
        readiness = context.write_readiness(view)
        problems += self._submission_problems(contract, project, view, manifest, readiness)
        if problems:
            raise Refused("not_allowed", problems)

        base = self._base_record(manifest, contract.risk)
        base["integration"] = INTEGRATION_MODE
        if candidate is not None:
            base.update(self.candidate_base_for(candidate, contract.project_id, contract.dependencies))
            if sum((manifest.base.get("dirty") or {}).values()):
                raise Refused("dirty_project", "the project has uncommitted changes; a candidate-bound task needs the clean state it was built on")
            if manifest.base_git_commit != base["candidate_base"]:
                raise Refused("stale_candidate", f"the project is at {manifest.base_git_commit}, but candidate {candidate} was built on "
                                                 f"{base['candidate_base']}; create a new candidate from the current state")
        snapshot = config_snapshot(self.config)
        try:
            with self.store.tx():
                again = self._deduplicate(contract)
                if again is not None:
                    return again
                task_id = self.store.next_task_id(contract.project_id)
                self.store.insert_task(
                    task_id=task_id, project_id=contract.project_id, idempotency_key=contract.idempotency_key,
                    contract_sha256=contract.sha256, contract=contract.doc, title=contract.title, role=contract.role,
                    requested_worker=contract.worker, risk_tier=contract.risk, submitted_by=submitted_by,
                    ad_hoc=contract.ad_hoc, base=base, profile=snapshot)
                self.store.add_event("TASK_SUBMITTED", task_id=task_id, reason=f"submitted by {submitted_by}",
                                     data={"project": contract.project_id, "risk": contract.risk, "role": contract.role,
                                           "worker": contract.worker, "contract_sha256": contract.sha256,
                                           "base_commit": base["commit"], "ad_hoc": contract.ad_hoc,
                                           "candidate": base.get("candidate"), "integration": base.get("integration"),
                                           "config_digest": snapshot["digest"], "warnings": list(contract.warnings)})
                self.store.set_status(task_id, T.READY, reason="the contract passed validation")
                approvals: "list[int]" = []
                if contract.writes:
                    approvals.append(self.store.add_approval(task_id, "WRITE_PERMISSION", {
                        "allowed_paths": list(contract.allowed_paths), "base_commit": base["commit"],
                        "candidate": base.get("candidate"), "checks": [c["name"] for c in contract.checks]}))
                if submitted_by == "coordinator" and contract.worker:
                    approvals.append(self.store.add_approval(task_id, "WORKER_OVERRIDE", {"worker": contract.worker}))
                if approvals:
                    self.store.set_status(task_id, T.PERMISSION_WAIT, reason="waiting for your approval")
                else:
                    self.store.set_status(task_id, T.QUEUED, reason="ready to run")
        except IdempotencyConflict:
            raise Refused("idempotency_conflict", "this idempotency key is already used by a different contract") from None
        return SubmitResult(task_id, T.PERMISSION_WAIT.value if approvals else T.QUEUED.value, True, False,
                            contract.warnings, tuple(approvals))

    def validate(self, source: "str | bytes | Mapping[str, Any]", *, submitted_by: str = "user",
                 coordinator_id: "str | None" = None) -> ValidateResult:
        """Everything `submit()` checks before it opens a ledger transaction - and nothing past
        that: no task is created, no event or approval is recorded. Raises the same `Refused`
        `submit()` would for the same contract. Deliberately does not check for a duplicate
        (`_deduplicate` would write a `SUBMIT_DEDUPLICATED` event, which this must not do)."""
        from par.core.kernel import Refused
        if submitted_by not in ("user", "coordinator"):
            raise ValueError("submitted_by must be 'user' or 'coordinator'")
        if submitted_by == "coordinator" and coordinator_id is not None:
            holder = self.coordinator_lease()["coordinator"]
            coordinator_id = registry.normalize(coordinator_id) or coordinator_id
            if holder is not None and coordinator_id != holder:
                raise Refused("not_the_coordinator", f"{coordinator_id!r} is not the coordinator any more ({holder!r} took over); "
                                                     "the person who switched can confirm a new takeover")
        try:
            contract = parse_contract(source)
        except ContractError as exc:
            raise Refused("invalid_contract", exc.problems) from None
        project = self.projects.get(contract.project_id)
        if project is None:
            raise Refused("unknown_project", f"project {contract.project_id!r} is not registered (par project add ...)")
        problems: "list[str]" = []
        for dep in contract.dependencies:
            row = self.store.get_task(dep)
            if row is None:
                problems.append(f"dependency {dep} does not exist")
            elif row["project_id"] != contract.project_id:
                problems.append(f"dependency {dep} belongs to another project")
        context = self._context(project)
        view = context.view()
        manifest = context.manifest(view)
        readiness = context.write_readiness(view)
        problems += self._submission_problems(contract, project, view, manifest, readiness)
        if problems:
            raise Refused("not_allowed", problems)
        would: "list[str]" = []
        if contract.writes:
            would.append("WRITE_PERMISSION")
        if submitted_by == "coordinator" and contract.worker:
            would.append("WORKER_OVERRIDE")
        return ValidateResult(contract.warnings, tuple(would))

    def _deduplicate(self, contract: Contract) -> "SubmitResult | None":
        from par.core.kernel import Refused
        existing = self.store.find_task(contract.project_id, contract.idempotency_key)
        if existing is None:
            return None
        if existing["contract_sha256"] != contract.sha256:
            raise Refused("idempotency_conflict",
                          f"idempotency key {contract.idempotency_key!r} was already used for a different contract ({existing['task_id']})")
        with self.store.tx():
            self.store.add_event("SUBMIT_DEDUPLICATED", task_id=existing["task_id"],
                                 reason="the same contract was submitted again; the original task is returned")
        return SubmitResult(existing["task_id"], existing["status"], False, True, (),
                            tuple(a["id"] for a in self._pending(existing["task_id"])))

    @staticmethod
    def _submission_problems(contract: Contract, project: ProjectEntry, view: Any, manifest: ContextManifest,
                             readiness: Any) -> "list[str]":
        problems: "list[str]" = []
        if not view.exists:
            return ["the project folder does not exist"]
        if project.context_mode == "generic":
            if not contract.ad_hoc:
                problems.append("a generic project has no parent contract; set ad_hoc=true (the task's JSON contract is the execution contract)")
        else:
            state = view.contract_state
            if contract.ad_hoc and contract.risk == "R2":
                problems.append("an ad-hoc task (no parent contract) can only be R0 or R1 in a Harness project; code changes need a parent contract")
            if contract.ad_hoc and state != "none":
                problems.append("this project has an active contract: submit under it (ad_hoc is only for projects without one)")
            elif not contract.ad_hoc and state == "none":
                problems.append("the project has no active parent contract; set ad_hoc=true for a read-only diagnostic")
            elif state == "multiple":
                problems.append("the project has several active contracts; the Harness Core allows at most one")
        if contract.risk in ("R1", "R2") and (manifest.base.get("vcs") != "git" or not manifest.base_git_commit):
            problems.append("this task needs a git base commit to work from, and the project has none (R0 tasks do not)")
        if contract.risk == "R2" and not readiness.allowed:
            problems += [f"write blocked - {b.code}: {b.message}" for b in readiness.blockers]
        return problems
