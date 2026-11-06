# 使用指南

> 适用版本：`1.0.0`。没有网页控制台：批准/验收/查看全部走终端或主控。每一节都能单独看；遇到问题先翻第 7 节。

## 0. 先看这一页

**它是什么**：一个只在你这台 Mac 上运行的「任务调度中枢」。你用自然语言对一个模型（**主控**，现在可选 Claude 或 Codex）说要做什么，主控把任务交给 Runtime；Runtime 把任务派给 **Worker**（Codex、agy，或你手动操作的 WorkBuddy / TRAE），在**私有副本**里干活，再由 Runtime 自己**量**改动、跑检查；主控可以自己批准写入、验收结果（2026-10-20 起，你的决定），但**把改动应用到真实项目永远只有你能做**——Runtime 不会自己应用补丁。

```text
你 ──说话──▶ 主控（Claude 或 Codex）
                │  17 个工具：准备合同 / 提交 / 查询 / 查看结果 / 查看 diff / 启动 / 取消 / 重试 / 暂停 / 继续 / 结清孤儿 / 批准 / 拒绝 / 验收 / 关闭
                ▼
       Runtime（Kernel + 任务台账） ◀── 你：看进度、随时能在终端接手、交接、切换主控
                │  在私有副本里调度
                ▼
  Worker：Codex（自动）· agy（自动）· WorkBuddy / TRAE（人工交接）
```

**为什么这样设计**：中枢是 Runtime，不是任何一个聊天。主控额度用完时，任务、批准、证据一样都在，换一个主控接着干就行。

**谁能做什么**

| | 主控 | 你 | Worker |
|---|---|---|---|
| 提交、查看、取消、启动任务 | ✓ | ✓ | |
| 批准写入权限、验收结果（2026-10-20 起） | ✓（记着是它决定的） | ✓ | |
| 改文件 | | | 只在私有副本里 |
| 把改动应用到真实项目 | ✗ | **只有你**：Runtime 永远不会自己应用补丁；验收后你自己 `git apply`（第 3A 节） | ✗ |

**你每天做的事**：① 对主控说清要做什么；② 看它做的决定和结果（批准/验收现在它自己能做，台账里始终查得到是谁决定的）；③ 需要时随时在终端自己接手（`par task status/approve/accept …`）。

## 1. 一眼看懂你自己的状态

装好之后，用这几个命令就能看清当前状态，不用猜：

| 想知道什么 | 命令 |
|---|---|
| 哪个主控已经接入、最近谁连上过 | `par coordinator status` |
| 配了哪些 Worker、策略是什么 | `par health` |
| 已登记哪些项目、各自能不能写 | `par project list` |
| 有没有任务在等你处理 | `par task list --waiting` |

**刚装好时的典型样子**：所有真实项目默认只读；只有你自己用 `par project enable-write ID --confirm ID` 明确打开写入的项目才能被提交写任务（见第 3A 节的例子）；夹具项目（`par fixture create` 生成）随时可以写，用来练手最安全。

**你可以放心做的**：让主控对已登记的真实项目做**只读分析**（风险 R1）；在夹具项目或你自己打开了写入的项目上做**改文件**（风险 R2）并走完批准、验收、自己应用补丁；用人工交接把任务交给 WorkBuddy / TRAE。

> 下面所有 `par` 指你的 clone 目录下的 `.venv/bin/par`（下文用 `personal-agent-runtime/` 代指 clone 目录）。每次写全路径很烦，可以把这一行加进你的 shell 配置（`~/.zshrc` 或等效文件），把路径换成你的实际位置：
> `alias par='/path/to/personal-agent-runtime/.venv/bin/par'`

## 2. 第一次接通主控

主控怎么连 Runtime：它启动一个叫 `par` 的本地 MCP 服务器（`par mcp serve`），通过它的 17 个工具跟 Runtime 说话。你要做的只是把这个服务器登记进主控应用，并给主控一段**起手提示词**。下面是 Codex 和 Claude Desktop 两条路径；接完之后可以用 `par coordinator status` 确认它「最近出现」过。

### 2.1 Codex

