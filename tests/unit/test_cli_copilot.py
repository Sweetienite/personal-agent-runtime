"""The Copilot text-answer adapter: argv/environment safety, JSON events, routing and Kernel results.

No real Copilot call is made here. A fake launcher (tests/fake_copilot.py) records exactly what the
adapter asked for and answers from a scenario file.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from par.config import build_config, builtin_defaults
from par.core.router import config_snapshot, evaluate
from par.providers.base import FailureClass, RunRequest
from par.providers.copilot import CopilotAdapter
from par.providers.manifests import manifest_for
from par.providers.registry import AdapterRegistry
from tests.fake_copilot import FakeCopilot
from tests.kernel_fixture import KernelWorld
from tests.world import sample_contract

MODEL = "claude-haiku-4.5"


def request(workspace, *, allow_write=False, model=MODEL, prompt="Answer this question."):
    return RunRequest("task-0001", 1, prompt, Path(workspace), model, allow_write=allow_write)


def copilot_config(exe, *, enabled=True, executable=True, model=MODEL):
    profiles = {
        "schema_version": 1, "active_profile": "default",
        "workers": {"w_copilot": {"provider": "copilot", "model": model, "capabilities": ["read"]}},
        "profiles": {"default": {"coordinator": "claude_desktop", "default_worker": "w_copilot",
                                 "max_parallel_workers": 1, "worker_failure_policy": "ask",
                                 "additional_paid_credits": False,
                                 "roles": {"answer": {"primary": "w_copilot", "fallback": []}}}},
    }
    provider = {"enabled": enabled}
    if executable:
        provider["executable"] = str(exe)
    providers = {"schema_version": 1, "providers": {"copilot": provider}}
    result = build_config(profiles, providers)
    assert result.ok, result.errors
    return result.config


class Protocol(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeCopilot(self._tmp.name)
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_capabilities_are_text_only(self):
        caps = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home)).capabilities()
        self.assertEqual((caps.can_headless.value, caps.can_write.value, caps.can_restrict_tools.value),
                         ("yes", "no", "yes"))
        self.assertEqual(caps.has_sandbox.value, "unknown")

    def test_preflight_refuses_writes_a_relative_path_and_a_placeholder_model(self):
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home))
        self.assertIn("text-answer only", adapter.preflight(request(self.workspace, allow_write=True)))
        self.assertIn("absolute path", CopilotAdapter("copilot", copilot_home=str(self.fake.home)).preflight(request(self.workspace)))
        self.assertIn("model slug", adapter.preflight(request(self.workspace, model="<RUN-TIME-VERIFIED-SLUG>")))
        self.assertIsNone(adapter.preflight(request(self.workspace)))

    def test_argv_and_environment_keep_the_dangerous_grants_out(self):
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home), environ={"GITHUB_TOKEN": "secret"})
        argv = adapter.build_argv(request(self.workspace))
        for flag in ("--no-auto-update", "--output-format", "--allow-all-tools", "--available-tools=par-no-tools",
                     "--disable-builtin-mcps", "--no-custom-instructions", "--no-ask-user", "--no-bash-env",
                     "--disallow-temp-dir", "--log-level", "--secret-env-vars=GITHUB_TOKEN,GH_TOKEN,COPILOT_GITHUB_TOKEN"):
            self.assertIn(flag, argv)
        for bad in ("-p", "--prompt", "--allow-all-paths", "--allow-all-urls", "--trust-folder", "--yolo", "--allow-all"):
            self.assertNotIn(bad, argv)
        env = adapter.environment(request(self.workspace))
        self.assertEqual((env["HOME"], env["COPILOT_HOME"]), (str(self.fake.home), str(self.fake.home)))
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("COPILOT_GITHUB_TOKEN", env)

    def test_the_prompt_is_on_stdin(self):
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home))
        req = request(self.workspace, prompt="What is 2 + 2?")
        self.assertEqual(adapter.stdin_data(req), b"What is 2 + 2?")
        self.assertNotIn("What is 2 + 2?", " ".join(adapter.build_argv(req)))

    def test_events_become_a_message_and_a_terminal(self):
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home))
        message = adapter.parse_event(json.dumps({"type": "assistant.message", "content": "PAR-COPILOT-OK"}))
        terminal = adapter.parse_event(json.dumps({"type": "result", "exitCode": 0, "sessionId": "s1",
                                                   "usage": {"input_tokens": 3, "output_tokens": 2}}))
        self.assertEqual((message.kind, message.data["text"]), ("message", "PAR-COPILOT-OK"))
        self.assertEqual((terminal.kind, terminal.data["status"]), ("terminal", "SUCCESS"))
        self.assertEqual(terminal.data["usage"], {"input_tokens": 3, "output_tokens": 2})
        self.assertEqual(adapter.parse_event("not json").kind, "unparsed")

    def test_the_real_jsonl_shape_is_parsed(self):
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home))
        lines = [
            {"type": "session.info", "data": {"infoType": "configuration", "message": "Disabled tools: bash, create, edit"}},
            {"type": "assistant.message_delta", "data": {"messageId": "m1", "deltaContent": "PAR-"}},
            {"type": "assistant.message", "data": {"messageId": "m1", "content": "PAR-COPILOT-OK", "toolRequests": []}},
            {"type": "result", "sessionId": "s1", "exitCode": 0,
             "usage": {"premiumRequests": 1, "codeChanges": {"linesAdded": 0, "filesModified": []}}},
        ]
        parsed = [adapter.parse_event(json.dumps(line)) for line in lines]
        message = next(event for event in parsed if event and event.kind == "message")
        terminal = next(event for event in parsed if event and event.kind == "terminal")
        self.assertEqual(message.data["text"], "PAR-COPILOT-OK")
        self.assertEqual((terminal.data["status"], terminal.data["session_id"], terminal.data["exit_code"]),
                         ("SUCCESS", "s1", 0))

    def test_exit_zero_without_a_terminal_is_not_clean(self):
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home))
        from par.providers.base import RunOutcome, ProviderEvent
        outcome = RunOutcome(0, [ProviderEvent("message", {"text": "an answer"})])
        self.assertEqual(adapter.classify_failure(outcome), FailureClass.UNKNOWN)
        outcome = RunOutcome(1, [], stderr_tail="Error: No authentication information found.")
        self.assertEqual(adapter.classify_failure(outcome), FailureClass.AUTH_REQUIRED)

    def test_probe_is_offline_and_checks_the_bundled_flags(self):
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home), models=(MODEL,))
        probe = adapter.probe()
        self.assertEqual((probe.status.value, probe.version, probe.facts["tested_version"]), ("AVAILABLE", "1.0.5", True))
        self.assertFalse(self.fake.prompt_calls())
        self.assertTrue(self.fake.offline_calls())

    def test_a_missing_safety_flag_is_refused(self):
        self.fake.configure(help="--allow-all-tools\n--available-tools\n")
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home))
        self.assertIn("required safety flags", adapter.preflight(request(self.workspace)))

    def test_build_adapters_registers_copilot_only_when_enabled_with_an_executable(self):
        from par import cli_task
        self.assertEqual(cli_task.build_adapters(copilot_config(self.fake.exe)).ids(), ["copilot"])
        self.assertEqual(cli_task.build_adapters(copilot_config(self.fake.exe, enabled=False)).ids(), [])
        self.assertEqual(cli_task.build_adapters(copilot_config(self.fake.exe, executable=False)).ids(), [])
        self.assertFalse(builtin_defaults().providers["copilot"].enabled)


class World(KernelWorld):
    default_files = None

    def setUp(self):
        self._fake_tmp = tempfile.TemporaryDirectory()
        self.fake = FakeCopilot(self._fake_tmp.name)
        super().setUp()
        self.addCleanup(self._fake_tmp.cleanup)

    def config(self):
        return copilot_config(self.fake.exe)

    def registry(self):
        reg = AdapterRegistry()
        manifest = manifest_for("copilot")
        reg.register("copilot", self.fake.adapter, capabilities=manifest.capabilities, manifest=manifest)
        return reg

    def contract(self):
        return sample_contract(role="answer", risk="R1", allowed_paths=[],
                               acceptance={"criteria": ["answer the question"], "checks": []},
                               worker=None, title="Answer the question",
                               description="Give a short answer to the question in the fixture.")


class TextAnswer(World):
    def test_a_text_answer_is_review_required_and_unverified(self):
        tid = self.queued(self.contract())
        self.run_task(tid)
        self.assertEqual(self.status(tid), "REVIEW_REQUIRED")
        res = self.result(tid)
        self.assertEqual((res["provider"], res["worker"], res["outcome"], res["terminal_state"]),
                         ("copilot", "w_copilot", "completed", "SUCCESS"))
        self.assertEqual(res["summary"], "PAR-COPILOT-OK")
        self.assertFalse(res["summary_verified"])
        self.assertEqual(res["diff_stat"]["files"], 0)
        self.assertEqual(res["boundary"]["violations"], 0)
        calls = self.fake.prompt_calls()
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertNotIn("-p", call["argv"])
        self.assertNotIn("--prompt", call["argv"])
        self.assertIn("Answer the question", call["stdin"])
        self.assertEqual(call["env"]["COPILOT_HOME"], str(self.fake.home))
        self.assertNotIn("GITHUB_TOKEN", call["env"])
        self.assertNotIn("GH_TOKEN", call["env"])
        self.assertNotIn("COPILOT_GITHUB_TOKEN", call["env"])
        self.assertTrue(str(call["cwd"]).startswith(str(self.data)))
        self.assertEqual(call["env"]["HOME"], str(self.fake.home))
        self.assertFalse((self.attempt_dir(tid, 1) / "tmp" / "copilot-logs").exists())

    def test_a_write_task_is_not_routed_to_copilot(self):
        config = self.kernel.config
        candidate = evaluate(config_snapshot(config), "w_copilot", risk="R2", adapters=self.registry(),
                             health=self.kernel.health)
        self.assertFalse(candidate.usable)
        self.assertTrue(any("lacks the 'edit' capability" in r or "write no" in r for r in candidate.reasons), candidate.reasons)
        adapter = CopilotAdapter(str(self.fake.exe), copilot_home=str(self.fake.home))
        self.assertIn("text-answer only", adapter.preflight(request(self.workspace(), allow_write=True)))

    def test_auth_failure_is_classified(self):
        self.fake.configure(events=[], stderr="Error: No authentication information found.\n", exit=1)
        tid = self.queued(self.contract())
        self.run_task(tid)
        res = self.result(tid)
        self.assertEqual(res["failure_class"], "AUTH_REQUIRED")
        self.assertEqual(res["outcome"], "failed")
        self.assertEqual(self.status(tid), "AUTH_REQUIRED")

    def test_exit_zero_without_a_result_is_not_review_required(self):
        self.fake.configure(events=[{"type": "assistant.message", "content": "an answer"}], exit=0)
        tid = self.queued(self.contract())
        self.run_task(tid)
        res = self.result(tid)
        self.assertEqual(res["failure_class"], "UNKNOWN")
        self.assertNotEqual(res["outcome"], "completed")
        self.assertNotEqual(self.status(tid), "REVIEW_REQUIRED")

    def workspace(self):
        return self.data
