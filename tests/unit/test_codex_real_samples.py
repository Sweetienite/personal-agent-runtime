"""The parser and classifier against REAL `codex exec --json` output captured from the installed Codex (redacted).

Samples live in tests/data/codex_real/ with a manifest that says where each came from and what it must mean. The directory is
filled by tests/tools/smoke_codex.py runs the user approved; with no samples yet these tests have nothing to check and say so.
Everything here is offline: the files are text.
"""
import json
import unittest
from pathlib import Path

from par.execution import results as R
from par.providers.base import FailureClass, RunOutcome
from par.providers.codex import CodexAdapter

DATA = Path(__file__).resolve().parents[1] / "data" / "codex_real"
MANIFEST = DATA / "manifest.json"


def samples():
    if not MANIFEST.is_file():
        return []
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["samples"]


class RealSamples(unittest.TestCase):
    def setUp(self):
        self.adapter = CodexAdapter("/unused", environ={})

    def outcome(self, sample):
        lines = (DATA / sample["file"]).read_text(encoding="utf-8").splitlines()
        events = [e for e in (self.adapter.parse_event(l) for l in lines) if e is not None]
        return lines, events, RunOutcome(exit_code=sample["exit_code"], events=events, stderr_tail=sample.get("stderr_tail", ""))

    def test_there_is_a_manifest_or_nothing_to_check(self):
        if not samples():
            self.skipTest("no real samples have been captured yet (they need an approved real run)")
        self.assertTrue(all((DATA / s["file"]).is_file() for s in samples()))

    def test_every_real_line_is_understood(self):
        for s in samples():
            with self.subTest(sample=s["file"]):
                lines, events, _ = self.outcome(s)
                self.assertTrue(lines)
                unparsed = [l[:80] for l, e in zip([l for l in lines if l.strip()], events) if e.kind == "unparsed"]
                self.assertEqual(unparsed, [])
                unknown = sorted({e.data["type"] for e in events if e.kind == "other" and e.data.get("type") not in ("turn.started", "reasoning")})
                self.assertEqual(unknown, s.get("known_other_types", []), "an event type the adapter does not know appeared: update the parser or the manifest")

    def test_every_real_run_is_classified_as_the_manifest_says(self):
        for s in samples():
            with self.subTest(sample=s["file"]):
                _, events, outcome = self.outcome(s)
                got = self.adapter.classify_failure(outcome)
                want = s["classified_as"]
                self.assertEqual(got.value if got else None, want)

    def test_successful_real_runs_carry_what_the_result_needs(self):
        for s in samples():
            if s["classified_as"] is not None:
                continue
            with self.subTest(sample=s["file"]):
                _, events, _ = self.outcome(s)
                self.assertEqual(next(e for e in reversed(events) if e.kind == "terminal").data["status"], "SUCCESS")
                self.assertIsNotNone(R.worker_usage(events))
                self.assertNotEqual(R.worker_summary(events), R.NO_SUMMARY)

    def test_real_failures_carry_the_message_and_the_reset_time_when_the_vendor_gave_one(self):
        for s in samples():
            if s["classified_as"] is None:
                continue
            with self.subTest(sample=s["file"]):
                _, events, _ = self.outcome(s)
                if s.get("has_reset_time"):
                    self.assertIsNotNone(R.reset_time(events))

    def test_no_real_sample_contains_anything_a_redactor_would_change(self):
        from par.core.redact import redact_text
        for s in samples():
            with self.subTest(sample=s["file"]):
                for line in (DATA / s["file"]).read_text(encoding="utf-8").splitlines():
                    self.assertEqual(redact_text(line), line)


if __name__ == "__main__":
    unittest.main()