1. 完全退出并重新打开 Codex（ChatGPT 应用里的 Codex，或终端里的 `codex`）。检查：`codex mcp list` 里有 `par`。
   （如果你通过 ChatGPT 桌面应用安装 Codex，可执行文件在应用包内，例如 macOS 上：`/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`。先运行 `par doctor` / `par health --probe` 确认你配置的那个路径；不要假定 PATH 上的 `codex` 一定是同一个程序。）
2. 新开一个 Codex 会话，第一条消息贴起手提示词：`par coordinator prompt codex | pbcopy`（复制到剪贴板），再粘贴。
3. 让它「调用 par 的 list_tasks 看一下」。然后在终端运行 `par coordinator status`：Codex 的「最近出现」应是刚刚。

### 2.2 Claude Desktop（要退出一次应用，其余它自己做）

Claude Desktop 运行时会把自己内存里的配置写回文件，运行期间对配置的改动会被悄悄抹掉，所以必须在它**退出之后**写入。随仓库附带的小工具把这件事做成了「你只需要 ⌘Q 一次」：

1. 在终端里运行（Claude Desktop 里自带的那个终端也行，工具会脱离终端在后台等）：
   ```bash
   cd /path/to/personal-agent-runtime
   .venv/bin/python ops/connect_claude_desktop.py --apply --background --reopen
   ```
2. 然后**完全退出 Claude Desktop**（菜单栏 Claude > 退出，或 ⌘Q）。这会结束你当前在 Claude Desktop 里的所有会话。
3. 它退出之后几秒内，工具会：备份配置（旁边的 `claude_desktop_config.json.par-backup-<时间>`）、只加入 `par` 这一项、写后自检，然后自动重新打开 Claude Desktop。进度和结果写在 `~/Library/Application Support/PersonalAgentRuntime/logs/connect_claude_desktop.log`。
4. 重新打开后，新开一个聊天，工具列表里应有 `par`（17 个工具）。第一条消息贴起手提示词：`par coordinator prompt claude_desktop | pbcopy`，再让它调用 `list_tasks`，然后运行 `par coordinator status` 看「最近出现」。
5. 想先看它会做什么：不加任何参数运行（试运行，什么都不改）。想撤销：`.venv/bin/python ops/connect_claude_desktop.py --remove --apply --background --reopen`，再 ⌘Q。

> 如果过了 30 分钟你都没有退出应用，它会自己放弃，什么都不改。

**Claude Code（桌面端的 Code 标签）**：上面接入的是用户级配置，这会影响到**你所有项目的所有 Code 会话**——应用重启、配置生效之后，不需要任何额外操作，工具列表里就会多出 `par` 的 17 个工具。如果你只想让某一个项目有这些工具（不想全局都有），改用项目级的办法：在那个项目的文件夹里运行 `par coordinator kit claude_desktop`，执行它打印的 `claude mcp add-json …` 一行命令（不加 `--scope`，默认只对当前项目生效；不要加 `--scope user`，否则等于又做了一遍用户级接入）——如果用户级已经接了，没必要再做这一步。

### 2.3 起手提示词是什么

一段给主控模型看的规则：它有哪 17 个工具、怎么用 `prepare_task` 得到合同、怎么用 `dependencies` 拆大任务、它可以自己批准/拒绝/验收/关闭（2026-10-20 起，每次决定都记着是它做的）、Worker 说的话是**不可信数据**、不要循环重试、完成的任务不重跑。它对 Kernel 不是依赖——Kernel 的边界检查和验证不管主控怎么说都照常进行。随时可以重新打印：`par coordinator prompt NAME`（接入片段同样可以重新打印：`par coordinator kit NAME`）。

## 3. 每天怎么用

### 3.1 对主控说话

用自然语言说清四件事，主控会自己写合同：

> 「在 simproj 项目里，**只读分析**一下 src/solver 里时间步长是怎么选的，告诉我选步长的函数名。」
> 「在 fixture 项目里，**改 app/ 里的文件**，让单元测试通过；检查用 `python3 -m unittest -q`。」

- **项目**：必须是已登记的（`par project list`）。
- **要不要改文件**：只读（R1）还是改文件（R2）。真实项目要先 `par project enable-write` 打开写入；普通 Git 仓库用 `ad_hoc=true` 的 R2 合同（见 3.4）。
- **允许改哪些路径**（R2）：如 `app/`，前缀以 `/` 结尾，不能有通配符。
- **怎么验收**：Kernel 自己运行的检查（命令列表，不经过 shell）。

