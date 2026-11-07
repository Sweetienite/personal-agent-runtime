import os
import signal
import time
import unittest
from pathlib import Path

from par.core.kernel import Busy, NotAllowed, Refused
from par.core.settings import KernelSettings
from par.execution import subprocesses as sp
from par.providers.fake import ScenarioSequence, ops, scenarios
from tests.kernel_fixture import FIX, FIXED_CALC, KernelWorld, fast_settings, r1_contract
from tests.world import sample_contract


def using(world, codex=None, agy=None):
    if codex is not None:
        world.codex = ScenarioSequence(*codex)
    if agy is not None:
        world.agy = ScenarioSequence(*agy)
    world.kernel = world.new_kernel()


class Cancelling(KernelWorld):
    default_files = None

    def test_a_queued_task_can_be_canceled_and_later_retried_as_a_new_attempt(self):
        tid = self.queued(r1_contract())
        self.assertEqual(self.kernel.cancel(tid, reason="changed my mind")["status"], "CANCELED")
        self.assertEqual(self.kernel.cancel(tid)["status"], "CANCELED")                    # asking again is harmless
        self.assertEqual(self.event_types(tid).count("STATUS_CHANGED"), 3)                  # READY, QUEUED, CANCELED - no extra noise
        self.assertEqual(self.kernel.describe(tid)["next_actions"], ["retry"])
        with self.assertRaises(NotAllowed):
            self.run_task(tid)                                                             # canceled tasks never run in place
        self.kernel.retry(tid)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 1)

    def test_canceling_withdraws_what_was_waiting_for_a_decision(self):
        tid = self.submit().task_id
        self.kernel.cancel(tid)
        self.assertEqual([(a["kind"], a["status"]) for a in self.kernel.store.approvals(tid)], [("WRITE_PERMISSION", "REJECTED")])
        self.assertEqual(self.kernel.describe(tid)["pending_approvals"], [])

    def test_accepted_work_cannot_be_canceled(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.kernel.accept(tid)
        with self.assertRaises(NotAllowed):
            self.kernel.cancel(tid)
        with self.assertRaises(NotAllowed):
            self.kernel.retry(tid)
        self.kernel.close_task(tid)
        with self.assertRaises(NotAllowed):
            self.kernel.cancel(tid)
        with self.assertRaises(NotAllowed):
            self.kernel.retry(tid)

    def test_a_result_waiting_for_review_can_be_discarded(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.assertEqual(self.kernel.cancel(tid)["status"], "CANCELED")

    def test_cancel_stops_a_running_worker_politely(self):
        using(self, codex=[scenarios.cancellable(60)])
        tid = self.queued(r1_contract())
        other = self.new_kernel()                                                          # "another terminal": a different Kernel
        t = self.in_thread(lambda: self.kernel.run(tid))
        self.assertTrue(self.wait_for(lambda: other.describe(tid)["status"] == "RUNNING"))
        t0 = time.monotonic()
        self.assertEqual(other.cancel(tid, reason="stop")["status"], "CANCEL_REQUESTED")
        t.join(20)
        self.assertEqual(t.box["value"].status, "CANCELED")
        self.assertLess(time.monotonic() - t0, 15)                                          # it did not wait for the 60 s worker
        att = other.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["exit_code"], att["state"]), ("CANCELED", 130, "ENDED"))
        self.assertEqual(self.statuses(tid)[-3:], ["RUNNING", "CANCEL_REQUESTED", "CANCELED"])
        self.assertEqual(other.store.locks(), [])
        self.assertEqual(self.result(tid)["outcome"], "canceled")

    def test_cancel_kills_a_worker_that_ignores_the_polite_request(self):
        using(self, codex=[scenarios.custom("stubborn", ops.ignore_sigterm(), ops.message("working"), ops.sleep(60))])
        tid = self.queued(r1_contract())
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(tid))
        self.assertTrue(self.wait_for(lambda: other.describe(tid)["status"] == "RUNNING"))
        pgid = other.store.get_attempt(tid, 1)["pgid"]
        other.cancel(tid)
        t.join(20)
        self.assertEqual(t.box["value"].status, "CANCELED")
        self.assertEqual(other.store.get_attempt(tid, 1)["signal"], signal.SIGKILL)
        self.assertFalse(sp.group_alive(pgid))

    def test_cancel_during_verification_stops_the_check_too(self):
        self.default_files = {"app/slow.py": "import time\ntime.sleep(60)\n", "app/calc.py": FIXED_CALC}
        using(self, codex=[scenarios.success(self.default_files)])
        contract = sample_contract(acceptance={"criteria": [], "checks": [{"name": "slow", "argv": ["python3", "app/slow.py"], "timeout": 120}]})
        tid = self.queued(contract)
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(tid))
        self.assertTrue(self.wait_for(lambda: other.describe(tid)["status"] == "VERIFYING"))
        time.sleep(0.5)                                                                    # let the check actually start
        pgid = other.store.get_attempt(tid, 1)["pgid"]
        other.cancel(tid)
        t.join(30)
        self.assertEqual(t.box["value"].status, "CANCELED")
        self.assertFalse(sp.group_alive(pgid))
        self.assertEqual(other.store.locks(), [])


