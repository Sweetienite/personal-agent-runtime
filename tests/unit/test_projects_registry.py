import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par import config as C
from par.core.policy import PolicyViolation
from par.paths import APP_DIR_NAME, ensure_data_dir
from par.registry import add_project, load_registry, projects_file
from tests import SRC
from tests.helpers import run_cli, tree_digest
from tests.world import GOOD_STATE, make_harness_project

EXAMPLES = SRC.parent / "config"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        (self.home / "Library" / "Application Support").mkdir(parents=True)
        (self.home / "Documents").mkdir()
        self.data = self.home / "Library" / "Application Support" / APP_DIR_NAME
        ensure_data_dir(self.data, protected=())
        self.alpha = make_harness_project(self.home / "Documents" / "Alpha")
        self.beta = make_harness_project(self.home / "Documents" / "Beta")
        self._home = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._home.start()

    def tearDown(self):
        self._home.stop()
        self._tmp.cleanup()

    def add(self, pid, path, **kw):
        return add_project(self.data, pid, path, home=self.home, **kw)


def errors(issues):
    return {i.where for i in issues if i.severity == "error"}


class Parsing(Base):
    def parse(self, body):
        return C.parse_projects_text("schema_version = 1\n" + body, home=self.home)

    def test_minimal_entry_gets_safe_defaults(self):
        projects, issues = self.parse('[projects.alpha]\npath = "~/Documents/Alpha"\n')
        self.assertEqual(issues, ())
        p = projects["alpha"]
        self.assertEqual((p.path, p.vcs, p.write_enabled), (self.alpha, "auto", False))

    def test_writes_to_a_real_project_need_the_approval_date_and_git(self):
        head = '[projects.alpha]\npath = "~/Documents/Alpha"\n'
        for body, where in ((head + "write_enabled = true\n", "projects.alpha.write_approved"),                                      # no approval date
                            (head + 'vcs = "git"\nwrite_enabled = true\n', "projects.alpha.write_approved"),
                            (head + 'vcs = "git"\nwrite_enabled = true\nwrite_approved = "yesterday"\n', "projects.alpha.write_approved"),
                            (head + 'vcs = "git"\nwrite_enabled = true\nwrite_approved = 20261015\n', "projects.alpha.write_approved"),
                            (head + 'vcs = "none"\nwrite_enabled = true\nwrite_approved = "2026-10-15"\n', "projects.alpha.vcs"),     # no git baseline: no rollback
                            (head + 'write_enabled = true\nwrite_approved = "2026-10-15"\n', "projects.alpha.vcs"),                    # "auto" is not "git"
                            (head + '\nwrite_approved = "2026-10-15"\n', "projects.alpha.write_approved"),                           # approval without the switch
                            (head + 'write_enabled = "true"\n', "projects.alpha.write_enabled"),
                            (head + "write_enabled = 1\n", "projects.alpha.write_enabled")):
            projects, issues = self.parse(body)
            self.assertIn(where, errors(issues), body)
            self.assertNotIn("alpha", projects, "an entry that asks for writes the wrong way is dropped, never half-registered")

    def test_a_real_project_with_the_approval_date_and_git_may_be_written(self):
        projects, issues = self.parse('[projects.alpha]\npath = "~/Documents/Alpha"\nvcs = "git"\nwrite_enabled = true\nwrite_approved = "2026-10-15"\n')
        self.assertEqual(issues, ())
        p = projects["alpha"]
        self.assertEqual((p.write_enabled, p.write_approved, p.fixture), (True, "2026-10-15", False))

    def test_invalid_entries(self):
        cases = [
            ('[projects.Alpha]\npath = "~/Documents/Alpha"\n', "projects.Alpha"),
            ('[projects.alpha]\npath = "relative/dir"\n', "projects.alpha.path"),
            ('[projects.alpha]\nvcs = "git"\n', "projects.alpha.path"),
            ('[projects.alpha]\npath = "~/Documents/Alpha"\nvcs = "svn"\n', "projects.alpha.vcs"),
            ('[projects.alpha]\npath = "~/Documents/Alpha"\nvcs = ["git"]\n', "projects.alpha.vcs"),
            ('[projects.alpha]\npath = "~/Documents/AI_Harness_Core"\n', "projects.alpha.path"),
            ('[projects.alpha]\npath = "~/.codex/anything"\n', "projects.alpha.path"),
            ('projects = 5\n', "projects"),
            ('[projects.alpha]\npath = "~/Documents/Alpha"\ntoken = "x"\n', "projects.alpha.token"),
        ]
        for body, where in cases:
            projects, issues = self.parse(body)
            self.assertIn(where, errors(issues), body)
            self.assertEqual(projects, {}, body)

    def test_same_folder_cannot_be_registered_twice(self):
        projects, issues = self.parse(
            '[projects.alpha]\npath = "~/Documents/Alpha"\n[projects.again]\npath = "~/Documents/Alpha/../Alpha"\n')
        self.assertEqual(set(projects), {"alpha"})
        self.assertIn("projects.again.path", errors(issues))

    def test_unknown_key_is_only_a_warning(self):
        projects, issues = self.parse('[projects.alpha]\npath = "~/Documents/Alpha"\nnote = "hi"\n')
        self.assertIn("alpha", projects)
        self.assertEqual({i.severity for i in issues}, {"warning"})

    def test_broken_toml(self):
        projects, issues = C.parse_projects_text("[projects\n", home=self.home)
        self.assertEqual(projects, {})
        self.assertIn("projects.toml", errors(issues))

    def test_shipped_example_has_no_projects_and_no_issues(self):
        pf = C.load_projects_file(EXAMPLES / "projects.example.toml")
        self.assertEqual((pf.projects, pf.issues), ({}, ()))

    def test_settings_and_projects_load_together(self):
        f = self.base / "projects.toml"
        f.write_text('schema_version = 1\n[harness]\nmax_depth = 2\n[projects.alpha]\npath = "~/Documents/Alpha"\n')
        pf = C.load_projects_file(f, home=self.home)
        self.assertEqual((pf.settings.max_depth, list(pf.projects), pf.issues), (2, ["alpha"], ()))


