# 安全边界（P3）

## 一句话

Runtime 只写自己的数据目录；对**真实项目**默认只读：只有你亲自 `par project enable-write` 打开、并且每个写任务逐项批准后，Worker 才会在**私有副本**里改文件，补丁不会自动应用到项目；唯一由 Runtime 直接写进项目的情况是 `par project add --scaffold-harness`（你显式加这个开关才会发生）：只为项目里缺失的 `AGENTS.md`/`.ai/STATE.md` 各补一份极简通用模板，绝不覆盖已存在的文件，仍受同一套 `WriteGuard`/`protected_roots` 保护；启动的外部程序只有：git（只在它自己建的目录里、干净环境）、进程运行器启动的 Worker 与验收检查（Worker 是 Codex 官方 CLI 或脚本化的测试替身；**真实模型调用只发生在你逐项批准之后**）、Codex 的几个不调用模型的探测子命令与 `codesign`、Core 自带的检查脚本；不读、不复制任何凭据；输出一律先脱敏。

## 受保护 / 不保证 / 由你负责（P3/T15 审计矩阵，2026-11-06）

> 这张表只描述**Runtime 自己能保证的事**。第三方 CLI 的行为、操作系统隔离、账号条款都不在「受保护」一列里。每条承诺都能追到代码或测试；找不到证据的一律写进「不保证」。证据索引：`tests/unit/test_p3_security_audit.py`（22 项集中负面测试）、`tests/unit/test_policy.py`、`test_contract.py`、`test_workspace.py`、`test_generic_context.py`、`test_providers_fake.py`、`test_redact.py`、`docs/COMPATIBILITY.md`（外部工具的版本/条款）。

### A. 受保护（Runtime 自己执行、有回归测试）

