import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from par import PHASE
from par import doctor as D
from par.paths import APP_DIR_NAME, ENV_DATA_DIR, ensure_data_dir
from tests import SRC
from tests.helpers import run_cli, tree_digest

SK = "sk-" + "A1b2C3d4" * 4  # assembled at run time
EXAMPLES = SRC.parent / "config"


class DoctorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        (self.home / "Library" / "Application Support").mkdir(parents=True)
        self.data = self.home / "Library" / "Application Support" / APP_DIR_NAME

    def tearDown(self):
        self._tmp.cleanup()

    def init(self):
        ensure_data_dir(self.data, protected=())

    def put_config(self, name, text):
        (self.data / "config" / f"{name}.toml").write_text(text)

    def report(self):
        return D.run_doctor(self.data, home=self.home)

    def checks(self):
        return {c.name: c for c in self.report().checks}


class StatusTests(DoctorCase):
    def test_uninitialised_is_a_warning_and_creates_nothing(self):
        c = self.checks()
        self.assertEqual(c["data_dir"].status, D.WARN)
        self.assertIn("par init", c["data_dir"].detail)
        self.assertEqual(c["config"].status, D.PASS)
        self.assertIn("every external provider disabled", c["config"].detail)
        self.assertFalse(self.data.exists(), "doctor must not create the data directory")
        r = self.report()
        self.assertTrue(r.ok())
        self.assertFalse(r.ok(strict=True))

    def test_initialised_and_clean(self):
        self.init()
        r = self.report()
        c = {x.name: x for x in r.checks}
        for name in ("python", "data_dir", "disk_free", "data_dir_location", "config"):
            self.assertEqual(c[name].status, D.PASS, (name, c[name].detail))
        self.assertTrue(r.ok(strict=True), r.summary)
        self.assertEqual(r.summary[D.FAIL] + r.summary[D.WARN], 0)

    def test_every_provider_is_disabled_by_default(self):
        c = self.checks()
        providers = {k: v for k, v in c.items() if k.startswith("provider:")}
        self.assertEqual(len(providers), 6)
        self.assertTrue(all(v.status == D.INFO and "disabled" in v.detail for v in providers.values()))
        self.assertIn("never a fallback for agy", providers["provider:gemini_legacy"].detail)

    def test_wrong_mode_and_symlink_are_failures(self):
        self.data.mkdir(mode=0o755)
        os.chmod(self.data, 0o755)
        self.assertEqual(self.checks()["data_dir"].status, D.FAIL)
        shutil.rmtree(self.data)
        real = self.base / "real"
        real.mkdir(mode=0o700)
        os.symlink(real, self.data)
        c = self.checks()["data_dir"]
        self.assertEqual(c.status, D.FAIL)
        self.assertIn("symlink", c.detail)

    def test_missing_subdirectory_is_a_warning(self):
        self.init()
        (self.data / "config").rmdir()
        self.assertEqual(self.checks()["data_dir"].status, D.WARN)

    def test_data_dir_inside_a_protected_location_is_a_failure(self):
        bad = self.home / ".codex" / "par"
        bad.mkdir(parents=True, mode=0o700)
        os.chmod(bad, 0o700)
        c = {x.name: x for x in D.run_doctor(bad, home=self.home).checks}
        self.assertEqual(c["data_dir_location"].status, D.FAIL)
        self.assertIn("protected", c["data_dir_location"].detail)


