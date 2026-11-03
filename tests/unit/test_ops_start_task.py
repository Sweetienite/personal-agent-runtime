"""tests/unit/test_ops_start_task.py: exercises ops/start_task.py against faked `par` responses
(this script never touches git, so unlike test_ops_land_task.py no fixture repo is needed).
The faked `par` call shapes (submit --json, status --json's `pending_approvals[].{id,kind,
payload}`, approve --id N) were checked against a real throwaway `par fixture create` project
before writing this, not guessed."""
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

OPS = Path(__file__).resolve().parents[2] / "ops" / "start_task.py"
spec = importlib.util.spec_from_file_location("start_task", OPS)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

CONTRACT = {
    "schema_version": 1,
    "project_id": "fx",
    "title": "probe",
    "description": "d",
    "role": "code",
    "worker": "agy_flash",
    "risk": "R2",
    "allowed_paths": ["app/calc.py"],
    "acceptance": {"criteria": ["n/a"], "checks": []},
    "idempotency_key": "test-1",
}

WRITE_PERMISSION_MATCH = {"id": 1, "kind": "WRITE_PERMISSION", "payload": {"allowed_paths": ["app/calc.py"], "base_commit": "x"}}
WRITE_PERMISSION_MISMATCH = {"id": 1, "kind": "WRITE_PERMISSION", "payload": {"allowed_paths": ["app/other.py"]}}
WORKER_OVERRIDE_MATCH = {"id": 2, "kind": "WORKER_OVERRIDE", "payload": {"worker": "agy_flash"}}
WORKER_OVERRIDE_MISMATCH = {"id": 2, "kind": "WORKER_OVERRIDE", "payload": {"worker": "codex_luna"}}
BASE_DRIFT = {"id": 3, "kind": "BASE_DRIFT", "payload": {"changed": ["x"]}}


def fake_par(pending, *, status="PERMISSION_WAIT", worker_requested="agy_flash", pinned_worker=None,
             run_result=None, approve_fails=frozenset(), final_status=None,
             result_text="result text\n", diff_text="diff text\n", result_fails=False, diff_fails=False):
    """Simulates submit -> status -> approve -> status -> run -> status [-> result -> diff], with
    `approve` actually removing the matching id and, once `run` has happened, `status` reporting
    `final_status` (when given) - like the real Kernel does."""
    state = {"pending": list(pending), "status": status, "ran": False}

    def run(cmd, **kwargs):
        if "submit" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"task_id": "fx-0001", "status": state["status"], "created": True}), stderr="")
        if "status" in cmd and "--json" in cmd:
            if state["ran"] and final_status is not None:
                current = final_status
            else:
                current = state["status"] if state["pending"] else "QUEUED"
            body = {
                "task_id": "fx-0001", "title": "probe",
                "status": current,
                "pending_approvals": state["pending"],
                "worker_requested": worker_requested, "pinned_worker": pinned_worker,
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")
        if "approve" in cmd:
            aid = int(cmd[cmd.index("--id") + 1])
            if aid in approve_fails:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="approve failed")
            state["pending"] = [a for a in state["pending"] if a["id"] != aid]
            return subprocess.CompletedProcess(cmd, 0, stdout="approved\n", stderr="")
        if "result" in cmd:
            if result_fails:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="result failed")
            return subprocess.CompletedProcess(cmd, 0, stdout=result_text, stderr="")
        if "diff" in cmd:
            if diff_fails:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="diff failed")
            return subprocess.CompletedProcess(cmd, 0, stdout=diff_text, stderr="")
        if "run" in cmd:
            state["ran"] = True
            return run_result or subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected par call: {cmd}")

    return run


