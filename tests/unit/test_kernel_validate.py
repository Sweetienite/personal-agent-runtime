import unittest

from par.config import ProjectEntry
from par.core.kernel import Refused, ValidateResult
from tests.kernel_fixture import KernelWorld, r1_contract
from tests.world import sample_contract


class Validating(KernelWorld):
    def validate(self, contract=None, *, by="user", **over):
        doc = dict(contract if contract is not None else sample_contract())
        doc.update(over)
        return self.kernel.validate(doc, submitted_by=by)

    def test_a_valid_write_contract_reports_write_permission_only(self):
        res = self.validate()
        self.assertIsInstance(res, ValidateResult)
        self.assertEqual(res.would_approvals, ("WRITE_PERMISSION",))

    def test_a_coordinator_submitted_pinned_worker_also_reports_worker_override(self):
        res = self.validate(by="coordinator", worker="agy_flash")
        self.assertEqual(res.would_approvals, ("WRITE_PERMISSION", "WORKER_OVERRIDE"))

    def test_a_user_submitted_pinned_worker_does_not_need_worker_override(self):
        res = self.validate(by="user", worker="agy_flash")
        self.assertEqual(res.would_approvals, ("WRITE_PERMISSION",))

    def test_a_read_only_contract_needs_no_approval_at_all(self):
        res = self.validate(r1_contract())
        self.assertEqual(res.would_approvals, ())

    def test_warnings_are_reported_the_same_as_submit_would(self):
        res = self.validate()
        self.assertEqual(res.warnings, self.kernel.submit(dict(sample_contract(), idempotency_key="k-warn")).warnings)

    def test_nothing_is_written_on_success(self):
        before = self.project_digest()
        self.assertEqual(self.kernel.list_tasks(), [])
        self.validate()
        self.validate(r1_contract())
        self.assertEqual(self.kernel.list_tasks(), [])
        self.assertEqual(self.project_digest(), before)
        self.assertEqual(self.submit().task_id, "fixture-0001")          # the first real submit still gets id 1

    def test_an_invalid_contract_is_refused_exactly_like_submit(self):
        with self.assertRaises(Refused) as ctx:
            self.validate(r1_contract(allowed_paths=["app/"]))
        self.assertTrue(any("R1 task cannot change files" in p for p in ctx.exception.problems))
        self.assertEqual(self.kernel.list_tasks(), [])

    def test_an_unknown_project_is_refused_and_writes_nothing(self):
        with self.assertRaises(Refused) as ctx:
            self.validate(project_id="nope")
        self.assertTrue(any("not registered" in p for p in ctx.exception.problems))
        self.assertEqual(self.kernel.list_tasks(), [])

    def test_a_dirty_project_refuses_validation_and_writes_nothing(self):
        (self.project_path / "app" / "calc.py").write_text("# local edit\n")
        before = self.project_digest()
        with self.assertRaises(Refused) as ctx:
            self.validate()
        self.assertTrue(any("DIRTY_BASE" in p for p in ctx.exception.problems))
        self.assertEqual(self.kernel.list_tasks(), [])
        self.assertEqual(self.project_digest(), before)

    def test_a_write_disabled_project_refuses_validation(self):
        self.projects["fixture"] = ProjectEntry("fixture", self.project_path, "git", False)
        self.kernel = self.new_kernel()
        with self.assertRaises(Refused) as ctx:
            self.validate()
        self.assertTrue(any("WRITE_DISABLED" in p for p in ctx.exception.problems))
        self.assertEqual(self.validate(r1_contract()).would_approvals, ())          # reading is still fine

    def test_validate_does_not_deduplicate_or_write_an_event(self):
        """Calling validate() twice with the same idempotency key is not an error and not
        reported as a duplicate - deduplication only happens on a real submit()."""
        res1 = self.validate(idempotency_key="same-key")
        res2 = self.validate(idempotency_key="same-key")
        self.assertEqual((res1, res2), (ValidateResult(res1.warnings, res1.would_approvals),) * 2)
        self.assertEqual(self.kernel.list_tasks(), [])


if __name__ == "__main__":
    unittest.main()
