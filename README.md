# Personal Agent Runtime（PAR）

*[English README](README.en.md)（更短，含零配置演示与和同类工具的对比）；本文件与 `docs/` 是详细、权威的中文版本。许可：[MIT](LICENSE)。*

[![tests](https://github.com/Sweetienite/personal-agent-runtime/actions/workflows/tests.yml/badge.svg)](https://github.com/Sweetienite/personal-agent-runtime/actions/workflows/tests.yml)

本地多模型调度系统：按**你**配置的主模型、Worker 和备用链调度 Codex 与 Antigravity（`agy`），任务可暂停、可恢复，全程可审计。它既支持普通 Git 仓库（默认的 generic 模式，不需要 `AGENTS.md` / `.ai/STATE.md`），也能复用已有的 AI Harness（可选的进阶模式：Core 加各项目的 `AGENTS.md` 与 `.ai/STATE.md`，用 `--context-mode harness`），不新建第二份项目记忆。

> **版本 `1.0.0`**：真实的写入、批准、验收全流程都已跑通并通过人工验收；Codex 与 Claude Desktop 两种主控都已接入并验证过连通。下面链接的是 **2026-10-16 当时的历史验收快照**，记录的是那个版本与环境的结果，不是当前版本对所有 Provider / 平台的支持保证；当前范围以 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) 与 [docs/SECURITY.md](docs/SECURITY.md) 为准：[docs/reports/V1_ACCEPTANCE_REPORT_20261016.md](docs/reports/V1_ACCEPTANCE_REPORT_20261016.md)。
> 目前有两个真实适配器：Codex（ChatGPT 订阅登录）与 Antigravity（Google 登录，经系统钥匙串）。**默认没有任何适配器**：只有配置里显式启用了 `codex` / `antigravity` 并写明可执行文件的绝对路径才会注册。测试套件全程使用假的 codex / agy 程序，**真实模型调用只在你逐项批准后才会发生**。

## 现在能做什么

| 命令 | 作用 | 会写入吗 |
|---|---|---|
| `par version` | 版本与阶段 | 否 |
| `par doctor` | 只读环境报告：Python、数据目录、配置、各 Provider 状态 | 否 |
| `par init` | 创建运行数据目录（权限 0700） | 仅数据目录 |
| `par harness check` | 只读 Harness 体检（**可选 Harness 模式**）：Core 加 `search_root` 下的 Harness 文件夹，可与基线比对；默认 `search_root` 是 `~/Documents`，可在 `projects.toml` 改成你自己的目录 | 仅基线文件，且需 `--save-baseline` |
| `par project add ID PATH [--context-mode generic\|harness] [--scaffold-harness]` | 登记一个项目（只记录 id、路径、vcs、上下文模式，登记后写入是关着的；打开写入见 `enable-write`）。默认 generic：普通 Git 仓库即可，不要求也不写 `AGENTS.md`/`.ai/STATE.md`；`--context-mode harness` 沿用 Harness 规则；`--scaffold-harness` 只在显式要求时补缺失的极简模板，绝不覆盖已有文件 | 仅 Runtime 自己的 `projects.toml`；带 `--scaffold-harness` 时额外在项目里补缺失的那两个文件 |
| `par project enable-write ID [--confirm ID]` / `disable-write ID` | **写入开关**（真实项目）：先体检，不过关就列原因、什么都不改；过关后再敲一遍项目 id 才生效；留备份与审计行 | `projects.toml`（旧文件留备份）与 `logs/project_writes.jsonl` |
| `par project list` / `show ID` | 项目事实、**写入就绪**（谁在拦）、给 Worker 的 **Context Manifest** | 否 |
| `par task draft --draft FILE` | 把最小草案编译成严格合同：默认 role、ad_hoc、幂等键；缺路径/检查时列问题而不是猜；**只打印，不提交** | 否（会打开台账但不写任务） |
| `par task submit --contract FILE [--candidate ID]` | 校验任务合同并入队（幂等）；写任务等你批准；`--candidate` 让任务跑在已验证的候选基线上 | 仅 Runtime 自己的台账 |
| `par task run T` / `--next` | 前台跑一次 attempt（私有克隆里，Kernel 自己测改动与跑检查） | 台账与 `tasks/…`；**不碰项目** |
| `par task status / events / result / diff T` | 状态与下一步、事件日志、结果信封、补丁（只显示，不应用） | 否 |
| `par task approve / reject / accept / close / cancel / pause / resume / retry / recover` | 逐项批准、验收、控制与崩溃恢复 | 台账 |
| `par health [--probe] [clear PROVIDER]` | 哪些 Worker 现在能跑、为什么不能；`--probe` 让适配器报告自己：版本、签名、登录、模型目录、额度窗口（**不调用模型、不耗额度**） |
| `par provider login antigravity [--print-only]` | 没有现成 Google 登录时才用：在 Runtime 自己的 HOME 里启动 `agy` 的官方交互登录（不碰 `~/.gemini`，不经手凭据） | 仅 Runtime 自己的 `agy_login_home/` | 清除时写台账；任何 `par task`/`health` 命令首次使用都会在数据目录里建一份空台账 |
| `par task handoff T --to APP` / `par task import T --from DIR\|--patch F\|--text F` | **人工交接**：给 WorkBuddy / TRAE 这类只能人手操作的桌面应用准备一个包（提示词 + 私有副本 + 步骤），结果回来仍按普通 Worker 的标准验证。导出带 `mode`（`workspace` 改文件 / `text` 只收文字答案）、绝对路径和照抄就能做的 `next_actions`；导入核对 task/export/base 与实际导出路径，旧包、副本、伪造标记都点名拒绝（见 [docs/HANDOFF.md](docs/HANDOFF.md)） | 台账与 `tasks/…/handoff/` |
| `par candidate create --task A [--task B …]` / `show ID` / `list` | **候选整合**：把已验收的 R2 补丁按依赖顺序打到一条干净基线上，在 Kernel 自己的仓库里重跑各任务的检查；`VERIFIED` 才可被下游任务使用。真实项目一个字节都不动；冲突/检查失败留 `FAILED` 记录（见 [docs/KERNEL.md](docs/KERNEL.md)） | 数据目录 `candidates/` 与台账（只读记录） |
| `par task rebase T --candidate ID` | 让一个已排队、从未运行的任务改用候选基线（`base.commit` 变成候选提交） | 台账 |
| `par coordinator status / use NAME / kit NAME / prompt NAME` | **可选主控（路线 A）**：谁在主控、谁最近出现、一条命令切换（写接管包 + 记录）、打印接入片段与起手提示词；候选：Claude、Codex（见 [docs/COORDINATORS.md](docs/COORDINATORS.md)） | 台账与 `takeover/`（`kit`/`prompt`/`status` 只读） |
| `par takeover export / confirm ID --as NAME / status` | 主控交接：只读接管包（任务树与事实，不含对话和 Worker 的话）+ 显式确认；同一时刻只有一个主控 | 台账与 `takeover/` |
| `par mcp serve` / `par mcp config` | Claude Desktop 的 MCP 桥（只暴露 17 个受限工具——含调度/恢复/审批/验收/查看 diff，从不暴露配置/健康相关操作；见 [docs/MCP.md](docs/MCP.md)）；`config` 只打印要粘贴的配置，不写任何文件 | 否 |
| `par fixture create [ID]` | 造一个一次性的夹具项目（git 仓库、一个故意的 bug、无个人数据）并登记为可写（一次性的演练项目） | 仅数据目录里的 `fixtures/` 与 `projects.toml` |

真实项目的写入由你亲自打开（`par project enable-write ID`，先体检、再敲一遍项目 id 确认）。真实项目默认不能被提交写任务；只有你打开了写入的项目可以，而且**每个写任务**仍要你逐项批准；没有任何代码会把补丁应用到项目（要你自己 `git apply`）。MCP 桥已接进 Codex；接入 Claude Desktop 见 `ops/connect_claude_desktop.py`（详见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)）。备份、恢复与演练见 [docs/ROLLBACK.md](docs/ROLLBACK.md)。Worker 能读到你磁盘上的任何可读文件（Codex 沙箱不限制读），所以先在没有敏感文件的小项目上试运行；对你其他重要项目，必须先收紧读范围，见 [docs/CODEX.md](docs/CODEX.md)。

