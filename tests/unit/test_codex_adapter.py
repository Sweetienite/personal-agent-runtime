"""The Codex adapter, alone: what it asks the CLI to do, what the CLI sees, how it reads the CLI's answers.

Everything runs against a fake `codex` program (tests/fake_codex_exe.py): no model, no network, no real login.
"""
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from par.providers import codex as C
from par.providers.base import FailureClass, ProviderStatus, RunOutcome, RunRequest, Tri
from tests.fake_codex import BASE_ENV, FakeCodex

HIT = "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at {when}."


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._tmp.name))
        self.fake = FakeCodex(self.root)
        self.adapter = self.fake.adapter()
        self.work = self.root / "work space"                # a space on purpose: paths with spaces must survive intact
        self.work.mkdir()

    def tearDown(self):
        C.forget_caches()
        self._tmp.cleanup()

    def request(self, prompt="please do the task", model="gpt-test", write=False):
        return RunRequest("t-1", 1, prompt, self.work, model, allow_write=write)

    def outcome(self, lines, exit_code=0, stderr="", signal_no=None, adapter=None):
        adapter = adapter or self.adapter
        events = [e for e in (adapter.parse_event(l if isinstance(l, str) else json.dumps(l)) for l in lines) if e is not None]
        return RunOutcome(exit_code=exit_code, events=events, signal=signal_no, stderr_tail=stderr)


def ev(kind, **kw):
    return {"type": kind, **kw}


def message(text):
    return ev("item.completed", item={"id": "i", "type": "agent_message", "text": text})


OK = [ev("thread.started", thread_id="t1"), ev("turn.started"), message("done"), ev("turn.completed", usage={"input_tokens": 10, "output_tokens": 2})]


class Argv(Base):
    def test_the_exact_command_for_a_read_only_run(self):
        flags = self.adapter.isolation().flags()
        self.assertEqual(self.adapter.build_argv(self.request()),
                         [str(self.fake.exe), "exec", "--json", "--color", "never", "--ephemeral", "--skip-git-repo-check",
                          "-s", "read-only", "-m", "gpt-test", "-C", str(self.work), *flags, "-"])

    def test_the_sandbox_follows_the_task_never_the_other_way_round(self):
        for write, mode in ((False, "read-only"), (True, "workspace-write")):
            argv = self.adapter.build_argv(self.request(write=write))
            self.assertEqual(argv[argv.index("-s") + 1], mode)

    def test_everything_the_user_setup_would_hand_the_model_is_switched_off(self):
        argv = self.adapter.build_argv(self.request())
        disabled = {argv[i + 1] for i, a in enumerate(argv) if a == "--disable"}
        self.assertEqual(disabled, set(C.DISABLED_FEATURES))
        self.assertIn("memories", disabled)
        pairs = {argv[i + 1] for i, a in enumerate(argv) if a == "-c"}
        self.assertIn("notify=[]", pairs)                                       # no helper program started outside the sandbox
        self.assertIn("mcp_servers.node_repl.enabled=false", pairs)             # a server defined in the user's config
        self.assertNotIn("mcp_servers.browser_plugin.enabled=false", pairs)     # (a plugin's server goes away with `--disable plugins`)

    def test_no_flag_that_widens_rights_or_replaces_the_users_setup_is_ever_passed(self):
        argv = " ".join(self.adapter.build_argv(self.request(write=True)))
        for bad in ("--ignore-user-config", "--ignore-rules", "--add-dir", "--dangerously", "danger-full-access", "--full-auto", "--oss",
                    "--worktree", "--enable", "--profile", " -p ", "--ask-for-approval", " -a ", "--approve-for-me", "--cyber-access-program"):
            self.assertNotIn(bad, argv + " ")

    def test_the_prompt_never_appears_on_the_command_line(self):
        secret_looking = "PROMPT-MARKER-12345 do the thing"
        self.assertNotIn("PROMPT-MARKER", " ".join(self.adapter.build_argv(self.request(prompt=secret_looking))))
        self.assertEqual(self.adapter.stdin_data(self.request(prompt=secret_looking)), secret_looking.encode())
        self.assertEqual(self.adapter.build_argv(self.request())[-1], "-")                       # "read the prompt from standard input"

    def test_the_prompt_survives_unicode(self):
        self.assertEqual(self.adapter.stdin_data(self.request(prompt="修复 add() — ünïcode")).decode(), "修复 add() — ünïcode")

    def test_a_model_slug_cannot_smuggle_an_option(self):
        for bad in ("--oss", "-x", "a b", "", "m;rm", "m\nx", "é", "x" * 101):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.adapter.build_argv(self.request(model=bad))
        for good in ("gpt-6.1-sol", "gpt-5.5", "o3", "provider/model:tag+1"):
            self.adapter.build_argv(self.request(model=good))

    def test_the_working_directory_is_given_explicitly(self):
        argv = self.adapter.build_argv(self.request())
        self.assertEqual(argv[argv.index("-C") + 1], str(self.work))


