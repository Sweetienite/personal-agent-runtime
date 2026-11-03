"""Manual hand-off (plan 5.5): a person does a task in a desktop app (WorkBuddy, TRAE ...) and brings the result back.

Export writes a packet and holds the task; import treats what comes back like any Worker's output - nothing is believed, the Kernel compares the files
itself, enforces the boundary and runs the contract's checks. No application is started, nothing is applied to the real project."""
import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path

from par.core.kernel import NotAllowed, Refused
from par.providers.fake import ops, scenarios
from tests.kernel_fixture import BUGGY_CALC, FIXED_CALC, KernelWorld, r1_contract
from tests.unit.test_kernel_policy import using
from tests.world import sample_contract

PARTS = {f"app/part_{i}.txt": f"chunk {i}\n" for i in (1, 2, 3)}


class HandoffWorld(KernelWorld):
    default_files = None

    def exported(self, contract=None, *, to="workbuddy", **over):
        tid = self.queued(contract if contract is not None else sample_contract(role="code"), **over)
        return tid, self.kernel.handoff_export(tid, to=to)

    def fix_in(self, info, text=FIXED_CALC):
        (Path(info["workspace"]) / "app" / "calc.py").write_text(text)

    def imported(self, tid, info, **kw):
        return self.kernel.handoff_import(tid, source=Path(info["workspace"]), **kw)


