# 发行审查清单（P3/T16，2026-11-06）

本文是**审查结论**，不是发行公告。实际打 tag / 创建 GitHub Release 需要用户单独批准（任务卡明确要求），本轮没有执行。

## 结论：**不建议现在打新 tag / 发新版**

理由（按重要性）：

1. **仍没有跨环境通用 `GREEN` 的自动执行路径**。T14 原本是 `BLOCKED_BY_EVIDENCE`（Issue #16）：T13 是静态能力研究、无 GREEN。2026-11-06 用户授权解锁并选定「用官方允许的 ChatGPT 订阅路径做真实复核、**不新增自动 Provider**」；同日用户追加授权后，第二次尝试在宿主已由外部 OS 沙箱化的限定条件下取得了**成功的真实 R1/R2**，但等级只能是 **`GREEN (scoped)`**——真实运行用了非默认沙箱模式，PAR 默认 `-s` 模式仍需在普通终端复核。T14 的「不新增自动 Provider」结论不变，P3 Gate 里「至少一条**新增**正式自动执行路径」**仍未达成**。见 [第一部分](reports/CODEX_REAL_REVALIDATION_20261106.md) 与 [第二部分](reports/CODEX_REVALIDATION_PART2_20261106.md)。
2. **`codex-cli 0.166.0-alpha.6` 已完成 §12 版本复核（带限定）**：离线 8/8 PASS、trust 3/3 PASS、`par health --probe` AVAILABLE、可控分类样本 153/153 PASS、真实 R1/R2 成功；`TESTED_VERSION` 已更新为该版本，`untested version` 提示消除。**但**默认 `-s read-only|workspace-write` 在当前宿主无法叠加沙箱、未在本轮真实复核范围内，需在允许嵌套沙箱的普通终端用 `codex_revalidate.py` 补跑。见 [CODEX.md](CODEX.md) §12 / §14。
3. **Antigravity 条款风险**：官方 FAQ 明确禁止第三方封装；现有 `agy` 适配器只能作为实验性、默认禁用的历史实现保留。
4. **平台证据不齐**：macOS `E2E`、Linux `CORE_EXPERIMENTAL`（仅 CI 纯逻辑安全子集）、Windows `STATIC_GAP`。
5. **本机全量套件不能声称全绿**：4 个进程终止类模块（7 个用例）因沙箱无法向子进程组投递信号而失败；
   未修改基线失败集合完全相同。CI 只跑三个明确命名的作业，不是全量 1561 项。

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
| T14 新增自动 Provider | — | Issue #16 | **收口：不新增自动 Provider**；两次真实复核后现有 Codex 取得 `GREEN (scoped)`（第二次有成功的真实 R1/R2），仍无跨环境通用 GREEN、无空实现 |
| T15 安全审计与平台分级 | #17 / #18 | `5f27cfb` / `fe3270d` | 独立安全修复 + 安全矩阵 + Linux CI |
| T16 公开文档与发行审查 | 本 PR | — | QUICKSTART / MODES / 历史归档 / 本清单 |

## 解锁新版的条件

1. ~~用户明确选定官方允许的账号路径并授权在非敏感夹具上做真实 R1/R2 样本~~ **已完成（2026-11-06）**：用户选定了 ChatGPT 订阅 + `codex exec`，两次真实 R1/R2 均已留档；第二次取得成功的真实 R2。
2. ~~Codex 当前版本的真实复核与 `TESTED_VERSION` 更新~~ **已完成（带限定）**：§12 复核 8/8 + 3/3 + 153/153 + 真实 R1/R2，`TESTED_VERSION` = `0.166.0-alpha.6`。但等级为 `GREEN (scoped)`。
3. **仍需要在允许创建嵌套 OS 沙箱的普通终端**用 `python -B tests/tools/codex_revalidate.py --yes-use-quota --model <slug>` 补跑 **PAR 默认 `-s` 模式**的真实 R1/R2，并把结果写入 [CODEX.md](CODEX.md) 与 [COMPATIBILITY.md](COMPATIBILITY.md)；之后才能把 `GREEN (scoped)` 升格。该脚本在沙箱阻塞宿主上 exit 2、0 次模型调用，不会用提权参数做绿。
4. 若要新增（而不是只复核）一个自动 Provider，仍需付费 Gemini API key / 企业许可等**官方允许**的账号路径，并按 [ADAPTERS.md](ADAPTERS.md) 清单实现 + 真实样本；本轮用户已明确决定不新增。
5. 一个能在干净 Linux 环境跑通 fake CLI 全链路的记录（把 Linux 从 `CORE_EXPERIMENTAL` 升格）。
6. 用户单独批准打 tag / 发 Release，并确认 `CHANGELOG.md` 的版本号与发行说明。用户本轮明确要求**先不打 tag**。

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
- 中英 README / CONTRIBUTING / AGENTS / CHANGELOG 对齐：测试数更新为约 1558 项（本轮新增回归后为 1561）、明确 CI 三个作业、
  明确徽章不代表全量、去过时数字、补新文档索引。

## 2026-11-06 追加：T14 解锁后的两次真实复核

**第一部分**（PR #20）：

- 新增 [reports/CODEX_REAL_REVALIDATION_20261106.md](reports/CODEX_REAL_REVALIDATION_20261106.md)：
  用户授权后用官方允许的 ChatGPT 订阅路径做的真实 R1/R2 记录、副作用核对、根因复现、仍缺的证据。
  当时**无成功 R2、无通用 GREEN、无新 Provider、无 tag**。
- 新增 `tests/tools/sandbox_probe.py`（+ 单测）：区分「宿主不能创建嵌套沙箱」与「Provider 不兼容」。
- 修正状态表述：零机械检查的成功不再写「every check passed」；两条回归 + 变异 `M673` / `M674`。
- 真实 CLI 离线复核测试适配 `0.166.0-alpha.6`：隔离断言改为结构化核对工具定义（更严格），
  如实记录残留的 `spawn_agent` / `list_agents`。

**第二部分**（本 PR，用户追加授权后）：

- 新增 [reports/CODEX_REVALIDATION_PART2_20261106.md](reports/CODEX_REVALIDATION_PART2_20261106.md)：
  9 条宿主脱离通道的排除结论、**成功的真实 R1/R2**（`MEASURED · checks PASSED (1/1)`）、
  副作用核对、以及 `GREEN (scoped)` 的确切限定。
- `TESTED_VERSION` → `0.166.0-alpha.6`；`par health --probe` 不再提示 `untested version`；
  §12 复核 8/8 + 3/3 + 153/153 全通过。
- 新增 `tests/tools/codex_revalidate.py`（+ 4 项单测）：§12 一键复核；沙箱阻塞宿主上 exit 2、
  0 次模型调用，永不改适配器沙箱模式、永不传提权参数。
- `sandbox_probe.py` 新增继承沙箱的原始 `sandbox_check` 值（+ 2 项单测）。
- 同步更新 [COMPATIBILITY.md](COMPATIBILITY.md)、[CODEX.md](CODEX.md) §12 / §14、
  [ADAPTERS.md](ADAPTERS.md)、[QUICKSTART.md](QUICKSTART.md)、[MODES.md](MODES.md)、
  [reports/README.md](reports/README.md)、`CHANGELOG.md`、`README.en.md` 与本清单。
  **仍未打 tag / 发 Release。**
