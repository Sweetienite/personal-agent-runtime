"""The Kernel driving the REAL Antigravity adapter against a fake `agy` program (a real child process, real pipes, real ledger).

What this proves: the pieces fit - prompt through stdin, the permission preset follows the task and reaches the CLI through the
Runtime-written settings.json in the attempt's private HOME, a clean environment, soft denials surfaced, a CLI that runs wider than
asked is not trusted, failures classified into the right states and health records, routing between Antigravity and Codex follows
the profile, nothing believed without the Kernel's own check.
What it cannot prove: how the real CLI words things or whether it honours its documented behaviour. That is what docs/AGY.md §12 and
the real smoke test (tests/tools/smoke_agy.py) are for.
"""
import hashlib
import json
import os
import signal
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from par.config import ProjectEntry, build_config
from par.execution import subprocesses as sp
from par.providers import antigravity as agy
from par.providers.registry import AdapterRegistry
from tests import agy_samples as S
from tests.fake_agy import BASE_ENV, FakeAgy
from tests.kernel_fixture import BUGGY_CALC, FIXED_CALC, KernelWorld, r1_contract
from tests.world import sample_contract

AGY_MODEL = "gemini-3.8-flash-medium"
NOISE = {"PWD", "SHLVL", "_", "OLDPWD", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}               # what /bin/sh adds to the fake's own environment
EXPECTED_ENV = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "NO_COLOR", "CI", "AGY_CLI_DISABLE_AUTO_UPDATE"}
LEAKS = {"GEMINI_API_KEY": "sk-test-not-real-1", "GOOGLE_API_KEY": "sk-test-not-real-2", "AGY_ADC_AUTH": "adc-leak-marker",
         "GOOGLE_GEMINI_BASE_URL": "https://evil.invalid", "OPENAI_API_KEY": "sk-test-not-real-3", "GITHUB_TOKEN": "ghp_testnotreal",
         "SSH_CONNECTION": "10.0.0.1 1 10.0.0.2 22"}


def agy_config(policy="ask"):
    """Role `analysis` = Antigravity then Codex; role `code` = Codex then Antigravity (the profile the user is expected to write)."""
    profiles = {
        "schema_version": 1, "active_profile": "default",
        "workers": {
            "w_code": {"provider": "codex", "model": "m-code", "capabilities": ["read", "edit", "review"]},
            "w_agy": {"provider": "antigravity", "model": AGY_MODEL, "capabilities": ["read", "edit"]},
        },
        "profiles": {"default": {
            "coordinator": "claude_desktop", "default_worker": "w_code", "max_parallel_workers": 1,
            "worker_failure_policy": policy, "additional_paid_credits": False,
            "roles": {"code": {"primary": "w_code", "fallback": ["w_agy"]}, "analysis": {"primary": "w_agy", "fallback": ["w_code"]}},
        }},
    }
    providers = {"schema_version": 1, "providers": {"codex": {"enabled": True}, "antigravity": {"enabled": True}}}
    result = build_config(profiles, providers)
    assert result.ok, result.errors
    return result.config


def analysis(**over):
    return r1_contract(role="analysis", **over)


def healthy(text="add() subtracts instead of adding.\n", *middle):
    """The stream of a healthy run. `init` is produced by the fake itself: it reports the mode that settings.json asks for."""
    return [{"$init": True}, S.user_step(), *middle, S.delta(text, "DONE"), S.checkpoint(), S.result(text)]


class AgyWorld(KernelWorld):
    default_files = None
    environ = None

    def config(self):
        return agy_config(self.policy)

    def registry(self):
        if not hasattr(self, "fake"):
            self.fake = FakeAgy(self.base / "fake_agy", models=[AGY_MODEL])
        reg = AdapterRegistry()
        reg.register("codex", self.codex.factory("codex"))
        reg.register("antigravity", lambda: self.fake.adapter(environ=self.environ))
        return reg

    def play(self, events=None, **extra):
        self.fake.configure(run={**({"events": events} if events is not None else {}), **extra})

    def run_new(self, contract=None, **over):
        tid = self.queued(contract if contract is not None else analysis(), **over)
        return tid, self.run_task(tid)

    def last_run(self):
        runs = self.fake.runs()
        self.assertTrue(runs, "the fake agy was never asked to run")
        return runs[-1]

    def issues(self, tid):
        return " | ".join(self.result(tid)["remaining_issues"])


