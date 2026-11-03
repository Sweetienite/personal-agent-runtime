"""`par` with the real Antigravity adapter wired in through configuration (the fake `agy` program stands in for the CLI).

Everything a person would do in P4 from a terminal: look at health, probe, sign in (planned, never actually started here), submit, run,
review - on a fixture project.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par import cli_task
from par.config import load_config
from par.fixtures import create_fixture
from par.paths import APP_DIR_NAME, ensure_data_dir
from par.providers import antigravity as A
from tests import agy_samples as S
from tests.fake_agy import FakeAgy
from tests.helpers import run_cli

MODEL = "gemini-3.8-flash-medium"
PROFILES = f'''schema_version = 1
active_profile = "default"

[workers.w_agy]
provider = "antigravity"
model = "{MODEL}"
capabilities = ["read", "edit"]

[profiles.default]
coordinator = "claude_desktop"
default_worker = "w_agy"
max_parallel_workers = 1
worker_failure_policy = "ask"
additional_paid_credits = false

[profiles.default.roles.analysis]
primary = "w_agy"
fallback = []
'''


def providers_toml(exe, enabled=True):
    return f'schema_version = 1\n\n[providers.antigravity]\nkind = "antigravity"\nenabled = {str(enabled).lower()}\nexecutable = "{exe}"\n'


def contract(**over):
    doc = {"schema_version": 1, "project_id": "fixture", "title": "Question", "description": "What does add() return for add(2, 3)?",
           "role": "analysis", "risk": "R1", "allowed_paths": [], "acceptance": {"criteria": ["one sentence"], "checks": []},
           "idempotency_key": "q-1"}
    doc.update(over)
    return doc


class World(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        (self.home / "Library" / "Application Support").mkdir(parents=True)
        self.data = self.home / "Library" / "Application Support" / APP_DIR_NAME
        ensure_data_dir(self.data, protected=())
        self.fake = FakeAgy(self.base / "fake", models=[MODEL])
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        self.fixture = create_fixture(self.data, "fixture", home=self.home).entry.path
        self.configure(enabled=True)
        for patch in (mock.patch.object(A.AntigravityAdapter, "signature_problem", return_value=None),    # a fake program has no signature
                      mock.patch.object(A, "real_home", return_value=self.base / "real_home")):             # never link the user's real keychains
            patch.start()
            self.addCleanup(patch.stop)
        (self.base / "real_home" / "Library" / "Keychains").mkdir(parents=True)
        self.addCleanup(self._env.stop)
        self.addCleanup(A.forget_caches)
        self.addCleanup(self._tmp.cleanup)
        self.n = 0

    def configure(self, enabled):
        cfg = self.data / "config"
        for name, text in (("profiles.toml", PROFILES), ("providers.toml", providers_toml(self.fake.exe, enabled))):
            fd = os.open(cfg / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(text)

    def par(self, *argv):
        return run_cli(*argv, "--data-dir", str(self.data))

    def submit(self, **over):
        self.n += 1
        path = self.base / f"contract{self.n}.json"
        path.write_text(json.dumps(contract(idempotency_key=f"k{self.n}", **over)))
        code, out, err = self.par("task", "submit", "--contract", str(path))
        self.assertEqual(code, 0, err)
        return out.split()[1]

    def git_status(self):
        return subprocess.run(["git", "-C", str(self.fixture), "status", "--porcelain"], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)}).stdout


class Wiring(World):
    def test_the_registry_has_antigravity_only_when_enabled_with_an_absolute_executable(self):
        cfg = load_config(self.data / "config" / "profiles.toml", self.data / "config" / "providers.toml").config
        self.assertEqual(cli_task.build_adapters(cfg).ids(), ["antigravity"])
        adapter = cli_task.build_adapters(cfg).create("antigravity")
        self.assertEqual((adapter.executable, adapter.verify_signature, adapter.models), (str(self.fake.exe), True, (MODEL,)))
        self.configure(enabled=False)
        cfg = load_config(self.data / "config" / "profiles.toml", self.data / "config" / "providers.toml").config
        self.assertEqual(cli_task.build_adapters(cfg).ids(), [])

    def test_a_tilde_in_the_executable_path_is_expanded(self):
        (self.home / "bin").mkdir()
        (self.data / "config" / "providers.toml").write_text(
            'schema_version = 1\n[providers.antigravity]\nkind = "antigravity"\nenabled = true\nexecutable = "~/bin/agy"\n')
        cfg = load_config(self.data / "config" / "profiles.toml", self.data / "config" / "providers.toml").config
        self.assertEqual(cli_task.build_adapters(cfg).create("antigravity").executable, str(self.home / "bin" / "agy"))


class Health(World):
    def test_plain_health_never_starts_the_cli(self):
        code, out, err = self.par("health")
        self.assertEqual(code, 0, err)
        self.assertIn("adapters registered: antigravity", out)
        self.assertIn("worker w_agy", out)
        self.assertEqual(self.fake.calls(), [])

    def test_probe_asks_for_the_version_the_sign_in_and_the_models_and_calls_no_model(self):
        code, out, err = self.par("health", "--probe")
        self.assertEqual(code, 0, err)
        self.assertIn("probe   antigravity: AVAILABLE", out)
        self.assertIn("(version 1.3.5)", out)
        self.assertEqual(self.fake.runs(), [])
        self.assertEqual([c["kind"] for c in self.fake.offline_calls()], ["version", "models", "usage"])      # sign-in (and catalog) first, quota after
        for call in self.fake.offline_calls():
            self.assertEqual(call["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"], "true")
            self.assertEqual(call["stdin"], None)

    def test_probe_json(self):
        code, out, _ = self.par("health", "--probe", "--json")
        probe = json.loads(out)["probes"][0]
        self.assertEqual((probe["provider"], probe["status"]), ("antigravity", "AVAILABLE"))
        self.assertEqual(probe["facts"]["login"], "signed in")
        self.assertEqual(probe["facts"]["signature"], "valid, Google")
        self.assertEqual(probe["facts"]["models_in_catalog"], [MODEL])
        self.assertEqual((probe["facts"]["configured_models_missing"], probe["facts"]["tested_version"]), ([], True))
        self.assertEqual(len(probe["facts"]["usage"]), 4)
        self.assertEqual(set(probe["facts"]["usage"][0]), {"group", "window", "remaining", "resets_at"})
        self.assertEqual(probe["facts"]["usage"][0]["window"], "Weekly Limit Remaining")

    def test_probe_reports_trouble_plainly_and_still_exits_zero(self):
        for state, status, words in (("none", "AUTH_REQUIRED", "par provider login antigravity"), ("locked", "AUTH_REQUIRED", "keychain is locked"),
                                     ("weird", "UNHEALTHY", "could not confirm")):
            A.forget_caches()
            self.fake.configure(login=state)
            code, out, _ = self.par("health", "--probe")
            self.assertEqual(code, 0, state)
            self.assertIn(f"probe   antigravity: {status}", out, state)
            self.assertIn(words, out, state)
            self.assertNotIn("usage", [c["kind"] for c in self.fake.calls()], state)               # `-p /usage` waits 60 s when signed out: never asked

    def test_an_untested_version_and_a_model_the_account_does_not_offer_are_notes_not_failures(self):
        self.fake.configure(version="9.9.9", models=["some-other-model"])
        code, out, _ = self.par("health", "--probe")
        self.assertIn("probe   antigravity: AVAILABLE", out)
        self.assertIn("untested version (tested: 1.3.5)", out)
        self.assertIn(f"configured model(s) not offered to this account: {MODEL}", out)

    def test_probe_with_nothing_enabled_says_so(self):
        self.configure(enabled=False)
        code, out, _ = self.par("health", "--probe")
        self.assertEqual(code, 0)
        self.assertIn("probe   no adapter is registered", out)
        self.assertEqual(self.fake.calls(), [])


class ProviderLogin(World):
    def test_print_only_shows_the_plan_and_creates_nothing(self):
        before = sorted(p.name for p in self.data.iterdir())
        code, out, err = self.par("provider", "login", "--print-only")
        self.assertEqual(code, 0, err)
        self.assertIn("It runs in the Runtime's own home, not in your ~/.gemini", out)
        self.assertIn(str(self.data / "agy_login_home"), out)
        self.assertIn("(--print-only: nothing was created or started)", out)
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), before)
        self.assertEqual(self.fake.calls(), [])

    def test_the_real_command_starts_the_cli_itself_with_a_clean_environment_in_the_runtimes_home_and_removes_the_keychain_link_afterwards(self):
        leaks = {"GEMINI_API_KEY": "sk-test-not-real-1", "TERM": "xterm-256color", "HTTPS_PROXY": "http://proxy.invalid:3128"}
        ambient = {**leaks, "HOME": str(self.home), "PATH": "/evil/bin"}                           # exactly this, not the machine's own proxy settings
        home = self.data / "agy_login_home"
        seen = {}

        def start(argv, env=None, cwd=None):
            seen.update(argv=argv, env=env, cwd=cwd, link_while_running=os.path.islink(home / "Library" / "Keychains"))
            proc = mock.Mock()
            proc.wait.return_value = 0
            return proc

        with mock.patch.dict(os.environ, ambient, clear=True), mock.patch("par.cli_task.subprocess.Popen", side_effect=start):
            code, out, err = self.par("provider", "login")
        self.assertEqual(code, 0, err)
        self.assertEqual(seen["argv"], [str(self.fake.exe)])
        self.assertEqual(seen["env"], {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                                       "AGY_CLI_DISABLE_AUTO_UPDATE": "true", "TERM": "xterm-256color", "HTTPS_PROXY": "http://proxy.invalid:3128"})
        self.assertEqual(seen["cwd"], str(self.data / "scratch" / "agy_login_cwd"))
        if sys.platform == "darwin":
            self.assertTrue(seen["link_while_running"])                                            # the CLI needs the link while it runs ...
        self.assertFalse(os.path.lexists(home / "Library"))                                        # ... and it is gone when it has ended
        self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
        settings = json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text())
        self.assertEqual(settings["toolPermission"], "strict")
        self.assertEqual(self.fake.calls(), [])                                                   # the fake itself was never started (Popen was mocked)

    def test_the_exit_code_of_the_cli_is_the_commands_exit_code(self):
        proc = mock.Mock()
        proc.wait.return_value = 3
        with mock.patch("par.cli_task.subprocess.Popen", return_value=proc):
            code, _, _ = self.par("provider", "login")
        self.assertEqual(code, 3)

    def test_ctrl_c_while_the_cli_runs_is_left_to_the_cli(self):
        proc = mock.Mock()
        proc.wait.side_effect = [KeyboardInterrupt(), 0]                                           # the CLI got the same Ctrl-C and ended
        with mock.patch("par.cli_task.subprocess.Popen", return_value=proc):
            code, _, _ = self.par("provider", "login")
        self.assertEqual(code, 0)
        self.assertEqual(proc.wait.call_count, 2)

    def test_it_refuses_a_provider_that_is_not_antigravity_or_not_enabled_or_has_no_executable(self):
        code, _, err = self.par("provider", "login", "codex")
        self.assertEqual(code, 2)
        self.assertIn("is not an antigravity provider", err)
        self.configure(enabled=False)
        code, _, err = self.par("provider", "login")
        self.assertEqual(code, 2)
        self.assertIn("enable the provider", err)
        (self.data / "config" / "providers.toml").write_text('schema_version = 1\n[providers.antigravity]\nkind = "antigravity"\nenabled = true\n')
        code, _, err = self.par("provider", "login")
        self.assertEqual(code, 2)
        self.assertIn("enable the provider", err)

    def test_it_never_starts_a_binary_that_fails_the_signature_check(self):
        with mock.patch.object(A.AntigravityAdapter, "signature_problem", return_value="the executable is not signed by Google (team: none)"), \
                mock.patch("par.cli_task.subprocess.Popen") as popen:
            code, _, err = self.par("provider", "login")
        self.assertEqual(code, 2)
        self.assertIn("not signed by Google", err)
        popen.assert_not_called()
        self.assertFalse((self.data / "agy_login_home").exists())

    def test_a_cli_that_cannot_be_started_is_a_plain_error_and_leaves_no_keychain_link_behind(self):
        with mock.patch("par.cli_task.subprocess.Popen", side_effect=OSError(8, "Exec format error")):
            code, _, err = self.par("provider", "login")
        self.assertEqual(code, 2)
        self.assertIn("could not start", err)
        self.assertIn("Exec format error", err)
        self.assertFalse(os.path.lexists(self.data / "agy_login_home" / "Library"))

    def test_a_missing_data_directory_is_a_plain_error(self):
        code, _, err = run_cli("provider", "login", "--data-dir", str(self.base / "nowhere"))
        self.assertEqual(code, 2)
        self.assertIn("par init", err)


class ReadOnlyFlow(World):
    def test_a_question_about_the_fixture(self):
        self.fake.configure(run={"events": [{"$init": True}, S.user_step(), S.tool("view_file", params={"AbsolutePath": "app/calc.py"}),
                                            S.delta("add() subtracts.\n", "DONE"), S.checkpoint(), S.result("add() subtracts.\n")]})
        tid = self.submit()
        code, out, err = self.par("task", "run", tid)
        self.assertEqual(code, 0, err)
        self.assertIn("REVIEW_REQUIRED", out)
        code, out, _ = self.par("task", "result", tid)
        self.assertIn("worker's own summary (NOT verified): add() subtracts.", out)
        self.assertIn("usage   input 1200 (cached 800), output 90 (reasoning 30) tokens, as the vendor counted them", out)
        self.assertIn("boundary  0 violation(s)", out)
        self.assertEqual(self.git_status(), "")

    def test_a_soft_denial_is_visible_in_the_result_a_person_reads(self):
        self.fake.configure(run={"events": [{"$init": True}, S.user_step(), S.delta("could not.\n", "DONE"), S.checkpoint(), S.result("could not.\n")],
                                 "writes": {"app/calc.py": "x = 1\n"}})
        tid = self.submit()
        code, out, err = self.par("task", "run", tid)
        self.assertEqual(code, 0, err)
        code, out, _ = self.par("task", "result", tid)
        self.assertIn('a tool was soft-denied: the "write_file" permission', out)
        self.assertEqual(self.git_status(), "")

    def test_a_cli_that_is_not_signed_in_is_a_failed_attempt_with_the_way_forward(self):
        self.fake.configure(login="none")
        tid = self.submit()
        code, out, _ = self.par("task", "run", tid)
        self.assertEqual(code, 1)
        self.assertIn("FAILED", out)
        code, out, _ = self.par("task", "status", tid)
        self.assertIn("par provider login antigravity", out)
        self.assertEqual(self.fake.runs(), [])


if __name__ == "__main__":
    unittest.main()
