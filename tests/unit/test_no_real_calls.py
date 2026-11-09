"""Guard rails: by default, and in the whole test-suite, nothing can reach a vendor CLI, a network or a model.

P3 adds the first real adapter (Codex). It exists only when a configuration enables it AND names its executable;
the tests that exercise it use a fake executable. A real call needs the explicit opt-in tests (PAR_REAL_CODEX_CALL=1).
"""
import ast
import json
import socket
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import par
from par import cli_task
from par.config import ProviderEntry, build_config
from par.providers.registry import default_registry
from tests.kernel_fixture import KernelWorld, r1_contract
from tests.world import sample_contract

SRC = Path(par.__file__).parent
NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "requests",
                   "httpx", "aiohttp", "websockets", "asyncio"}


def imported_modules(path):
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


class NothingReal(unittest.TestCase):
    def test_no_adapter_exists_by_default(self):
        self.assertEqual(default_registry().ids(), [])
        self.assertEqual(cli_task.build_adapters().ids(), [])
        self.assertEqual(cli_task.build_adapters(build_config(None, None).config).ids(), [])        # built-in defaults: all disabled

    def test_an_adapter_needs_an_enabled_provider_and_an_absolute_executable(self):
        def ids(**codex):
            providers = {"schema_version": 1, "providers": {"codex": codex}}
            result = build_config(None, providers)
            return cli_task.build_adapters(result.config).ids()
        self.assertEqual(ids(enabled=True), [])                                                    # no executable named: nothing
        self.assertEqual(ids(enabled=False, executable="/bin/echo"), [])                           # disabled: nothing
        self.assertEqual(ids(enabled=True, executable="/bin/echo"), ["codex"])
        legacy = build_config(None, {"schema_version": 1, "providers": {"gemini_legacy": {"enabled": True, "executable": "/bin/echo"}}})
        self.assertTrue(legacy.errors)                                                              # never enabled ...
        self.assertEqual(cli_task.build_adapters(legacy.config).ids(), [])                         # ... so never an adapter

    def test_antigravity_follows_the_same_rule_and_the_legacy_gemini_cli_never_gets_an_adapter(self):
        def ids(**agy):
            return cli_task.build_adapters(build_config(None, {"schema_version": 1, "providers": {"antigravity": agy}}).config).ids()
        self.assertEqual(ids(enabled=True), [])
        self.assertEqual(ids(enabled=False, executable="/bin/echo"), [])
        self.assertEqual(ids(enabled=True, executable="/bin/echo"), ["antigravity"])
        cfg = build_config(None, {"schema_version": 1, "providers": {}}).config
        forced = replace(cfg, providers={**cfg.providers, "gemini_legacy": ProviderEntry("gemini_legacy", "gemini_legacy", True, "/bin/echo")})
        self.assertEqual(cli_task.build_adapters(forced).ids(), [])                                  # even a hand-made config that skipped validation

    def test_the_antigravity_adapter_never_names_the_legacy_gemini_cli_or_the_flag_that_skips_permissions(self):
        tree = ast.parse((SRC / "providers" / "antigravity.py").read_text())
        docstrings = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]
        for text in literals:
            self.assertNotEqual(text, "gemini", "the legacy CLI's name is used as a program or fallback")
            self.assertNotIn("gemini_legacy", text)
            self.assertNotIn("dangerously", text)
        self.assertEqual([t for t in literals if "always-proceed" in t], ["always-proceed"])       # named once, in the rank table, to be refused

    def test_the_source_never_imports_a_network_library(self):
        # The web console (the one module that ever opened a socket) is gone (2026-11-02): nothing under src/ may import a
        # network library at all now, client or server.
        offenders = {str(p.relative_to(SRC)): sorted(imported_modules(p) & NETWORK_MODULES) for p in SRC.rglob("*.py")}
        self.assertEqual({k: v for k, v in offenders.items() if v}, {})

    def test_only_the_expected_modules_start_processes(self):
        users = sorted(str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if "subprocess" in imported_modules(p))
        # git, the process runner, the read-only Harness check, the Codex and Antigravity adapters (offline probes + the signature
        # check; the model-calling process itself is started by the runner), the scripted provider, and the CLI module for ONE command:
        # `par provider login antigravity` (now in cli_health.py) starts the vendor's own sign-in as a child (so it can remove the
        # keychain link afterwards) - nothing else
        # P5: the MCP bridge's `run_task` starts ONE thing - `python -m par task run TASK` in its own session
        self.assertEqual(users, ["cli_health.py", "cli_task.py", "execution/gitops.py", "execution/subprocesses.py", "harness/check.py",
                                 "mcp/tools.py", "providers/antigravity.py", "providers/base.py", "providers/codex.py",
                                 "providers/fake.py", "providers/fake_cli.py"])

    def test_a_config_cannot_register_the_scripted_provider(self):
        providers = {"schema_version": 1, "providers": {"fake": {"kind": "fake", "enabled": True}}}
        self.assertTrue(build_config(None, providers).errors)

    def test_no_vendor_cli_name_is_ever_an_executable_in_the_kernel_sources(self):
        for rel in ("core/kernel.py", "core/attempt.py", "execution/gitops.py", "execution/subprocesses.py", "execution/validation.py"):
            text = (SRC / rel).read_text()
            for vendor in ('"codex"', '"agy"', '"gemini"', '"claude"'):
                self.assertNotIn(f"[{vendor}", text, f"{rel} appears to launch {vendor}")


class NoNetwork(KernelWorld):
    default_files = None

    def test_a_whole_task_runs_without_the_kernel_touching_the_network(self):
        def refuse(*a, **k):
            raise AssertionError("the Kernel tried to use the network")
        with mock.patch.object(socket.socket, "connect", refuse), mock.patch.object(socket, "create_connection", refuse), \
                mock.patch.object(socket, "getaddrinfo", refuse):
            tid = self.queued(r1_contract())
            self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
            self.assertEqual(self.kernel.accept(tid)["status"], "ACCEPTED")

    def test_every_attempt_names_only_registered_scripted_workers(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertIn(att["provider_id"], self.kernel.adapters.ids())
        argv_marker = (self.attempt_dir(tid) / "stdout.log").read_text()
        self.assertIn("SUCCESS", argv_marker)
        self.assertEqual(self.codex.calls, 1)                                              # one scripted Worker, nothing else


if __name__ == "__main__":
    unittest.main()
