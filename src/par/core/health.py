"""Provider health: what the Kernel has actually seen, never a guess.

No quota percentages, no usage-based estimates, no background polling. A health record is written only
when a real attempt ends with a classified failure, and it is removed when a real attempt succeeds or a
person clears it. Scope is deliberate:

* ``RATE_LIMITED`` and ``MODEL_UNAVAILABLE`` are about one model;
* ``QUOTA_EXHAUSTED`` and ``AUTH_REQUIRED`` are about the account, so they cover the whole provider.
  Which models share a quota pool is something only the user's configuration can say; V1 does not infer it
  from model names.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from par.core.clock import iso, parse_iso
from par.core.store import Store

PROVIDER_WIDE = frozenset({"QUOTA_EXHAUSTED", "AUTH_REQUIRED"})
MODEL_SCOPED = frozenset({"RATE_LIMITED", "MODEL_UNAVAILABLE"})
RECORDED = PROVIDER_WIDE | MODEL_SCOPED
_PRIORITY = ("AUTH_REQUIRED", "QUOTA_EXHAUSTED", "MODEL_UNAVAILABLE", "RATE_LIMITED")


@dataclass(frozen=True)
class Verdict:
    usable: bool
    status: "str | None" = None
    scope: "str | None" = None           # "provider" | "model"
    until: "str | None" = None           # None with a status = until a person clears it or a real call succeeds
    detail: str = ""

    def describe(self) -> str:
        if self.usable:
            return "no known problem"
        until = f"until {self.until}" if self.until else "until cleared (par health clear) or a real call succeeds"
        return f"{self.status} ({self.scope}-wide) {until}"


class Health:
    def __init__(self, store: Store, *, rate_limit_cooldown: float = 300.0, max_retry_after: float = 86400.0) -> None:
        self.store = store
        self.rate_limit_cooldown = rate_limit_cooldown
        self.max_retry_after = max_retry_after

    def verdict(self, provider_id: str, model: str) -> Verdict:
        now = self.store.clock.iso()
        active = []
        for scope, key_model in (("provider", ""), ("model", model)):
            row = self.store.get_health(provider_id, key_model)
            if row and (row["until"] is None or row["until"] > now):
                active.append((scope, row))
        if not active:
            return Verdict(True)
        scope, row = min(active, key=lambda item: _PRIORITY.index(item[1]["status"]) if item[1]["status"] in _PRIORITY else 99)
        return Verdict(False, row["status"], scope, row["until"], row["detail"] or "")

    def record_failure(self, failure_class: str, provider_id: str, model: str, *,
                       retry_after: "Optional[float]" = None, detail: str = "", task_id: "str | None" = None,
                       reset_at: "str | None" = None) -> bool:
        """Record a classified failure (inside a transaction). Returns False for classes that say nothing about health.

        ``reset_at`` is a time the VENDOR gave for a quota or auth block; it is used only if it is a real timestamp a
        little in the future (at most 35 days). Without one, quota and auth blocks last until cleared - never guessed.
        """
        if failure_class not in RECORDED:
            return False
        until: "str | None" = None
        if failure_class == "RATE_LIMITED":
            seconds = retry_after if isinstance(retry_after, (int, float)) and 0 < retry_after <= self.max_retry_after else self.rate_limit_cooldown
            until = iso(self.store.clock.now() + timedelta(seconds=seconds))
        elif failure_class == "QUOTA_EXHAUSTED" and isinstance(reset_at, str):
            try:
                when = parse_iso(reset_at)
            except ValueError:
                when = None
            now = self.store.clock.now()
            if when is not None and now < when <= now + timedelta(days=35):
                until = iso(when)
        key_model = "" if failure_class in PROVIDER_WIDE else model
        self.store.set_health(provider_id, key_model, failure_class, until=until, detail=detail)
        self.store.add_event("HEALTH_CHANGED", task_id=task_id, reason=failure_class,
                             data={"provider": provider_id, "model": key_model or None, "until": until})
        return True

    def record_success(self, provider_id: str, model: str, *, task_id: "str | None" = None) -> int:
        """A real attempt worked, so earlier failure records for it no longer hold."""
        removed = self.store.clear_health(provider_id, "") + self.store.clear_health(provider_id, model)
        if removed:
            self.store.add_event("HEALTH_CLEARED", task_id=task_id, reason="a real attempt succeeded",
                                 data={"provider": provider_id, "model": model})
        return removed

    def clear(self, provider_id: str, model: "str | None" = None) -> int:
        removed = self.store.clear_health(provider_id, model)
        if removed:
            self.store.add_event("HEALTH_CLEARED", reason="cleared by a person",
                                 data={"provider": provider_id, "model": model})
        return removed
