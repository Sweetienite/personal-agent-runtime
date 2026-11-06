"""The section-12 one-command revalidation must never quietly spend quota or bypass the CLI sandbox."""
import tempfile
import unittest
from pathlib import Path

from tests.tools.codex_revalidate import revalidate, smoke


class FakeProbe:
    def __init__(self, available):
        self.available = available

    def __call__(self):
        return {"kind": "sandbox_probe", "nested_sandbox_available": self.available,
                "inherited_sandbox_check": None if self.available else 1}


class RevalidationGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="par-reval-test-")
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "evidence"

    def runner(self, calls):
        def run(argv, env_extra):
            calls.append((argv, env_extra))
            return 0, "ok\n"
        return run

    def test_check_only_runs_offline_steps_and_no_model_call(self):
        calls = []
        code, rec = revalidate(check_only=True, yes_use_quota=False, model="", out=self.out,
                               runner=self.runner(calls), probe=FakeProbe(True))
        self.assertEqual(code, 0)
        self.assertEqual(rec["real_model_calls"], 0)
        self.assertEqual([s["name"] for s in rec["steps"]], ["offline", "trust"])
        for argv, _ in calls:
            self.assertNotIn("smoke_codex.py", " ".join(argv))
        self.assertTrue(all(Path(s["log"]).is_file() for s in rec["steps"]))

    def test_real_calls_are_refused_without_an_explicit_acknowledgement(self):
        calls = []
        code, rec = revalidate(check_only=False, yes_use_quota=False, model="gpt-6-luna", out=self.out,
                               runner=self.runner(calls), probe=FakeProbe(True))
        self.assertEqual(code, 2)
        self.assertIn("yes-use-quota", rec["refused"])
        self.assertEqual(calls, [])                   # nothing ran at all

    def test_a_sandbox_blocked_host_is_refused_instead_of_bypassed(self):
        calls = []
        code, rec = revalidate(check_only=False, yes_use_quota=True, model="gpt-6-luna", out=self.out,
                               runner=self.runner(calls), probe=FakeProbe(False))
        self.assertEqual(code, 2)
        self.assertIn("nested Seatbelt", rec["refused"])
        self.assertIn("will not bypass", rec["refused"])
        self.assertEqual(calls, [])
        self.assertEqual(rec["steps"], [])             # not even the offline steps are dressed up as a pass
        for forbidden in ("--dangerously-bypass-approvals-and-sandbox", "danger-full-access"):
            self.assertNotIn(forbidden, " ".join(smoke("gpt-6-luna", self.out)))

    def test_the_real_smoke_uses_only_the_default_adapter_flags(self):
        calls = []
        code, rec = revalidate(check_only=False, yes_use_quota=True, model="gpt-6-luna", out=self.out,
                               runner=self.runner(calls), probe=FakeProbe(True))
        self.assertEqual(code, 0)
        self.assertEqual(rec["real_model_calls"], 2)
        self.assertEqual([s["name"] for s in rec["steps"]], ["offline", "trust", "real_smoke"])
        smoke = calls[-1][0]
        self.assertIn("smoke_codex.py", " ".join(smoke))
        for forbidden in ("--dangerously-bypass-approvals-and-sandbox", "--yolo",
                          "danger-full-access", "PAR_REVAL_SANDBOX_OVERRIDE"):
            self.assertNotIn(forbidden, " ".join(smoke))


if __name__ == "__main__":
    unittest.main()
