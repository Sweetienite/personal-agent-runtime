# 发行审查清单（P3/T16，2026-11-06）

本文是**审查结论**，不是发行公告。实际打 tag / 创建 GitHub Release 需要用户单独批准（任务卡明确要求），本轮没有执行。

## 结论：**不建议现在打新 tag / 发新版**

理由（按重要性）：

1. **T14 = `BLOCKED_BY_EVIDENCE`**（Issue #16）：T13 的兼容性研究是静态能力研究，没有 GREEN 候选，
   没有真实 R1/R2、权限、取消、额度样本。P3 Gate 里的「至少一条新增正式自动执行路径有受支持的厂商接口、
   可核对版本、真实 smoke」**未达成**。按规划应诚实发布当前范围，不冒充 P3 能力全通过。
2. **内置 Codex 二进制当前是 `codex-cli 0.166.0-alpha.6`**（alpha 预览）；历史真实烟测对应的是更早的已签名版本。
   升级后需按 [CODEX.md](CODEX.md) §12 复核清单重跑真实 R1/R2 才能把「当前版本已验证」写进发布说明。
3. **Antigravity 条款风险**：官方 FAQ 明确禁止第三方封装；现有 `agy` 适配器只能作为实验性、默认禁用的历史实现保留。
4. **平台证据不齐**：macOS `E2E`、Linux `CORE_EXPERIMENTAL`（仅 CI 纯逻辑安全子集）、Windows `STATIC_GAP`。
5. **本机全量套件不能声称全绿**：4 个进程终止类模块（7 个用例）因沙箱无法向子进程组投递信号而失败；
   未修改基线失败集合完全相同。CI 只跑三个明确命名的作业，不是全量 1545 项。

因此本轮的成果是**文档与证据整理**，适合作为 `main` 上的持续交付；不适合对外宣布一个「P3 全部通过」的版本。
`pyproject.toml` / `__version__` 保持 `1.0.0`，未改动版本号。

## 已完成的 P0–P3（可核对）

| 任务 | PR | 合并提交 | 要点 |
|---|---|---|---|
| T00 暂停后 retry 重复审批 | #2 | `bab57db` | 复用 pending、换 Worker 重新授权；9 个回归 + M70/M627–M630 |
| T01 核心回归进 CI | #3 | `3fcec18` | `smoke_install` + `core_unittests` 两个明确层级 |
| T02 文档与 metadata 真实性 | #4 | `79477a3` | 去作者路径、修正 pyproject、AGENTS 私有引用 |
| T03 Context 接口抽象 | #5 | `3d6c134` | Kernel 只依赖接口；M631 |
| T04 普通 Git 仓库免 Harness | #6 | `6c97c6f` | 默认 generic；M632–M635 |
| T05 可选 Harness / 模式可见 | #7 | `24e553b` | show/list/doctor 显示模式；恶意 AGENTS 不改权限 |
| T06 兼容、迁移与回退 | #8 | `1e80a83` | 旧条目默认 harness；无数据库迁移 |
| T07 草案 → 严格合同编译器 | #9 | `548bc58` | 只打印不提交；M636–M639 |
| T08 主控短路径 | #10 | `5f20fd7` | `prepare_task`、派生状态、短摘要；M640–M641 |
| T09 人工交接 UX | #11 | `e5f3ab1` | 路径/mode/provenance；M642–M647 |
| T10 分层验收证据 | #12 | `121f062` | `evidence_summary`；M648–M653 |
| T11 候选整合 | #13 | `a79d87a` | 干净基线叠加、下游可见；M654–M666 |
| T12 角色/厂商解耦 | #14 | `e21e109` | surface、能力闸门；M667–M671 |
| T13 官方接口证据研究 | #15 | `e6b3e79` | 无 GREEN；`docs/COMPATIBILITY.md` |
| T14 新增自动 Provider | — | Issue #16 | **阻塞**，未创建空实现 |
| T15 安全审计与平台分级 | #17 / #18 | `5f27cfb` / `fe3270d` | 独立安全修复 + 安全矩阵 + Linux CI |
| T16 公开文档与发行审查 | 本 PR | — | QUICKSTART / MODES / 历史归档 / 本清单 |

## 解锁新版的条件

1. 用户明确选定官方允许的账号路径并授权在**非敏感夹具**上做真实 R1/R2、取消、权限、额度样本（T14 的四项解锁条件）。
2. Codex 当前版本（含内置 alpha）的真实复核记录，写入 [CODEX.md](CODEX.md) 与 [COMPATIBILITY.md](COMPATIBILITY.md)。
3. 一个能在干净 Linux 环境跑通 fake CLI 全链路的记录（把 Linux 从 `CORE_EXPERIMENTAL` 升格）。
4. 用户单独批准打 tag / 发 Release，并确认 `CHANGELOG.md` 的版本号与发行说明。

## 对外表述的红线

- 不宣称已实现自动 Supervisor、同项目多个 Worker 并行、自动真实仓库写入。
- 不把 `YELLOW`/`RED` 候选写成「已支持」；不把历史报告当成当前实测证据。
- 不把 `ACCEPTED` / `CANDIDATE_READY` / `APPLIED` 混为一谈；`applied_to_project` 恒为 false。
- 不以消费者订阅账号的非官方调用作为推荐路线，也不指导规避厂商使用限制。

## 本轮文档交付

- 新增 [QUICKSTART.md](QUICKSTART.md)（含 2026-11-06 全新 clone 实跑记录：doctor 5 PASS / 0 FAIL、
  fake smoke R1+R2、普通 Git 仓库 generic 登记、手工交接包、以及两处真实摩擦点的诚实记录）。
- 新增 [MODES.md](MODES.md)（三种角色、三种协作方式、状态词汇对照、不做什么）。
- 新增 [docs/reports/README.md](reports/README.md)（历史快照归档说明、两套阶段编号的区分、当前证据入口）。
- 中英 README / CONTRIBUTING / AGENTS / CHANGELOG 对齐：测试数更新为约 1545 项、明确 CI 三个作业、
  明确徽章不代表全量、去过时数字、补新文档索引。