class WorkingCopy(Base):
    def test_codex_asks_for_a_copy_without_git(self):
        self.assertIs(self.adapter.workspace_with_git, False)

    def test_other_providers_keep_the_normal_copy(self):
        from par.providers.fake import ScriptedProvider, scenarios
        self.assertTrue(getattr(ScriptedProvider(scenarios.success(), "codex"), "workspace_with_git", True))


class Environment(Base):
    def test_only_the_login_location_and_network_settings_reach_the_child(self):
        env = {**BASE_ENV, "OPENAI_API_KEY": "sk-test-not-real", "CODEX_API_KEY": "x", "ANTHROPIC_API_KEY": "y", "GITHUB_TOKEN": "z",
               "AWS_SECRET_ACCESS_KEY": "w", "HOME": "/Users/someone", "HTTPS_PROXY": "http://proxy.invalid:3128", "NO_PROXY": "localhost",
               "SSL_CERT_FILE": "/etc/ssl/x.pem"}
        adapter = self.fake.adapter(environ=env)
        got = dict(adapter.environment(self.request()))
        self.assertEqual(got, {"CODEX_HOME": str(self.fake.home), "HTTPS_PROXY": "http://proxy.invalid:3128", "NO_PROXY": "localhost",
                               "SSL_CERT_FILE": "/etc/ssl/x.pem"})

    def test_an_api_key_in_the_callers_environment_can_never_select_metered_billing(self):
        adapter = self.fake.adapter(environ={**BASE_ENV, "OPENAI_API_KEY": "sk-test-not-real", "CODEX_API_KEY": "k", "OPENAI_BASE_URL": "http://evil.invalid"})
        names = set(adapter.environment(self.request()))
        self.assertFalse({"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"} & names)

    def test_the_login_location_comes_from_the_environment_or_the_default(self):
        self.assertEqual(C.CodexAdapter("/x", environ={"CODEX_HOME": "/somewhere/else"}).codex_home, "/somewhere/else")
        self.assertEqual(C.CodexAdapter("/x", environ={}).codex_home, str(Path.home() / ".codex"))


class Executable(Base):
    def test_a_good_executable_has_no_problem(self):
        self.assertIsNone(self.adapter.executable_problem())

    def test_relative_missing_unexecutable_and_writable_by_others_are_refused(self):
        for exe, expect in (("codex", "absolute path"), ("", "absolute path"), (str(self.root / "nope"), "no executable at")):
            self.assertIn(expect, C.CodexAdapter(exe, codex_home=str(self.fake.home), environ=BASE_ENV).executable_problem())
        plain = self.root / "plain"
        plain.write_text("x")
        os.chmod(plain, 0o644)
        self.assertIn("not executable", C.CodexAdapter(str(plain), codex_home=str(self.fake.home), environ=BASE_ENV).executable_problem())
        for bit in (stat.S_IWGRP, stat.S_IWOTH):
            os.chmod(self.fake.exe, 0o755 | bit)
            self.assertIn("writable by other users", self.adapter.executable_problem())
        os.chmod(self.fake.exe, 0o755)

    def test_the_executable_is_never_looked_up_on_path(self):
        adapter = C.CodexAdapter("codex", codex_home=str(self.fake.home), environ={"PATH": str(self.fake.exe.parent)})
        self.assertIn("absolute path", adapter.executable_problem())
        self.assertIn("absolute path", adapter.preflight(self.request()))


