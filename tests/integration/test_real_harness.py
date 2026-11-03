"""Opt-in integration test against the REAL Harness on this machine. Read-only.

    PAR_REAL_HARNESS=1 python -B -m unittest tests.integration.test_real_harness -v

Skipped by default so the normal suite never touches anything outside its own temporary folders.
"""
import os
import unittest
from pathlib import Path

from par.config import ProjectEntry, default_harness_settings
from par.context.harness import HarnessContextAdapter
from par.harness import check as hc


@unittest.skipUnless(os.environ.get("PAR_REAL_HARNESS") == "1", "set PAR_REAL_HARNESS=1 to run against the real Harness")
class RealHarness(unittest.TestCase):
    def test_core_and_managed_projects_are_consistent(self):
        snap = hc.run_check(default_harness_settings())
        self.assertTrue(snap["core"]["present"], "Harness Core not found at the default location")
        self.assertEqual(len(set(snap["core"]["versions"].values())), 1, snap["core"]["versions"])
        self.assertEqual(snap["sync"]["exit"], 0, "sync_runtime_core --check must stay clean")
        self.assertGreaterEqual(len(snap["projects"]), 3)
        self.assertNotIn(hc.FAIL, {f.level for f in hc.evaluate(snap)})

    def test_two_runs_see_identical_harness_files(self):
        """Running the check twice must not change a single Harness file (hash comparison)."""
        s = default_harness_settings()
        a, b = hc.run_check(s), hc.run_check(s)
        self.assertEqual(a["core"]["tracked"], b["core"]["tracked"])
        for path in a["projects"]:
            for key in ("agents", "state"):
                self.assertEqual(a["projects"][path][key].get("sha256"), b["projects"][path][key].get("sha256"), (path, key))


@unittest.skipUnless(os.environ.get("PAR_REAL_HARNESS") == "1", "set PAR_REAL_HARNESS=1 to run against the real Harness")
class RealProjectContext(unittest.TestCase):
    """The adapter on the real Runge. Assertions follow the project's current state instead of freezing it."""

    def setUp(self):
        self.root = Path.home() / "Documents" / "Runge"
        if not (self.root / ".ai" / "STATE.md").is_file():
            self.skipTest("Runge is not on this machine")
        self.adapter = HarnessContextAdapter(ProjectEntry("runge", self.root, "git"))

    def test_state_handling_follows_its_size(self):
        v = self.adapter.view()
        m = self.adapter.manifest(v)
        state = next(e for e in m.entries if e.kind == "state")
        self.assertEqual(state.sha256, hc.sha256_file(self.root / ".ai" / "STATE.md"))
        codes = [b.code for b in self.adapter.write_readiness(v).blockers]
        if v.state.oversized:
            self.assertEqual(state.mode, "pointer", "an oversized STATE is never inlined")
            self.assertIn("STATE_OVERSIZED", codes)
        else:
            self.assertEqual(state.mode, "inline")
            self.assertNotIn("STATE_OVERSIZED", codes)
        self.assertIn("WRITE_DISABLED", codes)

    def test_inline_text_is_verified_and_the_check_is_read_only(self):
        before = hc.sha256_file(self.root / "AGENTS.md"), hc.sha256_file(self.root / ".ai" / "STATE.md")
        m = self.adapter.manifest()
        self.assertEqual(m.digest, self.adapter.manifest().digest)
        for entry, text in self.adapter.read_inline(m):
            self.assertTrue(text)
        self.assertEqual(before, (hc.sha256_file(self.root / "AGENTS.md"), hc.sha256_file(self.root / ".ai" / "STATE.md")))


if __name__ == "__main__":
    unittest.main()
