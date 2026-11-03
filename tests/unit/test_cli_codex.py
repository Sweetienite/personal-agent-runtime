"""`par` with the real Codex adapter wired in through configuration (the fake `codex` program stands in for the CLI).

Everything a person would do in P3 from a terminal: look at health, probe offline, submit, run, review, accept - on a fixture project.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par import cli_task
from par.config import load_config
from par.fixtures import FIXED, create_fixture
from par.paths import APP_DIR_NAME, ensure_data_dir
from par.providers import codex as C
from tests import codex_samples as S
from tests.fake_codex import FakeCodex
from tests.helpers import run_cli

PROFILES = '''schema_version = 1
active_profile = "default"

[workers.w_code]
provider = "codex"
model = "gpt-fake-1"
capabilities = ["read", "edit", "review"]

[profiles.default]
coordinator = "claude_desktop"
default_worker = "w_code"
max_parallel_workers = 1
worker_failure_policy = "ask"
additional_paid_credits = false

[profiles.default.roles.code]
primary = "w_code"
fallback = []
'''


def providers_toml(exe, enabled=True):
    return f'schema_version = 1\n\n[providers.codex]\nkind = "codex"\nenabled = {str(enabled).lower()}\nexecutable = "{exe}"\n'


def contract(**over):
    doc = {"schema_version": 1, "project_id": "fixture", "title": "Fix add()", "description": "add() returns the wrong value; fix it.",
           "role": "code", "risk": "R2", "allowed_paths": ["app/"],
           "acceptance": {"criteria": ["tests pass"], "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q"], "timeout": 60, "expect_exit": 0}]},
           "idempotency_key": "fix-add-1"}
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
        self.fake = FakeCodex(self.base / "fake")
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": str(self.fake.home)})       # where the login would be
        self._env.start()
        self.fixture = create_fixture(self.data, "fixture", home=self.home).entry.path
        self.configure(enabled=True)
        patch = mock.patch.object(C.CodexAdapter, "signature_problem", return_value=None)       # a fake program has no signature
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._env.stop)
        self.addCleanup(C.forget_caches)
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
    def test_the_registry_has_codex_only_when_enabled_with_an_absolute_executable(self):
        cfg = load_config(self.data / "config" / "profiles.toml", self.data / "config" / "providers.toml").config
        self.assertEqual(cli_task.build_adapters(cfg).ids(), ["codex"])
        adapter = cli_task.build_adapters(cfg).create("codex")
        self.assertEqual((adapter.executable, adapter.verify_signature), (str(self.fake.exe), True))
        self.configure(enabled=False)
        cfg = load_config(self.data / "config" / "profiles.toml", self.data / "config" / "providers.toml").config
        self.assertEqual(cli_task.build_adapters(cfg).ids(), [])

    def test_a_tilde_in_the_executable_path_is_expanded(self):
        (self.home / "bin").mkdir()
        providers = 'schema_version = 1\n[providers.codex]\nkind = "codex"\nenabled = true\nexecutable = "~/bin/codex"\n'
        (self.data / "config" / "providers.toml").write_text(providers)
        cfg = load_config(self.data / "config" / "profiles.toml", self.data / "config" / "providers.toml").config
        self.assertEqual(cli_task.build_adapters(cfg).create("codex").executable, str(self.home / "bin" / "codex"))


class Health(World):
    def test_plain_health_never_starts_the_cli(self):
        code, out, err = self.par("health")
        self.assertEqual(code, 0, err)
        self.assertIn("adapters registered: codex", out)
        self.assertIn("worker w_code", out)
        self.assertEqual(self.fake.calls(), [])

    def test_probe_asks_offline_and_shows_what_it_found(self):
        code, out, err = self.par("health", "--probe")
        self.assertEqual(code, 0, err)
        self.assertIn("probe   codex: AVAILABLE", out)
        self.assertIn("(version 0.162.0-alpha.17.2)", out)
        self.assertIn("extras switched off: 22 feature flag(s), MCP server(s) node_repl", out)
        self.assertEqual(self.fake.exec_calls(), [])
        self.assertTrue(self.fake.offline_calls())

    def test_probe_json(self):
        code, out, _ = self.par("health", "--probe", "--json")
        probe = json.loads(out)["probes"][0]
        self.assertEqual((probe["provider"], probe["status"]), ("codex", "AVAILABLE"))
        self.assertTrue(probe["facts"]["isolation"]["verified"])
        self.assertEqual(probe["facts"]["login"], "ChatGPT")

    def test_probe_reports_trouble_plainly_and_still_exits_zero(self):
        self.fake.configure(login=["Not logged in", 1])
        code, out, _ = self.par("health", "--probe")
        self.assertEqual(code, 0)
        self.assertIn("probe   codex: AUTH_REQUIRED", out)
        self.assertIn("sign in", out)

    def test_probe_with_nothing_enabled_says_so(self):
        self.configure(enabled=False)
        code, out, _ = self.par("health", "--probe")
        self.assertEqual(code, 0)
        self.assertIn("probe   no adapter is registered", out)
        self.assertEqual(self.fake.calls(), [])


class ReadOnlyFlow(World):
    def test_a_question_about_the_fixture(self):
        self.fake.configure(exec={"events": S.ok_run("add() subtracts.", S.command("cat app/calc.py"))})
        tid = self.submit(risk="R1", allowed_paths=[], acceptance={"criteria": ["answer"], "checks": []})
        code, out, err = self.par("task", "run", tid)
        self.assertEqual(code, 0, err)
        self.assertIn("REVIEW_REQUIRED", out)
        code, out, _ = self.par("task", "result", tid)
        self.assertIn("worker's own summary (NOT verified): add() subtracts.", out)
        self.assertIn("usage   input 1200 (cached 800), output 90 (reasoning 30) tokens, as the vendor counted them", out)
        self.assertIn("boundary  0 violation(s)", out)
        self.assertEqual(self.git_status(), "")


class WriteFlow(World):
    def test_submit_approve_run_review_accept_and_nothing_is_applied(self):
        self.fake.configure(exec={"events": S.ok_run("fixed add()", S.file_change("app/calc.py")), "writes": {"app/calc.py": FIXED}})
        tid = self.submit()
        code, out, _ = self.par("task", "status", tid)
        self.assertIn("PERMISSION_WAIT", out)
        self.assertIn("WRITE_PERMISSION", out)
        self.assertEqual(self.par("task", "approve", tid)[0], 0)
        code, out, err = self.par("task", "run", tid)
        self.assertEqual((code, "REVIEW_REQUIRED" in out), (0, True), err)
        code, out, _ = self.par("task", "result", tid)
        self.assertIn("check unit: PASS", out)
        self.assertIn("change  1 file(s) +1 -1", out)
        code, out, _ = self.par("task", "diff", tid)
        self.assertEqual(code, 0)
        self.assertIn("+    return a + b", out)
        self.assertEqual(self.par("task", "accept", tid)[0], 0)
        self.assertEqual(self.git_status(), "")                                                    # the fixture itself is untouched
        self.assertIn("return a - b", (self.fixture / "app" / "calc.py").read_text())

    def test_a_wrong_fix_ends_failed_with_the_failing_check_visible(self):
        self.fake.configure(exec={"events": S.ok_run("done, tests pass"), "writes": {"app/calc.py": "def add(a, b):\n    return 0\n"}})
        tid = self.submit()
        self.par("task", "approve", tid)
        code, out, _ = self.par("task", "run", tid)
        self.assertEqual(code, 1)
        self.assertIn("FAILED", out)
        code, out, _ = self.par("task", "result", tid)
        self.assertIn("check unit: FAIL", out)
        self.assertEqual(self.par("task", "accept", tid)[0], 1)                                    # it cannot be accepted

    def test_an_exhausted_quota_is_shown_with_what_to_do_next(self):
        self.fake.configure(exec={"events": S.failed_run(S.reset_in(2)), "exit": 1})
        tid = self.submit()
        self.par("task", "approve", tid)
        code, out, _ = self.par("task", "run", tid)
        self.assertEqual(code, 1)
        self.assertIn("QUOTA_EXHAUSTED", out)
        code, out, _ = self.par("health")
        self.assertRegex(out, r"QUOTA_EXHAUSTED  until 20\d\d-\d\d-\d\dT")                         # a timestamp the vendor gave, not "cleared by you"
        self.assertIn("NOT READY", out)
        code, out, _ = self.par("health", "clear", "codex")
        self.assertIn("cleared 1 health record(s) for codex", out)


if __name__ == "__main__":
    unittest.main()