class AddProject(Base):
    def test_registers_and_reads_back(self):
        entry = self.add("alpha", self.alpha)
        self.assertEqual((entry.id, entry.path, entry.vcs, entry.write_enabled), ("alpha", self.alpha, "auto", False))
        target = projects_file(self.data)
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
        text = target.read_text()
        self.assertIn('path = "~/Documents/Alpha"', text, "paths under the home directory are stored in ~ form")
        self.assertIn("write_enabled = false", text)
        reg = load_registry(self.data, home=self.home)
        self.assertEqual((list(reg.projects), reg.issues), (["alpha"], ()))

    def test_appends_and_keeps_what_the_user_wrote(self):
        target = projects_file(self.data)
        target.write_text('# my notes stay\nschema_version = 1\n\n[harness]\nmax_depth = 3\n')
        self.add("alpha", self.alpha)
        self.add("beta", self.beta, vcs="none")
        text = target.read_text()
        self.assertTrue(text.startswith("# my notes stay\n"))
        self.assertIn("max_depth = 3", text)
        reg = load_registry(self.data, home=self.home)
        self.assertEqual(sorted(reg.projects), ["alpha", "beta"])
        self.assertEqual(reg.projects["beta"].vcs, "none")
        self.assertEqual(reg.settings.max_depth, 3)

    def test_duplicates_are_refused(self):
        self.add("alpha", self.alpha)
        for pid, path in (("alpha", self.beta), ("other", self.alpha)):
            with self.assertRaises(PolicyViolation):
                self.add(pid, path)
        self.assertEqual(list(load_registry(self.data, home=self.home).projects), ["alpha"])

    def test_only_harness_projects_are_accepted_and_nothing_is_written_otherwise(self):
        plain = self.home / "Documents" / "Plain"
        plain.mkdir()
        (plain / "AGENTS.md").write_text("x")          # no .ai/STATE.md
        for pid, path, vcs in (("plain", plain, "auto"), ("gone", self.base / "gone", "auto"), ("rel", "relative/dir", "auto"),
                               ("Bad", self.alpha, "auto"), ("alpha", self.alpha, "svn")):
            with self.assertRaises(PolicyViolation, msg=pid):
                self.add(pid, path, vcs=vcs)
        self.assertFalse(projects_file(self.data).exists())

    def test_a_protected_folder_is_never_a_project(self):
        core = make_harness_project(self.home / "Documents" / "AI_Harness_Core")
        with self.assertRaises(PolicyViolation) as ctx:
            self.add("core", core)
        self.assertIn("protected", str(ctx.exception))

    def test_needs_an_initialised_data_dir(self):
        with self.assertRaises(PolicyViolation) as ctx:
            add_project(self.base / "no_data", "alpha", self.alpha, home=self.home)
        self.assertIn("par init", str(ctx.exception))

    def test_symlinked_registry_is_refused(self):
        outside = self.base / "elsewhere.toml"
        outside.write_text("schema_version = 1\n")
        os.symlink(outside, projects_file(self.data))
        with self.assertRaises(PolicyViolation):
            self.add("alpha", self.alpha)
        self.assertEqual(outside.read_text(), "schema_version = 1\n")

    def test_a_broken_registry_is_left_untouched(self):
        target = projects_file(self.data)
        target.write_text("[projects\n")
        before = tree_digest(self.data)
        with self.assertRaises(PolicyViolation):
            self.add("alpha", self.alpha)
        self.assertEqual(before, tree_digest(self.data))

    def test_the_project_folder_is_never_touched(self):
        before = tree_digest(self.alpha)
        self.add("alpha", self.alpha)
        self.assertEqual(before, tree_digest(self.alpha))
        self.assertFalse((self.data / "config" / "projects.toml.tmp").exists(), "no temp file is left behind")


