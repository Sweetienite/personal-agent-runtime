import json

from par.core.contract import parse_contract
from par.mcp.tools import Tools
from tests.kernel_fixture import KernelWorld


def text_of(result):
    return result["content"][0]["text"]


def payload_of(result):
    if result["isError"]:
        raise AssertionError("tool failed: " + text_of(result))
    return json.loads(text_of(result))


class PrepareTask(KernelWorld):
    default_files = None

    def setUp(self):
        super().setUp()
        self.tools = Tools(self.kernel)

    def ask(self, name, **arguments):
        return self.tools.call(name, arguments)

    def draft(self, **over):
        doc = {"project_id": "fixture", "title": "Fix add", "description": "Make add() return the sum.",
               "risk": "R2", "allowed_paths": ["app/"],
               "acceptance": {"criteria": ["the unit test passes"],
                              "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q"]}]}}
        doc.update(over)
        return doc

    def test_prepare_returns_a_submittable_contract_without_touching_the_ledger(self):
        out = payload_of(self.ask("prepare_task", draft=self.draft()))
        self.assertEqual(out["status"], "READY")
        self.assertEqual(out["agent_next"], ["submit_task"])
        self.assertEqual(out["contract"]["project_id"], "fixture")
        parse_contract(out["contract"])
        self.assertEqual(self.kernel.list_tasks(), [])

    def test_prepare_returns_questions_instead_of_guessing_a_scope(self):
        out = payload_of(self.ask("prepare_task", draft=self.draft(allowed_paths=[])))
        self.assertEqual(out["status"], "NEEDS_DECISION")
        self.assertIsNone(out["contract"])
        self.assertTrue(any("allowed_paths" in q for q in out["questions"]))
        self.assertEqual(out["agent_next"], [])
        self.assertEqual(self.kernel.list_tasks(), [])

    def test_prepare_does_not_submit_or_approve_anything(self):
        prepared = payload_of(self.ask("prepare_task", draft=self.draft()))["contract"]
        self.assertEqual([e for e in self.kernel.events() if e["type"] != "COORDINATOR_SEEN"], [])
        task_id = payload_of(self.ask("submit_task", contract=prepared))["task_id"]
        self.assertEqual(self.kernel.describe(task_id)["status"], "PERMISSION_WAIT")
        refused = payload_of(self.ask("run_task", task_id=task_id))
        self.assertFalse(refused["started"])                # no "prepare and run" shortcut around the approval
        self.assertIn("approval", refused["reason"])

    def test_an_oversized_or_malformed_draft_is_refused_before_the_kernel(self):
        out = self.ask("prepare_task", draft=self.draft(acceptance={"criteria": ["x" * 70_000]}))
        self.assertTrue(out["isError"])
        self.assertIn("larger than 64 KB", text_of(out))
        out = self.ask("prepare_task", draft=self.draft(allowed_paths="app/"))
        self.assertFalse(out["isError"])
        payload = payload_of(out)
        self.assertEqual(payload["status"], "NEEDS_DECISION")
        self.assertTrue(any("allowed_paths" in q for q in payload["questions"]))

    def test_status_carries_a_derived_state_and_the_approval_kind(self):
        prepared = payload_of(self.ask("prepare_task", draft=self.draft()))["contract"]
        task_id = payload_of(self.ask("submit_task", contract=prepared))["task_id"]
        out = payload_of(self.ask("get_task_status", task_id=task_id))
        self.assertEqual((out["derived_state"], out["status"]), ("NEEDS_SCOPE", "PERMISSION_WAIT"))
        self.assertEqual(out["pending_approvals"][0]["kind"], "WRITE_PERMISSION")


if __name__ == "__main__":
    import unittest

    unittest.main()
