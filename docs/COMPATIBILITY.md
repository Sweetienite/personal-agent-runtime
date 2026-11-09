# 兼容性证据矩阵（2026-11-06，2026-11-07 更新，2026-11-09 口径修订，P3/T13）

> **2026-11-09：P3 Gate 正式修订。** 正式自动执行范围收口为 **Codex（`GREEN`，仅该版本/OS/账号组合）+ 人工交接**；
> T14「新增一个正式自动 Provider」标记为 `CLOSED_BY_SCOPE_DECISION`，不再作为发行门槛，也不宣称多厂商自动执行。
> 逐项 59 项证据见 [acceptance/P0-P3-results.md](../acceptance/P0-P3-results.md)。Antigravity 仍为实验性、默认禁用。
>
> **本文的基线是静态能力研究；2026-11-06 追加两次真实复核，2026-11-07 补完默认沙箱模式，现有 Codex 为 `GREEN`，仍无其它通用 `GREEN` 候选。** T13 那一轮只做了本机离线 `--version` / `--help` 与公开官方资料核对。之后用户授权解锁 T14，选定「用**官方允许的 ChatGPT 订阅 + `codex exec` 路径**做真实复核、**不新增自动 Provider**」。第一次尝试因宿主无法创建嵌套 macOS Seatbelt 沙箱而没有成功的真实 R2（[第一部分](reports/CODEX_REAL_REVALIDATION_20261106.md)）；同日用户追加授权后的第二次尝试，在「宿主已由外部 OS 沙箱化」这一 `codex exec --help` 明确写出的适用情形下（"Intended solely for running in environments that are externally sandboxed"）取得了**成功的真实 R1 与 R2**（`MEASURED · checks PASSED (1/1)`），§12 版本复核完成、`TESTED_VERSION` 更新为 `0.166.0-alpha.6`，详见 [第二部分](reports/CODEX_REVALIDATION_PART2_20261106.md)。**2026-11-07 限定解除**：在 `sandbox_check = 0` 的普通终端上，产品代码的默认 `-s read-only|workspace-write` 模式成功跑完真实 R1/R2，详见 [第三部分](reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md)；现有 Codex 因此从 `GREEN (scoped: externally sandboxed host)` 升为 **`GREEN`**。T14 仍收口为「不新增自动 Provider」，不创建空适配器。
>
> 「官方存在某个参数」不等于「PAR 在现有隔离与授权模式下能安全工作」。本文每一行都列出证据等级、版本、OS、账号类型、实测方法和**没有测的内容**。可复现的离线探测：`python -B tests/tools/provider_probe.py`（默认只做 `which()`）、`python -B tests/tools/sandbox_probe.py --json`（能否创建嵌套 OS 沙箱、以及继承沙箱的原始 `sandbox_check` 值），以及 `python -B tests/tools/codex_revalidate.py --check-only`（一键跑 §12 离线复核，零模型调用）。

## 主控客户端（MCP，与自动 Worker 分开）

| 客户端 | 角色 | 真实证据 | 日期 | 限制 |
|---|---|---|---|---|
| Claude Desktop | MCP 主控 | 工具发现 + 一次真实只读 `list_tasks`（返回真实任务） | 2026-10-16 | 真实客户端里的提交/批准/验收未联调；这些路径由脚本化 MCP 测试覆盖 |
| Codex CLI | MCP 主控（同时可作 Worker） | `codex exec` 中一次真实只读 `list_tasks` | 2026-10-16 | 同上；Worker 证据另见下方 Codex 行 |
| 其他 MCP 客户端 | 协议可接入 | 无 | — | 接口已实现，不代表任何品牌已验证 |

MCP 是**主控接入协议**，不是自动 Worker 协议；一个客户端能调用 21 个工具，不代表它能被 PAR 自动派发代码任务。真实客户端证据见 [V1 验收附录](reports/V1_ACCEPTANCE_REPORT_20261016.md)。机器可读事实源为 [product-capabilities.json](product-capabilities.json)。

## 调查环境

