# Release readiness

Status vocabulary: `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED`. A configuration existing is not a PASS.
`NOT_RUN` is not `PASS`. This file is updated only from actual commands and run records.

HEAD at the start of this ledger: `6ef1bab` plus the uncommitted productization/docs commit that
will follow. The release commit and merged `main` SHA are recorded in the final run records below.

CI runs for PR #28:
- code/workflow commit `e473739`: run
  [42591934521](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42591934521) —
  all four jobs PASS: `smoke_install` 13 s, `linux_security_core` 11 s with **150 tests OK** plus
  `0 broken relative link or anchor(s)` and `docs_contract: OK`, `linux_process_control` 1 m 9 s
  with **102 tests OK**, `core_unittests` 3 m 29 s with **384 tests OK**.
- final docs-only commit `67ea705`: run
  [42593601356](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42593601356) —
  all four jobs PASS (smoke_install 10 s, linux_security_core 11 s with docs checks,
  linux_process_control 1 m 6 s, core_unittests 3 m 11 s).

## Gate A — security and integrity

| ID | Case | Status | Evidence |
|---|---|---|---|
| SEC-01 | Switch coordinator Claude→Codex; old session tries approve/reject/accept/close | PASS | `tests/unit/test_authorization_lease.py::test_the_replaced_coordinator_cannot_approve_reject_accept_or_close` (ledger has no decision events; statuses unchanged) |
| SEC-02 | `coordinator_scoped` project/risk/kind/action mismatch | PASS | `test_approval_grants.py`, `test_authorization_lease.py::test_a_grant_for_another_project_or_risk_does_not_cover_this_task` |
| SEC-03 | Grant expired/revoked/duplicate/concurrent | PASS | `test_approval_grants.py::test_a_grant_expires...`, `test_authorization_lease.py::test_authorization_is_checked_again_inside_the_write_transaction` |
| SEC-04 | `coordinator_autonomous` after a switch still refuses the old lease | PASS | `test_autonomous_mode_still_requires_the_current_lease` |
| SEC-05 | R2 not write-enabled, outside paths, protected paths, symlink/absolute escape, dirty repo | PASS | `test_policy.py`, `test_workspace.py`, `test_kernel_policy.py`, `test_p3_security_audit.py` |
| SEC-06 | Worker claims tests pass, but Kernel check fails/does not exist | PASS | `test_evidence_summary.py`, `test_kernel_run.py`, `test_validation_results.py` |
| SEC-07 | Candidate patch tampered or missing provenance | PASS | `test_candidate_integration.py` (hash mismatch, missing provenance), `M915` |
| SEC-08 | Cancel/timeout/recovery process groups | PASS on macOS and Ubuntu | local 142 tests OK; GitHub `linux_process_control` PASS with **102 tests** ([job](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42591934521/job/129075381799)) |
| SEC-09 | MCP can trigger candidate/check code | PASS (scoped, documented) | `candidate_create` requires current lease, records `actor`, warning in tool description and `docs/SECURITY.md` action matrix; tests in `test_authorization_lease.py` |
| SEC-10 | `human_only` wording vs host shell | PASS | `docs/SECURITY.md` §审批模式 and `docs/en/SECURITY.md` §5 explicitly deny OS-level identity |
| SEC-11 | Provider account/permission/quota failure | PASS | Adapter tests, `test_adapters`, `par health`; no auto credit purchase or privilege escalation |
| SEC-12 | `git apply`/push/deploy/automatic project write | PASS | `applied_to_project=false` paths, candidate tests; no apply code exists |

## Gate B — real user journeys

