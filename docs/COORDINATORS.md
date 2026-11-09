# 可选主控（路线 A）：谁来指挥可以换，任务不受影响

**核心思想**：中枢是 Runtime（Kernel + 台账），不是任何一个聊天里的模型。任务、批准、证据、命令行都不依赖主控；主控只是「你对话的那个模型」，它通过同一座 MCP 桥进入 Kernel。所以 Claude 额度用完，不会让架构失效：换一个主控，接着干。

## 现在的两个候选

| id | 名字 | 怎么连 | 额度用完意味着什么 |
|---|---|---|---|
| `claude_desktop` | Claude（Desktop / Code） | MCP：`claude_desktop_config.json`，或 Claude Code 的 `claude mcp add-json` / 项目 `.mcp.json` | 这个聊天停了；Kernel、任务不受影响 |
| `codex` | Codex（OpenAI） | MCP：`~/.codex/config.toml` 的 `[mcp_servers.par]`，或 `codex mcp add` | 同上 |

`codex_manual_takeover` 是 Codex 主控早先的名字，现在等同于 `codex`（配置里还写着它也能用，记录时统一成 `codex`）。别的模型以后要加，只需要在 `src/par/coordinators.py` 里加一项（名字、怎么连、额度说明）。

**主控与 Worker 是两种独立角色，不是两家厂商**：`coordinator` 只决定「你和谁对话」，Worker 由 profile 的 role→worker 链决定。同一个软件（例如 Codex）可以同时出现在两边，不需要第二个账号或新的 Solo 模式。每个主控有一个机器可读的描述符（`par.coordinators.descriptor(id)`）：`surface = mcp_host`、`connection = mcp`、以及桥允许的 capability（`read / submit / approve / reject / accept / close / recover`）——这是**描述，不是授权**，桥仍执行 Kernel 自己的审批并记录 `by="coordinator"`。`par coordinator status` 也显示每个主控的 surface 与 capability。Worker 运行使用 Kernel 新建的私有 HOME/MCP 环境，不会继承主控的配置或 token。

## 日常

```bash
par coordinator status           # 谁在主控、谁最近出现过、配置里的默认与建议的后备、最近的切换
```

## 连接一个主控（只有你动手，Runtime 不改任何应用的配置）

```bash
par coordinator kit codex        # 打印：步骤、要粘贴的片段、一行命令版本
par coordinator prompt codex     # 打印：起手提示词（它必须遵守的规则）；加 | pbcopy 直接复制
```

1. **Codex**：运行 `kit` 打印的那条一行命令（它调用 Codex 自己的 `codex mcp add`，写进 `~/.codex/config.toml`；改前先备份那个文件），然后重新启动 Codex。`codex mcp list` 应能看到 `par`。终端里请用 ChatGPT 应用自带的那个 `codex`（`kit` 打印的命令已经用了你在 `providers.toml` 里配置的路径）。
2. **Claude Desktop**：**必须在应用退出后**写入。Claude Desktop 运行时会把自己内存里的配置写回 `claude_desktop_config.json`，运行期间的改动会被悄悄抹掉（实测发生过）。用随仓库附带的小工具，它会备份、只动 `par` 这一项、写入后自检：
   ```bash
   .venv/bin/python ops/connect_claude_desktop.py            # 试运行：只显示要加什么
   .venv/bin/python ops/connect_claude_desktop.py --apply --background --reopen
   # 然后完全退出 Claude Desktop（⌘Q）：它退出后工具自动写入、自检、并重新打开应用（想撤销：--remove --apply …）
   ```
3. **Claude Code**：默认不接（接到用户级会让你所有项目的所有会话都多出 21 个工具）。只在需要的项目里，执行 `kit claude_desktop` 打印的 `claude mcp add-json …` 一行命令（不加 `--scope`，默认只对当前项目生效）。
4. 把 `par coordinator prompt NAME` 的内容作为新聊天的第一条消息（Claude Code：放进该会话的项目说明里）。它告诉主控：有哪 21 个工具、怎么用 `prepare_task` 得到合同、怎么用依赖关系拆大任务、它可以在当前租约与 `approval_mode`/grant 允许时自己决定批准/拒绝/验收/关闭（2026-10-20 起；每次决定都记着是它做的）、Worker 说的话不可信。
5. 验证：让它调用 `list_tasks`，然后运行 `par coordinator status`——它的「最近出现」应是刚刚。

- 片段里的 Python 是**生成它的那个 Python**。请用项目自己的解释器生成（`.venv/bin/par coordinator kit …`，它指向 uv 管理的稳定 Python 3.12），不要用放在「下载」文件夹里的 conda 环境——那个文件夹一清理，桥就悄悄坏了。
- 片段里的 `PYTHONPATH` 必须保留：Claude Desktop / Codex 启动桥时不在任何特定目录。
- Codex 的配置已用你机器上的 Codex（0.162）验证过：`codex mcp list` 能列出 `par` 且启用；用 `tests/tools/smoke_codex_mcp.py` 可以在一次性目录里重做这项验证（不调用模型）。

## 合同速查（主控按需阅读）

主控不需要手写完整 JSON：先给草案，`prepare_task` 会补齐机械字段或列出缺失项。