class PauseResume(KernelWorld):
    default_files = None

    def test_pause_holds_a_task_and_resume_releases_it(self):
        tid = self.queued(r1_contract())
        self.assertEqual(self.kernel.pause(tid, reason="later")["status"], "PAUSED")
        self.assertEqual(self.kernel.pause(tid)["status"], "PAUSED")                       # idempotent
        self.assertIsNone(self.kernel.run_next())                                          # a paused task is not picked up
        with self.assertRaises(NotAllowed):
            self.run_task(tid)
        self.assertEqual(self.kernel.resume(tid)["status"], "QUEUED")
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")

    def test_resume_only_applies_to_paused_tasks(self):
        tid = self.queued(r1_contract())
        with self.assertRaises(NotAllowed):
            self.kernel.resume(tid)

    def test_a_running_attempt_cannot_be_paused(self):
        using(self, codex=[scenarios.cancellable(60)])
        tid = self.queued(r1_contract())
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(tid))
        self.assertTrue(self.wait_for(lambda: other.describe(tid)["status"] == "RUNNING"))
        with self.assertRaises(NotAllowed) as ctx:
            other.pause(tid)
        self.assertIn("cancel it instead", str(ctx.exception))
        other.cancel(tid)
        t.join(20)

    def test_resuming_a_task_that_still_needs_a_decision_goes_back_to_waiting(self):
        tid = self.submit().task_id
        self.kernel.pause(tid)
        self.assertEqual(self.kernel.resume(tid)["status"], "PERMISSION_WAIT")
        self.assertEqual(len(self.kernel.describe(tid)["pending_approvals"]), 1)

    def test_pausing_is_available_in_every_waiting_state(self):
        using(self, codex=[scenarios.bare_429()])
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.assertEqual(self.status(tid), "RATE_LIMITED")
        self.assertEqual(self.kernel.pause(tid)["status"], "PAUSED")