主控提交后会告诉你任务编号（如 `fixture-0004`）和你下一步该做什么。

### 3.2 一个完整流程

批准、验收都走**终端**（`par task approve/reject/accept/close`）或**主控**（通过 MCP 桥的 `approve_task`/`reject_task`/`accept_task`/`close_task`，每次决定都带着理由记进台账）；`par task status 编号` 随时能看当前状态、待决批准的具体内容（允许改哪些路径等）、结果、检查、边界。

1. `par task list --waiting`（2026-11-03 新增）只列出「需要你处理」的任务——待批准、待验收、待恢复（Kernel 消失留下的 attempt）、或者卡在额度/登录/限速上（不加 `--waiting` 看全部任务）：
   - 改文件的任务先停在「**待批准**」——`par task status 编号` 能看清楚允许改哪些路径，批准：`par task approve 编号`（拒绝：`par task reject 编号`），或者交给主控处理。
2. 任务排队后，由主控或你自己启动：`par task run 编号`。**真实的 Worker 会调用厂商模型、消耗你的额度**。
3. 运行中 → 验证中（Runtime 自己量改动、跑检查）。
4. 变成「**待验收**」：`par task result 编号` 看结果——
   - **改动**：几个文件、+几行 −几行（Runtime 量的）；
   - **检查**：每条检查通过还是未通过（Runtime 自己跑的）；
   - **边界**：有没有越出允许路径；
   - **Worker 自述（未经核实）**：Worker 自己写的摘要，只当参考；
   - 需要细看补丁：`par task diff 编号`。
5. 满意：`par task accept 编号`；不满意：`par task reject 编号`（任务转为「失败」，补丁留作证据）。
6. 验收之后，结果仍然只是一份**补丁**：Runtime **不会**、也**没有功能**去把它应用到项目。要放进项目，由你自己应用——先 `par task diff 编号` 看一眼，再用 `git apply`（完整命令见 3A 第 5 步）。

### 3.3 几个例子

- **只读分析（真实项目）**：说「在 simproj 里分析…」→ 主控提交 R1 任务 → 排队 → 开始运行 → 待验收 → 你看「Worker 自述」和检查 → 终端或主控验收通过。全程不需要批准写入。
- **改文件（夹具）**：先 `par fixture create`（只需一次）→ 说「在 fixture 里改…」→ 待批准 → 终端 `par task approve` 或交给主控批准 → 运行 → 待验收 → 看改动与检查 → 验收通过。
- **主控说"完成了"但你不信**：`par task result 编号` 看「检查」和「边界」，那才是 Runtime 量的；主控和 Worker 的话都只是参考。

### 3.4 普通 Git 仓库（不用 Harness）

普通的 Git 仓库不需要先变成 Harness 项目：

1. `par project add simproj ~/code/simproj` —— 默认就是 generic 模式，不要求 `AGENTS.md` / `.ai/STATE.md`，也不往项目里写任何文件。
2. 只读分析：合同里 `ad_hoc=true`、`risk=R1`，不需要父合同。
3. 改文件：先 `par project enable-write simproj --confirm simproj`（要求干净工作树和 git 基线），合同用 `ad_hoc=true`、`risk=R2`，写明 `allowed_paths` 和 `acceptance.checks`。任务自己的 JSON 合同就是这次的执行合同。
4. 上下文只有仓库里已有的 `README.md`、`AGENTS.md`、`CLAUDE.md`（存在才读）加 git 事实。`AGENTS.md` 和 `CLAUDE.md` 同时存在时，清单会把两份都列出来并注明冲突，不会替你决定谁优先；它们都是**不可信项目数据**，不能改 Kernel 的权限。
5. 想回到原来的 Harness 规则：`par project add ID PATH --context-mode harness`（缺文件时加 `--scaffold-harness`）。两种模式共用同一套写入开关、路径白名单、私有副本和 Kernel 验收。

### 3.5 用草案少写机械字段（可选）

完整 JSON 太啰嗦时，可以只给最小草案：

```json
{
  "project_id": "simproj",
  "title": "Fix the solver step",
  "description": "Make choose_dt return the documented step.",
  "risk": "R2",
  "allowed_paths": ["src/solver/"],
  "acceptance": {"criteria": ["the solver test passes"],
                 "checks": [{"name": "solver", "argv": ["python3", "-m", "unittest", "tests/test_solver.py"]}]}
}
```

