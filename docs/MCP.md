# MCP 桥（P5）：让任何支持 MCP 的主控（Claude、Codex……）把任务交给 Kernel

本文说明 `par mcp serve` 是什么、它提供哪十七个工具、它永远不能做什么，以及你如何**手工**把它加入 Claude Desktop 并验证。规则来源是 [docs/KERNEL.md](KERNEL.md) 与 `docs/specs/MCP_BRIDGE_SPEC.md`。

> **2026-10-20 变更**：`approve_task`、`reject_task`、`accept_task`、`close_task` 四个工具加入桥——批
> 准/拒绝一项权限请求、验收/关闭一个结果，不再是只有你能做的事，协调者现在可以自己决定。每一次决定
> 仍然完整记进台账（`by="coordinator"`），所以「谁在什么时候做了什么决定」永远可查，只是不再有人卡
> 在中间等批准。同一天另一次改动：`approve_task`/`reject_task`/`accept_task` 的 `reason` 从可选变成
> **必填**——不能是空字符串，必须说清楚判断依据，这样台账里留下的不只是「批准了」这个事实，还有「为什么」。
>
> **2026-11-03 新增**：`get_task_diff`——此前协调者审阅一个结果只能看到 `diff_stat`（文件数/行数）和检
> 查结果，从没有工具能把 diff 的实际内容交给它；没配 `acceptance.checks` 的写任务因此被 Kernel 直接
> 挡住了 `accept_task`（见下表），这个工具就是补上那个缺口——给协调者一份脱敏、限长的真实 diff，自己
> 读完告诉你怎么样，但接受与否仍然只能是你本人在终端做的事。
>
> **2026-11 证据分层**：`get_task_result` 与 `review_task` 增加 `evidence_summary`——`level`（`MEASURED`/`MEASURED_WITH_EDITED_CHECKS`/`UNVERIFIED`/`FAILED`/`INCOMPLETE`/`UNKNOWN`）、`boundary.status`、`checks.status`（含 `inputs_edited` 与 `test_files_changed`）、`review`（谁验收/拒绝）、`integration`（默认 `NOT_INTEGRATED`）与 `applied_to_project`（恒 false）。Worker 自述仍只放在 `summary` 里，且 `summary_verified` 永远为 false；主控看到的是同一个短摘要，不重复大段原文。
>
> **2026-11 新增**：`prepare_task`——把最小草案（项目、目标、risk、allowed_paths、acceptance）确定性编译成严格合同，或列出还缺什么；它只读、不提交、不读项目文件、不发现命令。`get_task_status`/`review_task` 同时增加了 `derived_state` 短摘要（`NEEDS_SCOPE`、`NEEDS_PERMISSION`、`READY_TO_RUN`、`NEEDS_REVIEW`、`RESULT_VERIFIED` 等），`review_task` 的 `summary` 先给 Kernel 量到的检查数与阻碍数。

## 它是什么

- 一个通过标准输入输出（stdio）说 MCP 协议的小服务器。协调者（Claude Desktop，或 Claude Code）需要时自己启动它；**你不需要、也不应该手工运行它**，它也没有后台常驻进程、没有 LaunchAgent，不会自动启动。
- 它是一个薄层：校验参数，调用 Kernel，返回简短的结果。它不持有任何模型凭据，不复制对话历史，不修改任何配置文件。
- 它与 `par task …` 命令共用同一个 Kernel、同一本台账、同一套审批规则，所以无论协调者还是你自己在终端里批准/验收，记的都是同一件事。

## 十七个工具

