import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from par.cli import main
from par.execution import subprocesses as sp
from par.paths import ensure_data_dir
from par.providers.fake import ScenarioSequence, ops, scenarios
from par.providers.registry import AdapterRegistry
from tests import SRC
from tests.helpers import run_cli
from tests.kernel_fixture import KernelWorld, r1_contract
from tests.world import sample_contract

PROFILES = '''schema_version = 1
active_profile = "default"

[workers.w_code]
provider = "codex"
model = "m-code"
capabilities = ["read", "edit", "review"]

[workers.w_flash]
provider = "antigravity"
model = "m-flash"
capabilities = ["read", "edit"]

[profiles.default]
coordinator = "claude_desktop"
default_worker = "w_code"
max_parallel_workers = 1
worker_failure_policy = "ask"
additional_paid_credits = false

[profiles.default.roles.code]
primary = "w_code"
fallback = ["w_flash"]
'''
PROVIDERS = '''schema_version = 1

[providers.codex]
enabled = true

[providers.antigravity]
enabled = true
'''


def write_config(data, profiles=PROFILES, providers=PROVIDERS):
    """What a person does once: put the two config files in the data directory (mode 0600, like everything there)."""
    cfg = Path(data) / "config"
    cfg.mkdir(exist_ok=True)
    for name, text in (("profiles.toml", profiles), ("providers.toml", providers)):
        fd = os.open(cfg / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)


class CliWorld(KernelWorld):
    """The command line over a real data directory, a registered fixture project and scripted workers."""
    default_files = None

    def setUp(self):
        super().setUp()
        write_config(self.data)
        code, out, err = self.par("project", "add", "fixture", str(self.project_path), "--vcs", "git")
        self.assertEqual(code, 0, err)
        self.contract_file = self.base / "contract.json"
        self.scripted = ScenarioSequence(scenarios.success())
        self.patcher = mock.patch("par.cli_task.build_adapters", lambda config=None: self.adapters())
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def adapters(self):
        reg = AdapterRegistry()
        reg.register("codex", self.scripted.factory("codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        return reg

    def par(self, *argv, stdin=None):
        argv = list(argv)
        if "--data-dir" not in argv and argv[:2] != ["project", "add"] or (argv[:2] == ["project", "add"] and "--data-dir" not in argv):
            argv += ["--data-dir", str(self.data)]
        if stdin is not None:
            with mock.patch("sys.stdin", __import__("io").StringIO(stdin)):
                return run_cli(*argv)
        return run_cli(*argv)

    def write_contract(self, **over):
        doc = r1_contract(**over)
        self.contract_file.write_text(json.dumps(doc))
        return self.contract_file


class Basics(CliWorld):
    def test_submit_run_review_accept_close(self):
        path = self.write_contract()
        code, out, err = self.par("task", "submit", "--contract", str(path))
        self.assertEqual((code, out.strip()), (0, "submitted fixture-0001  QUEUED"), err)
        code, out, _ = self.par("task", "submit", "--contract", str(path))
        self.assertEqual(code, 0)
        self.assertIn("already submitted as fixture-0001", out)
        code, out, _ = self.par("task", "list")
        self.assertIn("fixture-0001", out)
        code, out, err = self.par("task", "run", "fixture-0001")
        self.assertEqual(code, 0, err)
        self.assertIn("REVIEW_REQUIRED", out)
        self.assertIn("par task accept fixture-0001", out)
        code, out, _ = self.par("task", "result", "fixture-0001")
        self.assertEqual(code, 0)
        self.assertIn("worker's own summary (NOT verified)", out)
        code, out, _ = self.par("task", "status", "fixture-0001")
        self.assertIn("next: accept | reject | cancel", out)
        self.assertEqual(self.par("task", "accept", "fixture-0001", "--note", "ok")[0], 0)
        self.assertEqual(self.par("task", "close", "fixture-0001")[0], 0)
        code, out, _ = self.par("task", "events", "fixture-0001")
        self.assertIn("ACCEPTED -> CLOSED", out)
        self.assertEqual(self.par("task", "status", "fixture-0001", "--json")[0], 0)

    def test_list_waiting_shows_only_tasks_a_person_must_decide_on(self):
        self.par("task", "submit", "--contract", str(self.write_contract(idempotency_key="w-1")))
        self.par("task", "submit", "--contract", str(self.write_contract(idempotency_key="w-2")))
        self.par("task", "run", "fixture-0002")
        code, out, err = self.par("task", "list", "--waiting")
        self.assertEqual(code, 0, err)
        self.assertIn("fixture-0002", out)
        self.assertNotIn("fixture-0001", out)
        code, out, _ = self.par("task", "list")
        self.assertIn("fixture-0001", out)                 # unfiltered list still shows the QUEUED one

    def test_submit_check_validates_without_submitting(self):
        code, out, err = self.par("project", "enable-write", "fixture")
        self.assertEqual(code, 0, err)
        code, out, err = self.par("project", "enable-write", "fixture", "--confirm", "fixture")
        self.assertEqual(code, 0, err)
        doc = sample_contract(project_id="fixture", idempotency_key="check-1")
        self.contract_file.write_text(json.dumps(doc))
        code, out, err = self.par("task", "submit", "--contract", str(self.contract_file), "--by", "coordinator", "--check")
        self.assertEqual(code, 0, err)
        self.assertIn("nothing was submitted", out)
        self.assertIn("would need approval: WRITE_PERMISSION", out)
        code, out, _ = self.par("task", "list")
        self.assertEqual(out.strip(), "no tasks")
        code, out, err = self.par("task", "submit", "--contract", str(self.contract_file), "--by", "coordinator", "--check", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(json.loads(out)), ["warnings", "would_approvals"])

    def test_submit_check_refuses_an_invalid_contract_the_same_way_submit_would(self):
        path = self.write_contract(allowed_paths=["app/"])          # R1 (the write_contract default) cannot write
        code, out, err = self.par("task", "submit", "--contract", str(path), "--check")
        self.assertEqual(code, 1)
        self.assertIn("invalid_contract", err)
        code, out, _ = self.par("task", "list")
        self.assertEqual(out.strip(), "no tasks")

    def test_json_output_is_json(self):
        self.par("task", "submit", "--contract", str(self.write_contract()))
        for argv in (("task", "list"), ("task", "status", "fixture-0001"), ("task", "events", "fixture-0001")):
            code, out, _ = self.par(*argv, "--json")
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out))
        code, out, _ = self.par("task", "submit", "--contract", str(self.write_contract(idempotency_key="again")), "--json")
        self.assertEqual(json.loads(out)["task_id"], "fixture-0002")

    def test_the_contract_can_come_from_standard_input(self):
        code, out, err = self.par("task", "submit", "--contract", "-", stdin=json.dumps(r1_contract()))
        self.assertEqual((code, out.strip()), (0, "submitted fixture-0001  QUEUED"), err)

    def test_diff_without_a_patch_says_so(self):
        self.par("task", "submit", "--contract", str(self.write_contract()))
        self.par("task", "run", "fixture-0001")
        code, out, err = self.par("task", "diff", "fixture-0001")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no patch", err)

    def test_run_next_and_nothing_to_run(self):
        self.assertEqual(self.par("task", "run", "--next")[1].strip(), "nothing to run: no queued task is ready")
        self.par("task", "submit", "--contract", str(self.write_contract()))
        code, out, _ = self.par("task", "run", "--next")
        self.assertEqual(code, 0)
        self.assertIn("fixture-0001  REVIEW_REQUIRED", out)

    def test_control_commands(self):
        self.par("task", "submit", "--contract", str(self.write_contract()))
        self.assertIn("pause: fixture-0001  PAUSED", self.par("task", "pause", "fixture-0001")[1])
        self.assertIn("resume: fixture-0001  QUEUED", self.par("task", "resume", "fixture-0001")[1])
        self.assertIn("cancel: fixture-0001  CANCELED", self.par("task", "cancel", "fixture-0001", "--reason", "test")[1])
        self.assertIn("retry: fixture-0001  QUEUED", self.par("task", "retry", "fixture-0001")[1])


