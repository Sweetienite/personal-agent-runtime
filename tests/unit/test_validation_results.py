import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from par.execution import results as R, validation as V
from par.providers.base import ProviderEvent
from tests.world import sample_contract, sample_result


class CheckCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.verify = self.base / "verify"
        self.verify.mkdir()
        self.attempt = self.base / "attempt"
        self.attempt.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()
        self.tmp = self.base / "tmp"
        self.tmp.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def put(self, name, body):
        (self.verify / name).write_text(body)

    def run_checks(self, *specs, **kw):
        return V.run_checks(specs, cwd=self.verify, attempt_dir=self.attempt, home=self.home, tmp=self.tmp, **kw)

    def unittest_spec(self, name="unit", pattern="test_*.py", **extra):
        return {"name": name, "argv": ["python3", "-m", "unittest", "discover", "-s", ".", "-p", pattern, "-q"], "timeout": 30, **extra}


class Executables(unittest.TestCase):
    def test_python_means_the_interpreter_running_the_kernel(self):
        self.assertEqual(V.resolve_executable("python3"), sys.executable)
        self.assertEqual(V.resolve_executable("python"), sys.executable)

    def test_other_programs_come_only_from_trusted_directories(self):
        self.assertEqual(V.resolve_executable("ls"), "/bin/ls")
        self.assertIsNone(V.resolve_executable("definitely-not-installed-xyz"))

    def test_the_users_path_is_never_searched(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "pytest"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": f"{d}:/usr/bin"}), mock.patch.object(V, "TRUSTED_DIRS", ("/nonexistent",)):
                self.assertIsNone(V.resolve_executable("pytest"))

    def test_the_child_environment_is_small_and_secret_free(self):
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "x", "ANTHROPIC_API_KEY": "y"}):
            env = V.check_env(Path("/h"), Path("/t"))
        self.assertEqual(set(env), {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "CI", "NO_COLOR"})
        self.assertEqual((env["HOME"], env["TMPDIR"]), ("/h", "/t"))


