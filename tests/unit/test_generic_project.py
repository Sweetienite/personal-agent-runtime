import unittest
from dataclasses import replace

from par.config import ProjectEntry
from par.core.kernel import Refused
from par.providers.fake import ScenarioSequence, scenarios
from tests.kernel_fixture import BUGGY_CALC, TEST_CALC, KernelWorld, r1_contract
from tests.world import sample_contract


class GenericProject(KernelWorld):
    default_files = None

    def setUp(self):
        super().setUp()
        self.plain = self.base / "projects" / "plain"
        self.plain.mkdir(parents=True)
        (self.plain / "README.md").write_text("# plain\n", encoding="utf-8")
        (self.plain / "app").mkdir()
        (self.plain / "app" / "__init__.py").write_text("", encoding="utf-8")
        (self.plain / "app" / "calc.py").write_text(BUGGY_CALC, encoding="utf-8")
        (self.plain / "tests").mkdir()
        (self.plain / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (self.plain / "tests" / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
        self.git(self.plain, "init", "-q", "-b", "main")
        self.git(self.plain, "add", "-A")
        self.git(self.plain, "commit", "-q", "-m", "init")
        self.entry = ProjectEntry("plain", self.plain, "git", False, False, "", "generic")
        self.projects["plain"] = self.entry
        self.kernel = self.new_kernel()

    def enable(self):
        self.projects["plain"] = replace(self.entry, write_enabled=True, write_approved="2026-11-06")
        self.kernel = self.new_kernel()

    def contract(self, **over):
        doc = sample_contract(project_id="plain", ad_hoc=over.pop("ad_hoc", True), **over)
        return doc

    def test_a_plain_repository_takes_read_only_work_without_state_or_a_parent_contract(self):
        self.kernel.validate(r1_contract(project_id="plain", ad_hoc=True))
        self.assertEqual(self.kernel.describe(self.submit(r1_contract(project_id="plain", ad_hoc=True)).task_id)["status"],
                         "QUEUED")

    def test_a_write_task_is_refused_until_the_person_enables_writes(self):
        with self.assertRaises(Refused) as caught:
            self.submit(self.contract())
        self.assertTrue(any("WRITE_DISABLED" in p for p in caught.exception.problems))
        self.assertEqual(self.kernel.store.list_tasks(), [])

    def test_generic_ad_hoc_r2_runs_to_review_on_a_fake_worker(self):
        self.enable()
        self.codex = ScenarioSequence(scenarios.success({"app/calc.py": "def add(a, b):\n    return a + b\n"}))
        self.kernel = self.new_kernel()
        tid = self.queued(self.contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(len(self.kernel.store.attempts(tid)), 1)

    def test_a_dirty_generic_project_is_refused_before_a_write_starts(self):
        self.enable()
        (self.plain / "uncommitted.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(Refused) as caught:
            self.submit(self.contract())
        self.assertTrue(any("DIRTY_BASE" in p for p in caught.exception.problems))

    def test_a_generic_contract_must_say_it_has_no_parent_contract(self):
        self.enable()
        with self.assertRaises(Refused) as caught:
            self.submit(self.contract(ad_hoc=False))
        self.assertTrue(any("ad_hoc=true" in p for p in caught.exception.problems))

    def test_a_harness_project_still_refuses_an_ad_hoc_write(self):
        with self.assertRaises(Refused) as caught:
            self.submit(sample_contract(ad_hoc=True))
        self.assertTrue(any("Harness project" in p for p in caught.exception.problems))


if __name__ == "__main__":
    unittest.main()
