"""The Kernel driving the REAL Codex adapter against a fake `codex` program (a real child process, real pipes, real ledger).

What this proves: the pieces fit - prompt through stdin, sandbox follows the task, clean environment, usage and warnings on
the result, failures classified into the right states and health records, nothing believed without the Kernel's own check.
What it cannot prove: how the real CLI words things. That is what the opt-in real tests and docs/CODEX.md are for.
"""
import hashlib
import json
import os
import signal
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from par.core.clock import parse_iso
from par.execution import subprocesses as sp
from par.providers.registry import AdapterRegistry
from tests import codex_samples as S
from tests.fake_codex import BASE_ENV, FakeCodex
from tests.kernel_fixture import BUGGY_CALC, FIXED_CALC, KernelWorld, r1_contract
from tests.world import sample_contract

NOISE = {"PWD", "SHLVL", "_", "OLDPWD", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}                 # what /bin/sh adds to the fake's own environment
EXPECTED_ENV = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "NO_COLOR", "CI", "CODEX_HOME"}
LEAKS = {"OPENAI_API_KEY": "sk-test-not-real-1", "CODEX_API_KEY": "sk-test-not-real-2", "ANTHROPIC_API_KEY": "sk-test-not-real-3",
         "GITHUB_TOKEN": "ghp_testnotreal", "AWS_SECRET_ACCESS_KEY": "test-not-real"}


class CodexWorld(KernelWorld):
    default_files = None
    environ = None

    def registry(self):
        if not hasattr(self, "fake"):
            self.fake = FakeCodex(self.base / "fake_codex")
        reg = AdapterRegistry()
        reg.register("codex", lambda: self.fake.adapter(environ=self.environ))
        reg.register("antigravity", self.agy.factory("antigravity"))
        return reg

    def play(self, events, *, writes=None, exit=0, **extra):
        self.fake.configure(exec={"events": events, "writes": writes or {}, "exit": exit, **extra})

    def run_new(self, contract=None, **over):
        tid = self.queued(contract if contract is not None else r1_contract(), **over)
        return tid, self.run_task(tid)

    def last_call(self):
        calls = self.fake.exec_calls()
        self.assertTrue(calls, "the fake codex was never asked to run")
        return calls[-1]


