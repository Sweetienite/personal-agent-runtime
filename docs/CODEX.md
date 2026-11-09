# Codex 适配器（P3）：版本兼容记录与决策依据

> 本文是「这个适配器对哪个版本的 Codex 做过什么验证」的记录。每一条都标明证据来源：**离线实测**（本机运行了不调用模型的子命令）、**源码**（开源仓库 `openai/codex` 的对应文件）、**未验证**（需要一次真实调用才能确认）。升级 Codex 之后先读本文的「升级复核清单」。

## 1. 被验证的安装

| 项 | 值 | 证据 |
|---|---|---|
| 可执行文件 | `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex` | 离线实测 |
| 版本 | `codex-cli 0.162.0-alpha.17.2`（`aarch64-apple-darwin`） | `--version`、`codex-package.json` |
| 签名 | `Developer ID Application: OpenAI OpCo, LLC (2DC432GLL2)`，`codesign --verify --strict` 通过 | 离线实测 |
| 登录 | `Logged in using ChatGPT`（订阅登录，不是 API key） | `codex login status` |
| 同目录的 `bin/codex` | 是一个**未签名的 `/bin/sh` 包装脚本**，只做 `exec` 上面那个二进制 | 离线实测 |
| PATH 上的 `/opt/homebrew/bin/codex` | 审计时已确认启动即 ENOENT；**适配器从不通过 PATH 找 Codex** | P0 审计 |

适配器要求配置里的 `executable` 是绝对路径，指向**已签名的二进制本身**。指向包装脚本会被拒绝（脚本没有自己的签名，无从证明它没被改过）。每次 attempt 之前都会核对：文件存在、可执行、不被组/其他用户写、属主是你或 root、签名有效且团队 ID 是 `2DC432GLL2`（结果缓存 10 分钟，按文件 inode/大小/mtime 失效）。

## 2. 命令行（`build_argv`）

```
codex exec --json --color never --ephemeral --skip-git-repo-check
           -s read-only|workspace-write  -m <模型>  -C <工作副本>
           --disable <功能> …  -c mcp_servers.<名>.enabled=false …  -c notify=[]
           -
```

| 参数 | 为什么 | 证据 |
|---|---|---|
| `exec --json` | 无头模式，stdout 只有 JSONL 事件，其余都在 stderr | 源码；`exec --help` |
| `--color never` `--ephemeral` | 不带颜色码；不把会话文件落盘 | `exec --help` |
| `--skip-git-repo-check` | R0 的工作目录是空文件夹；其余任务的 Worker 副本也**不含 `.git`**（见 §9），所以都不是仓库 | `exec --help` |
| `-s read-only` / `-s workspace-write` | **沙箱由任务的风险等级决定**，不由模型或配置决定；R0/R1 只读，R2 才可写工作副本 | 离线实测（`codex sandbox`，见 §5） |
| `-m <模型>` | 固定模型，不做任何自动选择；模型名只允许 `[A-Za-z0-9][A-Za-z0-9._:/+-]{0,99}`，不可能夹带选项 | 单元测试 |
| `-C <目录>` | 工作副本（Kernel 建的私有克隆）；路径可含空格 | 单元测试 |
| `-`（提示词从 stdin 读） | 提示词不出现在进程列表里，也不受 argv 长度限制；上限 400 000 字节 | `exec --help`；单元测试 |

**从不传**：`--ignore-user-config`（会丢掉 `memories=false` 与沙箱基线）、`--ignore-rules`、`-p/--profile`、`--add-dir`、`--enable`、`--dangerously-*`、`danger-full-access`、`--full-auto`、`--oss`、`--worktree`、`--approve-for-me`、`--cyber-access-program`、任何 `-a/--ask-for-approval`（`exec` 会强制 approval_policy 为 never，并自动拒绝一切审批请求——源码）。有测试逐个检查。

适配器在 `tests/integration/test_real_codex.py`（`PAR_REAL_CODEX=1`）里把自己构造的整条命令行（把末尾的 `-` 换成 `--help`）交给真实 Codex 解析：每个参数都被接受。

## 3. 隔离配方：哪些东西在 Worker 运行时被关掉

你的 Codex 配置启用了很多对话里有用、但对「Kernel 指派的 Worker」不该有的东西：ChatGPT 应用/连接器、十多个插件（浏览器、桌面控制、Canva、Figma……）、一个 `node_repl` MCP 服务器、一个 `notify` 程序。它们有的在沙箱**外**运行。适配器因此对**每一次** Worker 运行都关掉：