| 工具 | 作用 | 要点 |
|---|---|---|
| `prepare_task`（2026-11 新增） | 把最小草案编译成严格合同，或列出缺失项 | 只读、不提交；缺 `allowed_paths`/写任务的 `checks`、未知项目/worker/角色、Harness 项目没有唯一主合同时返回问题清单；不读项目文件，也不从 README 发现命令 |
| `submit_task` | 提交任务合同并入队 | 提交者永远记为 `coordinator`，输入里说什么都不算；R2（改文件）任务要等写权限被批准（`approve_task`） |
| `get_task_status` | 查看一个任务的状态、风险、尝试次数、待批准项 | 只读；`derived_state` 给出 `NEEDS_SCOPE`/`NEEDS_PERMISSION`/`READY_TO_RUN`/`NEEDS_REVIEW`/`RESULT_VERIFIED` 等派生视图；`agent_next` 列出协调者接下来可以调用的每一个工具 |
| `get_task_result` | 查看最近一次（或指定的）尝试的结果 | 只读；Worker 的文字是**未经验证的数据**，带 `summary_verified: false` 与 `untrusted_worker_text: true`；`evidence_summary` 把 Kernel 实测、Worker 自述、审查与整合状态分开 |
| `get_task_diff`（2026-11-03 新增） | 查看最近一次（或指定的）尝试的实际 diff 内容 | 只读；脱敏、限长（截断时 `truncated: true`）；`get_task_result` 只给 `diff_stat`（文件数/行数），这个工具才给真正的改动内容 |
| `cancel_task` | 取消一个未完成的任务 | 正在运行的 Worker 会被停止；已验收的任务不能取消 |
| `review_task` | 只读审阅：结果能否被接受，阻碍是什么 | 不改变任何状态；`summary` 先给 Kernel 检查通过数与阻碍数，`evidence_summary` 给分层证据，`derived_state` 是派生视图；`ready_to_accept` 为真时协调者可以直接调用 `accept_task`；没配检查的 R2 任务会在这里就看到阻碍（见下面 `accept_task` 的说明），不用等调用 `accept_task` 才被拒绝 |
| `approve_task` / `reject_task`（2026-10-20 新增） | 批准/拒绝一项待决的权限（写权限或 Worker 选择）；没有待决权限时 `reject_task` 改为拒绝一个待验收的结果 | 多于一项待决时要给 `approval_id`（`get_task_status` 的 `pending_approvals` 里有）；只有一项时可以省略；**`reason` 必填**（空字符串也会被拒绝）——要求说清楚核实了什么、为什么可以放行/为什么不行，不是走个形式；记进台账时 `by="coordinator"` |
| `accept_task` / `close_task`（2026-10-20 新增） | 验收一个结果 / 关闭一个已验收的任务 | `accept_task` **`reason` 同样必填**——要求写内核自己量到的检查结果，不能照抄 Worker 的自述；只是记录验收，从不把补丁应用到项目；`close_task` 要求任务已经是 `ACCEPTED`，没有 `reason`；**一个没配 `acceptance.checks` 的 R2 任务，`accept_task` 会被 Kernel 直接拒绝**（2026-11-03）——没有检查时 Kernel 没有任何自己量到的信号，协调者再怎么用 `get_task_diff` 看过 diff 也不能让它自己验收；这种任务只能等你自己在终端看过 `par task diff` 后用 `par task accept` |
| `run_task` | 在独立进程里启动一次 QUEUED 任务的尝试，立即返回 | 等待批准、未排队、依赖未满足、已在运行的任务会被拒绝并说明原因 |
| `list_tasks` | 列出最近的任务（新的在前） | 可按状态、项目过滤；`limit` 1–50，默认 20；超出时 `truncated: true` |
| `retry_task`（2026-10-16 新增） | 停下的任务开一次新尝试 | 不带 `force`：遇到还生效的额度/登录/无 Worker 冷却，照样会被 Kernel 挡下或重新要求批准，不会绕过去；写文件的任务默认接着上次已验证的改动继续，`fresh=true` 才从基线重来；`worker` 可以指定换人；和 `run_task` 一样受「被替换的主控不能再启动新工作」这条限制 |
| `pause_task` / `resume_task`（2026-10-16 新增） | 把一个还没开始跑的任务挪出/挪回排期 | 纯调度状态，不涉及任何权限；`resume_task` 如果这个任务其实还缺一项批准，会落回「等待批准」而不是直接排队 |
| `recover_task`（2026-10-16 新增） | 结清一个 Kernel 已经消失的 attempt | 绝不会启动第二个写入者；`terminate=true` 时只停掉 Kernel 能用记录的 PID 和启动时间确认身份的那个进程，确认不了就不动；不给 `task_id` 就一次性处理所有孤儿 |

