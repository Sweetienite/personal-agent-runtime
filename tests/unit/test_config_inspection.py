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
from tests.unit.test_cli_task import PROFILES, CliWorld, write_config
from tests.unit.test_mcp_process import run_bridge


class ConfigInspection(CliWorld):
    def test_invalid_provider_config_allows_only_read_only_inspection(self):
        self.par("task", "submit", "--contract", str(self.write_contract()))
        db = self.data / "runtime.sqlite3"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        (self.data / "config" / "providers.toml").write_text(
            'schema_version = 1\n[providers.unknown]\nkind = "not_a_provider"\nenabled = true\n', encoding="utf-8")

        code, out, err = self.par("task", "list", "--json")
        self.assertEqual(code, 0, err)
        self.assertIn("fixture-0001", out)
        self.assertIn("inspection-only", err)
        listed = json.loads(out)
        self.assertTrue(listed["inspection_only"])
        self.assertIn("inspection-only", listed["diagnostic"])
        self.assertEqual(listed["tasks"][0]["task_id"], "fixture-0001")
        code, out, err = self.par("task", "status", "fixture-0001", "--json")
        self.assertEqual(code, 0, err)
        status = json.loads(out)
        self.assertEqual(status["next_actions"], [])
        self.assertTrue(status["inspection_only"])
        self.assertIn("inspection-only", status["diagnostic"])
        self.assertIn("inspection-only", err)
        code, out, err = self.par("task", "events", "fixture-0001", "--json")
        self.assertEqual(code, 0, err)
        events = json.loads(out)
        self.assertTrue(events["inspection_only"])
        self.assertIsInstance(events["events"], list)
        for args in (("result", "fixture-0001"), ("diff", "fixture-0001")):
            code, _, err = self.par("task", *args)
            self.assertIn(code, (0, 1), err)
            self.assertIn("inspection-only", err)

        kernel = cli_task.open_kernel(SimpleNamespace(data_dir=str(self.data)), allow_inspection=True)
        tools = Tools(kernel)
        names = {tool["name"] for tool in tools.list_tools()}
        self.assertEqual(names, {"get_task_status", "get_task_result", "get_task_diff", "list_tasks"})
        event_count = len(kernel.events("fixture-0001"))
        payload = json.loads(tools.call("get_task_status", {"task_id": "fixture-0001"})["content"][0]["text"])
        self.assertEqual(payload["agent_next"], [])
        self.assertTrue(payload["inspection_only"])
        self.assertEqual(set(payload["decisions"]), {"mode", "coordinator", "allowed_actions", "reason"})
        self.assertEqual(payload["decisions"]["allowed_actions"], [])
        self.assertEqual(payload["decisions"]["reason"], "inspection-only")
        for name, arguments in (("get_task_result", {"task_id": "fixture-0001"}),
                                ("get_task_diff", {"task_id": "fixture-0001"}),
                                ("list_tasks", {})):
            answer = json.loads(tools.call(name, arguments)["content"][0]["text"])
            self.assertTrue(answer["inspection_only"], name)
        self.assertEqual(len(kernel.events("fixture-0001")), event_count)
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
        self.assertIn("inspection-only", replies[0]["result"]["instructions"])
        self.assertIn(b"inspection-only", stderr)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

    def test_invalid_profiles_also_allow_inspection(self):
        self.par("task", "submit", "--contract", str(self.write_contract()))
        write_config(self.data, profiles=PROFILES.replace("max_parallel_workers = 1", "max_parallel_workers = 4"))
        code, out, err = self.par("task", "list", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data["inspection_only"])
        self.assertEqual(data["tasks"][0]["task_id"], "fixture-0001")
        self.assertIn("inspection-only", err)

    def test_an_older_v1_ledger_without_candidates_still_reports_its_result(self):
        self.par("task", "submit", "--contract", str(self.write_contract()))
        code, _, err = self.par("task", "run", "fixture-0001")
        self.assertEqual(code, 0, err)
        with sqlite3.connect(self.data / "runtime.sqlite3") as conn:
            conn.execute("DROP TABLE candidates")
        (self.data / "config" / "providers.toml").write_text(
            'schema_version = 1\n[providers.unknown]\nkind = "not_a_provider"\nenabled = true\n', encoding="utf-8")

        code, out, err = self.par("task", "result", "fixture-0001", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data["inspection_only"])
        self.assertEqual(data["evidence_summary"]["integration"], "NOT_INTEGRATED")
        self.assertIn("candidate data is unavailable", err)
        for command in ("list", "status", "events"):
            argv = ("task", command, "--json") if command == "list" else ("task", command, "fixture-0001", "--json")
            code, out, err = self.par(*argv)
            self.assertEqual(code, 0, err)
            self.assertTrue(json.loads(out)["inspection_only"], command)


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
        with self.assertRaisesRegex(LedgerError, "not a regular file"):
            Store(self.data, read_only=True).list_tasks()

    def test_a_newer_schema_is_refused(self):
        writer = Store(self.data)
        writer.conn
        writer.close()
        with sqlite3.connect(self.data / "runtime.sqlite3") as conn:
            conn.execute("PRAGMA user_version = 999")
        with self.assertRaisesRegex(LedgerError, "999"):
            Store(self.data, read_only=True).list_tasks()

    def test_a_corrupt_ledger_is_refused_without_being_rewritten(self):
        db = self.data / "runtime.sqlite3"
        db.write_bytes(b"this is not a sqlite database")
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        with self.assertRaises(sqlite3.DatabaseError):
            Store(self.data, read_only=True).list_tasks()
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

    def test_read_only_open_may_create_private_wal_sidecars_but_not_change_the_ledger(self):
        writer = Store(self.data)
        writer.conn
        writer.close()
        db = self.data / "runtime.sqlite3"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        reader = Store(self.data, read_only=True)
        self.assertEqual(reader.list_tasks(), [])
        reader.close()
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
        for sidecar in self.data.glob("runtime.sqlite3-*"):
            self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600, sidecar.name)


class AdapterRegistration(unittest.TestCase):
    def test_non_automatic_manifest_is_never_registered(self):
        config = RuntimeConfig(providers={"inactive": ProviderEntry("inactive", "codex", True, "/opt/copilot")})
        with mock.patch("par.cli_task.manifest_for", return_value=SimpleNamespace(automatic=False)):
            self.assertEqual(cli_task.build_adapters(config).ids(), [])
