import os
import stat
import subprocess
import unittest
from pathlib import Path

from par.core.kernel import NotAllowed, Refused, UnknownTask
from par.providers.fake import ScenarioSequence, ScriptedProvider, ops, scenarios
from par.providers.registry import AdapterRegistry
from tests.helpers import tree_digest
from tests.kernel_fixture import BUGGY_CALC, FIX, FIXED_CALC, KernelWorld, Disobedient, r1_contract
from tests.world import sample_contract


class ReadOnlyRuns(KernelWorld):
    default_files = None

    def test_a_text_only_task_runs_in_an_empty_folder(self):
        self.codex = ScenarioSequence(scenarios.custom("answer", ops.message("the answer is 42"), *ops.success_tail("the answer is 42")))
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract(risk="R0"))
        report = self.run_task(tid)
        self.assertEqual((report.status, report.attempt), ("REVIEW_REQUIRED", 1))
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual(list(Path(att["workspace_path"]).iterdir()), [])                   # no project files at all
        self.assertFalse((self.attempt_dir(tid) / "ref").exists())
        res = self.result(tid)
        self.assertEqual((res["outcome"], res["summary"], res["summary_verified"], res["diff_artifact"]), ("completed", "the answer is 42", False, None))
        self.assertEqual(self.statuses(tid), ["READY", "QUEUED", "STARTING", "RUNNING", "VERIFYING", "REVIEW_REQUIRED"])

    def test_a_read_only_success_never_claims_that_checks_passed(self):
        """Regression for the 2026-11-06 real Codex run: R1 had no checks and the Worker said it
        could not read anything, yet the status line said 'every check passed'."""
        self.codex = ScenarioSequence(scenarios.success())
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        reasons = [e["reason"] for e in self.kernel.events(tid)
                   if e["type"] == "STATUS_CHANGED" and e["to_status"] == "REVIEW_REQUIRED"]
        self.assertEqual(len(reasons), 1)
        self.assertNotIn("every check passed", reasons[0])
        self.assertIn("no Kernel checks ran", reasons[0])
        self.assertIn("review", reasons[0].lower())

    def test_a_read_only_task_sees_the_project_but_cannot_change_it(self):
        self.codex = ScenarioSequence(scenarios.success())
        self.kernel = self.new_kernel()
        tid = self.queued(r1_contract())
        before = self.project_digest()
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        att = self.kernel.store.get_attempt(tid, 1)
        ws = Path(att["workspace_path"])
        self.assertEqual((ws / "app" / "calc.py").read_text(), BUGGY_CALC)
        self.assertEqual(stat.S_IMODE(os.stat(ws).st_mode), 0o700)
        self.assertEqual(self.project_digest(), before)
        changes = json_load(self.attempt_dir(tid) / "changes.json")
        self.assertEqual((changes["allowed"], changes["violations"]), ([], []))
        self.assertEqual(changes["base"], self.git(self.project_path, "rev-parse", "HEAD"))

    def test_the_attempt_leaves_a_private_trail(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        d = self.attempt_dir(tid)
        for name in ("prompt.txt", "stdout.log", "stderr.log", "changes.json", "result.json"):
            self.assertEqual(os.stat(d / name).st_mode & 0o777, 0o600, name)
        prompt = (d / "prompt.txt").read_text()
        self.assertIn("# Task fixture-0001 (attempt 1): Fix add()", prompt)
        self.assertIn("This task is read-only", prompt)
        self.assertIn("# Context for project fixture", prompt)
        att = self.kernel.store.get_attempt(tid, 1)
        import hashlib
        self.assertEqual(att["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
        self.assertEqual((att["state"], att["end_kind"], att["exit_code"], att["kernel_id"]), ("ENDED", "COMPLETED", 0, self.kernel.kernel_id))
        self.assertTrue(att["pid"] and att["pgid"] == att["pid"] and att["proc_identity"])

    def test_scratch_copies_are_removed_but_the_workers_copy_is_kept_as_evidence(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        d = self.attempt_dir(tid)
        self.assertTrue((d / "workspace").is_dir())
        self.assertFalse((d / "ref").exists())
        self.assertFalse((d / "verify").exists())

    def test_a_worker_that_ignores_read_only_is_caught_by_the_comparison(self):
        reg = AdapterRegistry()
        seq = ScenarioSequence(scenarios.success({"app/calc.py": FIXED_CALC, "notes.txt": "hi\n"}))
        reg.register("codex", lambda: Disobedient(seq.next(), "codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        self.kernel = self.new_kernel(adapters=reg)
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["boundary"]["violations"]), ("boundary", 2))
        self.assertEqual(sorted(res["boundary"]["paths"]), ["app/calc.py", "notes.txt"])
        self.assertIsNone(res["diff_artifact"])
        self.assertEqual(self.kernel.describe(tid)["next_actions"], ["retry", "cancel"])


class StderrWarnings(KernelWorld):
    """An adapter's notices from standard error end up on the result, marked as the worker run's, never as the raw line."""
    default_files = None

    def test_a_soft_denial_notice_is_shown_on_the_result(self):
        class Noting(ScriptedProvider):
            def parse_stderr(self, line):
                from par.providers.base import ProviderEvent
                return ProviderEvent("warning", {"message": "a tool was soft-denied: " + line[7:]}) if line.startswith("denied ") else None
        reg = AdapterRegistry()
        seq = ScenarioSequence(scenarios.custom("denied", ops.stderr("denied run_command"), ops.message("done anyway"), *ops.success_tail("done anyway")))
        reg.register("codex", lambda: Noting(seq.next(), "codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        self.kernel = self.new_kernel(adapters=reg)
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertIn("worker run: a tool was soft-denied: run_command", self.result(tid)["remaining_issues"])


class PrepareHook(KernelWorld):
    """An adapter may fill the attempt's private HOME before the Worker starts (agy reads a Runtime-written settings file there)."""
    default_files = None

    def registry_with(self, provider_cls, seq):
        reg = AdapterRegistry()
        reg.register("codex", lambda: provider_cls(seq.next(), "codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        return reg

    def test_the_hook_fills_the_private_home_and_runs_after_preflight(self):
        calls = []

        class Preparing(ScriptedProvider):
            def preflight(self, request):
                calls.append("preflight")

            def prepare(self, request, home, tmp):
                calls.append(("prepare", request.allow_write, home.name, tmp.name, sorted(p.name for p in home.iterdir())))
                fd = os.open(home / "marker.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)     # the data dir holds only private files
                with os.fdopen(fd, "w") as f:
                    f.write("written by prepare")

        seq = ScenarioSequence(scenarios.success())
        self.kernel = self.new_kernel(adapters=self.registry_with(Preparing, seq))
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(calls, ["preflight", ("prepare", False, "home", "tmp", [])])           # empty home, read-only request
        self.assertEqual((self.attempt_dir(tid) / "home" / "marker.txt").read_text(), "written by prepare")

    def test_a_refusing_preflight_means_prepare_never_runs(self):
        calls = []

        class Refusing(ScriptedProvider):
            def preflight(self, request):
                return "not today"

            def prepare(self, request, home, tmp):
                calls.append("prepare")

        self.kernel = self.new_kernel(adapters=self.registry_with(Refusing, ScenarioSequence(scenarios.success())))
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "FAILED")
        self.assertEqual(calls, [])

    def test_a_failing_hook_stops_the_attempt_before_anything_is_started(self):
        class Failing(ScriptedProvider):
            def prepare(self, request, home, tmp):
                raise OSError("disk says no, token sk-abcdefghijklmnopqrstuvwxyz0123456789")

        self.kernel = self.new_kernel(adapters=self.registry_with(Failing, ScenarioSequence(scenarios.success())))
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "FAILED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["end_kind"], att["stage"], att["pid"]), ("FAILED", "preflight", None))
        reason = self.kernel.describe(tid)["last_failure"]
        self.assertIn("could not prepare the Worker's environment", reason)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz0123456789", reason)                    # redacted like every other reason


class CleanupHook(KernelWorld):
    """An adapter may undo what its `prepare` set up as soon as the child has ended; whatever it does or raises, the attempt is judged as before."""
    default_files = None

    def registry_with(self, provider_cls, seq):
        reg = AdapterRegistry()
        reg.register("codex", lambda: provider_cls(seq.next(), "codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        return reg

    def test_cleanup_runs_once_after_the_child_ended_and_before_the_result_is_judged(self):
        calls = []

        class Cleaning(ScriptedProvider):
            def prepare(self, request, home, tmp):
                fd = os.open(home / "marker.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)

            def cleanup(self, request, home, tmp):
                calls.append((home.name, tmp.name, (home / "marker.txt").exists()))
                (home / "marker.txt").unlink()

        self.kernel = self.new_kernel(adapters=self.registry_with(Cleaning, ScenarioSequence(scenarios.success())))
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        self.assertEqual(calls, [("home", "tmp", True)])
        self.assertFalse((self.attempt_dir(tid) / "home" / "marker.txt").exists())

    def test_cleanup_also_runs_when_prepare_failed_half_way_and_when_the_child_could_not_start(self):
        calls = []

        class HalfWay(ScriptedProvider):
            def prepare(self, request, home, tmp):
                raise OSError("disk says no")

            def cleanup(self, request, home, tmp):
                calls.append("prepare-failed")

        class NoProgram(ScriptedProvider):
            def build_argv(self, request):
                return ["/nonexistent/program"]

            def cleanup(self, request, home, tmp):
                calls.append("spawn-failed")

        for n, cls in enumerate((HalfWay, NoProgram)):
            self.kernel = self.new_kernel(adapters=self.registry_with(cls, ScenarioSequence(scenarios.success())))
            tid = self.queued(r1_contract(idempotency_key=f"cleanup-{n}"))
            self.assertEqual(self.run_task(tid).status, "FAILED")
        self.assertEqual(calls, ["prepare-failed", "spawn-failed"])

    def test_a_cleanup_that_raises_changes_nothing(self):
        class Raising(ScriptedProvider):
            def cleanup(self, request, home, tmp):
                raise RuntimeError("no")

        self.kernel = self.new_kernel(adapters=self.registry_with(Raising, ScenarioSequence(scenarios.success())))
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")


class PromptNote(KernelWorld):
    """An adapter may tell the Worker up front what its environment will not allow; the note is part of the saved, hashed prompt."""
    default_files = None

    def registry_with(self, provider_cls, seq):
        reg = AdapterRegistry()
        reg.register("codex", lambda: provider_cls(seq.next(), "codex"))
        reg.register("antigravity", self.agy.factory("antigravity"))
        return reg

    def test_the_note_is_appended_under_an_environment_heading_before_the_prompt_is_saved_and_hashed(self):
        seen = []

        class Noting(ScriptedProvider):
            def prompt_note(self, *, allow_write):
                seen.append(allow_write)
                return "  Commands do not exist here.  \n"

        self.kernel = self.new_kernel(adapters=self.registry_with(Noting, ScenarioSequence(scenarios.success())))
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        prompt = (self.attempt_dir(tid) / "prompt.txt").read_text()
        self.assertTrue(prompt.endswith("\n\n## Environment\nCommands do not exist here.\n"), prompt[-120:])
        self.assertEqual(seen, [False])                                                          # a read-only task
        import hashlib
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(), self.kernel.store.get_attempt(tid, 1)["prompt_sha256"])

    def test_an_adapter_without_the_hook_or_with_an_empty_or_failing_one_changes_nothing(self):
        class Empty(ScriptedProvider):
            def prompt_note(self, *, allow_write):
                return "   "

        class Failing(ScriptedProvider):
            def prompt_note(self, *, allow_write):
                raise RuntimeError("no")

        plain = self.new_kernel(adapters=self.registry_with(ScriptedProvider, ScenarioSequence(scenarios.success())))
        self.kernel = plain
        base = self.queued(r1_contract())
        self.run_task(base)
        baseline = (self.attempt_dir(base) / "prompt.txt").read_text()
        self.assertNotIn("## Environment", baseline)
        for n, cls in enumerate((Empty, Failing)):
            self.kernel = self.new_kernel(adapters=self.registry_with(cls, ScenarioSequence(scenarios.success())))
            tid = self.queued(r1_contract(idempotency_key=f"note-{n}"))
            self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
            self.assertNotIn("## Environment", (self.attempt_dir(tid) / "prompt.txt").read_text())


def json_load(path):
    import json
    return json.loads(Path(path).read_text())


class WriteRuns(KernelWorld):
    def test_the_full_story_of_a_file_changing_task(self):
        before = self.project_digest()
        tid = self.queued()
        report = self.run_task(tid)
        self.assertEqual((report.status, report.attempt), ("REVIEW_REQUIRED", 1))
        res = self.result(tid)
        self.assertEqual(res["tests"][0]["name"], "unit")
        self.assertEqual((res["tests"][0]["status"], res["tests"][0]["exit_code"]), ("PASS", 0))
        self.assertEqual((res["diff_stat"], res["boundary"]["violations"], res["partial"]), ({"files": 1, "additions": 1, "deletions": 1}, 0, False))
        self.assertEqual((res["provider"], res["worker"], res["model_requested"]), ("codex", "w_code", "m-code"))
        patch = self.kernel.diff(tid)
        self.assertIn(b"-    return a - b", patch)
        self.assertIn(b"+    return a + b", patch)
        self.assertEqual(self.kernel.accept_blockers(tid), [])
        self.assertEqual(self.kernel.accept(tid)["status"], "ACCEPTED")
        self.assertEqual(self.kernel.close_task(tid)["status"], "CLOSED")
        self.assertEqual(self.statuses(tid), ["READY", "PERMISSION_WAIT", "QUEUED", "STARTING", "RUNNING", "VERIFYING",
                                              "REVIEW_REQUIRED", "ACCEPTED", "CLOSED"])
        self.assertEqual(self.project_digest(), before)                                     # nothing was ever applied to the project

    def test_a_write_success_reports_how_many_kernel_checks_actually_ran(self):
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        reasons = [e["reason"] for e in self.kernel.events(tid)
                   if e["type"] == "STATUS_CHANGED" and e["to_status"] == "REVIEW_REQUIRED"]
        self.assertEqual(len(reasons), 1)
        self.assertIn("1 Kernel check(s) passed", reasons[0])
        self.assertNotIn("no Kernel checks ran", reasons[0])

    def test_the_patch_is_exactly_what_a_person_would_apply(self):
        tid = self.queued()
        self.run_task(tid)
        copy = self.base / "copy"
        subprocess.run(["git", "clone", "-q", str(self.project_path), str(copy)], check=True, capture_output=True)
        patch_file = self.base / "w.patch"
        patch_file.write_bytes(self.kernel.diff(tid))
        subprocess.run(["git", "-C", str(copy), "apply", str(patch_file)], check=True, capture_output=True)
        self.assertEqual((copy / "app" / "calc.py").read_text(), FIXED_CALC)
        proc = subprocess.run(["python3", "-m", "unittest", "-q"], cwd=copy, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_worker_that_claims_success_but_fails_the_check_does_not_get_through(self):
        self.codex = ScenarioSequence(scenarios.success({"app/calc.py": "def add(a, b):\n    return a * b\n"}, message="all done", claim_tests_pass=True))
        self.kernel = self.new_kernel()
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "FAILED")
        res = self.result(tid)
        self.assertIn("tests: PASS", res["summary"])                                        # its own statement is kept...
        self.assertIs(res["summary_verified"], False)                                       # ...and never believed
        self.assertEqual((res["stage"], res["tests"][0]["status"], res["tests"][0]["exit_code"]), ("validation", "FAIL", 1))
        self.assertTrue(res["diff_artifact"])
        with self.assertRaises(Refused):
            self.kernel.accept(tid)

    def test_changes_outside_the_allowed_paths_fail_the_attempt_and_stay_out_of_the_patch(self):
        self.codex = ScenarioSequence(scenarios.success({"app/calc.py": FIXED_CALC, "README.md": "# hijacked\n"}))
        self.kernel = self.new_kernel()
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["boundary"]["violations"], res["boundary"]["paths"]), ("boundary", 1, ["README.md"]))
        self.assertNotIn(b"hijacked", self.kernel.diff(tid))
        self.assertTrue(res["partial"])
        self.assertFalse(res["handoff_usable"])
        self.assertEqual(res["tests"], [])                                                  # checks were not even run

    def test_rule_files_state_and_secrets_are_protected_even_inside_an_allowed_folder(self):
        for path in ("AGENTS.md", ".ai/STATE.md", ".env", "app/.env", "app/AGENTS.md", "app/vendor/.git/config"):
            with self.subTest(path=path):
                self.codex = ScenarioSequence(scenarios.success({path: "x\n"}))
                self.kernel = self.new_kernel()
                tid = self.queued(sample_contract(allowed_paths=["app/"]))
                self.assertEqual(self.run_task(tid).status, "FAILED")
                res = self.result(tid)
                expected = path.split(".git/")[0] + ".git" if ".git/" in path else path     # a nested repository is reported as a whole
                self.assertEqual(res["boundary"]["paths"], [expected])
                self.assertEqual(res["boundary"]["sensitive"], 1)

    def test_a_symlink_to_somewhere_else_is_never_accepted(self):
        self.codex = ScenarioSequence(scenarios.custom("link", ops.write("app/ok.py", "x = 1\n"), ops.symlink("app/portal", "/etc"), *ops.success_tail()))
        self.kernel = self.new_kernel()
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "FAILED")
        res = self.result(tid)
        self.assertEqual(res["boundary"]["paths"], ["app/portal"])
        self.assertNotIn(b"portal", self.kernel.diff(tid))

    def test_a_worker_that_poisons_its_own_git_config_gets_nowhere(self):
        marker = self.base / "git_config_ran"
        self.codex = ScenarioSequence(scenarios.success({"app/calc.py": FIXED_CALC, ".git/config": f"[core]\n\tfsmonitor = touch {marker}\n"}))
        self.kernel = self.new_kernel()
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")                    # .git is not part of the work
        self.assertFalse(marker.exists())
        self.assertNotIn(b"fsmonitor", self.kernel.diff(tid))

    def test_build_noise_is_set_aside_not_blamed(self):
        self.codex = ScenarioSequence(scenarios.success({"app/calc.py": FIXED_CALC, "build/out.o": "obj", "run.log": "log",
                                                         "app/__pycache__/calc.cpython-312.pyc": "pyc"}))
        self.kernel = self.new_kernel()
        tid = self.queued()
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        res = self.result(tid)
        self.assertEqual(res["boundary"]["violations"], 0)
        self.assertTrue(any("3 new file(s) were ignored" in i for i in res["remaining_issues"]))
        self.assertNotIn(b"out.o", self.kernel.diff(tid))
        self.assertEqual(json_load(self.attempt_dir(tid) / "changes.json")["noise_count"], 3)

    def test_new_files_deletions_and_executable_bits_are_all_in_the_patch(self):
        self.codex = ScenarioSequence(scenarios.custom(
            "rich", ops.write("app/calc.py", FIXED_CALC), ops.write("app/tool.sh", "#!/bin/sh\necho hi\n"), ops.chmod("app/tool.sh", 0o755),
            ops.delete("app/__init__.py"), ops.write("app/__init__.py", ""), *ops.success_tail()))
        self.kernel = self.new_kernel()
        tid = self.queued(sample_contract(allowed_paths=["app/"]))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        patch = self.kernel.diff(tid)
        self.assertIn(b"new file mode 100755", patch)
        self.assertIn(b"app/tool.sh", patch)

    def test_an_r2_task_that_changes_nothing_is_reported_honestly(self):
        self.codex = ScenarioSequence(scenarios.success())
        self.kernel = self.new_kernel()
        tid = self.queued(sample_contract(acceptance={"criteria": ["x"], "checks": []}))
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        res = self.result(tid)
        self.assertIsNone(res["diff_artifact"])
        self.assertTrue(any("no changes" in i for i in res["remaining_issues"]))
        self.assertTrue(any("no mechanical check" in i for i in res["remaining_issues"]))

    def test_secret_looking_text_in_worker_output_is_redacted_everywhere(self):
        key = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"
        self.codex = ScenarioSequence(scenarios.custom("leak", ops.message(f"found key {key}"), ops.stderr(f"Authorization: Bearer {key}"),
                                                       ops.write("app/calc.py", FIXED_CALC), *ops.success_tail(f"done with {key}")))
        self.kernel = self.new_kernel()
        tid = self.queued()
        self.run_task(tid)
        blob = b"".join(f.read_bytes() for f in self.attempt_dir(tid).iterdir() if f.is_file())
        self.assertNotIn(key.encode(), blob.replace(b"work.patch", b""))                    # logs, result.json, changes.json
        self.assertNotIn(key.encode(), json_dump(self.result(tid)).encode())
        self.assertNotIn(key.encode(), json_dump(self.kernel.events(tid)).encode())

    def test_committed_secrets_are_not_given_to_the_worker(self):
        self.commit_in_project(self.project_path, ".env", "KEY=topsecret\n", "oops, committed a secret")
        self.commit_in_project(self.project_path, "keys/id_rsa", "private\n", "and a key")
        self.commit_in_project(self.project_path, "runtime/accounts/main.json", '{"account": "x"}\n', "and an account file")
        tid = self.queued(r1_contract())
        self.run_task(tid)
        ws = Path(self.kernel.store.get_attempt(tid, 1)["workspace_path"])
        self.assertFalse((ws / ".env").exists())
        self.assertFalse((ws / "keys" / "id_rsa").exists())
        self.assertFalse((ws / "runtime" / "accounts" / "main.json").exists())                 # account JSON is kept out too (T15)
        self.assertEqual(sorted(json_load(self.attempt_dir(tid) / "changes.json")["left_out_of_the_copy"]), [".env", "keys/id_rsa", "runtime/accounts/main.json"])
        self.assertTrue(any("left out of the Worker's copy" in i for i in self.result(tid)["remaining_issues"]))


def json_dump(obj):
    import json
    return json.dumps(obj)


class WorkerSurroundings(KernelWorld):
    def test_the_worker_starts_from_a_clean_environment_in_its_own_copy(self):
        self.codex = ScenarioSequence(scenarios.custom("env", ops.write_env("app/env.json"), *ops.success_tail()))
        self.kernel = self.new_kernel()
        tid = self.queued(sample_contract(acceptance={"criteria": ["x"], "checks": []}))
        from unittest import mock
        with mock.patch.dict(os.environ, {"PAR_TEST_SECRET": "hunter2", "ANTHROPIC_API_KEY": "sk-ant-abcdefghijklmnop0123", "OPENAI_API_KEY": "x"}):
            self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        ws = Path(self.kernel.store.get_attempt(tid, 1)["workspace_path"])
        doc = json_load(ws / "app" / "env.json")
        self.assertLessEqual(set(doc["names"]), {"HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "NO_COLOR", "CI",
                                                 "LC_CTYPE", "__CF_USER_TEXT_ENCODING"})
        self.assertNotIn("PAR_TEST_SECRET", doc["names"])
        self.assertNotIn("ANTHROPIC_API_KEY", doc["names"])
        attempt_dir = self.attempt_dir(tid)
        self.assertEqual(doc["HOME"], str(attempt_dir / "home"))                           # a private HOME, not the user's
        self.assertEqual(doc["TMPDIR"], str(attempt_dir / "tmp"))
        self.assertEqual(os.path.realpath(doc["cwd"]), str(ws))


class ApprovalGuard(KernelWorld):
    def test_run_refuses_a_write_task_nobody_approved_even_if_its_state_says_queued(self):
        tid = self.submit().task_id
        with self.kernel.store.tx():                                                        # a bug or tampering that skips the gate
            self.kernel.store.set_status(tid, "QUEUED", reason="forced for the test")
        with self.assertRaises(Refused) as ctx:
            self.run_task(tid)
        self.assertEqual(ctx.exception.code, "missing_approval")
        self.assertEqual(self.kernel.store.attempts(tid), [])
        self.assertEqual(self.codex.calls, 0)


class StoredContracts(KernelWorld):
    default_files = None

    def test_a_stored_contract_that_no_longer_validates_is_a_refusal_not_a_crash(self):
        from unittest import mock
        from par.core.contract import ContractError
        tid = self.queued(r1_contract())
        with mock.patch("par.core.kernel.parse_contract", side_effect=ContractError(["a rule that was tightened later"])):
            with self.assertRaises(Refused) as ctx:
                self.run_task(tid)
        self.assertEqual(ctx.exception.code, "contract_invalid")
        self.assertIn("a rule that was tightened later", ctx.exception.problems)
        self.assertEqual(self.status(tid), "QUEUED")


class RunRules(KernelWorld):
    default_files = None

    def test_only_queued_tasks_run_and_never_twice(self):
        tid = self.queued(r1_contract())
        self.assertEqual(self.run_task(tid).status, "REVIEW_REQUIRED")
        with self.assertRaises(NotAllowed):
            self.run_task(tid)
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 1)
        self.assertEqual(self.codex.calls, 1)                                               # the Worker really ran once
        waiting = self.submit().task_id                                                     # still waiting for write permission
        with self.assertRaises(NotAllowed):
            self.run_task(waiting)

    def test_run_next_picks_the_oldest_ready_task(self):
        a = self.queued(r1_contract())
        b = self.queued(r1_contract())
        self.assertEqual(self.kernel.run_next().task_id, a)
        self.assertEqual(self.kernel.run_next().task_id, b)
        self.assertIsNone(self.kernel.run_next())

    def test_dependencies_hold_a_task_back_until_they_are_accepted(self):
        first = self.queued(r1_contract())
        second = self.queued(r1_contract(dependencies=[first]))
        with self.assertRaises(Refused) as ctx:
            self.run_task(second)
        self.assertIn(f"waiting for {first}", ctx.exception.problems[0])
        self.assertEqual(self.kernel.describe(second)["waiting_for"], f"waiting for {first} (QUEUED)")
        self.assertEqual(self.kernel.run_next().task_id, first)                             # run_next skips the blocked one
        self.assertIsNone(self.kernel.run_next())
        self.kernel.accept(first)
        self.assertEqual(self.kernel.run_next().task_id, second)

    def test_a_retry_delay_is_respected(self):
        tid = self.queued(r1_contract())
        with self.kernel.store.tx():
            self.kernel.store.update_task(tid, "TASK_UPDATED", reason="test", columns={"not_before": "2999-01-01T00:00:00.000Z"})
        with self.assertRaises(Refused) as ctx:
            self.run_task(tid)
        self.assertEqual(ctx.exception.code, "not_before")
        self.assertIsNone(self.kernel.run_next())

    def test_the_context_a_worker_gets_comes_from_the_manifest(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        prompt = (self.attempt_dir(tid) / "prompt.txt").read_text()
        self.assertIn("## state: .ai/STATE.md", prompt)
        self.assertIn("## contract: .ai/tasks/active/TASK-1.md", prompt)
        self.assertIn("## Pointers", prompt)


class AttemptPrompt(KernelWorld):
    default_files = None

    def test_attempt_prompt_reads_attempt_text(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        prompt_text = self.kernel.attempt_prompt(tid)
        self.assertIsNotNone(prompt_text)
        self.assertIn("# Task fixture-0001 (attempt 1): Fix add()", prompt_text)
        self.assertEqual(prompt_text, self.kernel.attempt_prompt(tid, attempt=1))

    def test_attempt_prompt_selects_specific_or_latest_attempt(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.kernel.reject(tid)                          # REVIEW_REQUIRED is not retryable; a rejected result is
        self.kernel.retry(tid)
        self.run_task(tid)
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 2)
        p1 = self.kernel.attempt_prompt(tid, attempt=1)
        p2 = self.kernel.attempt_prompt(tid, attempt=2)
        latest = self.kernel.attempt_prompt(tid)
        self.assertIn("(attempt 1)", p1)
        self.assertIn("(attempt 2)", p2)
        self.assertEqual(latest, p2)

    def test_attempt_prompt_returns_none_for_missing_attempt_number(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        self.assertIsNone(self.kernel.attempt_prompt(tid, attempt=99))

    def test_attempt_prompt_returns_none_when_task_has_no_attempts(self):
        tid = self.queued(r1_contract())
        self.assertIsNone(self.kernel.attempt_prompt(tid))
        self.assertIsNone(self.kernel.attempt_prompt(tid, attempt=1))

    def test_attempt_prompt_safety_checks_missing_file_and_symlink(self):
        tid = self.queued(r1_contract())
        self.run_task(tid)
        p = self.attempt_dir(tid) / "prompt.txt"
        p.unlink()
        self.assertIsNone(self.kernel.attempt_prompt(tid, attempt=1))
        p.symlink_to(self.attempt_dir(tid) / "stdout.log")
        self.assertIsNone(self.kernel.attempt_prompt(tid, attempt=1))

    def test_attempt_prompt_requires_known_task(self):
        with self.assertRaises(UnknownTask):
            self.kernel.attempt_prompt("fixture-9999")


if __name__ == "__main__":
    unittest.main()