class Export(HandoffWorld):
    def test_the_packet_holds_the_exact_prompt_a_private_copy_and_plain_instructions(self):
        tid, info = self.exported()
        folder = Path(info["folder"])
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["PROMPT.md", "README.md", "manifest.json", "workspace"])
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        for name in ("PROMPT.md", "README.md", "manifest.json"):
            self.assertEqual((folder / name).stat().st_mode & 0o777, 0o600, name)
        prompt = (folder / "PROMPT.md").read_text()
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
        self.assertEqual((manifest["export"], manifest["to"], manifest["risk"], manifest["allowed_paths"]), ("manual-1", "workbuddy", "R2", ["app/"]))
        self.assertIn("You may create or change files only under: app/", prompt)
        self.assertIn("## Environment", prompt)
        self.assertIn("Do not run git commands", prompt)
        readme = (folder / "README.md").read_text()
        self.assertIn("WorkBuddy", readme)
        self.assertIn(f"par task import {tid}", readme)
        self.assertIn("不要在已有的项目/会话里继续", readme)
        self.assertIn("动手之前确认一眼你面前的是上面那个新文件夹", readme)
        self.assertIn("选择工作空间", readme)
        self.assertIn("打开本地文件夹", readme)
        self.assertIn("`fixture`", readme)                     # the real project's own folder name, named by name as the wrong space to click

    def test_the_workbuddy_specific_steps_are_not_shown_for_other_apps(self):
        tid, info = self.exported(to="trae")
        readme = (Path(info["folder"]) / "README.md").read_text()
        self.assertIn("TRAE", readme)
        self.assertIn("不要在已有的项目/会话里继续", readme)          # the generic warning still applies
        self.assertNotIn("选择工作空间", readme)                   # WorkBuddy's own menu path does not - we never verified TRAE's

    def test_the_prompt_makes_the_worker_check_it_is_in_the_right_folder_before_doing_anything(self):
        tid, info = self.exported()
        prompt = (Path(info["folder"]) / "PROMPT.md").read_text()
        self.assertIn(f"# Task {tid}", prompt)                                    # what the marker's task_id must be checked against
        self.assertIn(".par-handoff.json", prompt)
        self.assertIn("stop immediately", prompt)
        self.assertIn("do not fall back to a project you already had open", prompt)
        self.assertEqual(prompt.count("summary"), 1)                              # asked for exactly once, not duplicated by the environment note
        work = Path(info["workspace"])
        self.assertEqual((work / "app" / "calc.py").read_text(), BUGGY_CALC)
        self.assertFalse((work / ".git").exists())                                              # a plain folder, not a repository
        marker = json.loads((work / ".par-handoff.json").read_text())
        self.assertEqual((marker["task_id"], marker["export"]), (tid, "manual-1"))
        self.assertEqual(work.stat().st_mode & 0o777, 0o700)

    def test_a_queued_task_is_held_so_nobody_else_starts_it_and_the_ledger_says_why(self):
        tid, info = self.exported()
        self.assertEqual(info["status"], "PAUSED")
        self.assertIn("HANDOFF_EXPORTED", self.event_types(tid))
        with self.assertRaises(NotAllowed):
            self.run_task(tid)                                                                  # nothing can run it behind the person's back
        self.assertEqual(self.codex.calls, 0)

    def test_the_real_project_is_not_touched_and_no_sensitive_file_is_in_the_copy(self):
        self.commit_in_project(self.project_path, ".env", "TOKEN=do-not-copy\n")
        self.commit_in_project(self.project_path, "keys/id_rsa", "PRIVATE\n")
        before = self.project_digest()
        tid, info = self.exported()
        work = Path(info["workspace"])
        self.assertFalse((work / ".env").exists())
        self.assertFalse((work / "keys" / "id_rsa").exists())
        self.assertEqual(self.project_digest(), before)

    def test_a_task_that_was_never_approved_for_writing_cannot_be_handed_off(self):
        res = self.submit(sample_contract(role="code"))                                         # R2: a write permission is pending
        with self.assertRaises(NotAllowed) as waiting:
            self.kernel.handoff_export(res.task_id, to="trae")
        self.assertEqual(waiting.exception.code, "not_exportable")
        self.kernel.pause(res.task_id)                                                          # paused is exportable - but the permission is still missing
        with self.assertRaises(Refused) as ctx:
            self.kernel.handoff_export(res.task_id, to="trae")
        self.assertEqual(ctx.exception.code, "missing_approval")
        self.assertFalse((self.data / "tasks" / res.task_id / "handoff").exists())

    def test_the_application_name_is_a_plain_label(self):
        tid = self.queued(sample_contract(role="code"))
        for bad in ("", "Work Buddy", "../x", "a;b", "x" * 40, "UPPER"):
            with self.assertRaises(Refused, msg=repr(bad)):
                self.kernel.handoff_export(tid, to=bad)
        self.assertEqual(self.status(tid), "QUEUED")

    def test_a_project_that_changed_since_submission_needs_the_usual_approval_first(self):
        tid = self.queued(sample_contract(role="code"))
        self.commit_in_project(self.project_path, "docs/new.md", "# new\n")
        with self.assertRaises(NotAllowed) as ctx:
            self.kernel.handoff_export(tid, to="trae")
        self.assertEqual(ctx.exception.code, "base_drift")
        self.assertEqual(self.status(tid), "PERMISSION_WAIT")

    def test_after_a_stopped_attempt_the_copy_already_holds_its_verified_work(self):
        using(self, codex=[scenarios.quota_exhausted(PARTS)])
        tid = self.queued(sample_contract(role="code"))
        self.assertEqual(self.run_task(tid).status, "QUOTA_EXHAUSTED")
        info = self.kernel.handoff_export(tid, to="trae")
        for rel, text in PARTS.items():
            self.assertEqual((Path(info["workspace"]) / rel).read_text(), text)
        self.assertIn("## Handoff from attempt 1", (Path(info["folder"]) / "PROMPT.md").read_text())
        self.assertEqual(json.loads((Path(info["folder"]) / "manifest.json").read_text())["relay_from"], 1)
        self.assertEqual(self.status(tid), "QUOTA_EXHAUSTED")                                   # a stopped task is not paused: nothing could start it

    def test_a_text_only_task_gets_no_project_files(self):
        tid = self.queued(r1_contract(risk="R0", role="code"))
        info = self.kernel.handoff_export(tid, to="workbuddy")
        self.assertIsNone(info["workspace"])
        self.assertEqual(sorted(p.name for p in Path(info["folder"]).iterdir()), ["PROMPT.md", "README.md", "manifest.json"])
        self.assertIn("--text", (Path(info["folder"]) / "README.md").read_text())

    def test_two_exports_get_their_own_folders(self):
        tid, first = self.exported()
        second = self.kernel.handoff_export(tid, to="trae")
        self.assertEqual((Path(first["folder"]).name, Path(second["folder"]).name), ("manual-1", "manual-2"))
        self.assertEqual(self.status(tid), "PAUSED")


