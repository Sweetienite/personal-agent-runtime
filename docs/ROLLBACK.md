# 回滚（P0 到 P4）

> 下面的分阶段表格和「Core 的回滚」一节，记录的是这个项目自己最初开发时、在作者本机上发生的具体改动（包含一份本仓库之外的私有资料包、一个独立的 Harness Core 仓库）——对你自己的安装环境不直接适用，留着是为了这个项目自身的历史完整。你自己想备份、恢复或卸载，看「备份与恢复」「卸载 Runtime」「回滚后验证」这三节就够了，它们是通用的。

P0 动了 5 处；P1 在数据目录里多了一份登记表（第 6 处，Runtime 自己的文件）；第 7 处是你可选安装的巡检；P2 在数据目录里多了台账与任务产物（第 8 处，同样是 Runtime 自己的文件）；P3 多了夹具项目目录与 Codex 的配置条目（第 9 处，同样是 Runtime 自己的文件）。每一处都能独立回滚，且都是**非破坏性**的：先改名保留，确认后再由你决定是否删除。

| # | 改动 | 回滚 |
|---|---|---|
| 1 | Runtime 项目本身。它与施工资料包**共用** `~/Documents/Personal Agent Runtime/`，所以不要整体改名 | 只移走仓库内容，保留资料包与候选稿，见下「卸载 Runtime」 |
| 2 | 运行数据目录 `~/Library/Application Support/PersonalAgentRuntime/`（含 `config/`、`harness/baseline.json`） | `mv "$HOME/Library/Application Support/PersonalAgentRuntime" "$HOME/Library/Application Support/PersonalAgentRuntime.removed"` |
| 3 | Core 1.2.0 → 1.3.0（5 个文件，未提交） | 见下 |
| 4 | 另一个真实项目的 `.gitignore` 加一行（未提交） | `git -C ~/Documents/SimProj restore .gitignore` |
| 5 | 施工资料包目录里的 `Harness整改候选清单_20261014.md` 与 `Harness_候选稿/` | 普通文件，直接删除或移走 |
| 6 | P1 的项目登记 `~/Library/Application Support/PersonalAgentRuntime/config/projects.toml` | 删除该文件即可；它只是 Runtime 自己的登记表，不影响任何项目 |
| 8 | P2 的执行台账 `…/PersonalAgentRuntime/runtime.sqlite3`（含 `-wal` `-shm`）、`tasks/`（每个 attempt 的提示词、日志、补丁、结果、Worker 的副本）、`scratch/`、可选的 `config/profiles.toml` `providers.toml` | 无任务在运行时（`par task list` 没有 `RUNNING`）：`mv "$HOME/Library/Application Support/PersonalAgentRuntime/runtime.sqlite3"* "$HOME/Library/Application Support/PersonalAgentRuntime/tasks" …/removed/`。只是 Runtime 自己的执行记录；**没有任何项目被改动**，所以不需要还原项目 |
| 10 | 你批准后写入真实数据目录的 `config/providers.toml` 与 `config/profiles.toml`（启用 Codex，Worker `codex_luna` = `gpt-6-luna`），以及首次使用 `par health` 时自动建立的空台账 `runtime.sqlite3`、`scratch/`、`tasks/` | 停用 Codex：把 `providers.toml` 里 `[providers.codex]` 的 `enabled` 改回 `false`；整体撤销：把这两个 toml 与台账一起移走（无任务在运行时）：`mkdir -p "$HOME/Library/Application Support/PersonalAgentRuntime.removed" && mv "$HOME/Library/Application Support/PersonalAgentRuntime/config/"{providers,profiles}.toml "$HOME/Library/Application Support/PersonalAgentRuntime/runtime.sqlite3"* "$HOME/Library/Application Support/PersonalAgentRuntime/"{scratch,tasks} "$HOME/Library/Application Support/PersonalAgentRuntime.removed/"`（它们只是 Runtime 自己的配置与执行记录，没有任何项目被改动） |
| 9 | P3 的夹具项目 `…/PersonalAgentRuntime/fixtures/`（一次性的演练项目，不含个人数据），以及你写进 `config/providers.toml` / `profiles.toml` 的 Codex 条目；`projects.toml` 里 `fixture = true` 的条目 | 删除 `projects.toml` 里 `[projects.fixture]`（或你起的名字）那一段，再 `mv …/PersonalAgentRuntime/fixtures …/fixtures.removed`；把 `providers.toml` 里 `codex` 的 `enabled` 改回 `false` 即停用 Codex。烟测脚本在系统临时目录里留下的 `par-smoke-codex-*` 文件夹是私有的，确认不需要后自行删除 |
| 11 | P4 的 Antigravity：①`…/PersonalAgentRuntime/vendor/agy/1.3.5/agy`（官方二进制，私有安装，目录 0700）；②你批准后改写的真实 `config/providers.toml`（启用 `antigravity`，原文件备份为 `providers.toml.par-backup-20261015`）；③（只有你运行过 `par provider login antigravity` 才有）`…/PersonalAgentRuntime/agy_login_home/` 与 `scratch/agy_login_cwd/`（钥匙串链接在登录结束后即被删除）；④每次探测/运行产生的私有 HOME（临时目录与 `tasks/<id>/attempts/<n>/home/`，Runtime 自己的文件） | 停用：把 `providers.toml` 里 `[providers.antigravity]` 的 `enabled` 改回 `false`，或 `cp providers.toml.par-backup-20261015 providers.toml`（注册表立刻不再含 agy）。卸载：`rm -r "$HOME/Library/Application Support/PersonalAgentRuntime/vendor/agy"`（只删这个文件夹；没有改过 PATH 与 shell 配置；`~/.gemini` 被一项变异检验误改过的部分已还原，见下文「P4 之后的特别说明」，所以没有别的要还原）。`agy_login_home/` 可直接删除。**钥匙串里的 Google 登录条目不是 Runtime 建的**（是你的 Antigravity 应用留下的），Runtime 从不读写它，只在私有 HOME 里放一个指向钥匙串目录的符号链接；要撤销登录，在 Antigravity 里登出即可 |
| 7 | 定期巡检（**仅当你自己安装过**） | `launchctl bootout gui/$(id -u)/com.par-runtime.harness-patrol && rm ~/Library/LaunchAgents/com.par-runtime.harness-patrol.plist` |

