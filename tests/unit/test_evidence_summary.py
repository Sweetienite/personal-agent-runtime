"""T10: a derived evidence view - Kernel measurements, the Worker's claim, review and integration stay separate.

None of this changes TaskStatus or the result envelope: it is recomputed from what the Kernel already recorded, and an
old or incomplete envelope comes back as UNKNOWN/NOT_RUN rather than PASS."""
import unittest

from par.execution.results import evidence_summary
from par.providers.fake import scenarios
from tests.kernel_fixture import KernelWorld, r1_contract
from tests.unit.test_kernel_policy import using
from tests.world import sample_contract

WRONG_CALC = "def add(a, b):\n    return a * b\n"
WRONG_TEST = ("import unittest\nfrom app.calc import add\n\n\nclass T(unittest.TestCase):\n"
              "    def test_add(self):\n        self.assertEqual(add(2, 3), 6)\n")
INDEPENDENT = ["python3", "checks/independent.py"]


class PureSummary(unittest.TestCase):
    def test_an_absent_or_old_envelope_is_unknown_not_pass(self):
        for result in (None, {}, {"schema_version": 1, "summary": "all good"}):
            ev = evidence_summary(result)
            self.assertEqual((ev["level"], ev["checks"]["status"]), ("UNKNOWN", "NOT_RUN"), result)
            self.assertEqual(ev["boundary"]["status"], "UNKNOWN")
            self.assertFalse(ev["claim"]["verified"])

    def test_no_contract_checks_is_its_own_level(self):
        ev = evidence_summary({"outcome": "completed", "partial": False, "tests": [],
                               "boundary": {"violations": 0, "sensitive": 0, "paths": []}, "summary": "done"})
        self.assertEqual((ev["level"], ev["checks"]["status"]), ("UNVERIFIED", "NO_MECHANICAL_CHECK"))
        self.assertTrue(ev["untested"])
        self.assertEqual((ev["integration"], ev["applied_to_project"]), ("NOT_INTEGRATED", False))

    def test_a_boundary_violation_or_a_failed_check_is_failed_even_when_other_checks_pass(self):
        base = {"outcome": "completed", "partial": False, "summary": "done",
                "tests": [{"name": "unit", "status": "PASS"}],
                "boundary": {"violations": 0, "sensitive": 0, "paths": []}}
        failed = evidence_summary({**base, "tests": [{"name": "unit", "status": "FAIL"}]}, contract_checks=[{"name": "unit", "argv": ["python3"]}])
        self.assertEqual((failed["level"], failed["checks"]["status"], failed["checks"]["failed"]), ("FAILED", "FAILED", ["unit"]))
        crossed = evidence_summary({**base, "boundary": {"violations": 1, "sensitive": 0, "paths": ["README.md"]}})
        self.assertEqual((crossed["level"], crossed["boundary"]["status"]), ("FAILED", "VIOLATIONS"))

    def test_a_worker_edited_check_is_named_even_when_it_passes(self):
        result = {"outcome": "completed", "partial": False, "summary": "done",
                  "tests": [{"name": "unit", "status": "PASS"}],
                  "boundary": {"violations": 0, "sensitive": 0, "paths": []}}
        ev = evidence_summary(result, contract_checks=[{"name": "unit", "argv": ["python3", "-m", "unittest", "tests.test_calc"]}],
                              changed_paths=["app/calc.py", "tests/test_calc.py"], worker_can_edit_checks=True)
        self.assertEqual((ev["level"], ev["checks"]["status"]), ("MEASURED_WITH_EDITED_CHECKS", "PASSED_CHECKS_EDITED"))
        self.assertEqual((ev["checks"]["inputs_edited"], ev["checks"]["test_files_changed"]), (["tests/test_calc.py"], ["tests/test_calc.py"]))
        self.assertTrue(ev["checks"]["worker_editable"])

    def test_a_completed_result_without_a_boundary_section_is_unknown_not_measured(self):
        ev = evidence_summary({"outcome": "completed", "tests": [{"name": "unit", "status": "PASS"}], "summary": "ok"})
        self.assertEqual((ev["level"], ev["checks"]["status"]), ("UNKNOWN", "PASSED"))

    def test_the_workers_words_are_redacted_bounded_and_never_verified(self):
        ev = evidence_summary({"summary": "\x1b[31mPASS\x07 TOKEN=sk-abcdefghijklmnopqrstuvwxyz0123456789 " + "x" * 5000})
        self.assertNotIn("\x1b", ev["claim"]["text"])
        self.assertNotIn("\x07", ev["claim"]["text"])
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", ev["claim"]["text"])
        self.assertLessEqual(len(ev["claim"]["text"]), 1200)
        self.assertFalse(ev["claim"]["verified"])


