"""The Kernel: task state, rules and decisions. It outlives any coordinator session.

Everything that changes a task goes through here, in one ledger transaction together with its event, along
the explicit transition table. The Kernel never scores workers or asks a model anything: routing follows
the configured chain, failures are classified from structured evidence, and what happens next is decided by
the user's ``worker_failure_policy`` and the task's risk tier:

* ``ask``       - stop in the matching state and wait for a person (default; always for R2);
* ``pause``     - move the task to PAUSED instead of switching or retrying;
* ``safe_auto`` - for R0/R1 only: one bounded retry of a transient error, one switch to the next worker.

Nothing here applies a result to a real project: ACCEPTED means a person accepted the result; applying a
patch to real files is a separate, later, separately approved step (P8).
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from par import coordinators as registry
from par.config import ProjectEntry, RuntimeConfig
from par.context.harness import ContextChanged, ContextManifest, HarnessContextAdapter
from par.core import relay
from par.core.attempt import AttemptOutcome, AttemptRunner, private_dirs, remove_scratch, write_private
from par.core.clock import Clock, iso, parse_iso
from par.core.contract import Contract, ContractError, parse_contract
from par.core.health import Health
from par.core.manual import ManualHandoffMixin
from par.core.prompt import build_prompt
from par.core.redact import redact_text
from par.core.router import config_snapshot, route as route_task
from par.core.settings import KernelSettings
from par.core.states import ACTIVE, NEXT_ACTIONS, TRANSITIONS, TaskStatus as T
from par.core.takeover import TakeoverMixin
from par.core.store import IdempotencyConflict, LedgerError, LockBusy, NotFound, Store, VersionConflict
from par.execution import gitops, results as R, subprocesses as sp, workspace as ws
from par.providers.registry import AdapterRegistry

AUTO_RISKS = ("R0", "R1")          # the only tiers a failure policy may act on without asking
CANCELLABLE_DIRECT = frozenset({T.DRAFT, T.READY, T.QUEUED, T.PERMISSION_WAIT, T.BLOCKED_NO_WORKER, T.FAILED, T.INTERRUPTED,
                                T.AUTH_REQUIRED, T.RATE_LIMITED, T.QUOTA_EXHAUSTED, T.PAUSED, T.REVIEW_REQUIRED})
PAUSABLE = frozenset({T.READY, T.QUEUED, T.PERMISSION_WAIT, T.BLOCKED_NO_WORKER, T.INTERRUPTED, T.AUTH_REQUIRED,
                      T.RATE_LIMITED, T.QUOTA_EXHAUSTED})
RETRYABLE = frozenset({T.FAILED, T.INTERRUPTED, T.BLOCKED_NO_WORKER, T.AUTH_REQUIRED, T.RATE_LIMITED, T.QUOTA_EXHAUSTED,
                       T.RECOVERY_REQUIRED, T.CANCELED, T.PAUSED})
SUPERSEDABLE = ("FALLBACK_SWITCH", "PERMISSION_RETRY")   # offers a retry may withdraw; real permissions must be decided by name
CANCEL_ON_REJECT = frozenset({"WRITE_PERMISSION", "WORKER_OVERRIDE", "BASE_DRIFT", "PERMISSION_RETRY"})


class KernelError(Exception):
    def __init__(self, code: str, problems: "list[str] | str") -> None:
        self.code = code
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


class Refused(KernelError):
    """A request the Kernel will not carry out (invalid contract, policy, unmet condition)."""


class NotAllowed(KernelError):
    """The action does not exist in the task's current state."""


class UnknownTask(KernelError):
    pass


class Busy(KernelError):
    """Another attempt holds the capacity or the project lock; nothing was changed."""


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


@dataclass(frozen=True)
class RunReport:
    task_id: str
    status: str
    attempt: "int | None"
    reason: str


@dataclass(frozen=True)
class RecoveryAction:
    task_id: str
    attempt: int
    action: str             # orphaned | alive | terminated | skipped
    detail: str


class _MissingGit:
    def __init__(self, message: str) -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        raise gitops.GitError(self._message)


