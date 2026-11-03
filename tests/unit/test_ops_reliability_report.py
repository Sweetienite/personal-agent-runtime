"""tests/unit/test_ops_reliability_report.py: exercises ops/reliability_report.py against faked
`par` responses - no real ledger, no disk fixture needed (this script never touches the data
directory's files directly, only the `par` CLI, unlike test_ops_cleanup_tasks.py)."""
import contextlib
import importlib.util
import io
import json
import subprocess
import unittest
import unittest.mock
from pathlib import Path

OPS = Path(__file__).resolve().parents[2] / "ops" / "reliability_report.py"
spec = importlib.util.spec_from_file_location("reliability_report", OPS)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def attempt(n, end_kind, *, worker_id="agy_flash", failure_class=None):
    return {"n": n, "worker_id": worker_id, "provider_id": "antigravity", "state": "ENDED",
           "end_kind": end_kind, "failure_class": failure_class}


def fake_par(tasks, statuses):
    """`tasks`: the `par task list --json` rows. `statuses`: task_id -> full `par task status --json` dict."""

    def run(cmd, **kwargs):
        if "list" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(tasks), stderr="")
        if "status" in cmd:
            task_id = cmd[cmd.index("status") + 1]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(statuses[task_id]), stderr="")
        raise AssertionError(f"unexpected par call: {cmd}")

    return run


class Gather(unittest.TestCase):
    def test_a_project_filter_only_keeps_matching_rows(self):
        tasks = [{"task_id": "a-1", "project_id": "a", "risk": "R1"}, {"task_id": "b-1", "project_id": "b", "risk": "R1"}]
        statuses = {"a-1": {"status": "CLOSED", "attempts": []}, "b-1": {"status": "CLOSED", "attempts": []}}
        out = helper.gather(data_dir=Path("/x"), project_id="a", run=fake_par(tasks, statuses))
        self.assertEqual([t["task_id"] for t in out], ["a-1"])

    def test_a_failing_par_call_is_refused_not_raised_raw(self):
        def run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        with self.assertRaises(helper.Refused):
            helper.gather(data_dir=Path("/x"), project_id=None, run=run)


class Report(unittest.TestCase):
    def ask(self, tasks):
        lines = []
        helper.report(tasks, out=lines.append)
        return "\n".join(lines)

    def test_every_task_succeeding_first_try_says_so_and_skips_the_retry_section(self):
        tasks = [{"task_id": "t-1", "status": "CLOSED", "attempts": [attempt(1, "COMPLETED")]},
                 {"task_id": "t-2", "status": "ACCEPTED", "attempts": [attempt(1, "COMPLETED")]}]
        text = self.ask(tasks)
        self.assertIn("一次就成（end_kind=COMPLETED）：2/2", text)
        self.assertIn("全部一次就成", text)

    def test_a_first_failure_that_eventually_succeeds_is_counted_in_both_sections(self):
        tasks = [{"task_id": "t-1", "status": "CLOSED",
                 "attempts": [attempt(1, "FAILED", failure_class="EXECUTION_FAILED"), attempt(2, "COMPLETED")]}]
        text = self.ask(tasks)
        self.assertIn("一次就成（end_kind=COMPLETED）：0/1", text)
        self.assertIn("第一次没成、原因 EXECUTION_FAILED：1 个任务", text)
        self.assertIn("最终进了 ACCEPTED/CLOSED：1/1", text)

    def test_a_first_failure_that_never_recovers_is_counted_as_still_stuck(self):
        tasks = [{"task_id": "t-1", "status": "FAILED", "attempts": [attempt(1, "FAILED", failure_class="UNKNOWN")]},
                 {"task_id": "t-2", "status": "CANCELED", "attempts": [attempt(1, "INTERRUPTED")]}]
        text = self.ask(tasks)
        self.assertIn("最终进了 ACCEPTED/CLOSED：0/2", text)
        self.assertIn("最终状态是 FAILED：1 个任务", text)
        self.assertIn("最终状态是 CANCELED：1 个任务", text)

    def test_per_worker_counts_use_the_attempts_own_worker_id_not_the_task_level_one(self):
        tasks = [{"task_id": "t-1", "status": "CLOSED",
                 "attempts": [attempt(1, "FAILED", worker_id="agy_flash_high"), attempt(2, "COMPLETED", worker_id="agy_flash")]}]
        text = self.ask(tasks)
        self.assertIn("agy_flash_high：0/1 次 attempt 成功", text)
        self.assertIn("agy_flash：1/1 次 attempt 成功", text)

    def test_a_missing_worker_id_is_labelled_not_dropped(self):
        tasks = [{"task_id": "t-1", "status": "CLOSED", "attempts": [attempt(1, "COMPLETED", worker_id=None)]}]
        self.assertIn("(未知)：1/1", self.ask(tasks))

    def test_the_attempt_count_distribution_buckets_by_how_many_attempts_each_task_took(self):
        tasks = [{"task_id": "t-1", "status": "CLOSED", "attempts": [attempt(1, "COMPLETED")]},
                 {"task_id": "t-2", "status": "CLOSED",
                 "attempts": [attempt(1, "FAILED"), attempt(2, "FAILED"), attempt(3, "COMPLETED")]}]
        text = self.ask(tasks)
        self.assertIn("1 次：1 个任务", text)
        self.assertIn("3 次：1 个任务", text)

    def test_a_task_with_zero_attempts_counts_toward_the_total_but_nothing_else(self):
        tasks = [{"task_id": "t-1", "status": "PERMISSION_WAIT", "attempts": []}]
        helper.report(tasks, out=lambda line: None)  # must not raise (no attempts[0] to index)


class Main(unittest.TestCase):
    def test_main_reports_nothing_and_exits_zero_when_there_are_no_tasks(self):
        tasks = []
        statuses = {}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with unittest.mock.patch("subprocess.run", fake_par(tasks, statuses)):
                code = helper.main(["--data-dir", "/x"])
        self.assertEqual(code, 0)
        self.assertIn("没有任务", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