| 项 | 值 |
|---|---|
| 日期 | 2026-11-06（官方资料的检索日期，非「最后验证通过」日期） |
| OS / 架构 | macOS 15.6.1 (24G90), arm64,达尔文内核 |
| Python | 3.12（项目 `.venv`）；本机另有 Node v22.22.2 / npm 10.9.7 |
| 本机在 PATH 上的候选 | `gemini` 0.44.1（`/opt/homebrew/bin/gemini`）；`codex` / `agy` / `claude` / `opencode` 均 **不在 PATH** |
| 已安装但不在 PATH | ChatGPT.app 内置 `codex-cli 0.166.0-alpha.6`（**alpha 预览版**）、Claude.app（桌面应用，非 CLI） |
| 未安装 | `agy` 1.3.5（历史私有安装，见 [AGY.md](AGY.md)，本机当前路径已不存在）、Claude Code CLI、OpenCode、任何 ACP agent |
| 本次运行的外部命令 | 仅 `gemini --version`、`gemini --help`、内置 codex 的 `--version`、`npm ls -g --depth=0`；**零模型调用、零网络请求、零凭据读取** |

### 2026-11-06 追加：真实复核环境（T14 解锁尝试，第一部分）

| 项 | 值 |
|---|---|
| 账号路径 | **ChatGPT 订阅登录**（`codex login status` → `Logged in using ChatGPT`），官方 `codex exec` 明确面向脚本 / CI；非 API key、非按量计费 |
| 二进制 | ChatGPT.app 内置的**签名**二进制 `…/CodexCLI.app/Contents/MacOS/codex`（Team `2DC432GLL2`），**绝对路径** |
| 版本 / OS | `codex-cli 0.166.0-alpha.6`（alpha）/ macOS 15.6.1 arm64；模型 `gpt-6-luna` |
| 真实调用 | **2 次**（R1 只读问答 + R2 限定写），一次性私有数据目录 + 无个人数据的夹具项目 |
| 实测结果 | R1 干净终态但 Worker 自述读不到文件；R2 厂商 `SUCCESS` 但 Kernel `unit` 检查失败 → `FAILED`，0 文件变更、无补丁、未应用到项目 |
| 阻塞根因 | 宿主 shell 已在 macOS 沙箱内，`/usr/bin/sandbox-exec` 报 `sandbox_apply: Operation not permitted`（exit 71）；CLI 无法创建嵌套 Seatbelt 沙箱 |
| 未做的事 | 没用 `--dangerously-bypass-approvals-and-sandbox` / `--yolo` / 任何提权参数；没用个人 Google 账号；没接受 Gemma 条款下载模型；没打 tag / 发 Release |
| 证据 | [第一部分报告](reports/CODEX_REAL_REVALIDATION_20261106.md) |

### 2026-11-06 追加：第二次真实复核（成功的 R1/R2）

| 项 | 值 |
|---|---|
| 账号路径 / 二进制 / 版本 | 同上；`TESTED_VERSION` 已更新为 `0.166.0-alpha.6` |
| 宿主状态 | `sandbox_check = 1`（进程继承 macOS App Sandbox），嵌套 Seatbelt EPERM；9 条脱离通道逐一排除，见第二部分 §1 |
| 沙箱模式 | `-s danger-full-access`，**仅一次性临时 worktree 副本**；`codex exec --help` 明确该模式的适用情形是「已由外部环境沙箱化」，主干适配器仍是默认 `-s read-only\|workspace-write`，未新增任何开关 |
| 真实调用 | **2 次**（R1、R2 各 1 次，ChatGPT 订阅额度） |
| 实测结果 | R1 模型真正读到文件并答对；R2 `evidence MEASURED · checks PASSED (1/1)`、补丁 1 文件 +1 −1、边界 0 违规、未应用到项目 |
| §12 复核 | 离线 8/8 PASS、trust 3/3 PASS、`par health --probe` AVAILABLE、可控分类样本 153/153 PASS |
| 限定（当时） | `GREEN (scoped)`：默认沙箱模式在本宿主不可运行且未复核；2026-11-07 已在普通终端补跑，见下一节 |
| 未做的事 | 没把提权参数写进主干代码或默认配置；没改 `pyproject` 版本号；没打 tag / 发 Release |
| 证据 | [第二部分报告](reports/CODEX_REVALIDATION_PART2_20261106.md) |

### 2026-11-07 追加：默认沙箱复核（`GREEN` 升级）