class ImportFolder(HandoffWorld):
    def test_a_correct_result_is_verified_like_any_workers_and_waits_for_the_users_review(self):
        tid, info = self.exported()
        self.fix_in(info)
        before = self.project_digest()
        report = self.imported(tid, info)
        self.assertEqual((report.status, report.attempt), ("REVIEW_REQUIRED", 1))
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual((att["provider_id"], att["worker_id"], att["model_requested"], att["end_kind"]), ("manual_handoff", "workbuddy", "manual", "COMPLETED"))
        res = self.result(tid)
        self.assertEqual((res["provider"], res["worker"], res["outcome"], res["terminal_state"]), ("manual_handoff", "workbuddy", "completed", "NONE"))
        self.assertEqual([(t["name"], t["status"]) for t in res["tests"]], [("unit", "PASS")])          # the Kernel ran the check itself
        self.assertEqual((res["diff_stat"]["files"], res["boundary"]["violations"]), (1, 0))
        patch = self.kernel.diff(tid)
        self.assertIn(b"+    return a + b", patch)
        self.assertNotIn(b".par-handoff", patch)                                                # the bookkeeping file is not work
        self.assertEqual(self.kernel.store.health_rows(), [])                                   # a person is not a provider: no health record
        self.assertEqual(self.project_digest(), before)                                         # nothing applied
        self.assertEqual(self.kernel.accept(tid)["status"], "ACCEPTED")
        types = self.event_types(tid)
        self.assertLess(types.index("HANDOFF_EXPORTED"), types.index("HANDOFF_IMPORTED"))
        self.assertEqual(self.codex.calls + self.agy.calls, 0)                                  # no CLI was ever started

    def test_the_record_shows_the_prompt_the_person_worked_from(self):
        tid, info = self.exported()
        self.fix_in(info)
        self.imported(tid, info)
        recorded = (self.attempt_dir(tid) / "prompt.txt").read_text()
        self.assertEqual(recorded, (Path(info["folder"]) / "PROMPT.md").read_text())
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["prompt_sha256"], hashlib.sha256(recorded.encode()).hexdigest())

    def test_a_wrong_result_fails_the_kernels_own_check_however_it_was_described(self):
        tid, info = self.exported()
        self.fix_in(info, "def add(a, b):\n    return a * b\n")
        report = self.imported(tid, info, note="all tests pass, I ran them")
        self.assertEqual(report.status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], [(t["name"], t["status"]) for t in res["tests"]]), ("validation", [("unit", "FAIL")]))

    def test_a_change_outside_the_allowed_paths_is_a_boundary_violation(self):
        tid, info = self.exported()
        self.fix_in(info)
        (Path(info["workspace"]) / "README.md").write_text("# changed by the app\n")
        self.assertEqual(self.imported(tid, info).status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["boundary"]["paths"]), ("boundary", ["README.md"]))

    def test_settings_folders_an_app_adds_next_to_the_files_are_ignored_but_not_changes_to_tracked_files(self):
        tid, info = self.exported()
        self.fix_in(info)
        work = Path(info["workspace"])
        for app_dir in (".trae", ".workbuddy", ".codebuddy", ".vscode", ".cursor", ".windsurf", ".cline"):
            (work / app_dir).mkdir()
            (work / app_dir / "settings.json").write_text("{}\n")
        self.assertEqual(self.imported(tid, info).status, "REVIEW_REQUIRED")
        self.assertEqual(self.result(tid)["boundary"]["violations"], 0)
        self.assertNotIn(b".trae", self.kernel.diff(tid))

    def test_a_link_in_the_folder_is_never_accepted(self):
        tid, info = self.exported()
        self.fix_in(info)
        os.symlink("/etc/hosts", Path(info["workspace"]) / "app" / "link.txt")
        self.assertEqual(self.imported(tid, info).status, "FAILED")
        res = self.result(tid)
        self.assertEqual((res["stage"], res["boundary"]["violations"]), ("boundary", 1))
        self.assertNotIn(b"hosts", self.kernel.diff(tid) or b"")

    def test_a_sensitive_looking_file_stops_the_import_without_reading_it(self):
        tid, info = self.exported()
        self.fix_in(info)
        (Path(info["workspace"]) / "app" / ".env").write_text("TOKEN=sk-abcdefghijklmnopqrstuvwxyz0123456789\n")
        report = self.imported(tid, info)
        self.assertEqual(report.status, "FAILED")
        att = self.kernel.store.get_attempt(tid, 1)
        self.assertEqual(att["stage"], "prepare")
        self.assertIn("sensitive-looking", " ".join(self.result(tid)["remaining_issues"]))
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", json.dumps(self.result(tid)))
        self.assertFalse((self.attempt_dir(tid) / "workspace" / "app" / ".env").exists())        # it was never copied

    def test_a_pipe_in_the_folder_cannot_hang_the_copy(self):
        tid, info = self.exported()
        self.fix_in(info)
        os.mkfifo(Path(info["workspace"]) / "app" / "pipe")
        self.assertEqual(self.imported(tid, info).status, "FAILED")
        self.assertIn("special files", " ".join(self.result(tid)["remaining_issues"]))

    def test_a_folder_that_is_not_an_exported_workspace_of_this_task_is_refused_before_anything_starts(self):
        tid, info = self.exported()
        other = self.base / "elsewhere"
        other.mkdir()
        (other / "app").mkdir()
        with self.assertRaises(Refused) as ctx:
            self.kernel.handoff_import(tid, source=other)
        self.assertEqual(ctx.exception.code, "wrong_folder")
        marker = Path(info["workspace"]) / ".par-handoff.json"
        marker.write_text(json.dumps({"schema": 1, "task_id": "someone-else-0009", "export": "manual-1"}))
        with self.assertRaises(Refused):
            self.imported(tid, info)
        marker.write_text(json.dumps({"schema": 1, "task_id": tid, "export": "manual-77"}))      # an export this task never made
        with self.assertRaises(Refused):
            self.imported(tid, info)
        self.assertEqual(self.kernel.describe(tid)["attempt_count"], 0)
        self.assertEqual(self.status(tid), "PAUSED")

    def test_a_link_to_the_folder_or_a_folder_inside_the_data_directory_is_refused(self):
        tid, info = self.exported()
        link = self.base / "link"
        os.symlink(info["workspace"], link)
        with self.assertRaises(Refused):
            self.kernel.handoff_import(tid, source=link)
        inside = self.data / "tasks" / tid
        with self.assertRaises(Refused):
            self.kernel.handoff_import(tid, source=inside)
        import shutil
        copy = self.data / "scratch" / "copy_of_the_workspace"                                   # a faithful copy (marker included) - but inside our own data directory
        shutil.copytree(info["workspace"], copy)
        try:
            with self.assertRaises(Refused) as ctx:
                self.kernel.handoff_import(tid, source=copy)
            self.assertEqual(ctx.exception.code, "bad_source")
        finally:
            shutil.rmtree(copy)                                                                   # (the copy's folders are not private: keep the layout check happy)
        self.kernel.handoff_import(tid, source=Path(info["workspace"]))                           # the exported folder itself is fine (it has no fix: the check fails)

    def test_exactly_one_kind_of_input_and_the_right_kind_for_the_task(self):
        tid, info = self.exported()
        with self.assertRaises(Refused):
            self.kernel.handoff_import(tid)
        with self.assertRaises(Refused):
            self.kernel.handoff_import(tid, source=Path(info["workspace"]), patch=Path(info["readme"]))
        answer = self.base / "answer.txt"
        answer.write_text("done")
        with self.assertRaises(Refused) as ctx:
            self.kernel.handoff_import(tid, text=answer)
        self.assertEqual(ctx.exception.code, "needs_files")

    def test_a_paused_stopped_or_queued_task_can_take_the_result_but_a_finished_one_cannot(self):
        tid, info = self.exported()
        self.fix_in(info)
        self.assertEqual(self.status(tid), "PAUSED")
        self.assertEqual(self.imported(tid, info).status, "REVIEW_REQUIRED")                    # paused -> resumed -> checked
        with self.assertRaises(NotAllowed):
            self.imported(tid, info)                                                            # already under review: a second result is not taken in

    def test_a_task_that_failed_before_can_take_the_result_and_the_attempt_numbers_continue(self):
        using(self, codex=[scenarios.custom("crash", ops.exit(3))])
        tid = self.queued(sample_contract(role="code"))
        self.assertEqual(self.run_task(tid).status, "FAILED")
        info = self.kernel.handoff_export(tid, to="trae")
        self.fix_in(info)
        report = self.imported(tid, info)
        self.assertEqual((report.status, report.attempt), ("REVIEW_REQUIRED", 2))

    def test_a_stopped_attempts_work_is_in_the_copy_so_the_final_patch_holds_both(self):
        using(self, codex=[scenarios.quota_exhausted(PARTS)])
        tid = self.queued(sample_contract(role="code"))
        self.run_task(tid)
        info = self.kernel.handoff_export(tid, to="workbuddy")
        self.fix_in(info)
        self.assertEqual(self.imported(tid, info).status, "REVIEW_REQUIRED")
        patch = self.kernel.diff(tid)
        for rel in PARTS:
            self.assertIn(rel.encode(), patch)
        self.assertIn(b"return a + b", patch)