## 快速开始

先用两分钟看一眼全流程是什么样（不需要装任何模型、不需要登录任何账号——全程假 CLI + 一次性夹具项目）。完整陌生人演练（含隔离数据目录、普通 Git 仓库、手工交接与实跑结果）见 [docs/QUICKSTART.md](docs/QUICKSTART.md)；协作模式与状态词汇见 [docs/MODES.md](docs/MODES.md)：

```bash
git clone https://github.com/Sweetienite/personal-agent-runtime.git
cd personal-agent-runtime
uv venv --python 3.12 .venv && uv pip install -e .     # 零第三方依赖；没有 uv 时：python3.12 -m venv .venv && .venv/bin/pip install -e .
# 注意：如果你的 python3.12 本身就是用 `uv python install` 装的，plain venv 会因为 ensurepip 被 PEP 668 挡住而失败——这种情况就用上面那条 uv venv 命令
python -B tests/tools/smoke_codex.py --self-test        # 全流程演示：真实 Codex 适配器 + 假 codex 程序 + 夹具项目，无模型无额度
python -B tests/tools/smoke_agy.py --self-test          # 同上，Antigravity 版（真实 agy 适配器 + 假 agy 程序）
```

确认装得对、跑得通之后，再做完整的初始化与验证：

```bash
.venv/bin/par init                                      # 建 ~/Library/Application Support/PersonalAgentRuntime（0700）
.venv/bin/par doctor                                    # 全部 PASS，Provider 全部 disabled
python -B -m unittest discover -s tests -t .            # 约 1564 项测试（单进程，约 6–9 分钟），无需安装、无需网络、不调用模型
python -B tests/tools/parallel_tests.py                 # 同一套测试，按模块并行跑（约 1 分钟）；日常用这个
.venv/bin/par health --probe                            # 配置并启用 codex / antigravity 之后：签名、登录、模型目录、额度（见 docs/OPERATIONS.md）
```