- **功能开关**（`--disable`）：`memories apps plugins plugin_sharing remote_plugin skill_mcp_dependency_install hooks computer_use browser_use browser_use_external browser_use_full_cdp_access in_app_browser multi_agent multi_agent_v2 image_generation shell_snapshot goals fast_mode ultrafast_mode daemon_auto_start unbounded_connection_retries tool_suggest`（本版本全部存在，共 22 个）。**当前版本不认识的名字不会传**——`--disable <未知名>` 会直接报错 `Unknown feature flag`（离线实测），所以名单是按已安装版本动态筛的；`par health --probe` 会列出被丢掉的名字。
- **配置里定义的 MCP 服务器**（`-c mcp_servers.<名>.enabled=false`）：按 `mcp list --json` 实际发现的名字逐个关；插件自带的服务器随 `--disable plugins` 一起消失。
- **`notify`**：`-c notify=[]`，不再启动你配置的通知程序（在沙箱外、每个 turn 结束时运行）。
- `memories`：你的配置里本来就是 `false`；这里**重申**，防止以后配置漂移。

**证明方式（完整发现缓存 5 分钟，但每次 attempt 前都会再用一次 `mcp list` 复查）**：先用 `features list` 得到已知功能名，再用 `mcp list --json` 得到仍启用的服务器名，把它们全部关掉后**再列一遍**：不得有任何 MCP 服务器仍启用，`memories` 必须读作 `false`。每次 attempt 前用本次的参数再问一次「还有哪些 MCP 服务器会启动」，有新增（比如你刚在配置里加了一个）就丢弃缓存重新发现。任何一步失败或对不上 → 这次 attempt 在**启动前**被拒绝（`preflight`，不会产生任何模型调用）。

离线对照（`codex debug prompt-input`，渲染「模型能看到的提示词」）：在空目录里量，不加配方 15 130 字节、含 5 处 `plugin`，加配方 13 614 字节、0 处 `plugin`；在夹具目录里量是 16 477 → 14 961 字节（环境上下文更长）。证据文件第 10 节有可复现的脚本。

**这个配方没有、也无法离线证明的**：
1. 提示词里仍有一段「你可以用 `spawn_agent` 创建子代理」的说明——它来自模型目录的基础指令，不随 `multi_agent` 开关变化（离线实测）。工具本身是否可用只有真实运行才能看到；万一模型调用了，事件里会出现 `collab_tool_call`，适配器会在结果里写一条警告。
2. 系统自带技能（如 `imagegen`）的说明仍在；技能只是文字说明，不是可执行入口，但模型可能照着它去读 `~/.codex/skills/…` 下的脚本。
3. Codex 自带的 `web_search` 工具模式（`disabled/cached/indexed/live`）取决于你的配置；适配器没有覆盖它（覆盖项的实际效果未验证）。事件里出现 `web_search` 会被标成警告。

## 4. 环境与凭据

- Kernel 给子进程的环境永远是干净的：固定 PATH、attempt 内的私有 `HOME` 与 `TMPDIR`、语言设置。适配器再**只**加：`CODEX_HOME`（你的真实登录位置：`$CODEX_HOME` 或 `~/.codex`）以及代理/证书变量（`HTTPS_PROXY` `HTTP_PROXY` `ALL_PROXY` `NO_PROXY` 及小写形式、`SSL_CERT_FILE` `SSL_CERT_DIR` `CODEX_CA_CERTIFICATE`）。
- **任何 API key 环境变量都不会传给 Codex**（`OPENAI_API_KEY`、`CODEX_API_KEY`……即使它们在你的 shell 里）。这样环境变量不可能把一次运行悄悄切换到按量计费。
- 每次 attempt 前核对 `codex login status`（缓存 60 秒）：只有 `Logged in using ChatGPT` 才放行；API key 登录、未登录、无法识别的输出都**拒绝启动**并说明原因。适配器从不读、不复制 `auth.json`。
- 子进程的 `HOME` 是私有目录，但 Codex 用的是真实的 `CODEX_HOME`，所以你的 `config.toml`（模型、记忆设置、规则）仍然生效。

## 5. 沙箱实测（`codex sandbox`，离线，不调用模型）

在临时目录里用 `codex sandbox -c 'sandbox_mode="…"' -- <命令>` 实际跑了一遍（只读取文件大小，没有读内容）：

| 模式 | 写工作目录 | 写工作目录之外（含 `~/.codex`） | 联网 | 读 `~/.codex/auth.json`、别的项目文件 |
|---|---|---|---|---|
| `read-only` | 拒绝 | — | — | **允许（只读沙箱不限制读）** |
| `workspace-write` | 允许 | 拒绝 | 拒绝（域名都解析不了） | 允许 |

