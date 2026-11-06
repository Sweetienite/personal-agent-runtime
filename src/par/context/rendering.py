"""Turn a manifest back into text, verifying every inline entry against what the manifest recorded.

Both context adapters use this: the manifest is the promise, and rendering is where that promise is
checked against the files again (a file changed between manifest and render is a refusal, not a silent
new context)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from par.context.types import ContextChanged, ContextManifest, ManifestEntry
from par.core.policy import PolicyViolation, is_sensitive_path


def read_inline(root: Path, manifest: ContextManifest) -> "list[tuple[ManifestEntry, str]]":
    """The text of every inline entry, verified against the recorded size and hash."""
    out = []
    root_real = os.path.realpath(root)
    for e in manifest.inline_entries():
        target = os.path.realpath(root / e.path)
        if is_sensitive_path(e.path) or not (target == root_real or target.startswith(root_real + os.sep)):
            raise PolicyViolation("a manifest entry points outside the project or at a sensitive path")
        data = Path(target).read_bytes()
        if len(data) != e.bytes or hashlib.sha256(data).hexdigest() != e.sha256:
            raise ContextChanged(e.path)
        out.append((e, data.decode("utf-8", errors="replace")))
    return out


def render_manifest(root: Path, manifest: ContextManifest) -> str:
    parts = [f"# Context for project {manifest.project_id} (manifest {manifest.digest[:12]})"]
    b = manifest.base
    parts.append(f"# base: {b['vcs']}" + (f" {b['head'][:12]} on {b['branch']}" if b.get("head") else "")
                 + (f", uncommitted: {sum(b['dirty'].values())}" if sum(b["dirty"].values()) else ""))
    for e, text in read_inline(root, manifest):
        parts.append(f"\n## {e.kind}: {e.path}  (sha256 {e.sha256[:12]}, {e.bytes} bytes)\n{text.rstrip()}")
    pointers = [e for e in manifest.entries if e.mode == "pointer"]
    if pointers:
        parts.append("\n## Pointers (not included; read on demand if your permissions allow)")
        parts += [f"- {e.kind}: {e.path}" + (f" ({e.bytes} bytes)" if e.bytes is not None else "")
                  + (f" - {e.reason}" if e.reason else "") for e in pointers]
    if manifest.skipped_sensitive:
        parts.append(f"\n({manifest.skipped_sensitive} sensitive-looking pointer(s) were left out.)")
    return "\n".join(parts) + "\n"
