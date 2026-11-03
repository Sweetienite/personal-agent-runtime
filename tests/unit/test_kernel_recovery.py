import os
import signal
import threading
import time
import unittest
from pathlib import Path

from par.core.kernel import Busy, NotAllowed
from par.execution import subprocesses as sp
from par.providers.fake import ScenarioSequence, ScriptedProvider, ops, scenarios
from par.providers.registry import AdapterRegistry
from tests.kernel_fixture import FIX, FIXED_CALC, KernelWorld, fast_settings, r1_contract
from tests.world import sample_contract

LONG_WRITE = scenarios.custom("write then wait", ops.write("app/calc.py", FIXED_CALC), ops.message("halfway"), ops.sleep(60))


class Recovery(KernelWorld):
    """A Kernel that dies must leave a ledger the next Kernel can settle - without a second writer, without lies."""

    def start_and_crash(self, scenario=LONG_WRITE, *, contract=None, kill_worker=False, cancel_first=False):
        tid = self.queued(contract if contract is not None else sample_contract())
        # A Kernel that is about to be killed right after a cancel request must not get the chance to act on it first (it would stop
        # the Worker itself and the "crash with a cancel pending" situation would be gone): so it polls for cancellation only rarely.
        child = self.spawn_kernel(tid, scenario, settings={"cancel_check": 30.0} if cancel_first else None)
        peek = self.new_kernel()
        self.assertTrue(self.wait_for(lambda: peek.describe(tid)["status"] == "RUNNING"), "the child never reached RUNNING")
        self.assertTrue(self.wait_for(lambda: (peek.store.get_attempt(tid, 1) or {}).get("pgid")))
        att = peek.store.get_attempt(tid, 1)
        time.sleep(0.4)                                                    # let the worker write something
        if cancel_first:
            self.assertEqual(peek.cancel(tid)["status"], "CANCEL_REQUESTED")
        self.crash(child)
        if kill_worker:
            os.killpg(att["pgid"], signal.SIGKILL)
            self.assertTrue(self.wait_for(lambda: not sp.group_alive(att["pgid"])))
        return tid, att, peek

    def stale(self):
        time.sleep(self.settings.stale_after + 0.3)

    def test_a_live_kernel_is_left_alone(self):
        tid = self.queued(sample_contract())
        child = self.spawn_kernel(tid, LONG_WRITE)
        peek = self.new_kernel()
        self.assertTrue(self.wait_for(lambda: peek.describe(tid)["status"] == "RUNNING"))
        actions = peek.recover(tid)
        self.assertEqual([(a.action, "is alive" in a.detail) for a in actions], [("skipped", True)])
        self.assertEqual(peek.recover(), [])                               # the implicit sweep says nothing about live owners
        self.assertEqual(peek.describe(tid)["status"], "RUNNING")
        peek.cancel(tid)
        child.wait(timeout=30)

    def test_a_crashed_kernel_whose_worker_is_still_running_blocks_everyone_until_it_is_dealt_with(self):
        self.skip_orphans = True
        tid, att, peek = self.start_and_crash()
        self.stale()
        other = self.queued(r1_contract())
        with self.assertRaises(Busy) as ctx:
            peek.run(other)                                                # run() sweeps first, and finds a live orphan
        self.assertEqual(ctx.exception.code, "capacity")
        d = peek.describe(tid)
        self.assertEqual(d["status"], "RECOVERY_REQUIRED")
        self.assertEqual(d["lock"]["task_id"], tid)                        # the project stays locked: no second writer
        self.assertTrue(sp.group_alive(att["pgid"]))
        self.assertEqual(peek.store.get_attempt(tid, 1)["state"], "RUNNING")
        with self.assertRaises(NotAllowed):
            peek.retry(tid)                                                # not while an attempt is unfinished
        actions = peek.recover(tid)
        self.assertEqual(actions[0].action, "alive")
        self.assertIn("--terminate", actions[0].detail)
        self.assertTrue(sp.group_alive(att["pgid"]))                       # plain recovery never kills anything

    def test_recovery_with_terminate_stops_the_verified_orphan_and_keeps_its_partial_work(self):
        tid, att, peek = self.start_and_crash()
        self.stale()
        (action,) = peek.recover(tid, terminate=True)
        self.assertEqual(action.action, "terminated")
        self.assertFalse(sp.group_alive(att["pgid"]))
        d = peek.describe(tid)
        self.assertEqual((d["status"], d["lock"], d["attempts"][0]["end_kind"]), ("RECOVERY_REQUIRED", None, "ORPHANED"))
        res = peek.result(tid)
        self.assertEqual((res["outcome"], res["partial"], res["handoff_usable"]), ("orphaned", True, True))
        self.assertIn(b"return a + b", peek.diff(tid))                     # what it had written before the crash is not lost
        self.assertFalse((self.attempt_dir(tid) / "ref").exists())
        self.assertTrue((self.attempt_dir(tid) / "workspace").is_dir())
        self.assertEqual(peek.recover(tid), [])                            # recovery is idempotent
        self.codex = ScenarioSequence(scenarios.success(FIX))
        peek = self.new_kernel()
        peek.retry(tid)
        self.assertEqual(peek.run(tid).status, "REVIEW_REQUIRED")

    def test_recovery_rebuilds_its_scratch_copies_instead_of_trusting_what_a_crash_left(self):
        tid, att, peek = self.start_and_crash(kill_worker=True)
        d = self.attempt_dir(tid)
        (d / "ref").mkdir(exist_ok=True)                                   # a pristine copy cut short by the crash...
        (d / "ref" / "junk.txt").write_text("not a real checkout")
        (d / "verify").mkdir(exist_ok=True)                                # ...and a half-built verification tree
        (d / "verify" / "leftover.txt").write_text("x")
        self.stale()
        peek.recover(tid)
        res = peek.result(tid)
        self.assertEqual((res["partial"], res["boundary"]["violations"], res["diff_stat"]["files"]), (True, 0, 1))
        patch = peek.diff(tid)
        self.assertIn(b"return a + b", patch)
        self.assertNotIn(b"junk.txt", patch)
        self.assertNotIn(b"leftover.txt", patch)
        self.assertFalse((d / "ref").exists() or (d / "verify").exists())

    def test_a_crashed_kernel_with_a_dead_worker_is_settled_on_the_next_run(self):
        tid, att, peek = self.start_and_crash(kill_worker=True)
        self.stale()
        other = self.queued(r1_contract())
        self.codex = ScenarioSequence(scenarios.success())
        runner = self.new_kernel()
        self.assertEqual(runner.run(other).status, "REVIEW_REQUIRED")      # the sweep at the start of run() cleared the way
        d = runner.describe(tid)
        self.assertEqual((d["status"], d["attempts"][0]["end_kind"], d["lock"] is None or d["lock"]["task_id"] != tid), ("RECOVERY_REQUIRED", "ORPHANED", True))
        self.assertIn("ATTEMPT_ENDED", self.event_types(tid))

    def test_a_cancel_that_was_already_requested_finishes_as_canceled(self):
        tid, att, peek = self.start_and_crash(kill_worker=True, cancel_first=True)
        self.stale()
        peek.recover(tid)
        d = peek.describe(tid)
        self.assertEqual((d["status"], d["attempts"][0]["end_kind"]), ("CANCELED", "ORPHANED"))
        self.assertEqual(self.statuses(tid)[-2:], ["CANCEL_REQUESTED", "CANCELED"])

    def test_canceling_a_task_whose_kernel_is_gone_does_not_wait_for_anyone(self):
        tid, att, peek = self.start_and_crash(kill_worker=True)
        self.stale()
        self.assertEqual(peek.cancel(tid)["status"], "CANCELED")
        self.assertEqual(peek.store.locks(), [])

    def test_canceling_a_recovery_task_with_a_live_orphan_stops_it_first(self):
        tid, att, peek = self.start_and_crash()
        self.stale()
        peek.recover(tid)                                                  # -> RECOVERY_REQUIRED, orphan alive
        self.assertEqual(peek.cancel(tid)["status"], "CANCELED")
        self.assertFalse(sp.group_alive(att["pgid"]))
        self.assertEqual(peek.store.get_attempt(tid, 1)["end_kind"], "ORPHANED")

    def test_an_orphan_that_cannot_be_identified_is_never_killed(self):
        self.skip_orphans = True
        self.default_files = None
        tid = self.queued(r1_contract())
        stray_scn = scenarios.custom("leaves a stray", ops.stray(60, "stray.pid"), ops.message("hi"), ops.sleep(60))
        child = self.spawn_kernel(tid, stray_scn)
        peek = self.new_kernel()
        self.assertTrue(self.wait_for(lambda: peek.describe(tid)["status"] == "RUNNING"))
        att = self.wait_for(lambda: peek.store.get_attempt(tid, 1)["pgid"]) and peek.store.get_attempt(tid, 1)
        ws = Path(att["workspace_path"])
        self.assertTrue(self.wait_for(lambda: (ws / "stray.pid").exists()))
        self.crash(child)
        os.kill(att["pid"], signal.SIGKILL)                                # the leader dies too; the stray lives on in the group
        self.assertTrue(self.wait_for(lambda: not sp.pid_alive(att["pid"])))
        self.assertTrue(sp.group_alive(att["pgid"]))
        self.stale()
        (action,) = peek.recover(tid, terminate=True)
        self.assertEqual(action.action, "alive")
        self.assertIn("cannot be verified", action.detail)
        self.assertTrue(sp.group_alive(att["pgid"]))                       # recovery refused to shoot at something it cannot identify

    def test_an_attempt_that_never_got_as_far_as_a_process_is_settled(self):
        self.skip_orphans = False
        tid = self.queued(r1_contract())
        store = self.kernel.store
        with store.tx():
            store.insert_attempt(tid, 1, worker_id="w_code", provider_id="codex", model="m-code", kernel_id="ghost-kernel",
                                 config={"route": {"worker": "w_code", "provider": "codex", "model": "m-code"}, "risk": "R1"})
            store.acquire_lock("fixture", tid, 1, "ghost-kernel")
            store.set_status(tid, "STARTING", reason="attempt 1", attempt_n=1, columns={"attempt_count": 1})
        store.conn.execute("UPDATE attempts SET heartbeat_at = '2000-01-01T00:00:00.000Z' WHERE task_id = ?", (tid,))
        (action,) = self.kernel.recover()
        self.assertEqual(action.action, "orphaned")
        d = self.kernel.describe(tid)
        self.assertEqual((d["status"], d["lock"]), ("RECOVERY_REQUIRED", None))
        self.kernel.retry(tid)
        self.codex = ScenarioSequence(scenarios.success())
        self.kernel = self.new_kernel()
        self.assertEqual(self.kernel.run(tid).status, "REVIEW_REQUIRED")