## 卸载 Runtime（第 1 项）

```bash
cd "$HOME/Documents/Personal Agent Runtime"
mkdir ../Personal_Agent_Runtime.removed
mv .git .gitignore .venv README.md config docs ops pyproject.toml src tests ../Personal_Agent_Runtime.removed/
```

资料包 `Personal_Agent_Runtime_V1.0施工资料包/`、`Harness_候选稿/` 和候选清单留在原处。确认无误后，是否删除 `Personal_Agent_Runtime.removed/` 由你决定。

## 备份与恢复（P8 演练过）

**要备份的是整个数据目录**（`~/Library/Application Support/PersonalAgentRuntime/`），不是只有 `runtime.sqlite3`。台账跑在 WAL 模式：最新的事务先写进 `runtime.sqlite3-wal`，过一阵才并进主文件。只拷主文件的备份会悄悄丢掉最新的任务；恢复演练里就量到了：只拷 `runtime.sqlite3` 的备份只有 2 个任务中的 1 个、26 条事件中的 11 条。

做法：先让 Runtime 空闲（`par task list` 没有 `RUNNING`；Codex 与 Claude 里的 `par` 连接最好先关掉），再把整个文件夹拷走——台账的三个文件（`runtime.sqlite3`、`-wal`、`-shm`）要在同一时刻、一起拷：

```bash
cp -Rp "$HOME/Library/Application Support/PersonalAgentRuntime" "$HOME/par-backup-$(date +%Y%m%d)"
```