class Retrying(KernelWorld):
    default_files = None

    def test_a_fresh_retry_starts_over_from_the_base_not_from_the_failed_attempt(self):
        using(self, codex=[scenarios.custom("junk then crash", ops.write("app/junk.txt", "left behind"), ops.exit(3)), scenarios.success(FIX)])
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "FAILED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["failure_class"], "EXECUTION_FAILED")
        self.kernel.retry(tid, fresh=True)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        first = self.kernel.store.get_attempt(tid, 1)
        second = self.kernel.store.get_attempt(tid, 2)
        self.assertNotEqual(first["workspace_path"], second["workspace_path"])
        self.assertTrue((Path(first["workspace_path"]) / "app" / "junk.txt").exists())      # the failed attempt's evidence is kept
        self.assertFalse((Path(second["workspace_path"]) / "app" / "junk.txt").exists())    # the new one started clean
        self.assertNotIn(b"junk.txt", self.kernel.diff(tid))
        self.assertEqual((first["end_kind"], second["end_kind"]), ("FAILED", "COMPLETED"))
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 2)
        self.assertNotIn("RELAY_SEED", self.event_types(tid))

    def test_a_plain_retry_continues_the_verified_work_of_the_failed_attempt(self):
        using(self, codex=[scenarios.custom("junk then crash", ops.write("app/junk.txt", "left behind"), ops.exit(3)), scenarios.success(FIX)])
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "FAILED")
        self.kernel.retry(tid)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        second = self.kernel.store.get_attempt(tid, 2)
        self.assertTrue((Path(second["workspace_path"]) / "app" / "junk.txt").exists())      # carried over (see tests/unit/test_kernel_relay.py)
        self.assertIn(b"junk.txt", self.kernel.diff(tid))
        self.assertIn("RELAY_SEED", self.event_types(tid))

    def test_a_retry_can_name_the_worker_and_resets_the_budgets(self):
        self.policy = "safe_auto"
        using(self, codex=[scenarios.quota_exhausted(), scenarios.success()], agy=[scenarios.crash(), scenarios.success()])
        tid = self.queued(r1_contract())
        self.run_task(tid)                                                                 # codex out of quota -> auto switch
        self.assertEqual(self.run_task(tid).status, "FAILED")                              # agy crashed
        d = self.kernel.describe(tid)
        self.assertEqual((d["switch_budget"], d["retry_budget"]), (0, 1))
        self.kernel.retry(tid, worker="w_flash")
        d = self.kernel.describe(tid)
        self.assertEqual((d["switch_budget"], d["retry_budget"], d["pinned_worker"]), (1, 1, "w_flash"))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(self.result(tid)["worker"], "w_flash")

    def test_retry_refuses_workers_the_configuration_does_not_know_and_states_it_does_not_apply_to(self):
        using(self, codex=[scenarios.crash()])
        tid = self.queued(r1_contract())
        with self.assertRaises(NotAllowed):
            self.kernel.retry(tid)                                                         # QUEUED: just run it
        self.run_task(tid)
        with self.assertRaises(Refused):
            self.kernel.retry(tid, worker="w_ghost")
        done = self.queued(r1_contract())
        self.codex = ScenarioSequence(scenarios.success())
        self.kernel = self.new_kernel()
        self.run_task(done)
        with self.assertRaises(NotAllowed):
            self.kernel.retry(done)                                                        # REVIEW_REQUIRED: accept, reject or cancel
        self.kernel.accept(done)
        with self.assertRaises(NotAllowed):
            self.kernel.retry(done)

    def test_rejecting_a_result_sends_the_task_back_for_a_retry(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        out = self.kernel.reject(tid, note="the summary misses the second module")
        self.assertEqual(out["status"], "FAILED")
        self.assertIn("RESULT_REJECTED", self.event_types(tid))
        self.kernel.retry(tid)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")

    def test_a_cancel_cannot_be_used_to_skip_the_write_permission_gate(self):
        self.default_files = FIX
        using(self, codex=[scenarios.success(FIX)])
        tid = self.submit().task_id                                                        # waiting for write permission
        self.kernel.cancel(tid)
        self.kernel.retry(tid)
        d = self.kernel.describe(tid)
        self.assertEqual(d["status"], "PERMISSION_WAIT")                                   # asked again, not queued
        self.assertEqual([a["kind"] for a in d["pending_approvals"]], ["WRITE_PERMISSION"])
        with self.assertRaises(NotAllowed):
            self.run_task(tid)

    def test_a_coordinators_worker_override_is_also_asked_again(self):
        tid = self.submit(r1_contract(), worker="w_flash", by="coordinator").task_id
        self.kernel.cancel(tid)
        self.kernel.retry(tid)
        self.assertEqual([a["kind"] for a in self.kernel.describe(tid)["pending_approvals"]], ["WORKER_OVERRIDE"])


    def test_retry_paused_permission_wait_reuses_pending_write_approval(self):
        tid = self.submit().task_id
        original = self.kernel.store.approvals(tid, "PENDING")[0]["id"]
        self.kernel.pause(tid)
        self.assertEqual(self.kernel.retry(tid)["status"], "PERMISSION_WAIT")
        self.assertEqual([a["id"] for a in self.kernel.store.approvals(tid, "PENDING")], [original])
        self.assertEqual(self.event_types(tid).count("APPROVAL_REQUESTED"), 1)
        self.assertEqual(self.kernel.approve(tid)["status"], "QUEUED")
        self.assertEqual(self.event_types(tid).count("APPROVAL_DECIDED"), 1)

    def test_retry_paused_permission_wait_reuses_worker_override(self):
        tid = self.submit(r1_contract(), worker="w_flash", by="coordinator").task_id
        original = self.kernel.store.approvals(tid, "PENDING")[0]["id"]
        self.kernel.pause(tid)
        self.assertEqual(self.kernel.retry(tid)["status"], "PERMISSION_WAIT")
        self.assertEqual([a["id"] for a in self.kernel.store.approvals(tid, "PENDING")], [original])
        self.assertEqual(self.kernel.approve(tid)["status"], "QUEUED")

    def test_retry_paused_with_two_distinct_pending_kinds_preserves_both(self):
        tid = self.submit(sample_contract(role="code", worker="w_code"), by="coordinator").task_id
        original = [(a["id"], a["kind"]) for a in self.kernel.store.approvals(tid, "PENDING")]
        self.assertEqual([k for _, k in original], ["WRITE_PERMISSION", "WORKER_OVERRIDE"])
        self.kernel.pause(tid)
        self.assertEqual(self.kernel.retry(tid)["status"], "PERMISSION_WAIT")
        self.assertEqual([(a["id"], a["kind"]) for a in self.kernel.store.approvals(tid, "PENDING")], original)
        self.assertEqual(self.event_types(tid).count("APPROVAL_REQUESTED"), 2)
        self.assertEqual(self.kernel.approve(tid, original[0][0])["status"], "PERMISSION_WAIT")
        self.assertEqual(self.kernel.approve(tid, original[1][0])["status"], "QUEUED")

    def test_retry_paused_after_one_kind_approved_does_not_duplicate_other(self):
        tid = self.submit(sample_contract(role="code", worker="w_code"), by="coordinator").task_id
        pending = self.kernel.store.approvals(tid, "PENDING")
        self.kernel.approve(tid, pending[0]["id"])
        self.kernel.pause(tid)
        self.assertEqual(self.kernel.retry(tid)["status"], "PERMISSION_WAIT")
        self.assertEqual([a["id"] for a in self.kernel.store.approvals(tid, "PENDING")], [pending[1]["id"]])
        self.assertEqual(self.event_types(tid).count("APPROVAL_REQUESTED"), 2)
        self.assertEqual(self.kernel.approve(tid)["status"], "QUEUED")

    def test_cancel_then_retry_reissues_revoked_permission(self):
        tid = self.submit().task_id
        old_id = self.kernel.store.approvals(tid, "PENDING")[0]["id"]
        self.kernel.cancel(tid)
        self.assertEqual(self.kernel.retry(tid)["status"], "PERMISSION_WAIT")
        approvals = self.kernel.store.approvals(tid)
        self.assertEqual([(a["id"], a["status"]) for a in approvals],
                         [(old_id, "REJECTED"), (approvals[1]["id"], "PENDING")])
        self.assertNotEqual(old_id, approvals[1]["id"])
        self.assertEqual(self.event_types(tid).count("APPROVAL_REQUESTED"), 2)

    def test_pause_resume_path_unmodified(self):
        tid = self.submit().task_id
        original = self.kernel.store.approvals(tid, "PENDING")[0]["id"]
        self.kernel.pause(tid)
        self.assertEqual(self.kernel.resume(tid)["status"], "PERMISSION_WAIT")
        self.assertEqual([a["id"] for a in self.kernel.store.approvals(tid, "PENDING")], [original])

    def test_retry_does_not_bypass_write_gate(self):
        tid = self.submit().task_id
        self.kernel.pause(tid)
        self.kernel.retry(tid)
        with self.assertRaises(NotAllowed):
            self.kernel.run(tid)
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 0)

    def test_retry_with_different_worker_replaces_stale_override(self):
        tid = self.submit(r1_contract(), worker="w_code", by="coordinator").task_id
        old = self.kernel.store.approvals(tid, "PENDING")[0]
        self.kernel.pause(tid)
        self.assertEqual(self.kernel.retry(tid, worker="w_flash")["status"], "PERMISSION_WAIT")
        approvals = self.kernel.store.approvals(tid)
        self.assertEqual(approvals[0]["status"], "REJECTED")
        pending = self.kernel.store.approvals(tid, "PENDING")
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0]["id"], old["id"])
        self.assertEqual(pending[0]["payload"]["worker"], "w_flash")
        self.assertEqual(self.kernel.approve(tid)["status"], "QUEUED")

    def test_retry_changed_worker_does_not_reuse_old_approved_override(self):
        tid = self.submit(r1_contract(), worker="w_code", by="coordinator").task_id
        self.kernel.approve(tid)
        self.kernel.pause(tid)
        self.assertEqual(self.kernel.retry(tid, worker="w_flash")["status"], "PERMISSION_WAIT")
        pending = self.kernel.store.approvals(tid, "PENDING")
        self.assertEqual([(a["kind"], a["payload"]["worker"]) for a in pending],
                         [("WORKER_OVERRIDE", "w_flash")])
        with self.assertRaises(NotAllowed):
            self.kernel.run(tid)
        self.assertEqual(self.kernel.approve(tid)["status"], "QUEUED")


