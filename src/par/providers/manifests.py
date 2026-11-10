"""The one place that says which automatic adapters exist and what each build may do.

An adapter manifest is a static fact of this build, not of a run: kind, factory class, declared
capabilities, the platforms it was verified on, how it authenticates, the version it was tested
with, and whether it may run without a person starting it. The router and ``build_adapters`` both
read this table, so adding an adapter cannot leave a second hard-coded allowlist behind. Nothing
here imports a vendor program, reads a credential or contacts a service.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from par.providers.antigravity import AntigravityAdapter
from par.providers.codex import CodexAdapter
from par.providers.copilot import CopilotAdapter


@dataclass(frozen=True)
class AdapterManifest:
    kind: str
    adapter_cls: Any
    capabilities: "object | None"
    platforms: "tuple[str, ...]"          # sys.platform values with real verification evidence
    auth_mode: str
    version_bound: str                     # the version the evidence was gathered with
    automatic: bool                        # may run without a person starting it
    note: str = ""


BUILTIN_MANIFESTS: "dict[str, AdapterManifest]" = {
    "codex": AdapterManifest(
        kind="codex", adapter_cls=CodexAdapter, capabilities=getattr(CodexAdapter, "CAPABILITIES", None),
        platforms=("darwin",), auth_mode="chatgpt_subscription_login", version_bound="codex-cli 0.166.0-alpha.6",
        automatic=True, note="real R1/R2 on macOS with the default read-only/workspace-write sandbox (2026-11-07)"),
    "antigravity": AdapterManifest(
        kind="antigravity", adapter_cls=AntigravityAdapter, capabilities=getattr(AntigravityAdapter, "CAPABILITIES", None),
        platforms=("darwin",), auth_mode="google_login_via_system_keychain", version_bound="agy 1.3.5",
        automatic=True, note="experimental and disabled by default: the vendor terms prohibit third-party wrapping"),
    "copilot": AdapterManifest(
        kind="copilot", adapter_cls=CopilotAdapter, capabilities=getattr(CopilotAdapter, "CAPABILITIES", None),
        platforms=("darwin",), auth_mode="github_copilot_login", version_bound="GitHub Copilot CLI 1.0.5",
        automatic=True,
        note="experimental text-answer only and disabled by default: no file writes, npm Node script with no vendor "
             "signature, tools disabled through an empty allowlist, no OS sandbox claimed"),
}

AUTOMATIC_KINDS = frozenset(kind for kind, manifest in BUILTIN_MANIFESTS.items() if manifest.automatic)


def manifest_for(kind: str) -> "Optional[AdapterManifest]":
    """The manifest of one provider kind, or None when this build has no automatic adapter for it."""
    return BUILTIN_MANIFESTS.get(kind)


def factory_for(kind: str) -> Any:
    manifest = manifest_for(kind)
    return manifest.adapter_cls if manifest is not None else None