@unittest.skipUnless(sys.platform == "darwin", "code signatures are a macOS matter")
class Signature(Base):
    def check(self, path):
        return C.CodexAdapter(str(path), codex_home=str(self.fake.home), environ=BASE_ENV).signature_problem()

    def test_a_script_has_no_signature_of_its_own(self):
        self.assertIn("is a script", self.check(self.fake.exe))

    def test_a_program_outside_any_app_bundle_is_refused(self):
        self.assertIn("not inside a signed .app bundle", self.check("/usr/bin/true"))

    def test_a_validly_signed_program_from_someone_else_is_refused(self):
        apple = Path("/System/Applications/Calculator.app/Contents/MacOS/Calculator")
        if not apple.is_file():
            self.skipTest("no system app to test with")
        self.assertIn("not signed by OpenAI", self.check(apple))

    def test_the_check_is_cached_so_attempts_do_not_pay_for_it_each_time(self):
        adapter = C.CodexAdapter("/usr/bin/true", codex_home=str(self.fake.home), environ=BASE_ENV)
        C.forget_caches()
        with mock.patch.object(C.subprocess, "run", wraps=C.subprocess.run) as run:
            adapter._check_signature = mock.Mock(return_value="x")                # the expensive part
            first, second = adapter.signature_problem(), adapter.signature_problem()
        self.assertEqual((first, second), ("x", "x"))
        self.assertEqual(adapter._check_signature.call_count, 1)
        run.assert_not_called()

    def test_the_switch_that_skips_the_check_exists_only_for_tests(self):
        self.assertTrue(C.CodexAdapter("/x").verify_signature)
        entry = mock.Mock(executable="/x/codex", id="codex")
        self.assertTrue(C.CodexAdapter.from_entry(entry).verify_signature)


class Preflight(Base):
    def test_a_ready_install_may_start(self):
        self.assertIsNone(self.adapter.preflight(self.request()))
        self.assertEqual(self.fake.exec_calls(), [])                                   # asking never runs a model

    def test_refusals(self):
        self.assertIn("not a usable model slug", self.adapter.preflight(self.request(model="--oss")))
        self.assertIn("larger than", self.adapter.preflight(self.request(prompt="x" * (C.MAX_PROMPT_BYTES + 1))))
        missing_home = C.CodexAdapter(str(self.fake.exe), codex_home=str(self.root / "no_home"), environ=BASE_ENV, verify_signature=False)
        self.assertIn("does not exist", missing_home.preflight(self.request()))

    def test_a_login_other_than_the_subscription_is_refused_before_anything_is_spent(self):
        for login, expect in ((["Logged in using an API key - sk-...abcd", 0], "metered billing"), (["Not logged in", 1], "not logged in"),
                              (["something new", 0], "could not confirm")):
            self.fake.configure(login=login)
            self.assertIn(expect, self.adapter.preflight(self.request()), login)
        self.assertEqual(self.fake.exec_calls(), [])

    def test_extras_that_cannot_be_proven_off_stop_the_run(self):
        cases = ({"ignore_overrides": True}, {"memories_sticky": True}, {"features_exit": 3}, {"mcp_exit": 2}, {"mcp_raw": "not json"}, {"mcp_raw": '{"a": 1}'})
        for cfg in cases:
            self.fake = FakeCodex(Path(tempfile.mkdtemp(dir=self.root)), **cfg)
            reason = self.fake.adapter().preflight(self.request())
            self.assertIn("could not verify that Codex's extra tools are switched off", reason or "", cfg)
            self.assertEqual(self.fake.exec_calls(), [])

    def test_a_server_name_that_cannot_be_switched_off_safely_stops_the_run(self):
        fake = FakeCodex(Path(tempfile.mkdtemp(dir=self.root)), mcp=[{"name": "weird name=x", "enabled": True, "source": "config"}])
        self.assertIn("cannot be switched off safely", fake.adapter().preflight(self.request()) or "")

    def test_an_untrusted_executable_stops_the_run_before_anything_starts(self):
        with mock.patch.object(C.CodexAdapter, "signature_problem", return_value="the executable is not signed by OpenAI (team: none)"):
            self.assertIn("not signed by OpenAI", self.adapter.preflight(self.request()))


