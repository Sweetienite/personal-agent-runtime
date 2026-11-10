# 运行手册（P3）

## 安装、升级与卸载

```bash
cd /path/to/personal-agent-runtime
uv venv --python 3.12 .venv && uv pip install -e .      # 没有 uv：python3.12 -m venv .venv && .venv/bin/pip install -e .（如果这个 python3.12 本身是 `uv python install` 装的，plain venv 会被 PEP 668 挡住，用前一条 uv venv 命令即可）
.venv/bin/par version
```

- 要求 Python ≥ 3.11（已在 3.11、3.12、3.13、3.14 上跑过全部测试）。零第三方依赖。
- 升级：`git pull` 后重新执行 `uv pip install -e .`；可编辑安装下代码改动立即生效。
- 卸载：删除 `.venv` 与项目目录，不影响任何 Harness 文件；运行数据另见 [ROLLBACK.md](ROLLBACK.md)。
- 没有常驻进程，也没有 LaunchAgent。`par task run` 是前台命令，跑完退出；`par mcp serve`（MCP 桥，见 [MCP.md](MCP.md)）只在你或 Claude Desktop 启动它时存在。

## 命令

| 命令 | 要点 |
|---|---|
| `par version [--json]` | 版本、阶段、Python 与平台 |
| `par doctor [--json] [--strict] [--data-dir PATH]` | 只读；`--strict` 把 WARN 也当失败 |
| `par init [--data-dir PATH]` | 创建数据目录与 `config/`，权限 0700；已存在且不安全时拒绝，不会 `chmod` |
| `par harness check [--json] [--save-baseline] [--no-baseline] [--core P] [--root P] [--max-depth N] [--data-dir P]` | Harness 体检 |
| `par fixture create [ID]` | 造一个一次性的**夹具项目**（`<数据目录>/fixtures/<ID>/`，默认 `fixture`）：小型 git 仓库 + Harness 文件 + 一个故意的 bug，不含个人数据；登记为可写。它始终可写（真实项目要你先 `par project enable-write`） |
| `par project add ID PATH [--vcs auto\|git\|none] [--context-mode generic\|harness]` | 登记项目；默认 generic（普通 Git 仓库，不要求 `AGENTS.md`/`.ai/STATE.md`，也不写项目）；`--context-mode harness` 要求并沿用 Harness 规则；只追加到 `projects.toml`，整份文件校验通过才写入 |
| `par project enable-write ID [--confirm ID]` / `disable-write ID` | **P8 的写入开关**（只对真实项目）：`enable-write` 先体检（基线干净、有 git、git 里看不到敏感路径；Harness 项目还要求 STATE 不超 15 KB、恰好一份生效的主合同），不通过就列出原因并且什么都不改；通过后加 `--confirm ID`（再敲一遍项目 id）才真的打开。只改 `projects.toml` 里该项目的两行（旧文件留作 `*.par-backup-<时间>`），并在 `logs/project_writes.jsonl` 记一行。`disable-write` 随时可用 |
| `par project list [--json]` | 已登记项目一览 |
| `par project show ID [--manifest] [--json]` | 事实、写入就绪、Context Manifest |
| `par task draft --draft FILE\|-` `[--json]` | 把最小草案（项目、目标、risk、allowed_paths、acceptance）编译成严格合同；**只打印，不提交**；缺安全关键字段时以退出码 2 返回问题清单，不产出合同 |
| `par task submit --contract FILE\|-` `[--by user\|coordinator]` `[--check]` `[--candidate ID]` | 校验合同并入队；相同幂等键+相同合同返回原任务。`--check`：只做校验，**不入队**——不写任务、不写事件、不记批准，只报告合同是否通过以及会问你批准哪些事项（`WRITE_PERMISSION`/`WORKER_OVERRIDE`），拒绝时和不加 `--check` 报的原因完全一样 |
| `par task list [--waiting]` `status T` `events T` `result T` `diff T` | 只读查看：状态与你下一步能做什么、事件日志、结果信封、补丁（不应用到任何地方）；`--waiting`（2026-11-03 新增）只列出需要你决定才能往下走的任务（待批准、待验收、待恢复、卡在额度/登录/限速/没有可用 Worker 上） |
| `par task run T` / `par task run --next` | 前台跑**一次** attempt；Ctrl-C 或 SIGTERM 会停掉 Worker 并留下干净记录 |
| `par task approve T [--id N]` `reject T [--id N]` | 逐项批准 / 拒绝待决事项；有多项时必须指定 `--id`；默认由你决定，`approval_mode` 为 `coordinator_scoped` 时主控需要一条活跃 grant |
| `par coordinator grant NAME --action ... --expires ...` / `grants` / `revoke N` | 给人以外的协调者创建/查看/撤销限定 grant（动作、审批类型、项目、风险、到期）；只能由人在 CLI 执行，台账只增不改 |
| `par task accept T` `close T` | 接受 `REVIEW_REQUIRED` 的结果 / 归档。接受**不会**把补丁应用到项目 |
| `par task cancel T` `pause T` `resume T` | 取消（会停掉运行中的 Worker）、暂停、恢复 |
| `par task retry T [--worker ID] [--force] [--fresh]` | 新 attempt，采用当前配置；写文件的任务**默认接着上一次已验证的改动继续**（接力，见 [HANDOFF.md](HANDOFF.md)），`--fresh` 从基线重来；`--force` 清除健康记录 |
| `par candidate create --task A [--task B …] [--note TEXT]` | 候选整合：按依赖顺序把已验收 R2 任务的 `work.patch` 打到第一个任务的干净 base 上，提交到 `<数据目录>/candidates/<id>/repo`，再跑各任务的检查；记录补丁 sha256、提交、允许路径与检查结果。冲突/受保护路径/检查失败 → `FAILED`；真实项目不动 |
| `par candidate show ID` / `par candidate list [--project ID]` | 只读查看候选：base、commit、任务/补丁、检查、失败原因 |
| `par task rebase T --candidate ID` | 已排队、从未运行的任务改用候选基线；有 attempt 或 handoff 包时拒绝 |
| `par task handoff T [--to APP] [--note TEXT]` | 为人工交接（WorkBuddy、TRAE、Cursor、Windsurf、Cline、Aider 或任何没有脚本化接口的应用）准备交接包：`PROMPT.md`、`README.md`、`manifest.json`、私有副本 `workspace/`；输出 `mode`、`workspace_absolute_path`、`prompt_path`、`manifest_path`、`base_commit`、`allowed_paths` 与可照抄的 `next_actions`；`QUEUED` 的任务会被暂停，防止别的 Worker 抢跑 |
| `par task import T --from DIR \| --patch FILE \| --text FILE [--worker APP]` | 把人带回来的结果交给 Kernel 验证（文件比对、边界、合同里的检查），结果和普通 Worker 一样进入 `REVIEW_REQUIRED` |
| `par coordinator status\|list` / `use NAME [--note T]` / `kit NAME` / `prompt NAME` | 可选主控：看谁在主控与最近出现；`use` = 写接管包并记录切换（你敲命令就是确认）；`kit` 打印接入片段（只打印，不改任何应用的配置）；`prompt` 打印起手提示词。详见 [COORDINATORS.md](COORDINATORS.md) |
| `par takeover export [--project ID]` / `confirm ID --as NAME` / `status` | 主控交接：只读接管包 → 显式确认（只有最新的包可确认）→ 同一时刻只有一个主控 |
| `par mcp serve [--data-dir P] [--coordinator NAME]` / `par mcp config` | MCP 桥（由 Claude Desktop 启动，不要手动跑）/ 打印要粘贴进 Claude Desktop 配置的片段（不写文件） |
| `par task recover [T] [--terminate]` | 结算 Kernel 已消失的 attempt；`--terminate` 只停能确认身份的进程组 |
| `par health [--probe]` / `par health clear PROVIDER [--model M]` | 哪些 Worker 现在能跑、为什么不能；清除一条健康记录。`--probe` 另外让每个已注册的适配器**离线**报告自己的状态（版本、签名、登录、隔离是否证明、模型目录），**不调用模型、不耗额度** |

