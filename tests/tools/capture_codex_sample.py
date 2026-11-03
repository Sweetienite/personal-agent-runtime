#!/usr/bin/env python3
"""Turn a real run's stdout.log into a redacted test sample (tests/data/codex_real/) and register it in the manifest.

    python -B tests/tools/capture_codex_sample.py --log <attempt>/stdout.log --name r1_success --scenario "read-only question" \
        --exit-code 0 --classified-as none [--stderr-log <attempt>/stderr.log] [--data-dir <throw-away data dir>]

The log was already redacted by the Kernel when it was written. Here the throw-away data directory and your home directory are
also replaced by placeholders, every line must be JSON, and nothing a redactor would still change may remain.
You state what the run MEANS (`--classified-as`); the tests then check the adapter agrees.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from par.core.redact import redact_text                  # noqa: E402
from par.providers import codex as C                     # noqa: E402
from par.providers.codex import CodexAdapter             # noqa: E402

DATA = REPO / "tests" / "data" / "codex_real"
MANIFEST = DATA / "manifest.json"
CLASSES = {"none", "QUOTA_EXHAUSTED", "RATE_LIMITED", "AUTH_REQUIRED", "MODEL_UNAVAILABLE", "TRANSIENT_ERROR", "EXECUTION_FAILED", "UNKNOWN"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", required=True)
    ap.add_argument("--name", required=True, help="file name stem, e.g. r1_success")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--exit-code", type=int, required=True)
    ap.add_argument("--classified-as", required=True, choices=sorted(CLASSES))
    ap.add_argument("--stderr-log")
    ap.add_argument("--data-dir", help="the throw-away data directory of the run: its path is replaced by <DATA>")
    args = ap.parse_args()

    text = Path(args.log).read_text(encoding="utf-8", errors="replace")
    home = str(Path.home())
    if args.data_dir:
        text = text.replace(str(Path(args.data_dir).resolve()), "<DATA>").replace(args.data_dir, "<DATA>")
    text = text.replace(home, "~")
    text = re.sub(r"/(?:private/)?var/folders/[^\s\"'\\]+", "<TMP>", text)
    lines = [l for l in text.splitlines() if l.strip()]
    for n, line in enumerate(lines, 1):
        try:
            json.loads(line)
        except ValueError:
            sys.exit(f"line {n} is not JSON: {line[:80]!r}")
        if redact_text(line) != line:
            sys.exit(f"line {n} still contains text a redactor would change: refusing to store it")
    adapter = CodexAdapter("/unused", environ={})
    events = [e for e in (adapter.parse_event(l) for l in lines) if e is not None]
    other = sorted({e.data["type"] for e in events if e.kind == "other" and e.data.get("type") not in ("turn.started", "reasoning")})
    has_reset = any("reset_at" in e.data for e in events)
    stderr_tail = ""
    if args.stderr_log and Path(args.stderr_log).is_file():
        stderr_tail = redact_text(Path(args.stderr_log).read_text(encoding="utf-8", errors="replace"))[-500:].replace(home, "~")

    version = subprocess.run([C.BUNDLED_CODEX, "--version"], capture_output=True, text=True).stdout.strip().split()[-1]
    DATA.mkdir(parents=True, exist_ok=True)
    target = DATA / f"{args.name}.jsonl"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {"samples": []}
    entry = {"file": target.name, "codex_version": version, "scenario": args.scenario, "exit_code": args.exit_code,
             "classified_as": None if args.classified_as == "none" else args.classified_as, "known_other_types": other,
             "has_reset_time": has_reset, "stderr_tail": stderr_tail}
    manifest["samples"] = [s for s in manifest["samples"] if s["file"] != target.name] + [entry]
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"stored {target.relative_to(REPO)}: {len(lines)} lines, codex {version}, classified_as={entry['classified_as']}, other types={other}, reset time={has_reset}")


if __name__ == "__main__":
    main()