class Approvals(KernelWorld):
    default_files = None

    def two_pending(self):
        return self.submit(sample_contract(role="code", worker="w_code"), by="coordinator").task_id

    def test_several_pending_approvals_must_be_decided_one_by_one_by_name(self):
        tid = self.two_pending()
        kinds = [a["kind"] for a in self.kernel.describe(tid)["pending_approvals"]]
        self.assertEqual(kinds, ["WRITE_PERMISSION", "WORKER_OVERRIDE"])
        with self.assertRaises(NotAllowed) as ctx:
            self.kernel.approve(tid)
        self.assertIn("name one with --id", str(ctx.exception))
        first, second = [a["id"] for a in self.kernel.describe(tid)["pending_approvals"]]
        self.assertEqual(self.kernel.approve(tid, first)["status"], "PERMISSION_WAIT")
        self.assertEqual(self.kernel.approve(tid, second)["status"], "QUEUED")

    def test_unknown_and_already_decided_approvals_are_refused(self):
        tid = self.submit().task_id
        with self.assertRaises(NotAllowed):
            self.kernel.approve(tid, 999)
        self.kernel.approve(tid)
        with self.assertRaises(NotAllowed):
            self.kernel.approve(tid)                                                       # nothing left to approve
        with self.assertRaises(NotAllowed):
            self.kernel.reject(tid, 1)

    def test_refusing_a_permission_cancels_the_task(self):
        for coordinator_worker in (False, True):
            tid = (self.submit(r1_contract(), worker="w_flash", by="coordinator") if coordinator_worker else self.submit()).task_id
            out = self.kernel.reject(tid, note="no")
            self.assertEqual(out["status"], "CANCELED")
            self.assertEqual([a["status"] for a in self.kernel.store.approvals(tid)], ["REJECTED"])

    def test_declining_a_switch_in_a_failure_state_just_clears_the_offer(self):
        using(self, codex=[scenarios.quota_exhausted()])
        tid = self.queued(r1_contract())
        self.run_task(tid)
        out = self.kernel.reject(tid)
        self.assertEqual((out["status"], out["pending_approvals"]), ("QUOTA_EXHAUSTED", []))
        self.assertEqual(out["next_actions"], ["retry", "pause", "cancel"])

    def test_declining_a_switch_before_starting_pauses_instead_of_stranding_the_task(self):
        self.codex_enabled = False
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "PERMISSION_WAIT")
        self.assertEqual(self.kernel.reject(tid)["status"], "PAUSED")

    def test_accepting_leaves_an_audit_record(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.kernel.accept(tid, by="user", note="looks right")
        accepted = [a for a in self.kernel.store.approvals(tid) if a["kind"] == "ACCEPT_RESULT"]
        self.assertEqual([(a["status"], a["decided_by"], a["note"]) for a in accepted], [("APPROVED", "user", "looks right")])
        with self.assertRaises(Refused):
            self.kernel.accept(tid)                                                        # not twice
        with self.assertRaises(NotAllowed):
            self.kernel.close_task(self.queued(r1_contract()))                             # only accepted work closes


class NothingUnfinishedIsAccepted(KernelWorld):
    default_files = None

    def refused_in(self, tid, label):
        with self.assertRaises(Refused, msg=label) as ctx:
            self.kernel.accept(tid)
        self.assertTrue(any("only REVIEW_REQUIRED" in p for p in ctx.exception.problems), label)
        self.assertNotEqual(self.status(tid), "ACCEPTED", label)

    def test_accept_is_refused_in_every_state_a_task_can_be_in_before_review(self):
        waiting = self.submit().task_id
        self.refused_in(waiting, "PERMISSION_WAIT")
        queued = self.queued(r1_contract())
        self.refused_in(queued, "QUEUED")
        self.kernel.pause(queued)
        self.refused_in(queued, "PAUSED")
        self.kernel.cancel(queued)
        self.refused_in(queued, "CANCELED")
        for scenario, label in ((scenarios.crash(), "FAILED"), (scenarios.truncated(), "INTERRUPTED"), (scenarios.bare_429(), "RATE_LIMITED"),
                                (scenarios.quota_exhausted(), "QUOTA_EXHAUSTED"), (scenarios.auth_required(), "AUTH_REQUIRED"),
                                (scenarios.model_unavailable(), "BLOCKED_NO_WORKER"), (scenarios.permission_denied(), "PERMISSION_WAIT")):
            using(self, codex=[scenario])
            with self.kernel.store.tx():
                self.kernel.store.clear_health("codex")
            tid = self.queued(r1_contract())
            self.run_task(tid)
            self.assertEqual(self.status(tid), label)
            self.refused_in(tid, label)

    def test_a_running_task_cannot_be_accepted(self):
        using(self, codex=[scenarios.cancellable(60)])
        tid = self.queued(r1_contract())
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(tid))
        self.assertTrue(self.wait_for(lambda: other.describe(tid)["status"] == "RUNNING"))
        with self.assertRaises(Refused):
            other.accept(tid)
        other.cancel(tid)
        t.join(20)

    def test_a_failed_result_cannot_be_accepted_even_if_the_worker_said_it_passed(self):
        using(self, codex=[scenarios.success({"app/calc.py": "def add(a, b):\n    return 0\n"}, claim_tests_pass=True)])
        self.default_files = FIX
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "FAILED")
        self.refused_in(tid, "FAILED after failed checks")


