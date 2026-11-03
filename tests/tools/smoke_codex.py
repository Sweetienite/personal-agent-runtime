#!/usr/bin/env python3
"""A REAL Codex smoke test on a throw-away fixture project. It calls the model: it uses ChatGPT-subscription quota.

    python -B tests/tools/smoke_codex.py --yes-use-quota --model SLUG [--write] [--workdir DIR] [--executable PATH]

What it does, in a private throw-away data directory (never the real one):
  1. writes a provider/profile configuration naming the bundled Codex and the given model;
  2. creates a fixture project (a tiny git repo with one deliberate bug, no personal data);
  3. runs `par health --probe` (offline), then a READ-ONLY question task (one real call);
  4. with --write: a one-line fix task on the fixture (one more real call; write permission is granted for the fixture only);
  5. prints every command's output, the vendor's event-type histogram, and whether anything outside the throw-away folder changed.

Never run by the test suite. Real projects are not involved: the only registered project is the fixture.
"""
import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from par.cli import main as par_main                         # noqa: E402
from par.fixtures import create_fixture                      # noqa: E402
from par.paths import default_data_dir, ensure_data_dir      # noqa: E402
from par.providers import codex as C                         # noqa: E402

PROFILES = '''schema_version = 1
active_profile = "default"

[workers.w_code]
provider = "codex"
model = "{model}"
capabilities = ["read", "edit", "review"]

[profiles.default]
coordinator = "claude_desktop"
default_worker = "w_code"
max_parallel_workers = 1
worker_failure_policy = "ask"
additional_paid_credits = false

[profiles.default.roles.code]
primary = "w_code"
fallback = []
'''
PROVIDERS = 'schema_version = 1\n\n[providers.codex]\nkind = "codex"\nenabled = true\nexecutable = "{exe}"\n'
TIMEOUTS = {"startup": 90, "idle": 180, "total": 600}      # a stuck CLI must not run (and spend quota) for the default 20 minutes'


