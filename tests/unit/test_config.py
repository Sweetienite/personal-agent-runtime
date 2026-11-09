import copy
import os
import tempfile
import unittest
from pathlib import Path

from par import config as C
from tests import SRC

EXAMPLES = SRC.parent / "config"
SK = "sk-" + "A1b2C3d4" * 4  # assembled at run time: no literal secret in this file


def raw_profiles():
    return {
        "schema_version": 1,
        "active_profile": "main",
        "profiles": {"main": {
            "coordinator": "claude_desktop", "fallback_coordinator": "codex_manual_takeover",
            "default_worker": "w1", "max_parallel_workers": 1, "worker_failure_policy": "ask",
            "additional_paid_credits": False,
            "roles": {"code": {"primary": "w1", "fallback": ["w2"]}, "review": {"primary": "w2", "fallback": []}},
        }},
        "workers": {
            "w1": {"provider": "codex", "model": "m-1", "capabilities": ["read", "edit"]},
            "w2": {"provider": "antigravity", "model": "m-2", "capabilities": ["read"]},
        },
    }


def raw_providers():
    return {"schema_version": 1, "providers": {"codex": {"kind": "codex", "enabled": True, "executable": "/opt/x/codex"}}}


def build(profiles=None, providers=None):
    return C.build_config(profiles, providers)


def wheres(result, severity):
    return {i.where for i in result.issues if i.severity == severity}


class DefaultsAndExamples(unittest.TestCase):
    def test_builtin_defaults_are_conservative(self):
        cfg = C.builtin_defaults()
        self.assertEqual(set(cfg.providers), set(C.DEFAULT_PROVIDER_IDS))
        self.assertFalse(any(p.enabled for p in cfg.providers.values()))
        self.assertEqual((cfg.profiles, cfg.workers, cfg.active_profile), ({}, {}, None))

    def test_no_files_means_defaults_and_no_issues(self):
        r = C.load_config()
        self.assertTrue(r.ok)
        self.assertEqual(r.issues, ())
        self.assertEqual(r.config, C.builtin_defaults())

    def test_shipped_examples_validate(self):
        r = C.load_config(EXAMPLES / "profiles.example.toml", EXAMPLES / "providers.example.toml")
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.config.active_profile, "claude_main")
        self.assertEqual(set(r.config.workers), {"codex_light", "codex_strong", "agy_flash"})
        self.assertFalse(any(p.enabled for p in r.config.providers.values()), "examples ship everything disabled")
        self.assertEqual(len(r.warnings), 3, "one placeholder-model warning per worker")
        self.assertFalse(any(w.model_configured for w in r.config.workers.values()))
        self.assertEqual(r.config.profiles["claude_main"].roles["code"].fallback, ("agy_flash",))

    def test_projects_example_validates(self):
        s, issues = C.load_harness_settings(EXAMPLES / "projects.example.toml")
        self.assertEqual(issues, ())
        self.assertEqual(s.core_path, Path.home() / "Documents" / "AI_Harness_Core")
        self.assertEqual((s.search_root, s.max_depth), (Path.home() / "Documents", 4))


class ValidConfig(unittest.TestCase):
    def test_valid_synthetic_config_is_clean(self):
        r = build(raw_profiles(), raw_providers())
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.issues, ())
        self.assertTrue(r.config.providers["codex"].enabled)
        self.assertEqual(r.config.providers["codex"].executable, "/opt/x/codex")
        self.assertTrue(r.config.workers["w1"].model_configured)
        self.assertEqual(r.config.profiles["main"].roles["review"].fallback, ())

    def test_home_relative_executable_is_expanded_check(self):
        p = raw_providers()
        p["providers"]["codex"]["executable"] = "~/bin/codex"
        self.assertTrue(build(raw_profiles(), p).ok)


