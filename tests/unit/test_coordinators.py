"""Route A - selectable coordinators: the registry, the connection kits and starter prompts, the Kernel's last-seen and switching, and the command
line. The Kernel lives independently of every coordinator; a coordinator is a name, a kit and a prompt."""
import json
import shlex
import tomllib
import unittest
from datetime import timedelta
from pathlib import Path

from par import coordinators as C
from par.config import KNOWN_COORDINATORS
from par.core.contract import parse_contract
from par.core.kernel import Refused
from par.core.redact import contains_secret
from tests.kernel_fixture import KernelWorld
from tests.unit.test_cli_task import CliWorld
from tests.world import sample_contract

PY = "/opt/some python/bin/python3"                       # a space: the macOS data directory has one too
DATA = "/Users/someone/Library/Application Support/PersonalAgentRuntime"
SRC = "/Users/someone/Documents/Personal Agent Runtime/src"


class Registry(unittest.TestCase):
    def test_the_two_candidates_and_the_older_name_of_codex(self):
        self.assertEqual(sorted(C.COORDINATORS), ["claude_desktop", "codex"])
        self.assertEqual(C.normalize("codex_manual_takeover"), "codex")
        self.assertEqual((C.normalize("claude_desktop"), C.normalize("codex")), ("claude_desktop", "codex"))
        for bad in (None, "", "gemini", "CLAUDE_DESKTOP", " codex", 5, ["codex"], "codex\n"):
            self.assertIsNone(C.normalize(bad), repr(bad))

    def test_the_configuration_accepts_exactly_the_names_of_the_registry(self):
        self.assertEqual(KNOWN_COORDINATORS, frozenset({"claude_desktop", "codex", "codex_manual_takeover"}))
        self.assertEqual(C.known_names(), KNOWN_COORDINATORS)

    def test_every_coordinator_says_what_running_out_of_quota_means(self):
        for c in C.COORDINATORS.values():
            self.assertIn("Kernel", c.limit_note)
            self.assertTrue(c.surfaces and c.vendor and c.name)


