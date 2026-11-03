#!/usr/bin/env python3
"""Run the full unit/integration test suite with one module per subprocess, in parallel.

    python -B tests/tools/parallel_tests.py              # parallel, DEFAULT_WORKERS at a time
    python -B tests/tools/parallel_tests.py --workers=1   # serial (debugging, or a deterministic log)
    python -B tests/tools/parallel_tests.py test_kernel_run test_concurrency   # only these modules

`python -B -m unittest discover -s tests -t .` is still the canonical, single-process way to run
the suite (CI, a from-scratch check) and stays the documented command for that. This script is a
faster day-to-day alternative for the same test modules: each `test_*.py` under tests/unit/ and
tests/integration/ already runs correctly on its own (that is what `unittest discover` does to it
too, one module at a time, just serially) and tests spawn their own real subprocesses already, so
running N modules at once in separate OS processes changes nothing about what each one checks -
it only overlaps wall-clock time that was otherwise spent waiting on unrelated modules' Kernel
timeout/recovery scenarios, which real subprocess timing can not be sped up without weakening what
they prove. Mirrors `mutation_check.py`'s own ProcessPoolExecutor pattern.
"""
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_WORKERS = min(8, os.cpu_count() or 4)
_SUMMARY = re.compile(r"Ran (\d+) tests? in [\d.]+s\s*\n+(OK|FAILED)(?:\s*\(([^)]*)\))?\s*$")


def discover_modules() -> "list[str]":
    mods = []
    for base in ("unit", "integration"):
        root = REPO / "tests" / base
        for f in sorted(root.glob("test_*.py")):
            mods.append(f"tests.{base}.{f.stem}")
    return mods


def run_module(module: str) -> "tuple[str, int, str, float]":
    t0 = time.monotonic()
    cp = subprocess.run([sys.executable, "-B", "-m", "unittest", module], cwd=REPO,
                        capture_output=True, text=True, timeout=300)
    return module, cp.returncode, cp.stdout + cp.stderr, time.monotonic() - t0


def _parse_counts(output: str) -> "tuple[int, int, int, int]":
    """(ran, failures, errors, skipped) parsed from unittest's own trailing summary; (0, 0, 1, 0) if
    unparseable (counted as one error so a module whose output does not match the expected shape is
    never silently OK)."""
    m = _SUMMARY.search(output.strip())
    if not m:
        return 0, 0, 1, 0
    ran = int(m.group(1))
    tail = m.group(3) or ""
    failures = int(fm.group(1)) if (fm := re.search(r"failures=(\d+)", tail)) else 0
    errors = int(em.group(1)) if (em := re.search(r"errors=(\d+)", tail)) else 0
    skipped = int(sm.group(1)) if (sm := re.search(r"skipped=(\d+)", tail)) else 0
    return ran, failures, errors, skipped


def _parse_workers(argv: "list[str]") -> int:
    for a in argv:
        if a.startswith("--workers="):
            return max(1, int(a.split("=", 1)[1]))
    return DEFAULT_WORKERS


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--workers=")]
    workers = _parse_workers(sys.argv[1:])
    modules = discover_modules()
    if args:
        modules = [m for m in modules if m.rsplit(".", 1)[-1] in args]
        if not modules:
            print(f"no module matches: {args}")
            return 2
    t0 = time.monotonic()
    total_ran = total_failures = total_errors = total_skipped = 0
    failed_modules: "list[tuple[str, str]]" = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_module, m): m for m in modules}
        for fut in as_completed(futures):
            module, code, output, dt = fut.result()
            ran, failures, errors, skipped = _parse_counts(output)
            total_ran += ran
            total_failures += failures
            total_errors += errors
            total_skipped += skipped
            ok = code == 0 and failures == 0 and errors == 0
            print(f"{'ok  ' if ok else 'FAIL'}  {dt:6.2f}s  {ran:4} tests  {module}", flush=True)
            if not ok:
                failed_modules.append((module, output))
    elapsed = time.monotonic() - t0
    print(f"\n{total_ran} tests across {len(modules)} modules in {elapsed:.1f}s "
          f"(failures={total_failures}, errors={total_errors}, skipped={total_skipped})")
    for module, output in failed_modules:
        print(f"\n===== {module} =====\n{output}")
    return 1 if failed_modules else 0


if __name__ == "__main__":
    raise SystemExit(main())