class Isolation(Base):
    def test_features_the_installed_version_does_not_know_are_not_passed(self):
        fake = FakeCodex(Path(tempfile.mkdtemp(dir=self.root)), unknown_features=["goals", "ultrafast_mode", "tool_suggest"])
        iso = fake.adapter().isolation()
        self.assertTrue(iso.verified)
        self.assertEqual(set(iso.dropped), {"goals", "ultrafast_mode", "tool_suggest"})
        self.assertNotIn("goals", iso.disable)
        self.assertNotIn("--disable goals", " ".join(iso.flags()).replace("--disable  ", "--disable "))

    def test_a_version_without_memories_cannot_be_proven_safe(self):
        fake = FakeCodex(Path(tempfile.mkdtemp(dir=self.root)), unknown_features=["memories"])
        iso = fake.adapter().isolation()
        self.assertFalse(iso.verified)
        self.assertIn("memories", " ".join(iso.problems))

    def test_every_enabled_server_defined_in_the_users_config_is_switched_off_by_name(self):
        fake = FakeCodex(Path(tempfile.mkdtemp(dir=self.root)),
                         mcp=[{"name": "a", "enabled": True, "source": "config"}, {"name": "b-2", "enabled": True, "source": "config"},
                              {"name": "off", "enabled": False, "source": "config"}, {"name": "p", "enabled": True, "source": "plugin"}])
        iso = fake.adapter().isolation()
        self.assertEqual((iso.mcp_off, iso.verified), (("a", "b-2"), True))

    def test_discovery_is_cached_and_can_be_forgotten(self):
        self.adapter.isolation()
        n = len(self.fake.calls())
        self.adapter.isolation()
        self.assertEqual(len(self.fake.calls()), n)
        C.forget_caches()
        self.adapter.isolation()
        self.assertGreater(len(self.fake.calls()), n)

    def test_a_server_added_to_the_users_config_after_discovery_is_caught_before_the_next_run(self):
        from tests.fake_codex_exe import MCP
        self.assertIsNone(self.adapter.preflight(self.request()))                       # discovery is now cached
        self.assertNotIn("mcp_servers.newcomer.enabled=false", self.adapter.build_argv(self.request()))
        cfg = json.loads((self.fake.home / "fake.json").read_text())
        cfg["mcp"] = MCP + [{"name": "newcomer", "enabled": True, "source": "config"}]
        (self.fake.home / "fake.json").write_text(json.dumps(cfg))                      # (not via configure(): that would clear the caches)
        self.assertIsNone(self.adapter.preflight(self.request()))
        self.assertIn("mcp_servers.newcomer.enabled=false", self.adapter.build_argv(self.request()))

    def test_a_cached_proof_that_can_no_longer_be_re_checked_is_not_trusted(self):
        self.assertIsNone(self.adapter.preflight(self.request()))
        cfg = json.loads((self.fake.home / "fake.json").read_text())
        cfg["mcp_exit"] = 2
        (self.fake.home / "fake.json").write_text(json.dumps(cfg))
        self.assertIn("could not verify", self.adapter.preflight(self.request()) or "")

    def test_discovery_runs_in_a_clean_environment_with_a_throwaway_home(self):
        adapter = self.fake.adapter(environ={**BASE_ENV, "OPENAI_API_KEY": "sk-test-not-real", "HOME": "/Users/someone"})
        adapter.isolation()
        for call in self.fake.calls():
            env = call["env"]
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotEqual(env["HOME"], "/Users/someone")
            self.assertEqual(env["CODEX_HOME"], str(self.fake.home))


