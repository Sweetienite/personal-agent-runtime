"""T11: a candidate is a verified, traceable baseline - accepted is not integrated, and integrated is not applied."""
import json
import os
import unittest
from unittest import mock
from pathlib import Path

from par.core.kernel import NotAllowed, Refused
from par.mcp.tools import Tools
from par.providers.fake import ops, scenarios
from tests.kernel_fixture import FIXED_CALC, KernelWorld, r1_contract
from tests.unit.test_kernel_policy import using
from tests.world import sample_contract

API = "VALUE = 42\n"
BROKEN_API = "VALUE = 0\n"
UNIT = [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q", "tests.test_calc"]}]
SEES_API = [{"name": "sees_api", "argv": ["python3", "checks/see_api.py"]}]


class CandidateWorld(KernelWorld):
    default_files = None

    def setUp(self):
        super().setUp()
        self.commit_in_project(self.project_path, "app/calc.py", FIXED_CALC)   # the base's own test passes before any task runs
        self.commit_in_project(self.project_path, "checks/see_api.py",
                               "import pathlib\np = pathlib.Path('app/api.py')\n"
                               "raise SystemExit(0 if p.is_file() and 'VALUE = 42' in p.read_text() else 1)\n")
        self.commit_in_project(self.project_path, "app/old.py", "OLD = True\n")
        self.commit_in_project(self.project_path, "app/data.txt", "base\n")
        self.commit_in_project(self.project_path, "app/tool.sh", "#!/bin/sh\necho hi\n")

    def contract(self, checks, dependencies=(), **over):
        doc = sample_contract(role="code", allowed_paths=["app/"], dependencies=list(dependencies),
                              acceptance={"criteria": ["the change behaves"], "checks": checks})
        doc.update(over)
        return doc

    def run_accept(self, contract, files=None, *, scenario=None):
        using(self, codex=[scenario or scenarios.success(files or {})])
        tid = self.queued(contract)
        report = self.run_task(tid)
        self.assertEqual(report.status, "REVIEW_REQUIRED", report.reason)
        self.kernel.accept(tid)
        return tid

    def submit_with(self, contract, candidate):
        res = self.kernel.submit(contract, candidate=candidate)
        for a in self.kernel.describe(res.task_id)["pending_approvals"]:
            self.kernel.approve(res.task_id, a["id"])
        self.assertEqual(self.status(res.task_id), "QUEUED")
        return res.task_id

    def api_task(self):
        return self.run_accept(self.contract(UNIT + SEES_API), {"app/api.py": API})


class Chain(CandidateWorld):
    def test_a_candidate_integrates_a_verified_patch_and_b_can_see_it(self):
        a = self.api_task()
        before = self.project_digest()
        cand = self.kernel.candidate_create([a])
        self.assertEqual(cand["status"], "VERIFIED", cand.get("reason"))
        self.assertEqual([(c["task_id"], c["name"], c["status"]) for c in cand["checks"]],
                         [(a, "unit", "PASS"), (a, "sees_api", "PASS")])
        self.assertEqual(len(cand["tasks"]), 1)
        self.assertEqual(cand["tasks"][0]["task_id"], a)
        self.assertEqual(cand["tasks"][0]["patch_sha256"], __import__("hashlib").sha256(
            (self.attempt_dir(a) / "work.patch").read_bytes()).hexdigest())
        self.assertEqual(self.project_digest(), before)                     # the real project is untouched
        self.assertEqual((self.kernel.evidence(a)["integration"], self.kernel.evidence(a)["applied_to_project"]),
                         ("CANDIDATE_READY", False))
        self.assertEqual((Path(cand["repo"]) / "app" / "api.py").read_text(), API)
        self.assertTrue((Path(cand["repo"]) / ".git").is_dir())             # a real candidate repository

        b = self.submit_with(self.contract(UNIT, dependencies=[a]), cand["candidate_id"])
        self.assertEqual(self.kernel.describe(b)["base"]["commit"], cand["commit_sha"])
        using(self, codex=[scenarios.success({"app/b.py": "B = 1\n"})])
        report = self.run_task(b)
        self.assertEqual(report.status, "REVIEW_REQUIRED", report.reason)
        self.assertEqual([t["status"] for t in self.result(b)["tests"]], ["PASS"])
        self.assertIn(b"app/b.py", self.kernel.diff(b))
        self.assertNotIn(b"VALUE = 42", self.kernel.diff(b))                # B's patch does not repeat A's work
        self.assertEqual(self.project_digest(), before)

    def test_an_accepted_dependency_without_integration_blocks_b_and_rebasing_unblocks_it(self):
        a = self.api_task()
        b = self.queued(self.contract(UNIT, dependencies=[a]))
        with self.assertRaises(Refused) as ctx:
            self.run_task(b)
        self.assertEqual(ctx.exception.code, "dependencies")
        self.assertIn("not integrated", " ".join(ctx.exception.problems))
        self.assertEqual(self.status(b), "QUEUED")
        self.assertIn("par candidate create", self.kernel.describe(b)["waiting_for"])

        cand = self.kernel.candidate_create([a])
        self.kernel.task_rebase(b, cand["candidate_id"])
        self.assertEqual(self.kernel.describe(b)["base"]["candidate"], cand["candidate_id"])
        using(self, codex=[scenarios.success({"app/b.py": "B = 1\n"})])
        self.assertEqual(self.run_task(b).status, "REVIEW_REQUIRED")
        self.assertIn("TASK_REBASED", self.event_types(b))

    def test_a_candidate_bound_task_can_still_be_exported_for_a_person(self):
        a = self.api_task()
        cand = self.kernel.candidate_create([a])
        b = self.submit_with(self.contract(UNIT, dependencies=[a]), cand["candidate_id"])
        info = self.kernel.handoff_export(b, to="workbuddy")
        self.assertIn(API, (Path(info["workspace"]) / "app" / "api.py").read_text())
        self.assertEqual(self.status(b), "PAUSED")
        marker = __import__("json").loads((Path(info["workspace"]) / ".par-handoff.json").read_text())
        self.assertEqual(marker["base"], cand["commit_sha"])

    def test_a_candidate_that_does_not_include_the_dependency_is_refused_at_submit(self):
        a = self.api_task()
        other = self.api_task()
        cand = self.kernel.candidate_create([other])
        with self.assertRaises(Refused) as ctx:
            self.kernel.submit(self.contract(UNIT, dependencies=[a]), candidate=cand["candidate_id"])
        self.assertEqual(ctx.exception.code, "candidate_missing")
        self.assertEqual([t["task_id"] for t in self.kernel.list_tasks()], [a, other])   # nothing new was submitted

    def test_an_old_task_without_the_integration_marker_keeps_the_old_dependency_meaning(self):
        a = self.api_task()
        b = self.queued(self.contract(UNIT, dependencies=[a]))
        task = self.kernel.store.get_task(b)
        base = dict(task["base"])
        base.pop("integration", None)
        with self.kernel.store.tx():
            self.kernel.store.update_task(b, "TEST_OLD_TASK", reason="simulate a pre-T11 task",
                                          columns={"base_json": base}, expected_version=task["version"])
        using(self, codex=[scenarios.success({"app/b.py": "B = 1\n"})])
        self.assertEqual(self.run_task(b).status, "REVIEW_REQUIRED")        # legacy: accepted is enough

    def test_read_only_dependencies_need_no_candidate(self):
        a = self.queued(r1_contract())
        using(self, codex=[scenarios.success({})])
        self.assertEqual(self.run_task(a).status, "REVIEW_REQUIRED")
        self.kernel.accept(a)
        b = self.queued(r1_contract(dependencies=[a]))
        using(self, codex=[scenarios.success({})])
        self.assertEqual(self.run_task(b).status, "REVIEW_REQUIRED")


class Failures(CandidateWorld):
    def test_conflicting_patches_fail_the_candidate_and_leave_the_project_alone(self):
        a = self.run_accept(self.contract(UNIT), {"app/data.txt": "A\n"})
        b = self.run_accept(self.contract(UNIT), {"app/data.txt": "B\n"})
        before = self.project_digest()
        cand = self.kernel.candidate_create([a, b])
        self.assertEqual(cand["status"], "FAILED")
        self.assertIn("does not apply", cand["reason"])
        self.assertEqual(len(cand["tasks"]), 1)                            # A was applied; B was not
        self.assertEqual(self.project_digest(), before)
        self.assertEqual(self.kernel.store.get_candidate(cand["candidate_id"])["status"], "FAILED")

    def test_a_patch_that_touches_a_protected_path_is_refused_by_name(self):
        a = self.run_accept(self.contract(UNIT), {"app/api.py": API})
        patch = self.attempt_dir(a) / "work.patch"
        patch.write_bytes(patch.read_bytes() + b"diff --git a/.ai/STATE.md b/.ai/STATE.md\n--- a/.ai/STATE.md\n"
                                                b"+++ b/.ai/STATE.md\n@@ -1 +1 @@\n-x\n+y\n")
        before = self.project_digest()
        cand = self.kernel.candidate_create([a])
        self.assertEqual(cand["status"], "FAILED")
        self.assertIn("protected paths", cand["reason"])
        self.assertEqual(self.kernel.evidence(a)["integration"], "CANDIDATE_FAILED")
        self.assertEqual(self.project_digest(), before)

    def test_a_tampered_patch_inside_the_allowed_paths_is_refused_by_its_hash(self):
        a = self.run_accept(self.contract(UNIT), {"app/data.txt": "changed\n"})
        patch = self.attempt_dir(a) / "work.patch"
        self.assertIn(b"+changed", patch.read_bytes())
        patch.write_bytes(patch.read_bytes().replace(b"+changed", b"+tampered"))
        before = self.project_digest()
        cand = self.kernel.candidate_create([a])
        self.assertEqual(cand["status"], "FAILED")
        self.assertIn("no longer matches the hash recorded when it was accepted", cand["reason"])
        self.assertEqual(self.project_digest(), before)

    def test_a_patch_without_a_recorded_hash_is_refused_as_missing_provenance(self):
        a = self.run_accept(self.contract(UNIT), {"app/api.py": API})
        (self.attempt_dir(a) / "changes.json").unlink()
        with self.assertRaises(Refused) as ctx:
            self.kernel.candidate_create([a])
        self.assertEqual(ctx.exception.code, "missing_provenance")

    def test_a_tampered_patch_that_leaves_the_allowed_paths_is_refused(self):
        a = self.run_accept(self.contract(UNIT), {"app/api.py": API})
        patch = self.attempt_dir(a) / "work.patch"
        patch.write_bytes(patch.read_bytes() + b"diff --git a/docs/spec.md b/docs/spec.md\n--- a/docs/spec.md\n"
                                                b"+++ b/docs/spec.md\n@@ -1 +1 @@\n-# spec\n+changed\n")
        before = self.project_digest()
        cand = self.kernel.candidate_create([a])
        self.assertEqual(cand["status"], "FAILED")
        self.assertIn("outside the task's allowed_paths", cand["reason"])
        self.assertEqual(self.project_digest(), before)

    def test_a_missing_patch_is_refused_by_name(self):
        a = self.run_accept(self.contract(UNIT), {"app/api.py": API})
        (self.attempt_dir(a) / "work.patch").unlink()
        before = self.project_digest()
        with self.assertRaises(Refused) as ctx:
            self.kernel.candidate_create([a])
        self.assertEqual(ctx.exception.code, "missing_patch")
        self.assertIn("no work.patch", " ".join(ctx.exception.problems))
        self.assertEqual(self.kernel.candidate_list(), [])                 # nothing was recorded as integrated
        self.assertEqual(self.project_digest(), before)

    def test_a_check_failure_after_integration_cannot_be_recorded_as_verified(self):
        a = self.api_task()
        first = self.kernel.candidate_create([a])
        b = self.submit_with(self.contract(UNIT, dependencies=[a]), first["candidate_id"])
        using(self, codex=[scenarios.success({"app/api.py": BROKEN_API})])
        self.assertEqual(self.run_task(b).status, "REVIEW_REQUIRED")
        self.kernel.accept(b)
        cand = self.kernel.candidate_create([a, b])
        self.assertEqual(cand["status"], "FAILED")
        self.assertIn("sees_api", cand["reason"])
        self.assertEqual([c["status"] for c in cand["checks"] if c["name"] == "sees_api"], ["FAIL"])
        self.assertEqual(self.kernel.store.get_candidate(cand["candidate_id"])["status"], "FAILED")
        self.assertEqual(self.project_digest(), self.project_digest())

    def test_only_accepted_tasks_and_write_tasks_can_be_integrated(self):
        tid = self.queued(self.contract(UNIT))
        with self.assertRaises(Refused) as ctx:
            self.kernel.candidate_create([tid])
        self.assertEqual(ctx.exception.code, "not_accepted")
        read_only = self.queued(r1_contract())
        using(self, codex=[scenarios.success({})])
        self.run_task(read_only)
        self.kernel.accept(read_only)
        with self.assertRaises(Refused) as ctx:
            self.kernel.candidate_create([read_only])
        self.assertEqual(ctx.exception.code, "no_patch")

    def test_a_dirty_project_or_a_moved_head_is_refused(self):
        a = self.api_task()
        (self.project_path / "dirty.txt").write_text("uncommitted\n")
        with self.assertRaises(Refused) as ctx:
            self.kernel.candidate_create([a])
        self.assertEqual(ctx.exception.code, "dirty_project")
        (self.project_path / "dirty.txt").unlink()
        self.commit_in_project(self.project_path, "later.txt", "moved\n")
        with self.assertRaises(Refused) as ctx:
            self.kernel.candidate_create([a])
        self.assertEqual(ctx.exception.code, "project_moved")

    def test_a_task_cannot_be_rebased_after_it_has_run_or_onto_a_stale_candidate(self):
        a = self.api_task()
        cand = self.kernel.candidate_create([a])
        b = self.queued(self.contract(UNIT, dependencies=[a]))
        b2 = self.queued(self.contract(UNIT))
        using(self, codex=[scenarios.custom("crash", ops.exit(3))])
        self.assertEqual(self.run_task(b2).status, "FAILED")
        self.kernel.retry(b2)
        with self.assertRaises(NotAllowed) as ctx:
            self.kernel.task_rebase(b2, cand["candidate_id"])
        self.assertEqual(ctx.exception.code, "has_attempts")
        self.commit_in_project(self.project_path, "later.txt", "moved\n")
        with self.assertRaises(Refused) as ctx:
            self.kernel.task_rebase(b, cand["candidate_id"])
        self.assertEqual(ctx.exception.code, "stale_candidate")


class Records(CandidateWorld):
    def test_repeating_an_integration_makes_a_new_record_and_keeps_the_old_one(self):
        a = self.api_task()
        first = self.kernel.candidate_create([a])
        second = self.kernel.candidate_create([a])
        self.assertNotEqual(first["candidate_id"], second["candidate_id"])
        self.assertEqual((first["status"], second["status"]), ("VERIFIED", "VERIFIED"))
        rows = self.kernel.candidate_list()
        self.assertEqual([r["candidate_id"] for r in rows], [first["candidate_id"], second["candidate_id"]])
        self.assertEqual(rows[0]["checks"], first["checks"])                # the old record was not rewritten

    def test_deletions_and_mode_changes_survive_the_patch(self):
        a = self.run_accept(self.contract(UNIT), scenario=scenarios.custom(
            "cleanup", ops.message("working"), ops.delete("app/old.py"), ops.chmod("app/tool.sh", 0o755), *ops.success_tail()))
        cand = self.kernel.candidate_create([a])
        self.assertEqual(cand["status"], "VERIFIED", cand.get("reason"))
        repo = Path(cand["repo"])
        self.assertFalse((repo / "app" / "old.py").exists())
        self.assertTrue((repo / "app" / "tool.sh").stat().st_mode & 0o100)      # still executable, now private
        self.assertEqual(self.kernel.diff(a).count(b"deleted file mode"), 1)
        self.assertIn(b"new mode 100755", self.kernel.diff(a))

    def test_an_interrupted_creation_leaves_no_record_and_does_not_damage_an_older_one(self):
        a = self.api_task()
        first = self.kernel.candidate_create([a])
        before = self.kernel.store.get_candidate(first["candidate_id"])
        with mock.patch.object(self.kernel, "_candidate_checks", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.kernel.candidate_create([a])
        self.assertEqual([r["candidate_id"] for r in self.kernel.candidate_list()], [first["candidate_id"]])
        self.assertEqual(self.kernel.store.get_candidate(first["candidate_id"]), before)
        self.assertEqual(self.kernel.candidate_create([a])["status"], "VERIFIED")

    def test_candidate_show_and_list_are_read_only(self):
        a = self.api_task()
        cand = self.kernel.candidate_create([a])
        before = self.kernel.store.get_candidate(cand["candidate_id"])
        shown = self.kernel.candidate_show(cand["candidate_id"])
        self.assertEqual(shown, {**before, "repo": str(Path(before["artifact_dir"]) / "repo"),
                                 "applied_to_real_project": False})
        self.assertEqual(self.kernel.candidate_list(), [shown])
        self.assertEqual(self.kernel.store.get_candidate(cand["candidate_id"]), before)

    def test_nothing_is_applied_to_the_project_even_after_accept_and_close(self):
        a = self.api_task()
        cand = self.kernel.candidate_create([a])
        self.assertFalse(cand["applied_to_real_project"])
        self.kernel.close_task(a)
        self.assertEqual(self.kernel.candidate_show(cand["candidate_id"])["applied_to_real_project"], False)
        self.assertFalse((self.project_path / "app" / "api.py").exists())


class McpCandidateWorkflow(CandidateWorld):
    def payload(self, out):
        self.assertFalse(out["isError"], out["content"][0]["text"])
        return json.loads(out["content"][0]["text"])

    def test_the_bridge_can_create_show_list_and_rebase_on_a_verified_candidate(self):
        a = self.api_task()
        tools = Tools(self.kernel)
        cand = self.payload(tools.call("create_candidate", {"tasks": [a], "note": "audit window"}))
        self.assertEqual((cand["status"], cand["applied_to_project"]), ("VERIFIED", False))
        self.assertEqual([(t["task_id"], t["attempt"]) for t in cand["tasks"]], [(a, 1)])
        self.assertEqual([(c["task_id"], c["name"], c["status"]) for c in cand["checks"]],
                         [(a, "unit", "PASS"), (a, "sees_api", "PASS")])
        listed = self.payload(tools.call("list_candidates", {}))
        self.assertEqual([(c["candidate"], c["status"], c["tasks"]) for c in listed["candidates"]],
                         [(cand["candidate"], "VERIFIED", 1)])
        shown = self.payload(tools.call("show_candidate", {"candidate": cand["candidate"]}))
        self.assertEqual(shown["commit_sha"], cand["commit_sha"])
        b = self.queued(self.contract(UNIT))                                      # queued, no attempt yet
        out = self.payload(tools.call("rebase_task", {"task_id": b, "candidate": cand["candidate"], "note": "use A"}))
        self.assertEqual(out["status"], "QUEUED")
        self.assertEqual(self.kernel.describe(b)["base"]["commit"], cand["commit_sha"])


if __name__ == "__main__":
    unittest.main()