class ReadOnlyAnalysis(AgyWorld):
    def test_a_read_only_analysis_task_end_to_end(self):
        self.play(healthy("add() subtracts instead of adding.\n", S.tool("run_command", params={"CommandLine": "cat app/calc.py"})))
        before = self.project_digest()
        tid, report = self.run_new()
        self.assertEqual((report.status, report.attempt), ("REVIEW_REQUIRED", 1))
        call = self.last_run()
        self.assertEqual(call["argv"], ["--input-format", "stream-json", "--output-format", "stream-json", "--model", AGY_MODEL,
                                        "--sandbox", "--print-timeout", "24h"])
        workspace = self.kernel.store.get_attempt(tid, 1)["workspace_path"]
        self.assertEqual(call["cwd"], workspace)
        self.assertEqual(call["home_settings"]["toolPermission"], "strict")                    # read-only task: the narrowest preset
        self.assertNotIn("permissions", call["home_settings"])                                 # ... and not one allow rule
        res = self.result(tid)
        self.assertEqual((res["provider"], res["worker"], res["model_requested"], res["model_observed"]), ("antigravity", "w_agy", AGY_MODEL, AGY_MODEL))
        self.assertEqual((res["terminal_state"], res["outcome"], res["summary"], res["summary_verified"]),
                         ("SUCCESS", "completed", "add() subtracts instead of adding.", False))
        self.assertEqual(res["usage"], S.USAGE_NORMALIZED)
        self.assertEqual((res["diff_artifact"], res["boundary"]["violations"]), (None, 0))
        self.assertEqual(self.project_digest(), before)
        self.assertEqual((len(self.fake.runs()), self.codex.calls), (1, 0))

    def test_the_whole_prompt_arrives_on_standard_input_as_one_user_event_and_nowhere_else(self):
        self.play(healthy())
        tid, _ = self.run_new()
        call = self.last_run()
        prompt = (self.attempt_dir(tid) / "prompt.txt").read_text()
        self.assertEqual(call["stdin"], prompt)
        self.assertEqual(hashlib.sha256(call["stdin"].encode()).hexdigest(), self.kernel.store.get_attempt(tid, 1)["prompt_sha256"])
        self.assertNotIn("Fix add()", " ".join(call["argv"]))
        self.assertNotIn("-p", call["argv"])

    def test_the_child_sees_a_clean_environment_and_no_credentials_of_ours(self):
        self.environ = {**BASE_ENV, **LEAKS, "HTTPS_PROXY": "http://proxy.invalid:3128"}
        self.play(healthy())
        with mock.patch.dict(os.environ, LEAKS):
            tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        env = self.last_run()["env"]
        self.assertEqual(set(env) - NOISE, EXPECTED_ENV | {"HTTPS_PROXY"})
        self.assertEqual(env["HOME"], str(self.attempt_dir(tid) / "home"))                      # a private HOME, not the user's
        self.assertEqual(env["AGY_CLI_DISABLE_AUTO_UPDATE"], "true")
        for name, value in LEAKS.items():
            self.assertNotIn(value, json.dumps(env), name)
        for call in self.fake.offline_calls():                                                  # the probes were just as clean
            self.assertEqual(call["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"], "true")
            self.assertNotIn("GEMINI_API_KEY", call["env"])
            self.assertNotEqual(call["env"]["HOME"], str(Path.home()))

    def test_the_attempts_private_home_holds_the_runtimes_settings_and_the_keychain_link_exists_only_while_agy_runs(self):
        sentinel = self.fake.keychains / "login.keychain-db"
        sentinel.write_text("not a real keychain")
        self.play(healthy())
        tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        home = self.attempt_dir(tid) / "home"
        settings = home / ".gemini" / "antigravity-cli" / "settings.json"
        self.assertEqual(os.stat(settings).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(settings.read_text())["toolPermission"], "strict")
        self.assertEqual(json.loads(settings.read_text()), self.last_run()["home_settings"])
        if sys.platform == "darwin":
            self.assertEqual(self.last_run()["keychains_link"], str(self.fake.keychains))      # the link was there WHILE agy ran ...
        self.assertFalse(os.path.lexists(home / "Library"))                                    # ... and is gone as soon as it ended: nothing in the
        self.assertEqual(sorted(p.name for p in home.iterdir()), [".gemini"])                  # evidence folder points at the user's keychains
        self.assertEqual(sentinel.read_text(), "not a real keychain")                          # whatever the link points to is never touched

    def test_a_soft_denied_write_in_a_read_only_task_is_a_warning_and_changes_nothing(self):
        self.play(healthy("I could not change the file.\n"), writes={"app/calc.py": FIXED_CALC})
        before = self.project_digest()
        tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")                                      # exit 0 and SUCCESS: a soft denial is no failure ...
        self.assertIn('worker run: a tool was soft-denied: the "write_file" permission', self.issues(tid))   # ... but it is on the record for a person
        workspace = Path(self.kernel.store.get_attempt(tid, 1)["workspace_path"])
        self.assertEqual((workspace / "app" / "calc.py").read_text(), BUGGY_CALC)
        res = self.result(tid)
        self.assertEqual((res["diff_artifact"], res["boundary"]["violations"]), (None, 0))
        self.assertEqual(self.project_digest(), before)

    def test_a_cli_that_runs_wider_than_asked_is_not_trusted_however_clean_its_run_looks(self):
        self.play(healthy(), permission_mode="always-proceed")
        before = self.project_digest()
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["failure_class"], att["stage"]), ("FAILED", "EXECUTION_FAILED", "worker"))
        self.assertIn("wider than", self.issues(tid))
        self.assertEqual(self.kernel.store.health_rows(), [])                                   # a posture problem says nothing about quota or login
        self.assertEqual(self.project_digest(), before)

    def test_a_cli_that_says_strict_but_writes_anyway_is_caught_by_the_comparison(self):
        self.play(healthy("fixed it\n"), act_as="always-proceed", writes={"app/calc.py": FIXED_CALC})
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["boundary"]["violations"], res["boundary"]["paths"]), ("boundary", 1, ["app/calc.py"]))
        self.assertIsNone(res["diff_artifact"])

    def test_garbage_lines_are_counted_and_a_warning_is_shown_but_a_good_run_stays_good(self):
        events = healthy()
        events.insert(1, {"$raw": "this is not json"})
        events.insert(2, S.tool("run_command", error={"type": "PERMISSION_DENIED", "message": "needs approval"}))
        self.play(events)
        tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        self.assertIn("1 output line(s) could not be parsed", self.issues(tid))
        self.assertIn("worker run: a tool call was refused: run_command", self.issues(tid))

    def test_an_attempt_leaves_the_vendor_stream_as_a_redacted_private_log(self):
        self.play(healthy("the key is sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 oops\n") + [{"$stderr": "warn: token ghp_abcdefghijklmnopqrstuvwxyz0123456789"}])
        tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        d = self.attempt_dir(tid)
        for name in ("stdout.log", "stderr.log", "result.json"):
            text = (d / name).read_text()
            self.assertNotIn("AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", text, name)
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", text, name)
            self.assertEqual(os.stat(d / name).st_mode & 0o777, 0o600)
        self.assertNotIn("AbCdEf", self.result(tid)["summary"])

    def test_a_text_only_task_runs_in_an_empty_folder(self):
        self.play(healthy("42\n"))
        tid, report = self.run_new(analysis(risk="R0"))
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual(list(Path(self.kernel.store.get_attempt(tid, 1)["workspace_path"]).iterdir()), [])