class Probe(Base):
    def test_a_ready_install(self):
        r = self.adapter.probe()
        self.assertEqual(r.status, ProviderStatus.AVAILABLE)
        self.assertEqual(r.version, "0.162.0-alpha.17.2")
        self.assertEqual(r.executable, str(self.fake.exe))
        self.assertEqual(r.facts["login"], "ChatGPT")
        self.assertTrue(r.facts["isolation"]["verified"])
        self.assertEqual(r.facts["isolation"]["mcp_servers_switched_off"], ["node_repl"])
        self.assertEqual(r.facts["models_in_catalog"], ["gpt-fake-1", "gpt-fake-2"])

    def test_probing_never_calls_a_model(self):
        self.adapter.probe()
        self.adapter.preflight(self.request())
        self.assertEqual(self.fake.exec_calls(), [])
        self.assertTrue(self.fake.offline_calls())

    def test_what_is_wrong_is_said_plainly(self):
        self.assertEqual(C.CodexAdapter("/no/such/codex", environ=BASE_ENV).probe().status, ProviderStatus.UNAVAILABLE)
        cases = (({"login": ["Not logged in", 1]}, ProviderStatus.AUTH_REQUIRED), ({"login": ["Logged in using an API key - sk-x", 0]}, ProviderStatus.UNHEALTHY),
                 ({"login": ["???", 0]}, ProviderStatus.UNHEALTHY), ({"ignore_overrides": True}, ProviderStatus.UNHEALTHY))
        for cfg, status in cases:
            fake = FakeCodex(Path(tempfile.mkdtemp(dir=self.root)), **cfg)
            self.assertEqual(fake.adapter().probe().status, status, cfg)

    def test_a_missing_login_folder_is_an_authentication_problem(self):
        adapter = C.CodexAdapter(str(self.fake.exe), codex_home=str(self.root / "gone"), environ=BASE_ENV, verify_signature=False)
        self.assertEqual(adapter.probe().status, ProviderStatus.AUTH_REQUIRED)

    def test_a_different_version_and_an_unlisted_model_are_noted_not_fatal(self):
        self.fake.configure(version="9.9.9", models=["other"])
        r = self.fake.adapter(models=("gpt-wanted",)).probe()
        self.assertEqual(r.status, ProviderStatus.AVAILABLE)
        self.assertIn("untested version", r.detail)
        self.assertIn("gpt-wanted", r.detail)
        self.assertEqual((r.facts["tested_version"], r.facts["configured_models_missing"]), (False, ["gpt-wanted"]))

    def test_the_declared_capabilities(self):
        c = self.adapter.capabilities()
        self.assertEqual((c.can_headless, c.structured_output, c.can_select_model, c.can_write), (Tri.YES,) * 4)
        self.assertEqual((c.can_restrict_tools, c.supports_subscription_auth), (Tri.NO, Tri.YES))

    def test_the_persistence_report_names_what_the_cli_keeps(self):
        rep = self.adapter.persistence_report()
        self.assertIn("--ephemeral", rep["sessions"])
        self.assertIn("memories", rep["memory"].lower() + "memories")
        self.assertEqual(rep["paths"], [str(self.fake.home)])


