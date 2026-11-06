"""The contract between the Kernel and whatever supplies a project's context.

The Kernel only needs four answers: what the project looks like, which entries a Worker may be given
(with sizes and hashes), the rendered text, and whether a write attempt is allowed. Keeping that
surface explicit lets T04 add a generic project adapter without teaching the Kernel about it.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from par.context.types import ContextManifest, ProjectView, WriteReadiness


@runtime_checkable
class Context(Protocol):
    def view(self) -> ProjectView: ...

    def manifest(self, view: "ProjectView | None" = None) -> ContextManifest: ...

    def render(self, manifest: ContextManifest) -> str: ...

    def write_readiness(self, view: "ProjectView | None" = None, *, assume_enabled: bool = False) -> WriteReadiness: ...