class WriteTask(AgyWorld):
    def test_a_file_changing_task_end_to_end_and_nothing_is_applied(self):
        self.play(healthy("fixed add()\n", S.tool("write_to_file", params={"TargetFile": "app/calc.py"})), writes={"app/calc.py": FIXED_CALC})
        before = self.project_digest()
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual(self.last_run()["home_settings"]["toolPermission"], "request-review")  # the write preset, for this task only
        res = self.result(tid)
        self.assertEqual((res["diff_stat"], res["boundary"]["violations"], res["partial"]), ({"files": 1, "additions": 1, "deletions": 1}, 0, False))
        self.assertEqual([(t["name"], t["status"]) for t in res["tests"]], [("unit", "PASS")])
        self.assertIn(b"+    return a + b", self.kernel.diff(tid))
        self.assertEqual(self.kernel.accept(tid)["status"], "ACCEPTED")
        self.assertEqual(self.project_digest(), before)                                         # ACCEPTED is not APPLIED
        self.assertEqual((self.project_path / "app" / "calc.py").read_text(), BUGGY_CALC)

    def test_the_write_task_gets_write_rules_for_exactly_its_allowed_paths_and_nothing_else(self):
        self.play(healthy("fixed add()\n", S.tool("write_to_file", params={"TargetFile": "app/calc.py"})), writes={"app/calc.py": FIXED_CALC})
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        workspace = os.path.realpath(self.kernel.store.get_attempt(tid, 1)["workspace_path"])
        settings = self.last_run()["home_settings"]
        self.assertEqual(settings["toolPermission"], "request-review")
        self.assertEqual(settings["permissions"], {"allow": ["write_file(app/)", f"write_file({workspace}/app/)"]})
        on_disk = self.attempt_dir(tid) / "home" / ".gemini" / "antigravity-cli" / "settings.json"
        self.assertEqual(json.loads(on_disk.read_text()), settings)
        self.assertNotIn("--dangerously-skip-permissions", self.last_run()["argv"])

    def test_the_accept_edits_fallback_lets_the_edit_through_without_any_rule(self):
        self.environ = {**BASE_ENV, "PAR_AGY_WRITE_STRATEGY": "accept-edits"}
        self.play(healthy("fixed add()\n", S.tool("write_to_file", params={"TargetFile": "app/calc.py"})), writes={"app/calc.py": FIXED_CALC})
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        call = self.last_run()
        self.assertEqual(call["argv"][-2:], ["--mode", "accept-edits"])
        self.assertEqual(call["home_settings"]["toolPermission"], "request-review")
        self.assertNotIn("permissions", call["home_settings"])
        self.assertEqual(self.result(tid)["boundary"]["violations"], 0)
        self.assertIn(b"+    return a + b", self.kernel.diff(tid))

    def test_a_write_outside_the_allowed_paths_is_already_refused_by_the_clis_own_rules(self):
        # emulated by the fake (the real CLI's matching of paths against rules is still to be validated, docs/AGY.md): the Kernel's boundary
        # check does not depend on it - see test_a_change_outside_the_allowed_paths_is_a_boundary_violation
        self.play(healthy("done\n"), writes={"app/calc.py": FIXED_CALC, "README.md": "# changed\n"})
        before = self.project_digest()
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        workspace = Path(self.kernel.store.get_attempt(tid, 1)["workspace_path"])
        self.assertEqual((workspace / "app" / "calc.py").read_text(), FIXED_CALC)
        self.assertNotEqual((workspace / "README.md").read_text(), "# changed\n")
        self.assertEqual(self.result(tid)["boundary"]["violations"], 0)
        self.assertIn('soft-denied: the "write_file" permission', self.issues(tid))
        self.assertEqual(self.project_digest(), before)

    def test_a_path_that_cannot_be_written_as_a_rule_stops_the_task_before_the_cli_is_started(self):
        self.play(healthy())
        tid, report = self.run_new(sample_contract(role="analysis", allowed_paths=["my docs/"]))
        self.assertEqual(report.status, "FAILED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["stage"]), ("FAILED", "preflight"))
        self.assertIn("cannot be written as a permission rule", self.issues(tid))
        self.assertEqual(self.fake.runs(), [])                                                  # agy was never started

    def test_a_write_the_cli_soft_denied_leaves_the_bug_and_the_reason_on_the_record(self):
        self.play(healthy("I tried to fix it.\n"), act_as="strict", writes={"app/calc.py": FIXED_CALC})
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "FAILED")                                               # the Kernel's own check: the unit test still fails
        res = self.result(tid)
        self.assertEqual((res["stage"], [(t["name"], t["status"]) for t in res["tests"]]), ("validation", [("unit", "FAIL")]))
        self.assertIn('worker run: a tool was soft-denied: the "write_file" permission', self.issues(tid))

    def test_a_confident_wrong_answer_is_not_believed(self):
        self.play(healthy("All tests pass, I ran them myself.\n"), writes={"app/calc.py": "def add(a, b):\n    return a * b\n"})
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["summary_verified"]), ("validation", False))
        self.assertIn("All tests pass", res["summary"])
        self.assertTrue(res["partial"])

    def test_a_change_outside_the_allowed_paths_is_a_boundary_violation(self):
        # a CLI that lets every write through (the Kernel never relies on the CLI's own rules for the boundary)
        self.play(healthy("done\n"), act_as="always-proceed", writes={"app/calc.py": FIXED_CALC, "README.md": "# changed\n"})
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["boundary"]["paths"]), ("boundary", ["README.md"]))

    def test_a_run_that_changes_nothing_is_reported_as_such(self):
        self.play(healthy("I looked and changed nothing\n"))
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.result(tid)["tests"][0]["status"], "FAIL")