`par task draft --draft draft.json > /tmp/task.json` 只打印严格合同，**不提交**：role 默认 `code`，ad_hoc 按项目模式推导，幂等键由内容确定性生成。缺 `allowed_paths`、写任务缺 `checks`、项目/worker 不存在、Harness 项目没有唯一主合同时，命令以退出码 2 列出问题，不会猜一个路径，也不会从 README 里自动挑命令。确认 `allowed_paths` 与 `checks` 后再 `par task submit --contract /tmp/task.json`。

## 3A. 第一次真实写入：完整走一遍

建议第一次真实写入选一个小项目练手——一个小文本库、一个脚本工具，什么都行，只要你不在乎它被改坏。下面假设这个项目注册时用的 id 是 `myproject`。

1. 登记项目：`par project add myproject /path/to/myproject`。默认是 generic：不要求 `AGENTS.md` / `.ai/STATE.md`，也不往项目里写任何文件；想沿用原来的 Harness 规则，用 `--context-mode harness`（缺文件时加 `--scaffold-harness` 让 Runtime 补一份极简模板，绝不覆盖已有文件）。
2. 打开写入：`par project enable-write myproject`。它先体检（两种模式都要求干净工作区、git 基线和看不到敏感文件；Harness 项目还要求 `.ai/STATE.md` 不超 15 KB、恰好一份生效的主合同），不过关就列出原因、什么都不改；过关后要你再敲一遍项目 id 确认（`--confirm myproject`）。`par project show myproject` 能看到 `Writes ALLOWED`。
3. 对主控说清要做什么（见第 3.1 节），或自己写合同提交。假设提交后任务编号是 `myproject-0001`，它会停在「待批准」。
4. `par task status myproject-0001` 看「待决批准」——它写明只允许改哪些路径，改动只发生在私有副本里。批准：`par task approve myproject-0001`。
5. `par task run myproject-0001`。**这一步会调用真实的 Worker，消耗对应厂商的额度**（大约一次调用）。
6. 变成「待验收」后看「结果」：`par task result myproject-0001`——改了几个文件、内核自己跑的检查是否通过、有没有越界。满意就在终端 `par task accept myproject-0001`。
7. 验收只是记录；**补丁不会自动应用**。要把它真正放进项目，由你自己来：
   ```bash
   par task diff myproject-0001 > /tmp/myproject.patch                 # 先看一眼内容
   cd /path/to/myproject && git apply --check /tmp/myproject.patch && git apply /tmp/myproject.patch
   # 跑一下你项目自己的测试，确认没问题，再 git add -A && git commit
   ```
8. 想关掉写入：`par project disable-write myproject`。

**项目打不开写入时常见的原因**（`par project enable-write` 的体检会逐条列出）：工作区有未提交的改动；`.ai/STATE.md` 超过 15 KB 上限；没有生效的主合同；不是 git 仓库。整理好对应的那一项，重新跑一遍 `enable-write` 就行。

## 4. 读懂任务状态

没有网页界面：一切都用 `par task …` 看，或交给主控（它用的是同一套 MCP 工具）。

### 4.1 `par task list`

按状态过滤（`--status`）：需要你处理（待批准、待验收、需要恢复、已暂停、没有可用 Worker、需要登录、额度用完、被限速）、进行中与排队中、已完成、已取消。`--waiting`（2026-11-03 新增）直接只列出「需要你决定才能往下走」的那些——待批准、待验收、待恢复、卡在额度/登录/限速/没有可用 Worker 上；已暂停不算在内（暂停是你自己选的，不是卡住了）。`par health` 看每个 Worker 现在**读 / 写**是否可用、不可用的原因；`par health --probe` 重新检测（只问命令在不在、版本、是否登录，**不调用模型、不耗额度**）。

### 4.2 `par task status 编号`

标题与状态、一句话说明、**待决批准**的具体内容（允许改哪些路径等——批准/拒绝/验收/关闭从 2026-10-21 起只能在终端或由主控完成，不是看出来的）、现在能做的下一步。`par task result 编号` 看结果，`par task diff 编号` 看补丁，`par task events 编号` 看时间线。

