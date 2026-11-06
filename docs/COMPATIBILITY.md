# 兼容性证据矩阵（2026-11-06，P3/T13）

> **本文是静态能力研究，缺真实测试。** 本次调查只做了：本机离线 `--version` / `--help`（不调用模型、不联网、不读凭据）、以及公开官方资料的核对。**没有运行任何真实 R1/R2 任务、没有登录、没有消耗额度、没有验证任何账号的条款许可。** 因此本轮**没有任何 `GREEN` 候选**；T14 的状态是 `BLOCKED_BY_EVIDENCE`，不得创建「看起来支持但不会跑」的适配器。
>
> 「官方存在某个参数」不等于「PAR 在现有隔离与授权模式下能安全工作」。本文每一行都列出证据等级、版本、OS、账号类型、实测方法和**没有测的内容**。可复现的离线探测脚本：`python -B tests/tools/provider_probe.py`（默认只做 `which()`，不启动任何外部程序；`--versions` / `--help-flags` 才执行离线命令）。

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

## 证据等级定义

| 等级 | 含义 |
|---|---|
| `GREEN` | 官方明确支持的接口，**在可用环境完成**最小 R1 + R2、权限、错误与取消样本，并核实账号/条款许可，适合作为正式自动适配路径 |
| `YELLOW` | 技术可试（官方文档/参数存在），但缺真实运行、权限、授权或条款证明；**仅实验** |
| `RED` | 无合规接口、隔离无法确定、或明显依赖规避条款；不纳入自动正式支持 |
| `EXISTING` | 已有的正式/实验实现，本轮只做版本与文档复核，**不是本轮新增证据** |

## 候选排序（给 T14 的决策）

| 排序 | 候选 | 等级 | 一句话理由 |
|---|---|---|---|
| 1 | **Antigravity CLI / `agy`** | `RED` | 官方 FAQ 明确禁止把 `agy` 包在第三方编码 Agent/应用中，且消费者订阅不支持自动账号轮换；PAR 正是第三方 runtime |
| 2 | **Gemini CLI** | `YELLOW`（偏 RED） | 官方 headless 接口齐全，但 2026-06-18 起免费层 / Google AI Pro / Ultra 已停服；本机 0.44.1 是旧版本，账号类型未确认，未跑真实任务 |
| 3 | **Claude Code CLI** | `YELLOW` | 官方 `-p` + `stream-json` + `--bare` + 权限模式齐全，但本机未安装 CLI；官方 headless 文档未对消费者订阅的程序化使用给出条款许可；脚本化路径要求 API key 或云凭据 |
| 4 | **Codex** | `EXISTING` | 已有适配器使用的正是官方 `codex exec --json`；但本机仅内置 alpha 版本，未重新做真实 R1/R2 复核 |
| 5 | **ACP（Agent Client Protocol）** | `YELLOW` | 协议本身公开且权限回调语义明确，但 v2 仍是 draft、本机无任何 ACP agent 可验证；不是「一个能跑的 Provider」 |
| 6 | **OpenCode** | `YELLOW` | 官方有 `opencode serve` / `run` headless 与 SSE/OpenAPI，但需额外 npm/bun 运行时，与本项目零第三方依赖原则冲突，本机未安装 |
| 7 | **WorkBuddy / TRAE / GUI-only** | `EXISTING` | 手工交接路径已在 T09 落实并测试；不是自动 Provider，不参与本排序的 GREEN 判定 |

**结论：没有 `GREEN`。T14 = `BLOCKED_BY_EVIDENCE`。** 不新增任何正式自动 Provider，不把「实验」写成「已支持」。要解锁，需要先由用户明确授权：使用哪条账号路径（企业许可 / 官方 API key / 明确允许的订阅用法）、在**非敏感夹具**上完成真实 R1 + R2 + 取消 + 权限 + 额度/错误样本，并记录版本与 OS。

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

### 4. Codex — `EXISTING`

