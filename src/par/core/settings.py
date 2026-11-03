"""Tunable Kernel constants in one place (tests shrink the times; the defaults follow the plan, section 6.5)."""
from __future__ import annotations

from dataclasses import dataclass, field

from par.core.contract import DEFAULT_TIMEOUTS


@dataclass(frozen=True)
class KernelSettings:
    heartbeat_interval: float = 2.0       # how often a running attempt proves its Kernel is alive
    stale_after: float = 15.0             # a heartbeat older than this means the owning Kernel is gone
    grace: float = 5.0                    # polite stop -> hard kill of the Worker's process group
    poll: float = 0.05
    cancel_check: float = 0.25
    max_log_bytes: int = 20 * 1024 * 1024
    rate_limit_cooldown: float = 300.0
    transient_retry_delay: float = 30.0   # wait before the one bounded automatic retry
    max_parallel_workers: "int | None" = None   # None: the active profile decides (1 or 2, default 1); tests may fix a number
    git_timeout: float = 120.0
    check_grace: float = 2.0
    default_timeouts: "dict[str, float]" = field(default_factory=lambda: dict(DEFAULT_TIMEOUTS))
