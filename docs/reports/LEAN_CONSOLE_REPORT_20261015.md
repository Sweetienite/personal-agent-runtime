# 阶段报告：主控接入 · 控制台中文轻量版 · 使用指南（2026-10-15）

> 本报告描述的网页控制台已在之后的版本里整体移除（见 [CHANGELOG.md](../../CHANGELOG.md)），`specs/LEAN_CONSOLE_SPEC.md` 等相关 spec 文件随之一起删除，下面的链接不再可用；报告正文保留作历史记录，未改动。

> 这是你说「全部批准」之后的第一批工作：① 把 Codex、Claude 接成主控；② 控制台做成中文轻量版；③ 出使用指南。并发 2、P8（真实项目试运行）、路线 B 你也批准了，但它们都需要你先给出细节，所以**没有动**（见「需要你决定」）。

## 阶段
路线 A 的落地（接入）+ P7 控制台的重做（中文、轻量）+ 使用指南。

## 结果
| 项 | 结果 | 一句话 |
|---|---|---|
| Codex 接成主控 | **PASS** | 已写入 `~/.codex/config.toml`（Codex 自己的 `codex mcp add`）；真实 Codex 能列出它；桥按这份配置原样启动并握手通过，真实台账字节不变 |
| Claude Desktop 接成主控 | **PARTIAL（要你动一次手）** | 我写进去的配置，几个小时内被 Claude Desktop 自己写回时抹掉了——它运行时会把内存里的配置覆盖回文件。所以改成：你退出应用后运行一条命令（`ops/connect_claude_desktop.py`，自动备份、只动 `par` 一项、写后自检） |
| 控制台中文轻量版 | **PASS** | 三页、全中文、只显示现在能做的操作；后端同时删掉了不再用的写接口；全量测试通过；真实浏览器逐页走查过 |
| 使用指南 | **PASS** | [docs/USER_GUIDE.md](../USER_GUIDE.md) |
| 并发 1→2 | **PASS（本报告之后追加）** | 见 CHANGELOG：容量由 Profile 决定（1 或 2），同一项目一次只做一件事，T17/T18 已测试 |
| P8 / 路线 B | **P8 被项目自身的三件事挡住；路线 B 不动** | 见下一份说明 |

## 做成了什么
**1. 主控接入**
- **Codex**：`codex mcp add par …` 已执行，改前备份了 `config.toml`（`config.toml.par-backup-20261015-161555`）。验证：`codex mcp list/get` 列出启用的 `par`；再按它的条目原样启动桥（工作目录 `/`、最小环境）做 MCP 握手：恰好 7 个工具、退出码 0，且你**真实**数据目录的台账文件哈希、修改时间都没变。
- **用项目自己的 Python**：片段里的解释器从「下载」文件夹里的 conda 环境（`~/downloads/enter`）改成项目 `.venv`（uv 管理的 Python 3.12）——那个文件夹一清理，桥就会悄悄坏掉。
- **Claude Desktop 的发现**：我按同样方式改了它的配置，之后发现文件被应用自己改回了原样。这条经验已写进指南与 [COORDINATORS.md](../COORDINATORS.md)。解决办法是小工具 `ops/connect_claude_desktop.py`：默认只是试运行；`--apply` 要求应用已退出；备份到旁边（`*.par-backup-<时间>`）；只动 `mcpServers.par`；原子写入、保持文件权限；写后自检，不对就用备份恢复；`--remove --apply` 可撤销。已对你的**真实**配置做过试运行（没有写入）：它的格式能无损重写。
- **Claude Code** 没有接成用户级：那会让你所有项目的所有会话都多出 7 个工具（与「轻量」相悖）；指南里写了按项目接入的一行命令。

