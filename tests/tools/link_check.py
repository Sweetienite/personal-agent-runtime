#!/usr/bin/env python3
"""Check relative links in the published Markdown docs.

    python -B tests/tools/link_check.py
    python -B tests/tools/link_check.py --historical   # also scan docs/reports (known archive exclusions)

Historical reports under ``docs/reports/`` intentionally point at evidence files that were excluded
from the public push; each report carries a disclaimer. They are skipped by default so a current
docs regression is visible as a failure, not hidden among known archive links. External (http,
mailto, anchor) links and links that resolve outside the repository (for example GitHub-relative
``../../security/advisories/new``) are not file links and are ignored.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FENCE = re.compile(r"```.*?```", re.S)


def _targets(text: str) -> "list[str]":
    text = _FENCE.sub("", text)
    out = []
    for raw in _LINK.findall(text):
        target = raw.strip().split("#", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        out.append(target)
    return out


def check(include_historical: bool = False) -> "list[tuple[str, str]]":
    missing = []
    for md in sorted(ROOT.rglob("*.md")):
        rel = md.relative_to(ROOT)
        if ".git" in rel.parts:
            continue
        if not include_historical and rel.parts[:2] == ("docs", "reports"):
            continue
        for target in _targets(md.read_text(encoding="utf-8", errors="replace")):
            resolved = (md.parent / target).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                continue                                # a GitHub-relative link, not a file path
            if not resolved.exists():
                missing.append((str(rel), target))
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--historical", action="store_true", help="also scan docs/reports (known archive exclusions)")
    args = parser.parse_args()
    missing = check(args.historical)
    for source, target in missing:
        print(f"{source} -> {target}")
    print(f"{len(missing)} missing relative link(s)")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
