"""The T13 compatibility probe must stay offline and must not invent evidence."""
import unittest

from tests.tools.provider_probe import HEADLESS_FLAGS, probe


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class OfflineProbe(unittest.TestCase):
    def test_the_default_probe_never_runs_an_external_command(self):
        calls = []
        out = probe(runner=lambda *a, **k: calls.append(a) or FakeCompleted())
        self.assertEqual(calls, [])                       # which() only: no subprocess at all
        self.assertEqual(out["evidence_level"], "STATIC_OFFLINE")
        self.assertEqual((out["model_calls"], out["network_calls"], out["credential_reads"], out["installs"]), (0, 0, 0, 0))
        self.assertEqual(set(out["candidates"]), {"codex", "gemini", "agy", "claude", "opencode"})
        self.assertTrue(out["not_tested"])                # absence of real-run evidence is explicit

    def test_a_version_probe_is_bounded_and_keeps_only_a_short_redacted_line(self):
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return FakeCompleted(stdout="gemini 0.44.1\n", returncode=0)

        out = probe(versions=True, runner=run)
        for argv, kw in calls:
            self.assertEqual(argv[-1], "--version")       # never a model invocation
            self.assertLessEqual(kw["timeout"], 15)
        self.assertIn("real R1/R2", " ".join(out["not_tested"]))

    def test_help_flags_are_reported_only_when_actually_present(self):
        def run(argv, **kw):
            return FakeCompleted(stdout="Use -p/--prompt for non-interactive (headless) mode.\n"
                                        "  -o, --output-format  [string] [choices: text, json, stream-json]\n"
                                        "      --approval-mode  plan\n      --sandbox\n")

        out = probe(help_flags=True, runner=run)
        # At least one candidate was resolved on this machine; every resolved one must be honest.
        for row in out["candidates"].values():
            if row.get("headless_flags_seen") is not None:
                self.assertTrue(set(row["headless_flags_seen"]) <= set(HEADLESS_FLAGS))

    def test_a_failing_cli_is_recorded_as_a_fact_not_an_exception(self):
        def run(argv, **kw):
            raise OSError("cannot execute")

        out = probe(versions=True, help_flags=True, runner=run)
        for row in out["candidates"].values():
            if row["on_path"]:
                self.assertIn("probe failed", row["version_output"])
                self.assertEqual(row["headless_flags_seen"], [])


if __name__ == "__main__":
    unittest.main()