以上 `task` 与 `health` 命令都支持 `--json` 与 `--data-dir`。各状态的含义、可做的事和恢复规则见 [KERNEL.md](KERNEL.md)。

数据目录默认 `~/Library/Application Support/PersonalAgentRuntime`；`PAR_DATA_DIR` 环境变量或 `--data-dir` 可覆盖，必须是绝对路径。

### 退出码

| 命令 | 0 | 1 | 2 |
|---|---|---|---|
| `doctor` | 无 FAIL | 有 FAIL，`--strict` 下有 WARN，或路径被策略拒绝 | 用法错误 |
| `init` | 已就绪或已创建 | 目录不安全或路径被策略拒绝 | 用法错误 |
| `harness check` | 无 FAIL，且与基线无差异 | 有 FAIL，或与基线有差异 | 配置或用法错误 |
| `project add` | 已登记 | 被拒绝（不是 Harness 项目、重复、受保护位置等），文件不变 | 用法错误 |
| `project list` `show` | 成功 | | 登记表有错误或项目不存在 |
| `task submit` | 已入队（含「已提交过」） | 被拒绝（合同无效、策略、幂等冲突……），台账不变 | 读不到合同文件、数据目录未就绪、配置有错 |
| `task run` | 产出了待审阅的结果，或自动切换后重新排队 | 其他任何结局（失败、等待批准、被阻塞、暂停、被中断……）；或被容量/锁拒绝 | 用法错误、任务不存在、配置有错 |
| `task` 其余命令 | 完成 | 该状态下不适用（例如接受一个没在审阅的任务） | 任务不存在、用法错误 |
| `health` | 成功 | | 配置有错、数据目录未就绪 |

