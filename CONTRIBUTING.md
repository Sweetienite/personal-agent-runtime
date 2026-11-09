# Contributing

This is a solo-maintained personal tool, open-sourced as-is. Issues and small, focused PRs are welcome; please open an issue first for anything beyond a trivial fix, since the design has some deliberate, non-obvious constraints (see below).

## Before changing code

- Read [AGENTS.md](AGENTS.md) first — it's the actual house rules this project (and any agent working on it) follows: no new third-party dependencies, no comments that explain *what* code does (only non-obvious *why*), don't widen a change's scope past what it asks for. It describes a `.ai/` task-ledger (an active task contract, a maintainer-owned `STATE.md`) that is the author's own private working state and isn't part of this repository - treat those references as describing a process, not files you'll find here. For anything beyond a small, focused fix you don't need to replicate that process; open an issue first instead.
- `docs/specs/*.md` describe the exact contract each module implements; `docs/SECURITY.md` describes what the Runtime is and isn't allowed to touch. If your change affects either, update the doc in the same PR.
- A change to a safety-relevant line (permissions, path policy, redaction, what a tool is allowed to execute) must keep `python3 -B tests/tools/mutation_check.py` passing for the entries it touches, not just the unit tests.

## Checking documentation links

```bash
python3 -B tests/tools/link_check.py              # current docs; exits 1 on a missing relative link
python3 -B tests/tools/link_check.py --historical # also lists known docs/reports archive exclusions
```

Historical reports under `docs/reports/` link to evidence files that were excluded from the public
push; each report carries a disclaimer, so the default check skips that archive. External URLs are
ignored. Same-page and cross-page `#fragment` links are checked against GitHub-style heading slugs,
and a path that differs only by case is reported as a 404.

## Checking the docs contract

```bash
python3 -B tests/tools/docs_contract.py
```

This compares [docs/product-capabilities.json](docs/product-capabilities.json) with the real version,
release status, MCP tool count, adapter manifests, CI job list and default approval mode, then checks
the stable README sections, table column counts, required English pages and dated evidence links.
It exits 1 on drift. A capability may only be labelled `VERIFIED_*` with a dated run and an evidence
file in the repository.

## Running the test suite

```bash
python3 -B -m unittest discover -s tests -t .      # full suite, ~1596 tests
python3 -B tests/tools/parallel_tests.py            # the same, parallelized, faster
```

CI runs four explicitly named checks, none of which is the full suite.
For a stranger-machine reproduction of the whole zero-account path, see [docs/QUICKSTART.md](docs/QUICKSTART.md).

- `smoke_install` (macOS) — `pip install -e .`, `par doctor`, `par version`.
- `core_unittests` (macOS) — the state, store, contract, approval, policy, hand-off and integration regressions.
- `linux_security_core` (Ubuntu) — `pip install -e .`, `par doctor` (WARN for an untested platform, exit 0),
  `par version`, then the platform-independent security regressions in `docs/SECURITY.md`'s matrix
  (states, contract, policy, redaction, paths, provider probe, `test_p3_security_audit`,
  `test_approval_grants`, `test_authorization_lease`), followed by `link_check.py` and `docs_contract.py`.
- `linux_process_control` (Ubuntu) — the scripted cancellation/timeout/process-group and orphan
  recovery regressions (`test_kernel_control`, `test_validation_results`, `test_kernel_recovery`) on
  real Linux signal behaviour. `test_subprocesses` stays on macOS: one case deliberately feeds a
  large synthetic event stream through argv, and Linux's smaller per-argument limit is an ARG_MAX
  assumption rather than a process-control semantic.

```bash
python3 -B -m unittest tests.unit.test_states tests.unit.test_store tests.unit.test_contract \
    tests.unit.test_kernel_control tests.unit.test_kernel_submit tests.unit.test_kernel_policy \
    tests.unit.test_context_interface tests.unit.test_generic_project tests.unit.test_p1_compat tests.unit.test_task_draft \
    tests.unit.test_mcp_prepare tests.unit.test_manual_handoff tests.unit.test_evidence_summary \
    tests.unit.test_candidate_integration tests.unit.test_role_provider_caps tests.unit.test_provider_probe \
    tests.unit.test_p3_security_audit tests.unit.test_approval_grants tests.unit.test_authorization_lease
```

The rest of the suite still runs only on a developer's machine: some tests assume a single-user macOS
machine (the real Codex/Antigravity adapters refuse GitHub's group-writable Python), and others expect
macOS-specific `doctor` checks or a larger `ARG_MAX`. Those are real safety checks, so CI stays narrow
rather than weakening them. The Linux job deliberately repeats the security regressions that have no
platform dependency; it does **not** claim Linux support for the real adapters. Known platform gaps are
graded in [docs/SECURITY.md](docs/SECURITY.md).

No network access and no real model calls happen in the test suite — everything runs against scripted fake CLIs.

## Scope

PRs that add a third-party dependency, call a real vendor model's API, or touch credentials/auth flows will almost certainly be declined — see the product boundaries in [README.md](README.md), [docs/en/SECURITY.md](docs/en/SECURITY.md) and [docs/product-capabilities.json](docs/product-capabilities.json). If you're unsure whether something fits, open an issue describing the idea before writing code.

Documentation changes must keep the English and Chinese facts identical. A new compatibility claim
needs a dated run, the exact tool/version/OS/account, and an evidence file; a `VERIFIED_*` label
without that is not accepted. Do not edit the historical reports under `docs/reports/` to make a
current claim look verified.