class Drift(KernelWorld):
    default_files = None

    def test_a_project_that_moved_since_submission_must_be_confirmed(self):
        tid = self.queued(r1_contract())
        old_head = self.git(self.project_path, "rev-parse", "HEAD")
        self.commit_in_project(self.project_path, "app/extra.py", "x = 1\n", "someone else committed")
        report = self.run_task(tid)
        self.assertEqual(report.status, "PERMISSION_WAIT")
        a = self.kernel.describe(tid)["pending_approvals"][0]
        self.assertEqual((a["kind"], a["payload"]["changed"]), ("BASE_DRIFT", ["head"]))
        self.assertEqual(self.kernel.store.attempts(tid), [])
        self.kernel.approve(tid)
        d = self.kernel.describe(tid)
        self.assertEqual(d["status"], "QUEUED")
        self.assertNotEqual(d["base"]["commit"], old_head)
        self.assertIn("BASE_REFRESHED", self.event_types(tid))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        ws = self.kernel.store.get_attempt(tid, 1)["workspace_path"]
        self.assertTrue(os.path.exists(os.path.join(ws, "app", "extra.py")))               # it worked on the confirmed state

    def test_refusing_the_drift_cancels_the_task(self):
        tid = self.queued(r1_contract())
        self.commit_in_project(self.project_path, "app/extra.py", "x = 1\n")
        self.run_task(tid)
        self.assertEqual(self.kernel.reject(tid)["status"], "CANCELED")

    def test_a_changed_state_file_counts_as_drift(self):
        tid = self.queued(r1_contract())
        self.commit_in_project(self.project_path, ".ai/STATE.md", "# Current State\n\n## Baseline\n\n- new\n")
        self.run_task(tid)
        changed = self.kernel.describe(tid)["pending_approvals"][0]["payload"]["changed"]
        self.assertIn("state", changed)

    def test_unrelated_uncommitted_edits_do_not_disturb_a_read_only_task(self):
        tid = self.queued(r1_contract())
        (self.project_path / "app" / "calc.py").write_text("# someone is editing this right now\n")
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        ws = self.kernel.store.get_attempt(tid, 1)["workspace_path"]
        self.assertIn("return a - b", (Path(ws) / "app" / "calc.py").read_text())          # the clone is of the committed base

    def test_a_file_changing_task_is_stricter_about_uncommitted_edits(self):
        self.default_files = FIX
        tid = self.queued()
        (self.project_path / "README.md").write_text("# edited after submission\n")
        self.assertEqual(self.run_task(tid).status, "PERMISSION_WAIT")
        self.assertIn("dirty_digest", self.kernel.describe(tid)["pending_approvals"][0]["payload"]["changed"])


