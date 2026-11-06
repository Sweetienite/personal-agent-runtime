import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par.config import parse_projects_text
from par.core.policy import PolicyViolation
from par.paths import APP_DIR_NAME, ensure_data_dir
from par.registry import add_project, load_registry, projects_file
from tests.helpers import run_cli, tree_digest


class Base(unittest.TestCase):
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

    def registry_text(self, mode: str) -> str:
        return (f'[projects.plain]\npath = "{self.plain}"\n' if mode == "" else
                f'[projects.plain]\npath = "{self.plain}"\ncontext_mode = "{mode}"\n')


class Parsing(Base):
    def test_an_entry_without_the_field_keeps_the_harness_behaviour(self):
        projects, issues = parse_projects_text(self.registry_text(""), self.home)
        self.assertEqual(issues, ())
        self.assertEqual(projects["plain"].context_mode, "harness")

    def test_an_explicit_generic_mode_is_read(self):
        projects, issues = parse_projects_text(self.registry_text("generic"), self.home)
        self.assertEqual(issues, ())
        self.assertEqual(projects["plain"].context_mode, "generic")

    def test_an_unknown_mode_is_an_error_not_a_silent_default(self):
        _, issues = parse_projects_text(self.registry_text("auto"), self.home)
        self.assertTrue(any(i.where.endswith("context_mode") and "must be one of" in i.message for i in issues))


class Registration(Base):
    def test_a_plain_directory_registers_as_generic_and_nothing_in_it_changes(self):
        before = tree_digest(self.plain)
        entry = add_project(self.data, "plain", self.plain, home=self.home, context_mode="generic")
        self.assertEqual((entry.context_mode, entry.write_enabled), ("generic", False))
        self.assertEqual(tree_digest(self.plain), before)
        self.assertIn('context_mode = "generic"', projects_file(self.data).read_text(encoding="utf-8"))
        self.assertEqual(load_registry(self.data, self.home).projects["plain"].context_mode, "generic")

    def test_the_api_default_is_still_the_harness_requirement(self):
        with self.assertRaises(PolicyViolation) as caught:
            add_project(self.data, "plain", self.plain, home=self.home)
        self.assertIn("not a Harness project", str(caught.exception))

    def test_scaffolding_and_generic_together_are_refused(self):
        with self.assertRaises(PolicyViolation) as caught:
            add_project(self.data, "plain", self.plain, home=self.home, scaffold=True, context_mode="generic")
        self.assertIn("--scaffold-harness", str(caught.exception))

    def test_the_cli_shows_the_generic_manifest(self):
        add_project(self.data, "plain", self.plain, home=self.home, context_mode="generic")
        (self.plain / "README.md").write_text("# plain\n", encoding="utf-8")
        code, out, _ = run_cli("project", "show", "plain", "--manifest", "--data-dir", str(self.data))
        self.assertEqual(code, 0, out)
        self.assertIn("readme", out)
        self.assertIn("README.md", out)

    def test_the_cli_default_registers_a_plain_project_as_generic(self):
        before = tree_digest(self.plain)
        code, out, _ = run_cli("project", "add", "plain", str(self.plain), "--data-dir", str(self.data))
        self.assertEqual(code, 0, out)
        self.assertIn("context generic", out)
        self.assertEqual(tree_digest(self.plain), before)
        self.assertEqual(load_registry(self.data, self.home).projects["plain"].context_mode, "generic")


if __name__ == "__main__":
    unittest.main()
