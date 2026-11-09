import tempfile
import unittest
from pathlib import Path

from par.core.clock import FakeClock
from par.core.health import Health
from par.core.router import AUTOMATIC_KINDS, config_snapshot, evaluate, route
from par.core.store import Store
from par.providers.base import Capabilities, ProviderAdapter, Tri
from par.providers.fake import ScenarioSequence, ScriptedProvider, scenarios
from par.providers.manifests import AUTOMATIC_KINDS as MANIFEST_KINDS, BUILTIN_MANIFESTS, manifest_for
from par.providers.registry import AdapterRegistry, NoAdapter, default_registry
from tests.world import sample_config


def registry(*ids):
    reg = AdapterRegistry()
    for pid in ids:
        reg.register(pid, ScenarioSequence(scenarios.success()).factory(pid))
    return reg


class _CapsProvider(ScriptedProvider):
    def __init__(self, caps: Capabilities) -> None:
        super().__init__(scenarios.success())
        self._caps = caps

    def capabilities(self) -> Capabilities:
        return self._caps


class Registry(unittest.TestCase):
    def test_the_default_registry_is_empty_so_nothing_real_can_run_yet(self):
        self.assertEqual(default_registry().ids(), [])

    def test_register_create_and_has(self):
        reg = registry("codex")
        self.assertTrue(reg.has("codex"))
        self.assertFalse(reg.has("antigravity"))
        self.assertEqual(reg.ids(), ["codex"])
        a, b = reg.create("codex"), reg.create("codex")
        self.assertIsInstance(a, ScriptedProvider)
        self.assertIsNot(a, b)                                  # a fresh adapter per attempt
        self.assertIsInstance(a, ProviderAdapter)

    def test_unknown_duplicate_and_invalid_ids(self):
        reg = registry("codex")
        with self.assertRaises(NoAdapter):
            reg.create("antigravity")
        with self.assertRaises(ValueError):
            reg.register("codex", lambda: None)
        for bad in ("Codex", "", "a b", "1x", "../x"):
            with self.assertRaises(ValueError, msg=bad):
                reg.register(bad, lambda: None)

    def test_the_automatic_kinds_come_from_one_manifest_table(self):
        self.assertEqual(AUTOMATIC_KINDS, MANIFEST_KINDS)
        self.assertEqual(MANIFEST_KINDS, frozenset({"codex", "antigravity"}))
        for kind, manifest in BUILTIN_MANIFESTS.items():
            self.assertEqual(manifest.kind, kind)
            self.assertTrue(manifest.automatic)
            self.assertTrue(manifest.platforms and manifest.auth_mode and manifest.version_bound)
            self.assertIsNotNone(manifest.capabilities)
        for not_automatic in ("manual_handoff", "claude_host", "gemini_legacy", "no_such_kind"):
            self.assertIsNone(manifest_for(not_automatic))

    def test_a_registered_manifest_is_carried_with_the_provider(self):
        reg = AdapterRegistry()
        manifest = BUILTIN_MANIFESTS["codex"]
        reg.register("codex", ScenarioSequence(scenarios.success()).factory("codex"),
                     capabilities=manifest.capabilities, manifest=manifest)
        self.assertIs(reg.manifest("codex"), manifest)
        self.assertEqual(reg.capabilities("codex"), manifest.capabilities)
        with self.assertRaises(NoAdapter):
            reg.manifest("missing")
        plain = registry("codex")
        self.assertIsNone(plain.manifest("codex"))

    def test_scenario_sequence_hands_out_in_order_and_repeats_the_last(self):
        seq = ScenarioSequence(scenarios.rate_limited(), scenarios.success())
        names = [seq.next().name for _ in range(4)]
        self.assertEqual(names, ["rate_limited", "success", "success", "success"])
        self.assertEqual(seq.calls, 4)
        with self.assertRaises(ValueError):
            ScenarioSequence()
        seq.reset(scenarios.crash())
        self.assertEqual((seq.calls, seq.next().name), (0, "crash"))
        with self.assertRaises(ValueError):
            seq.reset()


class HealthCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.store = Store(Path(self._tmp.name), self.clock)
        self.health = Health(self.store)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.store.close)

    def record(self, *args, **kw):
        with self.store.tx():
            return self.health.record_failure(*args, **kw)