| ID | Journey | Status | Evidence |
|---|---|---|---|
| UAT-00 | Fresh clone + wheel install; doctor/version; providers disabled | PASS | fresh clone of `1cedd1c` in `/tmp/par_final_clone`: `uv pip install -e .`, `par init`, `doctor` 5 PASS, fake smoke exit 0; release wheel `personal_agent_runtime-1.1.0-py3-none-any.whl` installed in `/tmp/par_release_wheel`, `par version --json` → `1.1.0` released 2026-11-09 |
| UAT-01 | No account, isolated data dir, fake R1/R2 smoke | PASS | `smoke_codex.py --self-test` exit 0 (manual path also verified); full output in final run log |
| UAT-02 | Ordinary Git project with no AGENTS/STATE | PASS (registration/readiness), R2 real run NOT_RUN | `par project add myrepo` generic, writes blocked; real R2 documented from 2026-11-07 evidence, not re-run |
| UAT-03 | Draft → contract → R2 → diff/review/accept | PASS | `UAT_LOG.md` UAT-A (manual hand-off; full lifecycle) |
| UAT-04 | Export WorkBuddy/other GUI → human work → import | PASS for the zero-account manual packet; real WorkBuddy NOT_RUN this round | `UAT_LOG.md` UAT-A; 2026-11-07 WorkBuddy report referenced |
| UAT-05 | Text-only client R0/R1 | PASS by documented/tested text path; no brand test | `test_manual_handoff.py`, docs |
| UAT-06 | A → verified candidate → B | PASS (tests) | `test_candidate_integration.py` |
| UAT-07 | Login/version mismatch, quota, timeout, permission wait, check failure, recovery | PASS (scripted), real incidents NOT_RUN | Failure-classification tests; documented |
| UAT-08 | Same tool as coordinator and worker, one subscription | PASS (design + docs); real session NOT_RUN | `docs/en/INTEGRATIONS.md` §2, `test_role_provider_caps.py` |
| UAT-09 | Switch coordinator; new reads packet; old cannot decide | PASS | `test_authorization_lease.py`, `test_takeover.py` |
| UAT-10 | English/Chinese independent README demo | PASS (manual checklist); independent reader not available | Language switch, 5-question checklist below |
| UAT-11 | Linux install/fake/process control | PASS (CI install + security + process control); full fake E2E NOT_RUN | `linux_security_core` 150 tests and `linux_process_control` 102 tests PASS ([run](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42591934521)); no full Linux fake CLI end-to-end |
| UAT-12 | Windows native / WSL2 | NOT_RUN / NOT_SUPPORTED | Matrix is honest; not a claimed feature |

## Gate C — docs and developer experience

| ID | Check | Status | Evidence |
|---|---|---|---|
| DOC-01 | English main README + Chinese mirror | PASS | `README.md`, `README.zh-CN.md`, language switch both ways |
| DOC-02 | 60-second Mermaid | PASS | Both READMEs contain a Mermaid block; GitHub render not yet manually checked |
| DOC-03 | Native/Protocol/Manual workflows and boundaries | PASS | `docs/en/INTEGRATIONS.md`, README table |
| DOC-04 | AI-tool and OS matrices, separate | PASS | `docs/en/COMPATIBILITY.md` §2/§3, `docs/COMPATIBILITY.md` host matrix |
| DOC-05 | README/install/CLI examples from a clean clone | PASS (manual path at current HEAD); final clean clone pending | `UAT_LOG.md`; commands re-run after final commit |
| DOC-06 | Bilingual core facts agree | PASS | `docs_contract.py` (version, status, tool count, mode, status words, lease in both languages) |
| DOC-07 | Relative links + anchors, 0 broken | PASS | `link_check.py` → 0 broken |
| DOC-08 | Markdown table columns/fences/Mermaid | PASS | `docs_contract.py` table check over READMEs and matrices |
| DOC-09 | Security/troubleshooting before high-risk actions | PASS | `docs/en/SECURITY.md`, `docs/en/TROUBLESHOOTING.md`, warnings in Quickstart |
| DOC-10 | Deliberate docs break makes CI/check red | PASS | Changed README tool count to 20 → `docs_contract` exit 1; added a bad anchor → `link_check` exit 1; both reverted |
| DOC-11 | Contributing/support/issue/PR/security | PASS | `CONTRIBUTING.md`, `SUPPORT.md`, `.github/ISSUE_TEMPLATE/*`, `.github/pull_request_template.md`, `.github/SECURITY.md` |
| DOC-12 | Secrets/privacy audit | PASS for current docs | No `/Users/` in READMEs/English docs; example has no credentials; historical reports untouched |