class TerminalSafety(CliWorld):
    def test_escape_sequences_from_a_worker_are_shown_not_obeyed(self):
        esc = "\x1b[2J\x1b]0;pwned\x07"
        self.scripted = ScenarioSequence(scenarios.custom("hostile", ops.message(f"all done {esc}"), *ops.success_tail(f"all done {esc}")))
        self.par("task", "submit", "--contract", str(self.write_contract()))
        self.par("task", "run", "fixture-0001")
        code, out, _ = self.par("task", "result", "fixture-0001")
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertIn("\\x1b[2J", out)

    def test_clean_keeps_ordinary_text_and_newlines(self):
        from par.cli_task import clean
        self.assertEqual(clean("héllo 世界\n\tok"), "héllo 世界\n\tok")
        self.assertEqual(clean("a\x1bb\x9bc"), "a\\x1bb\\x9bc")


class Failures(CliWorld):
    def test_refusals_exit_1_and_say_why(self):
        code, out, err = self.par("task", "submit", "--contract", "-", stdin='{"schema_version": 1}')
        self.assertEqual(code, 1)
        self.assertIn("invalid_contract", err)
        self.assertIn("missing required", err)
        code, _, err = self.par("task", "submit", "--contract", "-", stdin=json.dumps(sample_contract()))
        self.assertEqual(code, 1)
        self.assertIn("WRITE_DISABLED", err)                                       # a registered real project cannot take write tasks

    def test_unknown_tasks_and_bad_usage_exit_2(self):
        self.assertEqual(self.par("task", "status", "fixture-0099")[0], 2)
        self.assertEqual(self.par("task", "run")[0], 2)
        self.assertEqual(self.par("task", "submit", "--contract", str(self.base / "missing.json"))[0], 2)
        self.assertEqual(self.par("task", "nonsense")[0], 2)

    def test_actions_that_do_not_apply_exit_1(self):
        self.par("task", "submit", "--contract", str(self.write_contract()))
        code, _, err = self.par("task", "accept", "fixture-0001")
        self.assertEqual(code, 1)
        self.assertIn("only REVIEW_REQUIRED", err)
        self.assertEqual(self.par("task", "resume", "fixture-0001")[0], 1)
        self.assertEqual(self.par("task", "close", "fixture-0001")[0], 1)

    def test_a_run_that_does_not_produce_a_result_exits_1(self):
        self.scripted = ScenarioSequence(scenarios.quota_exhausted())
        self.par("task", "submit", "--contract", str(self.write_contract()))
        code, out, _ = self.par("task", "run", "fixture-0001")
        self.assertEqual(code, 1)
        self.assertIn("QUOTA_EXHAUSTED", out)
        code, out, _ = self.par("task", "status", "fixture-0001")
        self.assertIn("APPROVAL #", out)
        self.assertIn("FALLBACK_SWITCH  w_code -> w_flash", out)

    def test_an_unready_data_directory_is_reported(self):
        code, _, err = run_cli("task", "list", "--data-dir", str(self.base / "nowhere"))
        self.assertEqual(code, 2)
        self.assertIn("par init", err)

    def test_a_broken_config_stops_everything_with_the_reason(self):
        write_config(self.data, profiles=PROFILES.replace("max_parallel_workers = 1", "max_parallel_workers = 4"))
        code, _, err = self.par("task", "list")
        self.assertEqual(code, 2)
        self.assertIn("max_parallel_workers", err)

    def test_without_any_adapter_installed_nothing_can_run_yet(self):
        self.patcher.stop()                                                        # the real P2 situation: no real adapters exist
        self.par("task", "submit", "--contract", str(self.write_contract()))
        code, out, _ = self.par("task", "run", "fixture-0001")
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED_NO_WORKER", out)
        self.assertIn("no adapter is registered", out)
        self.patcher.start()


