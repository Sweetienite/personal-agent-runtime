"""tests/unit/test_ops_land_task.py: exercises ops/land_task.py against a disposable git repo and
faked `par` responses. Nothing here touches the real par_runtime repository."""
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.world import git_env, run_git

OPS = Path(__file__).resolve().parents[2] / "ops" / "land_task.py"
spec = importlib.util.spec_from_file_location("land_task", OPS)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

TASK_ID = "par_runtime-0099"

GOOD_DIFF = (
    "diff --git a/a.txt b/a.txt\n"
    "--- a/a.txt\n"
    "+++ b/a.txt\n"
    "@@ -1 +1,2 @@\n"
    " one\n"
    "+two\n"
)

BAD_DIFF = (
    "diff --git a/a.txt b/a.txt\n"
    "--- a/a.txt\n"
    "+++ b/a.txt\n"
    "@@ -1 +1,2 @@\n"
    " this line is not in a.txt\n"
    "+two\n"
)


def fake_par(status, diff_text, close_ok=True):
    """A `run` stand-in for the `par` CLI calls this script makes: `task status --json`,
    `task diff`, `task close`."""

    def run(cmd, **kwargs):
        if "status" in cmd and "--json" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(status), stderr="")
        if "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=diff_text, stderr="")
        if "close" in cmd:
            if close_ok:
                return subprocess.CompletedProcess(cmd, 0, stdout="close: ok\n", stderr="")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="close refused: already closed")
        raise AssertionError(f"unexpected par call: {cmd}")

    return run


def fake_tests(returncode, output="Ran 3 tests in 0.01s\n\nOK\n"):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=output, stderr="")

    return run


def never_called(name):
    def run(*args, **kwargs):
        raise AssertionError(f"{name} must not run here")

    return run


class Base(unittest.TestCase):
    """A throwaway one-commit git repo plus the real-git `git_run` this script would be given."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.repo = base / "repo"
        self.home = base / "home"
        self.repo.mkdir()
        self.home.mkdir()
        (self.repo / "a.txt").write_text("one\n")
        run_git(self.repo.parent, self.home, "init", "-q", "-b", "main", str(self.repo))
        run_git(self.repo, self.home, "add", "-A")
        run_git(self.repo, self.home, "commit", "-q", "-m", "init")
        self.base_commit = self._git("rev-parse", "HEAD").stdout.strip()
        self.git_run = lambda cmd, **kwargs: subprocess.run(cmd, env=git_env(self.home), **kwargs)
        self.status = {
            "status": "ACCEPTED",
            "title": "add a line",
            "worker_requested": "agy_flash",
            "pinned_worker": None,
            "base": {"commit": self.base_commit},
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, env=git_env(self.home))

    def head(self):
        return self._git("rev-parse", "HEAD").stdout.strip()

    def porcelain(self):
        return self._git("status", "--porcelain").stdout

    def commit_message(self):
        return self._git("log", "-1", "--format=%B").stdout


class Land(Base):
    def test_dry_run_reports_and_changes_nothing(self):
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=False,
            par_run=fake_par(self.status, GOOD_DIFF), git_run=self.git_run,
            test_run=never_called("the test suite"), out=out.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.head(), self.base_commit)
        self.assertEqual(self.porcelain(), "")
        self.assertTrue(any("试运行" in line for line in out), out)

    def test_happy_path_applies_tests_and_commits(self):
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=True,
            par_run=fake_par(self.status, GOOD_DIFF), git_run=self.git_run,
            test_run=fake_tests(0), out=out.append,
        )
        self.assertEqual(code, 0)
        self.assertNotEqual(self.head(), self.base_commit)
        self.assertEqual(self.porcelain(), "")
        self.assertEqual((self.repo / "a.txt").read_text(), "one\ntwo\n")
        message = self.commit_message()
        self.assertIn(TASK_ID, message)
        self.assertIn("agy_flash", message)
        self.assertTrue(any("已提交" in line for line in out), out)
        self.assertTrue(any("已 close" in line for line in out), out)

    def test_close_failure_is_reported_separately_from_the_commit(self):
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=True,
            par_run=fake_par(self.status, GOOD_DIFF, close_ok=False), git_run=self.git_run,
            test_run=fake_tests(0), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertNotEqual(self.head(), self.base_commit, "the commit must stand even though close failed")
        self.assertEqual((self.repo / "a.txt").read_text(), "one\ntwo\n")
        self.assertTrue(any("提交已经成功" in line and "close" in line for line in out), out)

    def test_not_accepted_refuses_and_changes_nothing(self):
        status = dict(self.status, status="REVIEW_REQUIRED")
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=True,
            par_run=fake_par(status, GOOD_DIFF), git_run=self.git_run,
            test_run=never_called("the test suite"), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.head(), self.base_commit)
        self.assertTrue(any("REVIEW_REQUIRED" in line for line in out), out)

    def test_base_commit_mismatch_refuses_and_changes_nothing(self):
        status = dict(self.status, base={"commit": "0" * 40})
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=True,
            par_run=fake_par(status, GOOD_DIFF), git_run=self.git_run,
            test_run=never_called("the test suite"), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.head(), self.base_commit)
        self.assertEqual(self.porcelain(), "")
        self.assertTrue(any("不一致" in line for line in out), out)

    def test_dirty_working_tree_refuses_and_changes_nothing(self):
        (self.repo / "untracked.txt").write_text("x\n")
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=True,
            par_run=fake_par(self.status, GOOD_DIFF), git_run=self.git_run,
            test_run=never_called("the test suite"), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.head(), self.base_commit)
        self.assertTrue((self.repo / "untracked.txt").exists())
        self.assertTrue(any("不干净" in line for line in out), out)

    def test_patch_that_does_not_apply_refuses_and_changes_nothing(self):
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=True,
            par_run=fake_par(self.status, BAD_DIFF), git_run=self.git_run,
            test_run=never_called("the test suite"), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.head(), self.base_commit)
        self.assertEqual(self.porcelain(), "")
        self.assertTrue(any("套不上" in line for line in out), out)

    def test_failing_tests_revert_the_apply_and_commit_nothing(self):
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=True,
            par_run=fake_par(self.status, GOOD_DIFF), git_run=self.git_run,
            test_run=fake_tests(1, "Ran 3 tests in 0.01s\n\nFAILED (failures=1)\n"), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.head(), self.base_commit)
        self.assertEqual(self.porcelain(), "")
        self.assertEqual((self.repo / "a.txt").read_text(), "one\n")
        self.assertTrue(any("撤销" in line for line in out), out)

    def test_no_diff_content_refuses_and_changes_nothing(self):
        out = []
        code = helper.land(
            TASK_ID, cwd=self.repo, apply_flag=True,
            par_run=fake_par(self.status, ""), git_run=self.git_run,
            test_run=never_called("the test suite"), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.head(), self.base_commit)
        self.assertTrue(any("没有补丁内容" in line for line in out), out)


if __name__ == "__main__":
    unittest.main()
