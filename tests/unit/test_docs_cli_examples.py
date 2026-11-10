import subprocess
import sys
import unittest

from tests import SRC

ROOT = SRC.parent


class DocsCliExamplesTests(unittest.TestCase):
    def test_documented_par_commands_parse(self):
        cp = subprocess.run(
            [sys.executable, "-B", "tests/tools/docs_cli_examples.py"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)

    def test_the_approval_example_uses_the_real_flag(self):
        text = (ROOT / "docs" / "en" / "USER_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("par task approve TASK --id N", text)
        self.assertNotIn("[APPROVAL_ID]", text)


if __name__ == "__main__":
    unittest.main()