class RealSoftDenial(AgyWorld):
    """The real write smoke (2026-10-15): the model read the file, tried to RUN the tests, headless mode auto-denied the command, and the
    run ended with vendor status SUCCESS, exit 0 and no output. These are the same events (S.denied_command and friends are real 1.3.5 shapes)."""

    def denied_run(self):
        return [{"$init": True}, S.user_step(), S.tool("view_file", params={"AbsolutePath": "app/calc.py"}, output="3 lines, 32 bytes"),
                *S.denied_command("python3 -m unittest -q", index=4), {"$stderr": S.DENIED_NOTICE}, S.result("", denied=[S.DENIED_COMMAND])]

    def test_a_write_task_that_ended_without_output_is_judged_by_the_kernel_not_by_the_vendors_success(self):
        self.play(self.denied_run())
        before = self.project_digest()
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["terminal_state"], res["outcome"], res["stage"]), ("SUCCESS", "failed", "validation"))   # the vendor said SUCCESS
        self.assertEqual((res["diff_artifact"], res["summary"]), (None, "(the worker gave no summary)"))
        self.assertEqual([(t["name"], t["status"]) for t in res["tests"]], [("unit", "FAIL")])                        # the Kernel's own check decides
        issues = res["remaining_issues"]
        self.assertEqual(sum(1 for i in issues if "soft-denied" in i), 1)                       # stderr, tool step and result say it once
        self.assertIn(f"worker run: {agy.soft_denial_message('command')}", issues)
        self.assertIn("worker run: a tool call was refused: run_command (python3 -m unittest -q)", issues)
        self.assertEqual(self.project_digest(), before)

    def test_a_read_only_task_that_ended_without_output_is_not_a_silent_success(self):
        self.play(self.denied_run())
        tid, report = self.run_new()
        self.assertEqual(report.status, "REVIEW_REQUIRED")                                      # nothing to check mechanically: a person reviews ...
        res = self.result(tid)
        self.assertEqual(res["summary"], "(the worker gave no summary)")                       # ... and sees that nothing was said ...
        self.assertIn(f"worker run: {agy.soft_denial_message('command')}", res["remaining_issues"])   # ... and why
        self.assertEqual(res["next_action"], "review")

    def test_the_environment_note_is_part_of_the_saved_prompt_and_exactly_what_agy_reads(self):
        for contract, mode in ((analysis(), "read-only environment"), (sample_contract(role="analysis"), "Shell commands are not available")):
            self.play(healthy())
            tid, _ = self.run_new(contract)
            prompt = (self.attempt_dir(tid) / "prompt.txt").read_text()
            self.assertIn("\n\n## Environment\n", prompt)
            self.assertTrue(prompt.rstrip().endswith("finish with a written answer.") or prompt.rstrip().endswith("finish with a written summary."))
            self.assertIn(mode, prompt)
            call = self.last_run()
            self.assertEqual(call["stdin"], prompt)                                              # what was saved and hashed is what the model got
            self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), self.kernel.store.get_attempt(tid, 1)["prompt_sha256"])
            self.fake.configure(login="ok")


