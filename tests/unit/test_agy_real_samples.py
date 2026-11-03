"""The parser and classifier against REAL `agy` stream-json output captured from the installed CLI (redacted).

Samples live in tests/data/agy_real/ with a manifest that says where each came from, which permission mode the Runtime asked for and
what it must mean. The directory is filled by tests/tools/smoke_agy.py runs the user approved (tests/tools/capture_agy_sample.py turns
an attempt's stdout.log into a sample); with no samples yet these tests have nothing to check and say so. Everything here is offline.
"""
import json
import unittest
from pathlib import Path

from par.execution import results as R
from par.providers.antigravity import AntigravityAdapter
from par.providers.base import RunOutcome

DATA = Path(__file__).resolve().parents[1] / "data" / "agy_real"
MANIFEST = DATA / "manifest.json"


def samples():
    if not MANIFEST.is_file():
        return []
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["samples"]


class RealSamples(unittest.TestCase):
    def adapter(self, sample):
        ad = AntigravityAdapter("/unused", environ={})
        ad._wanted = sample["mode"]
        return ad

    def outcome(self, sample):
        ad = self.adapter(sample)
        lines = (DATA / sample["file"]).read_text(encoding="utf-8").splitlines()
        events = [e for e in (ad.parse_event(l) for l in lines) if e is not None]
        return ad, lines, events, RunOutcome(exit_code=sample["exit_code"], events=events, stderr_tail=sample.get("stderr_tail", ""))

    def test_there_is_a_manifest_or_nothing_to_check(self):
        if not samples():
            self.skipTest("no real samples have been captured yet (they need an approved real run)")
        self.assertTrue(all((DATA / s["file"]).is_file() for s in samples()))

    def test_every_real_line_is_understood(self):
        for s in samples():
            with self.subTest(sample=s["file"]):
                _, lines, events, _ = self.outcome(s)
                self.assertTrue(lines)
                unparsed = [l[:80] for l, e in zip([l for l in lines if l.strip()], events) if e.kind == "unparsed"]
                self.assertEqual(unparsed, [])
                other = sorted({str(e.data.get("type")) for e in events if e.kind == "other"})
                self.assertEqual(other, s.get("known_other_types", []), "a step type the adapter does not know appeared: update the parser or the manifest")

    def test_every_real_run_is_classified_as_the_manifest_says(self):
        for s in samples():
            with self.subTest(sample=s["file"]):
                ad, _, _, outcome = self.outcome(s)
                got = ad.classify_failure(outcome)
                self.assertEqual(got.value if got else None, s["classified_as"])

    def test_the_real_init_event_reports_the_mode_the_runtime_asked_for(self):
        for s in samples():
            with self.subTest(sample=s["file"]):
                _, _, events, _ = self.outcome(s)
                modes = [e.data["permission_mode"] for e in events if "permission_mode" in e.data]
                self.assertEqual(modes, [s["mode"]])
                self.assertEqual([e.kind for e in events if "permission_mode" in e.data], ["session"])

    def test_successful_real_runs_carry_what_the_result_needs(self):
        for s in samples():
            if s["classified_as"] is not None:
                continue
            with self.subTest(sample=s["file"]):
                _, _, events, _ = self.outcome(s)
                self.assertEqual(next(e for e in reversed(events) if e.kind == "terminal").data["status"], "SUCCESS")
                self.assertIsNotNone(R.worker_usage(events))
                if s.get("empty_answer"):                                      # a vendor success that said nothing (the soft-denied smoke)
                    self.assertEqual(R.worker_summary(events), R.NO_SUMMARY)
                else:
                    self.assertNotEqual(R.worker_summary(events), R.NO_SUMMARY)

    def test_a_real_soft_denial_is_a_warning_on_the_result_and_never_a_failure(self):
        from par.providers.antigravity import soft_denial_message
        found = False
        for s in samples():
            if not s.get("denied_actions"):
                continue
            found = True
            with self.subTest(sample=s["file"]):
                ad, _, events, outcome = self.outcome(s)
                self.assertIsNone(ad.classify_failure(outcome))                                    # the Kernel judges it; the adapter does not fail it
                notes = R.worker_warnings(events)
                for action in s["denied_actions"]:
                    self.assertIn(soft_denial_message(action["action"]), notes)
                self.assertEqual(sum(1 for n in notes if "soft-denied" in n), len(s["denied_actions"]))   # stderr, step and result: one note each
                self.assertTrue(any("a tool call was refused" in n for n in notes))
        if not found and samples():
            self.skipTest("no real sample with a soft denial")

    def test_no_real_sample_contains_anything_a_redactor_would_change(self):
        from par.core.redact import redact_text
        for s in samples():
            with self.subTest(sample=s["file"]):
                for line in (DATA / s["file"]).read_text(encoding="utf-8").splitlines():
                    self.assertEqual(redact_text(line), line)


if __name__ == "__main__":
    unittest.main()