class ReadOnlyTask(CodexWorld):
    def test_a_read_only_task_end_to_end(self):
        self.play(S.ok_run("add() subtracts instead of adding.", S.command("cat app/calc.py")))
        before = self.project_digest()
        tid, report = self.run_new()
        self.assertEqual((report.status, report.attempt), ("REVIEW_REQUIRED", 1))
        call = self.last_call()
        argv = call["argv"]
        self.assertEqual(argv[0], "exec")
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        self.assertEqual(argv[argv.index("-m") + 1], "m-code")
        workspace = self.kernel.store.get_attempt(tid, 1)["workspace_path"]
        self.assertEqual(argv[argv.index("-C") + 1], workspace)
        self.assertEqual(call["cwd"], workspace)
        res = self.result(tid)
        self.assertEqual((res["provider"], res["worker"], res["model_requested"], res["model_observed"]), ("codex", "w_code", "m-code", None))
        self.assertEqual((res["terminal_state"], res["outcome"], res["summary"], res["summary_verified"]),
                         ("SUCCESS", "completed", "add() subtracts instead of adding.", False))
        self.assertEqual(res["usage"], S.USAGE)
        self.assertEqual((res["diff_artifact"], res["boundary"]["violations"]), (None, 0))
        self.assertEqual(self.project_digest(), before)
        self.assertEqual(len(self.fake.exec_calls()), 1)

    def test_the_whole_prompt_arrives_on_standard_input_and_nowhere_else(self):
        self.play(S.ok_run())
        tid, _ = self.run_new()
        call = self.last_call()
        prompt = (self.attempt_dir(tid) / "prompt.txt").read_text()
        self.assertEqual(call["stdin"], prompt)
        self.assertEqual(hashlib.sha256(call["stdin"].encode()).hexdigest(), self.kernel.store.get_attempt(tid, 1)["prompt_sha256"])
        self.assertNotIn("Fix add()", " ".join(call["argv"]))
        self.assertEqual(call["argv"][-1], "-")

    def test_the_child_sees_a_clean_environment_and_no_credentials_of_ours(self):
        self.environ = {**BASE_ENV, **LEAKS, "HTTPS_PROXY": "http://proxy.invalid:3128"}
        self.play(S.ok_run())
        with mock.patch.dict(os.environ, LEAKS):
            tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        env = self.last_call()["env"]
        self.assertEqual(set(env) - NOISE, EXPECTED_ENV | {"HTTPS_PROXY"})
        self.assertEqual(env["HOME"], str(self.attempt_dir(tid) / "home"))               # a private HOME, not the user's
        self.assertEqual(env["CODEX_HOME"], str(self.fake.home))
        for name, value in LEAKS.items():
            self.assertNotIn(value, json.dumps(env), name)

    def test_an_attempt_leaves_the_vendor_stream_as_a_redacted_private_log(self):
        self.play(S.ok_run("the key is sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 oops") + [{"$stderr": "warn: token ghp_abcdefghijklmnopqrstuvwxyz0123456789"}])
        tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        d = self.attempt_dir(tid)
        for name in ("stdout.log", "stderr.log", "result.json"):
            text = (d / name).read_text()
            self.assertNotIn("AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", text, name)
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", text, name)
            self.assertEqual(os.stat(d / name).st_mode & 0o777, 0o600)
        self.assertNotIn("AbCdEf", self.result(tid)["summary"])

    def test_garbage_lines_are_counted_and_a_warning_is_shown_but_a_good_run_stays_good(self):
        events = S.started() + [{"$raw": "this is not json"}, S.command("sudo x", status="declined"), S.message("done"), S.completed()]
        self.play(events)
        tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        issues = " | ".join(self.result(tid)["remaining_issues"])
        self.assertIn("1 output line(s) could not be parsed", issues)
        self.assertIn("worker run: a command was declined", issues)

    def test_a_read_only_task_that_writes_anyway_is_caught_by_comparison(self):
        self.play(S.ok_run("fixed it"), writes={"app/calc.py": FIXED_CALC})
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["boundary"]["violations"], res["boundary"]["paths"]), ("boundary", 1, ["app/calc.py"]))
        self.assertIsNone(res["diff_artifact"])

    def test_a_text_only_task_runs_without_any_project_files(self):
        self.play(S.ok_run("42"))
        tid, report = self.run_new(r1_contract(risk="R0"))
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        argv = self.last_call()["argv"]
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        self.assertEqual(list(Path(self.kernel.store.get_attempt(tid, 1)["workspace_path"]).iterdir()), [])


class WorkingCopy(CodexWorld):
    """Codex adds a trust entry for every writable git workspace to the user's own ~/.codex/config.toml (measured): it is given none."""

    def test_the_workers_copy_has_every_file_but_no_git_repository(self):
        for contract, files in ((r1_contract(), None), (sample_contract(), {"app/calc.py": FIXED_CALC})):
            self.play(S.ok_run("done"), writes=files or {})
            tid, report = self.run_new(contract)
            workspace = Path(self.kernel.store.get_attempt(tid, 1)["workspace_path"])
            self.assertFalse((workspace / ".git").exists(), contract["risk"])
            self.assertTrue((workspace / "app" / "calc.py").is_file())
            self.assertTrue((workspace / "AGENTS.md").is_file())
            self.assertEqual(self.fake.exec_calls()[-1]["cwd"], str(workspace))

    def test_a_write_task_is_judged_and_patched_exactly_as_before(self):
        self.play(S.ok_run("fixed"), writes={"app/calc.py": FIXED_CALC})
        tid, report = self.run_new(sample_contract())
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        res = self.result(tid)
        self.assertEqual((res["diff_stat"], res["boundary"]["violations"]), ({"files": 1, "additions": 1, "deletions": 1}, 0))
        self.assertIn(b"+    return a + b", self.kernel.diff(tid))

    def test_a_worker_that_makes_its_own_repository_changes_nothing_the_kernel_believes(self):
        self.play(S.ok_run("fixed"), writes={"app/calc.py": FIXED_CALC, ".git/config": "[core]\n\tfsmonitor = touch /tmp/never\n"})
        tid, report = self.run_new(sample_contract())
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        self.assertNotIn(b"fsmonitor", self.kernel.diff(tid))