**2. 控制台中文轻量版**（设计依据 [specs/LEAN_CONSOLE_SPEC.md](../specs/LEAN_CONSOLE_SPEC.md)）
- **三页**：工作台 / 任务 / 主控。
- **删掉的**（代码、样式、测试一起删）：模型配置页（Provider 表、Worker 表、角色表、Profile 切换、启停 Provider）、系统健康页、主题切换（颜色跟随系统）、新建任务表单、任务页的选项面板和 12 个按钮的网格、21 项的状态下拉、永久显示的版本/基线/项目锁等行。
- **工作台**：当前主控 +「切换…」、各状态任务数（只显示不为零的）、**需要你处理**（每行写明该做什么）、进行中与排队中、最近失败（≤5，没有就不显示）、**Worker**（读/写是否可用 + 中文原因 + 「重新检测」）。
- **任务页**：按组过滤 + 搜索；详情里**待你批准**（逐项）、**结果**（改动、检查、边界、Worker 自述〔标明未经核实〕、遗留问题）、**操作**（只显示现在能做的；选项都在各自的确认框里：原因、备注、「从基线重新开始」、「同时终止进程」、应用名）；补丁、尝试记录、时间线、技术信息折叠。
- **主控页**：谁在主控、最近出现、接入方法（步骤、可复制的片段与起手提示词）、切换（先确认）、切换记录。
- **只读模式**只在页顶提示一次，写按钮禁用；刷新 5 秒。Kernel 记录的英文原因（Worker 为什么不可用）已译成中文并保留原文作为悬停提示。
- **后端同步瘦身**：删除 `config_edit.py` 与 `/providers` `/profiles` `/projects` `/system/persistence` `/profiles/activate` `/providers/<id>/enabled` `/health/clear`、`POST /tasks`，`retry` 不再接受 `worker`/`force`（命令行仍可用）。**控制台现在不能写任何配置文件**，能写的只有任务动作、接管包（切换主控）和审计文件。
- **Claude/Codex 接入步骤**（控制台与 `par coordinator kit` 共用）已改成中文。

**3. 使用指南**：[docs/USER_GUIDE.md](../USER_GUIDE.md)——这是什么与谁能做什么、你现在的状态、第一次接通、每天怎么用、读懂控制台、额度用完怎么办、人工交接、排障、改配置、安全边界、备份回滚、命令速查、文件位置。

## 文件列表
新增：`ops/connect_claude_desktop.py` `tests/unit/test_ops_connect_claude.py` `docs/USER_GUIDE.md` `docs/specs/LEAN_CONSOLE_SPEC.md` `docs/specs/MUTATIONS_LEAN_L1.txt` `docs/specs/MUTATIONS_LEAN_L2.txt` `docs/reports/LEAN_CONSOLE_REPORT_20261015.md`
重写：`src/par/web/static/{index,tasks,coordinators}.html` `app.js` `style.css`（`models.html` → `coordinators.html`，删除 `health.html`）
修改：`src/par/web/{api,server,__init__}.py` `src/par/{coordinators,cli_coordinator}.py` `tests/unit/{test_web_static,test_web_api,test_web_server_process,test_coordinators}.py` `tests/tools/mutation_check.py`；文档 README、WEB、COORDINATORS、SECURITY、OPERATIONS、ROLLBACK、CHANGELOG、specs
删除：`src/par/web/config_edit.py` `tests/unit/test_web_config_edit.py`
你机器上被改过的两个文件：`~/.codex/config.toml`（加了 `[mcp_servers.par]`；Codex 自己的写入器顺带做了无害的规范化：重排了 `node_repl` 环境变量的键序、去掉了一个空的 `args = []`、`120` 写成 `120.0`；备份在旁边）；Claude Desktop 的配置**已恢复成原样**（我自己的两个备份文件也删了，字节核对一致）。

## 测试
- 命令：`python3 -B -m unittest discover -s tests -t .`
- 结果：**1359 项，全部通过（OK，另有 23 项默认跳过：需要真实 Codex/agy/Harness 的选择加入式核对）**，约 6 分钟；上一份报告时是 1312 项（净增 47 项：新增控制台页面测试与接入小工具测试，减去随被删接口和配置编辑一起去掉的测试）。证据：终端输出 `Ran 1359 tests … OK (skipped=23)`。
- 变异检验：`python3 -B tests/tools/mutation_check.py` 共 **289** 项；本轮相关的 **46** 项（控制台后端与页面 38、接入小工具 8；另有 14 项随被删的代码一起去掉了）**全部被测试发现**。过程中暴露一处测试偏弱（读取失败时「保留旧列表」的断言放过了「经辅助函数清空」的变异），已加强。
- 真实调用：**没有任何模型调用**。真实 Codex 程序只用于 `codex mcp add/list/get`（读写配置文件）。
- 浏览器：真实控制台（演示数据）里走通：工作台、任务页（待批准 → 批准 → 排队中、操作按状态变化）、主控页（接入方法、复制、切换确认与取消）、只读模式、窄屏；另用模拟接口覆盖演示里没有的状态（运行中、失败、需要恢复、两条批准、404、409、连接失败、令牌失效）。