### 4.3 `par coordinator status`

谁在主控、各候选最近一次连上的时间；`par coordinator kit NAME` 看接入方法（步骤、可复制的片段与起手提示词）；`par coordinator use NAME` 切换为主控。

### 4.4 状态词汇表

「你能做什么」列出对应的命令；批准/拒绝/验收/关闭从 2026-10-21 起固定走终端 `par task approve/reject/accept/close`，或交给主控：

| 状态 | 意思 | 你能做什么（命令） | 批准/验收走哪 |
|---|---|---|---|
| 排队中 | 可以运行了 | `run` · `pause` · `cancel` · `handoff` | — |
| 待批准 | 要批准写入权限等 | `cancel` | 终端或主控：approve / reject |
| 启动中 / 运行中 / 验证中 | 一次尝试正在进行 | `cancel` | — |
| 待验收 | 结果干净，等着被验收 | `cancel` | 终端或主控：accept / reject |
| 已验收 → 已关闭 | 结果已被验收 → 归档 | — | 终端或主控：close |
| 失败 | 这次尝试没成（进程失败、越界、检查没过…） | `retry` · `cancel` · `handoff` |
| 已中断 | 超时或无法归类，现场保留 | `retry` · `pause` · `cancel` |
| 额度用完 / 需要登录 / 被限速 | 厂商明确返回的原因 | 修好后 `retry` · `pause` · `cancel` |
| 没有可用 Worker | 没有能做这件事的 Worker | 修好后 `retry` · `pause` · `cancel` |
| 已暂停 | 你或失败策略暂停了它 | `resume` · `retry` · `cancel` |
| 需要恢复 | 上一次运行没有正常收尾 | `recover` · `retry` · `cancel` |
| 取消中 → 已取消 | 正在停 → 已停 | `retry`（开一次新的尝试） |

「重试」永远是**新的一次尝试**：写文件的任务默认**接着上一次已验证的改动继续**；要从头来，`par task retry 编号 --fresh`。

## 5. 额度用完时怎么办

**主控额度用完（比如 Claude 的聊天停了）**：Runtime 和任务都不受影响。
1. 终端 `par coordinator use codex`。它会写一份**接管包**（任务树和 Runtime 量到的事实，不含任何对话）并记录切换。
2. 在 Codex 里贴起手提示词，再把接管包的 `takeover.md` 路径告诉它读（`use` 会打印路径）。
3. 之后被换下的主控再提交或启动任务会被拒绝（`not_the_coordinator`）；你自己的命令永远不受限。切回来：`par coordinator use claude_desktop`。

**Worker 额度用完（任务显示「额度用完」）**：看厂商给的恢复时间（`par health` 里有 `until …`）。选择：等；配好 agy 后改走 agy；或「交给其他应用」做人工交接（第 6 节）。

## 6. 人工交接：WorkBuddy / TRAE

这类应用没有可用的程序接口，所以由你搬运，但结果仍然由 Runtime 量、查。同一套流程也适用于其他没有脚本化接口的 IDE/CLI 工具——Cursor、Windsurf、Cline、Aider 等（见 [HANDOFF.md](HANDOFF.md)）：

