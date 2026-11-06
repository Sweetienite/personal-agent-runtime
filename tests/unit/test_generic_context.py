import os
import unittest
from dataclasses import replace
from pathlib import Path

from par.config import ProjectEntry
from par.context.generic import GenericContextAdapter
from par.core.policy import PolicyViolation
from tests.kernel_fixture import KernelWorld


class GenericContext(KernelWorld):
    default_files = None

    def setUp(self):
        super().setUp()
        self.plain = self.base / "projects" / "plain"
        self.plain.mkdir(parents=True)
        (self.plain / "README.md").write_text("# plain project\n", encoding="utf-8")
        self.git(self.plain, "init", "-q", "-b", "main")
        self.git(self.plain, "add", "-A")
        self.git(self.plain, "commit", "-q", "-m", "init")
        self.entry = ProjectEntry("plain", self.plain, "git", False, False, "", "generic")

    def adapter(self, **over):
        return GenericContextAdapter(replace(self.entry, **over), self.home)

    def test_an_empty_repository_still_has_a_manifest_with_its_git_base(self):
        (self.plain / "README.md").unlink()
        self.git(self.plain, "add", "-A")
        self.git(self.plain, "commit", "-q", "-m", "drop readme")
        a = self.adapter()
        v = a.view()
        self.assertTrue(v.exists)
        self.assertIsNone(v.rules)
        self.assertEqual(v.contracts, ())
        m = a.manifest(v)
        self.assertEqual(m.base["vcs"], "git")
        self.assertTrue(m.base["head"])
        self.assertEqual(m.entries, ())
        self.assertTrue(any("AGENTS.md is missing" in w for w in m.warnings))

    def test_readme_and_agents_are_inlined_and_a_different_claude_is_a_pointer(self):
        (self.plain / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
        (self.plain / "CLAUDE.md").write_text("# other rules\n", encoding="utf-8")
        m = self.adapter().manifest()
        self.assertEqual([(e.kind, e.mode) for e in m.entries],
                         [("readme", "inline"), ("rules", "inline"), ("bridge", "pointer")])
        self.assertTrue(any("does not decide which one wins" in w for w in m.warnings))

    def test_a_thin_claude_bridge_is_skipped(self):
        (self.plain / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
        (self.plain / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        m = self.adapter().manifest()
        self.assertEqual([(e.kind, e.mode) for e in m.entries], [("readme", "inline"), ("rules", "inline"), ("bridge", "skip")])

    def test_an_oversized_readme_becomes_a_pointer_not_a_truncated_inline(self):
        (self.plain / "README.md").write_text("x" * 25_000, encoding="utf-8")
        m = self.adapter().manifest()
        readme = next(e for e in m.entries if e.kind == "readme")
        self.assertEqual(readme.mode, "pointer")
        self.assertIn("exceeds", readme.reason)

    def test_a_symlinked_readme_is_refused_when_rendering_not_read(self):
        outside = self.base / "outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        (self.plain / "README.md").unlink()
        os.symlink(outside, self.plain / "README.md")
        a = self.adapter()
        m = a.manifest()
        with self.assertRaises(PolicyViolation):
            a.render(m)

    def test_write_readiness_keeps_the_write_switch_git_and_a_clean_base(self):
        a = self.adapter()
        self.assertEqual([b.code for b in a.write_readiness().blockers], ["WRITE_DISABLED"])
        self.assertTrue(self.adapter(write_enabled=True).write_readiness().allowed)
        (self.plain / "dirty.txt").write_text("x", encoding="utf-8")
        self.assertEqual([b.code for b in self.adapter(write_enabled=True).write_readiness().blockers], ["DIRTY_BASE"])

    def test_write_readiness_refuses_a_project_without_git(self):
        plain = self.base / "nogit"
        plain.mkdir()
        entry = ProjectEntry("nogit", plain, "auto", True, False, "2026-11-06", "generic")
        readiness = GenericContextAdapter(entry, self.home).write_readiness()
        self.assertFalse(readiness.allowed)
        self.assertIn("NO_VCS", [b.code for b in readiness.blockers])


if __name__ == "__main__":
    unittest.main()
