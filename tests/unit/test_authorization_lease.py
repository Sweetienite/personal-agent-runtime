"""The coordinator lease is enforced in the Kernel, not only by the bridge's tool list.

A person switching coordinators must end the old session's ability to decide anything, in every
approval mode. These are the SEC-01 / SEC-03 / SEC-04 negative cases, plus the action policy for
side-effecting bridge tools that are not one of the four decisions.
"""
import unittest

from par.config import ProjectEntry
from par.core.kernel import Refused
from par.mcp.tools import Tools
from tests.kernel_fixture import FIX, KernelWorld
from tests.world import sample_config, sample_contract


class LeaseWorld(KernelWorld):
    default_files = FIX

    def switch(self, to="codex"):
        info = self.kernel.takeover_export()
        self.kernel.takeover_confirm(info["takeover_id"], coordinator=to)
        self.assertEqual(self.kernel.coordinator_lease()["coordinator"], to)
        return info

    def tool(self, name, coordinator="claude_desktop", **args):
        return Tools(self.kernel, coordinator).call(name, args)

    def pending_write(self):
        res = self.submit(sample_contract(role="code"), key="pending")
        pending = self.kernel.describe(res.task_id)["pending_approvals"]
        self.assertEqual([a["kind"] for a in pending], ["WRITE_PERMISSION"])
        return res.task_id, pending[0]


class DecisionsAfterASwitch(LeaseWorld):
    def test_the_replaced_coordinator_cannot_approve_reject_accept_or_close(self):
        pending = self.submit(sample_contract(role="code"), key="pending").task_id
        review = self.queued(sample_contract(role="code"), key="review")
        self.run_task(review)
        accepted = self.queued(sample_contract(role="code"), key="accepted")
        self.run_task(accepted)
        self.kernel.accept(accepted)
        self.switch()
        before = len(self.kernel.events())

        for name, args in (("approve_task", {"task_id": pending, "reason": "old session"}),
                           ("reject_task", {"task_id": review, "reason": "old session"}),
                           ("accept_task", {"task_id": review, "reason": "old session"}),
                           ("close_task", {"task_id": accepted})):
            out = self.tool(name, **args)
            self.assertTrue(out["isError"], name)
            self.assertIn("not_the_coordinator", out["content"][0]["text"], name)

        self.assertEqual(self.status(pending), "PERMISSION_WAIT")
        self.assertEqual(self.status(review), "REVIEW_REQUIRED")
        self.assertEqual(self.status(accepted), "ACCEPTED")
        added = self.kernel.events()[before:]
        self.assertFalse([e for e in added if e["type"] in ("APPROVAL_DECIDED", "ACCEPT_AUTHORIZED", "TASK_CLOSED",
                                                            "RESULT_REJECTED")])
        self.assertEqual([a["status"] for a in self.kernel.store.approvals(pending)], ["PENDING"])

    def test_autonomous_mode_still_requires_the_current_lease(self):
        task, approval = self.pending_write()
        self.switch()
        with self.assertRaises(Refused) as ctx:
            self.kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="old session")
        self.assertEqual(ctx.exception.code, "not_the_coordinator")
        self.assertEqual(self.status(task), "PERMISSION_WAIT")

    def test_a_known_coordinator_that_never_held_the_lease_is_refused(self):
        task, approval = self.pending_write()
        with self.assertRaises(Refused) as ctx:
            self.kernel.approve(task, approval["id"], by="coordinator", actor="codex", note="not my turn")
        self.assertEqual(ctx.exception.code, "not_the_coordinator")
        self.assertEqual(self.status(task), "PERMISSION_WAIT")

    def test_an_unknown_coordinator_identity_is_refused(self):
        task, approval = self.pending_write()
        for actor in (None, "", "some_other_ai"):
            with self.subTest(actor=actor), self.assertRaises(Refused) as ctx:
                self.kernel.approve(task, approval["id"], by="coordinator", actor=actor, note="who am I")
            self.assertEqual(ctx.exception.code, "coordinator_identity")
        self.assertEqual(self.status(task), "PERMISSION_WAIT")

    def test_the_decision_view_stops_advertising_decisions_to_the_old_session(self):
        task, _ = self.pending_write()
        self.assertEqual(self.kernel.decision_view("claude_desktop", self.kernel.describe(task))["allowed_actions"],
                         ["approve", "reject"])
        self.switch()
        view = self.kernel.decision_view("claude_desktop", self.kernel.describe(task))
        self.assertEqual(view["allowed_actions"], [])
        self.assertIn("codex coordinates now", view["reason"])
        self.assertEqual(self.kernel.decision_view("codex", self.kernel.describe(task))["allowed_actions"],
                         ["approve", "reject"])

    def test_the_new_coordinator_can_decide_once_the_person_switched(self):
        task, approval = self.pending_write()
        self.switch()
        self.kernel.approve(task, approval["id"], by="coordinator", actor="codex", note="I coordinate now")
        decided = [e for e in self.kernel.events(task) if e["type"] == "APPROVAL_DECIDED"]
        self.assertEqual(decided[-1]["data"]["decision_coordinator"], "codex")
        self.assertEqual(self.status(task), "QUEUED")

    def test_the_person_is_never_locked_out(self):
        task, approval = self.pending_write()
        self.switch()
        self.kernel.approve(task, approval["id"], note="I decided this myself")
        self.assertEqual(self.status(task), "QUEUED")