结论：**Worker 能读到你整块磁盘上的任何可读文件，包括 `~/.codex/auth.json` 和其他项目**；它不能写工作副本之外的任何位置，也不能联网（沙箱内）。模型的输出会发往 OpenAI（你的 Codex 会话本来就是），也会进入我们的日志——日志与结果在写盘前脱敏，但脱敏只是尽力而为。

**可行的收紧办法（已离线验证，尚未纳入适配器）**：Codex 支持「权限配置」把可读范围缩到最小：

```
-c default_permissions="par_worker"
-c permissions.par_worker.filesystem={":minimal"="read", ":project_roots"="write"}
```

离线实测：`auth.json`、其他项目的文件、工作目录之外的临时文件都读不到（`Operation not permitted`），工作目录可读可写；但 `/usr/bin/python3`、`/usr/bin/git`（Xcode 命令行工具的垫片）也跑不起来，需要补充 `/Library/Developer/CommandLineTools`、`/opt/homebrew` 等只读白名单。它与 `codex exec -s …` 的相互作用**必须用一次真实调用确认**，所以 P3 不默认启用。**进入 P8（真实项目试运行）之前必须采用并验证**。

## 6. 事件格式（`parse_event`）

来源是开源的 `codex-rs/exec/src/exec_events.rs`，**目前还没有来自真实运行的样本**（第一次获批的真实调用之后，会把脱敏样本加进测试并据此核对解析器）。

| `type` | 适配器的事件 | 说明 |
|---|---|---|
| `thread.started` | `session` | 只留 thread id |
| `turn.started` `reasoning` 等 | `other` | 只记类型与长度，不留推理文字 |
| `item.*` / `agent_message` | `message` | 最后一条成为结果里「Worker 自述」（未验证，标注）；脱敏、截断到 4000 字 |
| `item.*` / `command_execution` `file_change` | `tool` | 记命令（截断 200 字）或路径；`declined` 变成 `warning` |
| `item.*` / `error` | `warning` | 非致命，如元数据回退 |
| `item.*` / `mcp_tool_call` `collab_tool_call` `web_search` | `tool` + 说明 | 本不该出现（已关闭）；出现就写进结果的备注 |
| `turn.completed` | `terminal`（SUCCESS）+ `usage` | `usage` 是厂商自报的 token 数，原样记录，不估算、不求和 |
| `turn.failed` | `terminal`（ERROR）+ 消息 | 消息里的重置时间会被解析 |
| `error` | `error` | 致命或重试通知 |
| 其他/坏行 | `other` / `unparsed` | 坏行只记长度，**不保留原文** |

`stdout` 与 `stderr` 都由 Kernel 脱敏、限长后写入 `attempts/N/` 下 0600 的日志。

## 7. 失败分类（`classify_failure`）

**退出码 0 绝不等于成功**：只有收到 Codex 自己的 `turn.completed` 且进程退出码为 0，才是 Provider 层面的「干净」（Kernel 随后还要自己核对改动、边界和检查）。

| 类别 | 依据（版本相关的官方消息族，大小写与弯引号不敏感） | Kernel 的处理 |
|---|---|---|
| `QUOTA_EXHAUSTED` | `You've hit your usage limit…`、`You hit your spend cap…`、`Quota exceeded…`、`out of credits` | 整个 Provider 暂停；**消息里有重置时间（「try again at …」）就记下来**作为到期时间，否则一直保持到你清除 |
| `AUTH_REQUIRED` | `401`、`Unauthorized`、`refresh token`、`sign in again`、`Not logged in`、`invalid_api_key` | 整个 Provider 暂停，请你用官方客户端重新登录 |
| `MODEL_UNAVAILABLE` | 含 `model` 且含 `does not exist / not supported / not found / do not have access …` | 只标记该模型 |
| `RATE_LIMITED` | `Rate limit exceeded`、`exceeded retry limit, last status: 429`、`Selected model is at capacity` | 只冷却该模型；**裸 429 永远是限流，不是额度用尽** |
| `TRANSIENT_ERROR` | `stream disconnected before completion`、`Connection failed`、`experiencing high demand`、5xx | 中断，等你处理（`safe_auto` 下 R0/R1 有一次有界自动重试） |
| `EXECUTION_FAILED` | 其余一切：上下文窗口、内容过滤、**无法识别的消息** | 失败，保留现场；**不会把不认识的消息猜成额度或登录问题** |
| `UNKNOWN` | 没有终态且退出码 0（流直接结束）；被信号杀死且无终态；超时 | 中断，保留现场，等人确认 |