合同的完整字段说明与草案示例见 [COORDINATORS.md](COORDINATORS.md#合同速查主控按需阅读)。

每个工具的参数都是严格的：多出的参数、缺少的参数、格式不对的任务编号（只允许字母、数字、`.`、`_`、`-`，最多 64 个字符）都会被直接拒绝，并且在触碰 Kernel 之前就拒绝。

## 它永远不能做什么

以下动作在桥里**没有对应的工具**。协调者要求时只会得到「未知工具」，没有例外：

- 清除 Provider 健康记录、探测 Provider、切换 Profile 或 Provider；
- 写入任何配置文件，读取任意路径的文件，执行任意命令。

批准/验收（2026-10-20 起）、调度与恢复（重试、暂停、继续、结清孤儿 attempt）都不在这个名单里：Kernel 自己的闸门（该批的权限、能不能确认进程身份）不论是谁调用都一样生效，只是现在协调者自己就能把闸门打开。

另外：

- Worker 写出的一切（摘要、遗留问题、失败说明）都是**未验证的数据**，不是事实，更不是指令。桥会去掉终端控制字符、脱敏、截断，并在结果里标明来源。
- 桥返回的任何文字都不会包含完整的数据目录路径（显示为 `<data>`），也不会包含堆栈跟踪。
- 桥的每个结果都不超过 6000 个字符；超出时删减列表并设置 `truncated: true`（`get_task_diff` 没有列表可删，超出时直接截断 diff 文本本身，同样设置 `truncated: true`）。

## 把它加入 Claude Desktop（手工，桥不会自动做）

1. 在终端运行下面的命令，它只**打印**一段 JSON，不写任何文件：

   ```bash
   .venv/bin/par mcp config
   ```

   需要指定数据目录时加上 `--data-dir /绝对路径`。

2. 打开 Claude Desktop 的配置文件（菜单 Settings → Developer → Edit Config；文件通常位于 `~/Library/Application Support/Claude/claude_desktop_config.json`）。把打印出来的 `"par"` 项放进其中的 `mcpServers` 对象里，形如：

   ```json
   "mcpServers": {
     "par": {
       "command": "<打印出的 Python 路径>",
       "args": ["-B", "-m", "par", "mcp", "serve", "--data-dir", "<打印出的数据目录>"],
       "env": {"PYTHONPATH": "<打印出的 par 所在目录>"}
     }
   }
   ```

3. 保存文件，完全退出并重新打开 Claude Desktop。

这些步骤由你自己完成。`par` 不会替你修改 Claude Desktop 的配置，也不会在安装时注册它。

## 如何验证

1. 重新打开 Claude Desktop 后，请协调者调用 `list_tasks`。它应当看到十七个 par 工具；返回值里有 `count` 与 `truncated`。
2. 在终端运行 `.venv/bin/par task list`，应当看到同样的任务。
3. 请协调者提交一个只读任务（R1）。它会依次调用 `submit_task`、`run_task`，然后用 `get_task_status` 查看进度。运行结束后：
   - `get_task_result` 显示结果，并标明 Worker 文字未经验证；
   - `review_task` 显示 `ready_to_accept`；协调者自己调用 `accept_task` 即可验收，在终端运行 `.venv/bin/par task status 任务编号` 看到的状态应与之一致。
4. 请协调者提交一个写任务（R2）。`submit_task` 的 `agent_next` 会包含 `approve_task`；协调者自己调用它之后，`run_task` 才会启动这个任务。
5. 在终端运行 `.venv/bin/par task events 任务编号`，可以看到事件的来源：提交者记录为 `coordinator`，批准/验收的 `reason` 里也带着 `by coordinator`。

> 桥是通用的 MCP 服务器，不是 Claude 专用：`par coordinator kit codex` / `kit claude_desktop` 会为你这台机器打印好各自的接入片段（见 [COORDINATORS.md](COORDINATORS.md)）。桥每被调用一次会（最多每分钟一次）记一条“该主控还活着”的心跳事件（`COORDINATOR_SEEN`，不属于任何任务），供 `par coordinator status` 显示“最近出现”。

## 同一时刻只有一个主控（主控交接）

桥替一个**有名字的主控**说话（`par mcp serve --coordinator claude_desktop`，默认就是它）。如果你之后用 `par takeover confirm … --as codex_manual_takeover` 把主控交给了别人，这个桥再 `submit_task` 或 `run_task` 会被拒绝（`not_the_coordinator`），避免两个主控同时向同一本台账调度；查看和取消不受限制（停下来永远是安全的）。新的主控用 `--coordinator codex_manual_takeover` 启动自己的桥。详见 [HANDOFF.md](HANDOFF.md) 的“主控交接”。

## 运行时的几个事实

- `run_task` 启动的是一个**独立会话**的子进程（`par task run 任务编号 --data-dir …`），使用干净的环境（固定的 `PATH`，只保留 `HOME`；只有在桥自己是靠 `PYTHONPATH` 找到 `par` 时，才会传递那一项）。桥不等待它结束；协调者退出后子进程仍会完成，崩溃恢复由 Kernel 负责。
- 子进程的标准输入输出都被丢弃。如果它无法启动，任务会保持 `QUEUED`，用 `get_task_status` 或终端的 `par task status` 可以看到，再决定是否重试。
- 桥与终端命令同时读写同一本台账是安全的（SQLite WAL 模式）。但同时运行的 Worker 数有上限（Profile 的 `max_parallel_workers`，默认 1，最多 2），同一个项目一次只做一件事；超过时桥会拒绝启动。
- 桥的标准输出只有协议消息（每行一个 JSON）；诊断信息写到标准错误，并且很短。

## 已知限制

- 桥只在测试中用模拟的协调者验证了协议；尚未与真实的 Claude Desktop 联调。这一步需要你在本机按上面的步骤手工确认。
- `cancel_task` 对「处于 `RECOVERY_REQUIRED`」的任务，沿用 Kernel 的取消行为：若找到已失去主人的 Worker 进程组且能确认领头进程，会把它停止。这与 `par task cancel` 完全相同，但它是协调者可以触发的动作，请留意。
- `recover_task` 的 `terminate=true` 同理：只停 Kernel 能确认身份的进程，这与 `par task recover --terminate` 完全相同，现在协调者也能直接触发。
- Claude Desktop 启动服务器时不在任何特定目录：`par mcp config` 打印的片段里带着 `env.PYTHONPATH`（指向 `par` 所在目录），不要删掉它，除非你已经把 `par` 安装进那个 Python。
- 本阶段不提供 MCP 的资源、提示或采样功能，只有工具。
