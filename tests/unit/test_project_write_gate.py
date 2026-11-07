"""P8: a real project can be written only after the person switches writes on (`par project enable-write ID --confirm ID`): the readiness checks
must pass first, only that project's two lines change, the old file is kept, one audit line is written, and the Kernel still applies every per-task rule."""
import difflib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par import config as C
from par.fixtures import create_fixture
from par.core.kernel import Refused
from par.core.policy import PolicyViolation
from par.paths import APP_DIR_NAME, ensure_data_dir
from par.registry import add_project, load_registry, log_write_decision, projects_file, set_project_write
from tests.helpers import run_cli, tree_digest
from tests.kernel_fixture import KernelWorld
from tests.world import GOOD_STATE, git_env, make_harness_project, sample_contract


def make_git_project(path, home):
    make_harness_project(path)
    (path / "app").mkdir()
    (path / "app" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (path / ".gitignore").write_text("*.log\n")
    env = git_env(home)
    for args in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "init")):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, env=env)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        (self.home / "Library" / "Application Support").mkdir(parents=True)
        (self.home / "Documents").mkdir()
        self.data = self.home / "Library" / "Application Support" / APP_DIR_NAME
        ensure_data_dir(self.data, protected=())
        self.real = make_git_project(self.home / "Documents" / "Real", self.home)
        self.other = make_git_project(self.home / "Documents" / "Other", self.home)
        self._home = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._home.start()
        add_project(self.data, "real", self.real, vcs="git", home=self.home)
        add_project(self.data, "other", self.other, vcs="git", home=self.home)

    def tearDown(self):
        self._home.stop()
        self._tmp.cleanup()

    @property
    def toml(self):
        return projects_file(self.data)

    def text(self):
        return self.toml.read_text(encoding="utf-8")

    def backups(self):
        return sorted(p.name for p in self.toml.parent.iterdir() if ".par-backup-" in p.name)

    def args(self, *a):
        return [*a, "--data-dir", str(self.data)]

    def audit(self):
        path = self.data / "logs" / "project_writes.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class Registry(Base):
    def test_an_auto_registered_git_project_can_be_enabled_without_re_registering(self):
        auto = make_git_project(self.home / "Documents" / "Auto", self.home)
        add_project(self.data, "auto", auto, vcs="auto", home=self.home)
        entry, _ = set_project_write(self.data, "auto", True, day="2026-11-07", home=self.home)
        self.assertEqual((entry.vcs, entry.write_enabled, entry.write_approved), ("git", True, "2026-11-07"))
        self.assertIn('vcs = "git"', self.text())

    def test_enabling_changes_only_the_two_lines_of_that_project_and_keeps_the_old_file(self):
        before = self.text()
        entry, backup = set_project_write(self.data, "real", True, day="2026-10-15", home=self.home)
        after = self.text()
        self.assertEqual((entry.write_enabled, entry.write_approved, entry.fixture), (True, "2026-10-15", False))
        changes = [ln for ln in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0) if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
        self.assertEqual(changes, ["-write_enabled = false", "+write_enabled = true", '+write_approved = "2026-10-15"'])
        self.assertEqual(backup.read_text(encoding="utf-8"), before)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.toml.stat().st_mode), 0o600)
        reg = load_registry(self.data, self.home)
        self.assertTrue(reg.projects["real"].write_enabled)
        self.assertFalse(reg.projects["other"].write_enabled)                          # the other project is untouched

    def test_a_trailing_comment_survives_and_a_missing_line_is_added(self):
        body = self.text().replace("write_enabled = false", "write_enabled = false   # keep it off for now", 1)
        self.toml.write_text(body, encoding="utf-8")
        set_project_write(self.data, "real", True, day="2026-10-15", home=self.home)
        self.assertIn("write_enabled = true   # keep it off for now\nwrite_approved", self.text())
        bare = self.text().replace("write_enabled = false\n", "", 1)                 # the other project's line removed: the default is off
        self.toml.write_text(bare, encoding="utf-8")
        set_project_write(self.data, "other", True, day="2026-10-16", home=self.home)
        reg = load_registry(self.data, self.home)
        self.assertEqual((reg.projects["other"].write_enabled, reg.projects["other"].write_approved), (True, "2026-10-16"))

    def test_disabling_removes_the_approval_and_is_always_possible(self):
        set_project_write(self.data, "real", True, day="2026-10-15", home=self.home)
        entry, _ = set_project_write(self.data, "real", False, home=self.home)
        self.assertEqual((entry.write_enabled, entry.write_approved), (False, ""))
        self.assertNotIn("write_approved", self.text())

    def test_what_cannot_be_switched_is_refused_and_nothing_is_written(self):
        digest = tree_digest(self.toml.parent)
        for args, why in ((("ghost", True), "no registered project"), (("real", False), "already off")):
            with self.assertRaises(PolicyViolation, msg=why) as ctx:
                set_project_write(self.data, *args, home=self.home)
            self.assertIn(why, str(ctx.exception))
        set_project_write(self.data, "real", True, day="2026-10-15", home=self.home)
        digest = tree_digest(self.toml.parent)
        with self.assertRaises(PolicyViolation) as ctx:
            set_project_write(self.data, "real", True, home=self.home)
        self.assertIn("already on", str(ctx.exception))
        self.assertEqual(tree_digest(self.toml.parent), digest)

    def test_a_file_the_command_cannot_edit_exactly_is_left_alone(self):
        for body in (self.text() + '\n[projects.real]\npath = "~/Documents/Other"\n',                       # two tables with one name
                     self.text().replace("write_enabled = false", 'write_enabled = "false"', 1)):          # not the plain form
            self.toml.write_text(body, encoding="utf-8")
            digest = tree_digest(self.toml.parent)
            with self.assertRaises(PolicyViolation):
                set_project_write(self.data, "real", True, home=self.home)
            self.assertEqual(tree_digest(self.toml.parent), digest)

    def test_a_project_without_git_cannot_be_switched_on(self):
        add_project(self.data, "plain", make_harness_project(self.home / "Documents" / "Plain"), vcs="none", home=self.home)
        digest = tree_digest(self.toml.parent)
        with self.assertRaises(PolicyViolation) as ctx:
            set_project_write(self.data, "plain", True, home=self.home)
        self.assertIn("git", str(ctx.exception))
        self.assertEqual(tree_digest(self.toml.parent), digest)

    def test_two_switches_in_one_second_keep_both_backups(self):
        with mock.patch("par.registry.time.strftime", return_value="20261015-120000"):
            set_project_write(self.data, "real", True, day="2026-10-15", home=self.home)
            set_project_write(self.data, "real", False, home=self.home)
        self.assertEqual(self.backups(), ["projects.toml.par-backup-20261015-120000", "projects.toml.par-backup-20261015-120000-1"])

    def test_a_fixture_project_is_always_writable_and_has_nothing_to_switch(self):
        create_fixture(self.data, "fx", home=self.home)
        digest = tree_digest(self.toml.parent)
        for enabled in (True, False):
            with self.assertRaises(PolicyViolation) as ctx:
                set_project_write(self.data, "fx", enabled, home=self.home)
            self.assertIn("fixture", str(ctx.exception))
        code, out, _ = run_cli("project", "enable-write", "fx", "--data-dir", str(self.data))
        self.assertEqual(code, 0)
        self.assertIn("always writable", out)
        self.assertEqual(tree_digest(self.toml.parent), digest)

    def test_the_audit_line_has_only_the_documented_fields(self):
        log_write_decision(self.data, "real", True, head="abc123")
        log_write_decision(self.data, "real", False, head="abc123")
        lines = self.audit()
        self.assertEqual([ln["writes"] for ln in lines], ["on", "off"])
        for line in lines:
            self.assertEqual(set(line), {"ts", "project", "writes", "head", "by"})
        self.assertEqual(stat.S_IMODE((self.data / "logs" / "project_writes.jsonl").stat().st_mode), 0o600)