1. 任务在排队、已暂停或已停下时：`par task handoff 编号 --to workbuddy`（`--to` 换成 `trae`/`cursor`/`windsurf`/`cline`/`aider` 或其他应用名，默认 `manual`）。Runtime 会写出一个文件夹：`PROMPT.md`（提示词）、`README.md`（中文步骤）、`manifest.json`（基线、允许路径、提示词哈希）、`workspace/`（项目的私有副本）。命令会打印**模式**（`workspace` 会改文件，只收文件夹或补丁；`text` 只收文字答案）、完整路径和接下来每一步；`--json` 里还有 `workspace_absolute_path`、`prompt_path`、`manifest_path`、`base_commit`、`allowed_paths` 和 `next_actions`。排队中的任务会先被暂停，避免别的 Worker 抢着做。
2. 在应用里专门打开这个新文件夹，不要在你已经开着的、那个项目本来的会话/空间里继续——哪怕看着是同一个项目，那边对着的是真实项目，Runtime 完全看不到也验证不了。具体怎么点，交接包自己的 `README.md` 里写了确切菜单路径（WorkBuddy 那段按 2026-10 的界面记录，版本更新后可能改名；认准「打开本地文件夹」这个动作）。
3. 做完：`par task import 编号 --from 那个 workspace 文件夹路径`。Runtime 会按当时的样子取走那个文件夹，和项目基线比对、强制允许的路径、自己跑检查；应用自己的说法一概不用。通过后进入「待验收」，和普通 Worker 的结果一样处理。
   - 只读分析（`risk=R1`）是 `text` 模式：仍然给一份**只读副本**让它读，但只收文字答案（`--text 答案文件`），文件改动不会被采用；R0 文字任务连副本都没有。
   - `--from` 必须是 Runtime 导出的那个文件夹本身：复制到别处的副本、更早的导出包、标记里的 task/export/base 与台账或包内 `manifest.json` 对不上，都会被**点名拒绝**，不会悄悄接收。已经有一个更新的导出包时，旧包会被判为陈旧；确实是在旧包里做的，可以把改动做成补丁用 `--patch` 交回。
   - Runtime 不监视文件夹、也不猜应用是否结束：只有你运行 `par task import` 时才读取结果。
4. **隐私提醒**：这些应用会把你打开的文件发给它们自己的云端模型。每个交接包的 README 都写了这一点，由你决定这个项目能不能交给那家厂商。

## 7. 出问题怎么办

| 现象 | 怎么办 |
|---|---|
| `par coordinator status` 里某个候选一直「从未出现」 | 应用没有完全重启；或配置没真正写进去（Claude Desktop 运行时会抹掉改动，见 2.2）；检查 Codex：`codex mcp list` 里有没有 `par` |
| 主控应用里看不到 par 的工具 | 同上；新开一个聊天；片段里的 Python 路径必须真实存在 |
| 想手动试桥有没有问题 | 在终端运行片段里那条命令（`… -m par mcp serve --data-dir … --coordinator codex`）：它应当**安静地等待输入**（Ctrl-D 退出）；一启动就报错说明路径或数据目录不对 |
| `par task run 编号` 说「现在不能开始运行：已有 N 个任务在运行」或「这个项目里正在运行 …」 | 同时在跑的任务数到了上限（见第 8 节的 `max_parallel_workers`），或这个项目里已经有一个任务在跑。等它们结束 |
| 任务「没有可用 Worker」 | `par health`（加 `--probe` 重新检测）和 `par task status 编号` 会逐条写原因（没启用、没适配器、模型是占位符、冷却中…）；登录问题去对应应用登录 |
| 任务「需要登录」 | 去 Codex / agy 对应的应用里登录；然后重试 |
| 任务「额度用完 / 被限速」 | 见第 5 节 |
| 任务「需要恢复」 | 上一次运行没有正常收尾。先 `par task status 编号` 看原因，再 `par task recover 编号`；Worker 进程还在跑时用 `par task recover 编号 --terminate`（它只终止能核实的进程） |
| 操作被拒绝，说任务被改动过（`version_conflict`） | 主控或别的命令刚改过这个任务；`par task status 编号` 看一眼最新状态再操作 |
| `par` 命令找不到 | 用全路径 `<你的 clone 目录>/.venv/bin/par`，或加 1 节里的 alias |
| 不知道出了什么事 | `par doctor`（只读自检）、`par health`、`par task status 任务编号`（会写原因和你能做的下一步） |

## 8. 想改配置的时候

配置文件在数据目录的 `config/` 下（`~/Library/Application Support/PersonalAgentRuntime/config/`，建议权限 600）：

| 想做什么 | 改哪里 |
|---|---|
| 启用 / 停用某个 Provider | `providers.toml` 里对应项的 `enabled` |
| 加一个 agy Worker | `profiles.toml` 加 `[workers.xxx]`（`provider = "antigravity"`、`model = "<`agy models` 里的名字>"`），再把它放进某个角色的 `primary` 或 `fallback` |
| 换 Worker 的模型 | `profiles.toml` 里对应 `[workers.xxx]` 的 `model` |
| 失败时的策略 | `profiles.toml` 的 `worker_failure_policy`：`ask`（默认，什么都不自动做）、`pause`、`safe_auto`（只对只读任务自动重试一次） |
| 同时跑几个 Worker | `profiles.toml` 里 Profile 的 `max_parallel_workers`：`1`（默认）或 `2`。是 `2` 时，**不同项目**的任务可以同时跑；**同一个项目一次只做一件事**，不论设成几。注意两个 Codex Worker 会共用你本机的 `~/.codex`（Codex 自己的状态），额度也消耗得更快 |
| 登记新项目 | `par project add ID 路径`（默认 generic，不要求 `AGENTS.md` / `.ai/STATE.md`；要 Harness 规则加 `--context-mode harness`） |