- 备份里有每个任务的私有副本、补丁、结果和 agy 的私有 HOME，放在只有你能读的位置（数据目录本身是 0700，`cp -p` 会保留）。
- `vendor/`（agy 的私有安装，约 170 MB，可重新安装）可以不拷：`rsync -a --exclude vendor …` 或拷完后删掉备份里的 `vendor/`。恢复之后 `par health --probe` 会告诉你要不要重装。
- **拷的那几秒里不要有任务在写。** 想在 Runtime 开着时备份台账，用 SQLite 自带的在线备份，它给出一份完整的单文件副本（不需要 `-wal` `-shm`；已在你的真实台账上试过：2 个任务、26 条事件、完整性检查 ok）：`sqlite3 "$HOME/Library/Application Support/PersonalAgentRuntime/runtime.sqlite3" ".backup '备份文件夹/runtime.sqlite3'"`，再 `chmod 600` 那个文件（`.backup` 新建的文件按系统默认权限，是别人可读的）；其余文件照常拷。

**恢复**：原来的数据目录先 `mv` 改名保留（不要覆盖），把备份文件夹拷回原位置（`cp -Rp 备份 "$HOME/Library/Application Support/PersonalAgentRuntime"`）；或者原位置不动，直接用 `--data-dir` 指向备份，先看看是否完好：`par doctor --data-dir ~/par-backup-20261016`、`par task list --data-dir …`。恢复后依次：`par doctor`、`par task list`、`par health --probe`。

**演练**（原件只读，不调用模型）：`python -B tests/tools/restore_drill.py --rehearse-switch`。它把真实数据目录拷到临时位置，在副本上核对：台账通过 SQLite 完整性检查、任务/事件/attempt/批准数量与每个任务的状态都和原件一致、`par doctor` 通过、`par task list/result/diff` 在新位置读得出已验收任务的证据、项目与配置能加载；再在副本上演练主控切换（Claude → Codex → Claude：被替换的主控提交被拒绝但仍可读，新主控可以提交，切回成功），最后删除副本，并确认原台账没有变化。（这次演练的原始输出留在作者本机的开发副本里，没有包含进本仓库。）

## Core 的回滚（第 3 项）

改动尚未提交，所以：

```bash
git -C ~/Documents/AI_Harness_Core restore .          # 还原 5 个文件
# 或使用保存的补丁：
git -C ~/Documents/AI_Harness_Core apply -R "<资料包目录>/Harness_候选稿/H1_Core1.3.0.patch"
```

之后确认：

```bash
git -C ~/Documents/AI_Harness_Core status --short     # 应为空
python3 -B -I ~/Documents/AI_Harness_Core/scripts/sync_runtime_core.py --check   # 3 个 OK
```

若改动已被提交，则用 `git revert <提交>`。Core 的回滚触发条件见 Core 文档 05 §6。

## P5–P7 之后的特别说明

这三个阶段**没有改动仓库和数据目录之外的任何东西**：没有改 Claude Desktop 的配置、没有改任何 Provider 的配置、没有启动常驻进程（`par serve` 与 `par mcp serve` 只在你或 Claude Desktop 启动它们时存在）。回滚 = 回到 P4 的提交：`git revert --no-edit <P4 最后一个提交>..HEAD`（P4 之后的第一个提交见 `git log`，是 `fix(p4): file-changing agy tasks get write_file allow-rules…`）。

- 数据目录里 P5–P7 产生的东西都是**新增文件**，删掉不影响旧功能：`tasks/<任务>/handoff/`（接力包与人工交接包）、`takeover/`、`logs/web_audit.jsonl`、`config/*.par-backup-*`（控制台改配置前的备份）。台账里新增的只是新的事件类型（`RELAY_SEED` `HANDOFF_EXPORTED` `HANDOFF_IMPORTED` `TAKEOVER_EXPORTED` `TAKEOVER_CONFIRMED`），旧版本读不懂它们也不会出错。
- 如果你之后批准把 MCP 桥接进了 Claude Desktop：回滚方法是从它的配置里删掉 `par` 这一项并重启 Claude Desktop（接入时我会先备份那个配置文件并告诉你备份路径）。
- 控制台（中文轻量版）**不再改任何配置**；更早的版本改过 `profiles.toml`/`providers.toml` 时，同目录有 `*.par-backup-<时间戳>`，把它拷回原名即可。

