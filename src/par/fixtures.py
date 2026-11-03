"""Fixture projects: tiny throw-away Harness projects the Runtime makes for itself.

A real Worker can only be exercised end to end on a project it may write to, and no real project is writable
before P8. A fixture is the answer: a small git repository with Harness files and one deliberate bug, created
under ``<data dir>/fixtures/<id>/`` with a marker file, holding no personal data. It is the only kind of project
whose registry entry may say ``write_enabled = true`` (see ``config.fixture_problem``), so a write task can
never reach anything the user cares about.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from par.config import FIXTURE_MARKER, FIXTURES_DIR, ID_PATTERN, ProjectEntry
from par.core.policy import PolicyViolation, WriteGuard, protected_roots
from par.execution.gitops import Git, GitError, resolve_commit
from par.paths import inspect_data_dir
from par.registry import add_project, load_registry

MAX_ID = 32
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
FILES = {
    "AGENTS.md": (
        "# Fixture project\n\n"
        "This is a throw-away project made by `par fixture create` to exercise the Personal Agent Runtime.\n"
        "It holds no real data. Rules for any agent working here:\n\n"
        "- Change only what the task asks for, and only under the paths the task allows.\n"
        "- Do not read, print or copy anything outside this folder.\n"
        "- Check your work with `python3 -m unittest -q`.\n"),
    "CLAUDE.md": "@AGENTS.md\n",
    ".ai/STATE.md": (
        "# Current State\n\n## Baseline\n\n- A fixture project for the Personal Agent Runtime. `app/calc.py` has a deliberate bug in `add()`.\n\n"
        "## Current Focus\n\n- Exercise Worker integration: a read-only question and a one-line fix.\n\n"
        "## Sources of Truth\n\n- Spec: `docs/spec.md`\n\n## Next\n\n1. Run a task.\n"),
    ".ai/tasks/active/FIXTURE-1.md": "# FIXTURE-1\n\nGoal: exercise the Runtime's Worker integration.\nNon-goals: anything outside this folder.\n",
    ".ai/tasks/archive/.gitkeep": "",
    "app/__init__.py": "",
    "app/calc.py": BUGGY,
    "tests/__init__.py": "",
    "tests/test_calc.py": (
        "import unittest\n\nfrom app.calc import add\n\n\nclass AddTests(unittest.TestCase):\n"
        "    def test_adds_two_numbers(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
        "    def test_adds_negative_numbers(self):\n        self.assertEqual(add(-1, -2), -3)\n\n\n"
        "if __name__ == \"__main__\":\n    unittest.main()\n"),
    "docs/spec.md": "# Spec\n\n`add(a, b)` returns the sum of `a` and `b`.\n",
    "README.md": "# Fixture project\n\nA tiny calculator used to test the Personal Agent Runtime.\n",
    ".gitignore": "__pycache__/\n*.pyc\nbuild/\n",
}


@dataclass(frozen=True)
class FixtureInfo:
    entry: ProjectEntry
    commit: str


def fixtures_root(data_dir: "str | os.PathLike[str]") -> Path:
    return Path(data_dir) / FIXTURES_DIR


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise PolicyViolation(f"{path.name} is a symlink")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        if not path.is_dir():
            raise PolicyViolation(f"{path.name} exists and is not a folder") from None
    os.chmod(path, 0o700)


def create_fixture(data_dir: "str | os.PathLike[str]", project_id: str = "fixture",
                   home: "str | os.PathLike[str] | None" = None) -> FixtureInfo:
    """Make and register a fixture project. Raises ``PolicyViolation`` with a plain reason when it must not."""
    data = Path(data_dir)
    home_p = Path(home) if home is not None else Path.home()
    if not ID_PATTERN.match(project_id) or len(project_id) > MAX_ID:
        raise PolicyViolation(f"the fixture id must match [a-z][a-z0-9_]* and have at most {MAX_ID} characters")
    if not inspect_data_dir(data).usable:
        raise PolicyViolation("the data directory is missing or unsafe; run `par init` first")
    guard = WriteGuard(allowed_roots=(data,), protected=protected_roots(home_p))
    root = fixtures_root(data)
    guard.check(root / project_id)
    if project_id in load_registry(data, home_p).projects:
        raise PolicyViolation(f"project id {project_id!r} is already registered")
    _private_dir(root)
    target = root / project_id
    if os.path.lexists(target):
        raise PolicyViolation(f"{target.name} already exists in the fixtures folder; choose another id")

    with tempfile.TemporaryDirectory(prefix=".creating-", dir=root) as scratch_name:
        scratch = Path(scratch_name)
        build = scratch / project_id
        build.mkdir(mode=0o700)
        for rel, text in FILES.items():
            f = build / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        marker = {"schema_version": 1, "kind": "par-fixture", "id": project_id, "created_by": "par fixture create",
                  "note": "A throw-away project for testing the Personal Agent Runtime. It holds no real data and is the only kind of project the Runtime may write to before P8."}
        (build / FIXTURE_MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        try:
            git = Git(scratch / "git-home")
            (scratch / "git-home").mkdir(mode=0o700)
            git.run(build, "init", "--quiet", "--initial-branch=main")
            git.run(build, "add", "-A")
            git.run(build, "commit", "--quiet", "--no-gpg-sign", "-m", "fixture: initial commit")
            commit = resolve_commit(git, build)
        except GitError as exc:
            raise PolicyViolation(f"could not create the fixture repository: {exc}") from None
        os.rename(build, target)                                   # same folder, so the move is atomic

    try:
        entry = add_project(data, project_id, target, vcs="git", home=home_p, fixture=True)
    except PolicyViolation as exc:
        raise PolicyViolation(f"the fixture folder {target.name} was created but not registered ({exc}); remove it yourself or register it by hand") from None
    return FixtureInfo(entry, commit)
