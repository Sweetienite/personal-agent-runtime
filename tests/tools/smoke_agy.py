#!/usr/bin/env python3
"""A REAL Antigravity smoke test on a throw-away fixture project. It calls the model: it uses your Antigravity plan's quota.

    python -B tests/tools/smoke_agy.py --yes-use-quota --model SLUG --executable /absolute/path/to/agy
                                       [--try-write-readonly] [--write] [--write-strategy rules|accept-edits] [--skip-read] [--workdir DIR]

You must already be signed in (`par health --probe` says so). With --precheck-only it stops after the ZERO-QUOTA pre-check below.
What it does, in a private throw-away data directory (never the real one):
  0. --precheck: the real stream-json invocation with a model name that does not exist, so the CLI must reject it BEFORE any model is
     called: it shows whether the invocation form works (flags accepted, stdin protocol, exit code, error shape) at no cost;
  1. writes a provider/profile configuration naming the given agy and model;
  2. creates a fixture project (a tiny git repo with one deliberate bug, no personal data);
  3. runs `par health --probe` (no model call), then a READ-ONLY question task (one real call);
  4. with --try-write-readonly: a second read-only task that ASKS the model to create a file - to see how the CLI soft-denies it and
     what it prints (the fixture must stay untouched);
  5. with --write: a one-line fix task on the fixture (one more real call; write permission is granted for the fixture only);
  6. prints every command's output, the vendor's event/step histogram, the `init` event's tool list and permission mode, what the CLI
     wrote into its private HOME (names only), and whether anything outside the throw-away folder changed - in particular the
     user's ~/.gemini (names, sizes, modification times; never contents).

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
from par.providers import antigravity as A                   # noqa: E402

PROFILES = '''schema_version = 1
active_profile = "default"

[workers.w_agy]
provider = "antigravity"
model = "{model}"
capabilities = ["read", "edit"]

[profiles.default]
coordinator = "claude_desktop"
default_worker = "w_agy"
max_parallel_workers = 1
worker_failure_policy = "ask"
additional_paid_credits = false

[profiles.default.roles.analysis]
primary = "w_agy"
fallback = []

[profiles.default.roles.code]
primary = "w_agy"
fallback = []
'''
PROVIDERS = 'schema_version = 1\n\n[providers.antigravity]\nkind = "antigravity"\nenabled = true\nexecutable = "{exe}"\n'
TIMEOUTS = {"startup": 120, "idle": 240, "total": 900}     # a stuck CLI must not run (and spend quota) for the default 20 minutes
GEMINI = Path.home() / ".gemini"


def snapshot(root, limit=300_000):
    """{relative path: (size, mtime_ns)} for every entry below ``root``: names and numbers only, never contents; links are not followed."""
    found = {}
    root = Path(root)
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            found[os.path.relpath(path, root)] = (st.st_size, st.st_mtime_ns)
            if len(found) >= limit:
                return found
    return found


def listing(path):
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


def par(data, *argv):
    cmd = ["par", *argv, "--data-dir", str(data)]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    code = par_main([*argv, "--data-dir", str(data)])
    print(f"(exit {code})", flush=True)
    return code


def submit(data, work, key, **fields):
    doc = {"schema_version": 1, "project_id": "fixture", "idempotency_key": key, **fields}
    path = work / f"{key}.json"
    path.write_text(json.dumps(doc))
    par(data, "task", "submit", "--contract", str(path))
    return "fixture-" + f"{int(key.split('-')[-1]):04d}"


def read_stream(attempt_dir):
    """Every parsed line of the vendor stream in an attempt's stdout.log (errors are counted, never raised)."""
    objs, bad = [], 0
    log = attempt_dir / "stdout.log"
    if log.is_file():
        for line in log.read_text(errors="replace").splitlines():
            try:
                objs.append(json.loads(line))
            except ValueError:
                bad += 1
    return objs, bad