class HealthCommand(CliWorld):
    def test_the_overview_says_what_can_run(self):
        code, out, _ = self.par("health")
        self.assertEqual(code, 0)
        self.assertIn("role code: w_code -> w_flash", out)
        self.assertIn("worker w_code", out)
        self.assertIn("ready", out)
        self.assertIn("adapters registered: antigravity, codex", out)

    def test_records_show_and_clear(self):
        self.scripted = ScenarioSequence(scenarios.quota_exhausted())
        self.par("task", "submit", "--contract", str(self.write_contract()))
        self.par("task", "run", "fixture-0001")
        code, out, _ = self.par("health")
        self.assertIn("health  codex/*  QUOTA_EXHAUSTED", out)
        self.assertIn("NOT READY", out)
        code, out, _ = self.par("health", "clear", "codex")
        self.assertIn("cleared 1 health record(s) for codex", out)
        self.assertNotIn("QUOTA_EXHAUSTED", self.par("health")[1])

    def test_an_empty_configuration_is_explained(self):
        for name in ("profiles.toml", "providers.toml"):
            (self.data / "config" / name).unlink()
        code, out, _ = self.par("health")
        self.assertEqual(code, 0)
        self.assertIn("no workers configured", out)


class StoppingARun(KernelWorld):
    """`par task run` as a real process: SIGTERM must leave a clean ledger, not an orphan."""
    default_files = None

    def test_sigterm_stops_the_worker_and_records_the_interruption(self):
        write_config(self.data)
        self.assertEqual(run_cli("project", "add", "fixture", str(self.project_path), "--vcs", "git", "--data-dir", str(self.data))[0], 0)
        tid = self.queued(r1_contract())
        scenario = scenarios.custom("long", ops.message("working"), ops.sleep(60))
        root = str(Path(__file__).resolve().parents[2])
        args = {"argv": ["task", "run", tid, "--data-dir", str(self.data)],
                "scenarios": [{"name": scenario.name, "steps": list(scenario.steps)}]}
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), root]), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen([sys.executable, "-B", "-m", "tests.cli_child", json.dumps(args)], cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(proc)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        peek = self.new_kernel()
        self.assertTrue(self.wait_for(lambda: peek.describe(tid)["status"] == "RUNNING"))
        pgid = peek.store.get_attempt(tid, 1)["pgid"]
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("interrupted", err)
        d = peek.describe(tid)
        self.assertEqual((d["status"], d["lock"], d["attempts"][0]["end_kind"]), ("INTERRUPTED", None, "INTERRUPTED"))
        self.assertFalse(sp.group_alive(pgid))


if __name__ == "__main__":
    unittest.main()