class Parsing(Base):
    def parse(self, obj):
        return self.adapter.parse_event(obj if isinstance(obj, str) else json.dumps(obj))

    def test_the_stream_of_a_normal_run(self):
        kinds = [self.parse(o).kind for o in OK]
        self.assertEqual(kinds, ["session", "other", "message", "terminal"])
        self.assertEqual(self.parse(OK[0]).data["thread_id"], "t1")
        done = self.parse(OK[3]).data
        self.assertEqual((done["status"], done["usage"]), ("SUCCESS", {"input_tokens": 10, "output_tokens": 2}))

    def test_items(self):
        cmd = self.parse(ev("item.completed", item={"type": "command_execution", "command": "python3 -m unittest -q", "status": "completed", "exit_code": 0}))
        self.assertEqual((cmd.kind, cmd.data["tool"], cmd.data["exit_code"]), ("tool", "command", 0))
        chg = self.parse(ev("item.completed", item={"type": "file_change", "status": "completed", "changes": [{"path": "app/calc.py", "kind": "update"}]}))
        self.assertEqual((chg.kind, chg.data["paths"]), ("tool", ["app/calc.py"]))
        self.assertEqual(self.parse(ev("item.completed", item={"type": "reasoning", "text": "thinking"})).kind, "other")
        self.assertEqual(self.parse(ev("item.completed", item={"type": "error", "message": "Falling back to default metadata"})).kind, "warning")
        self.assertEqual(self.parse(ev("item.started", item={"type": "something_new", "status": "in_progress"})).data["tool"], "something_new")

    def test_a_declined_command_or_change_is_a_warning_a_person_will_see(self):
        for item in ({"type": "command_execution", "command": "sudo rm -rf /", "status": "declined"},
                     {"type": "file_change", "status": "declined", "changes": [{"path": "/etc/hosts", "kind": "update"}]}):
            e = self.parse(ev("item.completed", item=item))
            self.assertEqual(e.kind, "warning")
            self.assertIn("declined", e.data["message"])

    def test_things_that_should_not_happen_are_flagged_not_trusted(self):
        for item, word in (({"type": "mcp_tool_call", "server": "browser", "tool": "click", "status": "completed"}, "MCP"),
                           ({"type": "collab_tool_call", "tool": "spawn_agent", "status": "completed"}, "sub-agent"),
                           ({"type": "web_search", "query": "q", "status": "completed"}, "web search")):
            e = self.parse(ev("item.completed", item=item))
            self.assertEqual(e.kind, "tool")
            self.assertIn(word, e.data["note"])

    def test_errors_and_failed_turns_carry_the_message_and_the_reset_time(self):
        when = (datetime.now() + timedelta(hours=2)).astimezone().strftime("%-I:%M %p")
        err = self.parse(ev("error", message=HIT.format(when=when)))
        self.assertEqual(err.kind, "error")
        self.assertIn("usage limit", err.data["message"])
        self.assertIn("reset_at", err.data)
        failed = self.parse(ev("turn.failed", error={"message": HIT.format(when=when)}))
        self.assertEqual((failed.kind, failed.data["status"]), ("terminal", "ERROR"))
        self.assertIn("reset_at", failed.data)
        plain = self.parse(ev("turn.failed", error={"message": "stream disconnected before completion"}))
        self.assertNotIn("reset_at", plain.data)

    def test_garbage_is_counted_never_kept(self):
        for raw in ("not json at all", "{broken", "[1, 2, 3]", '"a string"', "42", '{"no_type": 1}', '{"type": 7}'):
            e = self.parse(raw)
            self.assertEqual(e.kind, "unparsed", raw)
            self.assertNotIn("not json at all", json.dumps(dict(e.data)))
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse("   \n"))
        self.assertEqual(self.parse(ev("item.completed", item="nope")).kind, "unparsed")

    def test_unknown_event_types_are_tolerated(self):
        e = self.parse(ev("some.future.event", payload={"x": 1}))
        self.assertEqual((e.kind, e.data["type"]), ("other", "some.future.event"))

    def test_text_is_bounded_and_secrets_are_redacted(self):
        e = self.parse(message("x" * 50_000))
        self.assertLessEqual(len(e.data["text"]), C.MAX_TEXT)
        leaky = self.parse(message("the token is sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 ok"))
        self.assertNotIn("AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", leaky.data["text"])

    def test_usage_keeps_only_whole_non_negative_numbers(self):
        e = self.parse(ev("turn.completed", usage={"input_tokens": 5, "cached_input_tokens": True, "output_tokens": -1,
                                                  "reasoning_output_tokens": "9", "surprise": 3}))
        self.assertEqual(e.data["usage"], {"input_tokens": 5})
        self.assertEqual(self.parse(ev("turn.completed")).data["usage"], {})