`harness check --save-baseline` 表示「我已看过，接受这个状态」，所以此时只有 FAIL 会让退出码变 1。

## 兼容与回退（P1 上下文模式）

- `projects.toml` 里**没有** `context_mode` 的条目按 `harness` 处理；generic 注册会写 `context_mode = "generic"`，harness 注册不写这一行，所以旧版本仍能读回原来的 Harness 项目。
- 没有 SQLite schema 迁移，也没有自动改写旧配置：`par doctor` / `par project show` 只读显示当前模式，不会“修正”旧记录。
- 回退到 P1 之前的版本：先备份 `projects.toml`，再移除 generic 条目里的 `context_mode = "generic"`（旧版本会把它当未知字段，整条丢弃），或恢复 `projects.toml.par-backup-*`。harness 条目本来就没有这一行。
- 已排队/暂停/待验收的任务保留当时的合同与基线；新配置只影响新提交的任务。不要为了“迁移”清空台账。

## 配置：Profile 与 Provider（P2 起使用）

任务的路由由你的配置决定，放在数据目录的 `config/` 下（建议 `chmod 600`）：

```bash
cd /path/to/personal-agent-runtime
D="$HOME/Library/Application Support/PersonalAgentRuntime/config"
cp config/profiles.example.toml  "$D/profiles.toml"     # Worker、角色、备用链、失败策略
cp config/providers.example.toml "$D/providers.toml"    # 哪些 Provider 启用
chmod 600 "$D"/*.toml
.venv/bin/par health                                    # 看每个 Worker 现在能不能跑、为什么不能
```

- **适配器只在两个条件同时满足时才存在**：Provider 写成 `enabled = true` **并且**给了绝对路径的 `executable`（现在有 `codex`（P3）、`antigravity`（P4）与 `copilot`（2026-11-10，仅 R0/R1 文本答题））。其余（`claude_host`、`manual_handoff`、`gemini_legacy`）即使启用，`par health` 也显示 `no adapter is registered`，任务停在 `BLOCKED_NO_WORKER`；`gemini_legacy` 永远不能启用。
- 模型 slug 保持 `<RUN-TIME-VERIFIED-SLUG>` 占位符时该 Worker 不会被使用，必须等适配器探测出真实可用的名字。
- `worker_failure_policy`：`ask`（默认）、`pause`、`safe_auto`（只对 R0/R1 自动重试暂时性错误一次、自动换 Worker 一次；R2 永不自动）。
- `approval_mode`：`coordinator_autonomous`（默认，主控可自行批准/验收，台账记录范围）、`coordinator_scoped`（需要人在 CLI 用 `par coordinator grant` 创建限定 grant）、`human_only`（只有人能决定）。
- **角色不绑定厂商**：`[profiles.<名字>.roles.<角色>]` 里的角色名（如 `code`）只是 primary/fallback 的名字，不要求某个厂商做主控或 Worker。同一个工具可以同时是 `coordinator` 和某个 worker 的 provider；两者的会话、私有 HOME 与审计仍然隔离，`coordinator` 可以是 `claude_desktop` / `codex` / 旧别名 `codex_manual_takeover`。配置 schema 没有变，旧 `profiles.toml` 原样可用；`surface` 由 provider `kind` 派生，只出现在配置快照里。
- **能力闸门**：自动运行要求适配器声明 `headless = yes`，R2 写任务还要求 `write = yes`。声明是注册时的静态事实（`build_adapters`），Kernel 在运行前用本次实例再复核；`unknown` 不会当成 `yes`，失败发生在任何子进程的模型调用之前。`par task status T` 会写明原因。

## 接入 Codex（P3）

