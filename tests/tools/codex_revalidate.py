#!/usr/bin/env python3
"""One-command re-run of docs/CODEX.md section 12 on a host that permits nested sandboxes.

    python -B tests/tools/codex_revalidate.py --check-only
    python -B tests/tools/codex_revalidate.py --yes-use-quota --model gpt-6-luna --out /tmp/par-reval

Steps: offline real-CLI checks, trust entries, then (only with --yes-use-quota) the real R1/R2
smoke, which itself runs `par health --probe`. Every step's output is written to <out>/*.log.

IMPORTANT: this script NEVER changes the adapter's sandbox mode and never passes
`--dangerously-bypass-approvals-and-sandbox`. PAR's default `-s read-only|workspace-write` needs a
nested Seatbelt sandbox. On a host that is ALREADY externally sandboxed, that nesting is refused
with EPERM and this script reports it (exit 2) instead of "fixing" the run with a privilege
shortcut. See docs/reports/CODEX_REVALIDATION_PART2_20261106.md for the scoped run that is possible
on such a host, and docs/CODEX.md section 14 for the terms and limits.

Never run by the default test suite. No credentials are read or copied.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.tools.sandbox_probe import probe_sandbox                     # noqa: E402

PY = sys.executable
OFFLINE = [PY, "-B", "-m", "unittest", "tests.integration.test_real_codex.RealCodexOffline", "-v"]
TRUST = [PY, "-B", "-m", "unittest", "tests.integration.test_real_codex.RealCodexTrustEntries", "-v"]


def smoke(model: str, out: Path) -> "list[str]":
    return [PY, "-B", "tests/tools/smoke_codex.py", "--yes-use-quota", "--model", model,
            "--write", "--workdir", str(out / "work")]


def default_runner(argv: "list[str]", env_extra: "dict[str, str]") -> "tuple[int, str]":
    env = {**os.environ, **env_extra}
    env.pop("PYTHONPATH", None)                      # Agent hosts may inject a sitecustomize
    try:
        p = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, timeout=900)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:                          # a broken step is a recorded fact, not a crash
        return 1, f"step failed to start: {type(exc).__name__}: {exc}"


def revalidate(*, check_only: bool, yes_use_quota: bool, model: str, out: Path,
               runner: Any = None, probe: Any = None) -> "tuple[int, dict[str, Any]]":
    run = runner or default_runner
    facts = (probe or probe_sandbox)()
    out.mkdir(parents=True, exist_ok=True)
    (out / "sandbox_probe.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True))
    record: dict[str, Any] = {
        "kind": "codex_revalidation", "schema": 1,
        "nested_sandbox_available": facts.get("nested_sandbox_available"),
        "inherited_sandbox_check": facts.get("inherited_sandbox_check"),
        "steps": [], "real_model_calls": 0,
        "not_tested": ["real cancellation/timeout/quota samples (controllable fake CLI samples only)"],
    }
    plan = [("offline", OFFLINE, {"PAR_REAL_CODEX": "1"}),
            ("trust", TRUST, {"PAR_REAL_CODEX_TRUST": "1"})]

    if not check_only:
        if not yes_use_quota or not model:
            return 2, {**record, "refused": "real model calls need --yes-use-quota and --model"}
        if facts.get("nested_sandbox_available") is False:
            return 2, {**record, "refused": (
                "this host cannot create a nested Seatbelt sandbox; PAR's default sandbox mode "
                "would fail with sandbox_apply EPERM. Use a normal terminal. This script will not "
                "bypass the CLI sandbox to make the run look green.")}
        plan.append(("real_smoke", smoke(model, out), {}))
        record["real_model_calls"] = 2            # one R1 + one R2, as smoke_codex.py documents

    for name, argv, extra in plan:
        rc, text = run(argv, extra)
        (out / f"{name}.log").write_text(text, encoding="utf-8")
        record["steps"].append({"name": name, "rc": rc, "log": str(out / f"{name}.log")})

    (out / "summary.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return (0 if all(s["rc"] == 0 for s in record["steps"]) else 1), record


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-run CODEX.md section 12 (never bypasses the CLI sandbox)")
    ap.add_argument("--check-only", action="store_true", help="offline checks only; no model call, no quota")
    ap.add_argument("--yes-use-quota", action="store_true", help="acknowledge real model calls (R1 + R2)")
    ap.add_argument("--model", default="", help="model slug, e.g. gpt-6-luna")
    ap.add_argument("--out", default="", help="evidence directory (default: a new temp folder)")
    args = ap.parse_args()
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="par-codex-revalidate-"))
    code, record = revalidate(check_only=args.check_only, yes_use_quota=args.yes_use_quota,
                              model=args.model, out=out)
    print(json.dumps({"exit": code, **record}, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"\nevidence: {out}")
    if record.get("refused"):
        print(f"REFUSED: {record['refused']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
