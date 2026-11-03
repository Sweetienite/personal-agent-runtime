"""A fake Codex install for tests: a launcher script, a private CODEX_HOME and a scenario file the fake reads.

    fake = FakeCodex(root)                      # root: a temp folder the test owns
    fake.configure(exec={"events": [...]})      # what `codex exec` does (see tests/fake_codex_exe.py)
    adapter = fake.adapter()                    # a real CodexAdapter pointed at the fake program
    fake.exec_calls()                           # what the adapter actually asked for
"""
import json
import os
import shlex
import sys
from pathlib import Path

from par.providers import codex as C

ROOT = Path(__file__).resolve().parents[1]
BASE_ENV = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}


class FakeCodex:
    def __init__(self, root, **config):
        self.root = Path(root)
        self.home = self.root / "codex_home"
        self.home.mkdir(parents=True, exist_ok=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        self.exe = bin_dir / "codex"
        code = f"import sys; sys.dont_write_bytecode = True; sys.path.insert(0, {str(ROOT)!r}); from tests.fake_codex_exe import main; main()"
        # a shell wrapper, not a `#!python` line: the repository path contains spaces, which a shebang cannot hold
        self.exe.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -B -c {shlex.quote(code)} \"$@\"\n")
        os.chmod(self.exe, 0o755)
        self.config = {}
        self.configure(**config)
        C.forget_caches()

    def configure(self, **kw):
        self.config.update(kw)
        (self.home / "fake.json").write_text(json.dumps(self.config))
        C.forget_caches()

    def adapter(self, *, environ=None, **kw):
        env = dict(BASE_ENV if environ is None else environ)
        return C.CodexAdapter(str(self.exe), codex_home=str(self.home), environ=env, verify_signature=False, **kw)

    def calls(self):
        log = self.home / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def exec_calls(self):
        return [c for c in self.calls() if C_rest(c["argv"])[:1] == ["exec"]]

    def offline_calls(self):
        return [c for c in self.calls() if C_rest(c["argv"])[:1] != ["exec"]]


def C_rest(argv):
    from tests.fake_codex_exe import split_flags
    return split_flags(argv)[2]
