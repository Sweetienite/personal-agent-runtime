import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

import par
from par.cli import main
from tests import SRC


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse exits on usage errors
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class VersionTests(unittest.TestCase):
    def test_version_text(self):
        code, out, _ = run_main("version")
        self.assertEqual(code, 0)
        self.assertIn(f"par {par.__version__} (phase {par.PHASE})", out)

    def test_version_json(self):
        code, out, _ = run_main("version", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["name"], "par")
        self.assertEqual(data["version"], par.__version__)
        self.assertEqual(data["phase"], par.PHASE)
        self.assertEqual(data["release_status"], par.RELEASE_STATUS)
        self.assertIn("released 2026-11-09", data["release_status"])
        self.assertIn("P3 gate partial", data["release_status"])

    def test_phase_matches_dev_number(self):
        # 1.0.0.devN <-> phase PN, so the two can never drift apart silently; the final release is "1.0.0" / "RELEASE", nothing else.
        if par.__version__ == "1.0.0":
            self.assertEqual(par.PHASE, "RELEASE")
        else:
            self.assertEqual(par.__version__, f"1.0.0.dev{par.PHASE[1:]}")

    def test_unknown_command_is_usage_error(self):
        code, _, err = run_main("no-such-command")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_no_command_is_usage_error(self):
        code, _, _ = run_main()
        self.assertEqual(code, 2)

    def test_python_dash_m_entry_point(self):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SRC)}
        cp = subprocess.run(
            [sys.executable, "-B", "-m", "par", "version"], capture_output=True, text=True, env=env, timeout=30
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn(f"par {par.__version__}", cp.stdout)


if __name__ == "__main__":
    unittest.main()
