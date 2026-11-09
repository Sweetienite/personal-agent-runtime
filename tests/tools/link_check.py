#!/usr/bin/env python3
"""Check relative links and heading anchors in the published Markdown docs.

    python -B tests/tools/link_check.py
    python -B tests/tools/link_check.py --historical   # also scan docs/reports (known archive exclusions)

Historical reports under ``docs/reports/`` intentionally point at evidence files that were excluded
from the public push; each report carries a disclaimer. They are skipped by default so a current
docs regression is visible as a failure, not hidden among known archive links. External (http,
mailto) links are ignored. A link that resolves outside the repository (for example GitHub-relative
``../../security/advisories/new``) is not a file path and is ignored.

Same-page and cross-page ``#fragment`` links are checked against GitHub-style heading slugs and
explicit ``<a name="...">`` anchors. A path that differs only by case is reported as a case error,
because GitHub renders it as a 404.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[2]
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FENCE = re.compile(r"```.*?```", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$", re.M)
_HTML_ANCHOR = re.compile(r"<a\s+(?:name|id)=\"([^\"]+)\"", re.I)
_SLUG_STRIP = re.compile(r"[^\w\- ]", re.UNICODE)


def _slugs(text: str) -> "set[str]":
    text = _COMMENT.sub("", _FENCE.sub("", text))
    out: "set[str]" = set()
    counts: "dict[str, int]" = {}
    for match in _HEADING.finditer(text):
        title = match.group(2).strip()
        title = re.sub(r"`([^`]*)`", r"\1", title)
        title = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
        base = _SLUG_STRIP.sub("", title.lower()).strip().replace(" ", "-")
        n = counts.get(base, 0)
        counts[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    out.update(_HTML_ANCHOR.findall(text))
    return out


def _paths(md: pathlib.Path) -> "list[tuple[str, str]]":
    text = _COMMENT.sub("", md.read_text(encoding="utf-8", errors="replace"))
    text = _FENCE.sub("", text)
    out = []
    for raw in _LINK.findall(text):
        target = raw.strip()
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, fragment = target.partition("#")
        out.append((path, fragment))
    return out


def _resolve(md: pathlib.Path, target: str) -> "pathlib.Path | None":
    resolved = (md.parent / target).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        return None
    return resolved


def _case_match(resolved: pathlib.Path) -> "pathlib.Path | None":
    """The same path with different case, when it exists; GitHub would 404 the literal link."""
    current = ROOT
    for part in resolved.relative_to(ROOT).parts:
        if not current.is_dir():
            return None
        names = {p.name.casefold(): p for p in current.iterdir()}
        found = names.get(part.casefold())
        if found is None:
            return None
        current = found
    return current if current != resolved and current.exists() else None


def check(include_historical: bool = False) -> "list[tuple[str, str, str]]":
    problems: "list[tuple[str, str, str]]" = []
    cache: "dict[pathlib.Path, set[str]]" = {}

    def anchors(path: pathlib.Path) -> "set[str]":
        if path not in cache:
            cache[path] = _slugs(path.read_text(encoding="utf-8", errors="replace")) if path.suffix == ".md" else set()
        return cache[path]

    for md in sorted(ROOT.rglob("*.md")):
        rel = md.relative_to(ROOT)
        if ".git" in rel.parts or rel.parts[:1] == (".ai",):
            continue                                # VCS internals and the private process ledger are not published docs
        if not include_historical and rel.parts[:2] == ("docs", "reports"):
            continue
        for target, fragment in _paths(md):
            if not target:
                destination = md
            else:
                destination = _resolve(md, target)
                if destination is None:
                    continue                            # a GitHub-relative link, not a file path
            if not destination.exists():
                similar = _case_match(destination)
                if similar is not None:
                    problems.append((str(rel), target or str(md), f"wrong case; the file is {similar.relative_to(ROOT)}"))
                else:
                    problems.append((str(rel), target or str(md), "missing file"))
                continue
            if fragment and not fragment.startswith("L") and destination.suffix == ".md":
                if fragment not in anchors(destination):
                    problems.append((str(rel), target or str(md), f"missing anchor #{fragment}"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--historical", action="store_true", help="also scan docs/reports (known archive exclusions)")
    args = parser.parse_args()
    problems = check(args.historical)
    for source, target, why in problems:
        print(f"{source} -> {target} ({why})")
    print(f"{len(problems)} broken relative link or anchor(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