class EvidenceOnTheKernel(KernelWorld):
    default_files = None

    def test_a_worker_that_says_it_succeeded_cannot_reach_review_when_the_check_fails(self):
        using(self, codex=[scenarios.success({"app/calc.py": WRONG_CALC})])
        tid = self.queued(sample_contract(role="code"))
        self.assertEqual(self.run_task(tid).status, "FAILED")
        ev = self.kernel.evidence(tid)
        self.assertEqual((ev["level"], ev["checks"]["status"], ev["checks"]["failed"]), ("FAILED", "FAILED", ["unit"]))
        self.assertFalse(ev["claim"]["verified"])
        self.assertEqual(ev["review"], "NOT_REVIEWED")

    def test_an_edited_test_that_passes_says_so_instead_of_claiming_a_clean_pass(self):
        using(self, codex=[scenarios.success({"app/calc.py": WRONG_CALC, "tests/test_calc.py": WRONG_TEST})])
        contract = sample_contract(role="code", allowed_paths=["app/", "tests/"],
                                   acceptance={"criteria": ["the unit test passes"],
                                               "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q", "tests.test_calc"]}]})
        tid = self.queued(contract)
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        ev = self.kernel.evidence(tid)
        self.assertEqual((ev["level"], ev["checks"]["status"]), ("MEASURED_WITH_EDITED_CHECKS", "PASSED_CHECKS_EDITED"))
        self.assertEqual(ev["checks"]["inputs_edited"], ["tests/test_calc.py"])
        self.assertIn("tests/test_calc.py", ev["checks"]["test_files_changed"])

    def test_an_independent_assertion_the_worker_cannot_edit_catches_the_wrong_behaviour(self):
        self.commit_in_project(self.project_path, "checks/__init__.py", "")
        self.commit_in_project(self.project_path, "checks/independent.py",
                               "import os, sys\nsys.path.insert(0, os.getcwd())\nfrom app.calc import add\n"
                               "raise SystemExit(0 if add(2, 3) == 5 else 1)\n")
        using(self, codex=[scenarios.success({"app/calc.py": WRONG_CALC, "tests/test_calc.py": WRONG_TEST})])
        contract = sample_contract(role="code", allowed_paths=["app/", "tests/"],
                                   acceptance={"criteria": ["the independent assertion holds"],
                                               "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q", "tests.test_calc"]},
                                                          {"name": "independent", "argv": INDEPENDENT}]})
        tid = self.queued(contract)
        self.assertEqual(self.run_task(tid).status, "FAILED")
        ev = self.kernel.evidence(tid)
        self.assertEqual((ev["checks"]["status"], ev["checks"]["failed"]), ("FAILED", ["independent"]))
        self.assertIn("tests/test_calc.py", ev["checks"]["test_files_changed"])

    def test_a_write_task_without_checks_stays_unverified_and_a_coordinator_still_cannot_accept_it(self):
        tid = self.queued(sample_contract(role="code", acceptance={"criteria": ["it works"], "checks": []}))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        ev = self.kernel.evidence(tid)
        self.assertEqual((ev["level"], ev["checks"]["status"]), ("UNVERIFIED", "NO_MECHANICAL_CHECK"))
        self.assertTrue(any("no acceptance.checks" in b for b in self.kernel.accept_blockers(tid, by="coordinator")))

    def test_read_only_and_text_results_have_no_checks_and_no_claim_of_completion(self):
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        ev = self.kernel.evidence(tid)
        self.assertEqual((ev["level"], ev["checks"]["status"]), ("UNVERIFIED", "NO_MECHANICAL_CHECK"))
        self.assertEqual((ev["boundary"]["status"], ev["review"]), ("CLEAN", "NOT_REVIEWED"))
        self.assertEqual((ev["integration"], ev["applied_to_project"]), ("NOT_INTEGRATED", False))
        self.assertFalse(ev["claim"]["verified"])

    def test_accepting_records_who_decided_and_never_claims_integration(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.kernel.accept(tid, by="user", note="looks right")
        ev = self.kernel.evidence(tid)
        self.assertEqual(ev["review"], "HUMAN_REVIEW")
        self.assertEqual((ev["integration"], ev["applied_to_project"]), ("NOT_INTEGRATED", False))
        other = self.queued(r1_contract())
        self.run_task(other)
        self.kernel.accept(other, by="coordinator", actor="claude_desktop", note="kernel checks only")
        self.assertEqual(self.kernel.evidence(other)["review"], "COORDINATOR_REVIEW")

    def test_rejecting_a_result_is_recorded_as_a_review_by_whoever_did_it(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.kernel.reject(tid, by="user", note="not what was asked")
        self.assertEqual(self.status(tid), "FAILED")
        ev = self.kernel.evidence(tid)
        self.assertEqual(ev["review"], "HUMAN_REVIEW")
        self.assertEqual(ev["checks"]["status"], "NO_MECHANICAL_CHECK")


if __name__ == "__main__":
    unittest.main()