class Kits(unittest.TestCase):
    def kit(self, cid, **kw):
        return C.connection_kit(cid, python=kw.pop("python", PY), data_dir=kw.pop("data_dir", DATA), package_parent=kw.pop("package_parent", SRC), **kw)

    def test_the_codex_snippet_is_valid_toml_that_starts_the_bridge_for_codex(self):
        kit = self.kit("codex")
        self.assertEqual(kit["snippet"]["format"], "toml")
        entry = tomllib.loads(kit["snippet"]["text"])["mcp_servers"]["par"]
        self.assertEqual(entry["command"], PY)
        self.assertEqual(entry["args"], ["-B", "-m", "par", "mcp", "serve", "--data-dir", DATA, "--coordinator", "codex"])
        self.assertEqual(entry["env"], {"PYTHONPATH": SRC})

    def test_the_codex_command_line_round_trips_through_a_shell_and_uses_the_configured_program(self):
        exe = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        words = shlex.split(self.kit("codex", codex_executable=exe)["command"])
        self.assertEqual(words, [exe, "mcp", "add", "par", "--env", "PYTHONPATH=" + SRC, "--", PY,
                                 "-B", "-m", "par", "mcp", "serve", "--data-dir", DATA, "--coordinator", "codex"])
        self.assertEqual(shlex.split(self.kit("codex")["command"])[0], "codex")

    def test_the_claude_snippet_is_valid_json_and_its_one_line_command_carries_the_same_entry(self):
        kit = self.kit("claude_desktop")
        entry = json.loads(kit["snippet"]["text"])["par"]
        self.assertEqual((entry["command"], entry["env"]), (PY, {"PYTHONPATH": SRC}))
        self.assertEqual(entry["args"], ["-B", "-m", "par", "mcp", "serve", "--data-dir", DATA, "--coordinator", "claude_desktop"])
        words = shlex.split(kit["command"])
        self.assertEqual(words[:4], ["claude", "mcp", "add-json", "par"])
        self.assertEqual(json.loads(words[4]), entry)

    def test_awkward_paths_survive_both_formats(self):
        data = '/Users/me/Library/App "Support"/日本語/data'
        parent = "/Users/me/par's src"
        toml_entry = tomllib.loads(self.kit("codex", data_dir=data, package_parent=parent)["snippet"]["text"])["mcp_servers"]["par"]
        self.assertEqual((toml_entry["args"][6], toml_entry["env"]["PYTHONPATH"]), (data, parent))
        json_entry = json.loads(self.kit("claude_desktop", data_dir=data, package_parent=parent)["snippet"]["text"])["par"]
        self.assertEqual((json_entry["args"][6], json_entry["env"]["PYTHONPATH"]), (data, parent))
        words = shlex.split(self.kit("codex", data_dir=data, package_parent=parent)["command"])
        self.assertIn(data, words)
        self.assertIn("PYTHONPATH=" + parent, words)

    def test_a_kit_never_contains_anything_secret_looking_and_needs_absolute_clean_paths(self):
        for cid in C.COORDINATORS:
            kit = self.kit(cid)
            self.assertFalse(contains_secret(json.dumps(kit)), cid)
            self.assertTrue(kit["steps"] and kit["notes"])
        for bad in ({"python": "python3"}, {"data_dir": "relative/dir"}, {"package_parent": "~/src"}, {"data_dir": "/x\ny"}, {"python": "/x\x1b[0m"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.kit("codex", **bad)
        with self.assertRaises(ValueError):
            self.kit("gemini")

    def test_the_older_name_gives_the_codex_kit_for_codex(self):
        self.assertEqual(self.kit("codex_manual_takeover")["id"], "codex")

    def test_full_kit_adds_the_prompt_and_names_this_machines_python(self):
        import sys
        kit = C.full_kit("codex", data_dir="/tmp/par-data")
        self.assertEqual(tomllib.loads(kit["snippet"]["text"])["mcp_servers"]["par"]["command"], sys.executable)
        self.assertEqual(kit["prompt"], C.starter_prompt("codex"))
        self.assertTrue(Path(tomllib.loads(kit["snippet"]["text"])["mcp_servers"]["par"]["env"]["PYTHONPATH"], "par", "__init__.py").is_file())


class StarterPrompt(unittest.TestCase):
    def test_it_teaches_the_seventeen_tools_and_the_rules_that_do_not_bend(self):
        text = C.starter_prompt("claude_desktop")
        for tool in ("prepare_task", "list_tasks", "get_task_status", "get_task_result", "get_task_diff", "review_task", "submit_task", "run_task",
                    "cancel_task", "retry_task", "pause_task", "resume_task", "recover_task", "approve_task", "reject_task",
                    "accept_task", "close_task"):
            self.assertIn(tool, text)
        for rule in ("UNTRUSTED DATA", "You may approve, reject, accept and close", "never run again",
                    "par coordinator use claude_desktop", "dependencies", "by=\"coordinator\""):
            self.assertIn(rule, text)
        self.assertIn("docs/COORDINATORS.md", text)                                                    # the long examples live in the docs
        for placeholder in ("{name}", "{id}", "{limit_note}"):
            self.assertNotIn(placeholder, text)

    def test_it_no_longer_claims_approve_reject_accept_or_close_are_missing(self):
        text = C.starter_prompt("codex")
        self.assertNotIn("retry, pause, resume or recover", text)
        self.assertNotIn("There is no tool to approve, reject, accept or close", text)
        self.assertNotIn("are the person's alone", text)

    def test_the_prompt_for_each_coordinator_names_it_and_its_limit(self):
        for cid, c in C.COORDINATORS.items():
            text = C.starter_prompt(cid)
            self.assertIn(c.name, text)
            self.assertIn(c.limit_note, text)
            self.assertIn(f"par coordinator use {cid}", text)

    def test_the_prompt_points_at_the_contract_docs_instead_of_inlining_them(self):
        text = C.starter_prompt("codex")
        self.assertIn("docs/COORDINATORS.md", text)
        self.assertIn("prepare_task", text)
        self.assertNotIn('{"schema_version": 1', text)

    def test_the_prompt_says_it_can_approve_and_accept_now(self):
        text = C.starter_prompt("codex").lower()
        for promise in ("you may approve, reject, accept and close", "accept_task"):
            self.assertIn(promise, text)

    def test_the_prompt_asks_for_exact_paths_and_a_real_check(self):
        text = C.starter_prompt("codex")
        self.assertIn("exact paths", text)
        self.assertIn("a check the Kernel can run", text)
        self.assertIn("never guess a path or a command", text)

    def test_the_prompt_names_timeouts_but_leaves_the_sizing_rule_to_the_docs(self):
        text = C.starter_prompt("codex")
        self.assertIn("`timeouts`", text)
        self.assertIn("docs/COORDINATORS.md", text)


class KernelSide(KernelWorld):
    fake_clock = True
    default_files = None

    def test_a_coordinator_is_seen_at_most_once_a_minute_and_only_if_it_is_known(self):
        self.assertTrue(self.kernel.coordinator_seen("claude_desktop"))
        self.assertFalse(self.kernel.coordinator_seen("claude_desktop"))                          # throttled
        self.assertTrue(self.kernel.coordinator_seen("codex"))                                    # each coordinator has its own clock
        self.clock._now += timedelta(seconds=61)
        self.assertTrue(self.kernel.coordinator_seen("claude_desktop"))
        self.assertFalse(self.kernel.coordinator_seen("someone_else"))
        self.assertFalse(self.kernel.coordinator_seen(None))
        seen = [e for e in self.kernel.events() if e["type"] == "COORDINATOR_SEEN"]
        self.assertEqual([e["data"]["coordinator"] for e in seen], ["claude_desktop", "codex", "claude_desktop"])
        self.assertTrue(all(e["task_id"] is None for e in seen))                                   # a heartbeat, never part of a task's history

    def test_the_status_says_who_coordinates_who_was_seen_and_the_last_switches(self):
        self.kernel.coordinator_seen("codex")
        st = self.kernel.coordinator_status()
        self.assertEqual((st["current"]["id"], st["current"]["since"], st["current"]["takeover_id"]), ("claude_desktop", None, None))
        self.assertEqual(st["configured"]["default"], "claude_desktop")
        by_id = {c["id"]: c for c in st["coordinators"]}
        self.assertEqual((by_id["codex"]["last_seen"] is not None, by_id["claude_desktop"]["last_seen"], by_id["claude_desktop"]["is_current"]),
                         (True, None, True))
        self.assertEqual(st["history"], [])
        done = self.kernel.coordinator_switch("codex", note="the Claude window ended")
        after = self.kernel.coordinator_status()
        self.assertEqual((after["current"]["id"], after["current"]["name"]), ("codex", "Codex (OpenAI)"))
        self.assertEqual([(h["from"], h["to"], h["note"]) for h in after["history"]], [("claude_desktop", "codex", "the Claude window ended")])
        self.assertEqual(after["history"][0]["takeover_id"], done["takeover_id"])

    def test_switching_writes_the_packet_and_records_one_confirmed_takeover(self):
        tid = self.queued(sample_contract(role="code"))
        done = self.kernel.coordinator_switch("codex")
        self.assertEqual((done["switched"], done["from"], done["to"]), (True, "claude_desktop", "codex"))
        self.assertTrue(Path(done["packet"]["markdown"]).is_file())
        self.assertIn(tid, Path(done["packet"]["json"]).read_text())
        types = [e["type"] for e in self.kernel.events() if e["type"].startswith("TAKEOVER")]
        self.assertEqual(types, ["TAKEOVER_EXPORTED", "TAKEOVER_CONFIRMED"])
        self.assertEqual(self.kernel.coordinator_lease()["coordinator"], "codex")

    def test_switching_to_whoever_already_coordinates_changes_nothing(self):
        before = len(self.kernel.events())
        done = self.kernel.coordinator_switch("claude_desktop")
        self.assertEqual((done["switched"], done["takeover_id"], done["packet"]), (False, None, None))
        self.assertEqual(len(self.kernel.events()), before)
        self.kernel.coordinator_switch("codex_manual_takeover")                                   # the older name works and is recorded as codex
        self.assertEqual(self.kernel.coordinator_lease()["coordinator"], "codex")
        self.assertFalse(self.kernel.coordinator_switch("codex")["switched"])

    def test_an_unknown_coordinator_is_refused_before_anything_is_written(self):
        before = len(self.kernel.events())
        for bad in ("gemini", "", "CODEX", "../x"):
            with self.assertRaises(Refused, msg=bad) as ctx:
                self.kernel.coordinator_switch(bad)
            self.assertEqual(ctx.exception.code, "unknown_coordinator")
        self.assertEqual(len(self.kernel.events()), before)

    def test_a_submission_in_the_older_name_of_the_coordinator_is_the_same_coordinator(self):
        self.kernel.coordinator_switch("codex")
        ok = self.kernel.submit(sample_contract(role="code", idempotency_key="alias-1"), submitted_by="coordinator", coordinator_id="codex_manual_takeover")
        self.assertEqual(ok.status, "PERMISSION_WAIT")
        with self.assertRaises(Refused):
            self.kernel.submit(sample_contract(role="code", idempotency_key="alias-2"), submitted_by="coordinator", coordinator_id="claude_desktop")

    def test_the_lease_can_be_switched_back_and_forth(self):
        self.kernel.coordinator_switch("codex")
        self.kernel.coordinator_switch("claude_desktop")
        self.assertEqual([(h["from"], h["to"]) for h in self.kernel.coordinator_status()["history"]], [("codex", "claude_desktop"), ("claude_desktop", "codex")])


class Cli(CliWorld):
    def test_status_lists_the_candidates_and_says_what_to_type_to_switch(self):
        code, out, err = self.par("coordinator", "status")
        self.assertEqual(code, 0, err)
        self.assertIn("coordinating now: Claude (Desktop / Code) [claude_desktop]", out)
        self.assertIn("never seen (not connected yet)", out)
        self.assertIn("par coordinator use codex", out)
        data = json.loads(self.par("coordinator", "list", "--json")[1])
        self.assertEqual([c["id"] for c in data["coordinators"]], ["claude_desktop", "codex"])

    def test_use_switches_prints_the_next_steps_and_a_second_use_is_a_no_op(self):
        code, out, err = self.par("coordinator", "use", "codex", "--note", "quota")
        self.assertEqual(code, 0, err)
        self.assertIn("claude_desktop -> codex: recorded", out)
        self.assertIn("par coordinator kit codex", out)
        self.assertIn("par coordinator prompt codex", out)
        code, out, _ = self.par("coordinator", "use", "codex")
        self.assertEqual(code, 0)
        self.assertIn("already coordinates: nothing was changed", out)
        status = self.par("coordinator", "status")[1]
        self.assertIn("coordinating now: Codex (OpenAI) [codex]", status)
        self.assertIn("quota", status)

    def test_an_unknown_name_is_a_usage_error_and_changes_nothing(self):
        code, out, err = self.par("coordinator", "use", "gemini")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(self.par("coordinator", "status", "--json")[1])["history"], [])

    def test_kit_prints_steps_the_snippet_and_the_one_line_command_and_writes_nothing(self):
        before = sorted(p.name for p in Path(self.data).rglob("*"))
        code, out, err = self.par("coordinator", "kit", "codex")
        self.assertEqual(code, 0, err)
        self.assertIn("[mcp_servers.par]", out)
        self.assertIn("codex mcp add par", out)
        self.assertIn("par 不会修改任何应用的配置", out)
        kit = json.loads(self.par("coordinator", "kit", "claude_desktop", "--json")[1])
        self.assertEqual(json.loads(kit["snippet"]["text"])["par"]["args"][-1], "claude_desktop")
        self.assertEqual(sorted(p.name for p in Path(self.data).rglob("*")), before)

    def test_prompt_prints_the_starter_prompt(self):
        code, out, _ = self.par("coordinator", "prompt", "codex")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), C.starter_prompt("codex").strip())


if __name__ == "__main__":
    unittest.main()