class Refusals(AgyWorld):
    def test_a_project_with_hooks_is_refused_before_anything_is_started(self):
        evil = self.make_project("evil", extra={".agents/hooks.json": '{"hooks": []}'})
        self.projects["evil"] = ProjectEntry("evil", evil, "git", True)
        self.kernel = self.new_kernel(projects=self.projects)
        tid, report = self.run_new(analysis(project_id="evil"))
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.fake.runs(), [])
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["stage"], att["pid"]), ("FAILED", "preflight", None))
        self.assertIn("hooks.json", self.kernel.describe(tid)["last_failure"])

    def test_a_cli_that_is_not_signed_in_stops_the_attempt_before_anything_is_spent(self):
        self.fake.configure(login="none")
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.fake.runs(), [])
        self.assertIn("not signed in", self.kernel.describe(tid)["last_failure"])
        self.assertIn("par provider login antigravity", self.kernel.describe(tid)["last_failure"])

    def test_a_locked_keychain_is_named_as_such_and_an_unreadable_state_is_not_assumed_to_be_fine(self):
        for state, said in (("locked", "keychain is locked"), ("weird", "could not confirm that agy is signed in")):
            self.fake.configure(login=state)
            tid, report = self.run_new(analysis(idempotency_key=f"k-{state}"))
            self.assertEqual(report.status, "FAILED", state)
            self.assertEqual(self.fake.runs(), [], state)
            self.assertIn(said, self.kernel.describe(tid)["last_failure"], state)

    def test_a_missing_executable_is_a_clean_refusal(self):
        os.unlink(self.fake.exe)
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertIn("no executable at", self.kernel.describe(tid)["last_failure"])

    def test_an_untrusted_executable_never_runs(self):
        with mock.patch("par.providers.antigravity.AntigravityAdapter.signature_problem", return_value="the executable is not signed by Google (team: none)"):
            tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.fake.calls(), [])                                                 # not even a probe ran the unverified program

    def test_the_sign_in_check_runs_with_the_private_layout_and_calls_no_model(self):
        self.play(healthy())
        self.run_new()
        checks = [c for c in self.fake.calls() if c["kind"] == "models"]
        self.assertTrue(checks)
        self.assertEqual(checks[0]["argv"], ["models"])                                         # fails at once when signed out (`-p /usage` would wait)
        self.assertEqual(checks[0]["stdin"], None)
        self.assertEqual(checks[0]["home_settings"]["toolPermission"], "strict")
        self.assertNotIn("usage", [c["kind"] for c in self.fake.calls()])                       # the quota report is a probe's business, not an attempt's
        self.assertEqual(len(self.fake.runs()), 1)                                              # exactly one model-calling run


