# P0–P3 验收结果账单（2026-11-09）

本表把 `PAR_Implementation_Master_Plan_P0-P3_2026-11-06.zip` 的 59 项 Case 逐项落到可核对的证据上。
它是**结果账单，不是发行公告**：`PASS` 表示该项目标行为有本仓库可复核的证据，而不是整个产品在所有平台上就绪。

- **代码基线**：公开仓库 `Sweetienite/personal-agent-runtime` `main @ bc9cb0c`（2026-11-07），
  本账单所在分支 `fix/p0-p3-audit-20261109`。
- **版本口径（账单日期 2026-11-09）**：包版本 `1.0.0`（兼容版本，当时未打 tag）；`RELEASE_STATUS = untagged; P3 gate partial`。
  > 后续更新：Maintainer 于 2026-11-09 授权发布，`v1.0.0` 已打 tag 并创建 GitHub Release；本账单的历史结论不改写。
- **审查者**：2026-11-09 独立审计（离线源码 + Linux 隔离环境），以及本仓库维护者在本轮整改后的复核。
- **证据来源优先级**：项目内测试/报告 > 审计独立运行 > `STATE.md` 的口径（冲突时报告 `STATE_DRIFT_DETECTED`）。
- **状态含义**：
  - `PASS`：有可复核证据，且本轮未发现回归；
  - `FAIL`：有证据表明行为不满足 Case（本表当前无此项）；
  - `NOT_RUN`：没有在可核对的环境里执行过；
  - `BLOCKED`：被外部条件或明确的范围决策挡住（见 Notes）。
- **环境缩写**：
  - `macOS`：macOS 15.6.1 arm64，Python 3.13.5，本轮 `python3 -B -m unittest ...`；
  - `Linux-audit`：审计方的隔离 Linux，Python 3.13.5，`PYTHONPATH=src`；
  - `real-macOS`：2026-11-07 的仓库自述真实运行（Codex / WorkBuddy / 通用 Git），非本轮重演；
  - `static`：源码/文档/链接检查，不需要运行模型。

## 汇总

| 阶段 | 总数 | PASS | NOT_RUN | BLOCKED |
|---|---:|---:|---:|---:|
| P0 | 11 | 10 | 0 | 1 |
| P1 | 14 | 14 | 0 | 0 |
| P2 | 19 | 19 | 0 | 0 |
| P3 | 15 | 13 | 1 | 1 |
| **合计** | **59** | **56** | **1** | **2** |

已知缺口：`P0-08` 的 GitHub Actions 历史无法从离线快照独立复核；`P3-07` 的“新增正式自动 Provider”
按 2026-11-07 的收口决定为 `CLOSED_BY_SCOPE_DECISION`；`P3-12` 的 Windows 没有验证，Linux 本轮新增进程控制 CI 作业
但尚需真实 runner 结果；`P3-09` 的真实额度/取消事故没有强制制造。

## P0（11 项）

| Case | 环境 | 命令 / 证据 | 结果 | 状态 | Notes |
|---|---|---|---|---|---|
| P0-01 | macOS | `tests.unit.test_kernel_control` | exit 0 | PASS | pause→retry 复用原 pending，不重复申请 |
| P0-02 | macOS | `tests.unit.test_kernel_control` | exit 0 | PASS | WORKER_OVERRIDE 不重复 |
| P0-03 | macOS | `tests.unit.test_kernel_control` | exit 0 | PASS | R2 + override 只有两个不同 kind |
| P0-04 | macOS | `tests.unit.test_kernel_control` | exit 0 | PASS | 先批准一个再 pause→retry，保留另一个 |
| P0-05 | macOS | `tests.unit.test_kernel_control` | exit 0 | PASS | CANCEL 后 retry 不复活旧授权 |
| P0-06 | macOS | `tests.unit.test_kernel_control` / `test_kernel_policy` | exit 0 | PASS | 未批准 R2 不启动 Worker |
| P0-07 | macOS | `tests.unit.test_kernel_control` | exit 0 | PASS | 换 Worker 后旧 override 不被误用 |
| P0-08 | GitHub Actions | `.github/workflows/tests.yml`（`smoke_install` / `core_unittests` / `linux_security_core` / `linux_process_control`） | 配置存在，历史 run 不可见 | BLOCKED | 离线快照没有 `.git` 与 Actions artifacts；不把 badge 当作 59 项全过 |
| P0-09 | macOS + Linux | workflow 分层 + `linux_security_core` 子集 | 配置/审计记录 | PASS | 不为转绿而放宽生产权限；作业名称明确只覆盖子集 |
| P0-10 | Linux-audit | 全新隔离数据目录 `par init` / `doctor` / 假 CLI smoke | exit 0（doctor 5 PASS / 1 Linux WARN / 0 FAIL） | PASS | 陌生目录安装路径成立 |
| P0-11 | static | `python3 -B tests/tools/link_check.py`；README/pyproject/AGENTS 本轮核对 | 0 缺失链接 | PASS | 审批语义与发行口径已统一（见 F1/F4） |

