"""Shared test fixtures: a miniature 'Documents' folder with a Core, Harness projects and traps."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from par.config import HarnessSettings
from par.harness import check as hc
from par.paths import APP_DIR_NAME, ensure_data_dir

BLOCK = "<!-- AI_HARNESS_CORE_BEGIN -->\n# Agent Entry\n- rule\n<!-- AI_HARNESS_CORE_END -->\n"
SECRET_TITLE = "Secret Research Title"
FAKE_HYGIENE = '''#!/usr/bin/env python3
import pathlib, sys
root = pathlib.Path(sys.argv[1])
lines = (root / ".hyg").read_text().splitlines() if (root / ".hyg").is_file() else []
bad = any(l.split(":")[0] in ("WARN", "REVIEW") for l in lines)
print(f"project: {root}")
if not bad:
    print("OK: no hygiene warnings.")
for l in lines:
    print(l)
sys.exit(1 if bad else 0)
'''
FAKE_SYNC = '''#!/usr/bin/env python3
import pathlib, sys
core = pathlib.Path(__file__).resolve().parent.parent
for p in sorted(core.parent.iterdir()):
    a = p / "AGENTS.md"
    if p.is_dir() and p != core and a.is_file() and "<!-- AI_HARNESS_CORE_BEGIN -->" in a.read_text():
        print(f"OK: {p} already matches canonical block")
f = core / ".sync_exit"
sys.exit(int(f.read_text()) if f.is_file() else 0)
'''
HYG_A = (
    "WARN: STATE.md is large: 20000 bytes (about 8000 tokens)\n"
    f"REVIEW: STATE.md has 2 section(s) outside the Core list: {SECRET_TITLE} A, {SECRET_TITLE} B (ignore if the project documents the exception)\n"
    "WARN: nested STATE.md files present: 1\n"
    "REVIEW: nested STATE: private/path/.ai/STATE.md\n"
    "INFO: AGENTS.md has no Core managed block (not covered by sync_runtime_core.py)\n"
)


class World(unittest.TestCase):
    """A miniature 'Documents' folder: a Core, two Harness projects and the traps around them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.home = self.base / "home"
        (self.home / "Library" / "Application Support").mkdir(parents=True)
        self.docs = self.base / "home" / "Documents"
        self.docs.mkdir()
        self.core = self.docs / "AI_Harness_Core"
        self.a = self.docs / "proj_a"
        self.b = self.docs / "proj_b"
        self.data = self.home / "Library" / "Application Support" / APP_DIR_NAME
        self.env = git_env(self.home)
        self.build_core()
        self.build_project(self.a, managed=True, git=True, hyg=HYG_A)
        self.build_project(self.b, managed=False, git=False)
        # traps that discovery must ignore
        for trap in (self.a / "reports" / "snap", self.docs / "node_modules" / "x", self.docs / ".hidden" / "y",
                     self.docs / "d1" / "d2" / "d3" / "d4" / "d5" / "too_deep"):
            (trap / ".ai").mkdir(parents=True)
            (trap / ".ai" / "STATE.md").write_text("# nested\n")
        outside = self.base / "outside_world"
        (outside / ".ai").mkdir(parents=True)
        (outside / ".ai" / "STATE.md").write_text("# outside\n")
        os.symlink(outside, self.docs / "linked")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=self.env)

    def build_core(self):
        (self.core / "scripts").mkdir(parents=True)
        (self.core / "templates" / "runtime").mkdir(parents=True)
        (self.core / "CORE_MANIFEST.json").write_text('{"name": "AI_Harness_Core", "version": "1.3.0"}\n')
        (self.core / "README.md").write_text("# AI Harness Core\n\n**Version:** 1.3.0\n")
        (self.core / "CHANGELOG.md").write_text("# Changelog\n\n## 1.3.0 — 2026-10-14\n\n- x\n")
        (self.core / "templates" / "runtime" / "AGENTS.md").write_text(BLOCK + "\n## Project-specific rules\n")
        (self.core / "scripts" / "harness_hygiene.py").write_text(FAKE_HYGIENE)
        (self.core / "scripts" / "sync_runtime_core.py").write_text(FAKE_SYNC)
        self.git(self.core.parent, "init", "-q", "-b", "main", str(self.core))
        self.git(self.core, "add", "-A")
        self.git(self.core, "commit", "-q", "-m", "core")

    def build_project(self, path, managed, git, hyg=""):
        (path / ".ai" / "tasks" / "active").mkdir(parents=True)
        (path / ".ai" / "tasks" / "archive").mkdir()
        (path / ".ai" / "tasks" / "active" / "TASK-1.md").write_text("task")
        (path / "AGENTS.md").write_text((BLOCK if managed else "# own rules\n") + "\n## Project-specific rules\n")
        (path / "CLAUDE.md").write_text("@AGENTS.md\n")
        (path / ".ai" / "STATE.md").write_text(f"# Current State\n\n## {SECRET_TITLE}\n\n- fact\n")
        if hyg:
            (path / ".hyg").write_text(hyg)
        if git:
            self.git(path.parent, "init", "-q", "-b", "main", str(path))
            self.git(path, "add", "-A")
            self.git(path, "commit", "-q", "-m", "init")
            (path / "runtime" / "accounts").mkdir(parents=True)
            (path / "runtime" / "accounts" / "x.json").write_text("{}")            # untracked + sensitive-looking
            (path / ".ai" / "STATE.md").write_text(f"# Current State\n\n## {SECRET_TITLE}\n\n- changed\n")  # modified

    def settings(self, **kw):
        return HarnessSettings(kw.get("core", self.core), kw.get("root", self.docs), kw.get("depth", 4))

    def check(self, **kw):
        return hc.run_check(self.settings(**kw), home=self.home)

    def init_data(self):
        ensure_data_dir(self.data, protected=())