class Failures(AgyWorld):
    def run_failing(self, error, exit_code=1, contract=None):
        self.play([{"$init": True}, S.result("", status="ERROR", error=error)], exit=exit_code)
        return self.run_new(contract)

    def test_an_exhausted_quota_blocks_the_provider_and_only_offers_the_fallback(self):
        tid, report = self.run_failing(S.QUOTA)
        self.assertEqual(report.status, "QUOTA_EXHAUSTED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["failure_class"], att["exit_code"]), ("FAILED", "QUOTA_EXHAUSTED", 1))
        row = self.kernel.store.health_rows()[0]
        self.assertEqual((row["provider_id"], row["model"], row["status"]), ("antigravity", "", "QUOTA_EXHAUSTED"))
        verdict = self.kernel.health.verdict("antigravity", "any-model")
        self.assertEqual((verdict.usable, verdict.scope), (False, "provider"))
        self.assertEqual([a["kind"] for a in self.kernel.describe(tid)["pending_approvals"]], ["FALLBACK_SWITCH"])   # offered, never taken on its own
        self.assertEqual(self.codex.calls, 0)

    def test_a_rate_limit_cools_down_one_model_only(self):
        tid, report = self.run_failing(S.RATE)
        self.assertEqual(report.status, "RATE_LIMITED")
        row = self.kernel.store.health_rows()[0]
        self.assertEqual((row["model"], row["status"]), (AGY_MODEL, "RATE_LIMITED"))
        self.assertTrue(self.kernel.health.verdict("antigravity", "another-model").usable)

    def test_a_sign_in_problem_stops_the_provider_until_a_person_signs_in(self):
        for n, text in enumerate((S.AUTH, S.KEYRING)):                                           # a fresh task each time; the record is cleared in between
            tid, report = self.run_failing(text, contract=analysis(idempotency_key=f"auth-{n}"))
            self.assertEqual(report.status, "AUTH_REQUIRED", text)
            self.assertEqual(self.kernel.store.health_rows()[0]["model"], "", text)
            self.kernel.clear_health("antigravity")

    def test_an_unknown_model_is_reported_by_the_cli_itself_and_blocks_that_worker(self):
        self.fake.configure(models=["some-other-model"])
        self.play(healthy())
        tid, report = self.run_new()
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["failure_class"], att["exit_code"]), ("MODEL_UNAVAILABLE", 1))
        self.assertNotEqual(report.status, "REVIEW_REQUIRED")

    def test_a_dropped_connection_is_interrupted_and_waits_for_a_person(self):
        tid, report = self.run_failing(S.TRANSIENT)
        self.assertEqual(report.status, "INTERRUPTED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["failure_class"], "TRANSIENT_ERROR")

    def test_an_unrecognised_failure_is_a_plain_failure_never_a_guess_about_quota(self):
        tid, report = self.run_failing("agy ran out of room in the model's context window.")
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.kernel.store.health_rows(), [])

    def test_a_run_that_ends_waiting_for_input_needs_a_person(self):
        self.play([{"$init": True}, S.result("", status="WAITING")], exit=0)
        tid, report = self.run_new()
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["failure_class"], "PERMISSION_BLOCKED")
        self.assertNotEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual(self.result(tid)["next_action"], "approve")

    def test_a_stream_that_just_ends_is_never_success(self):
        self.play([{"$init": True}, S.user_step()], exit=0)
        tid, report = self.run_new()
        self.assertEqual(report.status, "INTERRUPTED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["failure_class"], att["exit_code"]), ("INTERRUPTED", "UNKNOWN", 0))
        self.assertEqual(self.result(tid)["terminal_state"], "NONE")

    def test_a_truncated_stream_with_a_crash_is_not_success(self):
        self.play([{"$init": True}, {"$raw": '{"event": "step_up'}, {"$exit": 101}])
        tid, report = self.run_new()
        self.assertNotEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["exit_code"], 101)

    def test_success_events_with_a_failing_exit_code_do_not_pass(self):
        self.play(healthy(), exit=3)
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")

    def test_a_result_without_init_proves_nothing_about_the_permission_mode_and_fails(self):
        self.play([S.user_step(), S.delta("hi\n", "DONE"), S.result("hi\n")])
        tid, report = self.run_new()
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["failure_class"], "EXECUTION_FAILED")


