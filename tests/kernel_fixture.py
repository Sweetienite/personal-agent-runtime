"""A miniature world for Kernel tests: a fixture git project with Harness files, scripted workers, a real ledger."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from par.config import ProjectEntry
from par.core.clock import Clock, FakeClock
from par.core.kernel import Kernel
from par.core.settings import KernelSettings
from par.core.states import ACTIVE, TaskStatus as T
from par.execution import subprocesses as sp
from par.paths import ensure_data_dir
from par.providers.base import ProviderEvent
from par.providers.fake import ScenarioSequence, ScriptedProvider, ops, scenarios
from par.providers.registry import AdapterRegistry
from tests import SRC
from tests.helpers import tree_digest
from tests.world import GOOD_STATE, git_env, make_harness_project, sample_config, sample_contract

FIXED_CALC = "def add(a, b):\n    return a + b\n"
BUGGY_CALC = "def add(a, b):\n    return a - b\n"
TEST_CALC = ("import unittest\nfrom app.calc import add\n\n\nclass T(unittest.TestCase):\n"
             "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
FIX = {"app/calc.py": FIXED_CALC}


def fast_settings(**over):
    base = dict(heartbeat_interval=0.1, stale_after=1.5, grace=0.5, poll=0.02, cancel_check=0.05,
                transient_retry_delay=5.0, check_grace=0.5, git_timeout=60.0)
    base.update(over)
    return KernelSettings(**base)


def r1_contract(**over):
    """A read-only analysis task (R1 unless ``risk`` says otherwise)."""
    doc = dict(risk="R1", allowed_paths=[], acceptance={"criteria": ["summarise"], "checks": []}, role="code")
    doc.update(over)
    return sample_contract(**doc)


class Disobedient(ScriptedProvider):
    """A Worker that ignores the read-only request (a real CLI might)."""

    def build_argv(self, request):
        return [a for a in super().build_argv(request) if a != "--read-only"]


class KernelWorld(unittest.TestCase):
    policy = "ask"
    default_files = FIX            # what the scripted Worker writes unless a test says otherwise (None: read-only tasks)
    fake_clock = False
    codex_enabled = True
    agy_enabled = True
    register_adapters = True
    parallel = 1                   # the profile's max_parallel_workers (1 or 2)
    approval_mode = "coordinator_autonomous"

    # ---- fixture project ------------------------------------------------------------------------------
    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=git_env(self.home)).stdout.decode().strip()

    def make_project(self, name, *, contract=True, git=True, extra=None, state=GOOD_STATE):
        path = self.base / "projects" / name
        make_harness_project(path, contracts=("TASK-1.md",) if contract else (), state=state)
        files = {"app/__init__.py": "", "app/calc.py": BUGGY_CALC, "tests/__init__.py": "", "tests/test_calc.py": TEST_CALC,
                 "README.md": "# fixture\n", ".gitignore": "build/\n*.log\n", "docs/spec.md": "# spec\n", **(extra or {})}
        for rel, content in files.items():
            f = path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        if git:
            self.git(path.parent, "init", "-q", "-b", "main", str(path))
            self.git(path, "add", "-A")
            self.git(path, "commit", "-q", "-m", "init")
        return path

    def commit_in_project(self, path, rel, content, message="change"):
        f = Path(path) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
        self.git(path, "add", "-A")
        self.git(path, "commit", "-q", "-m", message)

    # ---- set-up / tear-down ----------------------------------------------------------------------------
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        self.home.mkdir()
        self.data = self.base / "data"
        ensure_data_dir(self.data, protected=())
        self.clock = FakeClock() if self.fake_clock else Clock()
        self.project_path = self.make_project("fixture")
        self.projects = {"fixture": ProjectEntry("fixture", self.project_path, "git", True)}
        self.codex = ScenarioSequence(scenarios.success(self.default_files))
        self.agy = ScenarioSequence(scenarios.success(self.default_files))
        self.settings = fast_settings()
        self.kernels = []
        self.skip_invariants = False
        self.children = []
        self._n = 0
        self.kernel = self.new_kernel()

    def registry(self):
        reg = AdapterRegistry()
        if self.register_adapters:
            reg.register("codex", self.codex.factory("codex"))
            reg.register("antigravity", self.agy.factory("antigravity"))
        return reg

    def config(self):
        return sample_config(policy=self.policy, codex=self.codex_enabled, agy=self.agy_enabled, parallel=self.parallel,
                             approval_mode=self.approval_mode)

    def new_kernel(self, **kw):
        kw.setdefault("config", self.config())
        kw.setdefault("projects", self.projects)
        kw.setdefault("adapters", self.registry())
        kw.setdefault("clock", self.clock)
        kw.setdefault("home", self.home)
        kw.setdefault("settings", self.settings)
        kernel = Kernel(self.data, **kw)
        self.kernels.append(kernel)
        return kernel

    def spawn_kernel(self, task_id, *scenario_list, policy=None, settings=None):
        """Start a Kernel in a separate process that runs ``task_id`` with the given scripted scenarios."""
        args = {"data": str(self.data), "project": str(self.project_path), "home": str(self.home), "task_id": task_id,
                "policy": policy or self.policy, "settings": settings or {},
                "scenarios": [{"name": s.name, "steps": list(s.steps), "on_sigterm": s.on_sigterm} for s in scenario_list]}
        root = str(Path(__file__).resolve().parents[1])
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), root]), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen([sys.executable, "-B", "-m", "tests.kernel_child", json.dumps(args)], cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(proc)
        self.assertEqual(proc.stdout.readline().strip(), "ready", proc.stderr.read() if proc.poll() is not None else "")
        return proc

    def crash(self, proc):
        """SIGKILL a child Kernel: no cleanup, no goodbye."""
        proc.kill()
        proc.wait()

    def tearDown(self):
        for proc in self.children:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass
        try:
            if not self.skip_invariants:
                self.assert_invariants()
        finally:
            for kernel in self.kernels:
                try:
                    for a in kernel.store.unfinished_attempts():
                        if a["pgid"]:
                            sp._signal_group(a["pgid"], signal.SIGKILL)
                except Exception:
                    pass
            for pid_file in self.data.rglob("stray.pid"):
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except (OSError, ValueError):
                    pass
            for kernel in self.kernels:
                kernel.close()
            self._tmp.cleanup()

    # ---- helpers -----------------------------------------------------------------------------------------
    def submit(self, contract=None, *, key=None, by="user", **over):
        """Submit a contract (default: the R2 sample) with a fresh idempotency key unless ``key`` is given."""
        doc = dict(contract if contract is not None else sample_contract())
        doc.update(over)
        self._n += 1
        doc["idempotency_key"] = key or f"key-{self._n}"
        return self.kernel.submit(doc, submitted_by=by)

    def queued(self, contract=None, **over):
        """Submit and approve whatever is needed so the task is QUEUED."""
        res = self.submit(contract, **over)
        for a in self.kernel.describe(res.task_id)["pending_approvals"]:
            self.kernel.approve(res.task_id, a["id"])
        self.assertEqual(self.kernel.describe(res.task_id)["status"], "QUEUED")
        return res.task_id

    def status(self, task_id, kernel=None):
        return (kernel or self.kernel).describe(task_id)["status"]

    def run_task(self, task_id):
        return self.kernel.run(task_id)

    def statuses(self, task_id):
        return [e["to_status"] for e in self.kernel.events(task_id) if e["type"] == "STATUS_CHANGED"]

    def event_types(self, task_id):
        return [e["type"] for e in self.kernel.events(task_id)]

    def attempt_dir(self, task_id, n=1):
        return self.data / "tasks" / task_id / "attempts" / str(n)

    def result(self, task_id, attempt=None):
        return self.kernel.result(task_id, attempt)

    def in_thread(self, fn):
        box = {}

        def target():
            try:
                box["value"] = fn()
            except BaseException as exc:                       # surfaced to the test that joins
                box["error"] = exc
        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.box = box
        return t

    def wait_for(self, predicate, timeout=15.0, interval=0.02):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    # ---- invariants: checked after EVERY test ---------------------------------------------------------------
    def assert_invariants(self):
        store = self.kernels[0].store
        from par.schema import check
        self.assertTrue(store.integrity_ok())
        unfinished = store.unfinished_attempts()
        locks = {l["project_id"]: l for l in store.locks()}
        held = {(a["task_id"], a["n"]) for a in unfinished}
        for lock in locks.values():
            self.assertIn((lock["task_id"], lock["attempt_n"]), held, f"lock without a live attempt: {lock}")
        for a in unfinished:
            task = store.get_task(a["task_id"])
            self.assertIn(task["project_id"], locks, f"unfinished attempt without its project lock: {a['task_id']}#{a['n']}")
        events = store.events()
        self.assertEqual([e["id"] for e in events], list(range(1, len(events) + 1)))
        for e in events:
            self.assertEqual(check(e, "event"), [], e)
        for task in store.list_tasks():
            tid = task["task_id"]
            chain = [e for e in events if e["task_id"] == tid and e["type"] == "STATUS_CHANGED"]
            prev = None
            for e in chain:
                if prev is not None:
                    self.assertEqual(e["from_status"], prev, f"{tid}: broken status chain at event {e['id']}")
                prev = e["to_status"]
            if chain:
                self.assertEqual(chain[-1]["to_status"], task["status"], f"{tid}: row and event log disagree")
            attempts = store.attempts(tid)
            self.assertEqual([a["n"] for a in attempts], list(range(1, task["attempt_count"] + 1)), f"{tid}: attempt numbering")
            live = [a for a in attempts if a["state"] != "ENDED"]
            if T(task["status"]) in ACTIVE:
                self.assertTrue(live, f"{tid} is {task['status']} but has no live attempt")
            else:
                self.assertFalse([a for a in live if not self.skip_orphans], f"{tid} is {task['status']} but has a live attempt")
            for a in attempts:
                if a["state"] == "ENDED" and a["result"] is not None and not getattr(self, "allow_bad_envelope", False):
                    self.assertEqual(check(a["result"], "result"), [], f"{tid}#{a['n']}")
        self.assert_private_layout()

    skip_orphans = False

    def assert_private_layout(self):
        """Everything the Kernel keeps outside the Workers' working copies is private to the user."""
        skip = {"workspace", "ref", "verify"}
        for dirpath, dirnames, filenames in os.walk(self.data):
            rel = Path(dirpath).relative_to(self.data)
            if skip & set(rel.parts):
                dirnames[:] = []
                continue
            for name in dirnames + filenames:
                if name in skip:
                    continue
                if os.path.islink(Path(dirpath) / name):        # a link's own mode means nothing (the Antigravity adapter links the keychains)
                    continue
                mode = os.lstat(Path(dirpath) / name).st_mode & 0o777
                self.assertEqual(mode & 0o077, 0, f"{rel / name} is accessible to others ({mode:o})")
            for name in list(dirnames):
                if name in skip:
                    mode = os.lstat(Path(dirpath) / name).st_mode & 0o777
                    self.assertEqual(mode & 0o077, 0, f"{rel / name} is accessible to others ({mode:o})")
                    dirnames.remove(name)

    def project_digest(self):
        return tree_digest(self.project_path)