```bash
D="$HOME/Library/Application Support/PersonalAgentRuntime/config"
# providers.toml：启用 codex，并写明【已签名的二进制本身】（不是 bin/codex 那个脚本，也不是 PATH 上的 codex）
cat > "$D/providers.toml" <<'TOML'
schema_version = 1

[providers.codex]
kind = "codex"
enabled = true
executable = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
TOML
chmod 600 "$D/providers.toml"
.venv/bin/par health --probe        # 离线：版本、OpenAI 签名、ChatGPT 登录、隔离（22 个功能开关与 MCP 服务器是否关干净）、模型目录
```

- **模型由你决定**：在 `profiles.toml` 的 `[workers.<id>]` 里写 `provider = "codex"` 与 `model = "<slug>"`。可用的名字在 `par health --probe --json` 的 `probes[0].facts.models_in_catalog` 里；模型不在目录里只是提示，不会阻止运行（目录是本地缓存，可能落后）。V1 没有「按 Worker 指定推理强度」：你的 Codex 全局配置（`model_reasoning_effort = "high"` 等）继续生效。
- **先在夹具上试**：`par fixture create` 造出 `fixture` 项目，然后提交 `config/fixture_r1.example.json`（只读问一句）与 `config/fixture_r2.example.json`（改 `app/` 里的文件）。R2 仍需你逐项批准写权限（`par task approve`），补丁只保存、不应用。
- **Codex 的 Worker 副本不含 `.git`**：Codex 在 git 工作副本里以可写模式运行时，会往你的 `~/.codex/config.toml` 追加一条受信任项目（真实烟测发现，见 [CODEX.md §9](CODEX.md)）；所以它拿到的是普通文件副本。Kernel 的判断与补丁不受影响；模型在副本里不能用 `git status` / `git diff`。
- **额度用尽时**：Codex 的 usage-limit 消息会被识别为 `QUOTA_EXHAUSTED`；消息里若有「try again at 7:30 PM」，该时间就是整个 Codex Provider 的到期时间（`par health` 里能看到 `until …`），到点后新任务会自动恢复路由；否则一直保持到你 `par task retry T --force` 或 `par health clear codex`。`ask` 策略下 Kernel 只会提出换 Worker，不会自己换。
- **启动前被拒**：`par task status T` 里看到 `the adapter refused to start: …` 时，原因就写在后面（签名不对、登录方式不是 ChatGPT、隔离无法证明、可执行文件不存在……）。这类拒绝发生在任何模型调用之前。
- **真实调用要你批准**：测试套件从不调用真实的 Codex。想自己跑一次真实烟测：`python -B tests/tools/smoke_codex.py --yes-use-quota --model <slug> [--write]`（在临时数据目录里造夹具、跑一次只读问题，加 `--write` 再跑一次一行修复；会消耗订阅额度）。`--self-test` 用假 codex 演练同样的步骤，不耗额度。

## 接入 Antigravity（P4）

```bash
D="$HOME/Library/Application Support/PersonalAgentRuntime"
# 安装是私有的：下载官方包 → 核对官方清单里的 SHA-512 → 只解出包里唯一的成员 → 不执行地核对 Google 签名 → 放到 vendor/。
# 不要运行官方的 install.sh（它会改你的 shell 配置并执行 `agy install`）。具体做法与 1.3.5 的校验记录见 docs/AGY.md §1、§11。
# providers.toml：启用 antigravity，并写明那个私有安装的二进制（绝对路径；PATH 上的 agy 永不使用）
#   [providers.antigravity]
#   kind = "antigravity"
#   enabled = true
#   executable = "<数据目录>/vendor/agy/1.3.5/agy"
.venv/bin/par health --probe        # 版本、Google 签名、登录状态、模型目录、额度窗口（不调用模型）
```