优先级：额度 > 登录 > 模型 > 限流 > 瞬时 > 其他。运行中途的重连通知（`error` 事件）之后仍收到 `turn.completed` 的，算成功。

**被拒绝的命令（`declined`）不是失败**：`exec` 无法给出审批，模型常会改用别的办法继续；重试也改变不了结果。它被记成结果里的警告（`worker run: a command was declined …`），由你和 Kernel 自己的核对来判断结果是否可用。

**重置时间解析**：`try again at 7:30 PM`（今天；若该时间已过则是明天）、`try again at Oct 15th, 2026 3:05 AM`；按本机时区理解，只在是真实的、未来 35 天内的时间戳时才采用（`core/health.py`），否则保持「直到清除」。读不懂就是读不懂，不猜。

## 8. 取消

Codex 只会优雅处理 Ctrl-C（SIGINT）：中断当前 turn 并退出（源码）。`request_cancel` 因此发 SIGINT；Kernel 在宽限期后按既有流程升级为对进程组 SIGTERM、再 SIGKILL。离线测试覆盖「听话的」与「无视 SIGINT 的」两种进程。

## 9. CLI 自己会留下什么（含一个真实烟测发现、已规避的问题）

`--ephemeral` 让这次运行**不写会话文件**。但 CLI 仍会在 `CODEX_HOME`（`~/.codex`）下写它自己的东西。真实烟测前后对比 `~/.codex` 里文件的名字、大小与修改时间（**只比名字和数字，从不读内容**），结果：

| 运行 | `~/.codex` 里变化的文件 |
|---|---|
| 只读（R1，工作副本是 git 克隆） | `logs_2.sqlite`、`logs_2.sqlite-wal`（CLI 自己的诊断日志）、`models_cache.json`（模型目录缓存）。没有会话、没有记忆文件、`config.toml` 不变 |
| 可写（R2，工作副本是 git 克隆）——**第一次真实运行** | 同上，**外加 `config.toml` 被 CLI 改写**：追加了 `[projects."<这次的工作副本路径>"]` 与 `trust_level = "trusted"` 两行 |

**这是一个必须规避的问题**：每次可写运行都会往你的全局 Codex 配置里追加一条「受信任项目」，无限增长，而且那是你的配置文件（桌面 App 也在读写它，并发写还有丢失更新的风险）。离线对照实验（临时 `CODEX_HOME`，只放一份 `config.toml` 的**拷贝**、没有登录，所以 CLI 的请求只会因缺少凭据得到 401、不可能调用模型；发出的只有 CLI 的固定指令和一个无意义的 "hello"）测得：

| 条件 | `config.toml` |
|---|---|
| 工作副本是 git 仓库 + `-s workspace-write` | **被追加** |
| 工作副本是 git 仓库 + `-s read-only` | 不变 |
| 工作副本**没有 `.git`** + `-s workspace-write` | 不变 |
| git 仓库 + `workspace-write` + `-c projects."<副本>".trust_level="trusted"`（或 `"untrusted"`） | **仍被追加**（覆盖项挡不住） |

写入发生在进程启动后约 0.2 秒（早于任何模型交互）。**规避办法**：适配器声明 `workspace_with_git = False`，Kernel 给 Codex 的 Worker 副本去掉顶层 `.git`（`execution/workspace.py::prepare(with_git=False)`）。这对 Kernel 没有任何影响——它从不使用 Worker 的仓库，而是把 `workspace/` 与自己留的 `ref/` 当普通文件比较（两边都跳过顶层 `.git`），补丁由 Kernel 自己的 git 在另一份副本里生成——还顺带去掉了「Worker 改写 `.git/config`」这条攻击面。代价是：模型在副本里不能用 `git status` / `git diff`。有测试（Worker 副本里没有 `.git`、判定与补丁完全不变、Worker 自己造 `.git` 也不被采信）加 3 项变异检验（M98–M100）；还有一个需要显式 `PAR_REAL_CODEX_TRUST=1` 的回归测试：在临时 `CODEX_HOME` 里、没有登录的情况下运行真实 `exec`，检查「可写运行 + 没有 `.git`」不会改 `config.toml`（并记录有 `.git` 时的真实行为，Codex 升级后行为变了会看到）。