class FixedDecisions(unittest.TestCase):
    def test_gemini_legacy_cannot_be_enabled(self):
        p = {"schema_version": 1, "providers": {"gemini_legacy": {"kind": "gemini_legacy", "enabled": True}}}
        r = build(None, p)
        self.assertIn("providers.gemini_legacy.enabled", wheres(r, "error"))
        self.assertFalse(r.config.providers["gemini_legacy"].enabled)

    def test_gemini_legacy_cannot_be_a_worker(self):
        pr = raw_profiles()
        pr["workers"]["w2"]["provider"] = "gemini_legacy"
        self.assertIn("workers.w2.provider", wheres(build(pr), "error"))

    def test_claude_host_is_not_a_worker(self):
        pr = raw_profiles()
        pr["workers"]["w2"]["provider"] = "claude_host"
        r = build(pr)
        self.assertIn("workers.w2.provider", wheres(r, "error"))
        self.assertIn("cannot run as a worker", [i.message for i in r.errors if i.where == "workers.w2.provider"][0])

    def test_additional_paid_credits_must_be_false(self):
        for bad in (True, "false", 0, 1):
            pr = raw_profiles()
            pr["profiles"]["main"]["additional_paid_credits"] = bad
            self.assertIn("profiles.main.additional_paid_credits", wheres(build(pr), "error"), bad)

    def test_concurrency_is_one_or_two_and_one_by_default(self):
        for ok in (1, 2):
            pr = raw_profiles()
            pr["profiles"]["main"]["max_parallel_workers"] = ok
            result = build(pr)
            self.assertTrue(result.ok, ok)
            self.assertEqual(result.config.profiles["main"].max_parallel_workers, ok)
        for bad in (3, 0, -1, True, "1", 1.0, 2.5, None):
            pr = raw_profiles()
            pr["profiles"]["main"]["max_parallel_workers"] = bad
            self.assertIn("profiles.main.max_parallel_workers", wheres(build(pr), "error"), bad)
        pr = raw_profiles()
        del pr["profiles"]["main"]["max_parallel_workers"]
        self.assertEqual(build(pr).config.profiles["main"].max_parallel_workers, 1)

    def test_failure_policy_values(self):
        for ok in ("ask", "pause", "safe_auto"):
            pr = raw_profiles()
            pr["profiles"]["main"]["worker_failure_policy"] = ok
            self.assertTrue(build(pr).ok, ok)
        pr = raw_profiles()
        pr["profiles"]["main"]["worker_failure_policy"] = "yolo"
        self.assertIn("profiles.main.worker_failure_policy", wheres(build(pr), "error"))

    def test_approval_mode_defaults_to_coordinator_autonomous_and_is_a_fixed_value(self):
        pr = raw_profiles()
        pr["profiles"]["main"].pop("approval_mode", None)
        self.assertEqual(build(pr).config.profiles["main"].approval_mode, "coordinator_autonomous")
        for ok in ("coordinator_autonomous", "coordinator_scoped", "human_only"):
            pr = raw_profiles()
            pr["profiles"]["main"]["approval_mode"] = ok
            self.assertTrue(build(pr).ok, ok)
        for bad in ("human-only", "autonomous", True, None):
            pr = raw_profiles()
            pr["profiles"]["main"]["approval_mode"] = bad
            self.assertIn("profiles.main.approval_mode", wheres(build(pr), "error"), bad)

    def test_executable_must_be_absolute(self):
        p = raw_providers()
        p["providers"]["codex"]["executable"] = "codex"
        r = build(None, p)
        self.assertIn("providers.codex.executable", wheres(r, "error"))
        self.assertIsNone(r.config.providers["codex"].executable)


