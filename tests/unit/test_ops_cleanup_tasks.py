"""tests/unit/test_ops_cleanup_tasks.py: exercises ops/cleanup_tasks.py against a disposable
directory tree standing in for a data directory, and faked `par` responses (this script never
touches git, so no git fixture is needed, unlike test_ops_land_task.py)."""
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import importlib.util

OPS = Path(__file__).resolve().parents[2] / "ops" / "cleanup_tasks.py"
spec = importlib.util.spec_from_file_location("cleanup_tasks", OPS)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def fake_par(tasks, status_overrides=None):
    """A `run` stand-in for `par task list --json` / `par task status <id> --json`."""
    status_overrides = status_overrides or {}

    def run(cmd, **kwargs):
        if "list" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(tasks), stderr="")
        if "status" in cmd:
            task_id = cmd[cmd.index("status") + 1]
            status = status_overrides.get(task_id) or next(
                (t["status"] for t in tasks if t["task_id"] == task_id), "UNKNOWN"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"task_id": task_id, "status": status}), stderr="")
        raise AssertionError(f"unexpected par call: {cmd}")

    return run


def make_task_dir(base, task_id, *, attempts=1, handoffs=1):
    """A task directory with the real on-disk shape: small evidence files plus the bulky,
    removable workspace/home/tmp per attempt and one or more hand-off export folders."""
    task_dir = base / "tasks" / task_id
    for n in range(1, attempts + 1):
        a = task_dir / "attempts" / str(n)
        (a / "workspace" / "src").mkdir(parents=True)
        (a / "workspace" / "src" / "file.py").write_text("x" * 1000)
        (a / "home" / ".config").mkdir(parents=True)
        (a / "home" / ".config" / "stuff").write_text("y" * 500)
        (a / "tmp").mkdir(parents=True)
        (a / "tmp" / "scratch").write_text("z" * 200)
        (a / "result.json").write_text('{"ok": true}')
        (a / "work.patch").write_text("diff --git a/x b/x\n")
        (a / "prompt.txt").write_text("prompt text")
        (a / "changes.json").write_text("{}")
        (a / "check_full_suite.out").write_text("Ran 3 tests\nOK\n")
        (a / "check_full_suite.err").write_text("")
    for n in range(1, handoffs + 1):
        h = task_dir / "handoff" / f"manual-{n}" / "workspace"
        h.mkdir(parents=True)
        (h / "file.txt").write_text("w" * 300)
        (task_dir / "handoff" / f"manual-{n}" / "README.md").write_text("readme")
    return task_dir


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "data"
        self.data_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def evidence_intact(self, task_id, attempt=1):
        a = self.data_dir / "tasks" / task_id / "attempts" / str(attempt)
        for name in ("result.json", "work.patch", "prompt.txt", "changes.json", "check_full_suite.out", "check_full_suite.err"):
            self.assertTrue((a / name).is_file(), f"{name} should survive cleanup")

    def bulk_gone(self, task_id, attempt=1):
        a = self.data_dir / "tasks" / task_id / "attempts" / str(attempt)
        for name in ("workspace", "home", "tmp"):
            self.assertFalse((a / name).exists(), f"{name} should be removed")
        self.assertFalse((self.data_dir / "tasks" / task_id / "handoff").exists())