**已处理**：第一次 R2 烟测（修复之前）让 Codex 在你的 `~/.codex/config.toml` 里留下了一条指向一次性临时目录的信任条目（三行：节标题、`trust_level`、一个空行）。Runtime 自己从不写那个文件；这一次是按你的明确批准（B1 = A）一次性手工处理的：先把原文件原样备份为 `~/.codex/config.toml.par-backup-20261014`（0600），再原子替换，只去掉这三行（核对过其余每一行都没变，`memories` 等设置原样）。

**修复的真实复验（你批准的第三次真实调用）**：Worker 副本不含 `.git` 之后，一次真实的、带登录的 `workspace-write` 运行**没有改动** `config.toml`（`config.toml` 的创建时间是我那次编辑，比任务提交早 8 秒；运行期间没有被碰）。这把「离线证据」补成了真实证据。

CLI 写诊断日志与续期令牌是它自己的行为；`par` 不碰这些，也不声称「零持久化」。

## 10. 你的全局 Codex 配置会继续影响 Worker

因为适配器不替换你的配置，这些设置仍然生效：`model_reasoning_effort = "high"`、`model_verbosity = "high"`、`personality = "friendly"`、`service_tier = "default"`、`sandbox_workspace_write.network_access = false`、`rules/` 下的执行策略。其中 **reasoning effort = high 会让每次调用消耗更多额度**。V1 没有「按 Worker 指定推理强度」的配置项；如果需要，应作为一项新功能提出（会改配置模式、路由快照和测试）。

`~/Documents` 被你的配置标为受信任项目，但 Worker 的工作副本在 `~/Library/Application Support/PersonalAgentRuntime/…` 下，**不在受信任路径内**，因此副本里的项目级 `.codex/` 配置、hooks、规则预计不会被加载（来自 Codex 的信任模型，**未做真实调用验证**）。

## 11. 真实烟测记录

**2026-10-14 19:30**，真实调用（额度恢复后进行）。模型 `gpt-6-luna`；全局配置里 `model_reasoning_effort = "high"` 继续生效；一次性数据目录与夹具项目；提示词约 2 KB（R1 1 930 字节），全部是夹具内容（无个人数据）。完整输出留在作者本机的开发副本里，没有包含进本仓库。

| | 只读问题（R1） | 一行修复（R2，写入） |
|---|---|---|
| 真实调用 | 1 次 | 1 次 |
| 用时 | 13.7 秒 | 25.2 秒 |
| 事件 | 7 行：`thread.started` `turn.started` 2 条 `agent_message` 1 组 `command_execution` `turn.completed` | 15 行：另有 4 组 `command_execution`（`sed`/`rg`/`cat`/`python3 -m unittest -q`）与 1 组 `file_change` |
| 厂商自报 usage | 输入 25 552（其中缓存 12 032）、输出 191、推理 0 | 输入 83 035（缓存 65 280）、输出 869、推理 83 |
| Kernel 结论 | `REVIEW_REQUIRED`；0 越界；回答正确（`add(2, 3)` 返回 −1，与 spec 的 5 不符，且只读未改文件） | `REVIEW_REQUIRED`；0 越界；补丁 1 个文件 +1 −1；**Kernel 自己跑的检查 `unit` PASS**；模型自己也跑了一遍测试并如实报告 |
| 夹具 | 未变 | 未变（补丁只保存，不应用） |
| 真实数据目录 | 未变 | 未变 |
| `~/.codex/config.toml` | 不变 | **被 CLI 追加了一条信任条目——这就是 §9 的发现，已规避** |
| stderr | 空 | 空 |

观察与结论：
- 解析器与分类器对两份真实输出：每一行都认得、没有未知事件类型、分类为「干净成功」、usage 与摘要都取到（`tests/data/codex_real/` 里的脱敏样本已进入测试）。
- 两次运行中模型**没有**尝试 MCP、子代理（`collab_tool_call`）、联网搜索或浏览器——本文 §3 里「无法离线证明」的几条因此只是「未观察到」，仍不是「已证明不可用」。
- 事件流**不含模型名**（`model_observed` 恒为空）：实际用了哪个模型只能信 `-m` 参数与 CLI 自己的日志。
- 模型的命令是 `/bin/zsh -lc '…'`（登录 shell，HOME 是私有空目录，所以没有任何个人 dotfile 被加载）；`rg` 可用；`python3 -m unittest -q` 在它的沙箱里能跑（离线实测里 `python3` 解析到 Homebrew 的 3.14）。
- 每次调用的输入约 2.5 万（只读）到 8.3 万（含多轮命令）token，其中大部分是 CLI 自己的系统指令与重复上下文（缓存命中约 47%–79%）。这是「一次最小任务」的量级，用来估算额度消耗。
- **复验调用（第三次真实调用，同日晚）**：同样的一行修复任务，`config.toml` 未被改动，Kernel 自己的检查 PASS；用时 13 秒，usage 输入 40 422（缓存 26 112）、输出 353、推理 46。`~/.codex` 里被列为「修改」的另外 4 个文件（两个 10/12、10/13 的旧会话和两个 sqlite WAL）属于你正在使用的桌面 App：它们的修改时间在这次运行结束之后仍在变化，且本次运行的会话 ID 与它们不同；没有新增会话文件（`--ephemeral`）。原始输出见证据文件的补充部分。
- 额度用尽时的**真实措辞**这次没有取得（额度已恢复）：额度用尽的分类依据仍是开源源码里的官方措辞与合成样本，等下次真的遇到时用 `tests/tools/capture_codex_sample.py` 补进来。