class Cli(Base):
    def test_without_the_confirmation_the_command_only_checks_and_explains(self):
        digest = tree_digest(self.toml.parent)
        code, out, _ = run_cli(*self.args("project", "enable-write", "real"))
        self.assertEqual(code, 0, out)
        self.assertIn("ready:", out)
        self.assertIn("--confirm real", out)
        code, out, _ = run_cli(*self.args("project", "enable-write", "real", "--confirm", "other"))     # the wrong id confirms nothing
        self.assertEqual(code, 0)
        self.assertIn("--confirm real", out)
        self.assertEqual(tree_digest(self.toml.parent), digest)
        self.assertEqual(self.audit(), [])

    def test_a_project_that_is_not_ready_is_refused_even_with_the_confirmation(self):
        (self.real / "app" / "calc.py").write_text("changed\n")                                             # an uncommitted change
        digest = tree_digest(self.toml.parent)
        code, out, _ = run_cli(*self.args("project", "enable-write", "real", "--confirm", "real"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", out)
        self.assertIn("DIRTY_BASE", out)
        self.assertEqual(tree_digest(self.toml.parent), digest)
        self.assertEqual(self.audit(), [])

    def test_the_confirmed_switch_works_is_shown_audited_and_can_be_undone(self):
        code, out, _ = run_cli(*self.args("project", "enable-write", "real", "--confirm", "real"))
        self.assertEqual(code, 0, out)
        self.assertIn("are ON", out)
        self.assertNotIn(str(self.home), out)
        self.assertEqual(len(self.backups()), 1)
        code, out, _ = run_cli(*self.args("project", "show", "real"))
        self.assertIn("Writes    ALLOWED", out)
        code, out, _ = run_cli(*self.args("project", "enable-write", "real", "--confirm", "real"))
        self.assertIn("already on", out)
        code, out, _ = run_cli(*self.args("project", "disable-write", "real"))
        self.assertEqual(code, 0)
        self.assertIn("are OFF", out)
        self.assertIn("already off", run_cli(*self.args("project", "disable-write", "real"))[1])
        code, out, _ = run_cli(*self.args("project", "show", "real"))
        self.assertIn("Writes    BLOCKED", out)
        self.assertEqual([(ln["project"], ln["writes"]) for ln in self.audit()], [("real", "on"), ("real", "off")])
        self.assertTrue(all(len(ln["head"]) == 40 for ln in self.audit()))                                  # the commit the switch was made at

    def test_doctor_says_which_real_projects_were_switched_on(self):
        self.assertIn("writes are disabled for all real projects", run_cli(*self.args("doctor"))[1])
        run_cli(*self.args("project", "enable-write", "real", "--confirm", "real"))
        out = run_cli(*self.args("doctor"))[1]
        self.assertRegex(out, r"writes are enabled for real \(approved \d{4}-\d{2}-\d{2}\) and disabled for the other real projects")
        self.assertNotIn("disabled for all real projects", out)
        run_cli(*self.args("project", "disable-write", "real"))
        self.assertIn("writes are disabled for all real projects", run_cli(*self.args("doctor"))[1])

    def test_an_unknown_project_is_a_usage_error(self):
        code, _, err = run_cli(*self.args("project", "enable-write", "ghost"))
        self.assertEqual(code, 2)
        self.assertIn("no registered project", err)


class WithTheKernel(KernelWorld):
    """The switch is only the first gate: the Kernel still asks for the readiness, the approval and the paths of every task."""
    default_files = None

    def setUp(self):
        super().setUp()
        self.real = self.make_project("real")
        self.cfg = self.data / "config"
        add_project(self.data, "real", self.real, vcs="git", home=self.home)

    def kernel_for_registry(self):
        reg = load_registry(self.data, self.home)
        return self.new_kernel(projects={**self.projects, **reg.projects})

    def submit(self, kernel, **over):
        doc = sample_contract(project_id="real", **over)
        return kernel.submit(doc, submitted_by="user")

    def test_a_write_task_is_blocked_until_the_switch_and_then_waits_for_the_persons_approval(self):
        with self.assertRaises(Refused) as ctx:
            self.submit(self.kernel_for_registry(), idempotency_key="before")
        self.assertTrue(any("WRITE_DISABLED" in p for p in ctx.exception.problems), ctx.exception.problems)
        set_project_write(self.data, "real", True, day="2026-10-15", home=self.home)
        kernel = self.kernel_for_registry()
        result = self.submit(kernel, idempotency_key="after")
        self.assertEqual(result.status, "PERMISSION_WAIT")                       # nothing runs before the person approves the write permission
        self.assertEqual([a["kind"] for a in kernel.describe(result.task_id)["pending_approvals"]], ["WRITE_PERMISSION"])

    def test_harness_files_stay_off_limits_and_a_dirty_base_blocks_again(self):
        set_project_write(self.data, "real", True, day="2026-10-15", home=self.home)
        kernel = self.kernel_for_registry()
        for paths in ([".ai/"], ["AGENTS.md"], ["CLAUDE.md"]):
            with self.assertRaises(Refused, msg=paths):
                self.submit(kernel, allowed_paths=paths, idempotency_key="p" + "".join(paths))
        (self.real / "README.md").write_text("edited by the person meanwhile\n")
        with self.assertRaises(Refused) as ctx:
            self.submit(kernel, idempotency_key="dirty")
        self.assertTrue(any("DIRTY_BASE" in p for p in ctx.exception.problems), ctx.exception.problems)


if __name__ == "__main__":
    unittest.main()
