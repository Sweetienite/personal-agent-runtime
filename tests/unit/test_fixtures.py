"""Fixture projects: the one kind of project the Runtime may write to before P8, and the proof that nothing else can pass for one."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par import config as C
from par import fixtures as F
from par.context.harness import HarnessContextAdapter
from par.core.kernel import Kernel, Refused
from par.core.policy import PolicyViolation
from par.paths import APP_DIR_NAME, ensure_data_dir
from par.providers.registry import AdapterRegistry
from par.registry import add_project, load_registry, projects_file
from tests.helpers import run_cli
from tests.world import make_harness_project, sample_config, sample_contract


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        (self.home / "Library" / "Application Support").mkdir(parents=True)
        (self.home / "Documents").mkdir()
        self.data = self.home / "Library" / "Application Support" / APP_DIR_NAME
        ensure_data_dir(self.data, protected=())
        self._home = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._home.start()

    def tearDown(self):
        self._home.stop()
        self._tmp.cleanup()

    def create(self, pid="fixture"):
        return F.create_fixture(self.data, pid, home=self.home)

    def registry(self):
        return load_registry(self.data, home=self.home)

    def errors(self, text):
        _, issues = C.parse_projects_text("schema_version = 1\n" + text, home=self.home, fixtures_root=self.data / "fixtures")
        return {i.where: i.message for i in issues if i.severity == "error"}

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1"}).stdout.strip()


class Creation(Base):
    def test_a_fixture_is_a_small_harness_project_in_a_git_repository(self):
        info = self.create()
        root = info.entry.path
        self.assertEqual(root, self.data / "fixtures" / "fixture")
        for rel in ("AGENTS.md", "CLAUDE.md", ".ai/STATE.md", ".ai/tasks/active/FIXTURE-1.md", "app/calc.py", "tests/test_calc.py", "docs/spec.md", ".par-fixture.json"):
            self.assertTrue((root / rel).is_file(), rel)
        self.assertEqual(self.git(root, "rev-parse", "HEAD"), info.commit)
        self.assertEqual(self.git(root, "log", "--format=%s"), "fixture: initial commit")
        self.assertEqual(self.git(root, "status", "--porcelain"), "")
        self.assertEqual(stat.S_IMODE(os.stat(self.data / "fixtures").st_mode), 0o700)

    def test_the_bug_is_real_and_the_obvious_fix_cures_it(self):
        root = self.create().entry.path
        copy = self.base / "copy"
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git"))
        env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
        run = lambda: subprocess.run([sys.executable, "-B", "-m", "unittest", "-q"], cwd=copy, env=env, capture_output=True, text=True)
        self.assertNotEqual(run().returncode, 0)
        (copy / "app" / "calc.py").write_text(F.FIXED)
        self.assertEqual(run().returncode, 0)

    def test_it_holds_nothing_personal_and_nothing_that_looks_like_a_secret(self):
        from par.core.policy import is_sensitive_path
        from par.core.redact import redact_text
        root = self.create().entry.path
        for p in root.rglob("*"):
            if ".git" in p.relative_to(root).parts or not p.is_file():
                continue
            self.assertFalse(is_sensitive_path(p.relative_to(root)), p)
            text = p.read_text()
            self.assertEqual(redact_text(text), text, p)
            self.assertNotIn(str(self.home), text)

    def test_it_is_registered_as_writable_and_the_loader_agrees(self):
        self.create()
        text = projects_file(self.data).read_text()
        self.assertIn("fixture = true", text)
        self.assertIn("write_enabled = true", text)
        reg = self.registry()
        self.assertEqual(reg.issues, ())
        entry = reg.projects["fixture"]
        self.assertEqual((entry.fixture, entry.write_enabled, entry.vcs), (True, True, "git"))

    def test_a_write_task_may_start_here_and_only_here(self):
        entry = self.create().entry
        self.assertTrue(HarnessContextAdapter(entry).write_readiness().allowed)
        real = make_harness_project(self.home / "Documents" / "Real")
        subprocess.run(["git", "init", "-q", "-b", "main", str(real)], check=True, capture_output=True)
        entry = add_project(self.data, "real", real, vcs="git", home=self.home)
        self.assertFalse(entry.write_enabled)
        self.assertIn("WRITE_DISABLED", {b.code for b in HarnessContextAdapter(entry).write_readiness().blockers})

    def test_creating_changes_nothing_outside_the_data_directory(self):
        before = {p: p.stat().st_mtime_ns for p in (self.home / "Documents").rglob("*")}
        self.create()
        self.assertEqual({p: p.stat().st_mtime_ns for p in (self.home / "Documents").rglob("*")}, before)
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), ["config", "fixtures"])
        self.assertEqual([p.name for p in (self.data / "fixtures").iterdir()], ["fixture"])             # no scratch left behind

    def test_two_fixtures_can_exist(self):
        self.create("fixture")
        self.create("second_one")
        self.assertEqual(sorted(self.registry().projects), ["fixture", "second_one"])

    def test_refusals(self):
        self.create()
        for bad in ("Fixture", "1abc", "a-b", "", "x" * 33, "../escape", "a/b"):
            with self.assertRaises(PolicyViolation, msg=bad):
                self.create(bad)
        with self.assertRaises(PolicyViolation):
            self.create("fixture")                                                                     # already registered
        (self.data / "fixtures" / "taken").mkdir()
        with self.assertRaises(PolicyViolation):
            self.create("taken")                                                                       # a folder is already there
        with self.assertRaises(PolicyViolation):
            F.create_fixture(self.base / "no_such_data_dir", "fixture", home=self.home)
        self.assertEqual(sorted(self.registry().projects), ["fixture"])

    def test_a_symlinked_fixtures_folder_is_refused(self):
        outside = self.base / "elsewhere"
        outside.mkdir()
        os.symlink(outside, self.data / "fixtures")
        with self.assertRaises(PolicyViolation):
            self.create()
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_failed_registration_says_so_and_leaves_no_scratch(self):
        with mock.patch.object(F, "add_project", side_effect=PolicyViolation("disk full")):
            with self.assertRaises(PolicyViolation) as ctx:
                self.create()
        self.assertIn("created but not registered", str(ctx.exception))
        self.assertEqual([p.name for p in (self.data / "fixtures").iterdir()], ["fixture"])             # the folder stays (nothing is deleted)
        self.assertNotIn("fixture", self.registry().projects)


class NothingElseCanPassForOne(Base):
    """projects.toml is a file a person (or a mistake) can edit: the loader must not take its word."""

    def entry(self, pid, path, **kw):
        extra = "".join(f"{k} = {json.dumps(v)}\n" if not isinstance(v, bool) else f"{k} = {str(v).lower()}\n" for k, v in kw.items())
        return f'[projects.{pid}]\npath = {json.dumps(str(path))}\n{extra}'

    def test_a_real_project_cannot_be_made_writable_by_a_bare_switch_or_a_fake_fixture_flag(self):
        real = make_harness_project(self.home / "Documents" / "Real")
        for kw in ({"write_enabled": True}, {"write_enabled": True, "fixture": False}, {"fixture": True, "write_enabled": True},
                   {"fixture": True}, {"write_enabled": "true"}, {"write_enabled": 1}, {"fixture": "yes", "write_enabled": True}):
            errs = self.errors(self.entry("real", real, **kw))
            self.assertTrue(errs, kw)
            projects, _ = C.parse_projects_text("schema_version = 1\n" + self.entry("real", real, **kw), self.home, self.data / "fixtures")
            self.assertNotIn("real", projects, f"{kw}: an entry that asks for too much is dropped whole")

    def test_the_message_points_the_way(self):
        real = make_harness_project(self.home / "Documents" / "Real")
        self.assertIn("par project enable-write", self.errors(self.entry("real", real, write_enabled=True))["projects.real.write_approved"])

    def test_a_genuine_fixture_is_accepted_but_not_when_anything_about_it_is_off(self):
        root = self.create().entry.path
        self.assertEqual(self.errors(self.entry("fixture", root, vcs="git", fixture=True, write_enabled=True)), {})
        marker = root / C.FIXTURE_MARKER
        good = marker.read_text()
        cases = {
            "no marker": lambda: marker.unlink(),
            "wrong id": lambda: marker.write_text(json.dumps({**json.loads(good), "id": "other"})),
            "wrong kind": lambda: marker.write_text(json.dumps({**json.loads(good), "kind": "real-project"})),
            "wrong schema": lambda: marker.write_text(json.dumps({**json.loads(good), "schema_version": 2})),
            "not json": lambda: marker.write_text("{nope"),
            "not an object": lambda: marker.write_text("[1, 2]"),
            "too big": lambda: marker.write_text(good + " " * 5000),
        }
        for name, spoil in cases.items():
            marker.write_text(good)
            spoil()
            errs = self.errors(self.entry("fixture", root, fixture=True, write_enabled=True))
            self.assertIn("projects.fixture.fixture", errs, name)
            marker.write_text(good)
        self.assertEqual(self.errors(self.entry("fixture", root, fixture=True, write_enabled=True)), {})

    def test_the_marker_must_be_a_plain_file(self):
        root = self.create().entry.path
        real_marker = self.base / "real_marker.json"
        shutil.move(root / C.FIXTURE_MARKER, real_marker)
        os.symlink(real_marker, root / C.FIXTURE_MARKER)
        self.assertIn("projects.fixture.fixture", self.errors(self.entry("fixture", root, fixture=True, write_enabled=True)))

    def test_a_marker_copied_into_a_real_project_does_not_make_it_writable(self):
        self.create()
        real = make_harness_project(self.home / "Documents" / "Real")
        shutil.copy(self.data / "fixtures" / "fixture" / C.FIXTURE_MARKER, real / C.FIXTURE_MARKER)
        errs = self.errors(self.entry("real", real, fixture=True, write_enabled=True))
        self.assertIn("direct child", errs["projects.real.fixture"])

    def test_the_fixtures_folder_is_not_a_back_door_for_real_projects(self):
        self.create()
        real = make_harness_project(self.home / "Documents" / "Real")
        # a symlink inside fixtures/ that points at a real project
        os.symlink(real, self.data / "fixtures" / "real")
        shutil.copy(self.data / "fixtures" / "fixture" / C.FIXTURE_MARKER, real / C.FIXTURE_MARKER)
        (real / C.FIXTURE_MARKER).write_text(json.dumps({"schema_version": 1, "kind": "par-fixture", "id": "real"}))
        errs = self.errors(self.entry("real", self.data / "fixtures" / "real", fixture=True, write_enabled=True))
        self.assertIn("projects.real.fixture", errs)
        # a folder nested one level deeper, and one whose name differs from its id
        nested = self.data / "fixtures" / "fixture" / "inner"
        nested.mkdir()
        shutil.copy(self.data / "fixtures" / "fixture" / C.FIXTURE_MARKER, nested / C.FIXTURE_MARKER)
        self.assertIn("projects.inner.fixture", self.errors(self.entry("inner", nested, fixture=True, write_enabled=True)))
        self.assertIn("projects.other.fixture", self.errors(self.entry("other", self.data / "fixtures" / "fixture", fixture=True, write_enabled=True)))

    def test_a_dotdot_path_into_the_fixtures_folder_is_normalised_not_trusted(self):
        root = self.create().entry.path
        tricky = self.data / "fixtures" / "fixture" / ".." / "fixture"
        self.assertEqual(self.errors(self.entry("fixture", tricky, fixture=True, write_enabled=True)), {})   # resolves to the same genuine folder
        away = self.data / "fixtures" / ".." / ".." / "Documents"
        self.assertTrue(self.errors(self.entry("fixture", away, fixture=True, write_enabled=True)))

    def test_outside_a_data_directory_nothing_is_ever_a_fixture(self):
        root = self.create().entry.path
        _, issues = C.parse_projects_text("schema_version = 1\n" + self.entry("fixture", root, fixture=True, write_enabled=True), self.home)
        self.assertTrue([i for i in issues if i.severity == "error"])                                          # no fixtures_root given
        copied = self.base / "other_place" / "config"
        copied.mkdir(parents=True)
        (copied / "projects.toml").write_text("schema_version = 1\n" + self.entry("fixture", root, fixture=True, write_enabled=True))
        pf = C.load_projects_file(copied / "projects.toml", home=self.home)
        self.assertEqual(pf.projects, {})                                                                      # that data directory has no such fixture
        elsewhere = self.base / "plain.toml"
        elsewhere.write_text("schema_version = 1\n" + self.entry("fixture", root, fixture=True, write_enabled=True))
        self.assertEqual(C.load_projects_file(elsewhere, home=self.home).projects, {})                         # not inside <data>/config/

    def test_registering_a_real_project_always_stays_read_only(self):
        real = make_harness_project(self.home / "Documents" / "Real")
        self.assertFalse(add_project(self.data, "real", real, home=self.home).write_enabled)
        inside = self.create().entry.path
        with self.assertRaises(PolicyViolation):
            add_project(self.data, "again", inside, home=self.home)                                            # the same folder cannot be a second project
        duplicate = self.data / "fixtures" / "copy2"
        shutil.copytree(inside, duplicate)
        again = add_project(self.data, "copy2", duplicate, home=self.home)                                     # the normal path never grants writes
        self.assertEqual((again.write_enabled, again.fixture), (False, False))


class KernelUse(Base):
    def kernel(self):
        return Kernel(self.data, config=sample_config(), projects=self.registry().projects, adapters=AdapterRegistry(), home=self.home)

    def test_the_kernel_takes_a_write_task_for_a_fixture_and_refuses_one_for_a_real_project(self):
        self.create()
        real = make_harness_project(self.home / "Documents" / "Real")
        subprocess.run(["git", "init", "-q", "-b", "main", str(real)], check=True, capture_output=True)
        add_project(self.data, "real", real, vcs="git", home=self.home)
        kernel = self.kernel()
        try:
            res = kernel.submit(sample_contract(project_id="fixture", idempotency_key="k1"))
            self.assertEqual(res.status, "PERMISSION_WAIT")                                                  # still needs a person's approval
            with self.assertRaises(Refused) as ctx:
                kernel.submit(sample_contract(project_id="real", idempotency_key="k2"))
            self.assertTrue(any("WRITE_DISABLED" in p for p in ctx.exception.problems))
        finally:
            kernel.close()


class Cli(Base):
    def test_create_list_and_a_second_try(self):
        code, out, err = run_cli("fixture", "create", "--data-dir", str(self.data))
        self.assertEqual(code, 0, err)
        self.assertIn("created fixture project fixture", out)
        self.assertIn("only projects the Runtime may write to", out)
        code, out, _ = run_cli("project", "list", "--data-dir", str(self.data))
        self.assertEqual(code, 0)
        self.assertIn("writes: ALLOWED (fixture)", out)
        code, out, err = run_cli("fixture", "create", "--data-dir", str(self.data))
        self.assertEqual(code, 1)
        self.assertIn("already registered", err)

    def test_a_custom_id_and_a_missing_data_directory(self):
        self.assertEqual(run_cli("fixture", "create", "playground", "--data-dir", str(self.data))[0], 0)
        self.assertIn("playground", load_registry(self.data, home=self.home).projects)
        code, _, err = run_cli("fixture", "create", "--data-dir", str(self.base / "nope"))
        self.assertEqual(code, 1)
        self.assertIn("par init", err)

    def test_doctor_reports_the_fixture_without_failing(self):
        self.create()
        code, out, _ = run_cli("doctor", "--data-dir", str(self.data))
        self.assertIn("fixture project(s) fixture may be written", out)

    def test_project_show_says_writes_are_allowed_for_the_fixture(self):
        self.create()
        code, out, _ = run_cli("project", "show", "fixture", "--data-dir", str(self.data))
        self.assertEqual(code, 0)
        self.assertIn("Writes    ALLOWED", out)


if __name__ == "__main__":
    unittest.main()