def git_env(home):
    """A git environment that never reads the real user's configuration."""
    return {"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def run_git(cwd, home, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=git_env(home))


GOOD_STATE = (
    "# Current State\n\n## Baseline\n\n- accepted\n\n## Current Focus\n\n- work\n\n"
    "## Sources of Truth\n\n- Spec: `docs/spec.md`\n\n## Next\n\n1. go\n"
)


def make_harness_project(path, *, agents="# own rules\n", state=GOOD_STATE, claude="@AGENTS.md\n",
                         contracts=("TASK-1.md",), archive=0, extra_dirs=()):
    """A minimal Harness project folder (no git)."""
    path = Path(path)
    (path / ".ai" / "tasks" / "active").mkdir(parents=True)
    (path / ".ai" / "tasks" / "archive").mkdir()
    for d in extra_dirs:
        (path / ".ai" / "tasks" / d).mkdir()
    for name in contracts:
        (path / ".ai" / "tasks" / "active" / name).write_text(f"# {name}\nGoal: x\n")
    for i in range(archive):
        (path / ".ai" / "tasks" / "archive" / f"OLD-{i}.md").write_text("done\n")
    (path / "AGENTS.md").write_text(agents)
    (path / ".ai" / "STATE.md").write_text(state)
    if claude is not None:
        (path / "CLAUDE.md").write_text(claude)
    return path


def sample_contract(**over):
    """A valid task contract (R2 write task) for the schema and Kernel tests."""
    doc = {
        "schema_version": 1, "project_id": "fixture", "title": "Fix add()", "description": "Make add() return the sum.",
        "role": "code", "worker": None, "risk": "R2", "allowed_paths": ["app/"],
        "acceptance": {"criteria": ["tests pass"],
                       "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q"], "timeout": 60, "expect_exit": 0}]},
        "dependencies": [], "ad_hoc": False, "idempotency_key": "fix-add-1",
    }
    doc.update(over)
    return doc


def sample_result(**over):
    doc = {
        "schema_version": 1, "task_id": "fixture-0001", "attempt": 1, "provider": "codex", "worker": "w_code",
        "model_requested": "m-code", "model_observed": None, "terminal_state": "SUCCESS", "outcome": "completed",
        "failure_class": None, "stage": None, "summary": "done (worker's own statement)", "summary_verified": False,
        "evidence": ["attempts/1/validation.json"], "diff_artifact": "attempts/1/work.patch",
        "diff_stat": {"files": 1, "additions": 2, "deletions": 1},
        "tests": [{"name": "unit", "status": "PASS", "exit_code": 0, "duration_s": 0.2, "log": "attempts/1/check_unit.log"}],
        "boundary": {"violations": 0, "sensitive": 0, "paths": []}, "remaining_issues": [], "next_action": "review",
        "handoff_usable": True, "partial": False, "started_at": "2026-10-14T03:00:00.000Z", "ended_at": "2026-10-14T03:00:05.000Z",
    }
    doc.update(over)
    return doc


def sample_config(*, policy="ask", codex=True, agy=True, flash_caps=("read", "edit"), code_model="m-code", parallel=1,
                  coordinator="claude_desktop", approval_mode="coordinator_autonomous"):
    """A validated RuntimeConfig: role 'code' = w_code (codex) then w_flash (antigravity); role 'draft' = w_flash."""
    from par.config import build_config
    profiles = {
        "schema_version": 1, "active_profile": "default",
        "workers": {
            "w_code": {"provider": "codex", "model": code_model, "capabilities": ["read", "edit", "review"]},
            "w_flash": {"provider": "antigravity", "model": "m-flash", "capabilities": list(flash_caps)},
        },
        "profiles": {"default": {
            "coordinator": coordinator, "default_worker": "w_code", "max_parallel_workers": parallel,
            "worker_failure_policy": policy, "additional_paid_credits": False, "approval_mode": approval_mode,
            "roles": {"code": {"primary": "w_code", "fallback": ["w_flash"]}, "draft": {"primary": "w_flash", "fallback": []}},
        }},
    }
    providers = {"schema_version": 1, "providers": {"codex": {"enabled": codex}, "antigravity": {"enabled": agy}}}
    result = build_config(profiles, providers)
    assert result.ok, result.errors
    return result.config