class WriteTask(CodexWorld):
    def test_a_file_changing_task_end_to_end_and_nothing_is_applied(self):
        self.play(S.ok_run("fixed add()", S.file_change("app/calc.py"), S.command()), writes={"app/calc.py": FIXED_CALC})
        before = self.project_digest()
        tid, report = self.run_new(sample_contract())
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        argv = self.last_call()["argv"]
        self.assertEqual(argv[argv.index("-s") + 1], "workspace-write")
        res = self.result(tid)
        self.assertEqual((res["diff_stat"], res["boundary"]["violations"], res["partial"]), ({"files": 1, "additions": 1, "deletions": 1}, 0, False))
        self.assertEqual([(t["name"], t["status"]) for t in res["tests"]], [("unit", "PASS")])
        self.assertIn(b"+    return a + b", self.kernel.diff(tid))
        self.assertEqual(self.kernel.accept(tid)["status"], "ACCEPTED")
        self.assertEqual(self.project_digest(), before)                                  # ACCEPTED is not APPLIED
        self.assertEqual((self.project_path / "app" / "calc.py").read_text(), BUGGY_CALC)

    def test_a_confident_wrong_answer_is_not_believed(self):
        self.play(S.ok_run("All tests pass, I ran them myself."), writes={"app/calc.py": "def add(a, b):\n    return a * b\n"})
        tid, report = self.run_new(sample_contract())
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["outcome"], res["summary_verified"]), ("validation", "failed", False))
        self.assertEqual([(t["name"], t["status"]) for t in res["tests"]], [("unit", "FAIL")])
        self.assertIn("All tests pass", res["summary"])                                  # kept for a person, marked unverified
        self.assertTrue(res["partial"])                                                  # the patch exists, but it is not trusted

    def test_a_failed_attempt_keeps_the_files_it_produced(self):
        self.play(S.ok_run("done"), writes={"app/calc.py": "def add(a, b):\n    return a * b\n"})
        tid, _ = self.run_new(sample_contract())
        d = self.attempt_dir(tid)
        self.assertEqual((d / "workspace" / "app" / "calc.py").read_text(), "def add(a, b):\n    return a * b\n")
        self.assertTrue((d / "work.patch").is_file())
        self.assertTrue((d / "stdout.log").is_file())

    def test_a_change_outside_the_allowed_paths_is_a_boundary_violation(self):
        self.play(S.ok_run("done"), writes={"app/calc.py": FIXED_CALC, "README.md": "# changed\n"})
        tid, report = self.run_new(sample_contract())
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["boundary"]["paths"]), ("boundary", ["README.md"]))

    def test_a_run_that_changes_nothing_is_reported_as_such(self):
        self.play(S.ok_run("I looked and changed nothing"))
        tid, report = self.run_new(sample_contract())
        self.assertEqual(report.status, "FAILED")                                       # the unit check still fails on the untouched bug
        self.assertEqual(self.result(tid)["tests"][0]["status"], "FAIL")