- **登录**：`agy` 的 Google 登录在 macOS 钥匙串里。Worker 在私有 HOME 里运行，Runtime 在里面放一个指向 `~/Library/Keychains` 的符号链接来让它找到你**已有**的登录（实测：你的 Antigravity 应用留下的登录就能用）。没有登录时 `par health --probe` 显示 `AUTH_REQUIRED`，运行 `par provider login antigravity`：它在 Runtime 自己的 HOME 里把官方的交互登录作为子进程启动（结束后删掉钥匙串链接），你在浏览器里完成（Runtime 不经手任何凭据）。`--print-only` 只显示将要做什么。
- **模型由你决定**：在 `profiles.toml` 的 `[workers.<id>]` 里写 `provider = "antigravity"` 与 `model = "<slug>"`；你的账号能用哪些以 `par health --probe --json` 的 `probes[*].facts.models_in_catalog`（即 `agy models`）为准。额度窗口在 `facts.usage`（每 5 小时 / 每周，含重置时间）。
- **只读任务与写任务**：只读任务 `agy` 以 `strict` 权限预设运行（所有非读取工具被软拒绝），写任务以 `request-review`（工作副本内的文件读写可以，shell 命令被软拒绝）。**软拒绝时运行不失败**（退出码 0），但结果里会有 `worker run: a tool was soft-denied: …` 的提示；是否真的做成，由 Kernel 的文件比对与检查裁决。
- **启动前被拒**：`par task status T` 里的 `the adapter refused to start: …` 会写明原因：项目带了 `.agents/hooks.json` 等（hooks/MCP/插件会执行命令，一律拒绝）、签名不对、`agy` 没登录、钥匙串被锁……
- **真实调用要你批准**：测试套件从不调用真实的 `agy`。只有显式设置 `PAR_REAL_AGY=1`（`PAR_AGY_EXE=…`）的离线核对测试，和需要批准才会运行的 `tests/tools/smoke_agy.py`（`--yes-use-quota --model <slug> --executable <绝对路径> [--try-write-readonly] [--write]`；`--self-test` 用假 agy 演练整个脚本，不耗额度）。
- **升级**：Runtime 永不运行 `agy update`（每次启动都设 `AGY_CLI_DISABLE_AUTO_UPDATE=true`）。要升级就重新做一次私有安装，再按 [AGY.md §13](AGY.md) 复核。

## 提交与运行一个任务

```bash
cat > /tmp/task.json <<'JSON'
{"schema_version": 1, "project_id": "simproj", "title": "Summarise the solver module",
 "description": "Read src/solver and explain how the time step is chosen.", "risk": "R1", "role": "code",
 "acceptance": {"criteria": ["names the function that picks dt"], "checks": []},
 "idempotency_key": "simproj-solver-summary-1"}
JSON
par task submit --contract /tmp/task.json          # → submitted simproj-0001  QUEUED
par task run simproj-0001                          # 前台跑一次 attempt
par task result simproj-0001                       # Worker 自述的摘要（未验证）+ Kernel 测得的边界与检查
par task accept simproj-0001                       # 接受（只是记录；不改项目）
```

- 合同规则见 [KERNEL.md](KERNEL.md)。常见拒绝：项目没有 active 主合同又没写 `ad_hoc`（→ 加 `"ad_hoc": true`）；`risk` 为 `R2`（真实项目没有被你用 `par project enable-write` 打开写入之前，一律被 `WRITE_DISABLED` 拦下）；路径含通配符或 `..`。
- 协调者（Claude）提交时使用 `--by coordinator`（P5 的桥会固定这一点）；它指定 `worker` 需要你批准。
- 被拒绝、阻塞或失败的任务用 `par task status T` 看原因与你能做的下一步。

## 落地一个已验收的任务（`ops/land_task.py`）

`accept` 只是记录，不会把补丁写进真实项目——这一步要你自己做。`ops/land_task.py` 是独立于 Runtime 本体的协调者/人工用小工具，收口这串本来要手敲的动作：

```bash
.venv/bin/python ops/land_task.py simproj-0001            # 试运行：只检查，不改动任何文件
.venv/bin/python ops/land_task.py simproj-0001 --apply    # 应用补丁 → 跑一次全量测试 → 测试通过才提交 → close
```

- 开始前核对：任务必须是 `ACCEPTED`；真实仓库当前的 commit 必须和任务记录的基线一致；工作区必须干净。任何一条不满足就什么都不改，原样报告原因。
- 应用之后如果全量测试没通过，会自动撤销刚才的补丁（`git apply -R`），不会提交任何东西。
- 提交成功之后才会 `par task close`；如果 close 本身失败，会和"什么都没改"分开报告——提交已经是真实、有价值的改动，不能和失败混在一起。
- 这个脚本**不会**改 `.ai/STATE.md`——这一步仍然是你或协调者单独做的事。
- 只通过 `par task status --json` / `par task diff` 读数据，从不直接读 Runtime 的数据目录，也从不 import `par` 的内部模块——`par` 本体"没有任何代码把补丁应用进项目"这句话继续成立。

## 提交并批准一个任务（`ops/start_task.py`）