详细用法、退出码与排障见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

> **CI 覆盖范围**：GitHub Actions 只有三个明确命名的作业——`smoke_install`（macOS 安装 + doctor + version）、`core_unittests`（macOS 上的状态/合同/审批/交接/候选整合等核心回归）、`linux_security_core`（Ubuntu 上平台无关的纯逻辑安全回归）。**徽章不代表全量 1564 项在 CI 通过**；其余模块只在开发机跑（部分测试假设单用户 macOS 机器、macOS 专属 doctor 检查或更大的 ARG_MAX）。普通终端上全量 1564 项已通过；在某些受限沙箱里，4 个进程终止类模块可能因无法向子进程组投递信号而失败（这些模块本身不因平台差异被跳过）。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

> **支持范围（2026-11-07 核对）**：正式自动执行路径只有已在配置中启用并写明绝对路径的 Codex；它的默认沙箱模式已于 2026-11-07 用真实 R1/R2 复核，等级为 `GREEN`（仅该版本/OS/账号组合）。Antigravity 适配器是**实验性、默认禁用**，其官方条款明确禁止第三方封装；Gemini CLI / Claude Code CLI / ACP / OpenCode 尚未在 PAR 的隔离与授权模式下做过真实验证。人工交接（`par task handoff`）适用于 GUI-only 工具。证据等级与未测项见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)，本轮没有新增正式自动 Provider。

## 设计边界（来自已拍板的决策，配置与代码都在强制）

- Worker 首批只有 **Codex** 与 **Antigravity（`agy`）**；Codex（P3）固定模型、固定沙箱、不含任何 API key，只接受 ChatGPT 订阅登录；Antigravity（P4）在 attempt 的私有 HOME 里运行（看不到你真实的 `~/.gemini`）、权限预设由 Runtime 写入（只读任务 `strict`）、不含任何 API key。旧 `gemini` CLI 保留安装，但配置校验**拒绝启用**，也不会成为 `agy` 的回退。Antigravity 走 Google 官方 `agy` CLI 的 headless 模式：该模式会随厂商版本变化，账号与条款风险由使用者自担；它默认不启用，也不是默认推荐的自动路径。
- 主模型、Worker、角色映射与备用链由你配置。不做模型评分，也不调用额外的 AI 去路由。
- 配置里**不允许**出现密钥、额外付费（`additional_paid_credits` 只能是 `false`），可执行文件必须是绝对路径，不查 PATH。
- 并发默认 1；Profile 里的 `max_parallel_workers` 可设为 2（不同项目的任务同时跑；同一个项目一次只做一件事；T17/T18 已测试）。
- 状态转换只走显式的表，同一张表由数据库触发器强制；事件只增不改；Worker 自述的「测试通过」永远不被采信，验收只认 Kernel 自己跑出的检查。
- Worker 只在 attempt 内的私有克隆里工作；Kernel 从不在 Worker 的 `.git` 里运行 git；越界（路径白名单外、受保护路径、符号链接）一律让 attempt 失败。
- R2 永不自动重试或换 Worker；R3 一律拒绝；权限被拒绝不会换更高权限重试；所有批准都是逐项的人的动作。
- 任何写入都要经过路径策略：不写 Harness Core、不写各工具的全局配置、不写 shell 配置、敏感路径一律拒绝。
- Runtime 的数据目录只放配置与执行台账，**不是项目记忆**。项目事实只在各项目的 `.ai/STATE.md` 与项目文件里。

## 目录

