# Finalization status

Branch: `release/final-productization`, based on `fix/p0-p3-audit-20261109` @ `c796981`
(which is one commit on top of `public-origin/main` @ `bc9cb0c`).

Working tree baseline for this task: clean except untracked `.ai/`, this package, and the audit
report. No user work was overwritten, reset or force-pushed.

| Card | Scope | Status | Evidence |
|---|---|---|---|
| F00 | Baseline, branch, status/gaps ledger | DONE | This file, `KNOWN_GAPS.md`, git log |
| F01 | Coordinator lease, grants, action policy | DONE | `6ef1bab`, `tests/unit/test_authorization_lease.py`, M921–M926; CI core_unittests PASS |
| F02 | Execution safety, candidate/check authorization, process control | DONE | `docs/SECURITY.md` action matrix; local 142 tests OK; GitHub `linux_process_control` PASS with 102 tests |
| F03 | Capability fact source and matrices | DONE | `docs/product-capabilities.json`, `docs/en/COMPATIBILITY.md`, `docs/COMPATIBILITY.md` |
| F04 | Bilingual README redesign | DONE | `README.md` (English), `README.zh-CN.md`, `README.en.md` pointer |
| F05 | Install / onboarding / first task | DONE (manual path verified); real-provider path documented | `docs/en/GETTING_STARTED.md`, `docs/QUICKSTART.md`, `UAT_LOG.md` |
| F06 | Integrations and manual workflows | DONE (docs); ≥3 paths reproduced in fake/manual environments | `docs/en/INTEGRATIONS.md`, `UAT_LOG.md` |
| F07 | Reference, security, architecture, troubleshooting | DONE | `docs/en/*`, `docs/INDEX*.md`, `docs/SECURITY.md` |
| F08 | CI, docs contract, links/anchors, deliberate-break proof | DONE | `tests/tools/docs_contract.py`, `tests/tools/link_check.py`, CI run [42591934521](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42591934521) |
| F09 | Packaging, governance, examples, changelog | DONE | wheel/sdist built and installed; governance files; `examples/` |
| F10 | Independent UAT and release readiness | DONE with limits | `UAT_LOG.md`, `RELEASE_READINESS.md`, `FINAL_REPORT.md`; independent readers not available |

## Test evidence

| When | Command | Environment | Result |
|---|---|---|---|
| 2026-11-09 | `python3 -B -m unittest discover -s tests -t .` | macOS 15.6.1 arm64, Python 3.12.12, baseline `c796981` | 1584 tests OK, 23 skipped, 461 s |
| 2026-11-09 | `python3 -B -m unittest discover -s tests -t .` | same, G0 `6ef1bab` | 1596 tests, 2 failures in tool-description length (fixed; final run below) |
| 2026-11-09 | `python3 -B -m unittest discover -s tests -t .` | same, `1cedd1c` + docs-only edits | **1596 tests OK, 23 skipped, 493 s** |
| 2026-11-09 | `python3 -B tests/tools/mutation_check.py` | same | **352/352 mutations detected, problems: 0** |
| 2026-11-09 | `python3 -B tests/tools/link_check.py` | same | 0 broken links/anchors |
| 2026-11-09 | `python3 -B tests/tools/docs_contract.py` | same | OK |
| 2026-11-09 | GitHub Actions PR #28 | run 42591934521 at `e473739` | all four jobs PASS: 150 + 102 + 384 tests and smoke install |

`NOT_RUN` is not `PASS`. The final full-suite, mutation, link, docs-contract, wheel, and GitHub
Actions results are recorded in `RELEASE_READINESS.md` when they actually run.

## Commits

- `6ef1bab` — `fix(auth): enforce the coordinator lease inside the Kernel`
- `1cedd1c` — `docs: publish the bilingual product entry and one capability fact source`
- final evidence commit: recorded in `RELEASE_READINESS.md` / PR #28