class Routing(AgyWorld):
    """The profile decides; the Kernel keeps state. No model is asked which worker to use, and nothing is scored."""

    def test_analysis_goes_to_antigravity_and_code_goes_to_codex(self):
        self.play(healthy())
        tid, report = self.run_new(analysis())
        self.assertEqual((report.status, len(self.fake.runs()), self.codex.calls), ("REVIEW_REQUIRED", 1, 0))
        self.assertEqual(self.result(tid)["worker"], "w_agy")
        tid2 = self.queued(r1_contract(role="code", idempotency_key="code-1"))
        self.assertEqual(self.run_task(tid2).status, "REVIEW_REQUIRED")
        self.assertEqual((len(self.fake.runs()), self.codex.calls), (1, 1))                       # the second task never reached agy
        self.assertEqual(self.result(tid2)["worker"], "w_code")

    def test_choosing_a_worker_starts_no_program(self):
        report = self.kernel.health_report()
        self.assertEqual({w["worker"] for w in report["workers"]}, {"w_code", "w_agy"})
        self.assertEqual(report["roles"]["analysis"], {"primary": "w_agy", "fallback": ["w_code"]})
        self.assertEqual(report["roles"]["code"], {"primary": "w_code", "fallback": ["w_agy"]})
        self.assertEqual(self.fake.calls(), [])                                                   # deciding is data, not a model call

    def test_after_a_quota_failure_the_next_attempt_runs_on_codex_only_once_a_person_approves(self):
        self.play([{"$init": True}, S.result("", status="ERROR", error=S.QUOTA)], exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "QUOTA_EXHAUSTED")
        approval = self.kernel.describe(tid)["pending_approvals"][0]
        self.assertEqual(approval["kind"], "FALLBACK_SWITCH")
        self.assertEqual((len(self.fake.runs()), self.codex.calls), (1, 0))
        self.kernel.approve(tid, approval["id"])
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual((len(self.fake.runs()), self.codex.calls), (1, 1))                       # agy was not asked again; codex did the work
        self.assertEqual(self.result(tid)["worker"], "w_code")

    def test_while_agy_is_blocked_a_new_analysis_task_does_not_reach_it(self):
        self.play([{"$init": True}, S.result("", status="ERROR", error=S.QUOTA)], exit=1)
        self.run_new()
        tid2 = self.queued(analysis(idempotency_key="second"))
        self.assertNotEqual(self.run_task(tid2).status, "REVIEW_REQUIRED")
        self.assertEqual(len(self.fake.runs()), 1)

    def test_a_person_can_clear_the_record_and_agy_is_used_again(self):
        self.play([{"$init": True}, S.result("", status="ERROR", error=S.QUOTA)], exit=1)
        tid, _ = self.run_new()
        self.play(healthy())
        self.kernel.retry(tid, force=True)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(self.kernel.store.health_rows(), [])
        self.assertEqual(len(self.fake.runs()), 2)


