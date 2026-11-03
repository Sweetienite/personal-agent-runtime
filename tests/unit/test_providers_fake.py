import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from par.providers.base import (
    Capabilities, FailureClass, ProbeResult, ProviderAdapter, ProviderEvent, ProviderStatus, RunOutcome,
    RunRequest, Tri,
)
from par.providers.fake import FakeProvider, ScriptedProvider, scenarios
from tests.helpers import run_adapter

FILES = {"a.txt": "alpha\n", "sub/b.txt": "beta\n"}


class FakeProviderCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.ws = self.base / "ws"
        self.ws.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def run_scn(self, scenario, allow_write=True, timeout=15.0):
        adapter = ScriptedProvider(scenario)
        req = RunRequest("T-1", 1, "do the thing", self.ws, "fake-model", allow_write)
        return adapter, run_adapter(adapter, req, timeout=timeout)

    def on_disk(self, root=None):
        root = root or self.ws
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


class ProtocolTests(FakeProviderCase):
    def test_conforms_to_the_adapter_protocol(self):
        p = ScriptedProvider(scenarios.success())
        self.assertIsInstance(p, ProviderAdapter)
        self.assertIs(FakeProvider, ScriptedProvider)
        self.assertEqual((p.kind, p.provider_id), ("fake", "fake"))

    def test_argv_is_a_list_and_never_a_shell_line(self):
        p = ScriptedProvider(scenarios.success())
        argv = p.build_argv(RunRequest("T", 1, "x", self.ws, "m", allow_write=True))
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(a, str) for a in argv))
        self.assertEqual(argv[:3], [sys.executable, "-I", "-B"])
        self.assertNotIn("-c", argv)
        self.assertNotIn("--read-only", argv)
        ro = p.build_argv(RunRequest("T", 1, "x", self.ws, "m", allow_write=False))
        self.assertEqual(ro[-1], "--read-only")

    def test_probe_capabilities_and_persistence_are_explicit(self):
        p = ScriptedProvider(scenarios.success())
        probe = p.probe()
        self.assertIsInstance(probe, ProbeResult)
        self.assertEqual(probe.status, ProviderStatus.AVAILABLE)
        caps = p.capabilities()
        self.assertIsInstance(caps, Capabilities)
        self.assertEqual(caps.supports_subscription_auth, Tri.NO)
        self.assertNotIn(Tri.UNKNOWN, vars(caps).values(), "a scripted provider knows everything about itself")
        self.assertEqual(Capabilities().can_write, Tri.UNKNOWN, "default is Unknown, never a guess")
        self.assertEqual(p.persistence_report()["sessions"], "none")

    def test_scenario_json_round_trip(self):
        s = scenarios.cancellable()
        doc = json.loads(s.to_json())
        self.assertEqual((doc["name"], doc["on_sigterm"]), ("cancellable", "cancel"))
        self.assertNotIn("on_sigterm", json.loads(scenarios.success().to_json()))

    def test_deterministic(self):
        _, a = self.run_scn(scenarios.success(FILES))
        _, b = self.run_scn(scenarios.success(FILES))
        self.assertEqual([(e.kind, dict(e.data)) for e in a.events], [(e.kind, dict(e.data)) for e in b.events])


class ParseEventTests(FakeProviderCase):
    def setUp(self):
        super().setUp()
        self.p = ScriptedProvider(scenarios.success())

    def test_known_kinds_keep_structured_fields(self):
        ev = self.p.parse_event('{"type": "error", "code": "quota_exhausted", "http_status": 429}')
        self.assertEqual(ev.kind, "error")
        self.assertEqual(dict(ev.data), {"code": "quota_exhausted", "http_status": 429})

    def test_bad_lines_become_unparsed_without_keeping_the_text(self):
        secret_line = "token=abc123 not json"
        for line in (secret_line, "{broken", "[1, 2]", '{"type": "mystery"}', '"str"'):
            ev = self.p.parse_event(line)
            self.assertEqual(ev.kind, "unparsed", line)
            self.assertEqual(set(ev.data), {"length"})
            self.assertNotIn("abc123", repr(ev))

    def test_blank_line_is_ignored(self):
        self.assertIsNone(self.p.parse_event("   \n"))