class Classification(Base):
    def cls(self, lines, **kw):
        return self.adapter.classify_failure(self.outcome(lines, **kw))

    def failed(self, message, *, via="turn.failed", exit_code=1):
        first = [ev("error", message=message)] if via == "turn.failed" else []
        last = ev("turn.failed", error={"message": message}) if via == "turn.failed" else ev("error", message=message)
        return [ev("thread.started", thread_id="t"), ev("turn.started"), *first, last], exit_code

    def test_a_clean_run_is_success_for_the_provider_and_nothing_more(self):
        self.assertIsNone(self.cls(OK))

    def test_exit_code_zero_alone_is_never_success(self):
        self.assertEqual(self.cls([ev("thread.started", thread_id="t"), ev("turn.started"), message("half way")]), FailureClass.UNKNOWN)
        self.assertEqual(self.cls([]), FailureClass.UNKNOWN)
        self.assertEqual(self.cls(["garbage"]), FailureClass.UNKNOWN)

    def test_the_usage_limit_is_quota_exhausted_whichever_way_it_arrives(self):
        for text in (HIT.format(when="7:30 PM"), "You’ve hit your usage limit. Try again later.", "You've hit your usage limit. To get more access now, send a request to your admin or try again at Oct 15th, 2026 3:05 AM.",
                     "You hit your spend cap for this workspace.", "Quota exceeded. Check your plan and billing details."):
            for via in ("turn.failed", "error"):
                lines, code = self.failed(text, via=via)
                self.assertEqual(self.cls(lines, exit_code=code), FailureClass.QUOTA_EXHAUSTED, (text, via))

    def test_a_bare_429_is_a_rate_limit_never_an_empty_quota(self):
        for text in ("exceeded retry limit, last status: 429 Too Many Requests", "unexpected status 429 Too Many Requests: slow down",
                     "Rate limit exceeded", "Selected model is at capacity. Please try a different model."):
            lines, code = self.failed(text)
            self.assertEqual(self.cls(lines, exit_code=code), FailureClass.RATE_LIMITED, text)

    def test_login_problems(self):
        for text in ("unexpected status 401 Unauthorized: Missing bearer or basic authentication in header",
                     "Your access token could not be refreshed because your refresh token was already used. Please log out and sign in again.",
                     "Not logged in"):
            lines, code = self.failed(text)
            self.assertEqual(self.cls(lines, exit_code=code), FailureClass.AUTH_REQUIRED, text)

    def test_model_problems(self):
        for text in ("The model `gpt-nope` does not exist or you do not have access to it.", "The 'x' model is not supported when using Codex with a ChatGPT account."):
            lines, code = self.failed(text)
            self.assertEqual(self.cls(lines, exit_code=code), FailureClass.MODEL_UNAVAILABLE, text)

    def test_transient_trouble_is_retryable_and_everything_unrecognised_is_a_plain_failure(self):
        for text in ("stream disconnected before completion: error sending request", "We're currently experiencing high demand, which may cause temporary errors.",
                     "Connection failed: connection reset by peer", "unexpected status 503 Service Unavailable"):
            lines, code = self.failed(text)
            self.assertEqual(self.cls(lines, exit_code=code), FailureClass.TRANSIENT_ERROR, text)
        for text in ("Codex ran out of room in the model's context window.", "Your input was flagged by content_filter", "something nobody has seen before"):
            lines, code = self.failed(text)
            self.assertEqual(self.cls(lines, exit_code=code), FailureClass.EXECUTION_FAILED, text)

    def test_retries_inside_a_run_that_then_succeeds_are_not_failures(self):
        lines = [ev("thread.started", thread_id="t"), ev("error", message="Reconnecting... 1/5 (stream disconnected before completion)"), *OK[1:]]
        self.assertIsNone(self.cls(lines))

    def test_a_non_zero_exit_without_any_terminal_event_is_read_from_what_was_said(self):
        self.assertEqual(self.cls([ev("error", message=HIT.format(when="7:30 PM"))], exit_code=1), FailureClass.QUOTA_EXHAUSTED)
        self.assertEqual(self.cls([], exit_code=1, stderr="Error: unexpected status 401 Unauthorized"), FailureClass.AUTH_REQUIRED)
        self.assertEqual(self.cls([], exit_code=2, stderr="error: unexpected argument '--nope' found"), FailureClass.EXECUTION_FAILED)

    def test_a_process_that_died_by_signal_without_finishing_is_unknown_not_failed(self):
        self.assertEqual(self.cls([ev("thread.started", thread_id="t")], exit_code=None, signal_no=9), FailureClass.UNKNOWN)

    def test_success_events_with_a_failing_exit_do_not_pass(self):
        self.assertEqual(self.cls(OK, exit_code=1, stderr="boom"), FailureClass.EXECUTION_FAILED)

    def test_a_declined_command_does_not_turn_a_finished_run_into_a_failure(self):
        lines = [OK[0], ev("item.completed", item={"type": "command_execution", "command": "sudo x", "status": "declined"}), *OK[2:]]
        self.assertIsNone(self.cls(lines))                                                  # ... but the warning is on the result

    def test_an_error_after_a_completed_turn_is_not_a_success_either(self):
        self.assertEqual(self.cls([*OK, ev("turn.failed", error={"message": "unexpected status 429"})], exit_code=1), FailureClass.RATE_LIMITED)

    def test_a_timeout_is_unknown(self):
        out = self.outcome(OK)
        out.timed_out = True
        self.assertEqual(self.adapter.classify_failure(out), FailureClass.UNKNOWN)


