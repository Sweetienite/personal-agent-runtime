#!/usr/bin/env python3
"""Check that documented ``par`` command lines are accepted by the real CLI parser.

    python -B tests/tools/docs_cli_examples.py

This is a syntax check, not an execution test: it extracts ``par ...`` lines from fenced shell
blocks, lets the real ``par.cli.build_parser`` parse them, and exits 1 on an unknown subcommand,
an unknown flag or an unexpected positional argument. Placeholders stay as literal strings; values
for integer flags (``--id``, ``--attempt``) are normalised to ``1``.
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import re
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from par.cli import build_parser  # noqa: E402

DOCS = (
    "README.md",
    "examples/README.md",
    "docs/QUICKSTART.md",
    "docs/USER_GUIDE.md",
    "docs/OPERATIONS.md",
    "docs/en/GETTING_STARTED.md",
    "docs/en/USER_GUIDE.md",
    "docs/en/INTEGRATIONS.md",
    "docs/en/TROUBLESHOOTING.md",
)
SHELL_LANGS = {"", "bash", "sh", "shell", "zsh"}
INT_FLAGS = {"--id", "--attempt"}
OPERATORS = {"|", "||", "&&", ";", ">", ">>", "<", "2>"}


def commands(text: str):
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*```(\w*)\s*$", line)
        if match:
            fence = None if fence is not None else match.group(1).lower()
            continue
        if fence not in SHELL_LANGS:
            continue
        stripped = line.strip()
        if stripped.startswith("$ "):
            stripped = stripped[2:]
        match = re.match(r"^(?:\./)?(?:\.venv/bin/)?par\s+(.*)$", stripped)
        if not match:
            continue
        try:
            tokens = shlex.split(match.group(1), comments=True)
        except ValueError:
            continue
        for index, token in enumerate(tokens):
            if token in OPERATORS:
                tokens = tokens[:index]
                break
        for index, token in enumerate(tokens[:-1]):
            if token in INT_FLAGS:
                tokens[index + 1] = "1"
        if tokens:
            yield number, tokens


def main() -> int:
    parser = build_parser()
    problems = []
    checked = 0
    for relative in DOCS:
        path = ROOT / relative
        if not path.exists():
            problems.append(f"{relative}: missing document")
            continue
        for line, tokens in commands(path.read_text(encoding="utf-8")):
            checked += 1
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    parser.parse_args(tokens)
            except SystemExit:
                problems.append(f"{relative}:{line}: `par {' '.join(tokens)}` is not accepted by the CLI parser")
    if problems:
        for problem in problems:
            print(problem)
        print(f"docs_cli_examples: FAIL ({len(problems)} of {checked} command lines)")
        return 1
    print(f"docs_cli_examples: OK ({checked} command lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