def listing(path):
    """Names, sizes and mtimes below a folder: enough to notice any change."""
    root = Path(path)
    if not root.exists():
        return "absent"
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        try:
            st = p.lstat()
        except OSError:
            continue
        h.update(f"{p.relative_to(root)}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()


def snapshot(root, limit=300_000):
    """{relative path: (size, mtime_ns)} for every file below ``root``: names and numbers only, never contents."""
    found = {}
    root = Path(root)
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            found[os.path.relpath(os.path.join(dirpath, name), root)] = (st.st_size, st.st_mtime_ns)
            if len(found) >= limit:
                return found
    return found


def par(data, *argv):
    cmd = ["par", *argv, "--data-dir", str(data)]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    code = par_main([*argv, "--data-dir", str(data)])
    print(f"(exit {code})", flush=True)
    return code


def submit(data, work, key, **fields):
    doc = {"schema_version": 1, "project_id": "fixture", "role": "code", "idempotency_key": key, **fields}
    path = work / f"{key}.json"
    path.write_text(json.dumps(doc))
    par(data, "task", "submit", "--contract", str(path))
    return "fixture-" + f"{int(key.split('-')[-1]):04d}"


def histogram(attempt_dir):
    counts = collections.Counter()
    unknown = []
    log = attempt_dir / "stdout.log"
    if not log.is_file():
        return counts, unknown
    for line in log.read_text(errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            counts["<not json>"] += 1
            continue
        kind = obj.get("type", "<no type>")
        item = obj.get("item", {}).get("type") if isinstance(obj.get("item"), dict) else None
        counts[f"{kind}" + (f" / {item}" if item else "")] += 1
    return counts, unknown


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes-use-quota", action="store_true", help="required: acknowledges that this calls the model for real")
    ap.add_argument("--model", help="the model slug to ask Codex for (required for a real run)")
    ap.add_argument("--executable", default=C.BUNDLED_CODEX)
    ap.add_argument("--workdir", help="where to build the throw-away data directory (default: a new temp folder)")
    ap.add_argument("--write", action="store_true", help="also run the one-line-fix task (write permission for the fixture only)")
    ap.add_argument("--skip-read", action="store_true", help="with --write: run only the one-line-fix task (no read-only question)")
    ap.add_argument("--self-test", action="store_true", help="rehearse this script against a FAKE codex program: no model, no quota, no login")
    args = ap.parse_args()
    if args.self_test:
        return self_test(args)
    if not args.yes_use_quota or not args.model:
        print("refusing: this makes REAL model calls. Re-run with --yes-use-quota and --model once you have approved it.", file=sys.stderr)
        return 2
    return smoke(args)


def self_test(args):
    """The same steps against tests/fake_codex_exe.py, so the script itself is proven before it ever spends quota."""
    from unittest import mock
    sys.path.insert(0, str(REPO))
    from tests import codex_samples as S
    from tests.fake_codex import FakeCodex
    from par.fixtures import FIXED
    base = Path(tempfile.mkdtemp(prefix="par-smoke-selftest-"))
    read = {"events": S.ok_run("add(2, 3) returns -1 as written; docs/spec.md says it should return 5.", S.command("cat app/calc.py"))}
    write = {"events": S.ok_run("Changed add() to return a + b.", S.file_change("app/calc.py")), "writes": {"app/calc.py": FIXED}}
    fake = FakeCodex(base / "fake", exec={"sequence": [write] if args.skip_read else [read, write]})
    args.executable, args.model, args.write = str(fake.exe), "gpt-fake-1", True
    args.workdir = args.workdir or str(base / "work")
    with mock.patch.dict(os.environ, {"CODEX_HOME": str(fake.home)}), \
            mock.patch.object(C.CodexAdapter, "signature_problem", return_value=None):
        code = smoke(args, rehearsal=True)
    print(f"\nself-test used a fake codex: {len(fake.exec_calls())} exec call(s), no model, no network")
    return code


def smoke(args, rehearsal=False):
    work = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="par-smoke-codex-"))
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    data = work / "data"
    ensure_data_dir(data)
    real_data = default_data_dir()
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    cfg_file = codex_home / "config.toml"
    digest = lambda: hashlib.sha256(cfg_file.read_bytes()).hexdigest() if cfg_file.is_file() else "absent"
    before_real, before_codex_cfg = listing(real_data), digest()
    before_cfg_lines = cfg_file.read_text(errors="replace").splitlines() if cfg_file.is_file() else []        # kept in memory only
    before_home = snapshot(codex_home)
    cfg = data / "config"
    (cfg / "profiles.toml").write_text(PROFILES.format(model=args.model))
    (cfg / "providers.toml").write_text(PROVIDERS.format(exe=args.executable))
    for f in cfg.glob("*.toml"):
        os.chmod(f, 0o600)
    entry = create_fixture(data, "fixture").entry
    print(f"throw-away data directory: {data}\nfixture project: {entry.path}\nmodel requested: {args.model}\nexecutable: {args.executable}")

    par(data, "health", "--probe")
    results = {}

    if not (args.write and args.skip_read):
        t1 = submit(data, work, "smoke-1", title="Smoke: read-only question", risk="R1", allowed_paths=[],
                    description=("Read app/calc.py. In ONE sentence, say what add(2, 3) returns as the code is written now, and whether that matches "
                                 "docs/spec.md. Do not change any file."),
                    acceptance={"criteria": ["one-sentence answer"], "checks": []}, timeouts=TIMEOUTS)
        results[t1] = par(data, "task", "run", t1)
        par(data, "task", "result", t1)
        par(data, "task", "status", t1)

    if args.write:
        t2 = submit(data, work, "smoke-2" if not args.skip_read else "smoke-1", title="Smoke: fix add()", risk="R2", allowed_paths=["app/"],
                    description="add() in app/calc.py returns the wrong value. Fix it so that `python3 -m unittest -q` passes. Change only app/calc.py.",
                    acceptance={"criteria": ["unit tests pass"],
                                "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q"], "timeout": 120, "expect_exit": 0}]},
                    timeouts=TIMEOUTS)
        par(data, "task", "approve", t2, "--note", "fixture project only (smoke test approved by the user)")
        results[t2] = par(data, "task", "run", t2)
        par(data, "task", "result", t2)
        par(data, "task", "diff", t2)
        par(data, "task", "status", t2)

    print("\n=== vendor event histogram (from each attempt's stdout.log) ===")
    for task_id in results:
        counts, _ = histogram(data / "tasks" / task_id / "attempts" / "1")
        print(f"{task_id}: " + ", ".join(f"{k} x{n}" for k, n in sorted(counts.items())))
    print("\n=== did anything outside the throw-away folder change? ===")
    git = subprocess.run(["git", "-C", str(entry.path), "status", "--porcelain"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(work)}).stdout
    print("fixture working tree:", "clean (nothing applied)" if not git.strip() else git.strip())
    print("real data directory unchanged:", listing(real_data) == before_real)
    print("Codex config.toml unchanged:", digest() == before_codex_cfg)
    if digest() != before_codex_cfg:
        now_lines = cfg_file.read_text(errors="replace").splitlines()
        known = set(before_cfg_lines)
        added = [l for l in now_lines if l not in known and l.strip()]
        print(f"  !! the CLI itself edited its own config.toml: {len(added)} new line(s); section headers among them: "
              + (", ".join(l for l in added if l.startswith("[")) or "none"))
    after_home = snapshot(codex_home)
    added = sorted(set(after_home) - set(before_home))
    removed = sorted(set(before_home) - set(after_home))
    changed = sorted(k for k in set(before_home) & set(after_home) if before_home[k] != after_home[k])
    print(f"\n=== what the CLI kept in its own home ({codex_home}): names only, never contents ===")
    print(f"added {len(added)}, removed {len(removed)}, modified {len(changed)}")
    for label, names in (("added", added), ("removed", removed), ("modified", changed)):
        for name in names[:25]:
            print(f"  {label:<9}{name}")
        if len(names) > 25:
            print(f"  {label:<9}... and {len(names) - 25} more")
    print(f"\nevidence stays in {work} (private; delete it when you no longer need it)")
    return 0 if all(code == 0 for code in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