## 与 Harness 兼容性
`par harness check`：FAIL 0 | WARN 13（均为既有项）| 与 2026-10-14 基线的差异 **0**。`par doctor`：PASS 5 | WARN 0 | FAIL 0。本轮没有读写任何项目的 `.ai/`、`AGENTS.md`、`CLAUDE.md`。

## Provider / 记忆 / 权限 / 凭据 / 计费安全
- **权限**：主控仍没有批准、验收工具；控制台的写动作只剩任务动作、重新检测、切换主控，**不再能改配置**。Claude Desktop 的接入由你运行的小工具完成（Runtime 本身从不改任何应用的配置）。
- **凭据**：没有读取任何凭据；Claude Desktop 配置里只看了结构（键名），没有打印值；接入片段只含路径。
- **真实数据目录**：台账文件哈希不变；`par coordinator kit` 与桥的启动检查都没有写它（台账的新索引要等第一次真实使用才会创建）。检查台账时用的 `sqlite3` 曾短暂生成两个空的旁路文件，已在确认无进程占用后删除。
- **计费**：没有新的付费通道，没有模型调用。
- **一处如实说明**：我在浏览器里测试控制台的「复制」按钮时，曾把演示用的接入片段（只有路径，没有密钥）写进你的系统剪贴板（上一轮）；本轮没有再复制。

## 问题与限制
1. **Claude Desktop 还没接上**：要你退出应用后运行一条命令（指南 2.2）。接上之前，Claude 一侧的「第一次 list_tasks」没有人试过。
2. **Codex 一侧同样没在聊天里真调用过工具**：配置与启动握手都验证了，但「模型在对话里调用它」要你重启 Codex、贴起手提示词后才能看到。
3. 任务页里 attempt 的状态（`ENDED` 等）、事件类型代码、任务标题、Kernel 记录、Worker 自述按原样显示（它们是 Runtime 的记录或模型的话，不是界面文字）。
4. 在内置浏览器里，确认框的关闭要等页面重绘后才生效（代理测试时发现）；真实浏览器不受影响。
5. Codex 在某些批准模式下可能不经询问就调用 MCP 工具；Kernel 自己的批准与验收照常生效。
6. 并发仍固定为 1（T17/T18 未覆盖）；真实项目仍一律不能写（`WRITE_DISABLED`）。

## 需要我批准的动作（逐项，不合并）
1. **Claude Desktop 接入**：这一步是**你**来做（不是我）：⌘Q 退出 Claude Desktop → `.venv/bin/python ops/connect_claude_desktop.py --apply` → 重新打开。注意：退出它会结束你当前在 Claude Desktop 里的所有会话。
2. **P8 第一次真实写入试运行**（你说了全部批准，但我需要你定两件事，见我随后的提问）：① 哪个项目；② 隔离等级。
3. **并发 1→2**（同样需要你确认值不值得做，见提问）。
4. **路线 B** 保持「以后再说」，不动。
5. 仍按你之前的决定**推迟到最后**：Harness 治理类的 4 项、6 项只有你能做的事、Runge STATE 对账。

## 回滚
非破坏性、一条命令：`git switch -c back-to-route-a ef78e67`（回到本轮之前的提交；`main` 原样保留）。你机器上的改动：Codex —— `codex mcp remove par`（或把 `~/.codex/config.toml.par-backup-20261015-161555` 拷回原名）；Claude Desktop —— 本轮没有留下改动。详见 [ROLLBACK.md](../ROLLBACK.md)。

## 是否达到 Gate
- 路线 A / P5 Gate「任一选定主控能通过支持的接口创建/查询一次真实任务」：**接口与配置一侧达到**（Codex）；**应用一侧待你**（Claude Desktop 接入 + 两边各一次真实对话）。
- 控制台（P7）验收「用户能独立操作」：代码、自动化测试、真实浏览器走查**达到**；请你亲自开一次 `par serve --allow-write` 试用，有不顺手的地方告诉我。

## 下一阶段建议（只是建议）
先接通 Claude 一侧并各试一次 `list_tasks`（15 分钟），再在夹具上走一遍「说话 → 批准 → 运行 → 验收」，确认体验；然后做 P8：选一个**小的、无敏感文件、有 git 的真实项目**，用现有的隔离（私有副本 + 路径白名单 + 内核自己量测）做第一次写入试运行；Runge 要等 STATE 对账与读范围收紧之后。并发 2 不急。