`par task submit` 默认 `--by user`；协调者（Claude）该用 `--by coordinator`，因为只有这样，钉死一个 `worker` 才会触发 `WORKER_OVERRIDE` 批准——这道闸门专门用来防"AI 自己选了模型，人没点头"。`ops/start_task.py` 用 `--by coordinator` 提交，只在批准事项和它刚提交的那份契约内容**完全一致**时才自动批准，别的一律不碰：

```bash
.venv/bin/python ops/start_task.py contract.json           # 提交；只批准和契约完全匹配的 WRITE_PERMISSION / WORKER_OVERRIDE；不会跑、不花钱
.venv/bin/python ops/start_task.py contract.json --run     # ……再真的跑一次 attempt（调用真实模型，花真实额度）
```

- 只自动批准两种：`WRITE_PERMISSION` 的 `allowed_paths` 和契约里写的完全一样；`WORKER_OVERRIDE` 的 `worker` 和契约里写的完全一样。除此之外任何批准（别的类型、哪怕只是内容不完全一致）都原样报告、留在 `PERMISSION_WAIT`，**既不批准也不拒绝**——拒绝会让任务直接变成已取消，这不该是脚本自己能决定的事。
- 不加 `--run` 就停在"已批准、排队中"；`--run` 才会 `par task run`，而且只跑这一次，跑完报告结果就停，不自动重试、不换 Worker、也不管后面可能又冒出来的批准。跑完如果是「待验收」（`REVIEW_REQUIRED`），会直接把 `par task result`/`par task diff` 的内容打出来，不用再手动敲两条命令——但 accept 和落地（`ops/land_task.py`）还是两步独立的动作，不会被这里一起做掉。
- 写契约容易第一次就写错（`R1` 不能带 `allowed_paths`、Harness 项目的 ad-hoc 不能是 `R2`、`checks` 的程序必须是 Kernel 能解析的 `python`/`python3` 或可信目录里的程序……）：先用 `par task submit --contract FILE --check` 校验一遍，通过了才真的提交或交给 `start_task.py`，省得每次试错都真的碰一下台账。
- 机械字段（role 默认、ad_hoc、幂等键）可以交给草案编译器：`par task draft --draft draft.json` 只打印合同、不提交；`allowed_paths` 或写任务的 `checks` 缺失时它会列出问题并以退出码 2 结束，不会猜一个路径或自动从 README 里挑命令。

## 清理已关闭任务的占用（`ops/cleanup_tasks.py`）

每个任务的私有副本、人工交接导出都会实实在在占硬盘——任务一旦 `CLOSED`，这些东西已经没用了（该留的结果和补丁不在此列）：

```bash
.venv/bin/python ops/cleanup_tasks.py            # 试运行：只报告会释放多少空间，不删任何东西
.venv/bin/python ops/cleanup_tasks.py --apply    # 真删
.venv/bin/python ops/cleanup_tasks.py par_runtime-0001 --apply   # 只清这一个任务（仍然要求是 CLOSED）
```

- 只处理状态**恰好是** `CLOSED` 的任务——`FAILED` 可能还要重试，`REVIEW_REQUIRED`/`ACCEPTED` 还没定论，都不碰。
- 只删固定的几样：每次尝试下的 `workspace/`、`home/`、`tmp/`，以及整个 `handoff/` 导出目录。`result.json`、`work.patch`、`prompt.txt`、`changes.json`、检查日志这些小而有用的证据留着不动。
- 不加 `--apply` 只报告、不删任何文件。

## 可靠性报告（`ops/reliability_report.py`，2026-10-21）

"Worker 靠不靠谱、第一次能不能成、哪个 Worker 成功率低" 这类问题，以前是手写一句"目前 N 个任务 M 次尝试……"塞进 `STATE.md`，每次有新任务落地就过期。改成跑脚本现场从台账算：

```bash
.venv/bin/python ops/reliability_report.py                    # 全部已登记项目
.venv/bin/python ops/reliability_report.py --project par_runtime   # 只看一个项目
```

- 只读：`par task list --json` 拿到全部任务，再对每个任务 `par task status <id> --json` 取它的完整 `attempts` 列表。不碰文件，不碰台账本身。
- 报什么：第一次 attempt 就成功的比例（以及没成功的按 `failure_class` 分类）、没一次就成的任务最终有没有进 `ACCEPTED`/`CLOSED`、按 Worker 分的 attempt 成功率、每个任务用了几次 attempt 的分布。
- `STATE.md` 里如果还留着一句"目前 N 个任务……"，跑一次这个脚本看现在的真实数字，不要凭记忆改那句话。

## Harness 体检与基线工作流

