"""Which adapter implements which provider id.

The registry starts EMPTY. Real adapters are added by the phase that builds them (Codex in P3, Antigravity
in P4); until then every real provider is "no adapter registered" and a task that routes to one ends in
BLOCKED_NO_WORKER instead of reaching for something unregistered. Tests register scripted providers here -
config files cannot, because a config has no "fake" provider kind.
"""
from __future__ import annotations

import re
from typing import Callable

from par.providers.base import ProviderAdapter

_ID = re.compile(r"[a-z][a-z0-9_]*\Z")


class NoAdapter(KeyError):
    pass


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: "dict[str, Callable[[], ProviderAdapter]]" = {}

    def register(self, provider_id: str, factory: "Callable[[], ProviderAdapter]") -> None:
        if not _ID.match(provider_id):
            raise ValueError(f"invalid provider id {provider_id!r}")
        if provider_id in self._factories:
            raise ValueError(f"an adapter for {provider_id!r} is already registered")
        self._factories[provider_id] = factory

    def has(self, provider_id: str) -> bool:
        return provider_id in self._factories

    def ids(self) -> "list[str]":
        return sorted(self._factories)

    def create(self, provider_id: str) -> ProviderAdapter:
        """A fresh adapter instance (one per attempt, so no state leaks between runs)."""
        try:
            factory = self._factories[provider_id]
        except KeyError:
            raise NoAdapter(provider_id) from None
        return factory()


def default_registry() -> AdapterRegistry:
    """P2: no real adapters yet. P3 registers Codex, P4 registers Antigravity."""
    return AdapterRegistry()
