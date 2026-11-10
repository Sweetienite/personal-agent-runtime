# User acceptance log (2026-11-09)

All commands below ran on macOS 15.6.1 arm64, Python 3.12.12, from the
`release/final-productization` checkout, with an isolated data directory. No real model call, no
paid quota, no real project was touched.

## UAT-A: zero-account manual hand-off (complete lifecycle)

Data directory: `/tmp/par_uat_data` (created fresh). Fixture: `demo`.

| Step | Command | Expected | Actual | Exit |
|---|---|---|---|---|
| init | `PAR_DATA_DIR=/tmp/par_uat_data par init` | create `0700` dir, print approval-mode notice | created, notice printed | 0 |
| doctor | `PAR_DATA_DIR=... par doctor` | 5 PASS, providers disabled | `PASS 5 \| WARN 0 \| FAIL 0 \| INFO 8` | 0 |
| fixture | `par fixture create demo` | writable fixture with a deliberate bug | created at `/tmp/par_uat_data/fixtures/demo`, commit `a53a3408d859` | 0 |
| profile | write `config/profiles.toml` with `manual_handoff` worker, `human_only` | valid config | `doctor` still 5 PASS | 0 |
| draft | `par task draft --draft /tmp/par_uat_draft.json` | strict contract, no submit | printed contract, role `code`, derived idempotency key | 0 |
| submit | `par task submit --contract task.json` | `PERMISSION_WAIT` | `submitted demo-0001 PERMISSION_WAIT` | 0 |
| approve | `par task approve demo-0001` | `QUEUED` | `approve: demo-0001 QUEUED` | 0 |
| handoff | `par task handoff demo-0001 --to workbuddy --json` | workspace packet, `PAUSED` | `mode: workspace`, absolute workspace, base `a53a3408d859...`, 4 next actions | 0 |
| external edit | change `workspace/app/calc.py` to `return a + b` | — | done by the tester | — |
| import | `par task import demo-0001 --from "<workspace>"` | Kernel re-check, `REVIEW_REQUIRED` | `REVIEW_REQUIRED`, `1 Kernel check(s) passed` | 0 |
| diff | `par task diff demo-0001` | one-line patch | `-    return a - b` / `+    return a + b` | 0 |
| accept | `par task accept demo-0001` | `ACCEPTED` | `accept: demo-0001 ACCEPTED` | 0 |
| source check | `git -C /tmp/par_uat_data/fixtures/demo status --porcelain` | empty | empty (fixture untouched) | 0 |

No real project was registered or modified. `applied_to_project=false` throughout.

## UAT-B: negative cases observed in tests

The following are covered by automated tests rather than by hand:

- stale coordinator approve/reject/accept/close: `tests/unit/test_authorization_lease.py` (12 tests).
- grant project/risk/kind/expiry/revocation mismatch: `test_approval_grants.py` and
  `test_authorization_lease.py`.
- tampered patch / missing provenance: `test_candidate_integration.py`.
- wrong hand-off folder, stale packet, forged marker: `test_manual_handoff.py`.
- worker claim does not become a verified status: `test_evidence_summary.py`,
  `test_kernel_run.py`.

## UAT-C: fresh clone and package (2026-11-09)

| Step | Command | Result |
|---|---|---|
| clean clone | `git clone <local checkout> /tmp/par_final_clone` at `1cedd1c` | clean checkout |
| install | `uv venv --python 3.12 .venv && uv pip install -e .` | installed `personal-agent-runtime==1.0.0` |
| doctor | `PAR_DATA_DIR=/tmp/par_final_data .venv/bin/par doctor` | `PASS 5 \| WARN 0 \| FAIL 0`, providers disabled |
| smoke | `.venv/bin/python -B tests/tools/smoke_codex.py --self-test` | exit 0, fake R1/R2, no model, no network |
| docs | `link_check.py`; `docs_contract.py` | 0 broken; OK |
| build | `python3 -m build --no-isolation --outdir /tmp/par_dist_final` | wheel + sdist |
| wheel install | fresh `uv` venv + wheel | `par version --json` → 1.0.0; `doctor` 5 PASS |
| sdist | `tar -tzf` | includes docs (49 entries), examples, tests; no private data |

## UAT-E: post-release audit verification (2026-11-10, `1.1.1`)

| Check | Command | Result |
|---|---|---|
| example, pre-fix | contract `argv` in a copy of `examples/ordinary-git-project` | exit non-zero, `Ran 1 test` (not `Ran 0 tests`) |
| example, post-fix | same command after `return a + b` | exit 0, `Ran 1 test` |
| onboarding config | temporary data dir + §5 `profiles.toml`/`providers.toml` | `par doctor` reads the Codex profile; no model call |
| onboarding contract | temporary Git repo + `myproject`, `ad_hoc=true`, unique key | `task submit --check` → `contract passes validation; nothing was submitted` |
| CLI docs | `docs_cli_examples.py` | 120 documented `par` lines accepted by the real parser |
| version output | `par version --json` | `1.1.1`, `released 2026-11-09`, `support_tier=scoped`, routes `codex_native`/`manual_handoff` |

## UAT-D: not run or not claimed

- Linux install/fake run/process control on a real GitHub runner: install + security + process
  control PASS (run 42588469528); a full Linux fake CLI end-to-end is **not run**.
- Windows native and WSL2: `NOT_TESTED`; not claimed.
- Real Codex R1/R2: not re-run in this round; the dated 2026-11-07 evidence is referenced, not
  re-labelled as new.
- Real Claude Desktop / Codex MCP submit/decision calls: scripted tests only; the 2026-10-16
  read-only sample is referenced.
- Real WorkBuddy GUI: not re-run this round; the 2026-11-07 sample is referenced. The zero-account
  manual path above used a human edit by the tester, not WorkBuddy.
