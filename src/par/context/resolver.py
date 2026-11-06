"""Which Context implementation a project gets.

The Kernel takes its factory from its own constructor (the CLI, or a test); nothing in a project's
files or configuration is read here, so a repository cannot supply its own context adapter. A registered
entry says which mode it wants; an entry written before the field existed keeps the Harness behaviour.
"""
from __future__ import annotations

import os

from par.config import ProjectEntry
from par.context.generic import GenericContextAdapter
from par.context.harness import HarnessContextAdapter
from par.context.interface import Context


def default_context(project: ProjectEntry, home: "str | os.PathLike[str] | None" = None) -> Context:
    if project.context_mode == "generic":
        return GenericContextAdapter(project, home)
    return HarnessContextAdapter(project, home)