## 12. 升级复核清单

1. `PAR_REAL_CODEX=1 python -B -m unittest tests.integration.test_real_codex -v`（离线；检查签名、探测、每个参数是否仍被接受、隔离配方是否仍然生效），以及 `PAR_REAL_CODEX_TRUST=1 … RealCodexTrustEntries`（没有登录的临时 `CODEX_HOME` 里跑 `exec`，确认可写运行不会改 `config.toml`）。
2. `par health --probe`：看版本是否仍是已验证的 `0.166.0-alpha.6`；`features_dropped` 里是否出现了新的未知功能名；`isolation.problems` 是否为空。
3. 在你批准后跑一次 `tests/tools/smoke_codex.py`，对照真实事件样本与本文 §6、§7。
4. 版本升级后若 `par health --probe` 提示「untested version」，在完成 1–3 之前不要把 Codex 当作可靠的 Worker。
5. **先确认宿主能创建嵌套 OS 沙箱**：`python -B tests/tools/sandbox_probe.py --json`。macOS 上
   `nested_sandbox_available: false` 时，PAR **默认** `-s` 模式的任何真实 R2 都会因为
   `sandbox_apply: Operation not permitted` 失败，这与适配器无关；换一个未被沙箱化的终端 / runner
   再跑 1–3，或直接用 `python -B tests/tools/codex_revalidate.py --check-only` 一键复核。
   **绝不要用提权参数把一次写测试「做绿」**：`codex_revalidate.py` 在这类宿主上会 exit 2、
   0 次模型调用，而不是绕过。§14 记录了唯一一次例外（宿主已由外部沙箱化，正是
   `codex exec --help` 写出的适用情形），它**只用于诊断、只改临时副本、不进主干配置**，
   当时结论标为 `GREEN (scoped)` 而非通用 GREEN。**2026-11-07 的普通终端默认沙箱复核成功后，等级已升为 `GREEN`，见 §15。** 三次实测见 §13–§15。

## 13. 2026-11-06 第一次真实复核（`0.166.0-alpha.6`，未完成）

用户授权解锁 T14 后，选定「用官方允许的 ChatGPT 订阅 + `codex exec` 路径做真实复核，不新增 Provider」。
完整证据：[reports/CODEX_REAL_REVALIDATION_20261106.md](reports/CODEX_REAL_REVALIDATION_20261106.md)。

| 项 | 结果 |
|---|---|
| 签名 / 登录 / 隔离配方 | PASS：`TeamIdentifier=2DC432GLL2`、`Logged in using ChatGPT`、22 个 feature flag 关闭、`node_repl` / `par` 两个 MCP server 关闭、`memories=false` 复核通过 |
| `RealCodexOffline`（8 项） | 7 PASS / **1 FAIL**：版本漂移闸门（`0.166.0-alpha.6` ≠ `0.162.0-alpha.17.2`）。这是刻意的安全闸门，不是 flaky 测试 |
| `RealCodexTrustEntries`（3 项） | 3 PASS；记录到「可写运行 + 有 `.git` 的工作副本仍会改写 `config.toml` = True」，所以 Worker 副本继续去掉顶层 `.git` |
| 真实 R1 / R2 | 各 1 次模型调用。R1 干净终态但 Worker 自述读不到文件；R2 厂商 `SUCCESS`、Kernel `unit` 检查 FAIL → `FAILED`、0 文件变更、无补丁 |
| 根因 | 宿主已在 macOS 沙箱内，`/usr/bin/sandbox-exec` 独立复现 `sandbox_apply: Operation not permitted`（exit 71），CLI 无法创建嵌套 Seatbelt 沙箱 |
| 副作用 | 夹具工作树 clean、真实数据目录未变、`config.toml` 未变；`~/.codex` 仅 3 个 CLI 自有 sqlite / 缓存文件被改写 |

