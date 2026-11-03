#!/usr/bin/env python3
"""A visible run of the P2 story: a fixture project, scripted workers (no model, no network), a private data directory.

    python -B tests/tools/demo_p2.py

Everything happens in a temporary folder that is removed at the end. It shows, in order: a read-only task, the
write-permission gate, a Worker whose claim of passing tests is not believed, a retry that works, a quota
failure with an offered (not taken) switch, and a Kernel that is killed mid-run and then recovered.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from par.cli_task import render_event, render_result, render_status                        # noqa: E402
from par.config import ProjectEntry                                                         # noqa: E402
from par.core.kernel import Kernel                                                          # noqa: E402
from par.core.settings import KernelSettings                                                # noqa: E402
from par.paths import ensure_data_dir                                                       # noqa: E402
from par.providers.fake import ScenarioSequence, ops, scenarios                             # noqa: E402
from par.providers.registry import AdapterRegistry                                          # noqa: E402
from tests.helpers import tree_digest                                                       # noqa: E402
from tests.world import git_env, make_harness_project, sample_config, sample_contract       # noqa: E402

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
WRONG = "def add(a, b):\n    return a * b\n"
TEST = ("import unittest\nfrom app.calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")


def say(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def git(path, home, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, env=git_env(home)).stdout.decode().strip()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(os.path.realpath(tmp))
        home, data = base / "home", base / "data"
        home.mkdir()
        ensure_data_dir(data, protected=())
        proj = base / "fixture"
        make_harness_project(proj)
        for rel, text in {"app/__init__.py": "", "app/calc.py": BUGGY, "tests/__init__.py": "", "tests/test_calc.py": TEST,
                          ".gitignore": "build/\n", "docs/spec.md": "# spec\n"}.items():
            (proj / rel).parent.mkdir(parents=True, exist_ok=True)
            (proj / rel).write_text(text)
        git(proj.parent, home, "init", "-q", "-b", "main", str(proj))
        git(proj, home, "add", "-A")
        git(proj, home, "commit", "-q", "-m", "init")
        before = tree_digest(proj)

        settings = KernelSettings(heartbeat_interval=0.1, stale_after=1.2, grace=0.5, poll=0.02, cancel_check=0.05)
        codex, agy = ScenarioSequence(scenarios.success()), ScenarioSequence(scenarios.success({"app/calc.py": FIXED}))
        reg = AdapterRegistry()
        reg.register("codex", codex.factory("codex"))
        reg.register("antigravity", agy.factory("antigravity"))
        kernel = Kernel(data, config=sample_config(), projects={"fixture": ProjectEntry("fixture", proj, "git", True)},
                        adapters=reg, home=home, settings=settings)

        def contract(**over):
            doc = sample_contract(**over)
            doc["idempotency_key"] = over.get("idempotency_key") or f"demo-{time.monotonic_ns()}"
            return doc

        say("1. A read-only task: submit, run, review, accept")
        r1 = dict(risk="R1", allowed_paths=[], acceptance={"criteria": ["explain add()"], "checks": []}, role="code", title="Explain add()")
        tid = kernel.submit(contract(**r1)).task_id
        print(render_status(kernel.describe(tid)))
        print(f"\n-> run: {kernel.run(tid).status}")
        print(render_result(kernel.result(tid)))
        kernel.accept(tid)
        print(f"-> accept: {kernel.describe(tid)['status']} (a person accepted the result; nothing was applied to the project)")

        say("2. A file-changing task needs your write permission first")
        res = kernel.submit(contract())
        write = res.task_id
        print(render_status(kernel.describe(write)))
        try:
            kernel.run(write)
        except Exception as exc:
            print(f"\n-> run before approval: refused ({type(exc).__name__}: {exc})")
        kernel.approve(write, note="demo")
        print(f"-> approve: {kernel.describe(write)['status']}")

        say("3. The Worker says 'tests pass' - the Kernel runs the check itself")
        codex.reset(scenarios.success({"app/calc.py": WRONG}, message="all done", claim_tests_pass=True))
        print(f"-> run: {kernel.run(write).status}")
        print(render_result(kernel.result(write)))

        say("4. Retry (a NEW attempt from the base), this time with a correct fix")
        codex.reset(scenarios.success({"app/calc.py": FIXED}))
        kernel.retry(write)
        print(f"-> run: {kernel.run(write).status}")
        print(render_result(kernel.result(write)))
        print("\nthe patch (not applied anywhere):\n" + kernel.diff(write).decode())
        print("-> accept blockers:", kernel.accept_blockers(write) or "none")
        kernel.accept(write)
        print(f"-> accept: {kernel.describe(write)['status']}")
        print(f"-> the real project is untouched: {tree_digest(proj) == before}")

        say("5. The first worker runs out of quota: a switch is OFFERED, never taken on its own")
        codex.reset(scenarios.quota_exhausted())
        agy.reset(scenarios.success())
        q = kernel.submit(contract(**r1)).task_id
        print(f"-> run: {kernel.run(q).status}")
        print(render_status(kernel.describe(q)))
        print(f"-> second worker has been called {agy.calls} time(s) so far")
        kernel.approve(q)
        print(f"-> approve the switch, run again: {kernel.run(q).status} on {kernel.result(q)['worker']} ({kernel.result(q)['provider']})")

        say("6. The Kernel is killed in the middle of a run (like a crash), then recovered")
        print(f"-> (codex was flagged QUOTA_EXHAUSTED in step 5; pretend the quota reset: cleared {kernel.clear_health('codex')} health record)")
        child_tid = kernel.submit(contract()).task_id
        kernel.approve(child_tid)
        scenario = scenarios.custom("slow", ops.write("app/calc.py", FIXED), ops.message("halfway"), ops.sleep(60))
        args = {"data": str(data), "project": str(proj), "home": str(home), "task_id": child_tid, "policy": "ask",
                "settings": {"stale_after": 1.2}, "scenarios": [{"name": scenario.name, "steps": list(scenario.steps)}]}
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT)]), PYTHONDONTWRITEBYTECODE="1")
        child = subprocess.Popen([sys.executable, "-B", "-m", "tests.kernel_child", json.dumps(args)], cwd=ROOT, env=env,
                                 stdout=subprocess.PIPE, text=True)
        child.stdout.readline()
        deadline = time.monotonic() + 30
        while kernel.describe(child_tid)["status"] != "RUNNING":
            if time.monotonic() > deadline or child.poll() is not None:
                print(f"!! the second Kernel never reached RUNNING (task is {kernel.describe(child_tid)['status']})")
                return 1
            time.sleep(0.05)
        time.sleep(0.5)
        att = kernel.store.get_attempt(child_tid, 1)
        print(f"-> a second Kernel process is running {child_tid} (worker pid {att['pid']}); now: kill -9 the Kernel")
        child.kill()
        child.wait()
        print(f"-> the ledger still says: {kernel.describe(child_tid)['status']}, project lock held: {kernel.describe(child_tid)['lock'] is not None}")
        time.sleep(settings.stale_after + 0.3)
        print("-> recover (no --terminate):")
        for a in kernel.recover():
            print(f"   {a.task_id}#{a.attempt}  {a.action}  {a.detail}")
        print("-> recover --terminate (the leader's start time matches the record, so it is safe to stop):")
        for a in kernel.recover(child_tid, terminate=True):
            print(f"   {a.task_id}#{a.attempt}  {a.action}  {a.detail}")
        print(render_status(kernel.describe(child_tid)))
        res = kernel.result(child_tid)
        print(f"-> partial work kept: {res['partial']}, usable by a successor: {res['handoff_usable']}")

        say("7. The event log of the file-changing task")
        for e in kernel.events(write):
            print(render_event(e))

        say("8. Summary")
        print(f"tasks: {len(kernel.list_tasks())}; the project folder is unchanged: {tree_digest(proj) == before}")
        print(f"ledger integrity_check: {kernel.store.integrity_ok()}; open project locks: {len(kernel.store.locks())}")
        print(f"real model calls: 0 (workers are scripted; adapters registered: {', '.join(reg.ids())})")
        kernel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