## 路线 A（可选主控）之后的特别说明

路线 A 同样**没有改动仓库和数据目录之外的任何东西**：接入片段只是打印出来的文字，Runtime 不写 `~/.codex/config.toml`、`claude_desktop_config.json` 或任何应用的配置。回滚 = 回到路线 A 之前的提交：`git switch -c back-to-p7 69069f0`（新开一个分支，`main` 原样保留）。

- 数据目录里新增的只有：`takeover/` 里的接管包（可以删）、台账里追加的 `COORDINATOR_SEEN` / `TAKEOVER_*` 事件（台账只追加，不需要也不能删，旧版本只是不认识 `COORDINATOR_SEEN`）、一个只读加速用的索引 `events_by_type`（旧版本没有它也能用；想去掉：`sqlite3 <数据目录>/runtime.sqlite3 'DROP INDEX IF EXISTS events_by_type'`，先确认没有 `par` 进程在用台账）。
- 如果你之后批准把桥接进了 Codex 或 Claude：回滚方法是让对应应用删掉 `par` 这一项——Codex：`codex mcp remove par`（它改的是 `~/.codex/config.toml`，接入时我会先备份并告诉你备份路径）；Claude Desktop：从 `claude_desktop_config.json` 的 `mcpServers` 里删掉 `par` 再完全重启；Claude Code：`claude mcp remove par`。
- 切换主控之后想回到原来的：`par coordinator use claude_desktop`（再写一个接管包并记录；不需要删除任何东西）。

## P4 之后的特别说明

- P4 **没有修改过任何真实项目，Runtime 的产品代码也没有写过你的 `~/.gemini`、shell 配置或 PATH**（**唯一的例外是我自己的一项变异检验误写过你的真实 home，已按你的批准还原**：`~` 与 `~/.gemini` 的权限位曾被改成 700、`~/.gemini/antigravity-cli/settings.json` 曾被新建；现在 `~` 是 750、`~/.gemini` 是 755、该文件与目录已删除，`~` 上多了一个 macOS 自己管理的空 `com.apple.provenance` 标记，无害）：`agy` 是按「核对 SHA-512 → 只解出唯一成员 → 不执行地核对 Google 签名 → 放进 `vendor/`」私有安装的，官方 `install.sh`（会改 shell 配置并执行 `agy install`）从未运行。真实探测前后对 `~/.gemini`、shell 配置、`~/.config`、`~/.cache`、`~/.local`、`~/Library/{Application Support,Caches,Logs,Preferences,Keychains}` 做了名字/大小/修改时间的快照，没有变化。
- `agy` 自己的状态（会话库、日志、内置技能、`settings.json`……）都落在**私有 HOME** 里（探测用的是临时目录，Worker 用的是 attempt 目录），随目录一起删除即可。
- 唯一对你真实配置的改动是 `providers.toml`（启用 `antigravity`，已备份）；`profiles.toml` 没动。
- 退回「只有 Codex」：见上表第 11 项。代码层面：`git revert` P4 的提交；数据目录里的台账与结果信封向前兼容。

## P3 之后的特别说明

