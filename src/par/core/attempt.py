"""One attempt, from a clean working copy to a measured result.

This module *observes*: it prepares the copy, runs the Worker, compares what is on disk with the pristine
base, cuts a patch, runs the contract's checks, and reports everything as an ``AttemptOutcome``. It never
decides what the task's status becomes - the Kernel does that from the outcome and the user's policy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from par.config import ProjectEntry
from par.core.clock import Clock
from par.core.contract import Contract, forbidden_reason
from par.core.redact import redact_text
from par.core.settings import KernelSettings
from par.core.store import Store
from par.execution import gitops, results as R, workspace as ws
from par.execution.subprocesses import Limits, RunResult, SpawnError, run_process
from par.execution.validation import CHILD_PATH, CheckResult, run_checks
from par.providers.base import FailureClass, ProviderAdapter, ProviderEvent, RunOutcome, RunRequest
from par.providers.registry import AdapterRegistry, NoAdapter

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
MAX_PATHS_IN_RESULT = 50
MAX_NOISE_LISTED = 200


class Heartbeat(threading.Thread):
    """Proves the Kernel *process* is alive, on its own connection, whatever its main thread is busy with."""

    def __init__(self, data_dir: Path, clock: Clock, task_id: str, n: int, project_id: str, interval: float) -> None:
        super().__init__(daemon=True, name=f"par-heartbeat-{task_id}-{n}")
        self.data_dir, self.clock, self.task_id, self.n, self.project_id = data_dir, clock, task_id, n, project_id
        self.interval = interval
        self.stopped = threading.Event()
        self.beats = 0
        self.errors = 0

    def run(self) -> None:
        store = Store(self.data_dir, self.clock)
        try:
            while not self.stopped.wait(self.interval):
                try:
                    with store.tx():
                        store.heartbeat(self.task_id, self.n, self.project_id)
                    self.beats += 1
                except Exception:
                    self.errors += 1
        finally:
            store.close()

    def stop(self) -> None:
        self.stopped.set()
        self.join(timeout=5)


@dataclass
class AttemptOutcome:
    kind: str                 # success | provider_failure | timeout | canceled | spawn_failed | prepare_failed | boundary | verify_failed | validation_failed | internal
    end_kind: str             # COMPLETED | FAILED | INTERRUPTED | CANCELED
    failure_class: "str | None"
    stage: "str | None"
    reason: str
    envelope: "dict[str, Any]"
    run: "RunResult | None" = None
    retry_after: "float | None" = None
    provider_ok: bool = False
    partial: bool = False
    timeout_kind: "str | None" = None
    reset_at: "str | None" = None


@dataclass
class Capture:
    classification: ws.Classification
    patch: "bytes | None" = None
    stat: "dict[str, int]" = field(default_factory=lambda: {"files": 0, "additions": 0, "deletions": 0})
    verify_dir: "Path | None" = None
    excluded: "tuple[str, ...]" = ()
    error: "str | None" = None


def write_private(path: Path, data: "bytes | str") -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def private_dirs(path: Path, root: Path) -> None:
    """Create ``path`` and every missing parent below ``root`` with mode 0700 (``mkdir(parents=True)`` would not)."""
    missing = []
    probe = path
    while probe != root and not probe.exists():
        missing.append(probe)
        probe = probe.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)


def child_env(adapter_env: "dict[str, str]", home: Path, tmp: Path) -> "dict[str, str]":
    """A clean environment: nothing is inherited from the user's shell; the adapter names what else it needs."""
    env = {"PATH": CHILD_PATH, "HOME": str(home), "TMPDIR": str(tmp), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1", "CI": "1"}
    for key, value in dict(adapter_env).items():
        if isinstance(key, str) and isinstance(value, str) and _ENV_NAME.match(key) and "\x00" not in value:
            env[key] = value
    return env


def remove_scratch(path: Path, root: Path) -> None:
    """Delete a scratch tree the Kernel made itself - and only that: never a symlink, never outside ``root``."""
    if not os.path.lexists(path) or os.path.islink(path):
        return
    real, real_root = os.path.realpath(path), os.path.realpath(root)
    if not real.startswith(real_root + os.sep):
        return
    shutil.rmtree(path, ignore_errors=True)


class AttemptRunner:
    def __init__(self, *, data_dir: Path, clock: Clock, settings: KernelSettings, adapters: AdapterRegistry,
                 git: gitops.Git) -> None:
        self.data_dir, self.clock, self.settings, self.adapters, self.git = data_dir, clock, settings, adapters, git

    def attempt_dir(self, task_id: str, n: int) -> Path:
        return self.data_dir / "tasks" / task_id / "attempts" / str(n)

    # ------------------------------------------------------------------------------------------------------
    def _base_source(self, project: ProjectEntry, base: "Mapping[str, Any]") -> Path:
        """Where this attempt's mirror comes from: the project itself, or a candidate repository under our data dir."""
        rel = base.get("candidate_repo")
        if not rel:
            return project.path
        root = Path(os.path.realpath(Path(self.data_dir) / "candidates"))
        source = Path(os.path.realpath(Path(self.data_dir) / str(rel)))
        if root not in source.parents:
            raise gitops.GitError("the candidate repository is outside the Runtime's data directory")
        return source

    def execute(self, store: Store, task: "dict[str, Any]", n: int, *, contract: Contract, project: ProjectEntry,
                route: "dict[str, Any]", prompt_text: str, started_at: str, seed_patch: "bytes | None" = None) -> AttemptOutcome:
        task_id = task["task_id"]
        heartbeat = Heartbeat(self.data_dir, self.clock, task_id, n, task["project_id"], self.settings.heartbeat_interval)
        heartbeat.start()
        attempt_dir = self.attempt_dir(task_id, n)
        try:
            outcome = self._execute(store, task, n, contract, project, route, prompt_text, started_at, attempt_dir, seed_patch)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:                      # a Kernel bug must end the attempt cleanly, not lose it
            outcome = self._end(task=task, n=n, route=route, started_at=started_at, attempt_dir=attempt_dir, contract=contract,
                                kind="internal", end_kind="FAILED", failure_class=None, stage="kernel",
                                reason=f"internal error: {type(exc).__name__}", run=None, events=[], capture=None, checks=[])
        finally:
            heartbeat.stop()
        return outcome

    def execute_manual(self, store: Store, task: "dict[str, Any]", n: int, *, contract: Contract, project: ProjectEntry,
                       route: "dict[str, Any]", prompt_text: str, started_at: str, incoming: "dict[str, Any]") -> AttemptOutcome:
        """The same measuring as a Worker's attempt, for a result a person brought back (hand-off, plan 5.5): no process is started."""
        task_id = task["task_id"]
        heartbeat = Heartbeat(self.data_dir, self.clock, task_id, n, task["project_id"], self.settings.heartbeat_interval)
        heartbeat.start()
        attempt_dir = self.attempt_dir(task_id, n)
        try:
            outcome = self._execute_manual(store, task, n, contract, project, route, prompt_text, started_at, attempt_dir, incoming)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            outcome = self._end(task=task, n=n, route=route, started_at=started_at, attempt_dir=attempt_dir, contract=contract,
                                kind="internal", end_kind="FAILED", failure_class=None, stage="kernel",
                                reason=f"internal error: {type(exc).__name__}", run=None, events=[], capture=None, checks=[])
        finally:
            heartbeat.stop()
        return outcome

    def _execute_manual(self, store: Store, task: "dict[str, Any]", n: int, contract: Contract, project: ProjectEntry,
                        route: "dict[str, Any]", prompt_text: str, started_at: str, attempt_dir: Path,
                        incoming: "dict[str, Any]") -> AttemptOutcome:
        task_id = task["task_id"]
        common = dict(task=task, n=n, route=route, started_at=started_at, attempt_dir=attempt_dir, contract=contract)
        base = task["base"]
        try:
            private_dirs(attempt_dir, self.data_dir)
            home, tmp = attempt_dir / "home", attempt_dir / "tmp"
            home.mkdir(mode=0o700, exist_ok=True)
            tmp.mkdir(mode=0o700, exist_ok=True)
            workspace = attempt_dir / "workspace"
            if contract.risk == "R0":
                workspace.mkdir(mode=0o700)
                prepared = ws.Prepared(workspace, None)
            else:
                ref = attempt_dir / "ref"
                excluded = gitops.clone_at(self.git, self._base_source(project, base), ref, base.get("commit"))
                if "source" in incoming:
                    ws.stage_import(incoming["source"], workspace)
                    marker = workspace / ".par-handoff.json"          # our own bookkeeping file, not part of the work
                    if marker.is_file() and not marker.is_symlink():
                        marker.unlink()
                else:
                    workspace.mkdir(mode=0o700)
                    top = os.fspath(ref)
                    shutil.copytree(ref, workspace, symlinks=True, dirs_exist_ok=True,
                                    ignore=lambda directory, names: {".git"} if directory == top else set())
                    os.chmod(workspace, 0o700)
                    if "patch" in incoming:
                        protected = [p for p in gitops.patch_paths(self.git, incoming["patch"]) if forbidden_reason(p)]
                        if protected:
                            raise ws.ImportRefused("the patch touches protected paths: " + ", ".join(protected[:5]))
                        ws.apply_seed(self.git, workspace, incoming["patch"])
                prepared = ws.Prepared(workspace, ref, tuple(excluded))
        except (gitops.GitError, OSError, ws.ImportRefused, ws.TreeTooLarge) as exc:
            return self._end(kind="prepare_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="prepare",
                             reason=f"could not take in the hand-off result: {redact_text(str(exc))[:300]}", run=None, events=[],
                             capture=None, checks=[], **common)
        write_private(attempt_dir / "prompt.txt", prompt_text)
        prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        with store.tx():
            store.update_attempt(task_id, n, workspace_path=str(prepared.workspace), artifact_dir=str(attempt_dir), prompt_sha256=prompt_sha)
        events = [ProviderEvent("message", {"text": incoming["text"]})] if "text" in incoming else []
        if self._cancel_requested(store, task_id):
            return self._canceled(store, prepared, common, events=[], run=None)
        capture = self._capture(prepared, contract, base.get("commit"), attempt_dir, noise_dirs=tuple(sorted(ws.APP_NOISE_DIRS)))
        return self._verify_and_end(store, prepared=prepared, capture=capture, run=None, events=events, common=common,
                                    attempt_dir=attempt_dir, home=home, tmp=tmp, provider_ok=False, who="the hand-off result")

    @staticmethod
    def _cleanup(adapter: Any, request: RunRequest, home: Path, tmp: Path) -> None:
        """Let the adapter undo what its ``prepare`` set up and must not outlive the run (never raises into the Kernel)."""
        hook = getattr(adapter, "cleanup", None)
        if hook is None:
            return
        try:
            hook(request, home, tmp)
        except Exception:
            pass

    def _cancel_requested(self, store: Store, task_id: str) -> bool:
        row = store.get_task(task_id)
        return bool(row and row["cancel_requested"])

    def _execute(self, store: Store, task: "dict[str, Any]", n: int, contract: Contract, project: ProjectEntry,
                 route: "dict[str, Any]", prompt_text: str, started_at: str, attempt_dir: Path,
                 seed_patch: "bytes | None" = None) -> AttemptOutcome:
        task_id, risk = task["task_id"], contract.risk
        common = dict(task=task, n=n, route=route, started_at=started_at, attempt_dir=attempt_dir, contract=contract)
        base = task["base"]

        # the adapter is asked first: it may say what the Worker's working copy should look like
        try:
            adapter = self.adapters.create(route["provider"])
        except NoAdapter:
            adapter = None
        with_git = bool(getattr(adapter, "workspace_with_git", True))
        note = getattr(adapter, "prompt_note", None)
        if note is not None:                          # what this CLI's environment will not let the Worker do: told up front, and part of the saved prompt
            try:
                extra = note(allow_write=contract.writes)
            except Exception:
                extra = None
            if isinstance(extra, str) and extra.strip():
                prompt_text = prompt_text.rstrip("\n") + "\n\n## Environment\n" + extra.strip() + "\n"

        # 1. the working copy
        try:
            private_dirs(attempt_dir, self.data_dir)
            home, tmp = attempt_dir / "home", attempt_dir / "tmp"
            home.mkdir(mode=0o700, exist_ok=True)
            tmp.mkdir(mode=0o700, exist_ok=True)
            prepared = ws.prepare(self.git, source=self._base_source(project, base) if risk != "R0" else None, base_commit=base.get("commit"),
                                  attempt_dir=attempt_dir, risk=risk, with_git=with_git)
        except (gitops.GitError, OSError) as exc:
            return self._end(kind="prepare_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="prepare",
                             reason=f"could not prepare the working copy: {redact_text(str(exc))[:200]}", run=None, events=[],
                             capture=None, checks=[], **common)
        if seed_patch:                                 # relay: the previous attempt's verified work is already in this copy when the Worker starts
            try:
                ws.apply_seed(self.git, prepared.workspace, seed_patch)
            except (gitops.GitError, OSError) as exc:
                return self._end(kind="prepare_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="prepare",
                                 reason=f"could not carry over the previous attempt's changes: {redact_text(str(exc))[:200]}", run=None,
                                 events=[], capture=None, checks=[], **common)
        write_private(attempt_dir / "prompt.txt", prompt_text)
        prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        with store.tx():
            store.update_attempt(task_id, n, workspace_path=str(prepared.workspace), artifact_dir=str(attempt_dir),
                                 prompt_sha256=prompt_sha)
        if self._cancel_requested(store, task_id):
            return self._canceled(store, prepared, common, events=[], run=None)

        # 2. the Worker
        if adapter is None:
            return self._end(kind="spawn_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="spawn",
                             reason=f"no adapter is registered for {route['provider']!r}", run=None, events=[], capture=None,
                             checks=[], **common)
        request = RunRequest(task_id, n, prompt_text, prepared.workspace, route["model"], allow_write=contract.writes,
                             allowed_paths=contract.allowed_paths if contract.writes else ())
        refusal = getattr(adapter, "preflight", lambda r: None)(request)
        if refusal:
            return self._end(kind="spawn_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="preflight",
                             reason=f"the adapter refused to start: {redact_text(str(refusal))[:200]}", run=None, events=[],
                             capture=None, checks=[], **common)
        prepare = getattr(adapter, "prepare", None)
        if prepare is not None:                       # the adapter may fill the private HOME/TMPDIR (a settings file the CLI reads)
            try:
                prepare(request, home, tmp)
            except Exception as exc:
                self._cleanup(adapter, request, home, tmp)                    # prepare may have got half way
                return self._end(kind="spawn_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="preflight",
                                 reason=f"the adapter could not prepare the Worker's environment: {redact_text(str(exc))[:200]}",
                                 run=None, events=[], capture=None, checks=[], **common)
        stdin_data = getattr(adapter, "stdin_data", lambda r: None)(request)
        timeouts = contract.timeouts(self.settings.default_timeouts)
        limits = Limits(startup=timeouts["startup"], idle=timeouts["idle"], total=timeouts["total"], grace=self.settings.grace,
                        max_log_bytes=self.settings.max_log_bytes, poll=self.settings.poll, tick=3600.0,
                        cancel_check=self.settings.cancel_check)

        def on_started(pid: int, pgid: int, identity: "str | None") -> None:
            with store.tx():
                store.update_attempt(task_id, n, state="RUNNING", pid=pid, pgid=pgid, proc_identity=identity)
                current = store.get_task(task_id)
                if current and current["status"] == "STARTING":
                    store.set_status(task_id, "RUNNING", reason="worker process started", attempt_n=n,
                                     data={"pid": pid, "pgid": pgid})

        try:
            run = run_process(
                adapter.build_argv(request), cwd=prepared.workspace,
                env=child_env(dict(adapter.environment(request)), home, tmp), limits=limits, parse_line=adapter.parse_event,
                stdout_log=attempt_dir / "stdout.log", stderr_log=attempt_dir / "stderr.log",
                request_cancel=adapter.request_cancel, on_started=on_started,
                should_cancel=lambda: self._cancel_requested(store, task_id), stdin_data=stdin_data,
                parse_stderr=getattr(adapter, "parse_stderr", None))
        except SpawnError as exc:
            return self._end(kind="spawn_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="spawn",
                             reason=str(exc), run=None, events=[], capture=None, checks=[], **common)
        finally:
            self._cleanup(adapter, request, home, tmp)                        # the child has ended (or never started)

        # 3. what happened to the process
        if run.canceled or self._cancel_requested(store, task_id):
            return self._canceled(store, prepared, common, events=run.events, run=run)
        capture = self._capture(prepared, contract, base.get("commit"), attempt_dir)
        if run.timed_out:
            return self._end(kind="timeout", end_kind="INTERRUPTED", failure_class="UNKNOWN", stage="worker",
                             reason=f"the Worker was stopped: {run.timed_out} timeout", run=run, events=run.events,
                             capture=capture, checks=[], timeout_kind=run.timed_out, **common)
        try:
            failure = adapter.classify_failure(RunOutcome(run.exit_code, run.events, False, run.signal, run.stderr_tail))
        except Exception:
            failure = FailureClass.UNKNOWN
        if failure is not None:
            retry_after = next((e.data.get("retry_after") for e in run.events if e.kind == "error" and "retry_after" in e.data), None)
            kind = "provider_failure"
            return self._end(kind=kind, end_kind="INTERRUPTED" if failure is FailureClass.UNKNOWN else "FAILED",
                             failure_class=failure.value, stage="worker",
                             reason=f"the Worker run ended as {failure.value}", run=run, events=run.events, capture=capture,
                             checks=[], retry_after=retry_after if isinstance(retry_after, (int, float)) else None, **common)

        # 4. clean provider success: now verify it ourselves
        return self._verify_and_end(store, prepared=prepared, capture=capture, run=run, events=run.events, common=common,
                                    attempt_dir=attempt_dir, home=home, tmp=tmp)

    def _verify_and_end(self, store: Store, *, prepared: ws.Prepared, capture: Capture, run: "RunResult | None", events: list,
                        common: "dict[str, Any]", attempt_dir: Path, home: Path, tmp: Path, provider_ok: bool = True,
                        who: str = "the Worker") -> AttemptOutcome:
        """What the Kernel believes only after measuring it itself: the comparison, the boundary, the contract's checks. Shared by a Worker
        that ran as a process and by a result a person brought back (the hand-off, plan 5.5)."""
        contract, task_id, n = common["contract"], common["task"]["task_id"], common["n"]
        if capture.error:
            return self._end(kind="verify_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="verify",
                             reason=capture.error, run=run, events=events, capture=capture, checks=[], provider_ok=provider_ok, **common)
        if capture.classification.violations:
            n_bad = len(capture.classification.violations)
            return self._end(kind="boundary", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="boundary",
                             reason=f"{n_bad} change(s) crossed the permission boundary", run=run, events=events,
                             capture=capture, checks=[], provider_ok=provider_ok, **common)
        with store.tx():
            current = store.get_task(task_id)
            if current and current["status"] == "RUNNING":
                store.update_attempt(task_id, n, state="VERIFYING")
                store.set_status(task_id, "VERIFYING", reason="the Worker finished cleanly; verifying", attempt_n=n)
        if self._cancel_requested(store, task_id):
            return self._canceled(store, prepared, common, events=events, run=run)
        checks: "list[CheckResult]" = []
        if contract.writes and contract.checks and capture.verify_dir is not None:
            def on_check_started(pid: int, pgid: int, identity: "str | None") -> None:
                with store.tx():
                    store.update_attempt(task_id, n, pid=pid, pgid=pgid, proc_identity=identity)
            checks = run_checks(contract.checks, cwd=capture.verify_dir, attempt_dir=attempt_dir, home=home, tmp=tmp,
                                should_cancel=lambda: self._cancel_requested(store, task_id), grace=self.settings.check_grace,
                                on_started=on_check_started)
            if self._cancel_requested(store, task_id):
                return self._canceled(store, prepared, common, events=events, run=run, checks=checks)
            if any(c.status != "PASS" for c in checks):
                bad = [c.name for c in checks if c.status != "PASS"]
                return self._end(kind="validation_failed", end_kind="FAILED", failure_class="EXECUTION_FAILED", stage="validation",
                                 reason=f"check(s) did not pass: {', '.join(bad)}", run=run, events=events, capture=capture,
                                 checks=checks, provider_ok=provider_ok, **common)
        return self._end(kind="success", end_kind="COMPLETED", failure_class=None, stage=None,
                         reason=f"{who} finished and every check passed", run=run, events=events, capture=capture,
                         checks=checks, provider_ok=provider_ok, **common)

    # ------------------------------------------------------------------------------------------------------
    def _canceled(self, store: Store, prepared: ws.Prepared, common: "dict[str, Any]", *, events: list,
                  run: "RunResult | None", checks: "list[CheckResult] | None" = None) -> AttemptOutcome:
        capture = self._capture(prepared, common["contract"], common["task"]["base"].get("commit"), common["attempt_dir"])
        return self._end(kind="canceled", end_kind="CANCELED", failure_class=None, stage=None,
                         reason="canceled on request", run=run, events=events, capture=capture, checks=checks or [], **common)

    def _capture(self, prepared: ws.Prepared, contract: Contract, base_commit: "str | None", attempt_dir: Path,
                 noise_dirs: "tuple[str, ...]" = ()) -> Capture:
        """Compare the Worker's copy with the pristine base as plain files; cut and cross-check the patch."""
        try:
            work = ws.scan_tree(prepared.workspace)
            if prepared.ref is None:
                changes = [ws.Change(p, "A", None, e) for p, e in sorted(work.items())]
            else:
                changes = ws.diff_trees(prepared.ref, ws.scan_tree(prepared.ref), prepared.workspace, work)
            added = [c.path for c in changes if c.status == "A"]
            ignored = gitops.ignored_among(self.git, prepared.ref, added) if prepared.ref is not None and added else set()
            cls = ws.classify(changes, allowed_paths=contract.allowed_paths, writes=contract.writes, ignored=ignored, noise_dirs=noise_dirs)
        except (ws.TreeTooLarge, ws.TooManyChanges, OSError, gitops.GitError) as exc:
            empty = ws.Classification()
            return Capture(empty, error=f"could not judge the Worker's changes: {redact_text(str(exc))[:200]}", excluded=prepared.excluded)
        cap = Capture(cls, excluded=prepared.excluded)
        if contract.writes and prepared.ref is not None and base_commit:
            try:
                verify = attempt_dir / "verify"
                remove_scratch(verify, attempt_dir)                  # (a crashed attempt may have left a half-built one)
                ws.build_verify_tree(prepared.ref, verify, prepared.workspace, cls.allowed)
                gitops.add_intent(self.git, verify, [c.path for c in cls.allowed if c.status == "A"])
                patch = gitops.diff_patch(self.git, verify, base_commit)
                cap.stat = gitops.diff_numstat(self.git, verify, base_commit)
                if patch:
                    gitops.apply_patch(self.git, prepared.ref, patch)                # does it reproduce the verified tree?
                    drift = ws.tree_equal(prepared.ref, verify)
                    if drift:
                        cap.error = f"the patch does not reproduce the verified tree ({len(drift)} file(s) differ)"
                    write_private(attempt_dir / "work.patch", patch)
                    cap.patch = patch
                cap.verify_dir = verify
            except (ws.EscapeAttempt, OSError, gitops.GitError) as exc:
                cap.error = f"could not build the patch: {redact_text(str(exc))[:200]}"
        write_private(attempt_dir / "changes.json", json.dumps(self._changes_doc(cap, base_commit), indent=2, sort_keys=True))
        return cap

    def capture_orphan(self, task: "dict[str, Any]", attempt: "dict[str, Any]", contract: Contract, project: ProjectEntry) -> "Capture | None":
        """Judge what an orphaned attempt left in its workspace (the Kernel that ran it is gone)."""
        attempt_dir = self.attempt_dir(task["task_id"], attempt["n"])
        base = task["base"].get("commit")
        try:
            if contract.risk == "R0":
                prepared = ws.Prepared(Path(attempt["workspace_path"]), None)
            else:
                ref = attempt_dir / "ref"
                remove_scratch(ref, attempt_dir)                     # never trust a pristine copy a crash may have cut short
                gitops.clone_at(self.git, self._base_source(project, task["base"]), ref, base)
                os.chmod(ref, 0o700)
                prepared = ws.Prepared(Path(attempt["workspace_path"]), ref)
        except (gitops.GitError, OSError, TypeError):
            return None
        return self._capture(prepared, contract, base, attempt_dir)

    def orphan_outcome(self, task: "dict[str, Any]", attempt: "dict[str, Any]", contract: Contract, route: "dict[str, Any]",
                       capture: "Capture | None", reason: str) -> AttemptOutcome:
        return self._end(task=task, n=attempt["n"], route=route, started_at=attempt["started_at"],
                         attempt_dir=self.attempt_dir(task["task_id"], attempt["n"]), contract=contract, kind="orphaned",
                         end_kind="ORPHANED", failure_class=None, stage="recovery", reason=reason, run=None, events=[],
                         capture=capture, checks=[])

    @staticmethod
    def _changes_doc(cap: Capture, base_commit: "str | None") -> "dict[str, Any]":
        cls = cap.classification
        return {
            "base": base_commit,
            "allowed": [{"path": c.path, "status": c.status, "size": c.new.size if c.new else 0, "exec": bool(c.new and c.new.exec)}
                        for c in cls.allowed],
            "violations": [{"path": v.path, "status": v.status, "reason": v.reason, "protected": v.protected} for v in cls.violations],
            "noise_count": len(cls.noise), "noise": sorted(cls.noise)[:MAX_NOISE_LISTED],
            "left_out_of_the_copy": list(cap.excluded), "patch": "work.patch" if cap.patch else None, "stat": cap.stat,
            "error": cap.error,
        }

    # ------------------------------------------------------------------------------------------------------
    def _end(self, *, task: "dict[str, Any]", n: int, route: "dict[str, Any]", started_at: str, attempt_dir: Path,
             contract: Contract, kind: str, end_kind: str, failure_class: "str | None", stage: "str | None", reason: str,
             run: "RunResult | None", events: list, capture: "Capture | None", checks: "list[CheckResult]",
             retry_after: "float | None" = None, provider_ok: bool = False, timeout_kind: "str | None" = None) -> AttemptOutcome:
        cls = capture.classification if capture else ws.Classification()
        partial = bool(capture and capture.patch) and end_kind != "COMPLETED"
        issues: "list[str]" = []
        if cls.violations:
            issues.append(f"{len(cls.violations)} change(s) crossed the permission boundary (see changes.json)")
        if cls.noise:
            issues.append(f"{len(cls.noise)} new file(s) were ignored as build noise or by the project's ignore rules")
        if capture and capture.excluded:
            issues.append(f"{len(capture.excluded)} tracked sensitive-looking file(s) were left out of the Worker's copy")
        if capture and capture.error:
            issues.append(capture.error)
        if end_kind != "COMPLETED":
            issues.append(reason)
        if run is not None and run.logs_truncated:
            issues.append("the process output exceeded the log cap and was truncated")
        if run is not None and run.strays_killed:
            issues.append("the Worker left processes behind; they were stopped")
        if run is not None and run.unparsed:
            issues.append(f"{run.unparsed} output line(s) could not be parsed")
        for note in R.worker_warnings(events):
            issues.append(f"worker run: {note}")
        if contract.writes and not contract.checks and end_kind == "COMPLETED":
            issues.append("the contract has no mechanical check: acceptance rests on your review of the diff")
        if contract.writes and end_kind == "COMPLETED" and capture and not capture.patch:
            issues.append("the Worker produced no changes")
        terminal = next((e.data.get("status") for e in reversed(events) if e.kind == "terminal"), None)
        next_action = {"COMPLETED": "review", "CANCELED": "none"}.get(end_kind, "investigate")
        if failure_class in ("RATE_LIMITED", "QUOTA_EXHAUSTED", "AUTH_REQUIRED", "TRANSIENT_ERROR", "MODEL_UNAVAILABLE"):
            next_action = "retry"
        elif failure_class == "PERMISSION_BLOCKED":
            next_action = "approve"
        paths = [v.path for v in cls.violations][:MAX_PATHS_IN_RESULT]
        evidence = [name for name in ("prompt.txt", "stdout.log", "stderr.log", "changes.json", "work.patch")
                    if (attempt_dir / name).exists()] + [f"check_{c.name}.log" for c in checks]
        envelope = R.build_envelope(
            task_id=task["task_id"], attempt=n, provider=route["provider"], worker=route["worker"],
            model_requested=route["model"], model_observed=R.observed_model(events),
            terminal_state=terminal if terminal in ("SUCCESS", "ERROR", "CANCELED") else "NONE",
            outcome={"COMPLETED": "completed", "FAILED": "failed", "INTERRUPTED": "interrupted", "CANCELED": "canceled", "ORPHANED": "orphaned"}[end_kind],
            failure_class=failure_class, stage=stage, summary=R.worker_summary(events), evidence=evidence,
            diff_artifact="work.patch" if capture and capture.patch else None,
            diff_stat=dict(capture.stat) if capture else {"files": 0, "additions": 0, "deletions": 0},
            tests=[c.to_test() for c in checks], usage=R.worker_usage(events),
            boundary={"violations": len(cls.violations), "sensitive": cls.protected_count, "paths": paths},
            remaining_issues=issues, next_action=next_action,
            handoff_usable=bool(capture and not cls.violations and not capture.error and (end_kind == "COMPLETED" or partial)),
            partial=partial, started_at=started_at, ended_at=self.clock.iso())
        return AttemptOutcome(kind=kind, end_kind=end_kind, failure_class=failure_class, stage=stage, reason=reason,
                              envelope=envelope, run=run, retry_after=retry_after, provider_ok=provider_ok, partial=partial,
                              timeout_kind=timeout_kind, reset_at=R.reset_time(events))