class Concurrency(KernelWorld):
    default_files = None

    def test_two_kernels_racing_for_one_task_start_it_once(self):
        using(self, codex=[scenarios.custom("slowish", ops.message("hi"), ops.sleep(1.0), *ops.success_tail())])
        tid = self.queued(r1_contract())
        a, b = self.new_kernel(), self.new_kernel()
        ta, tb = self.in_thread(lambda: a.run(tid)), self.in_thread(lambda: b.run(tid))
        ta.join(30)
        tb.join(30)
        outcomes = [t.box.get("value") or t.box.get("error") for t in (ta, tb)]
        reports = [o for o in outcomes if hasattr(o, "status")]
        errors = [o for o in outcomes if isinstance(o, (Busy, NotAllowed))]
        self.assertEqual((len(reports), len(errors)), (1, 1), outcomes)
        self.assertEqual(self.codex.calls, 1)
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 1)

    def test_one_worker_at_a_time(self):
        using(self, codex=[scenarios.custom("slow", ops.message("hi"), ops.sleep(2.0), *ops.success_tail())])
        first = self.queued(r1_contract())
        second = self.queued(r1_contract())
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(first))
        self.assertTrue(self.wait_for(lambda: other.describe(first)["status"] == "RUNNING"))
        with self.assertRaises(Busy) as ctx:
            other.run(second)
        self.assertEqual(ctx.exception.code, "capacity")
        self.assertIsNone(other.run_next())
        self.assertEqual((other.describe(second)["status"], other.store.attempts(second)), ("QUEUED", []))
        t.join(30)
        self.assertEqual(other.run(second).status, "REVIEW_REQUIRED")

    def test_a_project_is_never_worked_on_twice_at_once_even_with_spare_capacity(self):
        self.settings = fast_settings(max_parallel_workers=2)
        using(self, codex=[scenarios.custom("slow", ops.message("hi"), ops.sleep(2.0), *ops.success_tail())])
        first = self.queued(r1_contract())
        second = self.queued(r1_contract())
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(first))
        self.assertTrue(self.wait_for(lambda: other.describe(first)["status"] == "RUNNING"))
        lock = other.store.get_lock("fixture")
        self.assertEqual((lock["task_id"], lock["attempt_n"], lock["kernel_id"]), (first, 1, self.kernel.kernel_id))
        with self.assertRaises(Busy) as ctx:
            other.run(second)
        self.assertEqual(ctx.exception.code, "project_locked")
        t.join(30)
        self.assertEqual(other.store.locks(), [])

    def test_a_live_attempt_proves_it_is_alive(self):
        using(self, codex=[scenarios.custom("slow", ops.message("hi"), ops.sleep(1.5), *ops.success_tail())])
        tid = self.queued(r1_contract())
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(tid))
        self.assertTrue(self.wait_for(lambda: other.describe(tid)["status"] == "RUNNING"))
        first = other.store.get_attempt(tid, 1)["heartbeat_at"]
        self.assertTrue(self.wait_for(lambda: other.store.get_attempt(tid, 1)["heartbeat_at"] > first, timeout=5))
        self.assertGreater(other.store.get_lock("fixture")["heartbeat_at"], first)
        t.join(30)


if __name__ == "__main__":
    unittest.main()