## P1（14 项）

| Case | 环境 | 命令 / 证据 | 结果 | 状态 | Notes |
|---|---|---|---|---|---|
| P1-01 | macOS | `tests.unit.test_context_harness` / `test_p1_compat` | exit 0 | PASS | 旧 Harness fixture 行为不变 |
| P1-02 | macOS | `tests.unit.test_context_harness` | exit 0 | PASS | 已 hash 规则漂移在运行前拒绝 |
| P1-03 | Linux-audit | 空普通 Git 仓库 `par project add` | exit 0 | PASS | 无 Harness 注册成功且不写项目 |
| P1-04 | macOS | `tests.unit.test_generic_project` + `test_kernel_run` | exit 0 | PASS | generic R2 在私有副本执行并通过检查 |
| P1-05 | macOS | `tests.unit.test_kernel_policy` / `test_generic_project` | exit 0 | PASS | 未授权 R2 不执行、不扩路径 |
| P1-06 | macOS | `tests.unit.test_generic_project` | exit 0 | PASS | dirty / 无 Git 严格报错或受控回退 |
| P1-07 | macOS | `tests.unit.test_generic_project` | exit 0 | PASS | generic ad-hoc R2 可用，Harness 限制不退化 |
| P1-08 | macOS | `tests.unit.test_context_interface` | exit 0 | PASS | 无 README/AGENTS/STATE 仍生成最小上下文 |
| P1-09 | macOS | `tests.unit.test_context_harness` | exit 0 | PASS | AGENTS/CLAUDE 冲突给出来源警告，不自动定权 |
| P1-10 | macOS | `tests.unit.test_context_harness` | exit 0 | PASS | STATE 超限仍按 Harness 限制；generic 不强制 STATE |
| P1-11 | macOS | `tests.unit.test_context_harness` / `test_paths` | exit 0 | PASS | symlink/绝对引用/敏感文件不读取出带 |
| P1-12 | macOS | `tests.unit.test_p1_compat` | exit 0 | PASS | 旧 config 无 mode 维持 Harness |
| P1-13 | macOS | `tests.unit.test_kernel_recovery` / `test_p1_compat` | exit 0 | PASS | 旧 RUNNING/PAUSED/PERMISSION_WAIT 恢复不丢 |
| P1-14 | macOS | `tests.unit.test_p1_compat` / `test_config` | exit 0 | PASS | 坏字段/迁移中断明确拒绝，不默默变 generic |

## P2（19 项）

| Case | 环境 | 命令 / 证据 | 结果 | 状态 | Notes |
|---|---|---|---|---|---|
| P2-01 | macOS | `tests.unit.test_task_draft` | exit 0 | PASS | 简短草案输出严格 Canonical Contract |
| P2-02 | macOS | `tests.unit.test_task_draft` | exit 0 | PASS | 缺路径/检查时提问，不以 `.` 默认授权 |
| P2-03 | macOS | `tests.unit.test_task_draft` / `test_p1_compat` | exit 0 | PASS | 旧 contract schema/hash/运行兼容 |
| P2-04 | macOS | `tests.unit.test_task_draft` | exit 0 | PASS | README 恶意 shell check 不被自动纳入 |
| P2-05 | macOS | `tests.unit.test_mcp_tools` | exit 0 | PASS | 短摘要含待批准/下一步/失败原因，diff 可读 |
| P2-06 | macOS | `tests.unit.test_mcp_tools` / `test_approval_grants` | exit 0 | PASS | 越权入口不成功；`approval_mode` 与 grant 限定角色 |
| P2-07 | macOS | `tests.unit.test_mcp_tools` | exit 0 | PASS | 恶意长/深 JSON 结构校验与截断生效 |
| P2-08 | real-macOS | WorkBuddy 5.9.3 交接重跑（`docs/reports/WORKBUDDY_REAL_HANDOFF_20261107.md`） | `REVIEW_REQUIRED`，unit PASS，未应用 | PASS | 真实 GUI 运行，非本轮重演；报告属于项目自有证据 |
| P2-09 | macOS | `tests.unit.test_manual_handoff` | 48 项 exit 0 | PASS | 旧包/错 task 文件夹拒绝，原仓库不变 |
| P2-10 | macOS | `tests.unit.test_manual_handoff` | exit 0 | PASS | 允许路径外修改被 Kernel 拒绝 |
| P2-11 | macOS | `tests.unit.test_manual_handoff` | exit 0 | PASS | 文本任务以文件夹导入明确不兼容 |
| P2-12 | macOS | `tests.unit.test_evidence_summary` | exit 0 | PASS | Worker 说通过但检查失败 → 不可接受 |
| P2-13 | macOS | `tests.unit.test_evidence_summary` | exit 0 | PASS | 无检查命令明确 `NO_MECHANICAL_CHECK` |
| P2-14 | macOS | `tests.unit.test_evidence_summary` | exit 0 | PASS | 改测试文件被标记为 `inputs_edited`/`test_files_changed` |
| P2-15 | macOS | `tests.unit.test_evidence_summary` | exit 0 | PASS | 历史 result 缺字段默认 Unknown，非 PASS |
| P2-16 | macOS | `tests.unit.test_candidate_integration` | 21 项 exit 0 | PASS | B 基于已验证 candidate 看到 A 代码 |
| P2-17 | macOS | `tests.unit.test_candidate_integration` | exit 0 | PASS | A 接受但未集成时 B 不被误导 |
| P2-18 | macOS | `tests.unit.test_candidate_integration` | exit 0 | PASS | A/B 冲突保留证据、拒绝候选、原项目不变 |
| P2-19 | macOS | `tests.unit.test_candidate_integration` | exit 0 | PASS | 失败/中断/重试不产生伪 verified、不重复应用 |