class HealthRules(HealthCase):
    def test_a_fresh_provider_has_no_known_problem(self):
        v = self.health.verdict("codex", "m1")
        self.assertTrue(v.usable)
        self.assertEqual(v.describe(), "no known problem")

    def test_rate_limit_is_model_scoped_and_expires(self):
        self.assertTrue(self.record("RATE_LIMITED", "codex", "m1", retry_after=60))
        self.assertFalse(self.health.verdict("codex", "m1").usable)
        self.assertTrue(self.health.verdict("codex", "m2").usable)          # another model of the same provider
        self.assertTrue(self.health.verdict("antigravity", "m1").usable)    # another provider
        self.clock.advance(59)
        self.assertFalse(self.health.verdict("codex", "m1").usable)
        self.clock.advance(2)
        self.assertTrue(self.health.verdict("codex", "m1").usable)          # no polling needed: the clock decides

    def test_rate_limit_without_a_usable_retry_after_uses_the_default_cooldown(self):
        for bad in (None, 0, -5, 10 ** 9, "soon", float("inf")):
            self.record("RATE_LIMITED", "codex", "m1", retry_after=bad)
            v = self.health.verdict("codex", "m1")
            self.assertFalse(v.usable, bad)
            self.clock.advance(299)
            self.assertFalse(self.health.verdict("codex", "m1").usable, bad)
            self.clock.advance(2)
            self.assertTrue(self.health.verdict("codex", "m1").usable, bad)

    def test_quota_and_auth_cover_the_whole_provider_until_cleared(self):
        for failure in ("QUOTA_EXHAUSTED", "AUTH_REQUIRED"):
            self.record(failure, "codex", "m1")
            for model in ("m1", "m2", "anything"):
                v = self.health.verdict("codex", model)
                self.assertEqual((v.usable, v.status, v.scope, v.until), (False, failure, "provider", None))
            self.clock.advance(10 ** 6)                                       # time alone never heals these
            self.assertFalse(self.health.verdict("codex", "m1").usable)
            with self.store.tx():
                self.assertEqual(self.health.clear("codex"), 1)
            self.assertTrue(self.health.verdict("codex", "m2").usable)

    def test_model_unavailable_blames_only_that_model(self):
        self.record("MODEL_UNAVAILABLE", "codex", "gone")
        self.assertFalse(self.health.verdict("codex", "gone").usable)
        self.assertTrue(self.health.verdict("codex", "other").usable)
        self.assertEqual(self.health.verdict("codex", "gone").scope, "model")

    def test_the_most_serious_problem_is_reported(self):
        self.record("RATE_LIMITED", "codex", "m1", retry_after=60)
        self.record("AUTH_REQUIRED", "codex", "m1")
        self.assertEqual(self.health.verdict("codex", "m1").status, "AUTH_REQUIRED")

    def test_failures_that_say_nothing_about_health_are_not_recorded(self):
        for failure in ("EXECUTION_FAILED", "UNKNOWN", "TRANSIENT_ERROR", "PERMISSION_BLOCKED", "bogus"):
            self.assertFalse(self.record(failure, "codex", "m1"), failure)
        self.assertEqual(self.store.health_rows(), [])

    def test_a_real_success_clears_earlier_records(self):
        self.record("RATE_LIMITED", "codex", "m1", retry_after=600)
        self.record("QUOTA_EXHAUSTED", "codex", "m1")
        with self.store.tx():
            self.assertEqual(self.health.record_success("codex", "m1"), 2)
            self.assertEqual(self.health.record_success("codex", "m1"), 0)
        self.assertTrue(self.health.verdict("codex", "m1").usable)

    def test_changes_are_on_the_record_without_secrets(self):
        self.record("RATE_LIMITED", "codex", "m1", retry_after=60, detail="Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123", task_id=None)
        with self.store.tx():
            self.health.clear("codex", "m1")
        types = [e["type"] for e in self.store.events()]
        self.assertEqual(types, ["HEALTH_CHANGED", "HEALTH_CLEARED"])
        self.assertNotIn("abcdefghijklmnop", str(self.store.health_rows()) + str(self.store.events()))

    def test_describe_says_what_to_do(self):
        self.record("AUTH_REQUIRED", "codex", "m1")
        self.assertIn("par health clear", self.health.verdict("codex", "m1").describe())
        self.record("RATE_LIMITED", "antigravity", "m1", retry_after=60)
        self.assertIn("until 2026-10-14T03:01:00.000Z", self.health.verdict("antigravity", "m1").describe())


