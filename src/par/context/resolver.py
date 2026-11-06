"""Which Context implementation a project gets.

The Kernel takes its factory from its own constructor (the CLI, or a test); nothing in a project's
files or configuration is read here, so a repository cannot supply its own context adapter. T03 keeps
the single Harness default; T04 adds the explicit context-mode switch at this seam.
"""
from __future__ import annotations

import os

from par.config import ProjectEntry
from par.context.harness import HarnessContextAdapter


def default_context(project: ProjectEntry, home: "str | os.PathLike[str] | None" = None) -> HarnessContextAdapter:
    return HarnessContextAdapter(project, home)