class Failures(CodexWorld):
    def test_the_usage_limit_blocks_the_provider_until_the_time_the_vendor_gave(self):
        self.play(S.failed_run(S.reset_in(2)), exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "QUOTA_EXHAUSTED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["failure_class"], att["exit_code"]), ("FAILED", "QUOTA_EXHAUSTED", 1))
        row = self.kernel.store.health_rows()[0]
        self.assertEqual((row["provider_id"], row["model"], row["status"]), ("codex", "", "QUOTA_EXHAUSTED"))
        until = parse_iso(row["until"])
        wait = (until - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(90 * 60 < wait < 150 * 60, wait)                                 # about two hours: what the message said, not a guess
        verdict = self.kernel.health.verdict("codex", "any-model")
        self.assertEqual((verdict.usable, verdict.scope), (False, "provider"))
        d = self.kernel.describe(tid)
        self.assertEqual([a["kind"] for a in d["pending_approvals"]], ["FALLBACK_SWITCH"])  # offered, never taken on its own
        self.assertEqual(self.agy.calls, 0)

    def test_a_usage_limit_without_a_readable_time_holds_until_a_person_clears_it(self):
        self.play(S.failed_run("You've hit your usage limit. Try again later."), exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "QUOTA_EXHAUSTED")
        row = self.kernel.store.health_rows()[0]
        self.assertIsNone(row["until"])
        self.assertIn("until cleared", self.kernel.health.verdict("codex", "m-code").describe())

    def test_while_blocked_no_new_run_reaches_the_cli(self):
        self.play(S.failed_run(S.reset_in(2)), exit=1)
        self.run_new()
        self.assertEqual(len(self.fake.exec_calls()), 1)
        self.play(S.ok_run())
        tid2 = self.queued(r1_contract())
        report = self.run_task(tid2)
        self.assertNotEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual(len(self.fake.exec_calls()), 1)                                 # the blocked provider was not called again
        workers = {w["worker"]: w for w in self.kernel.health_report()["workers"]}
        self.assertFalse(workers["w_code"]["usable_read_only"])
        self.assertIn("QUOTA_EXHAUSTED", " ".join(workers["w_code"]["reasons"]))

    def test_once_a_person_says_they_are_ready_the_next_run_succeeds_and_clears_the_record(self):
        self.play(S.failed_run(S.reset_in(2)), exit=1)
        tid, _ = self.run_new()
        self.play(S.ok_run())
        self.kernel.retry(tid, force=True)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(self.kernel.store.health_rows(), [])

    def test_a_rate_limit_cools_down_one_model_only(self):
        self.play(S.failed_run(S.RATE_LIMIT), exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "RATE_LIMITED")
        row = self.kernel.store.health_rows()[0]
        self.assertEqual((row["model"], row["status"]), ("m-code", "RATE_LIMITED"))
        self.assertTrue(self.kernel.health.verdict("codex", "another-model").usable)

    def test_a_login_problem_stops_the_provider_until_a_person_signs_in(self):
        self.play(S.failed_run(S.AUTH), exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "AUTH_REQUIRED")
        self.assertEqual(self.kernel.store.health_rows()[0]["model"], "")
        self.assertIn("sign in with the vendor's official client", " ".join(e["reason"] or "" for e in self.kernel.events(tid) if e["type"] == "STATUS_CHANGED"))

    def test_an_unknown_model_blocks_that_worker(self):
        self.play(S.failed_run(S.MODEL), exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "BLOCKED_NO_WORKER")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["failure_class"], "MODEL_UNAVAILABLE")

    def test_a_dropped_connection_is_interrupted_and_waits_for_a_person(self):
        self.play(S.failed_run(S.TRANSIENT), exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "INTERRUPTED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["failure_class"], "TRANSIENT_ERROR")

    def test_an_unrecognised_failure_is_a_plain_failure_never_a_guess_about_quota(self):
        self.play(S.failed_run("Codex ran out of room in the model's context window."), exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.kernel.store.health_rows(), [])

    def test_a_stream_that_just_ends_is_never_success(self):
        self.play(S.started() + [S.message("I think I am done")], exit=0)
        tid, report = self.run_new()
        self.assertEqual(report.status, "INTERRUPTED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["failure_class"], att["exit_code"]), ("INTERRUPTED", "UNKNOWN", 0))
        self.assertEqual(self.result(tid)["terminal_state"], "NONE")

    def test_a_truncated_stream_with_a_crash_is_not_success(self):
        self.play(S.started() + [{"$raw": '{"type": "item.comp'}, {"$exit": 101}])
        tid, report = self.run_new()
        self.assertNotEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["exit_code"], 101)

    def test_success_events_with_a_failing_exit_code_do_not_pass(self):
        self.play(S.ok_run(), exit=3)
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")

    def test_an_error_inside_a_run_that_then_finishes_is_still_a_success(self):
        events = S.started() + [{"type": "error", "message": "Reconnecting... 1/5 (stream disconnected before completion)"}, S.message("done"), S.completed()]
        self.play(events)
        tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")


class Refusals(CodexWorld):
    def test_unprovable_isolation_stops_the_attempt_before_anything_is_spawned(self):
        self.fake.configure(ignore_overrides=True)
        self.play(S.ok_run())
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.fake.exec_calls(), [])
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["stage"], att["pid"]), ("FAILED", "preflight", None))
        self.assertIn("extra tools are switched off", self.kernel.describe(tid)["last_failure"])

    def test_a_login_that_became_an_api_key_stops_the_attempt_before_anything_is_spent(self):
        self.fake.configure(login=["Logged in using an API key - sk-...abcd", 0])
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.fake.exec_calls(), [])
        self.assertIn("API key", self.kernel.describe(tid)["last_failure"])

    def test_a_missing_executable_is_a_clean_refusal(self):
        os.unlink(self.fake.exe)
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertIn("no executable at", self.kernel.describe(tid)["last_failure"])

    def test_an_untrusted_executable_never_runs(self):
        with mock.patch("par.providers.codex.CodexAdapter.signature_problem", return_value="the executable is not signed by OpenAI (team: none)"):
            tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.fake.calls(), [])                                          # not even a probe ran the unverified program


