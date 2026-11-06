"""T12 (P3): coordinator/worker roles are independent of vendors; capabilities gate the write path.

R-01 old config + aliases, R-02 one tool in both roles, R-03 manual worker path, R-04 unproven
capability, R-05 unknown names are refused, R-06 fallback and quota policy stay unchanged.
"""
import tempfile
import unittest
from pathlib import Path

import par.coordinators as C
from par.config import build_config
from par.core.clock import FakeClock
from par.core.health import Health
from par.core.manual import APPS
from par.core.router import config_snapshot, route
from par.core.store import Store
from par.providers.base import Capabilities, Tri
from par.providers.fake import ScenarioSequence, ScriptedProvider, scenarios
from par.providers.registry import AdapterRegistry
from tests.kernel_fixture import FIX, KernelWorld, r1_contract
from tests.world import sample_config, sample_contract


class UnprovenWrite(ScriptedProvider):
    """A worker that can run headless but has never proved it can write safely."""

    def capabilities(self) -> Capabilities:
        return Capabilities(can_headless=Tri.YES, can_write=Tri.UNKNOWN)


class LegacyConfiguration(unittest.TestCase):
    def test_R01_the_old_profiles_and_providers_still_load_without_edits(self):
        # The exact shape config/profiles.example.toml used before T12, including the old alias.
        profiles = {
            "schema_version": 1, "active_profile": "default",
            "workers": {
                "w_code": {"provider": "codex", "model": "m-code", "capabilities": ["read", "edit", "review"]},
                "w_flash": {"provider": "antigravity", "model": "m-flash", "capabilities": ["read"]},
            },
            "profiles": {"default": {
                "coordinator": "claude_desktop", "fallback_coordinator": "codex_manual_takeover",
                "default_worker": "w_code", "max_parallel_workers": 1,
                "worker_failure_policy": "ask", "additional_paid_credits": False,
                "roles": {"code": {"primary": "w_code", "fallback": ["w_flash"]}},
            }},
        }
        providers = {"schema_version": 1, "providers": {"codex": {"enabled": True}, "antigravity": {"enabled": False}}}
        result = build_config(profiles, providers)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(C.normalize("codex_manual_takeover"), "codex")
        snap = config_snapshot(result.config)
        self.assertEqual(snap["workers"]["w_code"]["surface"], "native_cli")
        self.assertEqual(snap["providers"]["codex"]["surface"], "native_cli")
        self.assertEqual(snap["roles"]["code"], {"primary": "w_code", "fallback": ["w_flash"]})
        self.assertEqual(snap["digest"], config_snapshot(build_config(profiles, providers).config)["digest"])

    def test_R01_the_worker_surface_is_derived_from_the_provider_kind_not_a_vendor_name(self):
        profiles = {
            "schema_version": 1, "active_profile": "p",
            "workers": {"w": {"provider": "codex", "model": "m", "capabilities": ["read"]},
                        "m": {"provider": "manual_handoff", "model": "m", "capabilities": ["read"]}},
            "profiles": {"p": {"coordinator": "claude_desktop", "default_worker": "w", "max_parallel_workers": 1,
                               "worker_failure_policy": "ask", "additional_paid_credits": False,
                               "roles": {"code": {"primary": "w", "fallback": []}}}},
        }
        providers = {"schema_version": 1, "providers": {"codex": {"enabled": True},
                                                        "manual_handoff": {"kind": "manual_handoff", "enabled": False}}}
        result = build_config(profiles, providers)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((result.config.workers["w"].surface, result.config.workers["m"].surface),
                         ("native_cli", "manual"))


class Descriptors(unittest.TestCase):
    def test_R02_a_coordinator_descriptor_names_a_surface_and_only_bridge_capabilities(self):
        d = C.descriptor("codex")
        self.assertEqual((d["surface"], d["connection"]), ("mcp_host", "mcp"))
        self.assertIn("read", d["capabilities"])
        self.assertNotIn("write_files", d["capabilities"])         # the bridge has no file-write tool
        self.assertEqual(d["id"], C.normalize("codex_manual_takeover"))

    def test_R03_the_manual_surface_keeps_its_own_path(self):
        self.assertIn("workbuddy", APPS)
        self.assertEqual(C.descriptor("claude_desktop")["surface"], "mcp_host")  # coordinator unchanged