class Scaffold(Base):
    def test_creates_only_what_is_missing_and_then_registers(self):
        plain = self.home / "Documents" / "Plain"
        plain.mkdir()
        entry = self.add("plain", plain, scaffold=True)
        self.assertEqual(entry.id, "plain")
        self.assertIn("Agent Entry", (plain / "AGENTS.md").read_text())
        self.assertIn("Current State", (plain / ".ai" / "STATE.md").read_text())

    def test_never_overwrites_a_file_that_already_exists(self):
        plain = self.home / "Documents" / "Plain"
        plain.mkdir()
        (plain / "AGENTS.md").write_text("my own rules, untouched\n")
        self.add("plain", plain, scaffold=True)
        self.assertEqual((plain / "AGENTS.md").read_text(), "my own rules, untouched\n")
        self.assertIn("Current State", (plain / ".ai" / "STATE.md").read_text())

    def test_without_the_flag_nothing_is_written_and_it_is_still_refused(self):
        plain = self.home / "Documents" / "Plain"
        plain.mkdir()
        with self.assertRaises(PolicyViolation):
            self.add("plain", plain)
        self.assertEqual(sorted(p.name for p in plain.iterdir()), [])

    def test_a_protected_folder_is_still_never_scaffolded(self):
        core = self.home / "Documents" / "AI_Harness_Core"
        core.mkdir()
        with self.assertRaises(PolicyViolation) as ctx:
            self.add("core", core, scaffold=True)
        self.assertIn("protected", str(ctx.exception))
        self.assertEqual(sorted(p.name for p in core.iterdir()), [])

    def test_a_file_that_appears_between_the_check_and_the_write_is_never_overwritten(self):
        plain = self.home / "Documents" / "Plain"
        plain.mkdir()
        real_open = os.open
        raced = {"done": False}

        def racing_open(path, flags, mode=0o644):
            if not raced["done"] and os.fspath(path).endswith("AGENTS.md"):
                raced["done"] = True
                Path(path).write_text("a concurrent writer got here first\n")
            return real_open(path, flags, mode)

        with mock.patch("par.registry.os.open", side_effect=racing_open):
            self.add("plain", plain, scaffold=True)
        self.assertEqual((plain / "AGENTS.md").read_text(), "a concurrent writer got here first\n")
        self.assertIn("Current State", (plain / ".ai" / "STATE.md").read_text())

    def test_scaffolded_project_has_no_active_contract_yet(self):
        plain = self.home / "Documents" / "Plain"
        plain.mkdir()
        self.add("plain", plain, scaffold=True)
        from par.context.harness import HarnessContextAdapter
        entry = load_registry(self.data, home=self.home).projects["plain"]
        readiness = HarnessContextAdapter(entry, home=self.home).write_readiness(assume_enabled=True)
        self.assertIn("NO_ACTIVE_CONTRACT", {b.code for b in readiness.blockers})