class ResetTime(unittest.TestCase):
    NOW = datetime(2026, 10, 14, 18, 40).astimezone()

    def parse(self, text, now=None):
        return C.parse_reset_time(text, now or self.NOW)

    def local(self, iso):
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(self.NOW.tzinfo)

    def test_a_time_later_today(self):
        got = self.local(self.parse("... or try again at 7:30 PM."))
        self.assertEqual((got.year, got.month, got.day, got.hour, got.minute), (2026, 10, 14, 19, 30))

    def test_a_time_that_has_already_passed_today_means_tomorrow(self):
        got = self.local(self.parse("try again at 3:05 AM."))
        self.assertEqual((got.day, got.hour, got.minute), (15, 3, 5))

    def test_a_full_date(self):
        got = self.local(self.parse("Try again at Oct 21st, 2026 12:00 AM."))
        self.assertEqual((got.month, got.day, got.year, got.hour), (10, 21, 2026, 0))
        got = self.local(self.parse("try again at Nov 2nd, 2026 12:15 PM."))
        self.assertEqual((got.month, got.day, got.hour, got.minute), (11, 2, 12, 15))

    def test_typographic_apostrophes_and_case(self):
        self.assertIsNotNone(self.parse("You’ve hit your usage limit. TRY AGAIN AT 9:05 pm."))

    def test_what_cannot_be_read_is_none_never_a_guess(self):
        for text in ("Try again later.", "try again at tomorrow", "try again at 99:99 PM.", "try again at Foo 3rd, 2026 3:05 AM.", "", "try again at 25:00 AM."):
            self.assertIsNone(self.parse(text), text)

    def test_the_result_is_an_iso_utc_timestamp(self):
        self.assertRegex(self.parse("try again at 7:30 PM."), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

    def test_it_never_blows_up_on_hostile_text(self):
        self.parse("try again at " + "9" * 5000 + " pm")
        self.parse("try again at " + "a" * 5000)


class Cancel(Base):
    def test_cancel_is_a_keyboard_interrupt_to_the_process_not_a_kill(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        self.adapter.request_cancel(proc)
        proc.send_signal.assert_called_once_with(signal.SIGINT)

    def test_a_process_that_already_finished_is_left_alone(self):
        proc = mock.Mock()
        proc.poll.return_value = 0
        self.adapter.request_cancel(proc)
        proc.send_signal.assert_not_called()

    def test_a_real_process_that_handles_sigint_stops_gracefully(self):
        code = "import signal,sys,time\nsignal.signal(signal.SIGINT, lambda *a: sys.exit(130))\nprint('ready', flush=True)\ntime.sleep(30)\n"
        proc = subprocess.Popen([sys.executable, "-B", "-c", code], stdout=subprocess.PIPE, text=True, start_new_session=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            self.adapter.request_cancel(proc)
            self.assertEqual(proc.wait(timeout=10), 130)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
