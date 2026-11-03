"""Two attempts at once (the plan's T17 and T18): the profile asks for 1 or 2, the same project is never worked on twice at once,
a third attempt is refused, `run_next` does not stop at a project that is busy, two writers get separate work areas and results (T17),
and two tasks on the same file cannot overwrite each other (T18). The Workers are scripted: nothing real runs."""
import unittest
from pathlib import Path

from par.config import ProjectEntry
from par.core.kernel import Busy
from par.providers.fake import ScenarioSequence, ops, scenarios
from tests.kernel_fixture import FIXED_CALC, KernelWorld, fast_settings, r1_contract
from par.mcp.tools import Tools
from tests.unit.test_mcp_tools import payload_of


SWAPPED_CALC = "def add(a, b):\n    return b + a\n"


def slow(seconds=2.0):
    return scenarios.custom("slow", ops.message("hi"), ops.sleep(seconds), *ops.success_tail())


def slow_fix(content, seconds=1.5):
    return scenarios.custom("slow_fix", ops.write("app/calc.py", content), ops.sleep(seconds), *ops.success_tail())


class Capacity(KernelWorld):
    default_files = None

    def test_the_profile_decides_how_many_attempts_may_run_and_tests_may_fix_a_number(self):
        self.assertEqual(self.kernel.capacity(), 1)                                       # the default profile: one
        self.assertEqual(self.new_kernel(settings=fast_settings(max_parallel_workers=3)).capacity(), 3)
        self.parallel = 2
        self.assertEqual(self.new_kernel(settings=fast_settings()).capacity(), 2)         # settings left alone: the profile's number

    def test_with_one_worker_allowed_a_second_project_still_waits(self):
        second = self.make_project("second")
        self.projects["second"] = ProjectEntry("second", second, "git", True)
        self.codex = ScenarioSequence(slow(), slow())
        self.kernel = self.new_kernel()
        first = self.queued(r1_contract())
        other_task = self.queued(r1_contract(project_id="second"))
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(first))
        self.assertTrue(self.wait_for(lambda: other.describe(first)["status"] == "RUNNING"))
        with self.assertRaises(Busy) as ctx:
            other.run(other_task)
        self.assertEqual(ctx.exception.code, "capacity")
        self.assertIn("allows 1", str(ctx.exception))
        t.join(30)