class ImportPatchAndText(HandoffWorld):
    def good_patch(self):
        fixed = self.base / "fixed"
        subprocess.run(["git", "clone", "-q", str(self.project_path), str(fixed)], check=True, capture_output=True)
        (fixed / "app" / "calc.py").write_text(FIXED_CALC)
        out = subprocess.run(["git", "-C", str(fixed), "diff"], check=True, capture_output=True).stdout
        path = self.base / "fix.patch"
        path.write_bytes(out)
        return path

    def test_a_patch_against_the_base_is_applied_to_a_clean_copy_and_verified(self):
        tid = self.queued(sample_contract(role="code"))
        report = self.kernel.handoff_import(tid, patch=self.good_patch(), worker="trae")
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["worker_id"], "trae")
        self.assertEqual([t["status"] for t in self.result(tid)["tests"]], ["PASS"])

    def test_a_patch_that_touches_protected_paths_is_refused_by_name(self):
        tid = self.queued(sample_contract(role="code"))
        bad = self.base / "bad.patch"
        bad.write_bytes(b"diff --git a/.ai/STATE.md b/.ai/STATE.md\n--- a/.ai/STATE.md\n+++ b/.ai/STATE.md\n@@ -1 +1 @@\n-x\n+y\n")
        self.assertEqual(self.kernel.handoff_import(tid, patch=bad).status, "FAILED")
        self.assertIn("protected paths", " ".join(self.result(tid)["remaining_issues"]))

    def test_a_patch_that_does_not_apply_ends_the_attempt_cleanly(self):
        tid = self.queued(sample_contract(role="code"))
        bad = self.base / "bad.patch"
        bad.write_bytes(b"diff --git a/app/calc.py b/app/calc.py\n--- a/app/calc.py\n+++ b/app/calc.py\n@@ -1,2 +1,2 @@\n def nothing(a, b):\n-    return 1\n+    return 2\n")
        self.assertEqual(self.kernel.handoff_import(tid, patch=bad).status, "FAILED")
        self.assertEqual(self.kernel.store.get_attempt(tid, 1)["stage"], "prepare")

    def test_an_oversized_or_missing_patch_is_refused_before_the_task_changes(self):
        tid = self.queued(sample_contract(role="code"))
        big = self.base / "big.patch"
        big.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
        for path in (big, self.base / "missing.patch"):
            with self.assertRaises(Refused):
                self.kernel.handoff_import(tid, patch=path)
        self.assertEqual((self.status(tid), self.kernel.describe(tid)["attempt_count"]), ("QUEUED", 0))

    def test_a_text_answer_for_a_read_only_task_is_kept_as_an_unverified_statement(self):
        tid = self.queued(r1_contract())
        answer = self.base / "answer.txt"
        answer.write_text("add() subtracts. sk-abcdefghijklmnopqrstuvwxyz0123456789\n")
        report = self.kernel.handoff_import(tid, text=answer, worker="workbuddy")
        self.assertEqual(report.status, "REVIEW_REQUIRED")
        res = self.result(tid)
        self.assertIn("add() subtracts", res["summary"])
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", res["summary"])
        self.assertFalse(res["summary_verified"])
        self.assertEqual((res["diff_stat"]["files"], res["boundary"]["violations"]), (0, 0))

    def test_a_folder_for_a_read_only_task_is_refused_and_a_text_task_takes_text(self):
        tid = self.queued(r1_contract())
        with self.assertRaises(Refused) as ctx:
            self.kernel.handoff_import(tid, source=self.base)
        self.assertEqual(ctx.exception.code, "needs_text")
        t0 = self.queued(r1_contract(risk="R0", role="code"))
        answer = self.base / "a.txt"
        answer.write_text("42")
        self.assertEqual(self.kernel.handoff_import(t0, text=answer).status, "REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