class RecheckAndConcurrency(LeaseWorld):
    def test_authorization_is_checked_again_inside_the_write_transaction(self):
        task, approval = self.pending_write()
        original = self.kernel._decision_authorization
        calls = {"n": 0}

        def revoke_between_checks(action, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise Refused("not_the_coordinator", "the person switched while this decision was being prepared")
            return original(action, **kw)

        self.kernel._decision_authorization = revoke_between_checks
        try:
            with self.assertRaises(Refused) as ctx:
                self.kernel.approve(task, approval["id"], by="coordinator", actor="claude_desktop", note="race")
        finally:
            self.kernel._decision_authorization = original
        self.assertEqual(ctx.exception.code, "not_the_coordinator")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self.status(task), "PERMISSION_WAIT")
        self.assertEqual([a["status"] for a in self.kernel.store.approvals(task)], ["PENDING"])

    def test_a_grant_for_another_project_or_risk_does_not_cover_this_task(self):
        other = self.make_project("other")
        self.projects["other"] = ProjectEntry("other", other, "git", True)
        kernel = self.new_kernel(config=sample_config(approval_mode="coordinator_scoped"))
        res = kernel.submit(sample_contract(project_id="other", role="code", idempotency_key="other-1"), submitted_by="user")
        approval = kernel.describe(res.task_id)["pending_approvals"][0]
        grant = kernel.grant_create("claude_desktop", actions=["approve"], project_id="fixture",
                                    approval_kinds=["WRITE_PERMISSION"], risk_tiers=["R2"],
                                    expires_at="2099-01-01T00:00:00Z")
        with self.assertRaises(Refused) as ctx:
            kernel.approve(res.task_id, approval["id"], by="coordinator", actor="claude_desktop", note="wrong project")
        self.assertEqual(ctx.exception.code, "no_grant")
        kernel.grant_revoke(grant["grant_id"])
        wrong_risk = kernel.grant_create("claude_desktop", actions=["approve"], project_id="other",
                                         approval_kinds=["WRITE_PERMISSION"], risk_tiers=["R1"],
                                         expires_at="2099-01-01T00:00:00Z")
        with self.assertRaises(Refused) as ctx:
            kernel.approve(res.task_id, approval["id"], by="coordinator", actor="claude_desktop", note="wrong risk")
        self.assertEqual(ctx.exception.code, "no_grant")
        kernel.grant_revoke(wrong_risk["grant_id"])
        right = kernel.grant_create("claude_desktop", actions=["approve"], project_id="other",
                                    approval_kinds=["WRITE_PERMISSION"], risk_tiers=["R2"],
                                    expires_at="2099-01-01T00:00:00Z")
        kernel.approve(res.task_id, approval["id"], by="coordinator", actor="claude_desktop", note="covers it")
        self.assertEqual(right["active"], True)
        self.assertEqual(kernel.describe(res.task_id)["status"], "QUEUED")


class SideEffectActions(LeaseWorld):
    def test_the_replaced_bridge_cannot_resume_retry_terminate_create_or_rebase(self):
        task = self.queued(sample_contract(role="code"), key="resume")
        self.kernel.pause(task)
        self.switch()

        self.assertTrue(self.tool("resume_task", task_id=task)["isError"])
        self.assertTrue(self.tool("retry_task", task_id=task)["isError"])
        self.assertTrue(self.tool("recover_task", task_id=task, terminate=True)["isError"])
        for call in (lambda: self.kernel.candidate_create(["anything"], actor="claude_desktop"),
                     lambda: self.kernel.task_rebase("anything", "cand-fixture-deadbeef", actor="claude_desktop")):
            with self.assertRaises(Refused) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "not_the_coordinator")
        self.assertEqual(self.status(task), "PAUSED")

    def test_stopping_a_task_stays_open_to_the_replaced_bridge(self):
        task = self.queued(sample_contract(role="code"), key="cancel")
        self.switch()
        out = self.tool("cancel_task", task_id=task, reason="stop")
        self.assertFalse(out["isError"])
        self.assertEqual(self.status(task), "CANCELED")

    def test_a_candidate_records_which_coordinator_built_it(self):
        task = self.queued(sample_contract(role="code"), key="cand")
        self.run_task(task)
        self.kernel.accept(task)
        built = self.kernel.candidate_create([task], actor="claude_desktop")
        event = [e for e in self.kernel.events() if e["type"] == f"CANDIDATE_{built['status']}"][-1]
        self.assertEqual(event["data"]["actor"], "claude_desktop")
        self.assertFalse(built["applied_to_real_project"])


if __name__ == "__main__":
    unittest.main()