- P3 **没有修改过任何真实项目，Runtime 自己也没有写过 Codex 的配置**：适配器从不打开 `~/.codex` 里的任何文件（只通过 Codex 自己的子命令间接询问）。Codex 自己会在 `~/.codex` 里写诊断日志、续期令牌——那是 Codex 的行为，不属于本次改动，也无需回滚。
- **唯一的例外是 Codex 自己写的一条信任条目，已处理**：第一次真实 R2 烟测（修复「Worker 副本去掉 `.git`」之前）时，Codex 往 `~/.codex/config.toml` 追加了一条受信任项目。你批准后我已把 `config.toml` 原样备份为 `~/.codex/config.toml.par-backup-20261014`，再只删去那三行。**要恢复到处理之前的状态**：`cp ~/.codex/config.toml.par-backup-20261014 ~/.codex/config.toml`（会把那条无害的条目放回去）；备份确认不再需要后自行删除。
- 启用 Codex 之后若想退回「没有任何真实适配器」：把 `providers.toml` 里 `[providers.codex]` 的 `enabled` 改回 `false`（或删掉 `executable`）。注册表立刻变空，所有任务回到 `BLOCKED_NO_WORKER`。
- 代码层面：`git revert` P3 的提交即可；数据目录里的台账与结果信封是向前兼容的（`usage` 是可选字段）。

## P2 之后的特别说明

- P2 **从未修改过任何项目**：所有 Worker 都在 `tasks/<id>/attempts/<n>/workspace/` 里工作，补丁只保存、不应用。回滚 P2 不需要碰项目文件夹。
- 台账是追加式的，不要手工编辑；要重来就整体移走（见第 8 项），下次使用会建一份新的空台账。
- Worker 的副本不会被自动清理（它们是证据）。确认不再需要后，你自己删除 `tasks/` 下对应目录即可；删除前请先确认没有 `RUNNING` / `RECOVERY_REQUIRED` 的任务在用它们。
- 如果 `par task run` 被强行杀掉，下一次 `par task run` 或 `par task recover` 会结算遗留的 attempt；不需要手工清理数据库。

## P1 上下文模式（2026-11）

- 没有数据库迁移：旧任务、审批与 attempt 记录原样可读；回退代码不需要还原台账。
- 旧 `projects.toml` 没有 `context_mode` → `harness`；harness 的新注册也不写这一行。
- 回退到 P1 之前的代码：先备份 `projects.toml`，再删除 generic 条目里的 `context_mode = "generic"`（旧版本会把它当未知字段、整条丢弃），或恢复 `projects.toml.par-backup-*`。不要删除项目目录或台账。

## 候选整合（2026-11）

- `candidates` 表是**增量**的（`CREATE TABLE IF NOT EXISTS`），没有提升台账 `user_version`：旧版本打开同一个台账不会报“schema 更新”，只是看不到候选记录。候选行是审计记录，回滚代码不需要删。
- **回滚前**：取消或以原合同重新提交所有 `base.candidate` 已设置、但尚未完成的任务；旧版本不认识候选仓库，会拿候选提交去真实项目里 clone 而失败。已经 `ACCEPTED`/`CLOSED` 的任务不受影响。
- `base.integration = "candidate-v1"` 只是 base JSON 里的一个字段，旧版本忽略它，因此依赖检查回到“上游 ACCEPTED 即可”的旧语义。
- 不删除 `candidates/` 目录：删掉行或目录都不会影响项目，但会丢失补丁哈希、基线和检查记录这些审计事实。

## 没有改动的东西（回滚时不必担心）

- 各项目的 `AGENTS.md`、`CLAUDE.md`、`.ai/STATE.md`、`.ai/tasks/`，哈希前后一致。
- `~/.claude` `~/.codex` `~/.gemini` `~/.workbuddy` `~/.config/kilo` `~/.zshrc` 等：只做过 `stat`，没打开过内容，大小与权限前后一致。
- 没有创建定时任务、LaunchAgent、全局规则或任何形式的长期记忆文件。`~/Library/LaunchAgents` 里的现有项目（含一个别的项目自己的 harness 巡检任务）原样未动。

## 回滚后验证

回滚 3、4 之后重跑施工前就有的两项检查，结果应与施工前一致：Core 的 `sync_runtime_core.py --check` 为 3 个 OK；各项目 hygiene 的旧检查项没有变化。
