"""Opt-in checks against the REAL bundled Codex. OFFLINE: no model is called, no quota is used, nothing is written.

    PAR_REAL_CODEX=1 python -B -m unittest tests.integration.test_real_codex -v

Skipped unless PAR_REAL_CODEX=1 (the default suite never touches the real CLI). They run only subcommands that answer from local
files (`--version`, `login status`, `features list`, `mcp list`, `debug models`, `debug prompt-input`, `exec --help`).
A real model call is a separate, explicitly approved step: tests/tools/smoke_codex.py.

One more opt-in class (PAR_REAL_CODEX_TRUST=1) runs `exec` itself - but in a scratch CODEX_HOME that holds only a COPY of
config.toml and no login, so the CLI can only fail with "401 Unauthorized": no model is called, no quota is used, and the
user's real ~/.codex is never touched. It checks the one thing that motivated the .git-less working copy: whether a writable run
edits the (scratch) config.toml.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from par.providers import codex as C
from par.providers.base import ProviderStatus, RunRequest

REAL = os.environ.get("PAR_REAL_CODEX") == "1"
EXE = os.environ.get("PAR_CODEX_EXE", C.BUNDLED_CODEX)


@unittest.skipUnless(REAL, "set PAR_REAL_CODEX=1 to check the real Codex (offline)")
class RealCodexOffline(unittest.TestCase):
    def setUp(self):
        C.forget_caches()
        self.adapter = C.CodexAdapter(EXE)
        self.home = self.adapter.codex_home
        self.environ = os.environ

    def tearDown(self):
        C.forget_caches()

    def cli(self, *args, cwd=None):
        with tempfile.TemporaryDirectory(prefix="par-real-codex-") as h:
            p = subprocess.run([EXE, *args], env=C._clean_env(self.home, h, self.environ), capture_output=True, text=True, timeout=60,
                               cwd=cwd or h, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr

    def test_the_bundled_binary_carries_openais_signature(self):
        self.assertIsNone(self.adapter.executable_problem())
        self.assertIsNone(self.adapter.signature_problem())

    def test_the_wrapper_script_next_to_it_is_refused(self):
        wrapper = Path(EXE).parents[3] / "bin" / "codex"
        if not wrapper.is_file():
            self.skipTest("no wrapper script in this install")
        self.assertIn("is a script", C.CodexAdapter(str(wrapper)).signature_problem() or "")

    def test_the_probe_sees_a_ready_install(self):
        r = self.adapter.probe()
        self.assertEqual(r.status, ProviderStatus.AVAILABLE, r.detail)
        self.assertEqual(r.facts["login"], "ChatGPT")
        self.assertTrue(r.facts["isolation"]["verified"], r.facts["isolation"])
        self.assertEqual(r.facts["isolation"]["problems"], [])
        self.assertTrue(r.facts["models_in_catalog"])
        if not r.facts["tested_version"]:
            # Intentional drift gate, not a flaky test: a version bump must not silently inherit the
            # old version's "verified" status. Real R1/R2 evidence is required before TESTED_VERSION
            # is moved. See docs/CODEX.md section 12 and the 2026-11-06 re-validation report.
            self.fail(f"Codex is {r.version}, not the version this adapter was checked against ({C.TESTED_VERSION}): "
                      "re-run the real R1/R2 re-validation before trusting this version")

    def test_every_flag_the_adapter_passes_exists_in_this_version(self):
        _, help_text, _ = self.cli("exec", "--help")
        for flag in ("--json", "--color", "--ephemeral", "--skip-git-repo-check", "--sandbox", "--model", "--cd", "--disable", "--config"):
            self.assertIn(flag, help_text, flag)
        self.assertIn("read-only", help_text)
        self.assertIn("workspace-write", help_text)
        self.assertIn("read from stdin", help_text.replace("\n", " ").replace("  ", " ").replace("are read from stdin", "read from stdin"))

    def test_a_feature_name_the_version_does_not_know_is_an_error_so_unknown_names_must_not_be_passed(self):
        rc, _, err = self.cli("--disable", "no_such_feature_xyz", "features", "list")
        self.assertNotEqual(rc, 0)
        self.assertIn("Unknown feature flag", err)

    def test_the_isolation_recipe_changes_what_the_model_is_told(self):
        """Structural check, not word counting.

        Codex 0.166.0-alpha.6 still mentions "plugin" in its *static instructions*, so the old
        ``isolated.count("plugin") == 0`` assertion produced a false failure. What must be true is
        that no plugin / app / connector / MCP *tool definition* reaches the model.
        """
        iso = self.adapter.isolation()
        self.assertTrue(iso.verified)
        with tempfile.TemporaryDirectory(prefix="par-real-codex-cwd-") as cwd:
            _, plain, _ = self.cli("debug", "prompt-input", "hello", cwd=cwd)
            _, isolated, _ = self.cli(*iso.flags(), "debug", "prompt-input", "hello", cwd=cwd)
        plain_doc, isolated_doc = json.loads(plain), json.loads(isolated)
        self.assertTrue(plain_doc and isolated_doc)
        self.assertLess(len(isolated), len(plain))

        def tool_names(doc):
            # Tool definitions are nested (namespaces hold their own tool lists), so walk the whole
            # document rather than only the top-level "tools" array.
            found: "set[str]" = set()

            def walk(node):
                if isinstance(node, dict):
                    name = node.get("name")
                    if isinstance(name, str):
                        found.add(name)
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)

            walk(doc)
            return found

        plain_tools, isolated_tools = tool_names(plain_doc), tool_names(isolated_doc)
        for dangerous in ("mcp__cua_repl", "js", "js_reset"):
            self.assertIn(dangerous, plain_tools)             # the recipe really removes something
            self.assertNotIn(dangerous, isolated_tools)
        self.assertFalse(any(n.startswith("mcp__") for n in isolated_tools))
        self.assertNotIn("<recommended_plugins>", isolated)
        self.assertNotIn("Control native apps or browsers", isolated)
        # Recorded, NOT claimed as off: this version still lists sub-agent tools in the base tool
        # list even with multi_agent disabled. See docs/CODEX.md section 12.
        print(f"\n[recorded] tool names still visible after isolation: {sorted(isolated_tools)}")

    def test_memories_read_false_with_the_recipe_applied(self):
        iso = self.adapter.isolation()
        _, out, _ = self.cli(*iso.flags(), "features", "list")
        self.assertFalse(C.parse_features(out)["memories"][1])

    def test_the_request_the_adapter_builds_is_accepted_by_the_argument_parser(self):
        """`exec --help` placed where the prompt would be: the CLI parses every flag, prints help and exits 0 - no model call."""
        with tempfile.TemporaryDirectory(prefix="par-real-codex-ws-") as ws:
            argv = self.adapter.build_argv(RunRequest("t", 1, "x", Path(ws), (self.adapter.probe().facts["models_in_catalog"] or ["x"])[0], allow_write=True))
        self.assertEqual(argv[0], EXE)
        argv = argv[:-1] + ["--help"]                                                  # replace "read the prompt from stdin" by --help
        rc, out, err = self.cli(*argv[1:])
        self.assertEqual(rc, 0, err)
        self.assertIn("Run Codex non-interactively", out)


@unittest.skipUnless(os.environ.get("PAR_REAL_CODEX_TRUST") == "1", "set PAR_REAL_CODEX_TRUST=1 (runs `exec` without any login, in a scratch CODEX_HOME)")
class RealCodexTrustEntries(unittest.TestCase):
    """Codex remembers a writable git workspace in config.toml. The Runtime avoids it by not giving Codex a `.git`."""

    def setUp(self):
        C.forget_caches()
        self._tmp = tempfile.TemporaryDirectory(prefix="par-real-codex-trust-")
        self.base = Path(os.path.realpath(self._tmp.name))
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(C.forget_caches)
        self.home = self.base / "home"
        self.home.mkdir()

    def run_exec(self, *, write, git):
        n = len(list(self.base.glob("codex_home_*")))
        codex_home = self.base / f"codex_home_{n}"
        codex_home.mkdir(mode=0o700)
        shutil.copy(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml", codex_home / "config.toml")   # a COPY; no auth.json
        workspace = self.base / f"ws_{n}"
        workspace.mkdir()
        (workspace / "a.txt").write_text("x\n")
        if git:
            env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
            for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
                subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True, env=env)
        adapter = C.CodexAdapter(EXE, codex_home=str(codex_home), environ={"PATH": "/usr/bin:/bin"})
        argv = adapter.build_argv(RunRequest("t", 1, "hello", workspace, "gpt-6-luna", allow_write=write))
        before = (codex_home / "config.toml").read_text()
        subprocess.run(argv, input="hello\n", env={"PATH": "/usr/bin:/bin", "HOME": str(self.home), "CODEX_HOME": str(codex_home)},
                       capture_output=True, text=True, timeout=120, cwd=workspace)
        return (codex_home / "config.toml").read_text() != before

    def test_a_writable_run_in_a_workspace_without_git_leaves_config_toml_alone(self):
        self.assertFalse(self.run_exec(write=True, git=False))

    def test_a_read_only_run_leaves_config_toml_alone_even_with_git(self):
        self.assertFalse(self.run_exec(write=False, git=True))

    def test_what_a_writable_git_workspace_does_is_recorded_not_asserted(self):
        changed = self.run_exec(write=True, git=True)
        print(f"\n[recorded] writable run in a git workspace edited config.toml: {changed} "
              "(True on codex 0.162.0-alpha.17.2: that is why the Worker's copy has no .git)")


if __name__ == "__main__":
    unittest.main()