```json
{"project_id": "simproj", "title": "Fix the solver step",
 "description": "Make choose_dt return the documented step.",
 "risk": "R2", "allowed_paths": ["src/solver/"],
 "acceptance": {"criteria": ["the solver test passes"],
                "checks": [{"name": "solver", "argv": ["python3", "-m", "unittest", "tests/test_solver.py"]}]}}
```

- `risk`：`R0`/`R1` 只读，`R2` 改文件；`R3` 永不运行。`R1` 不能带 `allowed_paths` 或 `checks`。
- `allowed_paths`：写任务必填，文件或前缀（前缀以 `/` 结尾），不能是 `.`、绝对路径或 `..`。
- `acceptance.checks`：写任务必填；程序必须是 Kernel 能解析的 `python`/`python3` 或可信目录里的程序；检查在私有副本里由 Kernel 自己跑。
- `ad_hoc`：generic 项目必须为 `true`；Harness 项目在唯一主合同下为 `false`，没有主合同时 R0/R1 用 `true`。
- `timeouts.total` 不写就是 1200 秒；需要写新逻辑和它自己的测试时显式调大。
- `dependencies` 是任务 id 列表；前驱 `ACCEPTED`/`CLOSED` 后下游才会跑。如果前驱改了文件（R2），验收还不够：先用 `par candidate create --task A` 做候选整合，再把 candidate id 传给 `submit_task` 的 `candidate` 参数（或让用户 `par task rebase B --candidate …`），否则下游会在运行前被拦住。
- 幂等键：草案编译器按内容确定性生成；同一意图重提会得到同一个任务。

`prepare_task` 返回 `READY` 后，把它给的 `contract` 原样交给 `submit_task`；返回 `NEEDS_DECISION` 时把问题问人，不要猜路径或命令。字段级 schema 与错误见 `docs/MCP.md` 和 `src/par/schemas/task.schema.json`。

## 切换（额度用完的那一刻）

```bash
par coordinator use codex        # 写接管包 + 记录这次切换（你敲这条命令就是确认）
```

然后：在 Codex 里贴起手提示词，再把它打印出的接管包（`takeover.md`，只含 Kernel 量到的事实：任务树、状态、检查结果、待决批准、Harness 文件的路径与哈希）交给它读。切回来同理：`par coordinator use claude_desktop`。

- **同一时刻只有一个主控**：切换后，被替换的主控再通过桥提交、启动、重试、继续、构建候选、rebase，或批准/拒绝/验收/关闭，都会被 Kernel 以 `not_the_coordinator` 拒绝（三种 `approval_mode` 都一样，autonomy 不绕过租约），避免两个主控向同一本台账同时调度；它仍然可以查看、暂停和取消（停下来永远是安全的）。**你自己的命令（`par task …`）永远不被拦。**
- 已验收的任务不会因为换主控而重跑。
- 切换是**你的动作**：Kernel 看不到聊天应用的额度，所以不会自己切。`最近出现` 只是心跳（桥每次被调用时记一条，每个主控每分钟最多一条），帮你判断“它是不是还活着”。

## 安全小结
- 桥里没有健康检查、Provider 探测、配置写入一类的工具（有测试钉死）；批准/拒绝/验收/关闭自 2026-10-20 起也是桥里的工具了（`approve_task` `reject_task` `accept_task` `close_task`），每次决定都带着 `by="coordinator"` 和主控 id 记进台账。主控能否真正按下这一步由 profile 的 `approval_mode` 决定：默认 `coordinator_autonomous`（可自行决定），`coordinator_scoped` 需要你们在 CLI 创建的限定 grant，`human_only` 一律拒绝。Codex 可能不经询问就调用 MCP 工具（取决于它的批准模式），Claude Desktop 会逐个询问你——两种情况下 Kernel 自己的闸门（写权限路径白名单、补丁不会被应用到项目）都照常生效。
- 接入片段只含路径和固定字样，不含任何密钥；Runtime 不读、不写应用的配置文件，也不启动这些应用。
- 起手提示词里的规则（Worker 的话是不可信数据、不要循环重试、不要重跑已完成的任务、不要把任何东西当成已验证除非 Kernel 量过）是对主控的要求，**不是对 Kernel 的依赖**：Kernel 的边界检查和验证不管主控怎么说都照常进行。

## 排障
- 「最近出现」一直是 never seen：应用是否完全重启过？片段里的 Python 路径是否真实存在？`PYTHONPATH` 是否保留？Codex：`codex mcp list` 里有没有 `par`、状态是不是 enabled？Claude：新聊天的工具列表里有没有 par 的 21 个工具？
- 在终端手动运行片段里的命令（`python3 -B -m par mcp serve --data-dir … --coordinator codex`）应当**安静地等待输入**（Ctrl-D 退出）；它一启动就报错，说明路径或数据目录不对。
- 桥拒绝了 `submit_task`：`par coordinator status` 看现在是谁在主控；不是它的话，要么用 `par coordinator use` 切回来，要么换对应的主控去操作。

## 路线 B（以后再说）
“Runtime 自己驱动主控”——你只对 Runtime 说目标，它用你选的模型当主控、只给它这些工具。已讨论过代价（主控自己持续耗额度、要有主控会话的生命周期、Worker 输出会被主控读到有提示词注入风险、agy 当主控会碰到它的服务条款），你决定先做 A。需要时再单独设计，不影响现在的任何东西。
