# Known gaps and audit findings

Items found by the 2026-11-09 independent audit / productization package. `FIXED` means this branch
has a code/test/doc change; `OPEN` means it still blocks or limits the release.

| ID | Finding | State | Where |
|---|---|---|---|
| G01 | A replaced MCP coordinator could still approve/reject/accept/close under `coordinator_autonomous`; the Kernel decision path never checked the lease | FIXED `6ef1bab` | `core/approvals.py`, `test_authorization_lease.py`, M921 |
| G02 | A bridge could resume/retry/terminate/create-candidate/rebase after being replaced | FIXED `6ef1bab` | `mcp/tools.py`, `core/candidate.py`, M924–M926 |
| G03 | Decision authorization was checked before, not inside, the write transaction | FIXED `6ef1bab` | `approve`/`reject`/`close` re-check in `store.tx()` |
| G04 | `human_only` wording implied an unforgeable human identity / OS boundary | FIXED (docs) | `docs/SECURITY.md`, `docs/en/SECURITY.md` |
| G05 | Default `coordinator_autonomous` was not announced at init | FIXED | `cli.py` `init` output; Quickstart/README |
| G06 | `create_candidate` runs repository check commands under the coordinator's call with no explicit warning | FIXED (lease + actor + docs/tool description); no separate human gate by design | `core/candidate.py`, `mcp/catalog.py`, `docs/SECURITY.md` action matrix |
| G07 | Chinese README had command-table bloat, markdown table defects, an outdated English-CI job count and bad anchors | FIXED (README rewritten; tables/links contract-tested) | `README.zh-CN.md`, `tests/tools/docs_contract.py`, `link_check.py` |
| G08 | Quickstart mixed `python3` with the venv interpreter and did not isolate `PAR_DATA_DIR` | FIXED | `README*.md`, `docs/QUICKSTART.md`, `docs/en/GETTING_STARTED.md` |
| G09 | No English onboarding/reference set; English Quickstart pointed at Chinese only | FIXED | `docs/en/*`, `docs/INDEX.md` |
| G10 | No machine-readable capability source; docs facts drifted | FIXED | `docs/product-capabilities.json`, `docs_contract.py` |
| G11 | Link checker did not verify anchors, wrong case, or the legacy README redirect | FIXED | `tests/tools/link_check.py`, docs_contract |
| G12 | Linux cancellation/process-group behavior had never been observed on a real GitHub runner | OPEN until the F08 CI run; `NOT_RUN` is not `PASS` | `.github/workflows/tests.yml` `linux_process_control` |
| G13 | Windows native / WSL2 were never run | OPEN by scope; documented `NOT_SUPPORTED` / `NOT_TESTED` | compatibility matrix |
| G14 | Real cancellation/timeout/quota incidents were not forced | OPEN by evidence scope; documented | `docs/COMPATIBILITY.md` |
| G15 | Real Claude/Codex MCP sessions only exercised read-only `list_tasks`; submit/decision tools are scripted-tested | OPEN by evidence scope; documented | `docs/en/COMPATIBILITY.md` |
| G16 | `state.md`/plans referenced old SHAs; the local private `main` is not the public history | CHECKED; branch based on the real public-origin HEAD | `.ai/STATE.md` read-only |
| G17 | Version `1.0.0` could be misread as a published release | FIXED in wording: untagged RC, release_status printed by `par version`/`doctor`, docs repeated | README, `docs/en/COMPATIBILITY.md` |
| G18 | The old README command table was the main product page | FIXED: moved to `docs/en/USER_GUIDE.md` / `docs/OPERATIONS.md` | README |
| G19 | No governance files (support, conduct, issue/PR templates, root security pointer) | FIXED | `SUPPORT.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md` |
| G20 | No non-editable wheel/sdist install verification | OPEN until F09 runs it | `finalization/RELEASE_READINESS.md` |
| G21 | `README.en.md` was a full second English README that could drift | FIXED: now a compatibility pointer | `README.en.md` |
| G22 | Real Claude Desktop connection is user-level config, which exposes the tools to every project of that client | DOCUMENTED limitation | `docs/COORDINATORS.md`, `docs/en/INTEGRATIONS.md` |
