# Contributing

This is a solo-maintained personal tool, open-sourced as-is. Issues and small, focused PRs are welcome; please open an issue first for anything beyond a trivial fix, since the design has some deliberate, non-obvious constraints (see below).

## Before changing code

- Read [AGENTS.md](AGENTS.md) first — it's the actual house rules this project (and any agent working on it) follows: no new third-party dependencies, no comments that explain *what* code does (only non-obvious *why*), don't widen a change's scope past what it asks for. It describes a `.ai/` task-ledger (an active task contract, a maintainer-owned `STATE.md`) that is the author's own private working state and isn't part of this repository - treat those references as describing a process, not files you'll find here. For anything beyond a small, focused fix you don't need to replicate that process; open an issue first instead.
- `docs/specs/*.md` describe the exact contract each module implements; `docs/SECURITY.md` describes what the Runtime is and isn't allowed to touch. If your change affects either, update the doc in the same PR.
- A change to a safety-relevant line (permissions, path policy, redaction, what a tool is allowed to execute) must keep `python3 -B tests/tools/mutation_check.py` passing for the entries it touches, not just the unit tests.

## Running the test suite

```bash
python3 -B -m unittest discover -s tests -t .      # full suite, ~1564 tests
python3 -B tests/tools/parallel_tests.py            # the same, parallelized, faster
```

CI runs three explicitly named checks, none of which is the full suite.
For a stranger-machine reproduction of the whole zero-account path, see [docs/QUICKSTART.md](docs/QUICKSTART.md).

- `smoke_install` (macOS) — `pip install -e .`, `par doctor`, `par version`.
- `core_unittests` (macOS) — the state, store, contract, approval, policy, hand-off and integration regressions:
- `linux_security_core` (Ubuntu) — `pip install -e .`, `par doctor` (WARN for an untested platform, exit 0),
  `par version`, then the platform-independent security regressions in `docs/SECURITY.md`'s matrix
  (states, contract, policy, redaction, paths, provider probe, `test_p3_security_audit`).

```bash
python3 -B -m unittest tests.unit.test_states tests.unit.test_store tests.unit.test_contract \
    tests.unit.test_kernel_control tests.unit.test_kernel_submit tests.unit.test_kernel_policy \
    tests.unit.test_context_interface tests.unit.test_generic_project tests.unit.test_p1_compat tests.unit.test_task_draft \
    tests.unit.test_mcp_prepare tests.unit.test_manual_handoff tests.unit.test_evidence_summary \
    tests.unit.test_candidate_integration tests.unit.test_role_provider_caps tests.unit.test_provider_probe \
    tests.unit.test_p3_security_audit
```

The rest of the suite still runs only on a developer's machine: some tests assume a single-user macOS
machine (the real Codex/Antigravity adapters refuse GitHub's group-writable Python), and others expect
macOS-specific `doctor` checks or a larger `ARG_MAX`. Those are real safety checks, so CI stays narrow
rather than weakening them. The Linux job deliberately repeats the security regressions that have no
platform dependency; it does **not** claim Linux support for the real adapters. Known platform gaps are
graded in [docs/SECURITY.md](docs/SECURITY.md).

No network access and no real model calls happen in the test suite — everything runs against scripted fake CLIs.

## Scope

PRs that add a third-party dependency, call a real vendor model's API, or touch credentials/auth flows will almost certainly be declined — see the project's design boundaries in `README.md` for why. If you're unsure whether something fits, open an issue describing the idea before writing code.
