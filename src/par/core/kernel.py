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
from par.context.interface import Context
from par.context.resolver import default_context
from par.context.types import ContextChanged, ContextManifest
from par.core import relay
from par.core.approvals import ApprovalMixin
from par.core.attempt import AttemptOutcome, AttemptRunner, private_dirs, remove_scratch, write_private
from par.core.candidate import CandidateMixin, INTEGRATION_MODE
from par.core.clock import Clock, iso, parse_iso
from par.core.contract import Contract, ContractError, parse_contract
from par.core.health import Health
from par.core.manual import ManualHandoffMixin
from par.core.prompt import build_prompt
from par.core.recovery import RecoveryAction, RecoveryMixin
from par.core.reading import ReadingMixin
from par.core.redact import redact_text
from par.core.review import ReviewMixin
from par.core.router import config_snapshot, route as route_task
from par.core.settings import KernelSettings
from par.core.states import ACTIVE, NEXT_ACTIONS, TRANSITIONS, TaskStatus as T, derived_state
from par.core.submission import SubmissionMixin, SubmitResult, ValidateResult
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
class RunReport:
    task_id: str
    status: str
    attempt: "int | None"
    reason: str


class _MissingGit:
    def __init__(self, message: str) -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        raise gitops.GitError(self._message)


class Kernel(SubmissionMixin, ApprovalMixin, ReviewMixin, RecoveryMixin, ReadingMixin, CandidateMixin, ManualHandoffMixin,
             TakeoverMixin):
    def __init__(self, data_dir: "str | os.PathLike[str]", *, config: RuntimeConfig, projects: "Mapping[str, ProjectEntry]",
                 adapters: AdapterRegistry, clock: "Clock | None" = None, home: "str | os.PathLike[str] | None" = None,
                 settings: "KernelSettings | None" = None, kernel_id: "str | None" = None,
                 context_factory: "Optional[Callable[[ProjectEntry], Context]]" = None) -> None:
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
    def _context(self, project: ProjectEntry) -> Context:
        context = self._context_factory(project) if self._context_factory else default_context(project, self.home)
        if not isinstance(context, Context):
            raise Refused("context_invalid",
                          f"the context for {project.id!r} does not implement view/manifest/render/write_readiness")
        return context

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
        return self._candidate_dependency_problem(task)

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
        if (task["base"] or {}).get("candidate"):
            return None                                  # the candidate's base is immutable; the project moving does not change what runs
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
            # Use the attempt's own reason: with zero mechanical checks "every check passed" would be
            # a false claim, even though a clean terminal event is enough to reach review.
            go(T.REVIEW_REQUIRED, f"{outcome.reason} - waiting for your review"
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