| 承诺 | 机制 | 负面证据 |
|---|---|---|
| 路径不能越出合同 `allowed_paths` | `normalise_allowed_path` + `path_allowed` + `classify` | `PathEscape.test_dotdot_absolute_tilde_backslash_and_control_characters_are_refused`、`test_normalised_dotdot_cannot_escape_the_allowed_prefix`、`UnauthorisedWrite.test_a_change_outside_allowed_paths_is_a_violation` |
| `.git/`、`.ai/`、`AGENTS.md`、`CLAUDE.md`、疑似凭据路径永远不可改 | `forbidden_reason`；`is_sensitive_path` 同时按 `/` 与 `\` 拆分（含 Windows 盘符/UNC，2026-11 加固，PR #17 / M672） | `PathEscape.test_protected_and_sensitive_names_win_over_an_allowed_prefix`、`test_windows_style_sensitive_paths_are_denied_by_name`、`test_policy.py::test_windows_drives_unc_and_backslashes_are_not_a_bypass` |
| 符号链接、`..`、同前缀兄弟目录、受保护根不能绕过写入 | `WriteGuard` 先 `realpath`，NFC + casefold 比较 | `PathEscape.test_a_write_guard_refuses_a_sibling_with_the_same_prefix_and_a_symlink_escape`、`test_a_protected_root_is_never_writable_even_inside_an_allowed_root` |
| 只读任务不允许任何改动；符号链接/特殊文件不算成果 | `classify(writes=False)`；`classify` 拒绝非 file 条目 | `UnauthorisedWrite.test_a_read_only_task_may_change_nothing_even_inside_allowed_paths`、`test_a_symlink_or_special_file_is_never_accepted_as_work` |
| 验收检查不拼 shell、不查用户 PATH、不带凭据、不跑安装/发布/内联代码 | 合同 argv 白名单 + `resolve_executable` + `check_env` | `CheckSideEffects.test_shell_install_publish_and_inline_code_are_not_check_programs`、`test_the_check_environment_has_no_secrets_and_a_fixed_path`、`test_python_is_resolved_to_the_kernel_interpreter_not_the_users_path` |
| 合同里的疑似凭据、终端控制字符、重复键、未知字段被拒绝 | `parse_contract` 与配置校验器 | `HostileProjectData.test_a_contract_cannot_contain_a_secret_or_terminal_control_characters`、`test_contract.py` |
| Worker 的自述永远是「未核实」，项目规则文本不能扩大权限 | `evidence_summary.claim.verified = False`；清单内容只是数据 | `HostileProjectData.test_worker_words_are_never_verified_in_a_result_envelope`、`test_project_rule_text_cannot_widen_the_task_scope`、`test_generic_context.py::test_instructions_in_agents_md_are_context_not_authority` |
| 未知厂商事件、退出码 0 但无终态、截断输出都不能变成成功 | 适配器 `parse_event` 只保留结构化字段；`classify_failure` 要求 `turn.completed` | `UnknownProviderEvents`（4 项）、`test_codex_adapter.py`、`test_providers_fake.py` |
| 写权限/换 Worker/漂移确认都要逐项批准，重试不复用旧批准 | Kernel 审批闸门（Issue #1 修复） | `test_kernel_control.py`、`M70` / `M627`–`M630` |
| 补丁不会自动应用到真实项目；`ACCEPTED` ≠ `APPLIED` | Kernel 只保存 `work.patch` | `test_kernel_run.py`、`test_candidate_integration.py` |
| 台账只增、attempt 不可变、数据目录 0600/0700 | SQLite 触发器 + `WriteGuard` | `test_store.py`、`assert_private_layout` |

### B. 不保证（Runtime 明确做不到，不要误读为承诺）

| 事实 | 为什么 |
|---|---|
| **不是操作系统沙箱**：私有克隆不阻止外部 CLI 以同一用户身份读取磁盘上任何可读文件，也不阻止它主动联网 | 进程隔离由各厂商 CLI 自己的沙箱提供（Codex 的 sandbox 管写与网络，**不管读**）；`test_workspace.py` 只验证 Kernel 自己的副本 |
| 验收检查**会执行 Worker 补丁里的代码**（测试的本质），可启动子进程、读写其他路径 | 只有一次性副本、干净环境、固定可执行文件、独立进程组与超时；没有 `sandbox-exec`/容器。T14 之前真实项目不可提交写任务，风险限于夹具 |
| 脱敏是尽力而为，不保证识别所有形态的机密 | 首要原则是不去读机密；`test_redact_perf.py` 保证线性时间 |
| 私有 HOME / 干净环境不等于「零持久化」 | 厂商 CLI 仍可能写自己的诊断日志或续期令牌；`--ephemeral` 只是不写会话文件 |
| 恢复判断依赖心跳与时钟；跨睡眠可能误标 `RECOVERY_REQUIRED` | 不丢数据，也不会启动第二个写入者 |
| Worker 自行 `setsid` 脱离进程组后无法追踪/终止 | Kernel 只杀自己确认身份的进程组 |
| **账号与条款合规** | 见下方「由你负责」；技术隔离不能让被厂商条款禁止的用法变得合规 |
| Windows / Linux 的完整功能 | 见「平台验证分级」：目前只有 macOS 端到端，Linux 核心 experimental，Windows 仅有静态缺口清单 |

### C. 由你负责（Runtime 不替你决定）

- 选择账号/模型/额度，确认该厂商的服务条款允许这种用法。**Google Antigravity 官方 FAQ 明确禁止把 `agy` 封装在第三方编码 Agent 中**（消费者订阅不支持），Gemini CLI 自 2026-06-18 起停止为免费层 / AI Pro / Ultra 服务——见 [COMPATIBILITY.md](COMPATIBILITY.md)。PAR 的 `agy` 适配器因此实验性、默认禁用。
- 决定项目里哪些文件可以给外部模型看、是否允许联网、是否接受第三方 CLI 的读范围。
- 亲自 `par project enable-write` 打开写入、逐项批准每个写任务、审阅 diff 后才 `git apply`。
- 手工交接时决定能否把项目文件交给那个 GUI 应用的云端模型（每个交接包 README 都写了数据去向）。
- 在收紧读范围并做真实调用验证之前，不要对含敏感文件的真实项目开放写入。

## 平台验证分级（2026-11-06）

| 平台 | 证据等级 | 已验证 | 已知缺口 |
|---|---|---|---|
| **macOS 15.6.1 / arm64** | `E2E`（本机 + CI） | 安装、`par doctor/version`、CI 核心子集（16 个模块）、fake CLI 全链路、通用 Git 项目、人工交接包、候选整合、证据分层；历史真实 Codex / `agy` 烟测记录见 [CODEX.md](CODEX.md) / [AGY.md](AGY.md) | 内置 codex 二进制当前是 `0.166.0-alpha.6`（alpha）；真实 R1/R2 需按版本升级复核清单重跑。WorkBuddy 手工流程是历史 macOS 实测，版本需重核。CI 里 4 个进程终止类模块在本沙箱失败（无法向子进程组投递信号），失败集合与未修改基线完全一致 |
| **Linux (ubuntu-latest, GitHub Actions)** | `CORE_EXPERIMENTAL`（**CI 实跑纯逻辑核心**） | 新增 `linux_security_core` 作业：`pip install -e .`、`par doctor`（对未测平台明确报 WARN 但退出 0）、`par version`，以及 125 项平台无关回归（状态机、合同、路径策略、脱敏、数据目录、离线 probe、21 项 T15 安全审计） | 真实适配器（`codesign` 签名校验、Seatbelt/bwrap 沙箱、钥匙串链接）是 macOS 实现，**不能据此宣布 Linux 支持真实 Provider**；进程/信号相关模块仍只在 macOS 跑。`TRUSTED_DIRS` 含 `/opt/homebrew/bin`，Linux 上需重核可执行文件解析。CI 日志以具体 run 为准 |
| **Windows** | `STATIC_GAP`（仅静态缺口） | 2026-11 已加固：Windows 盘符 / UNC / 反斜杠形式的敏感路径不再漏判（PR #17） | 进程组与信号（`os.setsid`、SIGKILL）、文件权限位（0600/0700）、`codesign` 签名、`realpath` 与大小写/Unicode 规范化、盘符/UNC 在项目路径中的语义、`protected_roots` 的 `Library/…` 布局、`git` 行为、检查可执行文件白名单，全部**未验证**。不宣布 Windows 支持，也不为「全平台」放宽安全行为 |

CI 有三个作业：`smoke_install`（macOS）、`core_unittests`（macOS，含审批/状态机/交接/候选整合等）、`linux_security_core`（Linux，纯逻辑安全子集）。关键安全行为（路径策略、合同白名单、脱敏、敏感名、未知事件）**没有**因为平台差异被 `skip`，且在 Linux 上重复实跑一遍；宿主 macOS 特定假设写在适配器与 `paths.py` 里，作为上面的已知缺口列出。要把 Linux 升到 `CORE_EXPERIMENTAL` 之上，仍需要在 Linux 上真实跑一次 fake CLI 全链路与真实 Provider 的隔离/权限样本。

## 它能写什么、不能写什么

> **第三方条款不是 Runtime 能替你承担的风险**：外部 CLI 的服务条款由你与该厂商决定。2026-11-06 核对到 Google Antigravity 官方 FAQ 明确禁止把 `agy` 封装在第三方编码 Agent/应用中（消费者订阅不支持），以及 Gemini CLI 自 2026-06-18 起停止为免费层 / Google AI Pro / Ultra 提供服务。PAR 的 `agy` 适配器因此保持实验性、默认禁用；启用任何外部 Provider 前请看 [COMPATIBILITY.md](COMPATIBILITY.md) 的账号/条款列。Runtime 的技术隔离（私有 HOME、干净环境、文件比对）不能让一种被厂商条款禁止的用法变得合规。
>

| 位置 | 能否写入 | 机制 |
|---|---|---|
| `~/Library/Application Support/PersonalAgentRuntime`（0700） | 能 | `WriteGuard` 以它为唯一允许根 |
| `<数据目录>/candidates/<id>/repo`（0700） | 能，仅 Kernel 自己 | 候选整合只在这里应用已验收的 `work.patch` 并提交；补丁路径仍过 `forbidden_reason`，真实项目永远不写 |
| Harness Core、`AI_Harness_History`、`~/ai-harness-backups` | **不能** | `protected_roots` |
| `~/.claude` `~/.codex` `~/.gemini` `~/.workbuddy` `~/.config/kilo` `~/.copilot` 等厂商目录 | **不能** | `protected_roots` |
| `~/.zshrc` 等 shell 与 git 用户配置、`~/.local/bin`、`Library/LaunchAgents` | **不能** | `protected_roots` |
| 任何名字像凭据的路径（`.env`、`*.pem`、`auth.json`、`runtime/accounts/…`、`.deploy/` 等） | **不能** | `is_sensitive_path`，拒绝时不回显路径名 |
| 通过符号链接、`..`、大小写或 NFC/NFD 差异绕过 | **不能** | 先 `realpath`，保护项比较用 NFC 加 casefold |
| Windows 盘符 / UNC / 反斜杠形式的敏感路径 | **不能** | `normalise_allowed_path` 直接拒绝反斜杠；`is_sensitive_path` 同时按 `/` 与 `\` 拆分（POSIX 上反斜杠本是合法文件名字符），避免 `C:\Users\me\.ssh\id_rsa` 被当成一个无害名字 |
| 真实项目里缺失的 `AGENTS.md` / `.ai/STATE.md`（仅 `par project add --scaffold-harness`） | 能，但只补缺失的那个 | `WriteGuard(allowed_roots=(项目根,), protected=protected_roots)`；`O_CREAT\|O_EXCL` 防并发覆盖，已存在的文件永远不碰 |

数据目录已存在但属主、权限或类型不对时，**只报告、不修复**（它可能不是我们建的）。

## 它能执行什么

| 命令 | 谁在用 | 约束 |
|---|---|---|
| 只读 git 子命令：`rev-parse` `log` `status` `rev-list` `config --get` `ls-files` | `par harness check` | 固定带 `--no-optional-locks`、`core.fsmonitor=false`；不刷新索引、不起守护进程；`status` 最多处理 10 万行 |
| `python -B -I <Core>/scripts/harness_hygiene.py` 与 `sync_runtime_core.py --check` | `par harness check` | `-B` 不产生 `.pyc`，`-I` 隔离环境；脚本本身是只读的 |
| 无 | `par doctor`、`par version`、`par init` | `doctor` 不执行任何外部程序、不联网，有测试强制 |
| `git clone --no-hardlinks`（源项目只读）、`checkout --detach`、`remote remove`、`update-index --skip-worktree`、`check-ignore`、`add -N`、`diff`、`apply` | Kernel（`execution/gitops.py`） | 只在 Kernel 自己建的目录里运行（对源项目只做 `rev-parse` 与克隆）；每次调用都是干净环境：无用户/系统 git 配置、无 hooks、无 pager、不提示、字面路径；提交号必须是 40 位十六进制 |
| 进程运行器启动的 Worker（argv 来自适配器：Codex 官方 CLI，或测试替身） | Kernel（`execution/subprocesses.py`） | 独立会话与进程组；环境只有固定 PATH、私有 HOME 与 TMPDIR 加适配器声明的变量；提示词走标准输入；三类超时；输出先脱敏、有上限；只强杀自己启动的进程组 |
| Codex 的探测：`--version`、`login status`、`features list`、`mcp list --json`、`debug models`；以及 `/usr/bin/codesign --verify/-dv` | Codex 适配器（`par health --probe` 与每次 attempt 的 preflight） | **不调用模型**（有测试证明从不运行 `exec`）；干净环境、一次性 HOME、30 s 超时；结果缓存（完整的隔离发现 5 分钟，另在每次 attempt 前复查一次；签名 10 分钟；登录 60 秒）；只在适配器已注册（配置里启用且绝对路径）时才会发生 |
| 验收检查（`acceptance.checks`） | Kernel（`execution/validation.py`） | 不经 shell；可执行文件限于 `python3 pytest ruff mypy tsc node npm make go cargo swift` 且逐个有参数规则；从固定目录解析，不查 PATH；在一次性的验证副本里运行 |

可选的定期巡检（`ops/par-harness-patrol.sh`，由 launchd 启动，**默认未安装**）是 Python 之外的一层：它只运行 `par harness check`，只在需要关注时调用 `osascript` 发通知，AppleScript 源码是固定文本，检查输出只作为参数传入，不会被当作代码（有注入测试）；它只写数据目录里的 `harness/last_patrol.txt` 与 `.err`。

白名单在代码里（`harness/check.py::_allowed`），任何不在其中的命令会被拒绝；`git push/commit/add/reset`、改写 git 配置、其他脚本都有对应测试。

## 它读什么

- 只读 `AGENTS.md`、`CLAUDE.md`、`.ai/STATE.md`、`.ai/tasks/` 的目录名与文件数，以及 Core 的受管文件。
- **不读**：任何凭据文件、工具会话、`runtime/accounts/`、`.env*`、项目业务数据。敏感路径只计数、不列名。
- 基线文件只存结构事实：版本号、哈希、大小、行数、计数。**不存**章节标题、任务编号、嵌套路径、账户名。有测试检查这一点，因为 Runtime 台账不能变成项目知识的第二份拷贝。

## 项目登记与上下文适配器（P1）

- 登记只记录 id、路径、vcs，不记录项目里有什么；真实项目的 `write_enabled` 默认 `false`，只有同时带 `write_approved = "YYYY-MM-DD"`（你批准的日期）且 `vcs = "git"`（回滚靠 git 基线）才被接受，别的写法整条丢弃；受保护位置、重复目录、带密钥形键名的条目整条丢弃。
- 适配器只读：读 `AGENTS.md`、`CLAUDE.md`、`.ai/STATE.md`、active 合同的内容和哈希；事实源指针指向的文件只做 `stat`。其余项目文件不读。
- generic 项目只读 `README.md`、`AGENTS.md`、`CLAUDE.md`（存在才读）并记录大小/哈希；同样拒绝符号链接逃逸与敏感路径，渲染内联内容时重新校验。
- 敏感路径与疑似机密的指针只计数、不列名；越界指针（绝对路径、`..`、符号链接逃逸）不跟随。
- 读取内联内容时重新校验大小与哈希，并拒绝越出项目根或指向敏感路径的条目，伪造的清单无法借此读到别处的文件（有测试）。
- 写入就绪里的 `WRITE_DISABLED` 一直为真，直到你用 `par project enable-write` 打开；其余条件只是告诉你还差什么（generic 项目没有 STATE/主合同这两项）。

## 夹具项目与真实项目（P3）

- 夹具项目始终可写：`par fixture create` 造的夹具（`<数据目录>/fixtures/<id>/`，git 仓库，一个故意的 bug，不含任何个人数据）。加载器**每次**独立核对：是 `fixtures/` 的直接子目录、文件夹名等于 id、带合法 `.par-fixture.json`（id 一致）、文件夹与标记都不是符号链接；任何一项不符，整条登记被丢弃。`par project add` 登记的真实项目默认为只读；手改 `write_enabled = true`、把标记文件拷进真实项目、用符号链接伪装、把路径写成 `..` 绕回，都有对应测试。`--scaffold-harness`（显式才生效）是登记之前的一步，不是写入开关：只为缺失的 `AGENTS.md`/`.ai/STATE.md` 各建一份极简模板，已存在的文件原样不动；受保护位置（Harness Core 等）和敏感路径一样拒绝，不会因为加了这个开关就被绕过。
- 真实项目要**你亲自**打开写入：`par project enable-write ID`（先按 `write_readiness` 体检：基线干净、有 git、git 里看不到敏感路径；Harness 项目还要求 STATE 健康、恰好一份生效的主合同，generic 项目不需要这两项；通过后你再敲一遍项目 id 才会生效）。它只改 `projects.toml` 里这个项目的两行，旧文件留作 `projects.toml.par-backup-<时间>`，并在 `logs/project_writes.jsonl` 追加一行（时间、项目、开/关、当时的提交，没有别的内容）；`par project disable-write ID` 随时关掉。 打开之后每个写任务仍然要过：合同的路径白名单（`.ai/`、`AGENTS.md`、`CLAUDE.md` 永远不可写）、你逐项批准的写权限、私有副本里干活、补丁不会被任何代码应用到项目。没打开的项目照旧被 `WRITE_DISABLED` 挡住（有测试，包括同一个 Kernel 对夹具放行、对没打开的真实项目拒绝）。

## Codex Worker（P3）

- **执行体可信**：绝对路径；文件存在、可执行、不被组/其他用户写、属主是你或 root；macOS 上必须是 OpenAI 签名（团队 ID `2DC432GLL2`）的**二进制本身**，签名要能通过 `codesign --verify --strict`；未签名的包装脚本被拒绝；不查 PATH。
- **登录方式**：每次 attempt 前核对 `codex login status`，只有「Logged in using ChatGPT」放行；API key 登录（按量计费）、未登录、无法识别都拒绝启动。环境里**没有任何 API key**，环境变量不可能把运行切到计费通道。
- **额外能力先关再证明**：每次运行关掉应用/连接器、插件、浏览器与桌面控制、子代理、hooks、`notify`、配置里的 MCP 服务器等，并用 `mcp list` 与 `features list` 证明关干净（`memories` 读作 false）；证明不了就不启动。
- **沙箱由任务决定**：R0/R1 → `read-only`；R2 → `workspace-write`（只能写工作副本，联网被拒）；从不传 `--dangerously-*`、`danger-full-access`、`--ignore-user-config`、`--ignore-rules`、`--add-dir`；`exec` 无法给出审批，被拒的命令只是结果里的警告。
- **读不受限（已实测的真实限制）**：Codex 的沙箱不限制**读**。Worker 能读到你磁盘上任何你能读的文件，包括 `~/.codex/auth.json` 与其他项目；离线实测已确认。联网在沙箱内被拒，所以泄露路径只有模型对话本身（发往 OpenAI）与我们的日志（写盘前脱敏，尽力而为）。P3 只在夹具上运行，这不构成风险；**进入 P8 前必须收紧可读范围**（已离线验证一种做法，见 [CODEX.md §5](CODEX.md)）。
- **你的 Codex 全局配置继续生效**（模型、`reasoning_effort = high`、规则……）：高推理强度会让每次调用更耗额度。
- **Codex 会把「可写的 git 工作副本」记进你的全局配置（真实烟测发现，已规避）**：在 git 工作副本里以 `workspace-write` 运行，Codex 启动时会往 `~/.codex/config.toml` 追加一条 `[projects."<副本路径>"] trust_level = "trusted"`（每次 attempt 一条；`-c` 覆盖挡不住）。适配器因此要求 Kernel 给 Codex 的 Worker 副本**去掉顶层 `.git`**（Kernel 从不使用 Worker 的仓库，影响为零，还去掉了改写 `.git/config` 的攻击面）。有测试与变异检验；真实回归测试见 [CODEX.md §9](CODEX.md)。修复前的第一次 R2 烟测留下的那条条目已按你的批准备份并删除；修复已用一次真实的写入调用复验（`config.toml` 未被改动）。
- **日志**：Codex 的 stdout/stderr 由 Kernel 脱敏、限长后写入 0600 的日志；事件解析不保留原始行；Worker 的自述进入结果前脱敏并截断。
- **CLI 自己的留存**：`--ephemeral` 不写会话文件，但 CLI 仍会在 `~/.codex` 里写诊断日志，并可能续期令牌；`par` 不碰这些，也不声称零持久化。

## Antigravity 适配器（P4）

逐项依据见 [AGY.md](AGY.md)。要点：

- **绝对路径 + Google 签名**：可执行文件必须是配置里写明的绝对路径、Mach-O、`codesign --verify --strict` 通过且团队 ID 是 `EQHXZ8M8AV`；脚本、别的团队、PATH 上的 `agy` 一律拒绝。安装是私有的（`vendor/agy/<版本>/agy`，目录 0700）：先核对官方清单里的 SHA-512，只解出压缩包里唯一的成员，**在第一次运行之前**不执行地核对签名；**不运行官方 `install.sh`**（它会改 shell 配置并执行 `agy install`）；Runtime 永不运行 `agy update` / `agy install`。
- **`agy` 看不到你真实的 `~/.gemini`**：每个 Worker 的 HOME 是 attempt 的私有目录；`agy` 的配置只能靠 HOME 改位置，实测它完全尊重 HOME（运行前后 `~/.gemini`、shell 配置、`~/.config`、`~/Library/*` 无任何变化）。旧 `gemini` CLI 的凭据与设置、IDE 的数据、全局 `GEMINI.md`、全局 MCP/hooks/sidecar 配置对它都不存在。
- **权限预设由 Runtime 写入而不是继承**：私有 HOME 里的 `settings.json` 由适配器写（只读任务 `strict`，写任务 `request-review`；沙箱开、请求关闭遥测（**CLI 是否生效未验证**：它改写 `settings.json` 时丢掉了这个键）、`useG1Credits=false`、`allowNonWorkspaceAccess=false`；只读任务没有 `permissions` 允许规则；写任务只有 `write_file(<合同允许的路径>)` 规则（第二次真实写烟测发现：无头模式下 `request-review` 连工作副本内的文件写入也软拒绝；规则只来自合同的 `allowed_paths`，绝不含 `command(...)`、通配符或 `regex:`，写入前逐条核对；已被真实 agy 1.3.5 验证：规则被读到、保留并生效，见 AGY.md §11 第 10 项），也没有 `modelProvider`，API key 计量模式无法被打开）。`init` 事件报告的权限模式比请求的**宽**（或缺失）→ 这次运行判为失败；没有 `init` 的「成功」也不被信任。
- **软拒绝不是静默成功**：需要审批的工具被跳过、运行继续、退出码 0——适配器把它变成结果上可见的警告，由 Kernel 自己的文件比对与检查裁决（只读任务有任何改动即越界；写任务没改成 → 检查失败）。
- **登录是钥匙串里你已有的 Google 登录**：私有 HOME 里只放一个指向 `~/Library/Keychains` 的**符号链接**（不是拷贝；Runtime 从不打开它指向的东西），而且**只在 `agy` 运行期间存在**：子进程一结束适配器的 `cleanup` 钩子就删掉它，证据目录里不留指向你钥匙串的指针（有测试证明目标不受影响、真实目录不会被当成链接删除）。**私有 HOME 绝不能是你的真实 home**：`write_home` / `login_plan` 一开始就拒绝（P4 里一次变异检验误写过真实 home，已还原并加了这条规则与变异检验的安全网，见 AGY.md §11）。**实测：去掉这条链接，`agy` 就认不出登录**（所以它读的不是文件里的令牌）。Runtime 不经手任何凭据；`par provider login antigravity` 只是在 Runtime 自己的 HOME 里启动官方的交互登录（在你已有登录时用不上）。
- **项目自带的 hooks / MCP / 插件一律拒绝**：`.agents/hooks.json` 会执行 shell 命令，`.agents/mcp_config.json` 会启动进程；`.agents/` 下只接受 `rules`、`skills`、`rules.json`、`AGENTS.md`、`GEMINI.md`，未知条目也拒绝。
- **环境里没有任何 API key**：只放行代理/证书变量与关闭自更新的开关（`AGY_CLI_DISABLE_AUTO_UPDATE=true`，每次启动都设）；`GEMINI_API_KEY`、`GOOGLE_API_KEY`、`AGY_ADC_AUTH`、`GOOGLE_GEMINI_BASE_URL` 等有测试保证传不进去。
- **登录检查用 `agy models`，不用 `-p /usage`**：实测未登录时 `-p /usage` 会打印 Google 登录网址并**等 60 秒**，而 `models` 立刻失败。
- **已知缺口（诚实声明）**：①子代理——官方说主代理决定子代理的工具与权限，理论上可能绕过 `strict`，由 Kernel 的文件比对兜底，真实烟测需专门观察；②文件读写工具不在 `--sandbox` 里（沙箱只管命令），读范围靠 `allowNonWorkspaceAccess=false`，与 Codex 一样 P8 之前要评估系统级收紧；③模型运行时的真实行为（软拒绝措辞、写入是否被自动允许……）要等获批的真实烟测才能确认，见 AGY.md §12；④**官方 FAQ 的使用条款提示**：把 `agy` 包进第三方编码代理/应用、或配合第三方软件使用 Antigravity 登录，官方写明在消费者套餐下不受支持（甚至违反条款、可能导致账号被暂停）。这是账号层面的政策风险，由你决定边界，见 P4 报告。

## P5–P6 新增的面（MCP 桥、接力与交接）

- **MCP 桥（`par mcp serve`）**：17 个工具——读写任务的 7 个（`submit_task` `get_task_status` `get_task_result` `cancel_task` `review_task` `run_task` `list_tasks`），2026-11 补上的 `prepare_task`（草案→严格合同，只读、不提交），2026-10-16 补上的调度/恢复 4 个（`retry_task` `pause_task` `resume_task` `recover_task`），2026-10-20 补上的决策 4 个（`approve_task` `reject_task` `accept_task` `close_task`），加 2026-11-03 补上的 `get_task_diff`（脱敏、限长的 diff 内容；此前协调者只看得到 `diff_stat`，没配检查的写任务因此被 Kernel 挡在 `accept_task` 之外，这个工具只是让协调者能读到内容再转述给人，验收本身仍然只有人能做）——名单有测试钉死；源码里扫描不到的只剩 `clear_health` `probe_providers`（健康/配置相关，仍然只有人能做，桥完全不碰）。决策 4 个工具各自只调用 `Kernel.approve`/`reject`/`accept`/`close_task` 这同一套 CLI 已经在用的方法，固定传 `by="coordinator"`——谁决定的，台账里永远查得到，不会因为不再需要人批准就变得不可审计。`retry_task` 不带 `force` 参数，遇到还生效的额度/登录/无 Worker 冷却照样被挡下，不会替你绕过去；`recover_task` 的 `terminate` 只停 Kernel 能用记录的 PID 和启动时间确认身份的进程。提交者固定为 `coordinator`，并带着桥所代表的主控名字，主控被替换后桥不能再提交或启动。参数在碰 Kernel 之前逐项校验（类型、长度、控制字符、嵌套深度、未知参数一律拒绝）；请求行最长 256 KB、不接受批量、不接受重复键；返回的一切先经脱敏、去控制字符、截断（每个答案 ≤ 6000 字符），Worker 的话永远带着 `untrusted_worker_text: true` 与 `summary_verified: false`。`run_task` 只在 `QUEUED` 且没有待批准项时，用干净环境在**脱离会话的子进程**里启动 `par task run`，不等待。桥不持有任何模型凭据，不写任何文件，`par mcp config` 只打印要粘贴的配置。
- **接力与人工交接**：被带走的补丁只能是 Kernel 自己切出并交叉核对过的 `work.patch`，且每个路径都要符合合同的 `allowed_paths`；应用时先 `--check` 再应用，并禁止 git 向外找仓库。人工交接的导入把文件夹/补丁/文字全部当作**不可信输出**：先做私有快照（不跟随链接，拒绝特殊文件、疑似敏感文件、超过 1 GiB），再按普通 Worker 的流程做文件比对、边界和检查；应用自己生成的设置目录（`.trae/` `.workbuddy/` …）里**新增**的文件按噪声忽略，已跟踪文件被改照常判越界。Runtime 从不启动这些应用，也不读它们的凭据。数据去向（应用会把打开的文件发给它的云端模型）写在每个交接包的 README 里，由你决定。
- **可选主控（路线 A）**：接入片段与起手提示词只是**打印出来的文字**（只含路径与固定字样，有测试保证不含疑似密钥），Runtime 不读、不写、不启动任何主控应用或它的配置；切换是你的动作（`par coordinator use`），写接管包并记一条事件，之后被替换的主控经桥提交或启动会被拒绝，查看与取消不拦；桥的“最近出现”只是限速心跳事件（每主控每分钟至多一条，不属于任何任务）。
- **主控交接**：接管包只含 Kernel 量到的事实（任务树、状态、检查结果、待决批准、Harness 文件的路径与哈希），没有对话、没有 Worker 的话；只有最新的包能被确认；确认记一条事件。
- **脱敏保持线性时间**（P6 发现并修复）：旧实现的几个正则在“一个 20 万字符的字母串”“几千个私钥开头标记”这类输入上是二次方时间，一行异常的 Worker 输出就能卡住 Kernel 线程。现在所有模式都有界或手工单遍扫描；`tests/unit/test_redact_perf.py` 用 30 万字符的最坏输入逐项验证，并有 12 秒的警报。

## 任务内核（P2）

- **隔离**：Worker 只拿到 `attempts/N/workspace/`——项目在基线提交处的私有克隆（去掉 origin、关掉 hooks）；R0 任务拿到空文件夹。已提交的敏感文件（`.env`、密钥、`runtime/accounts/…`）**不物化**进副本。
- **Kernel 从不在 Worker 的副本里运行 git**：Worker 能改写 `.git/config`（fsmonitor、diff 驱动），之后任何 git 命令都会执行它的代码。Worker 的结果由「把 `workspace/` 与 Kernel 自留的干净 `ref/` 当普通文件比较」得到（不跟随符号链接），补丁由 Kernel 自己的 git 在另一份副本里生成，并被应用回干净基线逐文件比对（有测试：投毒的 `.git/config` 没有被执行）。
- **边界判定**：只有 `allowed_paths` 内的新增/修改/删除可以进入补丁。以下一律是越界并让 attempt 失败：`.git`（含嵌套仓库）、`.ai/`、任何位置的 `AGENTS.md` `CLAUDE.md`、敏感路径、符号链接与特殊文件、超大文件、含控制字符的文件名、只读任务的任何改动。`__pycache__`、`.DS_Store` 与被项目 `.gitignore` 忽略的新文件只计数不算错（受保护路径除外）。
- **环境**：Worker 与检查都从干净环境启动——不继承你 shell 里的任何变量（含 API key），HOME 指向 attempt 内的私有目录。
- **机密**：合同里出现疑似凭据整份拒绝且不入账；事件、原因、日志、结果、终端输出都先脱敏；Worker 的原话进入结果前脱敏并截断到 1200 字，显示给终端前转义控制字符（防转义序列劫持终端）。
- **批准走 Kernel 自己的闸门，不会被绕过**：写权限、协调者指定 Worker、漂移确认、换 Worker、被拒权限后重试都进入 `PERMISSION_WAIT` 逐项等待批准；`run` 开始前会再核对一遍，取消后再重试不能绕过。`par task approve/accept/reject/close` 原本是人用的命令；2026-10-20 起 MCP 桥也暴露了对应的四个工具（`approve_task` `reject_task` `accept_task` `close_task`），固定传 `by="coordinator"`——决定权放开了，但走的仍是同一套闸门、同一本台账，决定者始终可查。
- **接受不是应用**：`ACCEPTED` 只是记录你接受了结果；补丁保存在 `work.patch`，P2 没有任何代码把它应用到项目。
- **台账**：`0600`；数据库触发器保证事件只增、已结束的 attempt 不可变、状态只沿转换表走、版本逐次加一。只存任务事实，不存项目知识；有测试检查数据目录里除 Worker 副本外没有任何对他人可读的文件。
- **进程安全**：不会终止 Kernel 无法确认身份的进程；恢复时只在领头进程的起始时间与记录一致时才停止进程组；取消只对 Kernel 自己启动的进程组生效，不会碰 Claude Desktop、Codex 或 Antigravity 的 GUI。

## 脱敏

`core/redact.py` 遮蔽厂商 API key、JWT、PEM 私钥、`Bearer` 令牌、`Authorization` 与 `Cookie` 行、敏感键名的赋值（`api_key=…`、`"password": "…"`）、邮箱和不透明的混合大小写长串；git SHA、UUID、`max_tokens` 这类不会被误伤。脱敏是幂等的，输出里的家目录显示为 `~`。

## 配置

校验器（`config.py`）拒绝：密钥形状的键名或值、`gemini_legacy` 启用或充当 Worker、`claude_host` 充当 Worker、`additional_paid_credits` 不为 `false`、`max_parallel_workers` 不为 1、相对路径的可执行文件。

## Provider

所有外部 Provider 默认 disabled，适配器注册表默认为空。P3/P4 起只有一种情况会注册适配器：配置里启用了 `codex` 或 `antigravity` **并且**写明了绝对路径的可执行文件（`gemini_legacy` 永远不会，有测试连「绕过校验手工构造的配置」也覆盖）；其余 Provider 即使写成 `enabled = true` 也只会停在 `BLOCKED_NO_WORKER`。测试替身 `ScriptedProvider` 不能通过配置文件注册，生产 Profile 不可能把任务路由给它。有测试检查：源码不导入任何网络库、整条任务流在拦截了 `socket` 的情况下照常完成、只有预期的几个模块会启动进程、测试套件从不运行真实的 Codex 或 `agy`（只有显式设置 `PAR_REAL_CODEX=1` / `PAR_REAL_AGY=1` 的离线探测，和需要批准才会运行的 `tests/tools/smoke_codex.py`、`smoke_agy.py`）。

## 已知限制（诚实声明）

- 路径策略约束的是 **Runtime 自己的写入**。它不是操作系统沙箱，不能限制第三方 CLI 以同一用户身份做什么。Codex 的写入隔离来自它自己的沙箱（已离线实测：只能写工作副本、不能联网），但**读取不受限**（见上）；Antigravity 的隔离由「私有 HOME + Runtime 写的权限预设 + Kernel 的文件比对」组成，**模型运行时的真实表现要等获批的真实烟测才能声明**（见上一节与 AGY.md §12）。在读取范围收紧并经真实调用验证之前，不对真实项目开放写入。
- 脱敏是尽力而为的纵深防御，不保证识别所有形态的机密。首要原则仍然是不去读机密。
- P2 的写入只发生在数据目录内，路径各段都由 Kernel 自己生成（任务号、attempt 序号），用 `O_NOFOLLOW` 打开并保持 0600/0700；Worker 起的文件名只在做过包含检查之后才会被写入验证副本。这取代了 P0 时「引入多文件写入时再收紧」的待办。
- **检查会运行补丁里的代码**（测试的本质），P2 没有操作系统级沙箱：只靠一次性副本、无机密的干净环境、固定的可执行文件与参数规则、独立进程组与超时。因为真实项目不可能被提交写任务（可写的只有夹具项目），目前风险限于夹具；进入 P8 之前必须评估加固（如 `sandbox-exec`）。
- Worker 若自行 `setsid` 脱离进程组，Kernel 无法追踪也不会去杀它（读管道的线程不会因此卡死）。
- 恢复判断依赖心跳与时钟；机器睡眠恰好跨过阈值可能让另一个 Kernel 把活着的 attempt 误标为 `RECOVERY_REQUIRED`（不会丢数据，也不会启动第二个写入者）。
- `par harness check` 的 git 事实依赖 `git` 可执行文件；找不到时相关字段缺失而不是报错。
- 未重新验证各工具的原生记忆开关、WorkBuddy 身份文件是否被注入，这些需要读工具配置或模型会话，不属于 P0。
