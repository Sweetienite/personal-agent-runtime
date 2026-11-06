"""Offline, read-only evidence collector for the P3 provider compatibility research (T13).

It NEVER calls a model, never authenticates, never reads credentials, never opens a network
connection, and never installs anything. By default it only resolves executables on PATH.

    python -B tests/tools/provider_probe.py                 # platform + which(), no external process
    python -B tests/tools/provider_probe.py --versions     # also run `<cli> --version` (offline, no model call)
    python -B tests/tools/provider_probe.py --help-flags   # also inspect `<cli> --help` for headless flags

The output is deliberately labelled "static": the existence of a flag is NOT evidence that a
task can be run, that an account is authorised, or that isolation holds. See
docs/COMPATIBILITY.md for what was and was not tested.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from typing import Any

CANDIDATES = ("codex", "gemini", "agy", "claude", "opencode")
HEADLESS_FLAGS = ("--prompt", "--output-format", "stream-json", "headless", "-p", "--approval-mode",
                  "--permission-mode", "--sandbox", "--policy", "--acp", "--experimental-acp", "run", "serve")
VERSION_TIMEOUT = 15
HELP_TIMEOUT = 20


def probe(*, versions: bool = False, help_flags: bool = False, runner: Any = None) -> dict[str, Any]:
    """Collect static facts. ``runner`` is injectable so tests never touch the real machine."""
    run = runner or subprocess.run
    out: dict[str, Any] = {
        "kind": "provider_probe", "schema": 1,
        "evidence_level": "STATIC_OFFLINE",
        "model_calls": 0, "network_calls": 0, "credential_reads": 0, "installs": 0,
        "platform": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine(), "python": platform.python_version()},
        "candidates": {},
        "not_tested": [
            "any real R1/R2 task or model call", "login / account type / quota / billing state",
            "process cancellation, timeouts and orphan cleanup", "rate-limit / auth / permission error classification",
            "sensitive-data boundary and cross-project reads", "structured-event parsing against a live run",
            "OS sandbox scope (Seatbelt / bwrap / Windows)", "terms-of-service clearance for this Runtime",
        ],
    }
    for name in CANDIDATES:
        path = shutil.which(name)
        row: dict[str, Any] = {"on_path": bool(path), "path": path}
        if versions and path:
            try:
                p = run([name, "--version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT)
                row["version_output"] = ((p.stdout or "") + (p.stderr or "")).strip()[:300]
                row["version_returncode"] = p.returncode
            except Exception as exc:                      # a missing/slow CLI is a fact, not an error
                row["version_output"] = f"probe failed: {type(exc).__name__}"
        if help_flags and path:
            try:
                p = run([name, "--help"], capture_output=True, text=True, timeout=HELP_TIMEOUT)
                text = ((p.stdout or "") + (p.stderr or "")).lower()
                row["headless_flags_seen"] = sorted({f for f in HEADLESS_FLAGS if f.lower() in text})
            except Exception as exc:
                row["headless_flags_seen"] = []
                row["help_error"] = type(exc).__name__
        out["candidates"][name] = row
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline provider capability probe (never calls a model)")
    ap.add_argument("--versions", action="store_true", help="also run <cli> --version (offline)")
    ap.add_argument("--help-flags", action="store_true", help="also inspect <cli> --help for headless flags")
    args = ap.parse_args()
    print(json.dumps(probe(versions=args.versions, help_flags=args.help_flags), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
