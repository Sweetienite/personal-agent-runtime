# Historical evidence index (public archive notes)

The reports under `docs/reports/` were written against the author's local development copy. Their raw
evidence files are **not included in the public repository**: they contain the author's absolute local
paths and raw terminal output that was redacted only at display time. What is published here is this
index (purpose, byte size and SHA-256 of each original), plus one clearly-labelled placeholder per file
that a historical report still links to. The placeholders are not evidence and must not be read as PASS
claims; they only keep the historical links honest and resolvable.

| File | What it recorded | Bytes | SHA-256 of the original |
|---|---|---:|---|
| `P0_evidence_20261014.txt` | P0 install/doctor/test output | 14,256 | `c4af296de1bb2ed776a523e7ff2869c66f9b30d2ec021f34eefd52c5b8e979bb` |
| `P1_evidence_20261014.txt` | P1 project/Harness context tests | 19,356 | `e1a316261c576645b75bf9623b407c7bb4fb3718e989e6890b484d42bfddaaf6` |
| `P2_evidence_20261014.txt` | P2 Kernel/approval/run tests | 54,545 | `37d7f38aed3c31305a4122066c4c575af580340a195292f2bef22309205c9284` |
| `P3_evidence_20261014.txt` | P3 Codex adapter tests | 74,150 | `8a1582e6fa1c434deef32a687397d0e75a120433c5302b3873feb01d9c61109f` |
| `P3_smoke_evidence_20261014.txt` | P3 first real Codex R1/R2 smoke | 19,841 | `303d7d2d6d73277a723c6a13da7b9a0abc179327d59f1f75c738b00bb5de040d` |
| `P4_install_and_probe_evidence_20261015.txt` | Antigravity install/probe | 19,226 | `716ce8ffaad9dbce0fac0e4d131891c1c5a3b8e06959ffc7728bd3dc1b57053f` |
| `P4_smoke_evidence_20261015.txt` | Antigravity real smoke, part 1 | 12,783 | `c151cb368bc2f2db27964c5e07d47aa36db3be5ef10c6cca1cd9f62fd70b9e97` |
| `P4_smoke_evidence_20261015_part2.txt` | Antigravity real smoke, part 2 | 15,070 | `71cbd4f04e4456a0e3ea94ba806c51ae12275b5a08243ce20fe5137e310765cb` |
| `P8_first_real_write_run_20261015.txt` | first accepted real write run | 4,360 | `c3b9661e145d9158a79c9a3f2859c1a0b0ffa6da516137e80e3ab845c3b06e20` |
| `codex_mcp_kit_smoke_20261015.txt` | Codex MCP kit smoke | 1,002 | `9816f32320ea7b52a8abe3a18903afcb948dc1da7e167663783aeb3fcf378bde` |
| `facts_trae_workbuddy_20261015.md` | TRAE/WorkBuddy facts | 16,335 | `88e1ddb2e9021b8eea51f1d4dcc151be304797adc0e6e99988439c540d077576` |
| `gemini_cli_sunset_20261015.txt` | Gemini CLI sunset notes | 1,609 | `fb2d4083669d61a58e733f2dadd3291b56fbd0474163d51a509dc62fe99375a7` |
| `plan_digest_p5_p8_20261015.md` | P5-P8 plan digest | 31,902 | `9160eca1b6bffcba5771e230cc45abf0d32856e1706c28c20e98bc4e3531cb4e` |
| `restore_drill_20261016.txt` | backup/restore drill | 1,652 | `56419c7d4df5f6703f5b6f289a330579ab0371205198eff337d69acc91e2d007` |
| `t01_t24_matrix_20261016.md` | T01-T24 test matrix | 19,395 | `f16a2b6e88c5cd50153389487effed8d6e05a545d9ae6412bef8089ae8c4d5ef` |
| `uninstall_rehearsal_20261016.txt` | uninstall rehearsal | 2,983 | `1553cc9792e484dce656bb697d41e9dfc9fb4bec408ebe7591e149d5ff990ee0` |

If you have an original file, compare its SHA-256 with the row above before treating it as the same
artifact. The current, maintained evidence lives in `docs/reports/*20261107.md` and the per-task test
modules, not in this historical folder.