**这次尝试的版本复核未完成**：没有成功的真实 R2，`TESTED_VERSION` 当时保持 `0.162.0-alpha.17.2`，
`par health --probe` 继续提示 `untested version`。取消 / 超时 / 额度 / 限流的真实样本也仍缺，
当时只有 fake CLI 可控样本。**同日用户追加授权后的第二次尝试已完成 §12 复核并取得成功的真实 R2，
见 §14**；`TESTED_VERSION` 现已更新为 `0.166.0-alpha.6`。本节保留为过程记录。

### 本轮发现的两个版本差异（已处理，但不要误读为「放宽」）

- **静态说明文字**：`0.166.0-alpha.6` 的基础指令里仍会出现「plugin」一词，但那不是工具定义。
  原来的离线断言 `isolated.count("plugin") == 0` 因此是**假失败**。测试已改成结构化核对：
  plain 中确有 `mcp__cua_repl` / `js` / `js_reset` 工具定义，isolated 中这些定义全部消失、
  没有任何 `mcp__` 前缀工具、无 `<recommended_plugins>` 段、无原生应用/浏览器控制工具描述。
  这比数单词更严格。
- **残留的子代理工具名**：即使 `--disable multi_agent`，基础工具列表仍含
  `spawn_agent` / `list_agents`（测试会打印隔离后实际可见的工具名）。
  **本文不宣称「子代理已验证关闭」**；这是后续复核项。

### 一处表述修正（来自真实 R1）

只读任务没有任何机械检查时，状态行原写 `every check passed`。现在零检查时改为明确写出
「没有 Kernel 检查运行，接受前请审阅证据」，有检查时写出实际通过了几项；
`tests/unit/test_kernel_run.py` 加了两条回归。原因是真实 R1 里 Worker 已自述读不到文件，
却仍收到一句「每个检查都通过」——干净终态不等于完成了任务。

## 14. 2026-11-06 第二次真实复核：§12 完成，成功的真实 R1 / R2

用户追加授权「给你足够的权限，你可以执行所需的操作」后，同日做了第二次尝试。
完整证据：[reports/CODEX_REVALIDATION_PART2_20261106.md](reports/CODEX_REVALIDATION_PART2_20261106.md)。

### 先把 §13 的根因钉死

本会话的**所有**执行上下文都继承 macOS App Sandbox：`sandbox_check(getpid(), NULL, 0) = 1`，
`sandbox-exec` 叠加第二层 Seatbelt 必然 `sandbox_apply: Operation not permitted`（exit 71）。
本轮逐一排除了 9 条脱离路径（工具层隔离开关、`launchctl submit` / `bootstrap`、`cron` / `at`、
`ssh localhost`、`open -a Terminal <file.command>`、`osascript` → Terminal（`-10004` 权限违例）、
`shortcuts` CLI、独立 subagent 上下文），**全部失败**。这是宿主固有属性，不是 PAR 或 CLI 的缺陷。

### 为什么这里 `-s danger-full-access` 不是「用提权捷径让写测试通过」

`codex exec --help` 原文写着该模式「**Intended solely for running in environments that are
externally sandboxed**」。本宿主正满足这个前提，且：

- 模型进程**仍在宿主 OS 沙箱内**，不是无沙箱裸奔；失败原因是「无法叠加第二层」，不是「权限不足」。
- PAR 的隔离**全部保持**：私有一次性副本（无顶层 `.git`）、干净环境、私有 HOME/TMPDIR、
  绝对路径与签名校验、22 个 feature flag + MCP 关闭、进程组/超时、Kernel 边界比对与检查、补丁不自动应用。
- 改动**只在一次性临时 worktree 副本**的 `build_argv`，跑完即删；**主干 `codex.py` 从未改过沙箱模式**，
  也没有新增任何配置开关或把该参数写进产品代码/默认配置。
- 用的是 `-s danger-full-access`（只放开沙箱模式），**没有**用
  `--dangerously-bypass-approvals-and-sandbox`（后者还会跳过审批）。

即便如此，这仍是**非默认配置**，所以结论是 `GREEN (scoped)`，不是通用 GREEN。

### 结果（§12 逐项）