```bash
par harness check                      # 看现状；若已有基线，同时列出与基线的差异
par harness check --save-baseline      # 接受当前状态为新基线（旧基线保留为 baseline.prev.json）
par harness check --json               # 机器可读，适合定期巡检
```

- 结构性检查复用 Core 自带的 `harness_hygiene.py`，不在 Runtime 里重复实现；因此 Core 的检查升级会自动反映到这里。
- 基线在 `<数据目录>/harness/baseline.json`（0600）。只存结构事实，见 [SECURITY.md](SECURITY.md)。
- Harness 文件夹的发现规则：从 `search_root` 起深度不超过 `max_depth`，找含 `.ai/STATE.md` 的目录；嵌套在另一个里面的副本不单独算项目，由所属项目的 hygiene 报告。设置写在 `<数据目录>/config/projects.toml`，样例见 `config/projects.example.toml`。
- 差异不等于出错：你正在改的项目会产生差异。看过确认后用 `--save-baseline` 重置。

## 项目登记

```bash
par project add simproj ~/Documents/SimProj --vcs git
par project add thesis ~/Documents/毕业论文 --vcs none    # 项目自己的规则禁止在根目录建 git
par project list
par project show simproj --manifest
```

- 登记表是 `<数据目录>/config/projects.toml` 里的 `[projects.<id>]`。你也可以手工编辑；真实项目的 `write_enabled = true` 只有在同时写了 `write_approved = "YYYY-MM-DD"` 且 `vcs = "git"` 时才被接受，别的写法整条丢弃。**推荐用 `par project enable-write`**（见下一节）：如果项目是按默认 `vcs = "auto"` 登记的、而根目录确实有 `.git`，开启写入时会把这一行改成 `vcs = "git"`，不需要重新登记。
- 项目路径不能在受保护位置内（Core、各工具配置、shell 配置）；同一目录不能登记两次。
- `vcs = "none"` 表示项目自己的规则不允许根目录有仓库，Runtime 不会把「没有 git」当缺陷，但写任务需要显式的备份或快照流程。
- 清单与写入就绪的含义见 [CONTEXT.md](CONTEXT.md)。

## 定期巡检（launchd，可选，由你自己安装）

默认**没有安装**。它是一个不常驻的定时任务：每周一 09:00 运行一次 `par harness check`，然后退出；Mac 当时在睡眠则唤醒后补跑。

- 它只运行 `par harness check`，不带 `--save-baseline`，不修改任何 Harness 文件。
- 只写数据目录里的两个文件：`harness/last_patrol.txt` 与 `last_patrol.err`（每次覆盖，不增长）。
- **只在需要你关注时发 macOS 系统通知**：检查 FAIL、与基线有差异、还没有基线、检查无法运行、数据目录缺失。无变化时静默。
- 因为 `~/Library/LaunchAgents` 是 Runtime 不允许写入的位置，安装、卸载都由你本人执行。

```bash
cd /path/to/personal-agent-runtime
# 1. 先看一遍（分别 44 行与 40 行）：ops/par-harness-patrol.sh  ops/com.par-runtime.harness-patrol.plist.template
# 2. 渲染并校验
sed "s#__HOME__#$HOME#g" ops/com.par-runtime.harness-patrol.plist.template > ~/Library/LaunchAgents/com.par-runtime.harness-patrol.plist
plutil -lint ~/Library/LaunchAgents/com.par-runtime.harness-patrol.plist
# 3. 安装
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.par-runtime.harness-patrol.plist
# 4. 你在场时手动触发一次，并处理可能弹出的“允许访问文稿文件夹”提示
launchctl kickstart -k gui/$(id -u)/com.par-runtime.harness-patrol
cat "$HOME/Library/Application Support/PersonalAgentRuntime/harness/last_patrol.txt"
# 卸载
launchctl bootout gui/$(id -u)/com.par-runtime.harness-patrol && rm ~/Library/LaunchAgents/com.par-runtime.harness-patrol.plist
```

- **改频率**：编辑 plist 的 `StartCalendarInterval`。每天一次就删掉 `Weekday`；改完执行 `bootout` 再 `bootstrap`。
- **macOS 的“文稿”访问授权**：由 launchd 启动的进程不继承终端的授权，第一次读取 `~/Documents` 时 macOS 可能询问。步骤 4 就是为此。万一仍被拒绝，检查会看到「Core 找不到、项目为 0」，与基线比对是大量差异，会**提醒你**，而不会静默通过（有测试）。前提是你保留着基线。
- 基线用 `par harness check --save-baseline` 刷新。你确认差异无碍之后再刷新，否则每次巡检都会继续提醒。
- 排障：看 `last_patrol.err` 与 `harness/launchd.err`。