class TwoAtOnce(KernelWorld):
    """Two attempts in two different projects run at the same time; the same project never does, whatever the capacity."""
    default_files = None
    parallel = 2

    def setUp(self):
        super().setUp()
        for name in ("second", "third"):
            self.projects[name] = ProjectEntry(name, self.make_project(name), "git", True)

    def test_two_projects_run_together_and_a_third_attempt_is_refused(self):
        self.codex = ScenarioSequence(slow(2.0), slow(2.0), slow(0.2))
        self.kernel = self.new_kernel()
        a = self.queued(r1_contract())
        b = self.queued(r1_contract(project_id="second"))
        c = self.queued(r1_contract(project_id="third"))
        watcher = self.new_kernel()
        ta, tb = self.in_thread(lambda: self.kernel.run(a)), self.in_thread(lambda: self.new_kernel().run(b))
        self.assertTrue(self.wait_for(lambda: watcher.describe(a)["status"] == "RUNNING" and watcher.describe(b)["status"] == "RUNNING"))
        self.assertEqual(len(watcher.store.unfinished_attempts()), 2)
        self.assertEqual(sorted(lock["project_id"] for lock in watcher.store.locks()), ["fixture", "second"])
        with self.assertRaises(Busy) as ctx:
            watcher.run(c)
        self.assertEqual(ctx.exception.code, "capacity")
        self.assertIn("allows 2", str(ctx.exception))
        self.assertEqual((watcher.describe(c)["status"], watcher.store.attempts(c)), ("QUEUED", []))
        ta.join(30)
        tb.join(30)
        self.assertEqual([t.box.get("value").status for t in (ta, tb)], ["REVIEW_REQUIRED", "REVIEW_REQUIRED"])
        self.assertEqual(watcher.store.locks(), [])
        self.assertEqual(watcher.run(c).status, "REVIEW_REQUIRED")                         # room again: the third one runs

    def test_the_same_project_is_never_worked_on_twice_even_with_a_free_slot(self):
        self.codex = ScenarioSequence(slow(2.0), slow(0.2))
        self.kernel = self.new_kernel()
        first = self.queued(r1_contract())
        second = self.queued(r1_contract())
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(first))
        self.assertTrue(self.wait_for(lambda: other.describe(first)["status"] == "RUNNING"))
        self.assertEqual(len(other.store.unfinished_attempts()), 1)                        # one slot is still free
        with self.assertRaises(Busy) as ctx:
            other.run(second)
        self.assertEqual(ctx.exception.code, "project_locked")
        t.join(30)

    def test_run_next_goes_past_a_busy_project_to_a_task_of_another_one(self):
        self.codex = ScenarioSequence(slow(2.0), slow(0.2))
        self.kernel = self.new_kernel()
        busy = self.queued(r1_contract())
        waiting_same = self.queued(r1_contract())                                          # older than the task below, same project as `busy`
        elsewhere = self.queued(r1_contract(project_id="second"))
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(busy))
        self.assertTrue(self.wait_for(lambda: other.describe(busy)["status"] == "RUNNING"))
        report = other.run_next()
        self.assertIsNotNone(report)
        self.assertEqual(report.task_id, elsewhere)                                        # not stuck on waiting_same
        self.assertEqual(other.describe(waiting_same)["status"], "QUEUED")
        t.join(30)

    def test_run_next_stops_when_there_is_no_capacity_left(self):
        self.codex = ScenarioSequence(slow(2.0), slow(2.0))
        self.kernel = self.new_kernel()
        a = self.queued(r1_contract())
        b = self.queued(r1_contract(project_id="second"))
        c = self.queued(r1_contract(project_id="third"))
        watcher = self.new_kernel()
        ta, tb = self.in_thread(lambda: self.kernel.run(a)), self.in_thread(lambda: self.new_kernel().run(b))
        self.assertTrue(self.wait_for(lambda: len(watcher.store.unfinished_attempts()) == 2))
        self.assertIsNone(watcher.run_next())
        self.assertEqual(watcher.describe(c)["status"], "QUEUED")
        ta.join(30)
        tb.join(30)