| §12 | 结果 |
|---|---|
| 1a `PAR_REAL_CODEX=1 … RealCodexOffline -v` | **8/8 PASS**（`TESTED_VERSION` 更新后版本闸门转绿） |
| 1b `PAR_REAL_CODEX_TRUST=1 … RealCodexTrustEntries -v` | **3/3 PASS**；「可写 + 有 `.git`」仍改写 `config.toml`，所以 Worker 副本继续去掉顶层 `.git` |
| 2 `par health --probe` | `AVAILABLE`：`signed by OpenAI, logged in with ChatGPT, extras switched off`；`features_dropped` / `isolation.problems` 为空；`tested_version: True` |
| 3 经批准的真实 smoke | R1 真正读到 `app/calc.py` 并答出 `add(2,3) = -1`；R2 `MEASURED · checks PASSED (1/1)`、补丁 1 文件 +1 −1、边界 0 违规、未应用到项目 |
| 4 `untested version` | 消除（`TESTED_VERSION = 0.166.0-alpha.6`） |
| 可控分类样本 | **153/153 PASS**（额度、限流、裸 429、认证、模型不可用、软硬权限拒绝、取消、超时、截断、崩溃、中途写入后额度耗尽） |

R2 事件流：`thread.started`、`turn.started`、`item.started|completed / command_execution ×2`、
`item.started|completed / file_change ×1`、`item.completed / agent_message ×3`、`turn.completed`。
副作用：夹具工作树 clean、真实数据目录未变、`~/.codex/config.toml` 未变；`~/.codex` 仅 3 个 CLI 自有
sqlite / 缓存文件被改写（只记名称与大小，不读内容）。

### 限定条件（必须一起读）

1. **默认 `-s read-only|workspace-write` 在本宿主无法运行**，本轮真实 R1/R2 **不是**默认模式。
   在允许嵌套沙箱的普通终端上用 `python -B tests/tools/codex_revalidate.py --yes-use-quota --model <slug>`
   重跑默认模式，才能把它也记为已验证。该脚本在本宿主会 **exit 2 / 0 次模型调用**并明确拒绝，
   **不会**用提权参数把结果做绿。**2026-11-07 已在普通终端补跑成功，见 §15。**
2. 真实取消/超时/断线/额度事故**没有**人为制造；这部分是 153 项可控假样本 + 无登录 `exec` 的真实认证失败
   （trust 测试，不调用模型）。
3. `spawn_agent` / `list_agents` 在 `--disable multi_agent` 后仍出现在基础工具列表；不宣称子代理已验证关闭。
4. Linux `CORE_EXPERIMENTAL`、Windows `STATIC_GAP` 不变；事件流不含模型名（`model_observed` 恒空）。

`TESTED_VERSION` 记录的是**被复核的 CLI build**，不是对所有部署矩阵的无条件承诺
（`src/par/providers/codex.py` 常量上方写有同样的注释）。`pyproject.toml` / `__version__` 为 `1.0.0`；
2026-11-09 已按 Maintainer 授权发布 `v1.0.0`，发布范围仍受本节限定。

## 15. 2026-11-07 默认沙箱复核：`GREEN` 升级

§14 的唯一限定（默认 `-s` 模式未复核）已补完。完整证据：
[reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md](reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md)。

| 项 | 结果 |
|---|---|
| 宿主 | macOS 15.6.1 arm64；`sandbox_check = 0`（未被外部 Sandbox 约束）；`nested_sandbox_available: true` |
| 命令 | `python -B tests/tools/codex_revalidate.py --yes-use-quota --model gpt-6-luna` |
| 退出码 | **0**；offline 8/8、trust 3/3、real_smoke rc=0 |
| 真实调用 | **2 次**（R1 + R2，ChatGPT 订阅额度） |
| R1 | `REVIEW_REQUIRED`；模型正确答出 `add(2, 3) = -1`（与 spec 的 `5` 不符），0 文件变更 |
| R2 | `MEASURED · boundary CLEAN · checks PASSED (1/1)`；补丁 1 文件 +1 −1；0 边界违规；未应用到项目 |
| 沙箱模式 | 产品代码默认 `-s read-only\|workspace-write`；**未改适配器、未新增开关、未传任何提权参数** |
| 副作用 | 夹具工作树 clean、真实数据目录未变、`~/.codex/config.toml` 未变 |
| 等级 | 现有 Codex 从 `GREEN (scoped)` 升为 **`GREEN`**；仍只覆盖该版本/OS/账号组合，不是新增 Provider |

仍未人为制造真实取消 / 超时 / 断线 / 额度耗尽事故；这部分仍由 153 项可控假样本与
offline/trust 测试覆盖。`spawn_agent` / `list_agents` 仍出现在基础工具列表，不宣称子代理已验证关闭。
没有打 tag / 发 Release，T14 的「不新增自动 Provider」结论不变。
