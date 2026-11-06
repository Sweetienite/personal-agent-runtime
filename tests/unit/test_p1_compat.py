import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par.config import parse_projects_text
from par.context.harness import HarnessContextAdapter
from par.context.resolver import default_context
from par.core.policy import PolicyViolation
from par.paths import APP_DIR_NAME, ensure_data_dir
from par.registry import add_project, load_registry, projects_file
from tests.helpers import run_cli, tree_digest
from tests.kernel_fixture import KernelWorld, r1_contract
from tests.world import make_harness_project


class OldRegistry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        (self.home / "Library" / "Application Support").mkdir(parents=True)
        (self.home / "Documents").mkdir()
        self.data = self.home / "Library" / "Application Support" / APP_DIR_NAME
        ensure_data_dir(self.data, protected=())
        self.plain = self.home / "Documents" / "Plain"
        self.plain.mkdir()
        self._home = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._home.start()

    def tearDown(self):
        self._home.stop()
        self._tmp.cleanup()

    def old_text(self) -> str:
        return f'[projects.plain]\npath = "{self.plain}"\n'

    def test_an_entry_without_the_field_still_gets_the_harness_adapter_and_checks(self):
        projects, issues = parse_projects_text(self.old_text(), self.home)
        self.assertEqual(issues, ())
        entry = projects["plain"]
        self.assertEqual(entry.context_mode, "harness")
        context = default_context(entry, self.home)
        self.assertIsInstance(context, HarnessContextAdapter)
        codes = [b.code for b in context.write_readiness().blockers]
        self.assertIn("NO_STATE", codes)
        self.assertIn("NO_ACTIVE_CONTRACT", codes)

    def test_a_broken_or_unknown_registry_is_an_error_not_a_silent_generic(self):
        broken, issues = parse_projects_text("[projects.plain\n", self.home)
        self.assertEqual(broken, {})
        self.assertTrue(issues)
        projects, issues = parse_projects_text(self.old_text().replace("[projects.plain]", "[projects.plain]\ncontext_mode = \"auto\""), self.home)
        self.assertEqual(projects, {})
        self.assertTrue(any(i.where.endswith("context_mode") for i in issues))

    def test_loading_never_rewrites_the_registry(self):
        add_project(self.data, "plain", self.plain, home=self.home, context_mode="generic")
        path = projects_file(self.data)
        before = path.read_bytes()
        load_registry(self.data, self.home)
        load_registry(self.data, self.home)
        self.assertEqual(path.read_bytes(), before)

    def test_a_harness_registration_writes_no_field_an_older_build_would_reject(self):
        harness = make_harness_project(self.home / "Documents" / "Harness")
        add_project(self.data, "harness", harness, home=self.home, context_mode="harness")
        text = projects_file(self.data).read_text(encoding="utf-8")
        self.assertNotIn("context_mode", text)
        self.assertEqual(load_registry(self.data, self.home).projects["harness"].context_mode, "harness")

    def test_registering_the_same_project_twice_is_refused_and_leaves_one_entry(self):
        add_project(self.data, "plain", self.plain, home=self.home, context_mode="generic")
        with self.assertRaises(PolicyViolation):
            add_project(self.data, "plain", self.plain, home=self.home, context_mode="generic")
        self.assertEqual(list(load_registry(self.data, self.home).projects), ["plain"])

    def test_a_generic_project_with_harness_files_is_told_they_are_ignored(self):
        (self.plain / ".ai").mkdir()
        (self.plain / ".ai" / "STATE.md").write_text("# state\n", encoding="utf-8")
        add_project(self.data, "plain", self.plain, home=self.home, context_mode="generic")
        code, out, _ = run_cli("project", "show", "plain", "--data-dir", str(self.data))
        self.assertEqual(code, 0, out)
        self.assertIn("ignored in generic mode", out)

    def test_nothing_in_the_project_changes_when_it_is_registered(self):
        before = tree_digest(self.plain)
        add_project(self.data, "plain", self.plain, home=self.home, context_mode="generic")
        self.assertEqual(tree_digest(self.plain), before)


class Reload(KernelWorld):
    default_files = None

    def test_queued_and_paused_states_survive_a_reload(self):
        paused = self.queued(r1_contract())
        queued = self.submit(r1_contract()).task_id
        self.kernel.pause(paused)
        kernel = self.new_kernel()
        self.assertEqual(kernel.describe(paused)["status"], "PAUSED")
        self.assertEqual(kernel.describe(queued)["status"], "QUEUED")

    def test_a_task_waiting_for_review_survives_a_reload(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        kernel = self.new_kernel()
        self.assertEqual(kernel.describe(tid)["status"], "REVIEW_REQUIRED")

    def test_a_pending_approval_survives_a_new_kernel_over_the_same_ledger(self):
        tid = self.submit().task_id
        before = [a["id"] for a in self.kernel.store.approvals(tid, "PENDING")]
        kernel = self.new_kernel()
        self.assertEqual(kernel.describe(tid)["status"], "PERMISSION_WAIT")
        self.assertEqual([a["id"] for a in kernel.store.approvals(tid, "PENDING")], before)


if __name__ == "__main__":
    unittest.main()