class Routing(HealthCase):
    def setUp(self):
        super().setUp()
        self.cfg = sample_config()
        self.snap = config_snapshot(self.cfg)
        self.reg = registry("codex", "antigravity")

    def route(self, snap=None, **kw):
        kw.setdefault("role", "code")
        kw.setdefault("worker", None)
        kw.setdefault("risk", "R2")
        return route(snap or self.snap, adapters=kw.pop("adapters", self.reg), health=self.health, **kw)

    def test_snapshot_is_plain_data_with_a_stable_digest(self):
        snap = config_snapshot(self.cfg)
        self.assertEqual(snap["profile"], "default")
        self.assertEqual(snap["roles"]["code"], {"primary": "w_code", "fallback": ["w_flash"]})
        self.assertEqual(snap["workers"]["w_code"]["provider"], "codex")
        self.assertEqual((snap["workers"]["w_code"]["surface"], snap["providers"]["codex"]["surface"]),
                         ("native_cli", "native_cli"))
        self.assertEqual(snap["worker_failure_policy"], "ask")
        self.assertEqual(snap["digest"], config_snapshot(sample_config())["digest"])
        self.assertNotEqual(snap["digest"], config_snapshot(sample_config(policy="pause"))["digest"])
        self.assertNotEqual(snap["digest"], config_snapshot(sample_config(code_model="other"))["digest"])
        self.assertNotIn("executable", str(snap))

    def test_a_role_routes_to_its_primary_then_its_fallbacks_in_order(self):
        r = self.route()
        self.assertEqual((r.source, [c.worker_id for c in r.chain]), ("role", ["w_code", "w_flash"]))
        self.assertEqual(r.chosen.worker_id, "w_code")
        self.assertFalse(r.needs_switch)
        self.assertTrue(all(c.usable for c in r.chain))

    def test_a_disabled_primary_means_a_switch_is_needed_and_says_why(self):
        r = self.route(snap=config_snapshot(sample_config(codex=False)))
        self.assertTrue(r.needs_switch)
        self.assertEqual(r.chosen.worker_id, "w_flash")
        self.assertIn("provider 'codex' is disabled", r.chain[0].reasons)

    def test_an_explicit_worker_is_the_whole_chain(self):
        r = self.route(worker="w_flash")
        self.assertEqual((r.source, [c.worker_id for c in r.chain]), ("worker_override", ["w_flash"]))
        r = self.route(worker="w_flash", snap=config_snapshot(sample_config(agy=False)))
        self.assertIsNone(r.chosen)                                       # no quiet substitution of another worker
        self.assertFalse(r.needs_switch)

    def test_no_role_and_no_worker_uses_the_default_worker(self):
        r = self.route(role=None)
        self.assertEqual((r.source, [c.worker_id for c in r.chain]), ("default_worker", ["w_code"]))

    def test_an_unconfigured_role_has_an_empty_chain_and_a_reason(self):
        r = self.route(role="poetry")
        self.assertEqual((r.chain, r.chosen), ((), None))
        self.assertIn("poetry", r.explain())

    def test_without_any_profile_the_reason_says_how_to_create_one(self):
        from par.config import builtin_defaults
        snap = config_snapshot(builtin_defaults())
        r = self.route(snap=snap)
        self.assertEqual((r.chain, r.chosen), ((), None))
        self.assertIn("no profile is configured yet", r.explain())
        self.assertIn("profiles.example.toml", r.explain())

    def test_an_unknown_worker_is_unusable(self):
        r = self.route(worker="w_ghost")
        self.assertIsNone(r.chosen)
        self.assertIn("not defined", r.chain[0].reasons[0])

    def test_placeholder_models_are_not_run(self):
        r = self.route(snap=config_snapshot(sample_config(code_model="<RUN-TIME-VERIFIED-SLUG>")))
        self.assertIn("the model slug is still a placeholder", r.chain[0].reasons)
        self.assertEqual(r.chosen.worker_id, "w_flash")

    def test_capability_follows_the_risk_tier(self):
        snap = config_snapshot(sample_config(flash_caps=("read",)))
        self.assertTrue(evaluate(snap, "w_flash", risk="R1", adapters=self.reg, health=self.health).usable)
        self.assertTrue(evaluate(snap, "w_flash", risk="R0", adapters=self.reg, health=self.health).usable)
        cand = evaluate(snap, "w_flash", risk="R2", adapters=self.reg, health=self.health)
        self.assertFalse(cand.usable)
        self.assertIn("the worker lacks the 'edit' capability", cand.reasons)

    def test_a_write_run_needs_the_adapter_to_declare_write_capability(self):
        reg = AdapterRegistry()
        reg.register("codex", lambda: _CapsProvider(Capabilities(can_headless=Tri.YES, can_write=Tri.UNKNOWN)),
                     capabilities=Capabilities(can_headless=Tri.YES, can_write=Tri.UNKNOWN))
        cand = evaluate(self.snap, "w_code", risk="R2", adapters=reg, health=self.health)
        self.assertFalse(cand.usable)
        self.assertTrue(any("write unknown" in r for r in cand.reasons), cand.reasons)
        self.assertTrue(evaluate(self.snap, "w_code", risk="R1", adapters=reg, health=self.health).usable)

    def test_an_automatic_run_needs_the_adapter_to_declare_headless(self):
        reg = AdapterRegistry()
        reg.register("codex", lambda: _CapsProvider(Capabilities(can_headless=Tri.UNKNOWN, can_write=Tri.YES)),
                     capabilities=Capabilities(can_headless=Tri.UNKNOWN, can_write=Tri.YES))
        cand = evaluate(self.snap, "w_code", risk="R1", adapters=reg, health=self.health)
        self.assertFalse(cand.usable)
        self.assertTrue(any("headless unknown" in r for r in cand.reasons), cand.reasons)

    def test_a_declaration_never_creates_an_adapter_just_to_be_read(self):
        created = []
        reg = AdapterRegistry()
        reg.register("codex", lambda: created.append(1), capabilities=Capabilities(can_headless=Tri.YES, can_write=Tri.YES))
        self.assertEqual(reg.capabilities("codex").can_write, Tri.YES)
        self.assertEqual(created, [])                      # routing must not consume a run/scenario

    def test_an_adapter_that_declares_nothing_leaves_the_decision_to_the_runtime_gate(self):
        reg = AdapterRegistry()
        reg.register("codex", lambda: None)
        self.assertIsNone(reg.capabilities("codex"))
        self.assertTrue(evaluate(self.snap, "w_code", risk="R1", adapters=reg, health=self.health).usable)

    def test_a_role_name_is_not_bound_to_a_provider(self):
        from par.config import build_config
        profiles = {
            "schema_version": 1, "active_profile": "p",
            "workers": {"w": {"provider": "antigravity", "model": "m", "capabilities": ["read"]}},
            "profiles": {"p": {"coordinator": "codex", "default_worker": "w", "max_parallel_workers": 1,
                               "worker_failure_policy": "ask", "additional_paid_credits": False,
                               "roles": {"codex": {"primary": "w", "fallback": []}}}},
        }
        result = build_config(profiles, {"schema_version": 1, "providers": {"antigravity": {"enabled": True}}})
        self.assertTrue(result.ok, result.errors)
        route_result = route(config_snapshot(result.config), role="codex", worker=None, risk="R1",
                             adapters=registry("antigravity"), health=self.health)
        self.assertEqual(route_result.chosen.worker_id, "w")

    def test_a_provider_without_a_registered_adapter_cannot_run(self):
        r = self.route(adapters=registry("antigravity"))
        self.assertIn("no adapter is registered for 'codex'", r.chain[0].reasons)
        self.assertEqual(r.chosen.worker_id, "w_flash")
        r = self.route(adapters=default_registry())
        self.assertIsNone(r.chosen)

    def test_only_codex_and_antigravity_kinds_run_automatically(self):
        self.assertEqual(AUTOMATIC_KINDS, {"codex", "antigravity"})
        snap = config_snapshot(self.cfg)
        snap["providers"]["codex"]["kind"] = "manual_handoff"
        cand = evaluate(snap, "w_code", risk="R1", adapters=self.reg, health=self.health)
        self.assertFalse(cand.usable)
        self.assertTrue(any("never run automatically" in r for r in cand.reasons))

    def test_health_cooldowns_take_a_worker_out_and_bring_it_back(self):
        self.record("RATE_LIMITED", "codex", "m-code", retry_after=120)
        r = self.route()
        self.assertTrue(r.needs_switch)
        self.assertTrue(any("RATE_LIMITED" in x for x in r.chain[0].reasons))
        self.clock.advance(121)
        self.assertFalse(self.route().needs_switch)

    def record(self, *args, **kw):
        with self.store.tx():
            self.health.record_failure(*args, **kw)

    def test_excluded_workers_are_skipped_with_a_reason(self):
        r = self.route(exclude=["w_code"])
        self.assertEqual(r.chosen.worker_id, "w_flash")
        self.assertIn("already tried for this task", r.chain[0].reasons)

    def test_when_nothing_is_usable_the_explanation_names_every_worker(self):
        r = self.route(adapters=default_registry(), snap=config_snapshot(sample_config(agy=False)))
        self.assertIsNone(r.chosen)
        text = r.explain()
        self.assertIn("w_code:", text)
        self.assertIn("w_flash:", text)

    def test_routing_is_deterministic(self):
        self.assertEqual(self.route(), self.route())
        self.assertEqual(self.route(worker="w_flash"), self.route(worker="w_flash"))


if __name__ == "__main__":
    unittest.main()