class RoutingWithSafeAuto(AgyWorld):
    policy = "safe_auto"

    def test_with_safe_auto_one_switch_to_codex_happens_for_a_read_only_task(self):
        self.play([{"$init": True}, S.result("", status="ERROR", error=S.QUOTA)], exit=1)
        tid, report = self.run_new()
        self.assertEqual(report.status, "QUEUED")                                                # switched on its own, waiting to run
        d = self.kernel.describe(tid)
        self.assertEqual((d["pinned_worker"], d["switch_budget"], d["pending_approvals"]), ("w_code", 0, []))
        self.assertIn("WORKER_SWITCHED", self.event_types(tid))
        self.assertEqual((len(self.fake.runs()), self.codex.calls), (1, 0))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual((len(self.fake.runs()), self.codex.calls), (1, 1))
        self.assertEqual([self.kernel.store.get_attempt(tid, n)["provider_id"] for n in (1, 2)], ["antigravity", "codex"])
        self.assertEqual(self.result(tid)["worker"], "w_code")

    def test_a_file_changing_task_is_never_switched_on_its_own(self):
        self.play([{"$init": True}, S.result("", status="ERROR", error=S.QUOTA)], exit=1)
        tid, report = self.run_new(sample_contract(role="analysis"))
        self.assertEqual(report.status, "QUOTA_EXHAUSTED")
        d = self.kernel.describe(tid)
        self.assertEqual([a["kind"] for a in d["pending_approvals"]], ["FALLBACK_SWITCH"])          # R2 always asks
        self.assertEqual(self.codex.calls, 0)


class Interruption(AgyWorld):
    def start_and_wait(self, tid):
        other = self.new_kernel()
        t = self.in_thread(lambda: self.kernel.run(tid))
        self.assertTrue(self.wait_for(lambda: other.describe(tid)["status"] == "RUNNING"))
        self.assertTrue(self.wait_for(lambda: bool(self.fake.runs())))
        return other, t

    def test_cancel_interrupts_the_cli_politely(self):
        self.play([{"$init": True}], hang=True)
        tid = self.queued(analysis())
        other, t = self.start_and_wait(tid)
        t0 = time.monotonic()
        other.cancel(tid, reason="stop")
        t.join(20)
        self.assertEqual(t.box["value"].status, "CANCELED")
        self.assertLess(time.monotonic() - t0, 15)
        att = other.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["exit_code"]), ("CANCELED", 130))                 # Ctrl-C, handled: not a kill
        self.assertEqual(other.store.locks(), [])

    def test_a_cli_that_ignores_ctrl_c_is_stopped_anyway(self):
        self.play([{"$init": True}], hang=True, sigint="ignore")
        tid = self.queued(analysis())
        other, t = self.start_and_wait(tid)
        pgid = other.store.get_attempt(tid, 1)["pgid"]
        other.cancel(tid)
        t.join(20)
        self.assertEqual(t.box["value"].status, "CANCELED")
        self.assertFalse(sp.group_alive(pgid))
        self.assertIn(other.store.get_attempt(tid, 1)["signal"], (signal.SIGTERM, signal.SIGKILL))

    def test_a_silent_cli_is_stopped_by_the_idle_timeout_and_is_not_called_a_success(self):
        self.play([{"$init": True}], hang=True)
        tid = self.queued(analysis(timeouts={"startup": 60, "idle": 1, "total": 120}))
        report = self.run_task(tid)
        self.assertEqual(report.status, "INTERRUPTED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["timeout_kind"], "idle")


if __name__ == "__main__":
    unittest.main()
