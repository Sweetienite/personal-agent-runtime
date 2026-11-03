"""A fake Antigravity install for tests: a launcher script, a folder with the fake's configuration and call log, and an empty
Keychains folder that the adapter links to instead of the user's own keychains.

    fake = FakeAgy(root)                        # root: a temp folder the test owns
    fake.configure(run={"events": [...]})       # what a worker run does (see tests/fake_agy_exe.py)
    adapter = fake.adapter()                    # a real AntigravityAdapter pointed at the fake program
    fake.runs()                                 # what the adapter actually asked for
"""
import json
import os
import shlex
import sys
from pathlib import Path

from par.providers import antigravity as A

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}


class FakeAgy:
    def __init__(self, root, **config):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.keychains = self.root / "keychains"
        self.keychains.mkdir(exist_ok=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        self.exe = bin_dir / "agy"
        code = f"import sys; sys.dont_write_bytecode = True; sys.path.insert(0, {str(ROOT)!r}); from tests.fake_agy_exe import main; main()"
        # a shell wrapper, not a `#!python` line: the repository path contains spaces, which a shebang cannot hold
        self.exe.write_text(f"#!/bin/sh\nFAKE_AGY_ROOT={shlex.quote(str(self.root))}\nexport FAKE_AGY_ROOT\n"
                            f"exec {shlex.quote(sys.executable)} -B -c {shlex.quote(code)} \"$@\"\n")
        os.chmod(self.exe, 0o755)
        self.config = {}
        self.configure(**config)
        A.forget_caches()

    def configure(self, **kw):
        self.config.update(kw)
        (self.root / "fake.json").write_text(json.dumps(self.config))
        A.forget_caches()

    def adapter(self, *, environ=None, **kw):
        env = dict(BASE_ENV if environ is None else environ)
        return A.AntigravityAdapter(str(self.exe), environ=env, verify_signature=False, keychains_from=self.keychains, **kw)

    def calls(self):
        log = self.root / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def runs(self):
        return [c for c in self.calls() if c["kind"] == "run"]

    def offline_calls(self):
        return [c for c in self.calls() if c["kind"] != "run"]