class Cleanup(Base):
    def test_a_closed_task_loses_the_bulk_but_keeps_the_evidence(self):
        make_task_dir(self.data_dir, "par_runtime-0001")
        tasks = [{"task_id": "par_runtime-0001", "status": "CLOSED"}]
        out = []
        code = helper.cleanup([], data_dir=self.data_dir, apply_flag=True, par_run=fake_par(tasks), out=out.append)
        self.assertEqual(code, 0)
        self.evidence_intact("par_runtime-0001")
        self.bulk_gone("par_runtime-0001")
        self.assertTrue(any("已删除" in line for line in out), out)

    def test_a_failed_task_is_left_completely_untouched(self):
        make_task_dir(self.data_dir, "par_runtime-0002")
        tasks = [{"task_id": "par_runtime-0002", "status": "FAILED"}]
        code = helper.cleanup([], data_dir=self.data_dir, apply_flag=True, par_run=fake_par(tasks), out=lambda *_: None)
        self.assertEqual(code, 0)
        a = self.data_dir / "tasks" / "par_runtime-0002" / "attempts" / "1"
        self.assertTrue((a / "workspace").exists(), "a non-CLOSED task must not be touched")
        self.assertTrue((self.data_dir / "tasks" / "par_runtime-0002" / "handoff").exists())

    def test_an_accepted_task_is_also_left_untouched(self):
        make_task_dir(self.data_dir, "par_runtime-0003")
        tasks = [{"task_id": "par_runtime-0003", "status": "ACCEPTED"}]
        helper.cleanup([], data_dir=self.data_dir, apply_flag=True, par_run=fake_par(tasks), out=lambda *_: None)
        self.assertTrue((self.data_dir / "tasks" / "par_runtime-0003" / "attempts" / "1" / "workspace").exists())

    def test_dry_run_reports_and_deletes_nothing(self):
        make_task_dir(self.data_dir, "par_runtime-0004")
        tasks = [{"task_id": "par_runtime-0004", "status": "CLOSED"}]
        out = []
        code = helper.cleanup([], data_dir=self.data_dir, apply_flag=False, par_run=fake_par(tasks), out=out.append)
        self.assertEqual(code, 0)
        self.assertTrue((self.data_dir / "tasks" / "par_runtime-0004" / "attempts" / "1" / "workspace").exists())
        self.assertTrue(any("会释放" in line for line in out), out)
        self.assertTrue(any("--apply" in line for line in out), out)

    def test_an_explicit_task_id_scopes_cleanup_to_just_that_task(self):
        make_task_dir(self.data_dir, "par_runtime-0005")
        make_task_dir(self.data_dir, "par_runtime-0006")
        tasks = [
            {"task_id": "par_runtime-0005", "status": "CLOSED"},
            {"task_id": "par_runtime-0006", "status": "CLOSED"},
        ]
        code = helper.cleanup(
            ["par_runtime-0005"], data_dir=self.data_dir, apply_flag=True, par_run=fake_par(tasks), out=lambda *_: None
        )
        self.assertEqual(code, 0)
        self.bulk_gone("par_runtime-0005")
        self.assertTrue((self.data_dir / "tasks" / "par_runtime-0006" / "attempts" / "1" / "workspace").exists())

    def test_an_explicit_task_id_that_is_not_closed_refuses_and_deletes_nothing(self):
        make_task_dir(self.data_dir, "par_runtime-0007")
        tasks = [{"task_id": "par_runtime-0007", "status": "REVIEW_REQUIRED"}]
        out = []
        code = helper.cleanup(
            ["par_runtime-0007"], data_dir=self.data_dir, apply_flag=True, par_run=fake_par(tasks), out=out.append
        )
        self.assertEqual(code, 1)
        self.assertTrue((self.data_dir / "tasks" / "par_runtime-0007" / "attempts" / "1" / "workspace").exists())
        self.assertTrue(any("REVIEW_REQUIRED" in line for line in out), out)

    def test_several_attempts_and_handoffs_are_all_covered(self):
        make_task_dir(self.data_dir, "par_runtime-0008", attempts=3, handoffs=4)
        tasks = [{"task_id": "par_runtime-0008", "status": "CLOSED"}]
        helper.cleanup([], data_dir=self.data_dir, apply_flag=True, par_run=fake_par(tasks), out=lambda *_: None)
        for n in (1, 2, 3):
            self.evidence_intact("par_runtime-0008", attempt=n)
            self.bulk_gone("par_runtime-0008", attempt=n)

    def test_nothing_closed_is_a_clean_no_op(self):
        out = []
        code = helper.cleanup([], data_dir=self.data_dir, apply_flag=True, par_run=fake_par([]), out=out.append)
        self.assertEqual(code, 0)
        self.assertTrue(any("没有" in line for line in out), out)


if __name__ == "__main__":
    unittest.main()