| 项 | 值 |
|---|---|
| 宿主状态 | `sandbox_check = 0`（未被外部 App Sandbox 约束）；`nested_sandbox_available: true` |
| 沙箱模式 | 产品代码默认 `-s read-only\|workspace-write`；**未改适配器、未新增开关、未传任何提权参数** |
| 命令 | `python -B tests/tools/codex_revalidate.py --yes-use-quota --model gpt-6-luna` → **exit 0** |
| 真实调用 | **2 次**（R1 + R2，ChatGPT 订阅额度） |
| 实测结果 | R1 正确答出 `add(2, 3) = -1`（与 spec 的 `5` 不符），0 文件变更；R2 `MEASURED · boundary CLEAN · checks PASSED (1/1)`、补丁 1 文件 +1 −1、0 违规、未应用到项目 |
| 复核步骤 | offline 8/8、trust 3/3、real_smoke rc=0、`par health --probe` AVAILABLE |
| 副作用 | 夹具工作树 clean、真实数据目录未变、`~/.codex/config.toml` 未变 |
| 等级 | 现有 Codex 从 `GREEN (scoped)` 升为 **`GREEN`**；仍只覆盖该版本/OS/账号组合，不是新增 Provider |
| 证据 | [第三部分报告](reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md) |

## 证据等级定义

| 等级 | 含义 |
|---|---|
| `GREEN` | 官方明确支持的接口，**在可用环境完成**最小 R1 + R2、权限、错误与取消样本，并核实账号/条款许可，适合作为正式自动适配路径 |
| `GREEN (scoped)` | 已取得成功的真实 R1 + R2 与完整可控样本，但**只覆盖记录下来的那一种宿主/配置组合**；其它配置（例如默认沙箱模式在普通终端）需另行复核，**不得跨环境外推** |
| `YELLOW` | 技术可试（官方文档/参数存在），但缺真实运行、权限、授权或条款证明；**仅实验** |
| `RED` | 无合规接口、隔离无法确定、或明显依赖规避条款；不纳入自动正式支持 |
| `EXISTING` | 已有的正式/实验实现，本轮只做版本与文档复核，**不是本轮新增证据** |

## 候选排序（给 T14 的决策）

| 排序 | 候选 | 等级 | 一句话理由 |
|---|---|---|---|
| 1 | **Antigravity CLI / `agy`** | `RED` | 官方 FAQ 明确禁止把 `agy` 包在第三方编码 Agent/应用中，且消费者订阅不支持自动账号轮换；PAR 正是第三方 runtime |
| 2 | **Gemini CLI** | `YELLOW`（偏 RED） | 官方 headless 接口齐全，但 2026-06-18 起免费层 / Google AI Pro / Ultra 已停服；本机 0.44.1 是旧版本，账号类型未确认，未跑真实任务 |
| 3 | **Claude Code CLI** | `YELLOW` | 官方 `-p` + `stream-json` + `--bare` + 权限模式齐全，但本机未安装 CLI；官方 headless 文档未对消费者订阅的程序化使用给出条款许可；脚本化路径要求 API key 或云凭据 |
| 4 | **Codex** | `EXISTING` + `GREEN` | 已有适配器用的正是官方 `codex exec --json`；2026-11-06 第一次真实复核受宿主嵌套沙箱限制，第二次在外部沙箱化条件下成功；**2026-11-07 在普通终端用产品默认 `-s` 模式再次取得成功的真实 R1/R2**，§12 复核完成、`TESTED_VERSION` = `0.166.0-alpha.6`；这不是新增 Provider |
| 5 | **ACP（Agent Client Protocol）** | `YELLOW` | 协议本身公开且权限回调语义明确，但 v2 仍是 draft、本机无任何 ACP agent 可验证；不是「一个能跑的 Provider」 |
| 6 | **OpenCode** | `YELLOW` | 官方有 `opencode serve` / `run` headless 与 SSE/OpenAPI，但需额外 npm/bun 运行时，与本项目零第三方依赖原则冲突，本机未安装 |
| 7 | **WorkBuddy / TRAE / GUI-only** | `EXISTING` | 手工交接路径已在 T09 落实并测试；2026-11-07 用 WorkBuddy 5.9.3 真实重跑 R2 交接通过；不是自动 Provider，不参与本排序的 GREEN 判定 |