## P3（15 项）

| Case | 环境 | 命令 / 证据 | 结果 | 状态 | Notes |
|---|---|---|---|---|---|
| P3-01 | macOS | `tests.unit.test_role_provider_caps` / `test_p1_compat` | exit 0 | PASS | 旧 Profile/Coordinator 配置名称仍有效 |
| P3-02 | macOS | `tests.unit.test_role_provider_caps` | exit 0 | PASS | 同 Provider 两角色会话/权限隔离，角色不硬编码 |
| P3-03 | macOS | `tests.unit.test_role_provider_caps` / `test_routing` | exit 0 | PASS | 不认识/不支持能力的 Provider 明确失败 |
| P3-04 | static | `docs/COMPATIBILITY.md` / `docs/ADAPTERS.md` | 逐项版本/官方证据/实测等级 | PASS | 研究层完成；未测项明确标注 |
| P3-05 | static | `docs/COMPATIBILITY.md`（ACP 未实现授权/取消） | 明确不视为正式可用 | PASS | 研究/实测边界清楚 |
| P3-06 | macOS | `tests.unit.test_cli_codex` / `test_cli_agy` / `test_no_real_calls` | exit 0 | PASS | 无登录/不存在 binary preflight 拒绝，不触发模型 |
| P3-07 | — | 原 Gate：新增一个正式自动 Provider | 未新增 | BLOCKED | 2026-11-07 用户收口为 `CLOSED_BY_SCOPE_DECISION`；现有 Codex `GREEN` 是旧路径复核，不能抵扣本 Case |
| P3-08 | macOS | `tests.unit.test_codex_kernel` / `test_agy_kernel` | exit 0 | PASS | 退出码 0 但无终态不虚构成功 |
| P3-09 | macOS | `tests.unit.test_codex_adapter` / `test_agy_adapter` / fake samples | 153/153 可控样本 | PASS | quota/429/auth/权限/timeout/cancel 分类正确；真实事故未强制制造 |
| P3-10 | static | `docs/SECURITY.md` + `tests.unit.test_p3_security_audit` | exit 0 | PASS | 文档明确私有副本不是 OS 沙箱；检查命令以宿主身份执行 |
| P3-11 | macOS | `tests.unit.test_p3_security_audit` / `test_paths` | exit 0 | PASS | 脚本副作用/恶意路径边界拒绝或报告 |
| P3-12 | macOS / Linux / Windows | `tests.unit.test_kernel_control`（macOS）；新增 `linux_process_control` CI 作业；Windows 无 runner | macOS exit 0；Linux 待真实 CI | NOT_RUN | macOS `E2E`；Linux `CORE_EXPERIMENTAL`（本轮补进程控制作业）；Windows `STATIC_GAP` |
| P3-13 | Linux-audit | 陌生目录零账号 fake smoke | exit 0 | PASS | QUICKSTART 路径可执行 |
| P3-14 | Linux-audit + real-macOS | 普通 Git 注册；WorkBuddy 交接报告 | 注册 exit 0；交接 `REVIEW_REQUIRED` | PASS | 两项独立流程均有证据 |
| P3-15 | static | `link_check.py` 与 `--historical`；中英 README/QUICKSTART/MODES/CHANGELOG/RELEASE_NOTES | 0 缺失链接 | PASS | 本轮统一了审批模式、发行状态与历史归档注释 |

## 未达成的 Gate（不得被其它 PASS 抵消）

1. **T14 / P3-07**：没有新增正式自动 Provider。这是用户的明确范围决策，不是缺陷修复；产品口径已改为
   “Codex 自动 + 人工交接”，不再宣称多厂商自动执行。
2. **P0-08 / P3-12 / P3-09 的真实环境部分**：CI 历史、Windows、真实额度/取消事故没有独立证据；相应
   Case 保持 `BLOCKED` / `NOT_RUN`，不用“全量单测通过”抵消。
3. **发行（截至账单日期）**：没有 tag、没有 GitHub Release。`1.0.0` 只是兼容包版本；是否发布需要 Maintainer 单独决定。后续 2026-11-09 已获授权并发布 `v1.0.0`（见文首更新）。