class Interruption(CodexWorld):
    def start_and_wait(self, tid):
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(tid))
        self.assertTrue(self.wait_for(lambda: other.describe(tid)["status"] == "RUNNING"))
        self.assertTrue(self.wait_for(lambda: bool(self.fake.exec_calls())))
        return other, t

    def test_cancel_interrupts_the_cli_politely(self):
        self.play(S.started(), hang=True)
        tid = self.queued(r1_contract())
        other, t = self.start_and_wait(tid)
        t0 = time.monotonic()
        other.cancel(tid, reason="stop")
        t.join(20)
        self.assertEqual(t.box["value"].status, "CANCELED")
        self.assertLess(time.monotonic() - t0, 15)
        att = other.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["exit_code"]), ("CANCELED", 130))             # Ctrl-C, handled: not a kill
        self.assertEqual(other.store.locks(), [])

    def test_a_cli_that_ignores_ctrl_c_is_stopped_anyway(self):
        self.play(S.started(), hang=True, sigint="ignore")
        tid = self.queued(r1_contract())
        other, t = self.start_and_wait(tid)
        pgid = other.store.get_attempt(tid, 1)["pgid"]
        other.cancel(tid)
        t.join(20)
        self.assertEqual(t.box["value"].status, "CANCELED")
        self.assertFalse(sp.group_alive(pgid))
        self.assertIn(other.store.get_attempt(tid, 1)["signal"], (signal.SIGTERM, signal.SIGKILL))

    def test_a_silent_cli_is_stopped_by_the_idle_timeout_and_is_not_called_a_success(self):
        self.play(S.started(), hang=True)
        contract = r1_contract(timeouts={"startup": 60, "idle": 1, "total": 120})            # (generous: a loaded machine starts the fake slowly)
        tid = self.queued(contract)
        report = self.run_task(tid)
        self.assertEqual(report.status, "INTERRUPTED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["timeout_kind"], "idle")


class HealthResetTime(CodexWorld):
    fake_clock = True

    def record(self, reset_at):
        with self.kernel.store.tx():
            self.kernel.health.record_failure("QUOTA_EXHAUSTED", "codex", "m-code", reset_at=reset_at)
        return self.kernel.store.health_rows()[0]["until"]

    def test_a_reset_time_in_the_future_is_used(self):
        when = self.clock.now() + timedelta(hours=3)
        self.assertEqual(parse_iso(self.record(when.isoformat())), when)

    def test_a_time_in_the_past_or_absurdly_far_or_unreadable_is_not_used(self):
        for bad in ((self.clock.now() - timedelta(minutes=1)).isoformat(), (self.clock.now() + timedelta(days=36)).isoformat(),
                    "tomorrow", "", "2026-13-45T99:99:99Z", None):
            self.assertIsNone(self.record(bad), bad)

    def test_only_quota_blocks_use_a_vendor_reset_time(self):
        when = (self.clock.now() + timedelta(hours=3)).isoformat()
        with self.kernel.store.tx():
            self.kernel.health.record_failure("AUTH_REQUIRED", "codex", "m-code", reset_at=when)
        self.assertIsNone(self.kernel.store.health_rows()[0]["until"])                       # a login problem has no vendor clock


if __name__ == "__main__":
    unittest.main()
