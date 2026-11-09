# Finalization status

Branch: `release/final-productization`, based on `fix/p0-p3-audit-20261109` @ `c796981`
(which is one commit on top of `public-origin/main` @ `bc9cb0c`).

Working tree baseline for this task: clean except untracked `.ai/`, this package, and the audit
report. No user work was overwritten, reset or force-pushed.

| Card | Scope | Status | Evidence |
|---|---|---|---|
| F00 | Baseline, branch, status/gaps ledger | DONE | This file, `KNOWN_GAPS.md`, git log |
| F01 | Coordinator lease, grants, action policy | DONE (code/tests); CI pending | `6ef1bab`, `tests/unit/test_authorization_lease.py`, M921–M926 |
| F02 | Execution safety, candidate/check authorization, process control | DONE locally; Ubuntu CI pending | `docs/SECURITY.md` action matrix; `test_kernel_control` 142 tests OK; CI evidence pending |
| F03 | Capability fact source and matrices | DONE | `docs/product-capabilities.json`, `docs/en/COMPATIBILITY.md`, `docs/COMPATIBILITY.md` |
| F04 | Bilingual README redesign | DONE | `README.md` (English), `README.zh-CN.md`, `README.en.md` pointer |
| F05 | Install / onboarding / first task | DONE (manual path verified); real-provider path documented | `docs/en/GETTING_STARTED.md`, `docs/QUICKSTART.md`, `UAT_LOG.md` |
| F06 | Integrations and manual workflows | DONE (docs); ≥3 paths reproduced in fake/manual environments | `docs/en/INTEGRATIONS.md`, `UAT_LOG.md` |
| F07 | Reference, security, architecture, troubleshooting | DONE | `docs/en/*`, `docs/INDEX*.md`, `docs/SECURITY.md` |
| F08 | CI, docs contract, links/anchors, deliberate-break proof | IN PROGRESS | `tests/tools/docs_contract.py`, `tests/tools/link_check.py`, workflow update pending |
| F09 | Packaging, governance, examples, changelog | IN PROGRESS | Governance files added; wheel/sdist and examples pending |
| F10 | Independent UAT and release readiness | IN PROGRESS | `UAT_LOG.md`, `RELEASE_READINESS.md` pending |

## Test evidence

| When | Command | Environment | Result |
|---|---|---|---|
| 2026-11-09 | `python3 -B -m unittest discover -s tests -t .` | macOS 15.6.1 arm64, Python 3.12.12, baseline `c796981` | 1584 tests OK, 23 skipped, 461 s |
| 2026-11-09 | `python3 -B -m unittest discover -s tests -t .` | same, G0 `6ef1bab` | 1596 tests, 2 failures in tool-description length (fixed after the run) |
| 2026-11-09 | `python3 -B tests/tools/mutation_check.py M921 M922 M923 M924 M925 M926` | same | 6/6 detected, 0 problems |
| 2026-11-09 | `python3 -B tests/tools/link_check.py` | same | 9 missing files (governance files created; re-run pending) |
| 2026-11-09 | `python3 -B tests/tools/docs_contract.py` | same | checks wired; evidence file pending |

`NOT_RUN` is not `PASS`. The final full-suite, mutation, link, docs-contract, wheel, and GitHub
Actions results are recorded in `RELEASE_READINESS.md` when they actually run.

## Commits

- `6ef1bab` — `fix(auth): enforce the coordinator lease inside the Kernel`
