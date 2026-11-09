"""Approval modes and coordinator grants: scope, expiry, revocation, and no self-granting."""
import unittest

from par.core.clock import FakeClock
from par.core.kernel import Refused
from par.mcp.tools import Tools, UnknownTool
from tests.kernel_fixture import KernelWorld
from tests.world import sample_config, sample_contract


class Grants(KernelWorld):
    default_files = None

    def kernel_with(self, mode, *, clock=None):
        return self.new_kernel(config=sample_config(approval_mode=mode), **({"clock": clock} if clock else {}))

    def pending_write(self, kernel):
        res = kernel.submit(sample_contract(), submitted_by="user")
        pending = kernel.describe(res.task_id)["pending_approvals"]
        self.assertEqual([a["kind"] for a in pending], ["WRITE_PERMISSION"])
        return res.task_id, pending[0]

    def test_the_default_mode_keeps_coordinator_autonomy_but_records_its_scope(self):
        task, approval = self.pending_write(self.kernel)
        self.assertEqual(self.kernel.approval_mode(), "coordinator_autonomous")
        self.assertEqual(self.kernel.decision_view("claude_desktop", self.kernel.describe(task))["allowed_actions"],
                         ["approve", "reject"])
        self.kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="checked the paths")
        decided = [e for e in self.kernel.events(task) if e["type"] == "APPROVAL_DECIDED"]
        self.assertEqual(decided[-1]["data"]["decision_mode"], "coordinator_autonomous")
        self.assertEqual(decided[-1]["data"]["decision_coordinator"], "claude_desktop")

    def test_human_only_refuses_every_coordinator_decision_but_not_the_person(self):
        kernel = self.kernel_with("human_only")
        task, approval = self.pending_write(kernel)
        self.assertEqual(kernel.decision_view("claude_desktop", kernel.describe(task))["allowed_actions"], [])
        with self.assertRaises(Refused) as ctx:
            kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="please")
        self.assertEqual(ctx.exception.code, "approval_mode")
        kernel.approve(task, approval["id"], note="I read the diff myself")
        self.assertEqual(kernel.describe(task)["status"], "QUEUED")

    def test_scoped_mode_refuses_without_a_matching_grant(self):
        kernel = self.kernel_with("coordinator_scoped")
        task, approval = self.pending_write(kernel)
        with self.assertRaises(Refused) as ctx:
            kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="please")
        self.assertEqual(ctx.exception.code, "no_grant")
        self.assertIn("par coordinator grant", str(ctx.exception))

    def test_a_scoped_grant_must_match_the_coordinator_action_project_risk_and_approval_kind(self):
        kernel = self.kernel_with("coordinator_scoped")
        task, approval = self.pending_write(kernel)
        grant = kernel.grant_create("claude_desktop", actions=["approve"], project_id="fixture",
                                    approval_kinds=["WRITE_PERMISSION"], risk_tiers=["R2"], expires_at="2099-01-01T00:00:00Z")
        self.assertTrue(grant["active"])
        for wrong in ("codex",):
            with self.assertRaises(Refused) as ctx:
                kernel.approve(task, approval["id"], by="coordinator", actor=wrong, note="please")
            self.assertEqual(ctx.exception.code, "no_grant")
        kernel.grant_revoke(grant["grant_id"])
        wrong_risk = kernel.grant_create("claude_desktop", actions=["approve"], project_id="fixture",
                                         approval_kinds=["WRITE_PERMISSION"], risk_tiers=["R1"], expires_at="2099-01-01T00:00:00Z")
        with self.assertRaises(Refused) as ctx:
            kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="wrong risk")
        self.assertEqual(ctx.exception.code, "no_grant")
        kernel.grant_revoke(wrong_risk["grant_id"])
        wrong_kind = kernel.grant_create("claude_desktop", actions=["approve"], project_id="fixture",
                                         approval_kinds=["BASE_DRIFT"], risk_tiers=["R2"], expires_at="2099-01-01T00:00:00Z")
        with self.assertRaises(Refused) as ctx:
            kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="please")
        self.assertEqual(ctx.exception.code, "no_grant")
        right = kernel.grant_create("claude_desktop", actions=["approve"], project_id="fixture",
                                    approval_kinds=["WRITE_PERMISSION"], risk_tiers=["R2"], expires_at="2099-01-01T00:00:00Z")
        self.assertNotEqual(wrong_kind["grant_id"], right["grant_id"])
        kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="grant covers this")
        decided = [e for e in kernel.events(task) if e["type"] == "APPROVAL_DECIDED"]
        self.assertEqual(decided[-1]["data"]["decision_mode"], "coordinator_scoped")
        self.assertEqual(decided[-1]["data"]["decision_grant"], right["grant_id"])

    def test_a_grant_expires_and_revocation_is_immediate_and_permanent(self):
        clock = FakeClock()
        kernel = self.kernel_with("coordinator_scoped", clock=clock)
        task, approval = self.pending_write(kernel)
        grant = kernel.grant_create("claude_desktop", actions=["approve"], project_id="fixture",
                                    approval_kinds=["WRITE_PERMISSION"], risk_tiers=["R2"], expires_at="2026-10-14T04:00:00Z")
        self.assertTrue(kernel.grant_list()[0]["active"])
        clock.advance(7200)
        self.assertEqual(kernel.grant_list(), [])
        self.assertTrue(kernel.grant_list(include_inactive=True)[0]["expired"])
        with self.assertRaises(Refused) as ctx:
            kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="expired")
        self.assertEqual(ctx.exception.code, "no_grant")
        kernel.grant_revoke(grant["grant_id"])
        self.assertIsNotNone(kernel.grant_list(include_inactive=True)[0]["revoked_at"])
        with self.assertRaises(Refused) as ctx:
            kernel.grant_revoke(grant["grant_id"])
        self.assertEqual(ctx.exception.code, "already_revoked")

    def test_only_a_person_can_create_or_revoke_a_grant_and_the_bridge_has_no_such_tool(self):
        kernel = self.kernel_with("coordinator_scoped")
        with self.assertRaises(Refused) as ctx:
            kernel.grant_create("claude_desktop", actions=["approve"], expires_at="2099-01-01T00:00:00Z", by="coordinator")
        self.assertEqual(ctx.exception.code, "grant_by_human")
        grant = kernel.grant_create("claude_desktop", actions=["approve"], expires_at="2099-01-01T00:00:00Z")
        with self.assertRaises(Refused):
            kernel.grant_revoke(grant["grant_id"], by="coordinator")
        for name in ("grant", "grant_create", "grant_revoke", "coordinator_grant"):
            with self.assertRaises(UnknownTool):
                Tools(kernel).call(name, {})

    def test_a_grant_is_visible_in_the_coordinator_status(self):
        kernel = self.kernel_with("coordinator_scoped")
        kernel.grant_create("codex", actions=["accept"], project_id="fixture", risk_tiers=["R1"],
                            expires_at="2099-01-01T00:00:00Z")
        status = kernel.coordinator_status()
        self.assertEqual(status["approval_mode"], "coordinator_scoped")
        self.assertEqual([(g["coordinator"], g["actions"]) for g in status["grants"]], [("codex", ["accept"])])

    def test_a_naive_expiry_is_normalised_to_utc(self):
        kernel = self.kernel_with("coordinator_scoped")
        grant = kernel.grant_create("claude_desktop", actions=["approve"], expires_at="2099-01-01T00:00:00")
        self.assertTrue(grant["expires_at"].endswith("Z"))
        self.assertTrue(grant["active"])

    def test_a_malformed_grant_is_refused_before_it_reaches_the_ledger(self):
        kernel = self.kernel_with("coordinator_scoped")
        cases = (
            (dict(actions=["retry"], expires_at="2099-01-01T00:00:00Z"), "bad_grant_actions"),
            (dict(actions=["approve"], approval_kinds=["ACCEPT_RESULT"], expires_at="2099-01-01T00:00:00Z"), "bad_grant_kinds"),
            (dict(actions=["approve"], project_id="no-such-project", expires_at="2099-01-01T00:00:00Z"), "unknown_project"),
            (dict(actions=["approve"], risk_tiers=["R3"], expires_at="2099-01-01T00:00:00Z"), "bad_grant_risks"),
            (dict(actions=["approve"], expires_at="2020-01-01T00:00:00Z"), "bad_grant_expiry"),
        )
        for kwargs, code in cases:
            with self.subTest(code=code), self.assertRaises(Refused) as ctx:
                kernel.grant_create("claude_desktop", **kwargs)
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(kernel.grant_list(include_inactive=True), [])


if __name__ == "__main__":
    unittest.main()