class Interruption(KernelWorld):
    default_files = None

    def test_ctrl_c_before_the_worker_starts_leaves_a_clean_record(self):
        class Interrupting(ScriptedProvider):
            def build_argv(self, request):
                raise KeyboardInterrupt
        reg = AdapterRegistry()
        reg.register("codex", lambda: Interrupting(scenarios.success(), "codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        self.kernel = self.new_kernel(adapters=reg)
        tid = self.queued(r1_contract())
        with self.assertRaises(KeyboardInterrupt):
            self.run_task(tid)
        d = self.kernel.describe(tid)
        self.assertEqual((d["status"], d["lock"], d["attempts"][0]["end_kind"], d["attempts"][0]["stage"]), ("INTERRUPTED", None, "INTERRUPTED", "kernel"))
        self.kernel.retry(tid)
        self.assertEqual(self.status(tid), "QUEUED")

    def test_ctrl_c_while_the_worker_runs_stops_the_worker_and_records_it(self):
        # A suite started from a background job inherits an IGNORED SIGINT, and then os.kill(SIGINT) does nothing:
        # make sure the real Ctrl-C handler is installed, whoever launched the tests.
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        self.codex = ScenarioSequence(scenarios.custom("long", ops.message("working"), ops.sleep(30)))
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract())
        peek = self.new_kernel()                                           # one observer, made up front (not a connection per poll)
        fired = {}

        def interrupt_when_running():
            if self.wait_for(lambda: (peek.store.get_attempt(tid, 1) or {}).get("pgid"), timeout=45):
                fired["pgid"] = peek.store.get_attempt(tid, 1)["pgid"]
                time.sleep(0.3)
                os.kill(os.getpid(), signal.SIGINT)

        t = threading.Thread(target=interrupt_when_running, daemon=True)
        t.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.run_task(tid)
        finally:
            t.join(5)
        self.assertIn("pgid", fired, "the observer never saw the worker start")
        self.assertFalse(sp.group_alive(fired["pgid"]))
        d = self.kernel.describe(tid)
        self.assertEqual((d["status"], d["lock"], d["attempts"][0]["end_kind"]), ("INTERRUPTED", None, "INTERRUPTED"))


class CrashAtAnyMoment(KernelWorld):
    """Kill the Kernel at different moments of one lifecycle; afterwards the ledger must always be coherent."""

    def test_whenever_the_kernel_dies_the_next_one_can_settle_everything(self):
        self.skip_orphans = True
        scenario = scenarios.custom("quick", ops.write("app/calc.py", FIXED_CALC), ops.sleep(0.5), *ops.success_tail())
        settings = {"stale_after": 1.0}
        self.settings = fast_settings(**settings)
        outcomes = []
        for delay in (0.05, 0.25, 0.45, 0.65, 0.9, 1.2, 1.6):
            tid = self.queued(sample_contract())
            child = self.spawn_kernel(tid, scenario, settings=settings)
            time.sleep(delay)
            self.crash(child)
            time.sleep(self.settings.stale_after + 0.4)
            recovered = self.new_kernel()
            recovered.recover()
            d = recovered.describe(tid)
            outcomes.append(d["status"])
            self.assertNotIn(d["status"], ("STARTING", "RUNNING", "VERIFYING", "CANCEL_REQUESTED"), f"delay {delay}: {d['status']}")
            for a in recovered.store.attempts(tid):
                self.assertEqual(a["state"], "ENDED", f"delay {delay}: attempt {a['n']} left unfinished")
            self.assertEqual(recovered.store.locks(), [], f"delay {delay}")
            self.assertTrue(recovered.store.integrity_ok())
        self.assertTrue(set(outcomes) <= {"QUEUED", "RECOVERY_REQUIRED", "REVIEW_REQUIRED", "FAILED", "INTERRUPTED"}, outcomes)
        self.assertIn("RECOVERY_REQUIRED", outcomes)                          # at least some kills really did catch it mid-flight


if __name__ == "__main__":
    unittest.main()
