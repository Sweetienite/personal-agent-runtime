import json
import unittest
from pathlib import Path

from par.config import ProjectEntry
from par.core.kernel import Kernel, NotAllowed, Refused, UnknownTask
from tests.kernel_fixture import KernelWorld, r1_contract
from tests.helpers import tree_digest
from tests.world import GOOD_STATE, sample_contract


class Submitting(KernelWorld):
    def test_a_read_only_task_is_queued_and_records_what_it_was_based_on(self):
        res = self.submit(r1_contract())
        self.assertEqual((res.task_id, res.status, res.created, res.deduplicated), ("fixture-0001", "QUEUED", True, False))
        d = self.kernel.describe(res.task_id)
        self.assertEqual(self.statuses(res.task_id), ["READY", "QUEUED"])
        self.assertEqual(d["base"]["commit"], self.git(self.project_path, "rev-parse", "HEAD"))
        self.assertEqual(d["base"]["vcs"], "git")
        self.assertEqual(d["base"]["parent_contract_ref"]["path"], ".ai/tasks/active/TASK-1.md")
        self.assertEqual((d["risk"], d["submitted_by"], d["attempt_count"], d["version"]), ("R1", "user", 0, 3))
        self.assertEqual(d["next_actions"], ["run", "pause", "cancel"])
        self.assertEqual(self.event_types(res.task_id)[0], "TASK_SUBMITTED")

    def test_the_task_is_locked_to_the_configuration_it_was_submitted_under(self):
        res = self.submit(r1_contract())
        task = self.kernel.store.get_task(res.task_id)
        self.assertEqual(task["profile"]["digest"], self.kernel.describe(res.task_id)["config_digest"])
        self.assertEqual(task["profile"]["roles"]["code"]["primary"], "w_code")

    def test_a_file_changing_task_waits_for_write_permission(self):
        res = self.submit()
        self.assertEqual((res.status, len(res.pending_approvals)), ("PERMISSION_WAIT", 1))
        d = self.kernel.describe(res.task_id)
        self.assertEqual([a["kind"] for a in d["pending_approvals"]], ["WRITE_PERMISSION"])
        self.assertEqual(d["pending_approvals"][0]["payload"]["allowed_paths"], ["app/"])
        self.assertEqual(d["next_actions"], ["cancel", "approve", "reject"])
        self.kernel.approve(res.task_id)
        self.assertEqual(self.status(res.task_id), "QUEUED")

    def test_a_coordinators_worker_choice_needs_a_person_but_a_persons_does_not(self):
        by_user = self.submit(r1_contract(), worker="w_flash")
        self.assertEqual(by_user.status, "QUEUED")
        by_ai = self.submit(r1_contract(), worker="w_flash", by="coordinator")
        self.assertEqual(by_ai.status, "PERMISSION_WAIT")
        self.assertEqual([a["kind"] for a in self.kernel.describe(by_ai.task_id)["pending_approvals"]], ["WORKER_OVERRIDE"])
        self.assertEqual(self.submit(r1_contract(), by="coordinator").status, "QUEUED")      # no override, nothing to approve

    def test_the_same_key_and_contract_returns_the_original_task(self):
        first = self.submit(r1_contract(), key="same")
        again = self.submit(r1_contract(), key="same")
        self.assertEqual((again.task_id, again.created, again.deduplicated), (first.task_id, False, True))
        self.assertEqual(len(self.kernel.list_tasks()), 1)
        self.assertEqual(self.event_types(first.task_id).count("SUBMIT_DEDUPLICATED"), 1)

    def test_the_same_key_with_a_different_contract_is_refused(self):
        self.submit(r1_contract(), key="same")
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract(description="Something else entirely."), key="same")
        self.assertEqual(ctx.exception.code, "idempotency_conflict")
        self.assertEqual(len(self.kernel.list_tasks()), 1)

    def test_the_same_key_in_two_projects_is_two_tasks(self):
        other = self.make_project("other")
        self.projects["other"] = ProjectEntry("other", other, "git", True)
        self.kernel = self.new_kernel()
        a = self.submit(r1_contract(), key="k")
        b = self.submit(r1_contract(project_id="other"), key="k")
        self.assertEqual((a.task_id, b.task_id), ("fixture-0001", "other-0001"))

    def test_an_invalid_contract_creates_nothing_and_burns_no_task_number(self):
        with self.assertRaises(Refused) as ctx:
            self.kernel.submit({"schema_version": 1, "project_id": "fixture"})
        self.assertEqual(ctx.exception.code, "invalid_contract")
        self.assertGreater(len(ctx.exception.problems), 3)
        self.assertEqual(self.kernel.list_tasks(), [])
        self.assertEqual(self.submit(r1_contract()).task_id, "fixture-0001")

    def test_r3_work_is_refused(self):
        with self.assertRaises(Refused) as ctx:
            self.submit(sample_contract(risk="R3"))
        self.assertTrue(any("R3" in p for p in ctx.exception.problems))

    def test_an_unknown_project_is_refused(self):
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract(project_id="nowhere"))
        self.assertEqual(ctx.exception.code, "unknown_project")

    def test_a_secret_in_a_contract_is_refused_and_never_reaches_the_ledger(self):
        secret = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"
        with self.assertRaises(Refused):
            self.submit(r1_contract(description=f"use {secret} to log in"))
        self.kernel.store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for f in self.data.glob("runtime.sqlite3*"):
            self.assertNotIn(secret.encode(), f.read_bytes())

    def test_dependencies_must_exist_in_the_same_project(self):
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract(dependencies=["fixture-0042"]))
        self.assertTrue(any("does not exist" in p for p in ctx.exception.problems))
        other = self.make_project("other")
        self.projects["other"] = ProjectEntry("other", other, "git", True)
        self.kernel = self.new_kernel()
        o = self.submit(r1_contract(project_id="other"))
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract(dependencies=[o.task_id]))
        self.assertTrue(any("another project" in p for p in ctx.exception.problems))

    def test_submitting_never_touches_the_project(self):
        before = self.project_digest()
        self.submit(r1_contract())
        self.submit()
        self.assertEqual(self.project_digest(), before)