## 测试

```bash
python -B -m unittest discover -s tests -t .                              # 1360 项（另有 23 项默认跳过），单进程约 6–9 分钟，无网络、无模型（Codex、agy 用假程序）；含崩溃与信号测试
python -B tests/tools/parallel_tests.py                                  # 同一套测试，每个模块一个子进程并行跑（约 1 分钟）；日常用这个；加模块名只跑某几个，如 `... parallel_tests.py test_kernel_run`
PAR_REAL_HARNESS=1 python -B -m unittest tests.integration.test_real_harness -v   # 只读验证真实 Harness（默认跳过）
PAR_REAL_CODEX=1 python -B -m unittest tests.integration.test_real_codex -v       # 离线验证真实 Codex：签名、探测、每个参数、隔离配方（不调用模型，默认跳过）
python -B tests/tools/mutation_check.py                                  # 变异检验：故意改坏 285 处，测试必须失败（2026-10-21 起默认并行跑，约 11 分钟；`--workers=1` 退回严格串行；可以只跑某几项：加上它们的编号，如 `... mutation_check.py M911 M912`）
python -B tests/tools/mutation_check.py --patterns                       # 只确认每处变异的目标代码还在（几秒）
python -B tests/tools/demo_p2.py                                         # P2 全流程演示（临时目录，脚本化 Worker，无模型）
python -B tests/tools/smoke_codex.py --self-test                         # P3 全流程演示：真实 Codex 适配器 + 假 codex 程序 + 夹具项目（无模型、无额度）
python -B tests/tools/acceptance_matrix.py                              # 总方案 T01–T24 故障矩阵：每一行对应的测试逐个重跑，打印结果表（加 `--write 文件` 存成 Markdown；不调用模型）
python -B tests/tools/restore_drill.py --rehearse-switch                # 恢复演练：把真实数据目录整个拷到临时位置，核对台账、任务、证据、配置，并在副本上演练主控切换；原件只读，副本最后删除（无模型）
```

## 排障

| 现象 | 原因与处理 |
|---|---|
| `doctor` 报 `data_dir ... is not initialised` | 运行 `par init` |
| `doctor` 报 `mode is 0755, expected 0700` | 目录不是由 `par init` 创建或被改过。Runtime 不会自动修；确认无误后自行 `chmod 700` |
| `harness check` 没有项目 | 检查 `--root` 与 `--max-depth`；项目必须含 `.ai/STATE.md` |
| `harness check` 提示 Core 未找到 | 默认 `~/Documents/AI_Harness_Core`；在 `projects.toml` 里设置 `core_path` |
| `harness check --save-baseline` 报 `run par init first` | 数据目录不存在或不安全 |
| 退出码 1 但没有 FAIL | 与基线有差异，向下看差异列表 |
| `task submit` 报 `not_allowed` | 逐条读理由：没有 active 合同、项目有多份合同、`WRITE_DISABLED`、`DIRTY_BASE`、缺 git 基线…… |
| `task run` 报 `capacity` | 同时在跑的 attempt 已达上限（Profile 的 `max_parallel_workers`，默认 1，最多 2）。`par task list` 找 `RUNNING`；若是已消失的 Kernel 留下的，`par task recover`。报 `project_locked` 则是同一个项目里已有一个 attempt 在跑（同一个项目一次只做一件事，不论上限是几） |
| `the adapter refused to start: …` | Codex 适配器的启动前检查没过；原因就在后面（见「接入 Codex」）。没有发生任何模型调用 |
| 任务停在 `QUOTA_EXHAUSTED`，`par health` 显示 `until …` | 到那个时间之前 Codex 不会被路由；到点后自动恢复，或你自己 `par health clear codex` |
| 任务停在 `BLOCKED_NO_WORKER` | `par task status T` 与 `par health` 会逐个列出每个候选为什么不能用（未启用、没适配器、占位模型、冷却中……） |
| 任务停在 `RECOVERY_REQUIRED` | 上一个 Kernel 消失了。`par task status T` 看是否有残留进程；`par task recover T --terminate` 停掉能确认的进程组；确认后 `retry` 或 `cancel` |
| `task run` 提示 `project changed since submission` | 项目的提交、规则或 STATE 变了。看清楚后 `par task approve T` 以当前状态为基线，或 `reject` 取消 |
| `data directory ... is not ready` | 运行 `par init` |