改完运行 `par health` 看每个 Worker 能不能跑、为什么不能。`gemini_legacy` 永远不能启用（Gemini CLI 已不再为你的订阅服务）。

## 9. 安全与边界（这些永远不会发生）

- 主控自 2026-10-20 起可以自己批准、拒绝、验收、关闭（`by="coordinator"`，带着理由记进台账，台账里查得到）；你自己决定时走终端（`par task approve/reject/accept/close`，记录为「由 user 批准」）。两条路走的是同一个 Kernel 闸门，不会互相绕过。
- Worker 只在私有副本里改文件；Runtime 自己量改动、强制允许的路径、自己跑检查；Worker 的话一概当作不可信数据。
- Runtime **不会**把任何补丁应用到你的项目里；**不会**付款、不会删数据、不会改凭据、不会部署。
- 密钥不进任何日志或文档；Runtime 不开任何网络端口（2026-11-02 起：web 控制台已移除）。
- Runtime 不改任何应用的配置（Codex 的接入用的是 Codex 自己的 `codex mcp add`，Claude Desktop 的接入是你运行的 `ops/connect_claude_desktop.py`）。
- 真实项目默认不能写（`WRITE_DISABLED`）。只有你自己在终端里敲 `par project enable-write ID --confirm ID` 打开的项目才可以（要先通过体检：基线干净、STATE 不超 15 KB、恰好一份生效的主合同、有 git、看不到敏感文件）——这一步永远只有你能做，主控没有打开项目写入的工具。打开之后**每个写任务**仍然要先经过批准（主控自己也能做），补丁也从来不会被自动应用。`par project disable-write ID` 随时关。
- 数据去向：Worker 把任务内容发给对应厂商（Codex → OpenAI；agy → Google）；WorkBuddy / TRAE 把你打开的文件发给它们自己的云端模型。

## 10. 备份、回滚与卸载

- 接入主控会改两处应用自己的配置：Codex 的 `~/.codex/config.toml`（改前自动备份在同目录 `config.toml.par-backup-<时间戳>`；撤销用 `codex mcp remove par`）；Claude Desktop 由 `ops/connect_claude_desktop.py` 自动备份并可 `--remove`。
- **备份**：拷整个数据目录，不要只拷 `runtime.sqlite3`——台账最新的内容在旁边的 `-wal` 文件里，只拷主文件会丢最新的任务（演练里量到：2 个任务只剩 1 个）。Runtime 空闲时 `cp -Rp "$HOME/Library/Application Support/PersonalAgentRuntime" "$HOME/par-backup-$(date +%Y%m%d)"`；恢复和更细的做法见 [ROLLBACK.md](ROLLBACK.md)「备份与恢复」。想不动你的数据先演练一遍：`.venv/bin/python -B tests/tools/restore_drill.py --rehearse-switch`（在副本上核对并演练主控切换，结束即删副本）。
- Runtime 的代码回滚、数据目录里新增的东西，见 [ROLLBACK.md](ROLLBACK.md)。
- 卸载：删除项目目录与 `.venv`，并用上面的办法去掉两处应用配置里的 `par`。不影响任何 Harness 文件。

## 11. 命令速查

| 想做什么 | 命令 |
|---|---|
| 谁在主控、最近谁连上过 | `par coordinator status` |
| 切换主控 | `par coordinator use codex` / `par coordinator use claude_desktop` |
| 看接入方法 / 起手提示词 | `par coordinator kit NAME` / `par coordinator prompt NAME` |
| 看所有任务 / 某个任务 | `par task list` / `par task status 编号` |
| 看结果 / 改动 | `par task result 编号` / `par task diff 编号` |
| 在终端里批准、验收 | `par task approve 编号` · `par task accept 编号` |
| Worker 现在能不能跑 | `par health`（加 `--probe` 重新检测，不调模型） |
| 自检 | `par doctor` · `par harness check` |
| 已登记的项目 | `par project list` |
| 夹具项目（演练改文件） | `par fixture create` |