def describe_stream(attempt_dir):
    objs, bad = read_stream(attempt_dir)
    counts = collections.Counter()
    tools = collections.Counter()
    for obj in objs:
        kind = obj.get("event", "<no event>") if isinstance(obj, dict) else "<not an object>"
        step = obj.get("step_update", {}) if isinstance(obj, dict) and isinstance(obj.get("step_update"), dict) else {}
        label = kind + (f" / {step.get('step_type')} {step.get('state')}" if step else "")
        counts[label] += 1
        if step.get("tool_name"):
            tools[step["tool_name"]] += 1
    print("  events: " + (", ".join(f"{k} x{n}" for k, n in sorted(counts.items())) or "none") + (f"; {bad} line(s) not JSON" if bad else ""))
    if tools:
        print("  tools used: " + ", ".join(f"{k} x{n}" for k, n in sorted(tools.items())))
    for obj in objs:
        if isinstance(obj, dict) and obj.get("event") == "init" and isinstance(obj.get("init"), dict):
            init = obj["init"]
            print(f"  init: permission_mode={init.get('permission_mode')!r} model={init.get('model')!r} cwd={init.get('cwd')!r}")
            print(f"  init.tools ({len(init.get('tools') or [])}): {', '.join(str(t) for t in (init.get('tools') or [])[:60])}")
            break
    for obj in objs:
        if isinstance(obj, dict) and obj.get("event") == "result" and isinstance(obj.get("result"), dict):
            res = obj["result"]
            print(f"  result: status={res.get('status')!r} num_turns={res.get('num_turns')} usage={res.get('usage')} error={str(res.get('error'))[:200]!r}")
    err = attempt_dir / "stderr.log"
    if err.is_file() and err.stat().st_size:
        lines = err.read_text(errors="replace").splitlines()
        print(f"  stderr.log ({len(lines)} line(s), already redacted by the Kernel's log; first 15):")
        for line in lines[:15]:
            print("    | " + line[:220])


def tree(root, depth=3, limit=60):
    """Names and sizes under the attempt's private HOME: what the CLI kept there (never contents)."""
    root = Path(root)
    if not root.exists():
        print(f"  {root}: absent")
        return
    shown = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) >= depth:
            dirnames[:] = []
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            kind = "link -> " + os.readlink(path) if path.is_symlink() else ("dir" if path.is_dir() else f"{path.stat().st_size} bytes")
            print(f"  {rel / name}  ({kind})")
            shown += 1
            if shown >= limit:
                print("  ...")
                return


def permission_evidence(attempt_dir):
    """What decided the permission questions in an attempt: the settings.json the CLI left in the private HOME (it keeps only the keys that differ
    from its defaults, so a `permissions` key surviving shows the rules were understood) and the vendor log's own lines about settings and
    confirmations. Only matching lines are shown, with the attempt folder abbreviated."""
    cli = attempt_dir / "home" / ".gemini" / "antigravity-cli"
    spellings = sorted({str(attempt_dir), os.path.realpath(attempt_dir)}, key=len, reverse=True)      # the CLI prints the real path

    def short(text):
        for spelling in spellings:
            text = text.replace(spelling, "<attempt>")
        return A.redact_text(text)

    settings = cli / "settings.json"
    if settings.is_file():
        print("  settings.json as the CLI left it: " + short(" ".join(settings.read_text(errors="replace").split())))
    else:
        print("  settings.json: absent")
    logs = sorted((cli / "log").glob("cli-*.log")) if (cli / "log").is_dir() else []
    wanted = ("CLI settings initialized", "tool confirmation", "ApplyProjectPermissionGrants", "permission", "denied", "allow")
    lines = []
    for log in logs[-1:]:
        for line in log.read_text(errors="replace").splitlines():
            if any(w in line for w in wanted) and "Sonar" not in line and "hook" not in line:
                lines.append(line)
    for line in lines[:14]:
        print("  log | " + short(line)[:330])
    if not lines:
        print("  (no vendor log lines about permissions)")