class RoutingFacts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name), FakeClock())
        self.health = Health(self.store)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.store.close)
        self.reg = AdapterRegistry()
        self.reg.register("codex", ScenarioSequence(scenarios.success()).factory("codex"))
        self.reg.register("antigravity", ScenarioSequence(scenarios.success()).factory("antigravity"))
        self.snap = config_snapshot(sample_config())

    def test_R05_an_unknown_role_is_refused_not_silently_mapped_to_the_default(self):
        r = route(self.snap, role="does_not_exist", worker=None, risk="R1", adapters=self.reg, health=self.health)
        self.assertEqual(r.chain, ())
        self.assertIn("not configured", r.problem)

    def test_R05_an_unknown_worker_override_does_not_fall_back_to_another_account(self):
        r = route(self.snap, role=None, worker="w_missing", risk="R2", adapters=self.reg, health=self.health)
        self.assertEqual([c.worker_id for c in r.chain], ["w_missing"])
        self.assertFalse(r.chosen)
        self.assertIn("not defined in the configuration", r.chain[0].reasons[0])

    def test_R06_the_fallback_chain_is_the_same_for_every_risk_tier(self):
        for risk in ("R0", "R1", "R2"):
            with self.subTest(risk=risk):
                r = route(self.snap, role="code", worker=None, risk=risk, adapters=self.reg, health=self.health)
                self.assertEqual([c.worker_id for c in r.chain], ["w_code", "w_flash"])
        self.assertFalse(sample_config().profiles["default"].additional_paid_credits)   # a quota error never buys credits


class CapabilityGate(KernelWorld):
    default_files = FIX

    def test_R04_an_unproven_write_capability_never_enters_the_write_path(self):
        reg = AdapterRegistry()
        created = []
        reg.register("codex", lambda: (created.append(1), UnprovenWrite(scenarios.success(FIX), "codex"))[1])
        reg.register("antigravity", self.agy.factory("antigravity"))   # no static declaration on purpose
        self.kernel = self.new_kernel(adapters=reg)
        tid = self.queued(sample_contract(role="code", idempotency_key="r04-write"))
        report = self.run_task(tid)
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["failure_class"]), ("preflight", "EXECUTION_FAILED"))
        self.assertIn("write unknown", " ".join(res["remaining_issues"]))
        self.assertEqual(len(created), 1)                              # no extra adapter was built to read capabilities
        attempt = self.kernel.store.get_attempt(tid, 1)
        self.assertIsNone(attempt["pid"])                              # nothing was spawned

    def test_R04_a_read_only_run_only_needs_headless_proof(self):
        reg = AdapterRegistry()
        reg.register("codex", lambda: UnprovenWrite(scenarios.success(), "codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        self.kernel = self.new_kernel(adapters=reg)
        tid = self.queued(r1_contract(idempotency_key="r04-read"))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")

    def test_R02_one_tool_in_both_roles_does_not_deadlock_or_share_the_home(self):
        self.codex = ScenarioSequence(scenarios.success(FIX))
        cfg = sample_config(coordinator="codex")
        self.kernel = self.new_kernel(config=cfg)
        tid = self.queued(sample_contract(role="code", idempotency_key="r02-both"))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        attempt = self.kernel.store.get_attempt(tid, 1)
        home = Path(attempt["artifact_dir"]) / "home"
        self.assertTrue(home.is_dir())
        self.assertNotEqual(home, self.home)
        self.assertFalse((self.home / ".codex").exists())
        self.assertEqual(C.normalize("codex"), "codex")               # coordinator id unchanged by the worker run


if __name__ == "__main__":
    unittest.main()