class SuccessAndClaims(FakeProviderCase):
    def test_success_writes_files_and_is_clean(self):
        adapter, out = self.run_scn(scenarios.success(FILES))
        self.assertEqual(out.exit_code, 0)
        self.assertEqual(out.terminal().data["status"], "SUCCESS")
        self.assertIsNone(adapter.classify_failure(out))
        self.assertEqual(self.on_disk(), ["a.txt", "sub/b.txt"])
        self.assertEqual((self.ws / "sub" / "b.txt").read_text(), "beta\n")
        self.assertTrue(any(e.kind == "usage" for e in out.events))

    def test_self_reported_test_results_stay_plain_text(self):
        adapter, out = self.run_scn(scenarios.success(claim_tests_pass=True))
        claims = [e for e in out.events if "PASS" in str(e.data)]
        self.assertEqual([e.kind for e in claims], ["message"])
        self.assertEqual({e.kind for e in out.events} & {"tests", "validation", "result"}, set())
        self.assertIsNone(adapter.classify_failure(out), "a clean run is still only 'no provider-level failure'")


class FailureClassification(FakeProviderCase):
    def assertFailure(self, scenario, expected, exit_code, **kw):
        adapter, out = self.run_scn(scenario, **kw)
        self.assertEqual(adapter.classify_failure(out), expected, scenario.name)
        self.assertEqual(out.exit_code, exit_code, scenario.name)
        return out

    def test_quota_exhausted(self):
        out = self.assertFailure(scenarios.quota_exhausted(), FailureClass.QUOTA_EXHAUSTED, 1)
        self.assertEqual(out.terminal().data["status"], "ERROR")

    def test_rate_limited_keeps_retry_after(self):
        out = self.assertFailure(scenarios.rate_limited(retry_after=90), FailureClass.RATE_LIMITED, 1)
        self.assertEqual([e.data["retry_after"] for e in out.events if e.kind == "error"], [90])

    def test_bare_429_is_a_rate_limit_not_an_exhausted_quota(self):
        self.assertFailure(scenarios.bare_429(), FailureClass.RATE_LIMITED, 1)

    def test_auth_and_model(self):
        self.assertFailure(scenarios.auth_required(), FailureClass.AUTH_REQUIRED, 1)
        self.assertFailure(scenarios.model_unavailable(), FailureClass.MODEL_UNAVAILABLE, 1)

    def test_permission_denied_hard_and_soft(self):
        self.assertFailure(scenarios.permission_denied(), FailureClass.PERMISSION_BLOCKED, 1)
        # exit code 0 with an ERROR terminal: must still be a failure
        out = self.assertFailure(scenarios.permission_denied(soft=True), FailureClass.PERMISSION_BLOCKED, 0)
        self.assertEqual(out.terminal().data["status"], "ERROR")

    def test_read_only_request_cannot_write(self):
        self.assertFailure(scenarios.success(FILES), FailureClass.PERMISSION_BLOCKED, 1, allow_write=False)
        self.assertEqual(self.on_disk(), [], "no file may appear when the request was read-only")

    def test_crash_keeps_the_stderr_tail_and_files(self):
        out = self.assertFailure(scenarios.crash(code=139, files=FILES), FailureClass.EXECUTION_FAILED, 139)
        self.assertIn("segmentation fault", out.stderr_tail)
        self.assertEqual(self.on_disk(), ["a.txt", "sub/b.txt"])

    def test_truncated_stream_is_unknown_not_success(self):
        out = self.assertFailure(scenarios.truncated(FILES), FailureClass.UNKNOWN, 0)
        self.assertIsNone(out.terminal())

    def test_garbage_lines_do_not_hide_a_real_terminal(self):
        adapter, out = self.run_scn(scenarios.garbage())
        self.assertEqual([e.kind for e in out.events].count("unparsed"), 3)
        self.assertEqual(out.terminal().data["status"], "SUCCESS")
        self.assertIsNone(adapter.classify_failure(out))
        self.assertTrue(all(set(e.data) == {"length"} for e in out.events if e.kind == "unparsed"))

    def test_midrun_write_then_quota_keeps_the_partial_work(self):
        adapter, out = self.run_scn(scenarios.partial_write_then_quota(3))
        self.assertEqual(adapter.classify_failure(out), FailureClass.QUOTA_EXHAUSTED)
        self.assertEqual(self.on_disk(), ["part_1.txt", "part_2.txt", "part_3.txt"])
        self.assertEqual((self.ws / "part_2.txt").read_text(), "chunk 2\n")


