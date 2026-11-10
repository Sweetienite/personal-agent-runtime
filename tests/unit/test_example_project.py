import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "ordinary-git-project"
CONTRACT = ROOT / "examples" / "task-contract.json"


class ExampleProjectTests(unittest.TestCase):
    def test_the_public_example_contract_runs_one_test_and_flips_with_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp) / "ordinary-git-project"
            shutil.copytree(EXAMPLE, work)
            check = json.loads(CONTRACT.read_text(encoding="utf-8"))["acceptance"]["checks"][0]
            argv = [sys.executable if part == "python3" else part for part in check["argv"]]

            before = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=60)
            output = before.stdout + before.stderr
            self.assertNotEqual(before.returncode, 0, output)
            self.assertIn("Ran 1 test", output)

            calc = work / "app" / "calc.py"
            calc.write_text(calc.read_text(encoding="utf-8").replace("return a - b", "return a + b"), encoding="utf-8")
            after = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=60)
            output = after.stdout + after.stderr
            self.assertEqual(after.returncode, 0, output)
            self.assertIn("Ran 1 test", output)


if __name__ == "__main__":
    unittest.main()