def precheck(exe):
    """The adapter's own argv/stdin/home layout, a model that does not exist: the CLI has to refuse it before any model is called."""
    adapter = A.AntigravityAdapter(exe)
    base = Path(tempfile.mkdtemp(prefix="par-agy-precheck-"))
    os.chmod(base, 0o700)
    home, cwd = base / "home", base / "cwd"
    home.mkdir(mode=0o700)
    cwd.mkdir(mode=0o700)
    A.write_home(home, allow_write=False)
    request = A.RunRequest("precheck", 1, "This is a zero-quota check of the invocation form; no answer is expected.", cwd, "par-precheck-no-such-model")
    argv = adapter.build_argv(request)
    env = A._env(str(home), {k: v for k, v in os.environ.items() if k in A.PASSTHROUGH})
    print("\n=== zero-quota pre-check: the real invocation form with a model that does not exist ===")
    print("argv:", " ".join(argv[1:]))
    try:
        proc = subprocess.run(argv, input=adapter.stdin_data(request), env=env, cwd=cwd, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        print("!! the CLI did not finish within 90 s")
        return False
    out, err = proc.stdout.decode(errors="replace"), proc.stderr.decode(errors="replace")
    print(f"exit {proc.returncode}; stdout {len(out.splitlines())} line(s); stderr {len(err.splitlines())} line(s)")
    events = []
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            print("  (not JSON) " + A.redact_text(line)[:200])
    for ev in events:
        name = ev.get("event") if isinstance(ev, dict) else None
        body = ev.get(name) if isinstance(ev, dict) and isinstance(ev.get(name), dict) else {}
        print(f"  event {name!r}: " + ", ".join(f"{k}={A.redact_text(str(v))[:100]!r}" for k, v in body.items() if k in ("status", "error", "permission_mode", "model", "conversation_id")))
    for line in err.splitlines()[:12]:
        print("  stderr | " + A.redact_text(line)[:200])
    results = [e["result"] for e in events if isinstance(e, dict) and isinstance(e.get("result"), dict)]
    ok = proc.returncode == 1 and bool(results) and results[-1].get("status") == "ERROR" and "not recognized" in str(results[-1].get("error", ""))
    print("pre-check", "PASSED: the CLI refused the unknown model before calling any model" if ok else "did NOT behave as the documentation says - read the lines above")
    print(f"(scratch kept: {base})")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes-use-quota", action="store_true", help="required: acknowledges that this calls the model for real")
    ap.add_argument("--model", help="the model slug to ask agy for (see `agy models`; required for a real run)")
    ap.add_argument("--executable", help="absolute path of the signed agy binary (required for a real run)")
    ap.add_argument("--workdir", help="where to build the throw-away data directory (default: a new temp folder)")
    ap.add_argument("--try-write-readonly", action="store_true", help="also run a read-only task that asks the model to create a file")
    ap.add_argument("--write", action="store_true", help="also run the one-line-fix task (write permission for the fixture only)")
    ap.add_argument("--write-strategy", choices=("rules", "accept-edits"), default="rules",
                    help="how the file-changing task may edit files: write_file allow-rules for the contract's paths (default) or the CLI's own --mode accept-edits")
    ap.add_argument("--skip-read", action="store_true", help="skip the plain read-only question")
    ap.add_argument("--precheck", action="store_true", help="first run the zero-quota pre-check (the invocation form with a model that does not exist)")
    ap.add_argument("--precheck-only", action="store_true", help="run only the zero-quota pre-check and stop (no --yes-use-quota needed)")
    ap.add_argument("--self-test", action="store_true", help="rehearse this script against a FAKE agy program: no model, no quota, no login")
    args = ap.parse_args()
    if args.self_test:
        return self_test(args)
    if args.precheck_only:
        if not args.executable or not os.path.isabs(args.executable):
            print("refusing: --executable must be an absolute path", file=sys.stderr)
            return 2
        return 0 if precheck(args.executable) else 1
    if not args.yes_use_quota or not args.model or not args.executable:
        print("refusing: this makes REAL model calls. Re-run with --yes-use-quota, --model and --executable once you have approved it.", file=sys.stderr)
        return 2
    if not os.path.isabs(args.executable):
        print("refusing: --executable must be an absolute path", file=sys.stderr)
        return 2
    return smoke(args)


def self_test(args):
    """The same steps against tests/fake_agy_exe.py, so the script itself is proven before it ever spends quota."""
    from unittest import mock
    sys.path.insert(0, str(REPO))
    from tests import agy_samples as S
    from tests.fake_agy import FakeAgy
    from par.fixtures import FIXED
    base = Path(tempfile.mkdtemp(prefix="par-smoke-agy-selftest-"))
    model = "gemini-3.8-flash-medium"

    def stream(text, *middle):
        return [{"$init": True}, S.user_step(), *middle, S.delta(text, "DONE"), S.checkpoint(), S.result(text)]

    read = {"events": stream("add(2, 3) returns -1 as written; docs/spec.md says it should return 5.", S.tool("view_file", params={"AbsolutePath": "app/calc.py"}))}
    denied = {"events": stream("I could not create the file."), "writes": {"NEW_FILE.txt": "hello\n"}}
    write = {"events": stream("Changed add() to return a + b.", S.tool("write_to_file", params={"TargetFile": "app/calc.py"})), "writes": {"app/calc.py": FIXED}}
    # the first (empty) scenario is consumed by the zero-quota pre-check call, which the fake counts as a run
    steps = [{}] + ([] if args.skip_read else [read]) + ([denied] if args.try_write_readonly else []) + [write]
    fake = FakeAgy(base / "fake", models=[model], run={"sequence": steps})
    args.executable, args.model, args.write, args.precheck = str(fake.exe), model, True, True
    args.workdir = args.workdir or str(base / "work")
    (base / "real_home" / "Library" / "Keychains").mkdir(parents=True)
    with mock.patch.object(A.AntigravityAdapter, "signature_problem", return_value=None), mock.patch.object(A, "real_home", return_value=base / "real_home"):
        code = smoke(args, rehearsal=True)
    print(f"\nself-test used a fake agy: {len(fake.runs())} run call(s), {len(fake.offline_calls())} offline call(s), no model, no network")
    return code


def smoke(args, rehearsal=False):
    work = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="par-smoke-agy-"))
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    data = work / "data"
    ensure_data_dir(data)
    real_data = default_data_dir()
    before_real, before_gemini = listing(real_data), snapshot(GEMINI)
    os.environ["PAR_AGY_WRITE_STRATEGY"] = getattr(args, "write_strategy", "rules")      # read by the adapter each attempt (never reaches the child)
    cfg = data / "config"
    (cfg / "profiles.toml").write_text(PROFILES.format(model=args.model))
    (cfg / "providers.toml").write_text(PROVIDERS.format(exe=args.executable))
    for f in cfg.glob("*.toml"):
        os.chmod(f, 0o600)
    entry = create_fixture(data, "fixture").entry
    print(f"throw-away data directory: {data}\nfixture project: {entry.path}\nmodel requested: {args.model}\nexecutable: {args.executable}\n"
          f"write strategy: {os.environ['PAR_AGY_WRITE_STRATEGY']}")

    if args.precheck and not precheck(args.executable):
        print("stopping: the pre-check did not pass, so no model call is made")
        return 1
    par(data, "health", "--probe")
    results, n = {}, 0

    def task(role, title, description, risk, allowed, checks):
        nonlocal n
        n += 1
        key = f"smoke-{n}"
        tid = submit(data, work, key, role=role, title=title, risk=risk, allowed_paths=allowed, description=description,
                     acceptance={"criteria": ["done"], "checks": checks}, timeouts=TIMEOUTS)
        return tid, n

    if not args.skip_read:
        t, k = task("analysis", "Smoke: read-only question",
                    "Read app/calc.py. In ONE sentence, say what add(2, 3) returns as the code is written now, and whether that matches docs/spec.md. Do not change any file.",
                    "R1", [], [])
        results[t] = par(data, "task", "run", t)
        par(data, "task", "result", t)
        par(data, "task", "status", t)

    if args.try_write_readonly:
        t, k = task("analysis", "Smoke: asked to write in a read-only task",
                    "Create a new file named NEW_FILE.txt in the project root containing the single word hello. Then say whether you were able to.",
                    "R1", [], [])
        results[t] = par(data, "task", "run", t)
        par(data, "task", "result", t)

    if args.write:
        t, k = task("code", "Smoke: fix add()",
                    "add() in app/calc.py returns the wrong value. Fix it so that `python3 -m unittest -q` passes. Change only app/calc.py.",
                    "R2", ["app/"], [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q"], "timeout": 120, "expect_exit": 0}])
        par(data, "task", "approve", t, "--note", "fixture project only (smoke test approved by the user)")
        results[t] = par(data, "task", "run", t)
        par(data, "task", "result", t)
        par(data, "task", "diff", t)
        par(data, "task", "status", t)

    print("\n=== what the vendor stream looked like (from each attempt's stdout.log / stderr.log) ===")
    for task_id in results:
        print(f"{task_id}:")
        describe_stream(data / "tasks" / task_id / "attempts" / "1")
    print("\n=== what the CLI kept in each attempt's private HOME (names and sizes only) ===")
    for task_id in results:
        print(f"{task_id}:")
        tree(data / "tasks" / task_id / "attempts" / "1" / "home")
    print("\n=== what decided the permission questions (settings the CLI kept, vendor log lines) ===")
    for task_id in results:
        print(f"{task_id}:")
        permission_evidence(data / "tasks" / task_id / "attempts" / "1")
    print("\n=== did anything outside the throw-away folder change? ===")
    git = subprocess.run(["git", "-C", str(entry.path), "status", "--porcelain"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(work)}).stdout
    print("fixture working tree:", "clean (nothing applied)" if not git.strip() else git.strip())
    print("real data directory unchanged:", listing(real_data) == before_real)
    after = snapshot(GEMINI)
    added, removed = sorted(set(after) - set(before_gemini)), sorted(set(before_gemini) - set(after))
    changed = sorted(k for k in set(after) & set(before_gemini) if after[k] != before_gemini[k])
    print(f"~/.gemini ({GEMINI}): added {len(added)}, removed {len(removed)}, modified {len(changed)}   [names, sizes and mtimes only]")
    for label, names in (("added", added), ("removed", removed), ("modified", changed)):
        for name in names[:25]:
            print(f"  {label:<9}{name}")
        if len(names) > 25:
            print(f"  {label:<9}... and {len(names) - 25} more")
    if changed and not (added or removed):
        print("  (the open Antigravity app may touch its own files here: compare the names above with what agy is known to write)")
    print(f"\nevidence stays in {work} (private; delete it when you no longer need it)")
    return 0 if all(code == 0 for code in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
