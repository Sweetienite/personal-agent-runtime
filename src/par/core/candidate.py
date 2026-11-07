"""Candidate integration: put accepted patches on one traceable baseline, verify, and keep the real project untouched.

This is deliberately a *record*, not a new task status: a task can be ACCEPTED and still not integrated, and a candidate
can be VERIFIED without anything being applied to the person's repository. A dependent task may only use a candidate
that includes every dependency that changed files, so "A is accepted" can no longer be mistaken for "B can see A's code".

Nothing here writes to the project: the candidate lives in a Kernel-owned clone under ``<data>/candidates/<id>`` and is
built only from patches the Kernel cut and re-checked itself. The person still decides whether to apply the result.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from par.core.attempt import private_dirs, remove_scratch
from par.core.contract import forbidden_reason, path_allowed
from par.core.redact import redact_text
from par.core.states import TaskStatus as T
from par.execution import gitops, validation

CANDIDATE_ID = re.compile(r"cand-[a-z][a-z0-9_]*-[0-9a-f]{8}\Z")
CANDIDATE_DATE = "2000-01-01T00:00:00 +0000"      # deterministic candidate commits: same base + patches = same ids
MAX_PATCH_BYTES = 8 * 1024 * 1024
INTEGRATION_MODE = "candidate-v1"


class CandidateMixin:
    """Methods of :class:`par.core.kernel.Kernel` (kept apart: the Kernel is long enough)."""

    def candidate_create(self, task_ids: "Iterable[str]", *, note: str = "") -> "dict[str, Any]":
        """Apply accepted tasks' own patches to one clean baseline, run their checks, and record the outcome."""
        from par.core.kernel import Refused
        ids = list(task_ids)
        if not ids or any(not isinstance(t, str) for t in ids):
            raise Refused("no_tasks", "name at least one accepted task to integrate (repeat --task)")
        if len(set(ids)) != len(ids):
            raise Refused("duplicate_task", "the same task was named twice")
        tasks = [self._require(t) for t in ids]
        project_id = tasks[0]["project_id"]
        if any(t["project_id"] != project_id for t in tasks):
            raise Refused("other_project", "candidate integration is per project; these tasks belong to different projects")
        for task in tasks:
            self._candidate_task_problems(task)
        ordered = self._candidate_order(tasks)
        base_commit = ordered[0]["base"].get("commit")
        if not base_commit or not gitops.COMMIT.match(str(base_commit)):
            raise Refused("base_mismatch", "the first task has no full git base commit; a candidate must start from one")

        project = self.projects.get(project_id)
        if project is None:
            raise Refused("unknown_project", f"project {project_id!r} is no longer registered")
        manifest = self._context(project).manifest()
        dirty = sum((manifest.base.get("dirty") or {}).values())
        if dirty:
            raise Refused("dirty_project", f"the project has {dirty} uncommitted change(s); commit or stash them before integrating")
        if manifest.base_git_commit != base_commit:
            raise Refused("project_moved", f"the project is at {manifest.base_git_commit}, but these tasks were submitted at "
                                           f"{base_commit}; rebase the tasks or integrate from a clean state at their base")
        candidate_id = self._candidate_id(project_id)
        artifact_dir = self.data_dir / "candidates" / candidate_id
        private_dirs(artifact_dir, self.data_dir)
        repo = artifact_dir / "repo"
        applied: "list[dict[str, Any]]" = []
        checks: "list[dict[str, Any]]" = []
        reason = ""
        try:
            gitops.clone_at(self.git, project.path, repo, base_commit)
            current = base_commit
            for task in ordered:
                if task["base"].get("commit") not in (base_commit, current):
                    raise gitops.GitError(f"{task['task_id']} was submitted on {str(task['base'].get('commit'))[:12]}, "
                                          f"which is neither {base_commit[:12]} nor the previous task's commit; integrate its base first")
                contract = self._contract_of(task)
                n = self._latest_completed_attempt(task)
                patch_path = self.runner.attempt_dir(task["task_id"], n) / "work.patch"
                raw = self._candidate_patch(task, patch_path)
                sha = hashlib.sha256(raw).hexdigest()
                expected = self._candidate_changes(task["task_id"], n).get("patch_sha256")
                paths = gitops.patch_paths(self.git, raw)
                protected = sorted(p for p in paths if forbidden_reason(p))
                if protected:
                    raise gitops.GitError("the patch touches protected paths: " + ", ".join(protected[:5]))
                outside = sorted(p for p in paths if not path_allowed(p, contract.allowed_paths))
                if outside:
                    raise gitops.GitError("the patch changes paths outside the task's allowed_paths: " + ", ".join(outside[:5]))
                if expected != sha:
                    raise gitops.GitError(f"the work.patch of {task['task_id']} no longer matches the hash recorded when it was accepted")
                gitops.apply_patch(self.git, repo, raw, check_only=True, ceiling=repo)
                gitops.apply_patch(self.git, repo, raw)
                commit = gitops.commit_all(self.git, repo, f"candidate: {task['task_id']} attempt {n} ({sha[:12]})",
                                           date=CANDIDATE_DATE)
                applied.append({"task_id": task["task_id"], "attempt": n, "patch_sha256": sha, "commit": commit,
                                "base": task["base"].get("commit"), "risk": contract.risk,
                                "allowed_paths": list(contract.allowed_paths), "paths": sorted(paths)})
                current = commit
            self._privatize_tree(repo)
            checks = self._candidate_checks(artifact_dir, repo, ordered)
        except (gitops.GitError, OSError) as exc:
            reason = redact_text(str(exc))[:300]
            self._privatize_tree(repo)
            commit_sha = self._candidate_head(repo)
            return self._record_candidate(candidate_id, project_id, base_commit, commit_sha, "FAILED", applied, checks,
                                          artifact_dir, [t["task_id"] for t in ordered],
                                          reason or "the candidate could not be built")
        bad = [c for c in checks if c["status"] != "PASS"]
        status = "FAILED" if bad else "VERIFIED"
        reason = ("check(s) did not pass: " + ", ".join(sorted({c['name'] for c in bad}))[:300]) if bad else ""
        return self._record_candidate(candidate_id, project_id, base_commit, self._candidate_head(repo), status, applied,
                                      checks, artifact_dir, [t["task_id"] for t in ordered], reason)

    def candidate_show(self, candidate_id: str) -> "dict[str, Any]":
        from par.core.kernel import Refused
        if not CANDIDATE_ID.match(candidate_id or ""):
            raise Refused("bad_candidate", "a candidate id looks like cand-<project>-<8 hex characters>")
        row = self.store.get_candidate(candidate_id)
        if row is None:
            raise Refused("unknown_candidate", f"no candidate {candidate_id!r}")
        return {**row, "repo": str(Path(row["artifact_dir"]) / "repo"),
                "applied_to_real_project": False}

    def candidate_list(self, project_id: "str | None" = None) -> "list[dict[str, Any]]":
        from par.core.kernel import Refused
        if project_id is not None and project_id not in self.projects:
            raise Refused("unknown_project", f"project {project_id!r} is not registered")
        return [{**row, "repo": str(Path(row["artifact_dir"]) / "repo"), "applied_to_real_project": False}
                for row in self.store.list_candidates(project_id)]

    # ---- helpers -----------------------------------------------------------------------------------------
    def _candidate_task_problems(self, task: "Mapping[str, Any]") -> None:
        from par.core.kernel import Refused
        tid = task["task_id"]
        if T(task["status"]) not in (T.ACCEPTED, T.CLOSED):
            raise Refused("not_accepted", f"{tid} is {task['status']}; only an accepted (or closed) result can be integrated")
        contract = self._contract_of(task)
        if contract.risk != "R2":
            raise Refused("no_patch", f"{tid} is {contract.risk}: it changed no files, so there is nothing to integrate")
        if not contract.checks:
            raise Refused("no_checks", f"{tid} has no acceptance.checks; its patch cannot be integrated as verified")
        n = self._latest_completed_attempt(task)
        result = self.store.get_attempt(tid, n)["result"]
        boundary = result.get("boundary") if isinstance(result, Mapping) else None
        if not isinstance(boundary, Mapping) or boundary.get("violations"):
            raise Refused("bad_result", f"{tid} attempt {n} has no clean boundary record; refusing to integrate it")
        self._candidate_patch(task, self.runner.attempt_dir(tid, n) / "work.patch")   # exists, size, regular file
        if not isinstance(self._candidate_changes(tid, n).get("patch_sha256"), str):
            raise Refused("missing_provenance", f"{tid} attempt {n} has no recorded patch hash; refusing to integrate it")

    def _latest_completed_attempt(self, task: "Mapping[str, Any]") -> int:
        from par.core.kernel import Refused
        attempts = self.store.attempts(task["task_id"])
        if not attempts:
            raise Refused("no_attempt", f"{task['task_id']} has no finished attempt")
        latest = attempts[-1]
        if latest["end_kind"] != "COMPLETED" or not isinstance(latest.get("result"), Mapping):
            raise Refused("not_completed", f"{task['task_id']}'s latest attempt ended as {latest['end_kind'] or 'unfinished'}, not COMPLETED")
        return int(latest["n"])

    def _candidate_changes(self, task_id: str, n: int) -> "dict[str, Any]":
        try:
            doc = json.loads((self.runner.attempt_dir(task_id, n) / "changes.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    @staticmethod
    def _candidate_patch(task: "Mapping[str, Any]", path: Path) -> bytes:
        from par.core.kernel import Refused
        try:
            if path.is_symlink() or not path.is_file():
                raise Refused("missing_patch", f"{task['task_id']} has no work.patch; keep the attempt's evidence before integrating")
            if path.stat().st_size > MAX_PATCH_BYTES:
                raise Refused("patch_too_large", f"{task['task_id']}'s patch is larger than {MAX_PATCH_BYTES // (1024 * 1024)} MiB")
            return path.read_bytes()
        except OSError:
            raise Refused("missing_patch", f"{task['task_id']}'s work.patch cannot be read") from None

    @staticmethod
    def _candidate_order(tasks: "list[Mapping[str, Any]]") -> "list[Mapping[str, Any]]":
        from par.core.kernel import Refused
        by_id = {t["task_id"]: t for t in tasks}
        ordered: "list[Mapping[str, Any]]" = []
        done: "set[str]" = set()
        remaining = list(tasks)
        while remaining:
            ready = [t for t in remaining if all(d not in by_id or d in done for d in t["contract"].get("dependencies", []))]
            if not ready:
                raise Refused("dependency_cycle", "the selected tasks depend on each other in a cycle; order them explicitly")
            for task in ready:
                ordered.append(task)
                done.add(task["task_id"])
                remaining.remove(task)
        return ordered

    @staticmethod
    def _privatize_tree(root: Path) -> None:
        """Kernel-owned copies are private to the user, like every other artifact under the data directory."""
        if root.is_symlink() or not root.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames:
                p = Path(dirpath) / name
                if not p.is_symlink():
                    os.chmod(p, 0o700)
            for name in filenames:
                p = Path(dirpath) / name
                if p.is_symlink():
                    continue
                try:
                    executable = bool(p.stat().st_mode & 0o100)
                    os.chmod(p, 0o700 if executable else 0o600)
                except OSError:
                    pass
        os.chmod(root, 0o700)

    def _candidate_id(self, project_id: str) -> str:
        for _ in range(20):
            candidate_id = f"cand-{project_id}-{secrets.token_hex(4)}"
            if self.store.get_candidate(candidate_id) is None and not (self.data_dir / "candidates" / candidate_id).exists():
                return candidate_id
        raise RuntimeError("could not allocate a candidate id")

    def _candidate_checks(self, artifact_dir: Path, repo: Path, tasks: "list[Mapping[str, Any]]") -> "list[dict[str, Any]]":
        verify = artifact_dir / "verify"
        home, tmp = artifact_dir / "home", artifact_dir / "tmp"
        for d in (verify, home, tmp):
            d.mkdir(mode=0o700)
        shutil.copytree(repo, verify, symlinks=True, dirs_exist_ok=True,
                        ignore=lambda directory, names: {".git"} if Path(directory) == repo else set())
        os.chmod(verify, 0o700)
        out: "list[dict[str, Any]]" = []
        try:
            for task in tasks:
                contract = self._contract_of(task)
                tid = task["task_id"]
                check_dir = artifact_dir / "checks" / tid
                private_dirs(check_dir, artifact_dir)
                for check in validation.run_checks(contract.checks, cwd=verify, attempt_dir=check_dir, home=home, tmp=tmp,
                                                   grace=self.settings.check_grace):
                    out.append({"task_id": tid, "name": check.name, "status": check.status, "exit_code": check.exit_code,
                                "duration_s": round(check.duration_s, 3),
                                "log": f"checks/{tid}/{check.log}" if check.log else None})
        finally:
            remove_scratch(verify, artifact_dir)              # the checks' logs and the candidate repo are the evidence
        return out

    def _candidate_head(self, repo: Path) -> "Optional[str]":
        try:
            return gitops.resolve_commit(self.git, repo) if repo.is_dir() else None
        except (gitops.GitError, OSError):
            return None

    def _record_candidate(self, candidate_id: str, project_id: str, base_commit: str, commit_sha: "Optional[str]",
                          status: str, tasks: "list[dict[str, Any]]", checks: "list[dict[str, Any]]",
                          artifact_dir: Path, requested: "list[str]", reason: str) -> "dict[str, Any]":
        with self.store.tx():
            self.store.insert_candidate(candidate_id=candidate_id, project_id=project_id, base_commit=base_commit,
                                        commit_sha=commit_sha, status=status, tasks=tasks, checks=checks,
                                        artifact_dir=str(artifact_dir), requested=requested, reason=reason)
            self.store.add_event("CANDIDATE_" + status, task_id=None, attempt_n=None,
                                 reason=reason or f"{len(tasks)} task(s) integrated on {base_commit[:12]}",
                                 data={"candidate_id": candidate_id, "project": project_id, "base_commit": base_commit,
                                       "commit": commit_sha, "tasks": requested,
                                       "applied_to_real_project": False})
        return self.candidate_show(candidate_id)

    # ---- dependency semantics and rebasing ---------------------------------------------------------------
    def _candidate_dependency_problem(self, task: "Mapping[str, Any]") -> "str | None":
        """A dependency that changed files is satisfied only by a verified candidate that includes it."""
        base = task.get("base") or {}
        if base.get("integration") != INTEGRATION_MODE:
            return None                                            # tasks submitted before this feature keep their meaning
        deps = list(task["contract"].get("dependencies", []))
        changing = []
        for dep in deps:
            row = self.store.get_task(dep)
            if row is None or row["status"] not in (T.ACCEPTED.value, T.CLOSED.value):
                continue                                           # the ordinary "waiting for" check already reported it
            if row["risk_tier"] == "R2" and row["attempt_count"]:
                changing.append(dep)
        if not changing:
            return None
        candidate = self.store.get_candidate(base.get("candidate")) if base.get("candidate") else None
        if candidate is None:
            return (f"{', '.join(changing)} changed files but is not integrated; create a candidate "
                    f"(par candidate create --task {changing[0]}) and rebase this task (par task rebase {task['task_id']} --candidate CANDIDATE)")
        if candidate["status"] != "VERIFIED":
            return f"candidate {candidate['candidate_id']} is {candidate['status']}, not VERIFIED"
        included = {t["task_id"] for t in candidate["tasks"]}
        missing = [d for d in changing if d not in included]
        if missing:
            return f"candidate {candidate['candidate_id']} does not include {', '.join(missing)}; integrate them together"
        if base.get("commit") != candidate["commit_sha"]:
            return f"this task is not based on candidate {candidate['candidate_id']}'s commit; rebase it"
        return None

    def task_rebase(self, task_id: str, candidate_id: str, *, note: str = "") -> "dict[str, Any]":
        """Point a queued task at a verified candidate before it has ever run. Never touches an attempt's evidence."""
        from par.core.kernel import NotAllowed, Refused
        task = self._require(task_id)
        if T(task["status"]) not in (T.QUEUED, T.PAUSED):
            raise NotAllowed("not_rebasable", f"{task_id} is {task['status']}; only a queued or paused task can be rebased")
        if task["attempt_count"]:
            raise NotAllowed("has_attempts", f"{task_id} already has {task['attempt_count']} attempt(s); rebasing would invalidate its evidence")
        if self.handoff_exports(task_id):
            raise NotAllowed("has_handoff", f"{task_id} already has a hand-off packet; finish or discard it before rebasing")
        candidate = self.candidate_show(candidate_id)
        if candidate["status"] != "VERIFIED":
            raise Refused("not_verified", f"candidate {candidate_id} is {candidate['status']}, not VERIFIED")
        if candidate["project_id"] != task["project_id"]:
            raise Refused("other_project", f"candidate {candidate_id} belongs to {candidate['project_id']}, not {task['project_id']}")
        project = self.projects.get(task["project_id"])
        if project is None:
            raise Refused("unknown_project", f"project {task['project_id']!r} is no longer registered")
        manifest = self._context(project).manifest()
        if sum((manifest.base.get("dirty") or {}).values()):
            raise Refused("dirty_project", "the project has uncommitted changes; commit or stash them before rebasing a task")
        if manifest.base_git_commit != candidate["base_commit"]:
            raise Refused("stale_candidate", f"the project is at {manifest.base_git_commit}, but candidate {candidate_id} was built on "
                                             f"{candidate['base_commit']}; create a new candidate from the current state")
        base = self._base_record(manifest, task["risk_tier"])
        base.update({"integration": INTEGRATION_MODE, "candidate": candidate_id, "commit": candidate["commit_sha"],
                     "candidate_commit": candidate["commit_sha"], "candidate_base": candidate["base_commit"],
                     "candidate_repo": f"candidates/{candidate_id}/repo"})
        with self.store.tx():
            self.store.update_task(task_id, "TASK_REBASED", reason=note or f"rebased on candidate {candidate_id}",
                                   columns={"base_json": base}, expected_version=task["version"],
                                   data={"candidate": candidate_id, "from_commit": task["base"].get("commit"),
                                         "to_commit": candidate["commit_sha"], "base_commit": candidate["base_commit"]})
        return self.describe(task_id)

    def candidate_base_for(self, candidate_id: str, project_id: str, dependencies: "Iterable[str]") -> "dict[str, Any]":
        """The base override for a task submitted with ``--candidate``. Raises Refused when it cannot be used."""
        from par.core.kernel import Refused
        candidate = self.candidate_show(candidate_id)
        if candidate["status"] != "VERIFIED":
            raise Refused("not_verified", f"candidate {candidate_id} is {candidate['status']}, not VERIFIED")
        if candidate["project_id"] != project_id:
            raise Refused("other_project", f"candidate {candidate_id} belongs to {candidate['project_id']}, not {project_id}")
        included = {t["task_id"] for t in candidate["tasks"]}
        required = []
        for dep in dependencies:
            row = self.store.get_task(dep)
            if row is not None and row["risk_tier"] == "R2" and row["attempt_count"]:
                required.append(dep)          # a read-only dependency changed no files: it needs no candidate
        missing = [d for d in required if d not in included]
        if missing:
            raise Refused("candidate_missing", f"candidate {candidate_id} does not include {', '.join(missing)}; integrate them together")
        return {"integration": INTEGRATION_MODE, "candidate": candidate_id, "commit": candidate["commit_sha"],
                "candidate_commit": candidate["commit_sha"], "candidate_base": candidate["base_commit"],
                "candidate_repo": f"candidates/{candidate_id}/repo"}