## Gate D — CI, packaging, release

| ID | Item | Status | Evidence |
|---|---|---|---|
| REL-01 | The PR's CI actually runs | PASS | PR #28, run [42591934521](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42591934521), all four jobs PASS at `e473739` |
| REL-02 | Full suite in a controlled environment | PASS locally; CI scope documented | **1596 tests OK, 23 skipped, 493 s** (macOS 15.6.1 arm64, Python 3.12.12, `1cedd1c` + docs-only edits); CI runs the named subsets, not the full suite |
| REL-03 | Mutation tests | PASS | full run: **352/352 mutations detected, problems: 0**; M921–M926 and M227/M620 verified KILLED |
| REL-04 | macOS + Linux matrix | PASS for the claimed levels | Local macOS; Linux `linux_security_core` + `linux_process_control` PASS; no Linux real-provider claim |
| REL-05 | wheel + sdist clean install | PASS | `/tmp/par_dist_final` built from the clean clone; wheel installed in a fresh venv; `par version`/`doctor` OK; sdist contains docs (49 entries) |
| REL-06 | version/CLI/changelog/release notes agree | PASS | `par version` → `1.1.0`, `released 2026-11-09; P3 gate partial`; CHANGELOG heading, README status lines, compatibility page and release notes use the same wording |
| REL-07 | Open-source governance | PASS | LICENSE, SECURITY, CONTRIBUTING, SUPPORT, CODE_OF_CONDUCT, issue/PR templates |
| REL-08 | Clean restore/uninstall | PASS (documented + fixture check) | `GETTING_STARTED.md` §6; fixture `git status` empty after accept |
| REL-09 | Rollback | PASS (existing procedure) | `docs/ROLLBACK.md`; no destructive migration |
| REL-10 | Maintainer release decision | PASS for tag/Release; PyPI BLOCKED by missing credentials | Maintainer authorized merge/tag/Release/PyPI on 2026-11-09. The existing `v1.0.0` tag (2026-11-03) was left untouched; this release is `v1.1.0`. PyPI upload could not run: no `~/.pypirc`, no `TWINE_*`/`UV_PUBLISH_TOKEN` credentials, no `twine` binary. Recorded as BLOCKED, not PASS. |

## Five-question readability check

1. What is it? A local, verifiable task runtime for AI coding agents; the Kernel re-runs checks.
2. How do I connect? Native worker (Codex), MCP coordinator, or manual hand-off.
3. Who approves? The profile's `approval_mode` inside the current coordinator lease; `human_only` is CLI-only.
4. Does it change my repo automatically? No; `accept` records a diff, and you run `git apply`.
5. Which environments are really usable? macOS verified; Codex is the only formal automatic worker;
   Linux is core-experimental; Windows native is not supported.

## Final run records

- HEAD: `67ea705` (final docs/evidence tree; code and workflow are `e473739`).
- Full suite: 1596 tests OK, 23 skipped, 493 s (macOS 15.6.1 arm64, Python 3.12.12).
- Mutation: **352/352 detected, 0 problems** (full run, exit 0).
- link_check: 0 broken relative links or anchors.
- docs_contract: OK.
- Clean clone + wheel: PASS (`/tmp/par_final_clone`, `/tmp/par_dist_final`, `/tmp/par_wheel_final`).
- GitHub Actions: runs [42591934521](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42591934521) and [42593601356](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42593601356) — all four jobs PASS.