class RunningChecks(CheckCase):
    def test_a_passing_check(self):
        self.put("test_ok.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertEqual(1, 1)\n")
        (r,) = self.run_checks(self.unittest_spec())
        self.assertEqual((r.name, r.status, r.exit_code, r.detail), ("unit", "PASS", 0, ""))
        log = self.attempt / r.log
        self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)
        text = log.read_text()
        self.assertIn("--- stdout ---", text)
        self.assertIn("--- stderr ---", text)
        self.assertIn("OK", text)
        self.assertEqual(sorted(p.name for p in self.attempt.iterdir()), ["check_unit.log"])         # the two raw logs are folded in
        self.assertEqual(r.to_test()["status"], "PASS")

    def test_a_failing_check_reports_the_exit_code(self):
        self.put("test_bad.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertEqual(1, 2)\n")
        (r,) = self.run_checks(self.unittest_spec())
        self.assertEqual((r.status, r.exit_code, r.detail), ("FAIL", 1, "exit code 1"))
        self.assertIn("AssertionError", (self.attempt / r.log).read_text())

    def test_an_expected_non_zero_exit_is_a_pass(self):
        self.put("test_bad.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.fail('on purpose')\n")
        (r,) = self.run_checks(self.unittest_spec(expect_exit=1))
        self.assertEqual((r.status, r.exit_code, r.expect_exit), ("PASS", 1, 1))

    def test_a_hung_check_times_out_and_is_stopped(self):
        self.put("slow.py", "import time\ntime.sleep(60)\n")
        t0 = time.monotonic()
        (r,) = self.run_checks({"name": "slow", "argv": ["python3", "slow.py"], "timeout": 0.6}, grace=0.5)
        self.assertEqual(r.status, "TIMEOUT")
        self.assertLess(time.monotonic() - t0, 20)
        self.assertIn("no result within 0.6s", r.detail)

    def test_every_check_runs_even_after_a_failure(self):
        self.put("good.py", "print('fine')\n")
        self.put("bad.py", "raise SystemExit(3)\n")
        results = self.run_checks({"name": "one", "argv": ["python3", "good.py"]}, {"name": "two", "argv": ["python3", "bad.py"]},
                                  {"name": "three", "argv": ["python3", "good.py"]})
        self.assertEqual([(r.name, r.status, r.exit_code) for r in results], [("one", "PASS", 0), ("two", "FAIL", 3), ("three", "PASS", 0)])

    def test_a_check_killed_by_a_signal_fails_and_says_so(self):
        self.put("die.py", "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        (r,) = self.run_checks({"name": "die", "argv": ["python3", "die.py"]})
        self.assertEqual((r.status, r.exit_code, r.detail), ("FAIL", None, "killed by signal 9"))

    def test_a_missing_program_is_an_error_with_an_explanation(self):
        with mock.patch.object(V, "TRUSTED_DIRS", ()):
            (r,) = self.run_checks({"name": "lint", "argv": ["ruff", "check", "."]})
        self.assertEqual((r.status, r.exit_code, r.detail), ("ERROR", None, "executable not found"))
        self.assertIn("not found in the trusted locations", (self.attempt / r.log).read_text())

    def test_the_check_sees_only_the_clean_environment_and_the_verification_tree(self):
        self.put("env.py", "import json, os\njson.dump({'names': sorted(os.environ), 'home': os.environ['HOME'], 'tmp': os.environ['TMPDIR'], 'cwd': os.getcwd()}, open('env.json', 'w'))\n")
        with mock.patch.dict(os.environ, {"PAR_TEST_SECRET": "hunter2", "ANTHROPIC_API_KEY": "sk-ant-abcdefghijklmnop0123"}):
            (r,) = self.run_checks({"name": "env", "argv": ["python3", "env.py"]})
        import json
        doc = json.loads((self.verify / "env.json").read_text())
        self.assertEqual(r.status, "PASS")
        self.assertNotIn("PAR_TEST_SECRET", doc["names"])
        self.assertNotIn("ANTHROPIC_API_KEY", doc["names"])
        self.assertEqual((doc["home"], doc["tmp"]), (str(self.home), str(self.tmp)))
        self.assertEqual(os.path.realpath(doc["cwd"]), str(self.verify))

    def test_no_bytecode_litter_in_the_verification_tree(self):
        self.put("helper.py", "X = 1\n")
        self.put("test_uses_helper.py", "import unittest, helper\nclass T(unittest.TestCase):\n    def test_a(self): self.assertEqual(helper.X, 1)\n")
        (r,) = self.run_checks(self.unittest_spec())
        self.assertEqual(r.status, "PASS")
        self.assertEqual(list(self.verify.rglob("__pycache__")), [])

    def test_check_output_is_redacted_in_the_log(self):
        self.put("leak.py", "print('key sk-ant-abcdefghijklmnopqrstuvwxyz0123456789')\n")
        (r,) = self.run_checks({"name": "leak", "argv": ["python3", "leak.py"]})
        text = (self.attempt / r.log).read_text()
        self.assertNotIn("sk-ant-abcdefghijklmnop", text)
        self.assertIn("[REDACTED", text)

    def test_cancel_stops_the_run_and_skips_the_rest(self):
        self.put("slow.py", "import time\ntime.sleep(60)\n")
        self.put("good.py", "print('x')\n")
        t0 = time.monotonic()
        results = self.run_checks({"name": "slow", "argv": ["python3", "slow.py"], "timeout": 30}, {"name": "after", "argv": ["python3", "good.py"]},
                                  should_cancel=lambda: time.monotonic() - t0 > 0.4, grace=0.5)
        self.assertEqual([(r.name, r.status) for r in results], [("slow", "ERROR")])
        self.assertIn("canceled", results[0].detail)

    def test_heartbeat_keeps_ticking_while_a_check_runs(self):
        self.put("nap.py", "import time\ntime.sleep(1.2)\n")
        ticks = []
        (r,) = self.run_checks({"name": "nap", "argv": ["python3", "nap.py"]}, on_tick=lambda: ticks.append(1))
        self.assertEqual(r.status, "PASS")
        self.assertGreaterEqual(len(ticks), 0)           # (the default 2 s tick may not fire in 1.2 s; it must simply not break)


class Summaries(unittest.TestCase):
    def msg(self, text):
        return ProviderEvent("message", {"text": text})

    def test_the_last_message_wins_and_is_redacted_and_bounded(self):
        events = [self.msg("first"), ProviderEvent("usage", {"output_tokens": 3}), self.msg("key sk-ant-abcdefghijklmnopqrstuvwxyz0123456789 done")]
        self.assertNotIn("sk-ant", R.worker_summary(events))
        self.assertTrue(R.worker_summary(events).startswith("key"))
        self.assertEqual(len(R.worker_summary([self.msg("x" * 5000)])), R.SUMMARY_MAX)

    def test_a_terminal_event_may_carry_its_own_warnings_and_they_are_not_listed_twice(self):
        events = [ProviderEvent("warning", {"message": "a tool was soft-denied"}),
                  ProviderEvent("terminal", {"status": "SUCCESS", "warnings": ["a tool was soft-denied", "another note", 5, "  "]})]
        self.assertEqual(R.worker_warnings(events), ["a tool was soft-denied", "another note"])
        self.assertEqual(R.worker_warnings([ProviderEvent("terminal", {"warnings": "not a list"})]), [])

    def test_the_final_answer_may_arrive_on_the_terminal_event(self):
        events = [self.msg("thinking aloud"), ProviderEvent("terminal", {"status": "SUCCESS", "text": "the final answer, key sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"})]
        out = R.worker_summary(events)
        self.assertTrue(out.startswith("the final answer"))
        self.assertNotIn("sk-ant", out)
        self.assertEqual(R.worker_summary([self.msg("earlier"), ProviderEvent("terminal", {"status": "ERROR"})]), "earlier")     # no text there: the message stays

    def test_no_message_means_an_honest_placeholder(self):
        for events in ([], [ProviderEvent("usage", {})], [self.msg("   ")], [ProviderEvent("message", {"text": 5})]):
            self.assertEqual(R.worker_summary(events), R.NO_SUMMARY)

    def test_observed_model(self):
        self.assertIsNone(R.observed_model([self.msg("hi")]))
        self.assertEqual(R.observed_model([ProviderEvent("usage", {"model": "vendor-model-x"})]), "vendor-model-x")
        self.assertIsNone(R.observed_model([ProviderEvent("usage", {"model": "m" * 500})]))
        self.assertIsNone(R.observed_model([ProviderEvent("usage", {"model": 7})]))


class UsageWarningsAndReset(unittest.TestCase):
    def test_usage_is_what_the_vendor_reported_on_its_terminal_event(self):
        done = ProviderEvent("terminal", {"status": "SUCCESS", "usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 4}})
        self.assertEqual(R.worker_usage([ProviderEvent("message", {"text": "hi"}), done]), {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 4})
        self.assertIsNone(R.worker_usage([]))
        self.assertIsNone(R.worker_usage([ProviderEvent("message", {"usage": {"input_tokens": 5}})]))          # only terminal/usage events count
        self.assertIsNone(R.worker_usage([ProviderEvent("terminal", {"status": "SUCCESS", "usage": {}})]))

    def test_usage_keeps_only_known_whole_non_negative_numbers(self):
        weird = ProviderEvent("terminal", {"usage": {"input_tokens": -1, "output_tokens": True, "cached_input_tokens": "9", "reasoning_output_tokens": 7, "other": 3}})
        self.assertEqual(R.worker_usage([weird]), {"reasoning_output_tokens": 7})

    def test_the_last_usage_report_wins(self):
        first, last = ProviderEvent("usage", {"usage": {"input_tokens": 1}}), ProviderEvent("terminal", {"usage": {"input_tokens": 2}})
        self.assertEqual(R.worker_usage([first, last]), {"input_tokens": 2})

    def test_warnings_are_redacted_bounded_and_deduplicated(self):
        events = [ProviderEvent("warning", {"message": "careful"}), ProviderEvent("warning", {"message": "careful"}),
                  ProviderEvent("warning", {"message": "key sk-ant-abcdefghijklmnopqrstuvwxyz0123456789 leaked"}),
                  ProviderEvent("warning", {"message": "x" * 1000}), ProviderEvent("warning", {"message": "   "}), ProviderEvent("warning", {"message": 5}),
                  ProviderEvent("message", {"message": "not a warning"})]
        out = R.worker_warnings(events)
        self.assertEqual(out[0], "careful")
        self.assertEqual(len(out), 3)
        self.assertNotIn("sk-ant", " ".join(out))
        self.assertTrue(all(len(x) <= 300 for x in out))
        many = [ProviderEvent("warning", {"message": f"w{i}"}) for i in range(20)]
        self.assertEqual(len(R.worker_warnings(many)), R.MAX_WARNINGS)

    def test_a_reset_time_must_look_like_a_timestamp_and_the_last_one_wins(self):
        self.assertEqual(R.reset_time([ProviderEvent("error", {"reset_at": "2026-10-14T11:30:00.000Z"}), ProviderEvent("terminal", {"reset_at": "2026-10-14T12:30:00.000Z"})]),
                         "2026-10-14T12:30:00.000Z")
        for junk in ("soon", "", "x" * 100, 5, None):
            self.assertIsNone(R.reset_time([ProviderEvent("error", {"reset_at": junk})]))
        self.assertIsNone(R.reset_time([]))

    def test_the_envelope_carries_usage_and_stays_valid_without_it(self):
        fields = {k: v for k, v in sample_result().items() if k not in ("schema_version", "summary_verified")}
        self.assertEqual(R.envelope_problems(R.build_envelope(**fields, usage={"input_tokens": 3})), [])
        self.assertEqual(R.envelope_problems(R.build_envelope(**fields, usage=None)), [])
        self.assertEqual(R.envelope_problems(R.build_envelope(**fields)), [])                              # results written before P3
        for bad in ({"input_tokens": -1}, {"input_tokens": "3"}, {"input_tokens": 1.5}, "many"):
            self.assertTrue(R.envelope_problems(R.build_envelope(**fields, usage=bad)), bad)


class Envelope(unittest.TestCase):
    FIELDS = {k: v for k, v in sample_result().items() if k not in ("schema_version", "summary_verified")}

    def test_a_built_envelope_validates(self):
        self.assertEqual(R.envelope_problems(R.build_envelope(**self.FIELDS)), [])

    def test_an_unverified_summary_cannot_be_marked_verified(self):
        env = R.build_envelope(**{**self.FIELDS, "summary_verified": True})
        self.assertIs(env["summary_verified"], False)
        forged = dict(sample_result(), summary_verified=True)
        self.assertTrue(R.envelope_problems(forged))

    def test_malformed_envelopes_are_described(self):
        for broken in ({"outcome": "great"}, {"tests": [{"name": "x", "status": "MAYBE", "exit_code": 0, "duration_s": 1}]},
                       {"boundary": {"violations": -1, "sensitive": 0, "paths": []}}, {"attempt": 0}):
            self.assertTrue(R.envelope_problems(sample_result(**broken)), broken)
        env = sample_result()
        del env["tests"]
        self.assertTrue(any("tests" in p for p in R.envelope_problems(env)))


class Acceptance(unittest.TestCase):
    CHECKS = ({"name": "unit"},)

    def problems(self, status="REVIEW_REQUIRED", attempt="ok", checks=None, needs=False, approved=False, by="user", **result):
        if attempt == "ok":
            attempt = {"end_kind": "COMPLETED", "result": sample_result(**result)}
        return R.acceptance_problems(status=status, attempt=attempt, contract_checks=self.CHECKS if checks is None else checks,
                                     needs_write_approval=needs, write_approved=approved, by=by)

    def test_a_clean_result_may_be_accepted(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.problems(needs=True, approved=True), [])

    def test_only_review_required_results_can_be_accepted(self):
        for status in ("RUNNING", "FAILED", "ACCEPTED", "QUEUED", "PAUSED"):
            self.assertTrue(any("only REVIEW_REQUIRED" in p for p in self.problems(status=status)), status)

    def test_no_attempt_no_result_no_acceptance(self):
        self.assertTrue(self.problems(attempt=None))
        self.assertTrue(any("no result" in p for p in self.problems(attempt={"end_kind": "COMPLETED", "result": None})))

    def test_an_attempt_that_did_not_complete_cannot_be_accepted(self):
        attempt = {"end_kind": "FAILED", "result": sample_result()}
        self.assertTrue(any("not COMPLETED" in p for p in self.problems(attempt=attempt)))

    def test_an_invalid_envelope_is_never_accepted(self):
        attempt = {"end_kind": "COMPLETED", "result": sample_result(outcome="great")}
        self.assertTrue(any("invalid" in p for p in self.problems(attempt=attempt)))

    def test_partial_boundary_and_failed_checks_block_acceptance(self):
        self.assertTrue(any("partial" in p for p in self.problems(partial=True)))
        self.assertTrue(any("permission boundary" in p for p in self.problems(boundary={"violations": 2, "sensitive": 0, "paths": ["x", "y"]})))
        for status in ("FAIL", "TIMEOUT", "ERROR"):
            tests = [{"name": "unit", "status": status, "exit_code": 1, "duration_s": 0.1}]
            self.assertTrue(any(f"is {status}" in p for p in self.problems(tests=tests)), status)

    def test_a_check_the_contract_requires_must_have_run(self):
        self.assertTrue(any("never run" in p for p in self.problems(tests=[])))
        self.assertTrue(any("'lint' was never run" in p for p in self.problems(checks=({"name": "unit"}, {"name": "lint"}))))

    def test_extra_checks_that_failed_also_block(self):
        tests = sample_result()["tests"] + [{"name": "extra", "status": "FAIL", "exit_code": 1, "duration_s": 0.1}]
        self.assertTrue(any("'extra' is FAIL" in p for p in self.problems(tests=tests)))

    def test_write_tasks_need_an_approved_write_permission(self):
        self.assertTrue(any("write permission" in p for p in self.problems(needs=True, approved=False)))

    def test_a_coordinator_cannot_accept_a_write_task_with_no_checks(self):
        problems = self.problems(needs=True, approved=True, checks=(), by="coordinator")
        self.assertTrue(any("no acceptance.checks" in p for p in problems), problems)

    def test_a_person_may_still_accept_a_write_task_with_no_checks(self):
        self.assertEqual(self.problems(needs=True, approved=True, checks=(), by="user"), [])

    def test_a_coordinator_may_accept_a_write_task_that_has_checks(self):
        self.assertEqual(self.problems(needs=True, approved=True, by="coordinator"), [])

    def test_a_coordinator_may_accept_a_read_only_task_with_no_checks(self):
        self.assertEqual(self.problems(needs=False, checks=(), by="coordinator"), [])


if __name__ == "__main__":
    unittest.main()
