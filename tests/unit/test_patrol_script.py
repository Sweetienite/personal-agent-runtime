import os
import plistlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import SRC
from tests.helpers import tree_digest

OPS = SRC.parent / "ops"
SCRIPT = OPS / "par-harness-patrol.sh"
TEMPLATE = OPS / "com.par-runtime.harness-patrol.plist.template"

FAKE_PAR = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_PAR_ARGS"
cat "$FAKE_PAR_STDOUT"
exit "${FAKE_PAR_EXIT:-0}"
"""
FAKE_NOTIFIER = """#!/bin/sh
printf '%s\\n' "$@" >> "$FAKE_NOTIFIER_LOG"
printf -- '--- call ---\\n' >> "$FAKE_NOTIFIER_LOG"
"""


class PatrolCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.data = self.base / "data"
        (self.data / "harness").mkdir(parents=True)
        os.chmod(self.data, 0o700)
        self.par = self.base / "fake_par"
        self.par.write_text(FAKE_PAR)
        self.notifier = self.base / "fake_notifier"
        self.notifier.write_text(FAKE_NOTIFIER)
        for f in (self.par, self.notifier):
            os.chmod(f, 0o755)
        self.args_file = self.base / "par_args"
        self.stdout_file = self.base / "par_stdout"
        self.log = self.base / "notifier_log"

    def tearDown(self):
        self._tmp.cleanup()

    def run_patrol(self, stdout="Summary: FAIL 0 | WARN 1 | INFO 2 | differences 0\nBaseline from x: 0 difference(s)\n",
                   exit_code=0, par=None, data=None):
        self.stdout_file.write_text(stdout)
        env = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.base),
            "PAR_BIN": str(par or self.par), "PAR_NOTIFIER": str(self.notifier),
            "PAR_DATA_DIR": str(data or self.data),
            "FAKE_PAR_ARGS": str(self.args_file), "FAKE_PAR_STDOUT": str(self.stdout_file),
            "FAKE_PAR_EXIT": str(exit_code), "FAKE_NOTIFIER_LOG": str(self.log),
        }
        cp = subprocess.run(["/bin/sh", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=30)
        return cp

    def notifications(self):
        if not self.log.exists():
            return []
        return [c.strip().splitlines() for c in self.log.read_text().split("--- call ---\n") if c.strip()]


class Behaviour(PatrolCase):
    def test_healthy_run_is_silent_and_stores_the_report(self):
        cp = self.run_patrol()
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(self.notifications(), [])
        report = self.data / "harness" / "last_patrol.txt"
        self.assertIn("Summary: FAIL 0", report.read_text())
        self.assertEqual(stat.S_IMODE(os.stat(report).st_mode), 0o600)

    def test_it_only_ever_runs_the_read_only_check(self):
        self.run_patrol()
        self.assertEqual(self.args_file.read_text().split(), ["harness", "check"],
                         "never --save-baseline, never anything else")

    def test_failure_or_difference_notifies_with_the_summary(self):
        cp = self.run_patrol(stdout="Summary: FAIL 1 | WARN 0 | INFO 0\n", exit_code=1)
        self.assertEqual(cp.returncode, 0, "the job itself must always exit 0")
        calls = self.notifications()
        self.assertEqual(len(calls), 1)
        self.assertIn("Summary: FAIL 1 | WARN 0 | INFO 0", calls[0])
        self.assertIn("exit status 1 - run: par harness check", calls[0])

    def test_missing_baseline_notifies_even_when_the_check_passes(self):
        self.run_patrol(stdout="Baseline: none yet (use --save-baseline to record this state)\nSummary: FAIL 0 | WARN 0 | INFO 0\n")
        calls = self.notifications()
        self.assertEqual(len(calls), 1)
        self.assertIn("no baseline yet - run: par harness check", calls[0])

    def test_a_check_that_cannot_run_notifies(self):
        cp = self.run_patrol(par=self.base / "does_not_exist")
        self.assertEqual(cp.returncode, 0)
        calls = self.notifications()
        self.assertEqual(len(calls), 1)
        self.assertIn("the check could not run (exit 127)", calls[0])

    def test_missing_data_directory_notifies_and_creates_nothing(self):
        missing = self.base / "no_data"
        cp = self.run_patrol(data=missing)
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(len(self.notifications()), 1)
        self.assertFalse(missing.exists())

    def test_output_of_the_check_cannot_inject_commands(self):
        marker = self.base / "pwned"
        evil = f'Summary: $(touch {marker}) `touch {marker}` "q" \'s\' ; touch {marker}\n'
        self.run_patrol(stdout=evil, exit_code=1)
        self.assertFalse(marker.exists(), "text from the check must stay data")
        calls = self.notifications()
        self.assertEqual(len(calls), 1)
        self.assertIn(evil.strip(), calls[0], "and it is passed on verbatim as an argument")

    def test_applescript_source_is_static(self):
        self.run_patrol(stdout="Summary: x\n", exit_code=1)
        call = self.notifications()[0]
        source = "\n".join(call[:6])
        self.assertIn("display notification (item 1 of argv)", source)
        self.assertNotIn("Summary: x", source)

    def test_writes_only_two_files_inside_the_data_directory(self):
        self.args_file.write_text("")      # fixture files exist beforehand, so any new name is the patrol's
        self.stdout_file.write_text("")
        outside = {p.name for p in self.base.iterdir()}
        before = tree_digest(self.data)
        self.run_patrol()
        self.assertNotEqual(before, tree_digest(self.data))
        self.assertEqual({p.name for p in self.base.iterdir()}, outside, "nothing new outside the data directory")
        self.assertEqual(sorted(os.listdir(self.data / "harness")), ["last_patrol.err", "last_patrol.txt"])


class LaunchdTemplate(unittest.TestCase):
    def render(self, home="/Users/someone"):
        return plistlib.loads(TEMPLATE.read_text().replace("__HOME__", home).encode())

    def test_template_is_a_valid_plist_with_the_expected_schedule(self):
        doc = self.render()
        self.assertEqual(doc["Label"], "com.par-runtime.harness-patrol")
        self.assertEqual(doc["StartCalendarInterval"], {"Weekday": 1, "Hour": 9, "Minute": 0})
        self.assertEqual(doc["ProgramArguments"][0], "/bin/sh")
        self.assertEqual(doc["ProgramArguments"][1], "/Users/someone/Documents/Personal Agent Runtime/ops/par-harness-patrol.sh")
        self.assertTrue(doc["StandardErrorPath"].startswith("/Users/someone/Library/Application Support/PersonalAgentRuntime/"))

    def test_job_is_not_resident(self):
        doc = self.render()
        self.assertFalse(doc["RunAtLoad"])
        for key in ("KeepAlive", "StartInterval", "WatchPaths", "QueueDirectories", "Sockets"):
            self.assertNotIn(key, doc, key)

    def test_every_placeholder_is_filled(self):
        self.assertNotIn("__HOME__", TEMPLATE.read_text().replace("__HOME__", "/h"))

    @unittest.skipUnless(shutil.which("plutil"), "plutil is macOS only")
    def test_plutil_accepts_the_rendered_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False) as f:
            f.write(TEMPLATE.read_text().replace("__HOME__", "/Users/someone"))
        try:
            cp = subprocess.run(["plutil", "-lint", f.name], capture_output=True, text=True)
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