class Cli(Base):
    def args(self, *a):
        return [*a, "--data-dir", str(self.data)]

    def test_add_list_show_cycle(self):
        code, out, _ = run_cli("project", "add", "alpha", str(self.alpha), "--context-mode", "harness", "--data-dir", str(self.data))
        self.assertEqual(code, 0, out)
        self.assertIn("writes disabled", out)
        self.assertNotIn(str(self.home), out)
        code, out, _ = run_cli(*self.args("project", "list"))
        self.assertEqual(code, 0)
        self.assertIn("Registered projects (1)", out)
        self.assertIn("alpha", out)
        self.assertNotIn(str(self.home), out)
        code, out, _ = run_cli(*self.args("project", "show", "alpha"))
        self.assertEqual(code, 0)
        for needle in ("Project   alpha", "Rules     AGENTS.md", "STATE     .ai/STATE.md", "healthy", "Writes    BLOCKED", "WRITE_DISABLED", "Manifest  digest"):
            self.assertIn(needle, out)
        self.assertNotIn(str(self.home), out)

    def test_show_json_has_view_readiness_and_manifest(self):
        self.add("alpha", self.alpha)
        code, out, _ = run_cli(*self.args("project", "show", "alpha", "--json"))
        doc = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(set(doc), {"view", "write_readiness", "manifest"})
        self.assertFalse(doc["write_readiness"]["allowed"])
        self.assertEqual(doc["manifest"]["project_id"], "alpha")
        self.assertNotIn(str(self.home), out)

    def test_show_manifest_lists_entries(self):
        self.add("alpha", self.alpha)
        _, out, _ = run_cli(*self.args("project", "show", "alpha", "--manifest"))
        self.assertIn("[inline ] rules    AGENTS.md", out)
        self.assertIn("[skip   ] bridge   CLAUDE.md", out)

    def test_list_json(self):
        self.add("alpha", self.alpha)
        code, out, _ = run_cli(*self.args("project", "list", "--json"))
        rows = json.loads(out)
        self.assertEqual((code, len(rows), rows[0]["id"], rows[0]["write_allowed"]), (0, 1, "alpha", False))

    def test_empty_registry_is_not_an_error(self):
        code, out, _ = run_cli(*self.args("project", "list"))
        self.assertEqual(code, 0)
        self.assertIn("par project add", out)

    def test_unknown_project_and_broken_registry_are_exit_2(self):
        self.add("alpha", self.alpha)
        self.assertEqual(run_cli(*self.args("project", "show", "nope"))[0], 2)
        projects_file(self.data).write_text("schema_version = 1\n[projects.alpha]\npath = \"~/Documents/Alpha\"\nwrite_enabled = true\n")
        code, _, err = run_cli(*self.args("project", "list"))
        self.assertEqual(code, 2)
        self.assertIn("write_enabled", err)

    def test_add_refusals_are_exit_1_with_a_reason(self):
        code, _, err = run_cli(*self.args("project", "add", "alpha", str(self.base / "gone")))
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)

    def test_vcs_choice_is_validated_by_argparse(self):
        self.assertEqual(run_cli(*self.args("project", "add", "alpha", str(self.alpha), "--vcs", "svn"))[0], 2)

    def test_scaffold_harness_flag_creates_the_missing_files_and_says_so(self):
        plain = self.home / "Documents" / "Plain"
        plain.mkdir()
        code, out, _ = run_cli(*self.args("project", "add", "plain", str(plain), "--scaffold-harness"))
        self.assertEqual(code, 0, out)
        self.assertIn("created minimal AGENTS.md and", out)
        self.assertTrue((plain / "AGENTS.md").is_file())
        self.assertTrue((plain / ".ai" / "STATE.md").is_file())

    def test_without_scaffold_harness_the_refusal_mentions_the_flag(self):
        plain = self.home / "Documents" / "Plain"
        plain.mkdir()
        code, _, err = run_cli(*self.args("project", "add", "plain", str(plain), "--context-mode", "harness"))
        self.assertEqual(code, 1)
        self.assertIn("--scaffold-harness", err)

    def test_listing_and_showing_write_nothing(self):
        self.add("alpha", self.alpha)
        before = (tree_digest(self.data), tree_digest(self.alpha))
        run_cli(*self.args("project", "list"))
        run_cli(*self.args("project", "show", "alpha", "--manifest"))
        self.assertEqual(before, (tree_digest(self.data), tree_digest(self.alpha)))

    def test_doctor_reports_the_registry(self):
        _, out, _ = run_cli(*self.args("doctor"))
        self.assertIn("none registered", out)
        self.add("alpha", self.alpha)
        _, out, _ = run_cli(*self.args("doctor"))
        self.assertIn("1 registered (alpha); context: 0 generic / 1 harness; writes are disabled for all", out)
        self.alpha.rename(self.alpha.with_name("Moved"))
        code, out, _ = run_cli(*self.args("doctor"))
        self.assertIn("[WARN] projects", out)
        self.assertIn("folder not found: alpha", out)


if __name__ == "__main__":
    unittest.main()