class ConfigTests(DoctorCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_shipped_examples_give_warnings_for_placeholders_only(self):
        for name in ("profiles", "providers", "projects"):
            shutil.copy(EXAMPLES / f"{name}.example.toml", self.data / "config" / f"{name}.toml")
        c = self.checks()
        self.assertEqual(c["config"].status, D.WARN)
        self.assertIn("placeholder", c["config"].detail)
        self.assertEqual(c["workers"].status, D.INFO)
        self.assertIn("3 configured; 3 still have placeholder", c["workers"].detail)
        self.assertEqual(c["profile"].detail, "active profile: claude_main")
        self.assertEqual(self.report().summary[D.FAIL], 0)

    def test_invalid_toml_is_a_failure_not_a_traceback(self):
        self.put_config("providers", "schema_version = = 1\n")
        c = self.checks()["config"]
        self.assertEqual(c.status, D.FAIL)
        self.assertIn("invalid TOML", c.detail)

    def test_secret_in_config_fails_without_echoing_it(self):
        self.put_config("providers", f'schema_version = 1\n[providers.codex]\nkind = "codex"\nexecutable = "/x/{SK}"\n')
        r = self.report()
        self.assertEqual({c.name: c for c in r.checks}["config"].status, D.FAIL)
        self.assertNotIn(SK, D.render_text(r))
        self.assertNotIn(SK, json.dumps(r.to_dict()))

    def test_enabled_provider_is_flagged_and_executable_is_only_stat_checked(self):
        exe = self.base / "fake-codex"
        exe.write_text("#!/bin/sh\nexit 1\n")
        os.chmod(exe, 0o755)
        missing = self.base / "nope" / "codex"
        for path, status, expect in ((exe, D.INFO, "executable found"), (missing, D.WARN, "executable NOT found")):
            self.put_config("providers", f'schema_version = 1\n[providers.codex]\nkind = "codex"\nenabled = true\nexecutable = "{path}"\n')
            c = self.checks()["provider:codex"]
            self.assertEqual(c.status, status)
            self.assertIn(expect, c.detail)
        self.assertEqual(exe.read_text(), "#!/bin/sh\nexit 1\n")                                    # looked at, never run

    def test_enabled_codex_without_an_executable_and_other_enabled_providers(self):
        self.put_config("providers", 'schema_version = 1\n[providers.codex]\nkind = "codex"\nenabled = true\n'
                        '[providers.antigravity]\nkind = "antigravity"\nenabled = true\nexecutable = "/nowhere/agy"\n')
        checks = self.checks()
        self.assertEqual(checks["provider:codex"].status, D.WARN)
        self.assertIn("no `executable` is named", checks["provider:codex"].detail)
        self.assertEqual(checks["provider:antigravity"].status, D.WARN)
        self.assertIn("executable is missing", checks["provider:antigravity"].detail)             # P4: the adapter exists; the file does not

    def test_enabled_antigravity_is_only_stat_checked_and_other_kinds_still_have_no_adapter(self):
        exe = self.base / "fake-agy"
        exe.write_text("#!/bin/sh\nexit 1\n")
        os.chmod(exe, 0o755)
        self.put_config("providers", f'schema_version = 1\n[providers.antigravity]\nkind = "antigravity"\nenabled = true\nexecutable = "{exe}"\n'
                        '[providers.manual_handoff]\nkind = "manual_handoff"\nenabled = true\n')
        checks = self.checks()
        self.assertEqual(checks["provider:antigravity"].status, D.INFO)
        self.assertIn("the Antigravity adapter is built in", checks["provider:antigravity"].detail)
        self.assertIn("executable found", checks["provider:antigravity"].detail)
        self.assertEqual(checks["provider:manual_handoff"].status, D.WARN)
        self.assertIn("no adapter for it yet", checks["provider:manual_handoff"].detail)
        self.assertEqual(exe.read_text(), "#!/bin/sh\nexit 1\n")                                   # looked at, never run
        self.put_config("providers", 'schema_version = 1\n[providers.antigravity]\nkind = "antigravity"\nenabled = true\n')
        self.assertIn("no `executable` is named", self.checks()["provider:antigravity"].detail)

    def test_legacy_gemini_cannot_be_enabled_through_config(self):
        self.put_config("providers", 'schema_version = 1\n[providers.gemini_legacy]\nkind = "gemini_legacy"\nenabled = true\n')
        c = self.checks()
        self.assertEqual(c["config"].status, D.FAIL)
        self.assertEqual(c["provider:gemini_legacy"].status, D.INFO)

    def test_harness_settings_errors_are_reported(self):
        self.put_config("projects", 'schema_version = 1\n[harness]\nmax_depth = 99\n')
        self.assertEqual(self.checks()["config"].status, D.FAIL)


class SafetyGuarantees(DoctorCase):
    def test_writes_nothing(self):
        for prepare in (lambda: None, self.init):
            prepare()
            self.put_config("providers", 'schema_version = 1\n') if self.data.exists() else None
            before = (tree_digest(self.data), tree_digest(self.home))
            D.render_text(self.report())
            self.assertEqual(before, (tree_digest(self.data), tree_digest(self.home)))
        self.assertFalse((self.base / "stray").exists())

    def test_runs_no_external_program_and_uses_no_network(self):
        self.init()
        boom = mock.Mock(side_effect=AssertionError("doctor must not do this"))
        targets = [
            "subprocess.Popen", "subprocess.run", "subprocess.call", "subprocess.check_output",
            "os.system", "os.execv", "os.execvp", "os.posix_spawn", "os.fork", "socket.socket",
            "socket.create_connection", "socket.getaddrinfo",
        ]
        with contextlib_stack(*[mock.patch(t, boom) for t in targets]):
            r = self.report()
            D.render_text(r)
        self.assertEqual(r.summary[D.FAIL], 0)
        boom.assert_not_called()

    def test_output_is_redacted_and_home_is_shortened(self):
        self.init()
        text = D.render_text(self.report())
        self.assertNotIn(str(self.home), text)
        self.assertIn("~/Library/Application Support/" + APP_DIR_NAME, text)

    def test_text_report_has_a_summary_line(self):
        text = D.render_text(self.report())
        self.assertTrue(text.splitlines()[-1].startswith("Summary: PASS "))
        self.assertIn("read-only", text.splitlines()[0])


def contextlib_stack(*managers):
    import contextlib
    stack = contextlib.ExitStack()
    for m in managers:
        stack.enter_context(m)
    return stack


class CliTests(DoctorCase):
    def test_doctor_json_and_exit_codes(self):
        self.init()
        code, out, _ = run_cli("doctor", "--json", "--data-dir", str(self.data))
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["phase"], PHASE)
        self.assertEqual(set(data["summary"]), {"PASS", "WARN", "FAIL", "INFO"})
        self.assertTrue(all(set(c) == {"name", "status", "detail"} for c in data["checks"]))

    def test_strict_turns_warnings_into_exit_1(self):
        code, _, _ = run_cli("doctor", "--data-dir", str(self.data))            # uninitialised: WARN only
        self.assertEqual(code, 0)
        code, _, _ = run_cli("doctor", "--strict", "--data-dir", str(self.data))
        self.assertEqual(code, 1)

    def test_failure_gives_exit_1(self):
        self.data.mkdir(mode=0o755)
        os.chmod(self.data, 0o755)
        code, out, _ = run_cli("doctor", "--data-dir", str(self.data))
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] data_dir", out)

    def test_relative_data_dir_is_refused(self):
        code, _, err = run_cli("doctor", "--data-dir", "relative/dir")
        self.assertEqual(code, 1)
        self.assertIn("absolute", err)

    def test_init_creates_then_reports_ready(self):
        code, out, _ = run_cli("init", "--data-dir", str(self.data))
        self.assertEqual(code, 0)
        self.assertIn("created data directory", out)
        self.assertEqual(stat.S_IMODE(os.stat(self.data).st_mode), 0o700)
        code, out, _ = run_cli("init", "--data-dir", str(self.data))
        self.assertEqual((code, "created" in out), (0, False))
        self.assertIn("ready", out)

    def test_init_refuses_an_unsafe_existing_directory(self):
        self.data.mkdir(mode=0o755)
        os.chmod(self.data, 0o755)
        code, _, err = run_cli("init", "--data-dir", str(self.data))
        self.assertEqual(code, 1)
        self.assertIn("not safe", err)
        self.assertEqual(stat.S_IMODE(os.stat(self.data).st_mode), 0o755, "never chmod an existing directory")

    def test_env_override_selects_the_data_dir(self):
        target = self.base / "via_env"
        with mock.patch.dict(os.environ, {ENV_DATA_DIR: str(target)}):
            code, out, _ = run_cli("init")
        self.assertEqual(code, 0)
        self.assertTrue((target / "config").is_dir())

    def test_end_to_end_as_a_separate_process(self):
        env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC), "HOME": str(self.home)}
        cp = subprocess.run([sys.executable, "-B", "-m", "par", "init", "--data-dir", str(self.data)],
                            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        cp = subprocess.run([sys.executable, "-B", "-m", "par", "doctor", "--strict", "--data-dir", str(self.data)],
                            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("Summary: PASS", cp.stdout)


if __name__ == "__main__":
    unittest.main()