class TwoWriters(KernelWorld):
    parallel = 2

    def test_t17_two_write_tasks_run_together_each_in_its_own_workspace_with_its_own_diff_and_checks(self):
        """The plan's T17: two Workers change files at once (here in two projects: the same project is never worked on twice at once);
        the work areas are separate, and each result, diff and check can be looked at on its own."""
        self.projects["second"] = ProjectEntry("second", self.make_project("second"), "git", True)
        self.codex = ScenarioSequence(slow_fix(FIXED_CALC), slow_fix(SWAPPED_CALC))
        self.kernel = self.new_kernel()
        a = self.queued()
        b = self.queued(project_id="second")
        watcher = self.new_kernel()
        ta, tb = self.in_thread(lambda: self.kernel.run(a)), self.in_thread(lambda: self.new_kernel().run(b))
        self.assertTrue(self.wait_for(lambda: watcher.describe(a)["status"] == "RUNNING" and watcher.describe(b)["status"] == "RUNNING"))
        self.assertEqual(len(watcher.store.unfinished_attempts()), 2)                       # really at the same time
        ta.join(30)
        tb.join(30)
        self.assertEqual([t.box.get("value").status for t in (ta, tb)], ["REVIEW_REQUIRED", "REVIEW_REQUIRED"])
        self.assertNotEqual(self.attempt_dir(a) / "workspace", self.attempt_dir(b) / "workspace")
        written = {tid: (self.attempt_dir(tid) / "workspace" / "app" / "calc.py").read_text() for tid in (a, b)}
        self.assertEqual(sorted(written.values()), sorted([FIXED_CALC, SWAPPED_CALC]))     # each Worker's copy holds one of the two changes
        added = {FIXED_CALC: b"+    return a + b", SWAPPED_CALC: b"+    return b + a"}       # (which Worker got which script is up to the threads)
        for tid, other in ((a, b), (b, a)):
            patch = self.kernel.diff(tid)
            self.assertIn(added[written[tid]], patch)                                        # its own change is in its own diff...
            self.assertNotIn(added[written[other]], patch)                                   # ...and the other one's is not
        for tid in (a, b):                                                                  # every task has its own check result and log
            res = self.result(tid)
            self.assertEqual((res["tests"][0]["name"], res["tests"][0]["status"]), ("unit", "PASS"))
            self.assertEqual(res["diff_stat"]["files"], 1)
            self.assertTrue((self.attempt_dir(tid) / "check_unit.log").is_file())
        for name in ("fixture", "second"):                                                   # and neither real project was touched
            self.assertEqual(self.git(self.projects[name].path, "status", "--porcelain"), "")
            self.assertIn("return a - b", (Path(self.projects[name].path) / "app" / "calc.py").read_text())

    def test_t18_two_tasks_on_the_same_file_the_second_is_stopped_at_the_drift_gate_and_overwrites_nothing(self):
        """The plan's T18: two Workers on the same file. They cannot run at once (the project lock); after the first result has landed in the
        project (the person applied it), the second task, submitted against the old state, is stopped for a confirmation instead of
        overwriting what landed, and its patch is made against the confirmed state."""
        self.codex = ScenarioSequence(scenarios.success({"app/calc.py": FIXED_CALC}), scenarios.success({"app/calc.py": SWAPPED_CALC}))
        self.kernel = self.new_kernel()
        a = self.queued()
        b = self.queued()
        self.assertEqual(self.run_task(a).status, "REVIEW_REQUIRED")
        self.kernel.accept(a)
        patch_file = self.base / "a.patch"
        patch_file.write_bytes(self.kernel.diff(a))
        self.git(self.project_path, "apply", str(patch_file))                                # the person lands the first result...
        self.git(self.project_path, "add", "-A")
        self.git(self.project_path, "commit", "-q", "-m", "land the first task")
        landed = self.git(self.project_path, "rev-parse", "HEAD")
        report = self.run_task(b)                                                            # ...and the second task is not run silently
        self.assertEqual(report.status, "PERMISSION_WAIT")
        approval = self.kernel.describe(b)["pending_approvals"][0]
        self.assertEqual((approval["kind"], approval["payload"]["changed"]), ("BASE_DRIFT", ["head"]))
        self.assertEqual(self.kernel.store.attempts(b), [])                                  # no Worker was started
        self.assertEqual((self.project_path / "app" / "calc.py").read_text(), FIXED_CALC)    # what landed is untouched
        self.assertEqual(self.git(self.project_path, "status", "--porcelain"), "")
        self.kernel.approve(b)
        self.assertEqual(self.run_task(b).status, "REVIEW_REQUIRED")
        patch_b = self.kernel.diff(b)
        self.assertIn(b"-    return a + b", patch_b)                                         # made against the state that landed, not the old one
        self.assertIn(b"+    return b + a", patch_b)
        self.assertEqual(self.git(self.project_path, "rev-parse", "HEAD"), landed)           # nothing was applied or committed by the Runtime
        self.assertEqual((self.project_path / "app" / "calc.py").read_text(), FIXED_CALC)


class BridgeRespectsTheCapacity(KernelWorld):
    default_files = None

    def test_run_task_through_the_bridge_is_refused_when_every_slot_is_taken(self):
        self.codex = ScenarioSequence(slow(2.0), slow(0.2))
        self.kernel = self.new_kernel()
        tools = Tools(self.kernel)
        first = self.queued(r1_contract())
        second = self.queued(r1_contract())
        runner, watcher = self.new_kernel(), self.new_kernel()
        t = self.in_thread(lambda: runner.run(first))
        self.assertTrue(self.wait_for(lambda: watcher.describe(first)["status"] == "RUNNING"))
        answer = payload_of(tools.call("run_task", {"task_id": second}))
        self.assertIs(answer["started"], False)
        self.assertIn("1 attempt(s) are unfinished and this profile allows 1 at a time", answer["reason"])
        t.join(30)


if __name__ == "__main__":
    unittest.main()
