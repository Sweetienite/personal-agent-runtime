"""A fake GitHub Copilot CLI for tests: a launcher script, a private COPILOT_HOME and a scenario file.

    fake = FakeCopilot(root)
    fake.configure(events=[...], exit=0)
    adapter = fake.adapter()
    fake.calls()          # argv, cwd, environment and stdin the adapter used
"""
import json
import os
import shlex
import sys
from pathlib import Path

from par.providers import copilot as C

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}


class FakeCopilot:
    def __init__(self, root, **config):
        self.root = Path(root)
        self.home = self.root / "copilot_home"
        self.home.mkdir(parents=True, exist_ok=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        self.exe = bin_dir / "copilot"
        code = (f"import sys; sys.dont_write_bytecode = True; sys.path.insert(0, {str(ROOT)!r}); "
                "from tests.fake_copilot_exe import main; sys.exit(main())")
        # a shell wrapper, not a `#!python` line: the repository path may contain spaces
        self.exe.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -B -c {shlex.quote(code)} \"$@\"\n")
        os.chmod(self.exe, 0o755)
        self.config = {}
        self.configure(**config)
        C._HELP_CACHE.clear()

    def configure(self, **kw):
        self.config.update(kw)
        (self.home / "fake.json").write_text(json.dumps(self.config))
        C._HELP_CACHE.clear()

    def adapter(self, *, environ=None, **kw):
        env = dict(BASE_ENV if environ is None else environ)
        return C.CopilotAdapter(str(self.exe), copilot_home=str(self.home), environ=env, **kw)

    def calls(self):
        log = self.home / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def prompt_calls(self):
        return [c for c in self.calls() if c.get("kind") == "prompt"]

    def offline_calls(self):
        return [c for c in self.calls() if c.get("kind") != "prompt"]
