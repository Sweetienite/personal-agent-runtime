import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from par import cli_task
from par.config import ProviderEntry, RuntimeConfig
from par.core.store import LedgerError, Store
from par.mcp.tools import Tools, UnknownTool
from tests.unit.test_cli_task import CliWorld
from tests.unit.test_mcp_process import run_bridge


class ConfigInspection(CliWorld):
    def test_invalid_provider_config_allows_only_read_only_inspection(self):
        self.par("task", "submit", "--contract", str(self.write_contract()))
        db = self.data / "runtime.sqlite3"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        (self.data / "config" / "providers.toml").write_text(
            'schema_version = 1\n[providers.copilot]\nkind = "copilot"\nenabled = true\n', encoding="utf-8")

        code, out, err = self.par("task", "list", "--json")
        self.assertEqual(code, 0, err)
        self.assertIn("fixture-0001", out)
        self.assertIn("inspection-only", err)
        self.assertEqual(json.loads(out)[0]["task_id"], "fixture-0001")
        code, out, err = self.par("task", "status", "fixture-0001", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["next_actions"], [])
        self.assertIn("inspection-only", err)
        for args in (("events", "fixture-0001"), ("result", "fixture-0001"), ("diff", "fixture-0001")):
            code, _, err = self.par("task", *args)
            self.assertIn(code, (0, 1), err)
            self.assertIn("inspection-only", err)

        kernel = cli_task.open_kernel(SimpleNamespace(data_dir=str(self.data)), allow_inspection=True)
        tools = Tools(kernel)
        names = {tool["name"] for tool in tools.list_tools()}
        self.assertEqual(names, {"get_task_status", "get_task_result", "get_task_diff", "list_tasks"})
        payload = json.loads(tools.call("get_task_status", {"task_id": "fixture-0001"})["content"][0]["text"])
        self.assertEqual(payload["agent_next"], [])
        self.assertTrue(payload["inspection_only"])
        event_count = len(kernel.events("fixture-0001"))
        with self.assertRaises(UnknownTool):
            tools.call("approve_task", {"task_id": "fixture-0001", "reason": "no"})
        self.assertEqual(len(kernel.events("fixture-0001")), event_count)
        with self.assertRaises(sqlite3.OperationalError):
            kernel.store.conn.execute("DELETE FROM tasks WHERE task_id = ?", ("fixture-0001",))
        with self.assertRaisesRegex(LedgerError, "open read-only"):
            with kernel.store.tx():
                pass
        self.assertEqual(len(kernel.events("fixture-0001")), event_count)
        kernel.close()
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

        for command in (("run", "fixture-0001"), ("approve", "fixture-0001", "--id", "1"),
                        ("submit", "--contract", str(self.write_contract(title="second")))):
            code, _, err = self.par("task", *command)
            self.assertNotEqual(code, 0)
            self.assertIn("configuration:", err)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "run_task", "arguments": {"task_id": "fixture-0001"}}},
        ]
        stdin = b"".join(json.dumps(request).encode() + b"\n" for request in requests)
        code, stdout, stderr = run_bridge(self.home, "mcp", "serve", "--data-dir", str(self.data), stdin=stdin)
        self.assertEqual(code, 0, stderr.decode())
        replies = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual([tool["name"] for tool in replies[1]["result"]["tools"]],
                         ["get_task_status", "get_task_result", "get_task_diff", "list_tasks"])
        self.assertEqual(replies[2]["error"]["code"], -32602)
        self.assertIn(b"inspection-only", stderr)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

class ReadOnlyStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_only_store_does_not_create_missing_ledger(self):
        missing = self.data / "absent" / "runtime.sqlite3"
        missing.parent.mkdir(mode=0o700)
        with self.assertRaises(LedgerError):
            Store(missing.parent, read_only=True).list_tasks()
        self.assertFalse(missing.exists())

    def test_read_only_store_refuses_symlink_and_nonregular_ledger_paths(self):
        target = self.data / "target.sqlite3"
        target.write_bytes(b"not a ledger")
        alias = self.data / "runtime.sqlite3"
        alias.symlink_to(target)
        with self.assertRaises(LedgerError):
            Store(self.data, read_only=True).list_tasks()
        alias.unlink()
        alias.mkdir()
        with self.assertRaises(LedgerError):
            Store(self.data, read_only=True).list_tasks()


class AdapterRegistration(unittest.TestCase):
    def test_non_automatic_manifest_is_never_registered(self):
        config = RuntimeConfig(providers={"inactive": ProviderEntry("inactive", "codex", True, "/opt/copilot")})
        with mock.patch("par.cli_task.manifest_for", return_value=SimpleNamespace(automatic=False)):
            self.assertEqual(cli_task.build_adapters(config).ids(), [])