**结论：现有 Codex 为 `GREEN`，仍没有其它通用 `GREEN` 候选，也仍不新增自动 Provider。** T13 的静态研究阻塞，已由用户在 2026-11-06 作出范围决策收口为「**不新增自动 Provider**」。两次真实复核：第一次受宿主嵌套沙箱限制没有成功的 R2；第二次在宿主已由外部 OS 沙箱化的限定条件下取得成功；**2026-11-07 在普通终端用产品默认 `-s` 模式补跑成功，默认沙箱限定解除**。该 `GREEN` 仍只覆盖记录中的版本 / OS / 账号组合，不能跨版本或跨平台外推。不新增任何正式自动 Provider，不把「实验」写成「已支持」。若将来要新增（而不是只复核）一个自动 Provider，仍需付费 Gemini API key / 企业许可等官方允许路径与新的真实证据，而不是一个配置开关。

## 逐项证据

### 1. Antigravity CLI / `agy` — `RED`

| 项 | 内容 |
|---|---|
| 官方资料 | [FAQ](https://antigravity.google/docs/faq) · [Headless mode](https://antigravity.google/docs/cli/headless) · [Authenticate](https://antigravity.google/docs/cli-install) |
| 版本 / OS | 本机**未安装**；历史记录 `agy` 1.3.5 / macOS arm64（[AGY.md §1](AGY.md)），不可当作当前环境的证据 |
| 官方 headless 能力 | `agy -p`（`--print`）；`--output-format text\|json\|stream-json`（NDJSON，含 `conversation_id`、`status`、`response`、`usage`）；未认证时以 `authentication required` 退出而非挂起；支持 `GEMINI_API_KEY`（需在 `~/.gemini/antigravity-cli/settings.json` 设 `modelProvider = "gemini"`）与 Google Cloud ADC 供无头/CI 使用 |
| 账号 / 条款 | **FAQ 原文**：「Using third-party software, tools, or services to access Antigravity is a violation of our Terms of Service … Such actions can result in suspension or termination of your account」，以及「Wrapping agy inside third-party coding agents or applications, or automating account rotation, is not supported under consumer plans」；官方建议第三方编码 Agent 改用 **Gemini Enterprise 或 Google AI Studio API key** |
| 实测方法 | 本机 `command -v agy` → 不存在；未运行任何 `agy` 命令 |
| 没有测的内容 | 全部真实运行、登录、额度、取消、沙箱、事件解析 |
| 判定 | PAR 是一个第三方编码 runtime，把 `agy` 作为自动 Worker 封装正好落在官方禁止的范围内。即使技术可行也**不能**作为正式自动路径。现有 `agy` 适配器保留为「实验/风险」，默认禁用，且不得指导规避使用限制 |

### 2. Gemini CLI — `YELLOW`（偏 RED）

| 项 | 内容 |
|---|---|
| 官方资料 | [Headless mode reference](https://geminicli.com/docs/cli/headless/)（页面标注最后更新 2026-03-10）· [CLI cheatsheet](https://geminicli.com/docs/cli/cli-reference/)（标注 2026-05-01）· [Plans](https://geminicli.com/plans/) · [设置](https://geminicli.com/docs/cli/settings) · [Google 官方迁移公告](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/) |
| 版本 / OS | 本机 `gemini --version` → **0.44.1**（离线实测）；macOS 15.6.1 arm64 |
| 官方 headless 能力（本机 `--help` 与官网一致） | `-p/--prompt` 非交互；`-o/--output-format text\|json\|stream-json`（事件 `init` / `message` / `tool_use` / `tool_result` / `error` / `result`）；退出码 `0` 成功、`1` 通用/API 错误、`42` 输入错误、`53` 超出轮次上限；`--approval-mode default\|auto_edit\|yolo\|plan`（`plan` 为只读）；`--sandbox`；`--policy` / `--admin-policy`；`--include-directories`；`--experimental-acp`（本机帮助注明 **deprecated，改用 `--acp`**） |
| 账号 / 条款 | **官方公告**：自 2026-06-18 起，Gemini CLI 与 Gemini Code Assist IDE 扩展**停止为 Google AI Pro / Ultra 订阅者及免费个人用户提供服务**；官方鼓励迁移到 Antigravity CLI。企业 Gemini Code Assist Standard/Enterprise 许可、以及付费 Gemini / Gemini Enterprise Agent Platform API key 仍保留。官网同时注明「Gemini CLI 未针对 Gemini 4 Argon 优化」 |
| 实测方法 | `gemini --version`、`gemini --help`（均为离线，不调用模型）；核对官方页面 |
| 没有测的内容 | 本机账号属于哪一层、是否仍能发起请求、真实 R1/R2、`--approval-mode plan` 的实际只读范围、`--sandbox` 的 OS 级边界、额度/认证错误分类、取消与超时、结构化事件解析 |
| 判定 | 参数齐全但**账号可用性未证明**，且消费者订阅路径已被官方关闭；本机版本较旧。不能判 GREEN。若将来要试，只允许走官方明确保留的企业许可 / 付费 API key，且需用户明确授权 |

### 3. Claude Code CLI — `YELLOW`

| 项 | 内容 |
|---|---|
| 官方资料 | [Run Claude Code programmatically (headless)](https://code.claude.com/docs/en/headless) · [CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-reference) · [Permission modes](https://docs.anthropic.com/en/docs/claude-code/permission-modes) |
| 版本 / OS | 本机 `claude` **不在 PATH**（仅 Claude.app 桌面应用）；无版本可报告 |
| 官方 headless 能力 | `claude -p/--print`；`--output-format text\|json\|stream-json`（NDJSON，含 `system/init`、`system/api_retry`、`permission_denied`、最终 `result`）；`--bare`（脚本/CI 推荐：跳过 hooks/skills/plugins/MCP/`CLAUDE.md`/auto memory，**不使用订阅登录、不读 OAuth 凭据或系统钥匙串**）；`--permission-mode default\|acceptEdits\|plan\|auto\|dontAsk`；`--permission-prompts none`（需 v2.1.259+）；`-p` 无权限设置时默认 Manual mode（v2.1.302+）；`--allowedTools` 精确授权；SIGTERM 退出码 143 |
| 账号 / 条款 | `--bare` 要求 `ANTHROPIC_API_KEY`（Claude Console 创建的 key）或 `apiKeyHelper`，或 Bedrock / Google Cloud / Microsoft Foundry 凭据；**官方 headless 页面没有对消费者订阅（Pro/Max）用于程序化调用给出条款许可或禁止的明确说明**，需要另行核实。本项目不以「消费者订阅账号的非官方调用」作为推荐路线 |
| 实测方法 | `command -v claude` → 不存在；仅核对官方资料 |
| 没有测的内容 | 安装与版本、认证、任何真实 R1/R2、权限拒绝样本、取消/超时、事件解析、账号条款 |
| 判定 | 官方接口最完整，但本机没有 CLI、没有授权路径、也没有条款确认；只能 YELLOW |

### 4. Codex — `EXISTING` + `GREEN`（2026-11-07 默认沙箱复核后更新）

| 项 | 内容 |
|---|---|
| 官方资料 | [Non-interactive mode](https://developers.openai.com/codex/noninteractive) · [Agent approvals & security](https://developers.openai.com/codex/sandbox) · [CLI reference](https://developers.openai.com/codex/cli/reference) · 项目内 [CODEX.md](CODEX.md)（历史真实烟测记录） |
| 版本 / OS | 本机 PATH 无 `codex`；ChatGPT.app 内置二进制 `codex --version` → **`codex-cli 0.166.0-alpha.6`**（离线实测，alpha 预览版）。历史文档记录的是更早的已签名版本 |
| 官方 headless 能力 | `codex exec --json`（JSONL：`thread.started` / `turn.*` / `item.*` / `error`）；默认只读沙箱；`--sandbox read-only\|workspace-write`；`--ask-for-approval`；`--ephemeral`；`--ignore-user-config` / `--ignore-rules`；**`--full-auto` 已不再支持**（官方明确要求改用 `--sandbox workspace-write`）；macOS 用 Seatbelt、Linux 用 bwrap+seccomp |
| 与现有适配器的一致性 | `src/par/providers/codex.py` 的 `build_argv` 使用 `exec --json --color never --ephemeral --skip-git-repo-check -s <workspace-write\|read-only> -m <model> -C <cwd> -`，**与官方文档一致**，未使用已废弃的 `--full-auto` |
| 账号 / 条款 | 现有实现依赖 ChatGPT 订阅登录（`CODEX_HOME`）；官方 `codex exec` 明确面向脚本与 CI |
| 实测方法 | T13：`codex --version`（离线）。**2026-11-06 第一次**：签名校验、`login status`、`features list`、`mcp list`、`debug models`、`debug prompt-input`、`exec --help`、`PAR_REAL_CODEX_TRUST=1` 无登录 `exec`，以及 2 次真实 `codex exec`。**同日第二次**：`PAR_REAL_CODEX=1`（8/8 PASS）、`PAR_REAL_CODEX_TRUST=1`（3/3 PASS）、`par health --probe`、153 项可控分类样本、再加 2 次真实 `codex exec`（R1/R2，ChatGPT 订阅额度）。**2026-11-07**：在 `sandbox_check=0` 的普通终端上跑 `codex_revalidate.py --yes-use-quota --model gpt-6-luna`（offline 8/8、trust 3/3、real_smoke rc=0），2 次真实 R1/R2 使用产品默认 `-s` 模式 |
| 真实复核结果 | **第一次**：宿主无法创建嵌套 Seatbelt 沙箱，R1 读不到文件、R2 被 Kernel `unit` 检查正确判 `FAILED`（[第一部分](reports/CODEX_REAL_REVALIDATION_20261106.md)）。**第二次**：在「宿主已由外部 OS 沙箱化」这一 `codex exec --help` 明确写出的适用情形下，用 `-s danger-full-access`（**仅临时副本，主干未改**）取得**成功的真实 R1 + R2**：R1 真正读到文件并答对；R2 `MEASURED · checks PASSED (1/1)`、补丁 1 文件 +1 −1、边界 0 违规、未应用到项目。事件流含 `command_execution` / `file_change` / `turn.completed`。详见 [第二部分](reports/CODEX_REVALIDATION_PART2_20261106.md)。**第三次（2026-11-07，默认沙箱）**：同一结论，但使用产品代码默认 `-s read-only\|workspace-write`，未改适配器、未传提权参数；R1 答对、R2 `MEASURED · boundary CLEAN · checks PASSED (1/1)`、0 违规、未应用。详见 [第三部分](reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md) |
| 没有测的内容 | 真实取消/超时/断线/输出洪流与额度/限流的真实事故样本（本轮用 153 项可控假样本覆盖，且无登录 `exec` 的认证失败由 trust 测试真实触发）、`spawn_agent` / `list_agents` 在 `--disable multi_agent` 后仍出现在基础工具列表（已如实记录，**不宣称子代理已验证关闭**）、该版本/OS/账号之外的部署矩阵 |
| 判定 | 属于「维护、版本复核」，**不是 T14 的新增正式自动路径**。`TESTED_VERSION` 已更新为 `0.166.0-alpha.6`（该 build 的接口/事件/隔离配方/Kernel 集成已实测），`par health --probe` 不再提示 `untested version`。等级为 **`GREEN`**：2026-11-07 已用产品默认沙箱模式完成真实 R1/R2；仍只覆盖该版本/OS/账号组合，**不得跨版本或跨平台外推**。详见三份复核报告与 [CODEX.md §12 / §14 / §15](CODEX.md) |

> 本轮真实复核同时暴露并修正了一处表述问题：只读任务没有任何机械检查时，状态行却写
> `every check passed`。现在零检查时改为明确写出「没有 Kernel 检查运行，接受前请审阅证据」，
> 并在 `tests/unit/test_kernel_run.py` 加了回归测试。

### 5. ACP（Agent Client Protocol） — `YELLOW`

| 项 | 内容 |
|---|---|
| 官方资料 | [Protocol overview](https://agentclientprotocol.com/protocol) · [Initialization / session](https://agentclientprotocol.com/protocol/v2/draft/overview)（**v2 标注 Draft: In Progress and May Change**）· [Tool calls & permissions](https://agentclientprotocol.com/protocol/v2/draft/tool-calls) · [Session modes](https://agentclientprotocol.com/protocol/session-modes) |
| 版本 / OS | 协议 v1 文档 + **v2 draft**；本机**没有任何 ACP agent 实现**可连接 |
| 协议能力 | JSON-RPC 2.0 over stdio（远程 HTTP/WS 支持「仍在进行中」）；`initialize` 协商版本与能力；`session/new`、`session/prompt`、`session/cancel`；客户端侧 `session/request_permission`（`allow_once` / `allow_always` / `reject_once` / `reject_always`）；`fs/read_text_file` / `fs/write_text_file`、`terminal/*`；**所有文件路径必须是绝对路径**；`session/set_mode`（官方注明未来将被 Session Config Options 取代） |
| 对 PAR 的关键约束 | 协议明确要求「Clients MAY automatically allow or reject … according to user settings」并警告**自动批准不得只依赖标题/描述/选项名**、未知 outcome 不得当成同意。若 PAR 做 ACP 客户端，必须把 `request_permission` 接到 Kernel 自己的审批，不得盲目 allow；版本协商失败应停止，不得降级为无限权限模式 |
| 实测方法 | 仅阅读官方协议文档；本机无实现、未做任何连接 |
| 没有测的内容 | 任一被调 Agent 的真实支持、鉴权、cwd、事件、退出与恢复语义、v1/v2 兼容 |
| 判定 | 是协议而非可运行 Provider；「协议标准存在」不能写成「PAR 支持 ACP」 |

### 6. OpenCode — `YELLOW`

| 项 | 内容 |
|---|---|
| 官方资料 | [Server](https://opencode.ai/docs/server) · [CLI](https://opencode.ai/docs/cli) |
| 版本 / OS | 本机 `opencode` **不在 PATH**，未安装；无版本可报告 |
| 官方 headless 能力 | `opencode serve`（headless HTTP，OpenAPI 3.1 于 `/doc`，`GET /global/health`、`GET /global/event` SSE、`/session`、`POST /session/:id/abort`、`POST /session/:id/permissions/:permissionID`）；`opencode run [message]` 非交互，`--format json`（原始 JSON 事件）、`--attach` 复用已启动的 server；server 可用 `OPENCODE_SERVER_PASSWORD` 做 Basic Auth |
| 依赖 | 需要额外 npm/bun 运行时与包管理；本项目 `pyproject.toml` 为**零第三方 Python 依赖**，引入 TS 生态需单独权衡，且不能默认安装 |
| 实测方法 | `command -v opencode` → 不存在；`npm ls -g --depth=0` 显示仅 `corepack` / `npm` / 一个无关包；仅核对官方资料 |
| 没有测的内容 | 安装、运行态清理、事件兼容、模型提供者授权、权限回调、取消 |
| 判定 | 研究/实验，不纳入正式自动支持 |

### 7. 手工交接（WorkBuddy / TRAE / GUI-only） — `EXISTING`

`par task handoff --to APP` 与 `par task import` 已在 T09 落实：导出包给出模式、`workspace_absolute_path`、marker、目标、允许路径、检查与导入指引；导入核对 task/export/base 与真实导出路径，旧包、副本、伪造标记都点名拒绝。见 [HANDOFF.md](HANDOFF.md)。它不依赖任何 UI 自动化或厂商接口，因此不受上述账号/条款变化影响；但**它是人工路径，不是自动 Provider**，也不能用来宣称 P3 的自动执行 Gate 已通过。2026-11-07 用 WorkBuddy 5.9.3 在 macOS 15.6.1 上真实重跑了 R2 交接：Kernel `unit` 检查 PASS、边界 0 违规、任务停在 `REVIEW_REQUIRED`（[报告](reports/WORKBUDDY_REAL_HANDOFF_20261107.md)）。

## 与平台证据的关系

本文的 `GREEN/YELLOW/RED` 描述的是**工具/协议与账号路径**，不是操作系统支持。同一候选在不同 OS 上的可执行风险、进程组/信号、文件权限与签名校验另见 [SECURITY.md 的平台验证分级](SECURITY.md)：macOS `E2E`、Linux `CORE_EXPERIMENTAL`（CI 只实跑纯逻辑安全子集）、Windows `STATIC_GAP`。即使某天某个工具取得 `GREEN`，也只代表**当时记录的版本 + OS + 账号类型**下通过，不能跨版本或跨平台外推。

## 对现有文档的影响

- [AGY.md](AGY.md) 记录的是 2026-10-15 的 `agy` 1.3.5 真实烟测（技术可行性证据）。**技术可行 ≠ 条款许可**：官方 FAQ 现已明确禁止第三方封装。该适配器必须保持「实验/风险、默认禁用」，且文档中不得把它写成正式支持，也不得指导规避使用限制。
- [CODEX.md](CODEX.md) 中「PATH 上的 `codex` 是坏的安装」属于作者环境结论，不作为通用兼容承诺；内置二进制当前是 alpha 版本，升级后需按 §12 复核。
- [ADAPTERS.md](ADAPTERS.md) 的「接入真实 Provider 的清单」仍是**工程检查表**，不是兼容性声明。本文的矩阵才是公开标签的唯一依据。

## 复现本次离线探测

```bash
python -B tests/tools/provider_probe.py                    # 只做 which()，不启动任何外部程序
python -B tests/tools/provider_probe.py --versions        # 额外执行 <cli> --version（离线、不调用模型）
python -B tests/tools/provider_probe.py --help-flags      # 额外检查 <cli> --help 里的 headless 参数
```

输出中 `evidence_level = STATIC_OFFLINE`、`model_calls = 0`、`network_calls = 0`，并显式列出 `not_tested`。`tests/unit/test_provider_probe.py` 保证探测脚本不会偷偷执行外部命令、超时受限、失败被记为事实而不是异常。宿主沙箱另有独立探测：

```bash
python -B tests/tools/sandbox_probe.py --json   # 能否创建嵌套 OS 沙箱 + 继承沙箱的原始 sandbox_check 值
python -B tests/tools/codex_revalidate.py --check-only   # 一键跑 §12 离线复核（8/8 + 3/3），零模型调用
```

`codex_revalidate.py` 在允许嵌套沙箱的宿主上可用 `--yes-use-quota --model <slug>` 补跑真实 R1/R2；
在沙箱阻塞宿主上会 **exit 2 并明确拒绝**（0 次模型调用），不会用提权参数把结果做绿。
`tests/unit/test_codex_revalidate.py` 守住这条门。

## T14 决策（2026-11-06 已由用户作出范围决策）

**结论：`CLOSED_BY_SCOPE_DECISION` —— 不新增自动 Provider（此结论不变）；现有 Codex 为 `GREEN`（默认沙箱已复核），仍无其它通用 `GREEN` 候选。**

原状态 `BLOCKED_BY_EVIDENCE` 是「缺少用户授权与真实证据」。用户在 2026-11-06 明确授权解锁，
并在四条官方路由中选定「**用 ChatGPT 订阅 + `codex exec` 官方路径做真实复核，不新增 Provider**」，
同时要求**先不打 tag**。据此：

1. 已用**官方允许的账号路径**（ChatGPT 订阅，官方 `codex exec` 面向脚本 / CI）在**非敏感夹具**上完成
   真实 `probe`（无模型调用）、R1、R2，并记录版本与 OS → [第一部分](reports/CODEX_REAL_REVALIDATION_20261106.md)。
2. 第一次真实 R2 **未通过**：宿主无法创建嵌套 macOS Seatbelt 沙箱，Kernel 正确判 `FAILED`。
   同日用户追加授权后，第二次在「宿主已由外部 OS 沙箱化」这一 `codex exec --help` 明确写出的
   适用情形下取得**成功的真实 R1/R2**，`§12` 复核完成 → [第二部分](reports/CODEX_REVALIDATION_PART2_20261106.md)。
   **始终不创建新的正式自动适配器、不合并空实现、不把 YELLOW 写成「已支持」**。
3. 第二次真实运行的 `-s danger-full-access` **只在一次性临时 worktree 副本**，主干适配器未改、
   未新增配置开关；没有把任何提权参数写进产品代码或默认配置。未使用个人 Google 账号；
   未接受 Gemma 条款下载本地模型；未打 tag / 发 Release。
4. `TESTED_VERSION` 更新为 `0.166.0-alpha.6`，`par health --probe` 不再提示 `untested version`。
   **2026-11-07 已在允许创建嵌套沙箱的普通终端补跑产品默认 `-s` 模式，取得成功的真实 R1/R2，
   等级从 `GREEN (scoped)` 升为 `GREEN`** → [第三部分](reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md)。

**已完成的最后一步**：`python -B tests/tools/codex_revalidate.py --yes-use-quota --model gpt-6-luna`
在普通终端 exit 0（offline 8/8、trust 3/3、real_smoke rc=0），2 次真实调用均为默认沙箱模式。
若将来要**新增**（而不是只复核）一个自动 Provider，还需付费 Gemini API key / 企业许可等官方允许路径
与新的真实证据，而不是一个配置开关。

P3 的发布范围仍只包含：已有的 Codex（`GREEN`，默认沙箱已复核）、实验性的 Antigravity（默认禁用、带条款风险提示）、
通用人工交接，以及本文的研究与真实复核结论。**不宣称已实现自动 Supervisor、同项目多个 Worker 并行或
自动真实仓库写入。**
