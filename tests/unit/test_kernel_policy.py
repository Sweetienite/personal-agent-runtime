import json
import unittest
from datetime import timedelta
from pathlib import Path

from par.core.clock import iso
from par.core.kernel import NotAllowed, Refused
from par.providers.base import ProviderAdapter
from par.providers.fake import ScenarioSequence, ScriptedProvider, ops, scenarios
from par.providers.registry import AdapterRegistry
from tests.kernel_fixture import FIX, FIXED_CALC, KernelWorld, r1_contract
from tests.world import sample_contract


class BrokenSpawn(ScriptedProvider):
    def build_argv(self, request):
        return ["/nonexistent/binary"]


class ClassifierBoom(ScriptedProvider):
    def classify_failure(self, outcome):
        raise RuntimeError("adapter bug")


class EnvBoom(ScriptedProvider):
    def environment(self, request):
        raise RuntimeError("adapter bug")


def using(world, codex=None, agy=None):
    """Replace the scripted workers and make a fresh Kernel over the same ledger."""
    if codex is not None:
        world.codex = ScenarioSequence(*codex)
    if agy is not None:
        world.agy = ScenarioSequence(*agy)
    world.kernel = world.new_kernel()


class FailureClasses(KernelWorld):
    default_files = None

    def test_quota_exhaustion_is_recorded_and_a_switch_is_offered_not_taken(self):
        using(self, codex=[scenarios.quota_exhausted()], agy=[scenarios.success()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "QUOTA_EXHAUSTED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["failure_class"], att["stage"]), ("FAILED", "QUOTA_EXHAUSTED", "worker"))
        v = self.kernel.health.verdict("codex", "anything")                                # provider-wide: the account is out
        self.assertEqual((v.usable, v.status, v.scope), (False, "QUOTA_EXHAUSTED", "provider"))
        d = self.kernel.describe(tid)
        self.assertEqual([(a["kind"], a["payload"]["from"], a["payload"]["to"]) for a in d["pending_approvals"]], [("FALLBACK_SWITCH", "w_code", "w_flash")])
        self.assertIn("approve", d["next_actions"])
        self.assertEqual(self.agy.calls, 0)                                                # nothing switched on its own
        self.kernel.approve(tid)
        self.assertEqual((self.status(tid), self.kernel.describe(tid)["pinned_worker"]), ("QUEUED", "w_flash"))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(self.result(tid)["provider"], "antigravity")
        self.assertEqual(self.agy.calls, 1)

    def test_a_bare_429_is_a_rate_limit_and_never_an_exhausted_quota(self):
        using(self, codex=[scenarios.bare_429()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "RATE_LIMITED")
        self.assertEqual(self.kernel.health.verdict("codex", "m-code").status, "RATE_LIMITED")
        self.assertTrue(self.kernel.health.verdict("codex", "another-model").usable)        # only that model is cooling down

    def test_authentication_is_a_provider_wide_stop_until_a_person_says_otherwise(self):
        using(self, codex=[scenarios.auth_required(), scenarios.success()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "AUTH_REQUIRED")
        self.assertIn("sign in with the vendor's official client", self.kernel.store.events(tid)[-1]["reason"] + self.statuses_reason(tid))
        self.kernel.retry(tid)                                                             # a plain retry does not override the health record
        self.assertEqual(self.run_task(tid).status, "PERMISSION_WAIT")                     # (it offers w_flash instead)
        self.assertEqual(self.codex.calls, 1)
        self.kernel.retry(tid, force=True)                                                 # "I have logged in again"
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(self.codex.calls, 2)
        self.assertEqual(self.kernel.store.health_rows(), [])                              # and the success cleared the record

    def statuses_reason(self, tid):
        return " ".join(e["reason"] or "" for e in self.kernel.events(tid) if e["type"] == "STATUS_CHANGED")

    def test_an_unavailable_model_blocks_the_worker_not_the_provider(self):
        using(self, codex=[scenarios.model_unavailable()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "BLOCKED_NO_WORKER")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["failure_class"], "MODEL_UNAVAILABLE")
        self.assertFalse(self.kernel.health.verdict("codex", "m-code").usable)
        self.assertTrue(self.kernel.health.verdict("codex", "some-other-model").usable)

    def test_a_permission_refusal_is_never_answered_with_wider_rights(self):
        for soft in (False, True):
            with self.subTest(soft=soft):
                using(self, codex=[scenarios.permission_denied(soft=soft), scenarios.success()])
                tid = self.queued(r1_contract())
                self.assertEqual(self.run_task(tid).status, "PERMISSION_WAIT")
                d = self.kernel.describe(tid)
                self.assertEqual([a["kind"] for a in d["pending_approvals"]], ["PERMISSION_RETRY"])
                self.assertEqual(self.codex.calls, 1)                                      # no automatic second try
                self.kernel.approve(tid)
                self.assertEqual(self.status(tid), "QUEUED")
                self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")

    def test_a_crashing_worker_fails_the_attempt_and_the_evidence_is_kept(self):
        using(self, codex=[scenarios.crash(139)])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "FAILED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["failure_class"], att["exit_code"]), ("EXECUTION_FAILED", 139))
        self.assertIn("segmentation fault", (self.attempt_dir(tid) / "stderr.log").read_text())

    def test_exit_code_zero_without_a_terminal_state_is_not_success(self):
        using(self, codex=[scenarios.truncated()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "INTERRUPTED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["failure_class"], att["exit_code"]), ("INTERRUPTED", "UNKNOWN", 0))

    def test_unparseable_lines_are_survived_and_reported(self):
        using(self, codex=[scenarios.garbage()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertTrue(any("could not be parsed" in i for i in self.result(tid)["remaining_issues"]))

    def test_a_worker_that_cannot_even_be_started_is_a_failed_attempt_not_a_stuck_task(self):
        reg = AdapterRegistry()
        reg.register("codex", lambda: BrokenSpawn(scenarios.success(), "codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        self.kernel = self.new_kernel(adapters=reg)
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["outcome"]), ("spawn", "failed"))
        self.assertEqual(self.kernel.store.locks(), [])

    def test_a_buggy_adapter_cannot_wedge_the_kernel(self):
        for cls, expected in ((ClassifierBoom, "INTERRUPTED"), (EnvBoom, "FAILED")):
            with self.subTest(adapter=cls.__name__):
                reg = AdapterRegistry()
                reg.register("codex", lambda cls=cls: cls(scenarios.success(), "codex"))
                reg.register("antigravity", self.agy.factory("antigravity"))
                self.kernel = self.new_kernel(adapters=reg)
                tid = self.queued(r1_contract())
                self.assertEqual(self.run_task(tid).status, expected)
                self.assertEqual(self.kernel.store.locks(), [])

    def test_an_output_flood_is_capped_and_noted(self):
        using(self, codex=[scenarios.custom("chatty", ops.flood(3000, 100), *ops.success_tail())])
        self.kernel = self.new_kernel(settings=self.settings.__class__(**{**self.settings.__dict__, "max_log_bytes": 20_000}))
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertTrue(any("truncated" in i for i in self.result(tid)["remaining_issues"]))
        self.assertLess((self.attempt_dir(tid) / "stdout.log").stat().st_size, 20_100)

    def test_a_broken_result_envelope_is_kept_for_review_but_cannot_be_accepted(self):
        from par.execution import results
        original = results.envelope_problems
        results.envelope_problems = lambda env: ["$.tests: pretend schema failure"]
        self.addCleanup(setattr, results, "envelope_problems", original)
        self.allow_bad_envelope = True
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertIn("ENVELOPE_INVALID", self.event_types(tid))
        with self.assertRaises(Refused) as ctx:
            self.kernel.accept(tid)
        self.assertTrue(any("invalid" in p for p in ctx.exception.problems))


class Timeouts(KernelWorld):
    def test_silence_after_output_stops_the_worker_and_keeps_its_partial_work(self):
        self.codex = ScenarioSequence(scenarios.timeout(30, files=FIX))
        self.kernel = self.new_kernel()
        tid = self.queued(sample_contract(timeouts={"startup": 5, "idle": 1, "total": 20}))
        self.assertEqual(self.run_task(tid).status, "INTERRUPTED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["timeout_kind"], att["failure_class"]), ("INTERRUPTED", "idle", "UNKNOWN"))
        res = self.result(tid)
        self.assertTrue(res["partial"] and res["handoff_usable"])
        self.assertIn(b"return a + b", self.kernel.diff(tid))                              # what it had done is not lost
        self.assertEqual(self.kernel.describe(tid)["next_actions"], ["retry", "pause", "cancel"])

    def test_a_worker_that_never_speaks_hits_the_startup_limit(self):
        self.codex = ScenarioSequence(scenarios.custom("mute", ops.sleep(30)))
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract(timeouts={"startup": 1, "idle": 5, "total": 20}))
        self.assertEqual(self.run_task(tid).status, "INTERRUPTED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["timeout_kind"], "startup")

    def test_a_partial_write_before_quota_exhaustion_is_preserved_for_the_next_worker(self):
        self.codex = ScenarioSequence(scenarios.quota_exhausted({f"app/part_{i}.txt": f"chunk {i}\n" for i in (1, 2, 3)}))
        self.kernel = self.new_kernel()
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "QUOTA_EXHAUSTED")
        res = self.result(tid)
        self.assertEqual((res["partial"], res["handoff_usable"], res["boundary"]["violations"]), (True, True, 0))
        self.assertEqual(res["diff_stat"]["files"], 3)
        patch = self.kernel.diff(tid)
        for i in (1, 2, 3):
            self.assertIn(f"app/part_{i}.txt".encode(), patch)
        self.assertEqual(res["tests"], [])                                                 # it never reached verification


class Policies(KernelWorld):
    default_files = None

    def test_policy_pause_turns_every_stoppage_into_a_pause(self):
        self.policy = "pause"
        cases = [(scenarios.quota_exhausted(), "QUOTA_EXHAUSTED"), (scenarios.bare_429(), "RATE_LIMITED"),
                 (scenarios.auth_required(), "AUTH_REQUIRED"), (scenarios.truncated(), "INTERRUPTED"),
                 (scenarios.model_unavailable(), "BLOCKED_NO_WORKER"),
                 (scenarios.custom("transient", ops.emit(type="error", code="transient"), ops.terminal("ERROR"), ops.exit(1)), "TRANSIENT")]
        for scenario, class_name in cases:
            with self.subTest(case=class_name):
                with self.kernel.store.tx():
                    self.kernel.store.clear_health("codex")
                using(self, codex=[scenario, scenarios.success()])
                tid = self.queued(r1_contract())
                self.assertEqual(self.run_task(tid).status, "PAUSED")
                self.assertEqual(self.kernel.describe(tid)["pending_approvals"], [])      # pausing does not also ask to switch
                self.assertEqual(self.agy.calls, 0)

    def test_a_paused_task_resumes_and_pauses_again_while_its_worker_is_still_unavailable(self):
        self.policy = "pause"
        using(self, codex=[scenarios.quota_exhausted(), scenarios.success()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "PAUSED")
        self.kernel.resume(tid)
        self.assertEqual(self.status(tid), "QUEUED")
        report = self.run_task(tid)
        self.assertEqual(report.status, "PAUSED")                                          # the first choice is still out of quota
        self.assertIn("first-choice worker is unavailable", report.reason)
        self.assertEqual(self.codex.calls, 1)
        self.kernel.retry(tid, force=True)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")

    def test_safe_auto_switches_once_for_a_read_only_task(self):
        self.policy = "safe_auto"
        using(self, codex=[scenarios.quota_exhausted()], agy=[scenarios.success()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "QUEUED")                              # switched on its own, waiting to run
        d = self.kernel.describe(tid)
        self.assertEqual((d["pinned_worker"], d["switch_budget"], d["pending_approvals"]), ("w_flash", 0, []))
        self.assertIn("WORKER_SWITCHED", self.event_types(tid))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual([self.kernel.store.get_attempt(tid, n)["provider_id"] for n in (1, 2)], ["codex", "antigravity"])

    def test_safe_auto_does_not_chase_workers_forever(self):
        self.policy = "safe_auto"
        using(self, codex=[scenarios.quota_exhausted()], agy=[scenarios.quota_exhausted()])
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.assertEqual(self.run_task(tid).status, "QUOTA_EXHAUSTED")                     # the second worker failed too
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 2)
        self.assertEqual(self.kernel.describe(tid)["pending_approvals"], [])               # nobody left to offer
        self.assertEqual(self.kernel.describe(tid)["next_actions"], ["retry", "pause", "cancel"])

    def test_safe_auto_never_acts_on_a_file_changing_task(self):
        self.policy = "safe_auto"
        self.default_files = FIX
        using(self, codex=[scenarios.quota_exhausted()], agy=[scenarios.success(FIX)])
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "QUOTA_EXHAUSTED")
        d = self.kernel.describe(tid)
        self.assertEqual([a["kind"] for a in d["pending_approvals"]], ["FALLBACK_SWITCH"])  # R2 always asks
        self.assertEqual((d["pinned_worker"], d["switch_budget"]), (None, 1))

    def test_safe_auto_retries_a_transient_error_once_after_a_delay(self):
        self.policy = "safe_auto"
        transient = scenarios.custom("transient", ops.emit(type="error", code="transient"), ops.terminal("ERROR"), ops.exit(1))
        using(self, codex=[transient, transient, scenarios.success()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "QUEUED")
        d = self.kernel.describe(tid)
        self.assertEqual((d["retry_budget"], d["last_failure"] is not None), (0, True))
        self.assertIn("AUTO_RETRY", self.event_types(tid))
        self.assertIsNotNone(d["not_before"])
        with self.assertRaises(Refused):
            self.run_task(tid)                                                             # not before the delay is over
        with self.kernel.store.tx():
            self.kernel.store.update_task(tid, "TASK_UPDATED", reason="test: delay elapsed", columns={"not_before": None})
        self.assertEqual(self.run_task(tid).status, "INTERRUPTED")                         # the second transient error is not retried
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 2)

    def test_policy_ask_leaves_a_transient_error_to_a_person(self):
        transient = scenarios.custom("transient", ops.emit(type="error", code="transient"), ops.terminal("ERROR"), ops.exit(1))
        using(self, codex=[transient, scenarios.success()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "INTERRUPTED")
        self.assertNotIn("AUTO_RETRY", self.event_types(tid))
        self.kernel.retry(tid)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")


class BeforeStarting(KernelWorld):
    default_files = None

    def test_a_disabled_first_choice_is_offered_a_switch_under_ask(self):
        self.codex_enabled = False
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract())
        report = self.run_task(tid)
        self.assertEqual(report.status, "PERMISSION_WAIT")
        a = self.kernel.describe(tid)["pending_approvals"][0]
        self.assertEqual((a["kind"], a["payload"]["from"], a["payload"]["to"]), ("FALLBACK_SWITCH", "w_code", "w_flash"))
        self.assertIn("provider 'codex' is disabled", a["payload"]["why"])
        self.assertEqual(self.kernel.store.attempts(tid), [])                              # nothing started
        self.kernel.approve(tid)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(self.result(tid)["worker"], "w_flash")

    def test_the_same_situation_under_pause_and_safe_auto(self):
        self.codex_enabled = False
        self.policy = "pause"
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "PAUSED")
        self.policy = "safe_auto"
        self.kernel = self.new_kernel()
        auto = self.queued(r1_contract())
        self.assertEqual(self.run_task(auto).status, "REVIEW_REQUIRED")                    # switched and ran, once
        self.assertIn("WORKER_SWITCHED", self.event_types(auto))
        self.assertEqual(self.kernel.describe(auto)["switch_budget"], 0)

    def test_a_file_changing_task_always_asks_before_switching(self):
        self.codex_enabled = False
        self.policy = "safe_auto"
        self.default_files = FIX
        using(self)
        self.kernel = self.new_kernel()
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "PERMISSION_WAIT")
        self.assertEqual(self.kernel.describe(tid)["pending_approvals"][0]["kind"], "FALLBACK_SWITCH")

    def test_no_registered_adapter_means_blocked_with_the_reason_never_a_guess(self):
        self.register_adapters = False
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract())
        report = self.run_task(tid)
        self.assertEqual(report.status, "BLOCKED_NO_WORKER")
        self.assertIn("no adapter is registered for 'codex'", report.reason)
        self.assertIn("no adapter is registered for 'antigravity'", report.reason)
        self.assertEqual((self.kernel.store.attempts(tid), self.kernel.store.locks()), ([], []))
        self.policy = "pause"
        self.kernel = self.new_kernel()
        paused = self.queued(r1_contract())
        self.assertEqual(self.run_task(paused).status, "PAUSED")

    def test_an_explicitly_requested_worker_is_never_quietly_replaced(self):
        self.agy_enabled = False
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract(worker="w_flash"))
        self.assertEqual(self.run_task(tid).status, "BLOCKED_NO_WORKER")
        self.assertEqual(self.codex.calls + self.agy.calls, 0)

    def test_an_unknown_role_is_blocked_with_a_clear_reason(self):
        tid = self.queued(r1_contract(role="poetry"))
        report = self.run_task(tid)
        self.assertEqual(report.status, "BLOCKED_NO_WORKER")
        self.assertIn("role 'poetry' is not configured", report.reason)

    def test_a_queued_task_keeps_the_configuration_it_was_submitted_under(self):
        tid = self.queued(r1_contract())
        self.codex_enabled = False
        newer = self.new_kernel()                                                          # the user later disables codex...
        self.assertEqual(newer.run(tid).status, "REVIEW_REQUIRED")                         # ...but this task was promised codex
        self.assertEqual(newer.result(tid)["provider"], "codex")

    def test_an_explicit_retry_adopts_the_new_configuration(self):
        using(self, codex=[scenarios.crash()])
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "FAILED")
        self.codex_enabled = False
        newer = self.new_kernel()
        newer.retry(tid)
        self.assertIn("CONFIG_REFRESHED", [e["type"] for e in newer.events(tid)])
        self.assertEqual(newer.run(tid).status, "PERMISSION_WAIT")                         # now codex really is disabled
        self.assertEqual(newer.describe(tid)["pending_approvals"][0]["payload"]["to"], "w_flash")

    def test_one_tasks_failure_informs_the_next_tasks_routing(self):
        using(self, codex=[scenarios.quota_exhausted(), scenarios.success()])
        first = self.queued(r1_contract())
        second = self.queued(r1_contract())
        self.run_task(first)
        self.assertEqual(self.run_task(second).status, "PERMISSION_WAIT")                  # second never even tried codex
        self.assertEqual(self.codex.calls, 1)


if __name__ == "__main__":
    unittest.main()