class ReferenceIntegrity(unittest.TestCase):
    def test_unknown_references(self):
        pr = raw_profiles()
        pr["profiles"]["main"]["default_worker"] = "nope"
        pr["profiles"]["main"]["roles"]["code"]["fallback"] = ["ghost"]
        pr["workers"]["w2"]["provider"] = "nowhere"
        r = build(pr)
        self.assertTrue({"profiles.main.default_worker", "workers.w2.provider"} <= wheres(r, "error"))

    def test_fallback_rules(self):
        for fb, label in ((["w2", "w2"], "duplicates"), (["w1"], "primary"), (["ghost"], "unknown")):
            pr = raw_profiles()
            pr["profiles"]["main"]["roles"]["code"]["fallback"] = fb
            self.assertIn("profiles.main.roles.code.fallback", wheres(build(pr), "error"), label)

    def test_active_profile_must_exist(self):
        for bad in ("ghost", None, 3):
            pr = raw_profiles()
            pr["active_profile"] = bad
            self.assertIn("profiles.active_profile", wheres(build(pr), "error"), bad)

    def test_invalid_profile_is_dropped_not_half_used(self):
        pr = raw_profiles()
        pr["profiles"]["main"]["max_parallel_workers"] = 4
        r = build(pr)
        self.assertNotIn("main", r.config.profiles)


class Workers(unittest.TestCase):
    def test_placeholder_model_is_a_warning_not_an_error(self):
        pr = raw_profiles()
        pr["workers"]["w1"]["model"] = "<RUN-TIME-VERIFIED-SLUG>"
        r = build(pr)
        self.assertTrue(r.ok)
        self.assertIn("workers.w1.model", wheres(r, "warning"))
        self.assertFalse(r.config.workers["w1"].model_configured)

    def test_bad_model_and_capabilities(self):
        for field, value in (("model", ""), ("model", 5), ("capabilities", ["read", "shell"]), ("capabilities", "read")):
            pr = raw_profiles()
            pr["workers"]["w1"][field] = value
            self.assertIn(f"workers.w1.{field}", wheres(build(pr), "error"), (field, value))

    def test_a_worker_surface_is_derived_from_its_provider_not_from_a_vendor_name(self):
        pr = raw_profiles()
        pr["workers"]["w_manual"] = {"provider": "manual_handoff", "model": "m", "capabilities": ["read"]}
        ps = raw_providers()
        ps["providers"]["manual_handoff"] = {"kind": "manual_handoff", "enabled": False}
        ps["providers"]["claude"] = {"kind": "claude_host", "enabled": True}
        r = build(pr, ps)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual((r.config.providers["codex"].surface, r.config.providers["claude"].surface,
                          r.config.providers["manual_handoff"].surface),
                         ("native_cli", "programmatic_session", "manual"))
        self.assertEqual((r.config.workers["w1"].surface, r.config.workers["w_manual"].surface), ("native_cli", "manual"))

    def test_unrecognised_coordinator_is_only_a_warning(self):
        pr = raw_profiles()
        pr["profiles"]["main"]["coordinator"] = "something_else"
        r = build(pr)
        self.assertTrue(r.ok)
        self.assertIn("profiles.main.coordinator", wheres(r, "warning"))


class SecretsAndTypos(unittest.TestCase):
    def test_secret_looking_key_is_refused(self):
        p = raw_providers()
        p["providers"]["codex"]["api_key"] = ""
        self.assertIn("providers.codex.api_key", wheres(build(None, p), "error"))

    def test_secret_looking_values_are_refused(self):
        for value in (SK, "someone@example.com"):
            pr = raw_profiles()
            pr["workers"]["w1"]["model"] = value
            r = build(pr)
            self.assertIn("workers.w1.model", wheres(r, "error"), value)

    def test_innocent_keys_are_not_mistaken_for_secrets(self):
        pr = raw_profiles()
        pr["profiles"]["main"]["max_tokens"] = 5   # unknown key -> warning only
        r = build(pr)
        self.assertTrue(r.ok)
        self.assertIn("profiles.main.max_tokens", wheres(r, "warning"))

    def test_unknown_keys_are_warnings(self):
        pr = raw_profiles()
        pr["profiles"]["main"]["worker_failure_polcy"] = "ask"
        pr["surprise"] = 1
        r = build(pr)
        self.assertTrue(r.ok)
        self.assertTrue({"profiles.main.worker_failure_polcy", "surprise"} <= wheres(r, "warning"))

    def test_schema_version(self):
        for bad in (2, "1", True, None):
            pr = raw_profiles()
            pr["schema_version"] = bad
            self.assertIn("profiles.schema_version", wheres(build(pr), "error"), bad)

    def test_unknown_provider_kind(self):
        p = {"schema_version": 1, "providers": {"x": {"kind": "gpt_direct"}}}
        self.assertIn("providers.x.kind", wheres(build(None, p), "error"))