## 12. 文件在哪

| 东西 | 位置 |
|---|---|
| 代码 | 你的 clone 目录（下文用 `personal-agent-runtime/` 代指） |
| 数据目录 | `~/Library/Application Support/PersonalAgentRuntime/`（`PAR_DATA_DIR` 或 `--data-dir` 可改） |
| 配置 | 数据目录下 `config/`（`profiles.toml` `providers.toml` `projects.toml`） |
| 任务台账 | 数据目录下 `runtime.sqlite3`（只追加；不要手动改） |
| 每个任务的私有副本、结果、补丁、交接包 | 数据目录下 `tasks/` |
| 接管包 | 数据目录下 `takeover/` |
| 更多细节 | [OPERATIONS.md](OPERATIONS.md) · [COORDINATORS.md](COORDINATORS.md) · [HANDOFF.md](HANDOFF.md) · [KERNEL.md](KERNEL.md) · [SECURITY.md](SECURITY.md) |

## 13. 日常运维速查

| 想做什么 | 怎么做 |
|---|---|
| 重启 | 主控应用：完全退出再打开，新开一个聊天。没有常驻进程，也没有 LaunchAgent；MCP 桥由 Claude / Codex 在需要时自己启动，应用退出它就退出 |
| 升级 | `cd /path/to/personal-agent-runtime && git pull && uv pip install -e .`（没有 uv 就 `.venv/bin/pip install -e .`），再运行 `par version`、`par doctor`、`par health --probe`。升级 Codex 或 agy 之后也要 `par health --probe`（版本变了会提示）。台账只会向前加东西，旧数据照常能读 |
| 重新登录 | Codex：在 ChatGPT / Codex 应用里登录（订阅登录，不要用 API key）。agy：`par provider login antigravity`（在 Runtime 自己的目录里登录一次，不碰 `~/.gemini`）。之后运行 `par health --probe` |
| 额度、恢复任务 | 第 5 节、第 7 节 |
| 审计记录在哪 | 台账 `runtime.sqlite3` 里每个动作都有事件（谁、何时、为什么）；`logs/project_writes.jsonl`（写入开关）、`logs/connect_claude_desktop.log`（接入小工具）。这些只追加，重启不丢，全部脱敏 |
| 数据会长到多大、怎么清 | **Runtime 从不自己删东西。** 会增长的只有每个任务的 `tasks/<编号>/attempts/<n>/` 里的 `workspace/`（项目的私有副本，项目越大越大）、`home/`（agy 的私有 HOME，每次十几 MB）和 `tmp/`；台账、`logs/*.jsonl`、`result.json`、`work.patch`、检查日志都很小（日志有大小上限）。要腾空间：只对**已验收并已把补丁应用进项目、或已取消**的任务，删掉它们的 `workspace/` `home/` `tmp/` 三个文件夹——清完之后几十 MB 能降到几十 KB。`par task status / result / diff / events` 照常工作。**不要删** `result.json` `work.patch` `changes.json` 和日志，它们是证据 |
| 导出 | 整个数据目录的备份与恢复见 [ROLLBACK.md](ROLLBACK.md)「备份与恢复」（要拷整个文件夹，不能只拷 `runtime.sqlite3`）。单个任务：`par task result 编号`、`par task diff 编号 > 补丁文件` |
| 删除运行数据 | 先改名保留：`mv "$HOME/Library/Application Support/PersonalAgentRuntime" "$HOME/Library/Application Support/PersonalAgentRuntime.removed"`，确认不需要了再删。不影响任何项目和 Harness 文件 |
| 完整卸载 | ① `codex mcp remove par`（或用 `config.toml.par-backup-…` 还原）；② Claude Desktop：`.venv/bin/python ops/connect_claude_desktop.py --remove --apply --background --reopen`，再 ⌘Q；③ 数据目录与项目目录按 [ROLLBACK.md](ROLLBACK.md)；④ 任何你为试运行专门建的项目，留不留自己定。各项目的 Harness 文件（`AGENTS.md`、`.ai/`）从来没被 Runtime 改过 |
