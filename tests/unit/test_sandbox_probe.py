"""The nested-sandbox diagnostic must stay offline and must not invent a pass."""
import unittest

from tests.tools.sandbox_probe import MARKER, SANDBOX_EXEC, probe_sandbox


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class OfflineSandboxProbe(unittest.TestCase):
    def _calls(self, result):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return result

        return calls, run

    def test_it_only_ever_asks_for_a_trivial_sandbox_and_never_calls_a_model(self):
        calls, run = self._calls(FakeCompleted(stdout=MARKER + "\n"))
        out = probe_sandbox(runner=run, system="Darwin")
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertEqual(argv[0], SANDBOX_EXEC)
        self.assertEqual(argv[-2:], ["/bin/echo", MARKER])
        self.assertLessEqual(kw["timeout"], 10)
        self.assertEqual((out["model_calls"], out["network_calls"], out["credential_reads"], out["installs"]), (0, 0, 0, 0))
        self.assertIs(out["nested_sandbox_available"], True)
        self.assertTrue(out["not_tested"])

    def test_a_blocked_nested_sandbox_is_reported_not_hidden(self):
        calls, run = self._calls(FakeCompleted(
            stdout="", stderr="sandbox-exec: sandbox_apply: Operation not permitted", returncode=71))
        out = probe_sandbox(runner=run, system="Darwin")
        self.assertIs(out["nested_sandbox_available"], False)
        self.assertIn("BLOCKED", out["detail"])
        # Raw command output (which may contain host paths) is never copied into the record.
        self.assertNotIn("sandbox-exec:", out["detail"])

    def test_a_non_macos_host_is_not_claimed_as_tested(self):
        calls, run = self._calls(FakeCompleted(stdout=MARKER))
        out = probe_sandbox(runner=run, system="Linux")
        self.assertEqual(calls, [])
        self.assertIsNone(out["nested_sandbox_available"])
        self.assertIn("not applicable", out["detail"])

    def test_a_missing_binary_or_timeout_is_a_fact_not_an_exception(self):
        def missing(argv, **kw):
            raise FileNotFoundError(SANDBOX_EXEC)

        self.assertIn("missing", probe_sandbox(runner=missing, system="Darwin")["detail"])

        def slow(argv, **kw):
            raise TimeoutError("hung")

        self.assertIn("probe failed", probe_sandbox(runner=slow, system="Darwin")["detail"])

    def test_exit_zero_without_the_marker_is_not_a_pass(self):
        calls, run = self._calls(FakeCompleted(stdout="", returncode=0))
        out = probe_sandbox(runner=run, system="Darwin")
        self.assertIs(out["nested_sandbox_available"], False)


if __name__ == "__main__":
    unittest.main()