class TimeoutAndCancel(FakeProviderCase):
    def test_timeout_kills_the_process_and_keeps_midrun_files(self):
        started = time.monotonic()
        adapter, out = self.run_scn(scenarios.timeout(sleep=30, files=FILES), timeout=0.6)
        self.assertLess(time.monotonic() - started, 10, "the hung process must not be waited for")
        self.assertTrue(out.timed_out)
        self.assertEqual(out.signal, 15, "SIGTERM was sent through request_cancel")
        self.assertIsNone(out.terminal())
        self.assertEqual(adapter.classify_failure(out), FailureClass.UNKNOWN)
        self.assertEqual(self.on_disk(), ["a.txt", "sub/b.txt"], "files written before the hang survive")

    def test_cancel_request_yields_a_canceled_terminal(self):
        adapter, out = self.run_scn(scenarios.cancellable(), timeout=0.6)
        self.assertTrue(out.timed_out)
        self.assertEqual((out.exit_code, out.signal), (130, None))
        self.assertEqual(out.terminal().data["status"], "CANCELED")
        self.assertEqual(adapter.classify_failure(out), FailureClass.UNKNOWN)

    def test_request_cancel_on_a_finished_process_is_a_noop(self):
        import subprocess
        p = ScriptedProvider(scenarios.success())
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        p.request_cancel(proc)  # must not raise


class WorkspaceContainment(FakeProviderCase):
    def test_relative_escape_is_refused(self):
        adapter, out = self.run_scn(scenarios.escape_attempt("../outside.txt"))
        self.assertEqual(adapter.classify_failure(out), FailureClass.PERMISSION_BLOCKED)
        self.assertEqual(self.on_disk(self.base), [])

    def test_absolute_path_is_refused(self):
        target = self.base / "abs.txt"
        adapter, out = self.run_scn(scenarios.escape_attempt(str(target)))
        self.assertEqual(adapter.classify_failure(out), FailureClass.PERMISSION_BLOCKED)
        self.assertFalse(target.exists())

    def test_symlink_escape_is_refused(self):
        outside = self.base / "outside_dir"
        outside.mkdir()
        os.symlink(outside, self.ws / "link")
        adapter, out = self.run_scn(scenarios.escape_attempt("link/x.txt"))
        self.assertEqual(adapter.classify_failure(out), FailureClass.PERMISSION_BLOCKED)
        self.assertEqual(self.on_disk(outside), [])

    def test_child_environment_is_minimal(self):
        # the helper starts the child with PATH only: nothing from the caller's environment is inherited
        from tests.helpers import minimal_env
        self.assertEqual(set(minimal_env()), {"PATH"})


class ClassifierOnSyntheticOutcomes(unittest.TestCase):
    """The classifier is a pure function of the outcome, so its edge cases need no process."""

    def setUp(self):
        self.p = ScriptedProvider(scenarios.success())

    def outcome(self, events, exit_code=0, **kw):
        return RunOutcome(exit_code=exit_code, events=[ProviderEvent(k, d) for k, d in events], **kw)

    def test_success_terminal_with_error_event_is_a_contradiction(self):
        o = self.outcome([("error", {"message": "x"}), ("terminal", {"status": "SUCCESS"})])
        self.assertEqual(self.p.classify_failure(o), FailureClass.EXECUTION_FAILED)

    def test_success_terminal_with_nonzero_exit_is_a_contradiction(self):
        o = self.outcome([("terminal", {"status": "SUCCESS"})], exit_code=2)
        self.assertEqual(self.p.classify_failure(o), FailureClass.EXECUTION_FAILED)

    def test_error_terminal_without_a_recognised_cause(self):
        o = self.outcome([("error", {"code": "weird"}), ("terminal", {"status": "ERROR"})], exit_code=1)
        self.assertEqual(self.p.classify_failure(o), FailureClass.EXECUTION_FAILED)

    def test_structured_cause_beats_http_status(self):
        o = self.outcome([("error", {"code": "quota_exhausted", "http_status": 429})], exit_code=1)
        self.assertEqual(self.p.classify_failure(o), FailureClass.QUOTA_EXHAUSTED)

    def test_nothing_known(self):
        self.assertEqual(self.p.classify_failure(self.outcome([], exit_code=None)), FailureClass.UNKNOWN)
        self.assertEqual(self.p.classify_failure(self.outcome([], exit_code=0)), FailureClass.UNKNOWN)

    def test_nonzero_exit_without_terminal(self):
        self.assertEqual(self.p.classify_failure(self.outcome([], exit_code=1)), FailureClass.EXECUTION_FAILED)
        self.assertEqual(self.p.classify_failure(self.outcome([], exit_code=-9)), FailureClass.EXECUTION_FAILED)

    def test_timeout_always_wins(self):
        o = self.outcome([("terminal", {"status": "SUCCESS"})], timed_out=True)
        self.assertEqual(self.p.classify_failure(o), FailureClass.UNKNOWN)

    def test_last_terminal_event_is_the_one_that_counts(self):
        o = self.outcome([("terminal", {"status": "ERROR"}), ("terminal", {"status": "SUCCESS"})])
        self.assertEqual(o.terminal().data["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