class Kernel(ManualHandoffMixin, TakeoverMixin):
    def __init__(self, data_dir: "str | os.PathLike[str]", *, config: RuntimeConfig, projects: "Mapping[str, ProjectEntry]",
                 adapters: AdapterRegistry, clock: "Clock | None" = None, home: "str | os.PathLike[str] | None" = None,
                 settings: "KernelSettings | None" = None, kernel_id: "str | None" = None,
                 context_factory: "Optional[Callable[[ProjectEntry], HarnessContextAdapter]]" = None) -> None:
        self.data_dir = Path(data_dir)
        self.clock = clock or Clock()
        self.settings = settings or KernelSettings()
        self.config, self.projects, self.adapters = config, dict(projects), adapters
        self.home = Path(home) if home is not None else Path.home()
        self.kernel_id = kernel_id or f"k{os.getpid()}-{secrets.token_hex(4)}"
        self._context_factory = context_factory
        for sub in ("scratch", "tasks"):
            (self.data_dir / sub).mkdir(mode=0o700, exist_ok=True)
        self.store = Store(self.data_dir, self.clock)
        self.health = Health(self.store, rate_limit_cooldown=self.settings.rate_limit_cooldown)
        try:
            self.git: Any = gitops.Git(self.data_dir / "scratch", timeout=self.settings.git_timeout)
        except gitops.GitError as exc:
            self.git = _MissingGit(str(exc))
        self.runner = AttemptRunner(data_dir=self.data_dir, clock=self.clock, settings=self.settings, adapters=self.adapters,
                                    git=self.git)

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------------------------------------------
    # helpers
    def _context(self, project: ProjectEntry) -> HarnessContextAdapter:
        if self._context_factory:
            return self._context_factory(project)
        return HarnessContextAdapter(project, self.home)

    def _require(self, task_id: str) -> "dict[str, Any]":
        task = self.store.get_task(task_id)
        if task is None:
            raise UnknownTask("unknown_task", f"no task {task_id!r}")
        return task

    def _now(self) -> str:
        return self.clock.iso()

    def _later(self, seconds: float) -> str:
        return iso(self.clock.now() + timedelta(seconds=seconds))

    @staticmethod
    def _drift_signature(manifest: ContextManifest, risk: str) -> "dict[str, Any]":
        rules = next((e.sha256 for e in manifest.entries if e.kind == "rules"), None)
        sig = {"head": manifest.base.get("head"), "rules": rules, "state": manifest.state_revision,
               "contract": (manifest.parent_contract_ref or {}).get("sha256")}
        if risk == "R2":
            sig["dirty_digest"] = manifest.base.get("dirty_digest")
        return sig

    def _base_record(self, manifest: ContextManifest, risk: str) -> "dict[str, Any]":
        return {"vcs": manifest.base.get("vcs"), "commit": manifest.base_git_commit, "branch": manifest.base.get("branch"),
                "dirty": manifest.base.get("dirty"), "manifest_digest": manifest.digest,
                "state_revision": manifest.state_revision, "parent_contract_ref": manifest.parent_contract_ref,
                "drift": self._drift_signature(manifest, risk)}

    def _contract_of(self, task: "dict[str, Any]") -> Contract:
        try:
            contract = parse_contract(task["contract"])
        except ContractError as exc:
            raise Refused("contract_invalid", [f"the stored contract of {task['task_id']} no longer passes validation:"] + exc.problems) from None
        if contract.sha256 != task["contract_sha256"]:
            raise Refused("corrupt", f"the stored contract of {task['task_id']} no longer matches its recorded digest")
        return contract

    def _pending(self, task_id: str) -> "list[dict[str, Any]]":
        return self.store.approvals(task_id, "PENDING")

    def _missing_approvals(self, task: "dict[str, Any]", contract: Contract) -> "list[str]":
        """Permissions this task was submitted with that no person has granted (yet)."""
        granted = set()
        for approval in self.store.approvals(task["task_id"], "APPROVED"):
            if approval["kind"] == "WORKER_OVERRIDE":
                # An approval for the requested worker cannot authorize a different pinned worker.
                if approval["payload"].get("worker") == (task["pinned_worker"] or contract.worker):
                    granted.add(approval["kind"])
            else:
                granted.add(approval["kind"])
        needed = []
        if contract.writes:
            needed.append("WRITE_PERMISSION")
        if task["submitted_by"] == "coordinator" and contract.worker:
            needed.append("WORKER_OVERRIDE")
        return [kind for kind in needed if kind not in granted]

    # ------------------------------------------------------------------------------------------------------
    # submit
    def submit(self, source: "str | bytes | Mapping[str, Any]", *, submitted_by: str = "user",
               coordinator_id: "str | None" = None) -> SubmitResult:
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
                                           "config_digest": snapshot["digest"], "warnings": list(contract.warnings)})
                self.store.set_status(task_id, T.READY, reason="the contract passed validation")
                approvals: "list[int]" = []
                if contract.writes:
                    approvals.append(self.store.add_approval(task_id, "WRITE_PERMISSION", {
                        "allowed_paths": list(contract.allowed_paths), "base_commit": base["commit"],
                        "checks": [c["name"] for c in contract.checks]}))
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
    def _submission_problems(contract: Contract, project: ProjectEntry, view: Any, manifest: ContextManifest, readiness: Any) -> "list[str]":
        problems: "list[str]" = []
        if not view.exists:
            return ["the project folder does not exist"]
        state = view.contract_state
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

    # ------------------------------------------------------------------------------------------------------
    # approvals
    def _decide_target(self, task_id: str, approval_id: "int | None") -> "dict[str, Any]":
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

    def approve(self, task_id: str, approval_id: "int | None" = None, *, by: str = "user", note: str = "") -> "dict[str, Any]":
        task = self._require(task_id)
        target = self._decide_target(task_id, approval_id)
        kind = target["kind"]
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
                                 data={"approval_id": target["id"], "kind": kind, "decision": "APPROVED"})
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

    def reject(self, task_id: str, approval_id: "int | None" = None, *, by: str = "user", note: str = "") -> "dict[str, Any]":
        task = self._require(task_id)
        if task["status"] == T.REVIEW_REQUIRED.value and approval_id is None and not self._pending(task_id):
            with self.store.tx():
                self.store.add_event("RESULT_REJECTED", task_id=task_id, reason=f"result rejected by {by}", data={"note": note[:200]})
                self.store.set_status(task_id, T.FAILED, reason=f"the result was rejected by {by}: {note[:200]}".strip(": "))
            return self.describe(task_id)
        target = self._decide_target(task_id, approval_id)
        with self.store.tx():
            if not any(a["id"] == target["id"] for a in self._pending(task_id)):
                raise NotAllowed("already_decided", f"approval #{target['id']} was decided meanwhile")
            self.store.decide_approval(target["id"], "REJECTED", by, note)
            self.store.add_event("APPROVAL_DECIDED", task_id=task_id, reason=f"{target['kind']} rejected by {by}",
                                 data={"approval_id": target["id"], "kind": target["kind"], "decision": "REJECTED"})
            status = T(self.store.get_task(task_id)["status"])
            if target["kind"] in CANCEL_ON_REJECT and status in CANCELLABLE_DIRECT:
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

    # ------------------------------------------------------------------------------------------------------
    # run
    def capacity(self) -> int:
        """How many attempts may run at once: the settings' fixed number (tests), else the active profile's max_parallel_workers (1 or 2)."""
        if self.settings.max_parallel_workers is not None:
            return max(1, int(self.settings.max_parallel_workers))
        profile = self.config.profiles.get(self.config.active_profile) if self.config.active_profile else None
        return profile.max_parallel_workers if profile is not None else 1

    def run_next(self) -> "RunReport | None":
        """Run the oldest QUEUED task that may start now (dependencies done, not waiting for a retry delay)."""
        self.recover()
        now = self._now()
        for task in self.store.list_tasks(status=T.QUEUED.value):
            if task["not_before"] and task["not_before"] > now:
                continue
            if self._dependency_problem(task):
                continue
            try:
                return self.run(task["task_id"])
            except Busy as busy:
                if busy.code == "project_locked":
                    continue                     # that project is being worked on; a task of another project may still start
                return None                      # capacity (or a change under our feet): nothing else can start either
            except (NotAllowed, Refused):
                continue                         # someone else got it first, or it needs attention: try the next one
        return None

    def _dependency_problem(self, task: "dict[str, Any]") -> "str | None":
        for dep in task["contract"].get("dependencies", []):
            row = self.store.get_task(dep)
            if row is None or row["status"] not in (T.ACCEPTED.value, T.CLOSED.value):
                return f"waiting for {dep} ({row['status'] if row else 'missing'})"
        return None

    def run(self, task_id: str) -> RunReport:
        self.recover()
        task = self._require(task_id)
        if task["status"] != T.QUEUED.value:
            raise NotAllowed("not_queued", f"{task_id} is {task['status']}; only QUEUED tasks can be run")
        if task["not_before"] and task["not_before"] > self._now():
            raise Refused("not_before", f"{task_id} is waiting until {task['not_before']} before it may run again")
        waiting = self._dependency_problem(task)
        if waiting:
            raise Refused("dependencies", waiting)
        contract = self._contract_of(task)
        missing = self._missing_approvals(task, contract)
        if missing:
            raise Refused("missing_approval", [f"{task_id} has never been approved for: {', '.join(missing)}"])
        project = self.projects.get(task["project_id"])
        if project is None:
            raise Refused("unknown_project", f"project {task['project_id']!r} is no longer registered")

        context = self._context(project)
        manifest = context.manifest()
        report = self._check_drift(task, manifest)
        if report is not None:
            return report
        decision = self._choose_worker(task, contract)
        if isinstance(decision, RunReport):
            return decision
        route_info, switch = decision

        n = task["attempt_count"] + 1
        try:
            context_text = context.render(manifest)
        except ContextChanged:
            raise Refused("context_changed", "a project file changed while the context was being prepared; run again") from None
        seed = self._relay_seed(task, contract, n)
        prompt = build_prompt(contract, task_id=task_id, attempt=n, context_text=context_text,
                              workspace_has_files=contract.risk != "R0", handoff_text=seed.section if seed else None)
        started_at = self._start(task, n, contract, route_info, switch, seed)
        return self._run_attempt(task_id, n, contract, project, route_info, prompt, started_at, seed)

    def _relay_seed(self, task: "dict[str, Any]", contract: Contract, n: int) -> "Optional[relay.Seed]":
        """The previous attempt's verified work, when this attempt should continue it (plan 6.2). None: start from the baseline."""
        if not contract.writes:
            return None
        floor = int((task["base"] or {}).get("relay_floor", 0) or 0)
        previous = relay.choose_attempt(self.store.attempts(task["task_id"]), floor)
        if previous is None:
            return None
        try:
            return relay.build_seed(git=self.git, attempt=previous, to_attempt=n, attempt_dir=self.runner.attempt_dir(task["task_id"], previous["n"]),
                                    contract=contract, failure_reason=task["last_failure"])
        except relay.RelayError as exc:
            raise Refused("relay_unavailable", str(exc)) from None
        except gitops.GitError as exc:
            raise Refused("relay_unavailable", f"git could not be used to check the previous attempt's patch: {redact_text(str(exc))[:160]}") from None

    def _check_drift(self, task: "dict[str, Any]", manifest: ContextManifest) -> "RunReport | None":
        recorded = (task["base"] or {}).get("drift") or {}
        current = self._drift_signature(manifest, task["risk_tier"])
        changed = sorted(k for k in set(recorded) | set(current) if recorded.get(k) != current.get(k))
        if not changed:
            return None
        with self.store.tx():
            self.store.add_approval(task["task_id"], "BASE_DRIFT", {"changed": changed, "was": {k: recorded.get(k) for k in changed},
                                                                     "now": {k: current.get(k) for k in changed}})
            self.store.set_status(task["task_id"], T.PERMISSION_WAIT, expected_version=task["version"],
                                  reason=f"the project changed since submission ({', '.join(changed)}); approve to run on the current state")
        return RunReport(task["task_id"], T.PERMISSION_WAIT.value, None, f"project changed since submission: {', '.join(changed)}")

    def _choose_worker(self, task: "dict[str, Any]", contract: Contract) -> "RunReport | tuple[dict[str, Any], Optional[dict[str, Any]]]":
        snapshot = task["profile"]
        policy = snapshot.get("worker_failure_policy", "ask")
        worker = task["pinned_worker"] or contract.worker
        r = route_task(snapshot, role=contract.role, worker=worker, risk=contract.risk, adapters=self.adapters, health=self.health)
        task_id = task["task_id"]
        if r.chosen is None:
            return self._block(task, f"no worker can run this task: {r.explain()}", policy)
        switch: "Optional[dict[str, Any]]" = None
        if r.needs_switch:
            primary, alt = r.primary, r.chosen
            switch = {"from": primary.worker_id, "to": alt.worker_id, "why": list(primary.reasons)}
            can_auto = policy == "safe_auto" and contract.risk in AUTO_RISKS and task["switch_budget"] > 0
            if policy == "pause":
                with self.store.tx():
                    self.store.set_status(task_id, T.PAUSED, expected_version=task["version"],
                                          reason=f"{primary.worker_id} cannot run ({'; '.join(primary.reasons)}) and the policy is pause")
                return RunReport(task_id, T.PAUSED.value, None, "paused: the first-choice worker is unavailable")
            if not can_auto:
                with self.store.tx():
                    self.store.add_approval(task_id, "FALLBACK_SWITCH", dict(switch))
                    self.store.set_status(task_id, T.PERMISSION_WAIT, expected_version=task["version"],
                                          reason=f"{primary.worker_id} cannot run; approve a switch to {alt.worker_id}")
                return RunReport(task_id, T.PERMISSION_WAIT.value, None, f"waiting for approval to switch to {alt.worker_id}")
        chosen = r.chosen
        return ({"worker": chosen.worker_id, "provider": chosen.provider_id, "model": chosen.model, "source": r.source,
                 "explain": r.explain(), "policy": policy, "snapshot_digest": snapshot.get("digest")}, switch)

    def _block(self, task: "dict[str, Any]", reason: str, policy: str) -> RunReport:
        task_id = task["task_id"]
        with self.store.tx():
            self.store.set_status(task_id, T.BLOCKED_NO_WORKER, expected_version=task["version"], reason=reason)
            if policy == "pause":
                self.store.set_status(task_id, T.PAUSED, reason="paused by the failure policy")
        final = T.PAUSED if policy == "pause" else T.BLOCKED_NO_WORKER
        return RunReport(task_id, final.value, None, reason)

    def _start(self, task: "dict[str, Any]", n: int, contract: Contract, route_info: "dict[str, Any]",
               switch: "Optional[dict[str, Any]]", seed: "Optional[relay.Seed]" = None) -> str:
        """One transaction: capacity check, attempt row, project lock and STARTING - or nothing at all."""
        task_id = task["task_id"]
        config = {"route": route_info, "risk": contract.risk, "allow_write": contract.writes,
                  "timeouts": contract.timeouts(self.settings.default_timeouts), "snapshot_digest": route_info["snapshot_digest"]}
        if seed is not None:
            config["relay"] = {"from_attempt": seed.from_attempt, "patch_sha256": seed.patch_sha256, "files": seed.files}
        try:
            with self.store.tx():
                fresh = self.store.get_task(task_id)
                if fresh is None or fresh["version"] != task["version"] or fresh["status"] != T.QUEUED.value:
                    raise Busy("changed", f"{task_id} changed while it was being started")
                running, limit = len(self.store.unfinished_attempts()), self.capacity()
                if running >= limit:
                    raise Busy("capacity", f"{running} attempt(s) are already running; this profile allows {limit} at a time")
                if switch:
                    self.store.update_task(task_id, "WORKER_SWITCHED", reason=f"switched from {switch['from']} to {switch['to']}",
                                           columns={"pinned_worker": switch["to"], "switch_budget": fresh["switch_budget"] - 1},
                                           data=switch)
                self.store.insert_attempt(task_id, n, worker_id=route_info["worker"], provider_id=route_info["provider"],
                                          model=route_info["model"], kernel_id=self.kernel_id, config=config)
                self.store.acquire_lock(task["project_id"], task_id, n, self.kernel_id)
                self.store.set_status(task_id, T.STARTING, reason=f"attempt {n} on {route_info['worker']}", attempt_n=n,
                                      columns={"attempt_count": n}, data={"worker": route_info["worker"], "route": route_info["explain"]})
                if seed is not None:
                    self._write_relay_packet(task_id, seed)
                    self.store.add_event("RELAY_SEED", task_id=task_id, attempt_n=n,
                                         reason=f"attempt {n} continues the verified work of attempt {seed.from_attempt} ({seed.files} file(s))",
                                         data={"from_attempt": seed.from_attempt, "patch_sha256": seed.patch_sha256, "files": seed.files,
                                               "packet": f"handoff/relay-{seed.from_attempt}-to-{n}"})
                return self.store.get_attempt(task_id, n)["started_at"]
        except LockBusy as exc:
            raise Busy("project_locked", str(exc)) from None
        except VersionConflict:
            raise Busy("changed", f"{task_id} changed while it was being started") from None

    def _write_relay_packet(self, task_id: str, seed: "relay.Seed") -> None:
        """The relay packet is kept with the task (plan: a task-scoped hand-off file, not a second STATE.md)."""
        folder = self.data_dir / "tasks" / task_id / "handoff"
        private_dirs(folder, self.data_dir)
        stem = f"relay-{seed.from_attempt}-to-{seed.to_attempt}"
        write_private(folder / f"{stem}.json", json.dumps(seed.document, indent=2, sort_keys=True, ensure_ascii=False))
        write_private(folder / f"{stem}.md", seed.section + "\n")

    def _run_attempt(self, task_id: str, n: int, contract: Contract, project: ProjectEntry, route_info: "dict[str, Any]",
                     prompt: str, started_at: str, seed: "Optional[relay.Seed]" = None, manual: "Optional[dict[str, Any]]" = None) -> RunReport:
        task = self._require(task_id)
        try:
            if manual is not None:                    # a person's result is being taken in (hand-off): no process, the same measuring
                outcome = self.runner.execute_manual(self.store, task, n, contract=contract, project=project, route=route_info,
                                                     prompt_text=prompt, started_at=started_at, incoming=manual)
            else:
                outcome = self.runner.execute(self.store, task, n, contract=contract, project=project, route=route_info,
                                              prompt_text=prompt, started_at=started_at, seed_patch=seed.patch if seed else None)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._settle_interrupted(task_id, n, type(exc).__name__)
            self._drop_scratch(task_id, n)
            raise
        self._settle(task_id, n, contract, outcome)
        self._drop_scratch(task_id, n)
        final = self._require(task_id)
        return RunReport(task_id, final["status"], n, outcome.reason)

    def _drop_scratch(self, task_id: str, n: int) -> None:
        """The pristine and verification copies are the Kernel's own temporary trees; the Worker's copy is kept as evidence."""
        attempt_dir = self.runner.attempt_dir(task_id, n)
        for name in ("ref", "verify"):
            remove_scratch(attempt_dir / name, attempt_dir)

    # ------------------------------------------------------------------------------------------------------
    # settling an attempt: what its outcome means for the task
    def _settle_interrupted(self, task_id: str, n: int, why: str) -> None:
        """The Kernel itself was interrupted (Ctrl-C, SIGTERM): leave a clean, honest record."""
        with self.store.tx():
            attempt = self.store.get_attempt(task_id, n)
            task = self.store.get_task(task_id)
            if attempt is None or attempt["state"] == "ENDED":
                return
            self.store.end_attempt(task_id, n, end_kind="INTERRUPTED", failure_class="UNKNOWN", stage="kernel")
            self.store.release_lock(task["project_id"], task_id)
            self.store.add_event("ATTEMPT_ENDED", task_id=task_id, attempt_n=n, reason=f"the Kernel was interrupted ({why})",
                                 data={"end_kind": "INTERRUPTED"})
            status = T(task["status"])
            target = T.CANCELED if status == T.CANCEL_REQUESTED else T.INTERRUPTED
            if status in ACTIVE:
                self.store.set_status(task_id, target, attempt_n=n, reason=f"the Kernel was interrupted ({why}); partial work, if any, is in the workspace")

    def _settle(self, task_id: str, n: int, contract: Contract, outcome: AttemptOutcome) -> None:
        envelope = outcome.envelope
        problems = R.envelope_problems(envelope)
        run = outcome.run
        attempt_dir = self.runner.attempt_dir(task_id, n)
        write_private(attempt_dir / "result.json", json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False))
        with self.store.tx():
            task = self._require(task_id)
            route = self.store.get_attempt(task_id, n)["config"]["route"]
            self.store.end_attempt(task_id, n, end_kind=outcome.end_kind, failure_class=outcome.failure_class, stage=outcome.stage,
                                   exit_code=run.exit_code if run else None, signal=run.signal if run else None,
                                   timeout_kind=outcome.timeout_kind, result=envelope)
            self.store.release_lock(task["project_id"], task_id)
            self.store.add_event("ATTEMPT_ENDED", task_id=task_id, attempt_n=n, reason=outcome.reason,
                                 data={"end_kind": outcome.end_kind, "failure_class": outcome.failure_class, "stage": outcome.stage,
                                       "kind": outcome.kind, "exit_code": run.exit_code if run else None,
                                       "signal": run.signal if run else None, "timeout": outcome.timeout_kind})
            if problems:
                self.store.add_event("ENVELOPE_INVALID", task_id=task_id, attempt_n=n, reason="the result envelope failed its schema",
                                     data={"problems": problems[:5]})
            if outcome.provider_ok:
                self.health.record_success(route["provider"], route["model"], task_id=task_id)
            self._apply(task, n, contract, route, outcome, bool(problems))

    def _apply(self, task: "dict[str, Any]", n: int, contract: Contract, route: "dict[str, Any]", outcome: AttemptOutcome,
               envelope_bad: bool) -> None:
        """Choose the task's next status. Runs inside the settle transaction."""
        task_id = task["task_id"]
        snapshot = task["profile"]
        policy = snapshot.get("worker_failure_policy", "ask")
        status = T(task["status"])
        auto_ok = policy == "safe_auto" and contract.risk in AUTO_RISKS

        def go(to: T, reason: str, **columns: Any) -> None:
            self.store.set_status(task_id, to, reason=reason, attempt_n=n, columns=columns or None)

        if status not in ACTIVE:
            # someone (recovery) moved the task while its Kernel was still alive; the attempt is on record, the state stays
            if T.INTERRUPTED in TRANSITIONS[status]:
                go(T.INTERRUPTED, "the attempt ended after recovery had already flagged this task; its result is kept")
            return
        if status == T.CANCEL_REQUESTED or task["cancel_requested"] or outcome.kind == "canceled":
            if status != T.CANCEL_REQUESTED:
                self.store.update_task(task_id, "TASK_UPDATED", reason="cancel flag recorded", columns={"cancel_requested": 1}, attempt_n=n)
                go(T.CANCEL_REQUESTED, "cancel requested")
            go(T.CANCELED, "canceled on request; the Worker was stopped" if outcome.kind == "canceled" else "canceled; the attempt had already finished")
            return

        k, fc = outcome.kind, outcome.failure_class
        if k == "success":
            go(T.REVIEW_REQUIRED, "the Worker finished cleanly and every check passed - waiting for your review"
               + ("; NOTE: the result envelope failed its schema, so it cannot be accepted" if envelope_bad else ""),
               last_failure=None)
            return
        if k in ("boundary", "validation_failed", "verify_failed", "spawn_failed", "prepare_failed", "internal"):
            go(T.FAILED, outcome.reason, last_failure=f"{outcome.stage}: {outcome.reason}"[:300])
            return
        if k == "timeout":
            go(T.INTERRUPTED, f"stopped on a {outcome.timeout_kind} timeout; silence is not proof the process was dead",
               last_failure=f"timeout ({outcome.timeout_kind})")
            self._after_failure(task, n, contract, route, policy, auto_ok, T.INTERRUPTED, fc, retry_after=None)
            return
        # provider failures, by structured class
        retry_after = outcome.retry_after
        detail = f"{fc} from {route['provider']}/{route['model']}"
        if fc in ("RATE_LIMITED", "QUOTA_EXHAUSTED", "AUTH_REQUIRED", "MODEL_UNAVAILABLE"):
            self.health.record_failure(fc, route["provider"], route["model"], retry_after=retry_after, task_id=task_id,
                                       reset_at=outcome.reset_at)
        if fc == "RATE_LIMITED":
            go(T.RATE_LIMITED, detail, last_failure=detail)
        elif fc == "QUOTA_EXHAUSTED":
            go(T.QUOTA_EXHAUSTED, detail, last_failure=detail)
        elif fc == "AUTH_REQUIRED":
            go(T.AUTH_REQUIRED, detail + " - sign in with the vendor's official client", last_failure=detail)
        elif fc == "MODEL_UNAVAILABLE":
            go(T.BLOCKED_NO_WORKER, f"{route['model']} is not available from {route['provider']}", last_failure=detail)
        elif fc == "PERMISSION_BLOCKED":
            self.store.add_approval(task_id, "PERMISSION_RETRY", {"worker": route["worker"], "attempt": n,
                                                                  "note": "the Worker was refused a permission; nothing is escalated automatically"})
            go(T.PERMISSION_WAIT, "the Worker was blocked by a permission; it will not be retried with wider rights unless you approve",
               last_failure=detail)
            return
        elif fc == "TRANSIENT_ERROR":
            go(T.INTERRUPTED, "a transient error ended the run", last_failure=detail)
            if auto_ok and task["retry_budget"] > 0:
                self.store.set_status(task_id, T.QUEUED, attempt_n=n, reason="one bounded automatic retry after a transient error",
                                      columns={"retry_budget": task["retry_budget"] - 1, "not_before": self._later(self.settings.transient_retry_delay)})
                self.store.add_event("AUTO_RETRY", task_id=task_id, attempt_n=n, reason="transient error: retry once",
                                     data={"not_before": self._later(self.settings.transient_retry_delay)})
                return
        elif fc == "UNKNOWN":
            go(T.INTERRUPTED, "the run ended in a way the adapter could not classify; the work is kept for you to inspect", last_failure=detail)
        else:                                       # EXECUTION_FAILED and anything new
            go(T.FAILED, "the Worker process failed", last_failure=detail)
            return
        new_status = T(self.store.get_task(task_id)["status"])
        self._after_failure(task, n, contract, route, policy, auto_ok, new_status, fc, retry_after)

    def _after_failure(self, task: "dict[str, Any]", n: int, contract: Contract, route: "dict[str, Any]", policy: str,
                       auto_ok: bool, status: T, fc: "str | None", retry_after: "float | None") -> None:
        """The failure policy: pause, switch to the next worker, or leave the decision to a person."""
        task_id = task["task_id"]
        if status == T.QUEUED:
            return
        if policy == "pause":
            if status in (T.RATE_LIMITED, T.QUOTA_EXHAUSTED, T.AUTH_REQUIRED, T.INTERRUPTED, T.BLOCKED_NO_WORKER):
                self.store.set_status(task_id, T.PAUSED, attempt_n=n, reason="paused by the failure policy; resume or retry when ready")
            return
        if fc not in ("RATE_LIMITED", "QUOTA_EXHAUSTED", "AUTH_REQUIRED", "MODEL_UNAVAILABLE"):
            return
        worker = self.store.get_task(task_id)["pinned_worker"] or contract.worker
        r = route_task(task["profile"], role=contract.role, worker=worker, risk=contract.risk, adapters=self.adapters,
                       health=self.health, exclude=[route["worker"]])
        alt = r.chosen
        if alt is None:
            return
        info = {"from": route["worker"], "to": alt.worker_id, "why": [fc]}
        if auto_ok and task["switch_budget"] > 0:
            self.store.update_task(task_id, "WORKER_SWITCHED", reason=f"{route['worker']} -> {alt.worker_id} after {fc}",
                                   columns={"pinned_worker": alt.worker_id, "switch_budget": task["switch_budget"] - 1}, attempt_n=n, data=info)
            self.store.set_status(task_id, T.QUEUED, attempt_n=n, reason=f"automatically switching to {alt.worker_id} (policy safe_auto, one switch allowed)")
        else:
            self.store.add_approval(task_id, "FALLBACK_SWITCH", info)

    # ------------------------------------------------------------------------------------------------------
    # cancel, pause, resume, retry
    def cancel(self, task_id: str, *, reason: str = "") -> "dict[str, Any]":
        task = self._require(task_id)
        status = T(task["status"])
        if status == T.CANCELED or status == T.CANCEL_REQUESTED:
            return self.describe(task_id)
        if status in (T.ACCEPTED, T.CLOSED):
            raise NotAllowed("not_cancellable", f"{task_id} is {status.value}; there is nothing left to cancel")
        if status == T.RECOVERY_REQUIRED:
            self.recover(task_id, terminate=True)
            task = self._require(task_id)
            if self.store.unfinished_attempts() and any(a["task_id"] == task_id for a in self.store.unfinished_attempts()):
                raise NotAllowed("orphan_alive", f"{task_id} still has a process that could not be verified or stopped; check it, then retry or cancel again")
        with self.store.tx():
            task = self._require(task_id)
            status = T(task["status"])
            note = f": {reason[:200]}" if reason else ""
            if status in ACTIVE:
                self.store.set_status(task_id, T.CANCEL_REQUESTED, expected_version=task["version"],
                                      reason=f"cancel requested{note}", columns={"cancel_requested": 1})
            elif status in CANCELLABLE_DIRECT or status == T.RECOVERY_REQUIRED:
                self.store.set_status(task_id, T.CANCELED, expected_version=task["version"], reason=f"canceled{note}")
                self._supersede_pending(task_id, ("FALLBACK_SWITCH", "PERMISSION_RETRY", "BASE_DRIFT", "WRITE_PERMISSION", "WORKER_OVERRIDE"),
                                        "the task was canceled")
            else:
                raise NotAllowed("not_cancellable", f"{task_id} is {status.value}")
        if T(self._require(task_id)["status"]) == T.CANCEL_REQUESTED:
            self.recover(task_id)                       # if the owning Kernel is gone, finish the cancellation now
        return self.describe(task_id)

    def pause(self, task_id: str, *, reason: str = "") -> "dict[str, Any]":
        task = self._require(task_id)
        status = T(task["status"])
        if status == T.PAUSED:
            return self.describe(task_id)
        if status not in PAUSABLE:
            raise NotAllowed("not_pausable", f"{task_id} is {status.value}; a running attempt cannot be paused (cancel it instead)")
        with self.store.tx():
            self.store.set_status(task_id, T.PAUSED, expected_version=task["version"], reason=f"paused{': ' + reason[:200] if reason else ''}")
        return self.describe(task_id)

    def resume(self, task_id: str) -> "dict[str, Any]":
        task = self._require(task_id)
        if task["status"] != T.PAUSED.value:
            raise NotAllowed("not_paused", f"{task_id} is {task['status']}, not PAUSED")
        with self.store.tx():
            self.store.set_status(task_id, T.QUEUED, expected_version=task["version"], reason="resumed")
            if self._pending(task_id):
                self.store.set_status(task_id, T.PERMISSION_WAIT, reason="still waiting for an approval")
        return self.describe(task_id)

    def retry(self, task_id: str, *, worker: "str | None" = None, force: bool = False, by: str = "user",
              fresh: bool = False) -> "dict[str, Any]":
        """A NEW attempt. This is the explicit approval that lets configuration changes apply.

        A file-changing task continues the verified work of its stopped attempt (relay, plan 6.2) unless ``fresh`` asks for the baseline."""
        task = self._require(task_id)
        status = T(task["status"])
        if fresh and status == T.QUEUED:
            # the way out of a refused relay (the previous attempt's patch cannot be used): nothing to re-queue, only the starting point changes
            with self.store.tx():
                self.store.update_task(task_id, "TASK_RETRIED", reason=f"fresh start requested by {by}",
                                       columns={"base_json": {**(task["base"] or {}), "relay_floor": task["attempt_count"]}}, data={"fresh": True})
            return self.describe(task_id)
        if not self._retryable(task_id, status):
            raise NotAllowed("not_retryable", f"{task_id} is {status.value}; retry applies to {', '.join(sorted(s.value for s in RETRYABLE))}"
                                              " (or PERMISSION_WAIT when only a worker-switch offer is pending)")
        if status == T.RECOVERY_REQUIRED and any(a["task_id"] == task_id for a in self.store.unfinished_attempts()):
            raise NotAllowed("orphan_alive", f"{task_id} still has an unfinished attempt; run `par task recover {task_id}` first")
        snapshot = config_snapshot(self.config)
        if worker is not None and worker not in snapshot["workers"]:
            raise Refused("unknown_worker", f"worker {worker!r} is not defined in the current configuration")
        with self.store.tx():
            task = self._require(task_id)
            columns: "dict[str, Any]" = {"retry_budget": 1, "switch_budget": 1, "not_before": None, "cancel_requested": 0}
            if snapshot["digest"] != task["profile"].get("digest"):
                columns["profile_json"] = snapshot
                self.store.add_event("CONFIG_REFRESHED", task_id=task_id, reason="the configuration changed; this explicit retry adopts it",
                                     data={"was": task["profile"].get("digest"), "now": snapshot["digest"]})
            if worker is not None:
                columns["pinned_worker"] = worker
            if fresh:                                                    # attempts up to here are never used as a starting point
                columns["base_json"] = {**(task["base"] or {}), "relay_floor": task["attempt_count"]}
            if force:
                route = route_task(columns.get("profile_json", task["profile"]), role=task["role"], worker=worker or task["pinned_worker"] or task["requested_worker"],
                                   risk=task["risk_tier"], adapters=self.adapters, health=self.health)
                for cand in route.chain:
                    if cand.provider_id:
                        self.health.clear(cand.provider_id)
            self._supersede_pending(task_id, ("FALLBACK_SWITCH", "PERMISSION_RETRY"), "superseded by an explicit retry")
            self.store.update_task(task_id, "TASK_RETRIED", reason=f"retry requested by {by}", columns=columns,
                                   data={"worker": worker, "force": force})
            self.store.set_status(task_id, T.QUEUED, reason="queued for a new attempt")
            current = self._require(task_id)
            contract = self._contract_of(current)
            expected_worker = current["pinned_worker"] or contract.worker
            # A pending override for another worker must be withdrawn before checking what remains.
            for approval in self._pending(task_id):
                if approval["kind"] == "WORKER_OVERRIDE" and approval["payload"].get("worker") != expected_worker:
                    self.store.decide_approval(approval["id"], "REJECTED", "kernel",
                                               "superseded by a retry selecting a different worker")
                    self.store.add_event("APPROVAL_DECIDED", task_id=task_id,
                                         reason="WORKER_OVERRIDE superseded by changed worker",
                                         data={"approval_id": approval["id"], "kind": "WORKER_OVERRIDE", "decision": "REJECTED"})
            missing = self._missing_approvals(current, contract)
            pending = {a["kind"] for a in self._pending(task_id)}
            for kind in missing:
                if kind not in pending:
                    payload = ({"allowed_paths": list(contract.allowed_paths)} if kind == "WRITE_PERMISSION"
                               else {"worker": expected_worker})
                    self.store.add_approval(task_id, kind, payload)
                    pending.add(kind)
            # Pending is never treated as granted: it only avoids another request for the same permission.
            if self._pending(task_id) or missing:
                self.store.set_status(task_id, T.PERMISSION_WAIT, reason="still waiting for required approval")
        return self.describe(task_id)

    def _retryable(self, task_id: str, status: T) -> bool:
        if status in RETRYABLE:
            return True
        if status == T.PERMISSION_WAIT:
            pending = self._pending(task_id)
            return bool(pending) and all(a["kind"] in SUPERSEDABLE for a in pending)
        return False

    # ------------------------------------------------------------------------------------------------------
    # review: accept / close
    def accept_blockers(self, task_id: str, *, by: str = "user") -> "list[str]":
        task = self._require(task_id)
        contract = self._contract_of(task)
        attempts = self.store.attempts(task_id)
        approved = any(a["kind"] == "WRITE_PERMISSION" and a["status"] == "APPROVED" for a in self.store.approvals(task_id))
        return R.acceptance_problems(status=task["status"], attempt=attempts[-1] if attempts else None,
                                     contract_checks=contract.checks, needs_write_approval=contract.writes,
                                     write_approved=approved, by=by)

    def accept(self, task_id: str, *, by: str = "user", note: str = "") -> "dict[str, Any]":
        problems = self.accept_blockers(task_id, by=by)
        if problems:
            raise Refused("cannot_accept", problems)
        with self.store.tx():
            task = self._require(task_id)
            approval = self.store.add_approval(task_id, "ACCEPT_RESULT", {"attempt": task["attempt_count"]})
            self.store.decide_approval(approval, "APPROVED", by, note)
            self.store.set_status(task_id, T.ACCEPTED, expected_version=task["version"],
                                  reason=f"the result of attempt {task['attempt_count']} was accepted by {by}")
        return self.describe(task_id)

    def close_task(self, task_id: str) -> "dict[str, Any]":
        task = self._require(task_id)
        if task["status"] != T.ACCEPTED.value:
            raise NotAllowed("not_accepted", f"{task_id} is {task['status']}; only ACCEPTED tasks can be closed")
        with self.store.tx():
            self.store.set_status(task_id, T.CLOSED, expected_version=task["version"], reason="closed")
        return self.describe(task_id)

    # ------------------------------------------------------------------------------------------------------
    # recovery
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

    # ------------------------------------------------------------------------------------------------------
    # reading
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
        return {
            "task_id": task_id, "project_id": task["project_id"], "title": task["title"], "risk": task["risk_tier"],
            "role": task["role"], "worker_requested": task["requested_worker"], "pinned_worker": task["pinned_worker"],
            "status": task["status"], "version": task["version"], "created_at": task["created_at"], "updated_at": task["updated_at"],
            "attempt_count": task["attempt_count"], "retry_budget": task["retry_budget"], "switch_budget": task["switch_budget"],
            "not_before": task["not_before"], "last_failure": task["last_failure"], "cancel_requested": bool(task["cancel_requested"]),
            "ad_hoc": bool(task["ad_hoc"]), "submitted_by": task["submitted_by"], "base": task["base"],
            "attempts": attempts, "pending_approvals": [{"id": a["id"], "kind": a["kind"], "payload": a["payload"],
                                                          "requested_at": a["requested_at"]} for a in self._pending(task_id)],
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