| 项 | 内容 |
|---|---|
| 官方资料 | [Non-interactive mode](https://developers.openai.com/codex/noninteractive) · [Agent approvals & security](https://developers.openai.com/codex/sandbox) · [CLI reference](https://developers.openai.com/codex/cli/reference) · 项目内 [CODEX.md](CODEX.md)（历史真实烟测记录） |
| 版本 / OS | 本机 PATH 无 `codex`；ChatGPT.app 内置二进制 `codex --version` → **`codex-cli 0.166.0-alpha.6`**（离线实测，alpha 预览版）。历史文档记录的是更早的已签名版本 |
| 官方 headless 能力 | `codex exec --json`（JSONL：`thread.started` / `turn.*` / `item.*` / `error`）；默认只读沙箱；`--sandbox read-only\|workspace-write`；`--ask-for-approval`；`--ephemeral`；`--ignore-user-config` / `--ignore-rules`；**`--full-auto` 已不再支持**（官方明确要求改用 `--sandbox workspace-write`）；macOS 用 Seatbelt、Linux 用 bwrap+seccomp |
| 与现有适配器的一致性 | `src/par/providers/codex.py` 的 `build_argv` 使用 `exec --json --color never --ephemeral --skip-git-repo-check -s <workspace-write\|read-only> -m <model> -C <cwd> -`，**与官方文档一致**，未使用已废弃的 `--full-auto` |
| 账号 / 条款 | 现有实现依赖 ChatGPT 订阅登录（`CODEX_HOME`）；官方 `codex exec` 明确面向脚本与 CI |
| 实测方法 | 仅 `codex --version`（离线）；未运行 `codex exec` |
| 没有测的内容 | 当前 alpha 版本的真实 R1/R2、签名校验、登录状态、`--skip-git-repo-check` 与无 `.git` 副本的配合、额度/取消/错误分类的全部复核 |
| 判定 | 属于「维护、修正默认安装路径与版本信息，保留已验证边界」，**不是 T14 的新增正式自动路径**。alpha 版本不能冒充已验证版本；升级复核清单见 [CODEX.md §12](CODEX.md) |

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

`par task handoff --to APP` 与 `par task import` 已在 T09 落实：导出包给出模式、`workspace_absolute_path`、marker、目标、允许路径、检查与导入指引；导入核对 task/export/base 与真实导出路径，旧包、副本、伪造标记都点名拒绝。见 [HANDOFF.md](HANDOFF.md)。它不依赖任何 UI 自动化或厂商接口，因此不受上述账号/条款变化影响；但**它是人工路径，不是自动 Provider**，也不能用来宣称 P3 的自动执行 Gate 已通过。作者曾在 macOS 上做过一次真实 WorkBuddy 交接（见 [HANDOFF.md](HANDOFF.md) 的实测教训），版本需重核。

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

输出中 `evidence_level = STATIC_OFFLINE`、`model_calls = 0`、`network_calls = 0`，并显式列出 `not_tested`。`tests/unit/test_provider_probe.py` 保证探测脚本不会偷偷执行外部命令、超时受限、失败被记为事实而不是异常。

## T14 决策

**`BLOCKED_BY_EVIDENCE`**：本轮没有 GREEN 候选，因此不创建新的正式自动适配器，不把 YELLOW 写成「已支持」，不合并任何空实现。解锁条件（需用户明确授权后逐项留证据）：

1. 选定**官方允许**的账号路径（企业许可 / 官方 API key），并书面确认第三方 runtime 使用该接口的条款许可；
2. 在**非敏感夹具**上完成真实 `probe/no model call`、R1 只读、R2 限定写、取消/超时、认证失败、额度/限流分类、敏感数据边界，记录工具版本与 OS；
3. 按 [ADAPTERS.md](ADAPTERS.md) 的接入清单实现，默认禁用、用户显式配置才生效，并补齐 fake CLI 与真实样本测试；
4. 更新本文的证据等级与 [CODEX.md](CODEX.md) / [AGY.md](AGY.md) 的版本记录。

在上述条件达成前，P3 的发布范围只包含：已有的 Codex（需版本复核）、实验性的 Antigravity（默认禁用、带条款风险提示）、通用人工交接，以及本文的研究结论。**不宣称已实现自动 Supervisor、同项目多个 Worker 并行或自动真实仓库写入。**