class RealProjectSafety(KernelWorld):
    def test_a_registry_project_cannot_take_write_tasks_until_the_real_gates_exist(self):
        self.projects["fixture"] = ProjectEntry("fixture", self.project_path, "git", False)     # what projects.toml can express
        self.kernel = self.new_kernel()
        with self.assertRaises(Refused) as ctx:
            self.submit()
        self.assertTrue(any("WRITE_DISABLED" in p for p in ctx.exception.problems))
        self.assertEqual(self.submit(r1_contract()).status, "QUEUED")                             # reading is fine

    def test_a_dirty_project_refuses_write_tasks(self):
        (self.project_path / "app" / "calc.py").write_text("# local edit\n")
        with self.assertRaises(Refused) as ctx:
            self.submit()
        self.assertTrue(any("DIRTY_BASE" in p for p in ctx.exception.problems))
        self.assertEqual(self.submit(r1_contract()).status, "QUEUED")

    def test_an_oversized_state_refuses_write_tasks(self):
        (self.project_path / ".ai" / "STATE.md").write_text(GOOD_STATE + "\n" + "- filler line\n" * 2000)
        self.git(self.project_path, "add", "-A")
        self.git(self.project_path, "commit", "-q", "-m", "big state")
        with self.assertRaises(Refused) as ctx:
            self.submit()
        self.assertTrue(any("STATE_OVERSIZED" in p for p in ctx.exception.problems))

    def test_a_visible_secret_file_refuses_write_tasks(self):
        (self.project_path / ".env").write_text("KEY=1\n")
        with self.assertRaises(Refused) as ctx:
            self.submit()
        self.assertTrue(any("SENSITIVE_VISIBLE" in p or "DIRTY_BASE" in p for p in ctx.exception.problems))


class ParentContracts(KernelWorld):
    def test_a_project_without_an_active_contract_only_takes_ad_hoc_read_only_work(self):
        bare = self.make_project("bare", contract=False)
        self.projects["bare"] = ProjectEntry("bare", bare, "git", True)
        self.kernel = self.new_kernel()
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract(project_id="bare"))
        self.assertTrue(any("no active parent contract" in p for p in ctx.exception.problems))
        self.assertEqual(self.submit(r1_contract(project_id="bare"), ad_hoc=True).status, "QUEUED")
        with self.assertRaises(Refused):                                   # ad-hoc cannot change code (contract rule)
            self.submit(sample_contract(project_id="bare", ad_hoc=True))

    def test_ad_hoc_is_refused_where_a_contract_exists(self):
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract(), ad_hoc=True)
        self.assertTrue(any("active contract" in p for p in ctx.exception.problems))

    def test_several_active_contracts_stop_everything(self):
        (self.project_path / ".ai" / "tasks" / "active" / "TASK-2.md").write_text("# second\n")
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract())
        self.assertTrue(any("several active contracts" in p for p in ctx.exception.problems))


class ProjectKinds(KernelWorld):
    def test_projects_without_git_take_text_only_tasks(self):
        plain = self.make_project("plain", git=False)
        self.projects["plain"] = ProjectEntry("plain", plain, "none", False)
        self.kernel = self.new_kernel()
        r0 = r1_contract(project_id="plain", risk="R0")
        self.assertEqual(self.submit(r0).status, "QUEUED")
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract(project_id="plain"))
        self.assertTrue(any("git base commit" in p for p in ctx.exception.problems))

    def test_a_missing_project_folder_is_refused(self):
        self.projects["ghost"] = ProjectEntry("ghost", self.base / "gone", "git", False)
        self.kernel = self.new_kernel()
        with self.assertRaises(Refused) as ctx:
            self.submit(r1_contract(project_id="ghost"))
        self.assertTrue(any("does not exist" in p for p in ctx.exception.problems))


class Reading(KernelWorld):
    def test_unknown_tasks_are_reported_as_such(self):
        for call in (lambda: self.kernel.describe("fixture-0042"), lambda: self.kernel.events("fixture-0042"),
                     lambda: self.kernel.result("fixture-0042"), lambda: self.kernel.cancel("fixture-0042")):
            with self.assertRaises(UnknownTask):
                call()

    def test_listing_and_filtering(self):
        a = self.submit(r1_contract()).task_id
        b = self.submit().task_id
        self.assertEqual([t["task_id"] for t in self.kernel.list_tasks()], [a, b])
        self.assertEqual([t["task_id"] for t in self.kernel.list_tasks(status="PERMISSION_WAIT")], [b])
        self.assertEqual(self.kernel.list_tasks(project_id="nope"), [])

    def test_a_task_with_no_attempts_has_no_result_or_diff(self):
        a = self.submit(r1_contract()).task_id
        self.assertIsNone(self.kernel.result(a))
        self.assertIsNone(self.kernel.diff(a))


if __name__ == "__main__":
    unittest.main()
