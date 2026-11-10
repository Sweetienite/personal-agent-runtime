"""Personal Agent Runtime (PAR)."""

# 1.0.0.devN: N is the construction phase (P0 -> dev0 ... P8 -> dev8); 1.0.0 was the first
# tagged release and 1.1.x are the productization/audit releases. Support stays scoped to the
# routes named here; the P3 gate wording lives in release notes and acceptance records, not in
# the user-facing version output.
__version__ = "1.1.1"
PHASE = "RELEASE"
RELEASE_STATUS = "released 2026-11-09"
SUPPORT_TIER = "scoped"
SUPPORTED_ROUTES = ("codex_native", "manual_handoff")