class FilesOnDisk(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data, mode="w"):
        p = self.dir / name
        with open(p, mode) as f:
            f.write(data)
        return p

    def test_invalid_toml_falls_back_to_defaults_with_an_error(self):
        p = self.write("profiles.toml", "schema_version = = 1\n")
        r = C.load_config(profiles_path=p)
        self.assertFalse(r.ok)
        self.assertEqual(r.errors[0].where, "profiles.toml")
        self.assertIn("invalid TOML", r.errors[0].message)
        self.assertEqual(r.config, C.builtin_defaults())

    def test_missing_file(self):
        r = C.load_config(providers_path=self.dir / "providers.toml")
        self.assertFalse(r.ok)
        self.assertIn("file not found", r.errors[0].message)

    def test_non_utf8_file(self):
        p = self.write("providers.toml", b"\xff\xfe\x00bad", mode="wb")
        r = C.load_config(providers_path=p)
        self.assertFalse(r.ok)

    def test_errors_do_not_echo_absolute_paths(self):
        r = C.load_config(providers_path=self.dir / "providers.toml")
        self.assertNotIn(str(self.dir), r.errors[0].where + r.errors[0].message)


class HarnessSettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, text):
        p = self.dir / "projects.toml"
        p.write_text(text)
        return C.load_harness_settings(p, home="/Users/x")

    def test_defaults_without_a_file(self):
        s, issues = C.load_harness_settings(None, home="/Users/x")
        self.assertEqual(issues, ())
        self.assertEqual((s.core_path, s.search_root, s.max_depth),
                         (Path("/Users/x/Documents/AI_Harness_Core"), Path("/Users/x/Documents"), 4))

    def test_overrides(self):
        s, issues = self.load('schema_version = 1\n[harness]\ncore_path = "/opt/core"\nsearch_root = "/opt/docs"\nmax_depth = 2\n')
        self.assertEqual(issues, ())
        self.assertEqual((s.core_path, s.search_root, s.max_depth), (Path("/opt/core"), Path("/opt/docs"), 2))

    def test_invalid_values_keep_defaults_and_report(self):
        for text, key in (
            ('schema_version = 1\n[harness]\ncore_path = "relative/core"\n', "projects.harness.core_path"),
            ('schema_version = 1\n[harness]\nmax_depth = 0\n', "projects.harness.max_depth"),
            ('schema_version = 1\n[harness]\nmax_depth = 9\n', "projects.harness.max_depth"),
            ('schema_version = 1\n[harness]\nmax_depth = true\n', "projects.harness.max_depth"),
            ('schema_version = 2\n', "projects.schema_version"),
        ):
            s, issues = self.load(text)
            self.assertIn(key, {i.where for i in issues if i.severity == "error"}, text)
            self.assertEqual(s.max_depth, 4)

    def test_unknown_key_warns_and_secrets_are_refused(self):
        _, issues = self.load('schema_version = 1\n[harness]\ncore = "/x"\n')
        self.assertIn("projects.harness.core", {i.where for i in issues if i.severity == "warning"})
        _, issues = self.load('schema_version = 1\n[harness]\napi_key = "x"\n')
        self.assertTrue([i for i in issues if i.severity == "error"])

    def test_missing_file_falls_back(self):
        s, issues = C.load_harness_settings(self.dir / "nope.toml", home="/Users/x")
        self.assertEqual(s, C.default_harness_settings("/Users/x"))
        self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()