def never_called(name):
    def run(*args, **kwargs):
        raise AssertionError(f"{name} must not run here")

    return run


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "contract.json"
        self.path.write_text(json.dumps(CONTRACT), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()


class Start(Base):
    def test_matching_write_permission_is_approved_and_stops_without_run(self):
        out = []
        code = helper.start(self.path, cwd=Path("."), run_flag=False, par_run=fake_par([WRITE_PERMISSION_MATCH]), out=out.append)
        self.assertEqual(code, 0)
        self.assertTrue(any("已提交并批准" in line and "QUEUED" in line for line in out), out)
        self.assertTrue(any("--run" in line for line in out), out)

    def test_matching_worker_override_is_approved(self):
        out = []
        code = helper.start(self.path, cwd=Path("."), run_flag=False, par_run=fake_par([WORKER_OVERRIDE_MATCH]), out=out.append)
        self.assertEqual(code, 0)
        self.assertTrue(any("QUEUED" in line for line in out), out)

    def test_both_matching_approvals_are_each_approved_by_their_own_id(self):
        out = []
        code = helper.start(
            self.path, cwd=Path("."), run_flag=False,
            par_run=fake_par([WRITE_PERMISSION_MATCH, WORKER_OVERRIDE_MATCH]), out=out.append,
        )
        self.assertEqual(code, 0)
        self.assertTrue(any("QUEUED" in line for line in out), out)

    def test_mismatched_write_permission_is_left_pending_not_approved_not_rejected(self):
        out = []
        code = helper.start(self.path, cwd=Path("."), run_flag=False, par_run=fake_par([WRITE_PERMISSION_MISMATCH]), out=out.append)
        self.assertEqual(code, 1)
        self.assertTrue(any("不会自动处理" in line for line in out), out)
        self.assertTrue(any("WRITE_PERMISSION" in line for line in out), out)

    def test_mismatched_worker_override_is_left_pending(self):
        out = []
        code = helper.start(self.path, cwd=Path("."), run_flag=False, par_run=fake_par([WORKER_OVERRIDE_MISMATCH]), out=out.append)
        self.assertEqual(code, 1)
        self.assertTrue(any("不会自动处理" in line for line in out), out)

    def test_unrecognized_approval_kind_is_left_pending(self):
        out = []
        code = helper.start(self.path, cwd=Path("."), run_flag=False, par_run=fake_par([BASE_DRIFT]), out=out.append)
        self.assertEqual(code, 1)
        self.assertTrue(any("BASE_DRIFT" in line for line in out), out)

    def test_one_matching_and_one_unmatched_leaves_the_matching_one_pending_too(self):
        """Nothing is approved until every pending approval is something this script can vouch for."""
        out = []
        code = helper.start(
            self.path, cwd=Path("."), run_flag=False,
            par_run=fake_par([WRITE_PERMISSION_MATCH, BASE_DRIFT]), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertTrue(any("BASE_DRIFT" in line for line in out), out)

    def test_without_run_flag_the_run_command_is_never_called(self):
        out = []
        par_run = fake_par([WRITE_PERMISSION_MATCH])
        original = par_run

        def guarded(cmd, **kwargs):
            if "run" in cmd and "status" not in cmd:
                raise AssertionError("must not run without --run")
            return original(cmd, **kwargs)

        code = helper.start(self.path, cwd=Path("."), run_flag=False, par_run=guarded, out=out.append)
        self.assertEqual(code, 0)

    def test_run_flag_runs_once_and_reports_the_terminal_state(self):
        out = []
        run_result = subprocess.CompletedProcess(["par"], 0, stdout="", stderr="")
        code = helper.start(
            self.path, cwd=Path("."), run_flag=True,
            par_run=fake_par([WRITE_PERMISSION_MATCH], run_result=run_result), out=out.append,
        )
        self.assertEqual(code, 0)
        self.assertTrue(any("即将真实调用" in line and "agy_flash" in line for line in out), out)
        self.assertTrue(any("跑完一次 attempt" in line for line in out), out)

    def test_run_flag_propagates_a_failing_attempts_exit_code(self):
        out = []
        run_result = subprocess.CompletedProcess(["par"], 2, stdout="", stderr="")
        code = helper.start(
            self.path, cwd=Path("."), run_flag=True,
            par_run=fake_par([WRITE_PERMISSION_MATCH], run_result=run_result), out=out.append,
        )
        self.assertEqual(code, 2)

    def test_approve_failure_refuses_cleanly(self):
        out = []
        code = helper.start(
            self.path, cwd=Path("."), run_flag=False,
            par_run=fake_par([WRITE_PERMISSION_MATCH], approve_fails={1}), out=out.append,
        )
        self.assertEqual(code, 1)
        self.assertTrue(any("没有改动" in line for line in out), out)

    def test_review_required_prints_result_and_diff_and_the_next_commands(self):
        out = []
        run_result = subprocess.CompletedProcess(["par"], 0, stdout="", stderr="")
        code = helper.start(
            self.path, cwd=Path("."), run_flag=True,
            par_run=fake_par(
                [WRITE_PERMISSION_MATCH], run_result=run_result, final_status="REVIEW_REQUIRED",
                result_text="RESULT-TEXT\n", diff_text="DIFF-TEXT\n",
            ),
            out=out.append,
        )
        self.assertEqual(code, 0)
        self.assertTrue(any("RESULT-TEXT" in line for line in out), out)
        self.assertTrue(any("DIFF-TEXT" in line for line in out), out)
        self.assertTrue(any("par task accept fx-0001" in line and "land_task.py fx-0001" in line for line in out), out)

    def test_a_non_review_status_never_fetches_result_or_diff(self):
        out = []
        run_result = subprocess.CompletedProcess(["par"], 1, stdout="", stderr="")
        base_run = fake_par([WRITE_PERMISSION_MATCH], run_result=run_result, final_status="FAILED")

        def guarded(cmd, **kwargs):
            if "result" in cmd or "diff" in cmd:
                raise AssertionError("must not fetch result/diff for a non-REVIEW_REQUIRED status")
            return base_run(cmd, **kwargs)

        code = helper.start(self.path, cwd=Path("."), run_flag=True, par_run=guarded, out=out.append)
        self.assertEqual(code, 1)

    def test_review_required_but_a_failed_result_fetch_degrades_gracefully(self):
        out = []
        run_result = subprocess.CompletedProcess(["par"], 0, stdout="", stderr="")
        code = helper.start(
            self.path, cwd=Path("."), run_flag=True,
            par_run=fake_par(
                [WRITE_PERMISSION_MATCH], run_result=run_result, final_status="REVIEW_REQUIRED", result_fails=True,
            ),
            out=out.append,
        )
        self.assertEqual(code, 0, "the attempt itself still succeeded; only the extra reporting failed")
        self.assertTrue(any("读取 result 失败" in line for line in out), out)

    def test_a_no_patch_diff_does_not_hide_a_successfully_fetched_result(self):
        """The real bug this guards against: an R1/no-change attempt has no patch at all, and
        `par task diff` exits non-zero for that ("this attempt produced no patch") - that must
        not also swallow a result that was read just fine (found via a real par_runtime-0006
        diagnostic probe, not guessed)."""
        out = []
        run_result = subprocess.CompletedProcess(["par"], 0, stdout="", stderr="")
        code = helper.start(
            self.path, cwd=Path("."), run_flag=True,
            par_run=fake_par(
                [WRITE_PERMISSION_MATCH], run_result=run_result, final_status="REVIEW_REQUIRED",
                result_text="REAL-RESULT-TEXT\n", diff_fails=True,
            ),
            out=out.append,
        )
        self.assertEqual(code, 0)
        self.assertTrue(any("REAL-RESULT-TEXT" in line for line in out), out)
        self.assertTrue(any("没有补丁" in line for line in out), out)

    def test_bad_contract_file_refuses_without_calling_par(self):
        bad = Path(self._tmp.name) / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        out = []
        code = helper.start(bad, cwd=Path("."), run_flag=False, par_run=never_called("par"), out=out.append)
        self.assertEqual(code, 1)
        self.assertTrue(any("没有改动" in line for line in out), out)


if __name__ == "__main__":
    unittest.main()
