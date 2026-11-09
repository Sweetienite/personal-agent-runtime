# Final report — 2026-11-09 productization round

Status: **released as `1.0.0` (`v1.0.0`) on 2026-11-09** under the Maintainer's explicit
authorization: PR #28 merged, tag and GitHub Release created. PyPI upload is `BLOCKED` on missing
credentials and was not fabricated as a success.

## 1. What was delivered

| Area | Deliverable |
|---|---|
| Authorization | Kernel-enforced coordinator lease for approve/reject/accept/close in every `approval_mode`, re-checked inside the write transaction; stale bridges cannot resume/retry/terminate/create-candidate/rebase. `par init` states the default approval mode. |
| Execution safety | Candidate creation is explicitly documented as executing repository checks under the user's permissions and is lease-guarded; the security action matrix is published in both languages. |
| Product facts | `docs/product-capabilities.json` is the single conservative source; `tests/tools/docs_contract.py` checks it against version, release status, tool count, adapter manifests, CI jobs and the READMEs. |
| Product entry | `README.md` (English), `README.zh-CN.md` (Chinese), `README.en.md` (compatibility pointer), 60-second Mermaid diagram, three connection routes, two-dimensional compatibility summary. |
| Onboarding | `docs/en/GETTING_STARTED.md`, `docs/en/USER_GUIDE.md`, `docs/en/INTEGRATIONS.md`, `docs/en/COMPATIBILITY.md`, `docs/en/SECURITY.md`, `docs/en/ARCHITECTURE.md`, `docs/en/TROUBLESHOOTING.md`; updated Chinese Quickstart, index and security docs. |
| Docs quality | Anchor- and case-aware `tests/tools/link_check.py`, `docs_contract`, stable README sections and table checks; deliberate-break proof recorded. |
| Packaging / governance | Wheel + sdist built and installed in a fresh venv; `MANIFEST.in`; `SUPPORT.md`, `CODE_OF_CONDUCT.md`, root `SECURITY.md`, issue/PR templates, `examples/`, CHANGELOG/release notes. |
| Evidence ledger | `finalization/STATUS.md`, `KNOWN_GAPS.md`, `UAT_LOG.md`, `RELEASE_READINESS.md`, this report. |

## 2. Traceability: 2026-11-09 audit → this round

| Finding | Fix | Evidence |
|---|---|---|
| Stale coordinator could decide | `_current_coordinator` + in-transaction re-check | `6ef1bab`, `test_authorization_lease.py`, M921–M923 |
| Stale bridge could start/resume/terminate/candidate/rebase | lease guard at Kernel/MCP | `6ef1bab`, M924–M926 |
| `human_only` over-claimed identity | docs corrected | `docs/SECURITY.md`, `docs/en/SECURITY.md` |
| Default autonomy not announced | `par init` notice, README/Quickstart | `6ef1bab`, `1cedd1c` |
| README/docs drift/tables/anchors | rewrite + contract checks | `1cedd1c`, docs_contract/link_check |
| No English onboarding | `docs/en/*` | `1cedd1c` |
| No capability source | `docs/product-capabilities.json` | `1cedd1c` |
| Linux process control unverified | real GitHub job PASS | Actions run 42591934521 (102 tests) |
| Windows/WSL2 unverified | documented `NOT_SUPPORTED` / `NOT_TESTED` | compatibility JSON/matrix |
| No wheel test | wheel built and installed | `/tmp/par_dist_final`, `/tmp/par_wheel_final` |

## 3. Verification snapshot

- Legacy ledger: [acceptance/P0-P3-results.md](../acceptance/P0-P3-results.md) remains 56 PASS / 1
  NOT_RUN / 2 BLOCKED; this round did not rewrite its historical results. New F00–F10 assertions
  map to the numbered gates in `RELEASE_READINESS.md`.
- Full suite: **recorded in `RELEASE_READINESS.md`** (local run at the final code state).
- CI: PR #28, runs [42591934521](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42591934521) (code/workflow `e473739`) and [42593601356](https://github.com/Sweetienite/personal-agent-runtime/actions/runs/42593601356) (final docs tree `67ea705`) — all four jobs PASS: smoke_install, linux_security_core (150 tests + docs checks), linux_process_control (102 tests), core_unittests (384 tests).
- Mutation: **352/352 detected, 0 problems** (full run, exit 0); M921–M926 and M227/M620 are KILLED.
- Docs: `link_check.py` 0 broken; `docs_contract.py` OK; deliberate stale tool count and bad anchor both turned checks red and were reverted.
- Package: wheel installs in a clean venv; `par version/doctor` correct; sdist contains docs/examples/tests.
- Zero-account lifecycle: `UAT_LOG.md` UAT-A (export → human edit → import → Kernel check → accept; fixture untouched).

## 4. Unverified or blocked (do not read as PASS)

- **Windows native**: `NOT_SUPPORTED`; **WSL2**: `NOT_TESTED`.
- **Linux real provider / full fake E2E**: not run; Linux is `CORE_EXPERIMENTAL`.
- **Real Codex R1/R2 in this round**: not run; the 2026-11-07 evidence is referenced, not re-labelled.
- **Real Claude Desktop / Codex MCP decision sessions**: not run; the 2026-10-16 read-only `list_tasks` sample is referenced. Submit/decide tools are scripted-tested.
- **Real WorkBuddy GUI in this round**: not run; the 2026-11-07 sample is referenced.
- **Independent stranger readers**: not available. One maintainer blind pass was performed; independent review is still pending, and is not fabricated.
- **Real cancellation/timeout/quota incidents**: not forced; controllable samples cover the paths.
- **PyPI**: `BLOCKED` — no `~/.pypirc`, no `TWINE_*`/`UV_PUBLISH_TOKEN`, no `twine`. The wheel/sdist
  are built and verified locally; the upload needs the Maintainer's credentials.
- **Authorization**: merge, tag, GitHub Release and the PyPI attempt were explicitly authorized by
  the Maintainer on 2026-11-09; no unapproved release action was taken.

## 5. Release recommendation

**Released as a scoped, honest `1.0.0`**, with these non-negotiable claims:

- Codex is the only formal automatic worker, tied to `codex-cli 0.166.0-alpha.6`, macOS, ChatGPT
  login, 2026-11-07.
- MCP is a coordinator interface; only read-only real-client samples exist.
- Manual hand-off is a first-class path; WorkBuddy is the only verified brand sample (2026-11-07).
- macOS is verified; Linux is core-experimental; Windows is not supported.
- `human_only` is not OS-level identity; no automatic repository apply exists.

**NO-GO for any wording that claims all platforms/tools are supported, or any future tag/Release
action without the Maintainer's explicit approval.**

## 6. Release execution record

1. PR #28 reviewed and merged to `main` on 2026-11-09 under the Maintainer's explicit authorization.
2. Release metadata was aligned before the merge: `RELEASE_STATUS`, `product-capabilities.json`,
   both READMEs, compatibility page, getting-started, quickstart, CHANGELOG, release notes and this
   ledger all say `released 2026-11-09; P3 gate partial`.
3. `docs_contract.py` was re-run after the metadata change.
4. Tag `v1.0.0` and the GitHub Release were created from the merged `main`.
5. PyPI upload was attempted only as far as the environment allowed: no credentials and no `twine`
   are present, so it is recorded as `BLOCKED`, not PASS.
6. Any further independent-reader feedback belongs in `finalization/UAT_LOG.md`; do not upgrade
   `VERIFIED_*` labels without a dated run.
