# Release readiness

Status vocabulary: `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED`. A configuration existing is not a PASS.
`NOT_RUN` is not `PASS`. This file is updated only from actual commands and run records.

HEAD at the start of this ledger: `6ef1bab` plus the uncommitted productization/docs commit that
will follow. The final SHA is recorded here after the commit.

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
| SEC-08 | Cancel/timeout/recovery process groups | PASS on macOS; `NOT_RUN` on Ubuntu | local `test_kernel_control`, `test_validation_results`, `test_subprocesses`, `test_kernel_recovery` → 142 tests OK; GitHub `linux_process_control` pending |
| SEC-09 | MCP can trigger candidate/check code | PASS (scoped, documented) | `candidate_create` requires current lease, records `actor`, warning in tool description and `docs/SECURITY.md` action matrix; tests in `test_authorization_lease.py` |
| SEC-10 | `human_only` wording vs host shell | PASS | `docs/SECURITY.md` §审批模式 and `docs/en/SECURITY.md` §5 explicitly deny OS-level identity |
| SEC-11 | Provider account/permission/quota failure | PASS | Adapter tests, `test_adapters`, `par health`; no auto credit purchase or privilege escalation |
| SEC-12 | `git apply`/push/deploy/automatic project write | PASS | `applied_to_project=false` paths, candidate tests; no apply code exists |

## Gate B — real user journeys

| ID | Journey | Status | Evidence |
|---|---|---|---|
| UAT-00 | Fresh clone + wheel install; doctor/version; providers disabled | PASS (wheel); clean clone pending final commit | `/tmp/par_wheel_venv` from `/tmp/par_dist/*.whl`: `par version` 1.0.0, `doctor` 5 PASS, providers disabled |
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
| UAT-11 | Linux install/fake/process control | NOT_RUN | Pending GitHub Actions |
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
| REL-01 | The PR's CI actually runs | NOT_RUN | Branch not pushed yet |
| REL-02 | Full suite in a controlled environment | PASS locally; CI scope documented | Final run recorded below |
| REL-03 | Mutation tests | PASS locally on new entries; full run pending | M921–M926 → 6/6 detected, 0 problems; full 346+ pending |
| REL-04 | macOS + Linux matrix | macOS PASS; Linux NOT_RUN | Local macOS; GitHub Linux pending |
| REL-05 | wheel + sdist clean install | PASS | `/tmp/par_dist` built; wheel installed in a fresh `uv` venv; `par version`/`doctor` OK |
| REL-06 | version/CLI/changelog/release notes agree | PASS | `par version` → `1.0.0`, untagged partial; CHANGELOG and release notes use the same wording |
| REL-07 | Open-source governance | PASS | LICENSE, SECURITY, CONTRIBUTING, SUPPORT, CODE_OF_CONDUCT, issue/PR templates |
| REL-08 | Clean restore/uninstall | PASS (documented + fixture check) | `GETTING_STARTED.md` §6; fixture `git status` empty after accept |
| REL-09 | Rollback | PASS (existing procedure) | `docs/ROLLBACK.md`; no destructive migration |
| REL-10 | Maintainer release decision | BLOCKED | Requires explicit approval; no tag/Release/PyPI was created |

## Five-question readability check

1. What is it? A local, verifiable task runtime for AI coding agents; the Kernel re-runs checks.
2. How do I connect? Native worker (Codex), MCP coordinator, or manual hand-off.
3. Who approves? The profile's `approval_mode` inside the current coordinator lease; `human_only` is CLI-only.
4. Does it change my repo automatically? No; `accept` records a diff, and you run `git apply`.
5. Which environments are really usable? macOS verified; Codex is the only formal automatic worker;
   Linux is core-experimental; Windows native is not supported.

## Final run records

Filled after the final commit and CI run:

- HEAD:
- Full suite:
- Mutation:
- link_check:
- docs_contract:
- Clean clone + wheel:
- GitHub Actions URLs:
