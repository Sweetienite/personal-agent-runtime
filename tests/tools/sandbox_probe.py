"""Offline diagnostic: can this shell enter a nested OS sandbox?

Codex uses macOS Seatbelt (``sandbox-exec`` / ``sandbox_init``) for its ``read-only`` and
``workspace-write`` modes. Some agent hosts already run commands inside a sandbox; macOS then
refuses ``sandbox_apply`` with EPERM, the CLI cannot read or write ANY file, and a real R2
looks like a Worker failure instead of an environment limitation. This tool separates the two.

It NEVER calls a model, never authenticates, never reads credentials, never opens a network
connection and never installs anything. It only asks the OS whether a trivial, allow-all
nested sandbox can be entered.

    python -B tests/tools/sandbox_probe.py            # human-readable
    python -B tests/tools/sandbox_probe.py --json     # machine-readable

See docs/reports/CODEX_REAL_REVALIDATION_20261106.md for the run that motivated it.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PROFILE = "(version 1)\n(allow default)\n"
MARKER = "par-sandbox-ok"
SANDBOX_TIMEOUT = 10
SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def inherited_sandbox_check(*, system: "str | None" = None) -> "int | None":
    """Raw ``sandbox_check(getpid(), NULL, 0)`` value on macOS, or ``None`` elsewhere/unavailable.

    By the usual convention for this private API, 1 means the calling process already inherits a
    Seatbelt sandbox. In that case a vendor CLI cannot stack its own sandbox and fails with
    ``sandbox_apply: Operation not permitted`` (EPERM) -- an inherited *host* property, not a
    defect in the CLI or in PAR. We report the raw value rather than asserting what it means.
    """
    if (system or platform.system()) != "Darwin":
        return None
    try:
        import ctypes
        import os

        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        fn = lib.sandbox_check
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        fn.restype = ctypes.c_int
        return int(fn(os.getpid(), None, 0))
    except Exception:                                  # private API may be unavailable in future macOS
        return None


def probe_sandbox(*, runner: Any = None, system: "str | None" = None) -> dict[str, Any]:
    """Return an evidence record. ``runner``/``system`` are injectable so tests never touch the OS."""
    run = runner or subprocess.run
    sysname = system or platform.system()
    out: dict[str, Any] = {
        "kind": "sandbox_probe", "schema": 1,
        "evidence_level": "OS_OFFLINE",
        "model_calls": 0, "network_calls": 0, "credential_reads": 0, "installs": 0,
        "system": sysname,
        "nested_sandbox_available": None,
        "inherited_sandbox_check": inherited_sandbox_check(system=sysname),
        "detail": "",
        "not_tested": [
            "any real R1/R2 task or model call", "login / account type / quota / billing state",
            "Codex's own sandbox profile scope", "Linux bwrap / seccomp and Windows sandboxing",
        ],
    }
    if sysname != "Darwin":
        out["detail"] = "not applicable: this probe checks macOS Seatbelt only (Linux/Windows not covered)"
        return out
    with tempfile.TemporaryDirectory(prefix="par-sandbox-probe-") as tmp:
        profile = Path(tmp) / "allow-all.sb"
        profile.write_text(PROFILE, encoding="utf-8")
        argv = [SANDBOX_EXEC, "-f", str(profile), "/bin/echo", MARKER]
        try:
            p = run(argv, capture_output=True, text=True, timeout=SANDBOX_TIMEOUT,
                    stdin=subprocess.DEVNULL, check=False)
        except FileNotFoundError:
            out["detail"] = "sandbox-exec is missing: cannot determine nested sandbox availability"
            return out
        except Exception as exc:                       # a slow or broken OS is a fact, not an exception
            out["detail"] = f"probe failed: {type(exc).__name__}"
            return out
        text = ((p.stdout or "") + (p.stderr or "")).strip()
        ok = p.returncode == 0 and MARKER in (p.stdout or "")
        out["nested_sandbox_available"] = ok
        out["returncode"] = p.returncode
        # Deliberately do not echo raw command output: it can contain host paths. Classify instead.
        if ok:
            out["detail"] = "nested Seatbelt sandbox is available"
        elif "sandbox_apply" in text or "Operation not permitted" in text:
            out["detail"] = "nested Seatbelt sandbox is BLOCKED (sandbox_apply / Operation not permitted)"
            if out["inherited_sandbox_check"] == 1:
                out["detail"] += ("; the host process inherits a sandbox, so a vendor CLI cannot stack "
                                  "its own - see docs/CODEX.md section 14")
        else:
            out["detail"] = f"nested Seatbelt sandbox did not start (exit {p.returncode})"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline nested-sandbox check (never calls a model)")
    ap.add_argument("--json", action="store_true", help="emit a JSON evidence record")
    args = ap.parse_args()
    out = probe_sandbox()
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        state = {True: "AVAILABLE", False: "BLOCKED", None: "NOT_APPLICABLE"}[out["nested_sandbox_available"]]
        print(f"nested sandbox: {state} ({out['system']})")
        print(f"  {out['detail']}")
        print("  model calls: 0 | network calls: 0 | credential reads: 0 | installs: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