```text
src/par/
  cli.py  doctor.py  paths.py  config.py
  cli_task.py  `par task …` 与 `par health`    cli_mcp.py  `par mcp …`    cli_coordinator.py  `par coordinator …`    coordinators.py  主控注册表（接入片段、起手提示词）
  mcp/         protocol.py（逐行 JSON-RPC）  tools.py（17 个受限工具）  server.py（循环）
  core/        policy.py  redact.py  states.py（状态机）  store.py（SQLite 台账）  contract.py  router.py  health.py
               kernel.py（提交、审批、结算、恢复）  attempt.py（一次 attempt）  prompt.py  settings.py  clock.py
               relay.py（接力）  manual.py（人工交接）  takeover.py（主控交接）
  execution/   gitops.py  workspace.py（文件级比较）  subprocesses.py（进程组、超时）  validation.py  results.py
  schemas/     task / result / event 的 JSON Schema（schema.py 是不依赖第三方的校验器）
  providers/   base.py（协议）  registry.py（适配器注册表，默认为空）  codex.py（Codex 适配器）  antigravity.py（Antigravity 适配器）
               fake.py + fake_cli.py（脚本化测试替身）
  harness/     check.py（Harness 体检）
  context/     harness.py（HarnessContextAdapter：项目视图、Context Manifest、写入就绪）
  registry.py  项目登记    fixtures.py  夹具项目
config/        providers / profiles / projects 的 .example.toml
ops/           协调者/人工用的小工具：land_task.py（落地已验收任务）、start_task.py（提交+批准+跑）、
               cleanup_tasks.py（清理已关闭任务占用）、reliability_report.py（从台账算可靠性数据）、
               contract_skeleton.json（契约模板）、connect_claude_desktop.py（接入 Claude Desktop）；
               定期巡检的 launchd 模板与包装脚本（可选，由你自己安装）——见 docs/OPERATIONS.md
tests/         unit/（约 1564 项）  integration/（默认跳过：只读真实 Harness；离线核对真实 Codex / agy）  tools/（mutation_check.py 约 341 条变异、parallel_tests.py 并行跑全量测试、provider_probe.py 离线兼容探测、smoke_codex.py、smoke_agy.py）
               fake_codex.py + fake_codex_exe.py（假 codex 程序）  codex_samples.py（合成的事件流）  fake_agy.py + fake_agy_exe.py（假 agy 程序）  agy_samples.py
docs/          SECURITY  OPERATIONS  ROLLBACK  ADAPTERS  CONTEXT  KERNEL  CHANGELOG  reports/
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/SECURITY.md](docs/SECURITY.md) | 安全边界、能执行什么、已知限制 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 命令、退出码、基线工作流、排障 |
| [docs/KERNEL.md](docs/KERNEL.md) | 状态机、任务合同、台账、一次运行的完整流程、失败策略、崩溃恢复、已知限制 |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | **陌生人首次安装**：隔离数据目录 → 零账号 smoke → 普通 Git 仓库 → 手工交接；含 2026-11-06 实跑记录 |
| [docs/MODES.md](docs/MODES.md) | 主控/Worker/接口三种角色、CLI 自动 / 协议自动（未实现）/ 手工接力、状态词汇对照 |
| [docs/ROLLBACK.md](docs/ROLLBACK.md) | 历史版本（P0–P8）与本轮 P0–P3 改造的非破坏性回滚 |
| [docs/HANDOFF.md](docs/HANDOFF.md) | 接力、人工交接（WorkBuddy / TRAE）、主控交接 |
| [docs/MCP.md](docs/MCP.md) | MCP 桥（任何支持 MCP 的主控都能用）：17 个工具、永远不能做什么、怎么接入 |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | **使用指南**：怎么接通 Claude / Codex、每天怎么用、额度用完怎么办、人工交接、出问题怎么办 |
| [docs/COORDINATORS.md](docs/COORDINATORS.md) | 可选主控：Claude / Codex 的接入、一条命令切换、额度用完时怎么办 |
| [docs/CODEX.md](docs/CODEX.md) | Codex 适配器：版本兼容记录、命令行逐项依据、隔离配方、沙箱实测、失败分类、升级复核清单 |
| [docs/AGY.md](docs/AGY.md) | Antigravity 适配器：版本兼容记录、私有 HOME 与权限预设、真实实测与官方文档的差异、未验证清单、升级复核清单 |
| [docs/ADAPTERS.md](docs/ADAPTERS.md) | Provider 协议、失败分类、测试替身、接入清单 |
| [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) | **兼容性证据矩阵**：候选工具的版本/OS、官方资料、账号与条款、实测方法与「没有测的内容」；2026-11-06 静态研究加两次真实复核、2026-11-07 默认沙箱复核，现有 Codex 为 `GREEN`，仍无其它通用 GREEN 候选 |
| [docs/CONTEXT.md](docs/CONTEXT.md) | Context Manifest：给 Worker 的上下文契约、写入就绪 |
| [CHANGELOG.md](CHANGELOG.md) | 面向用户的版本变化摘要 |
| [docs/RELEASE_NOTES_2026-11.md](docs/RELEASE_NOTES_2026-11.md) | **发行审查清单**：P0–P3 全部合并记录、明确「不建议现在打 tag」的结论与解锁条件（打 tag 需你单独批准） |
| [docs/reports/](docs/reports/) | **历史快照**：各开发阶段当时的验证报告，不是当前版本的支持保证；当前证据以 COMPATIBILITY / SECURITY / CI 为准 |
