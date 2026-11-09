# Kernel：任务、台账与恢复（P2，P3 补充）

Kernel 是 Runtime 里唯一允许改变任务状态的地方。它独立于任何协调者（Claude 会话）运行：协调者退出，任务状态、历史和已落盘的改动都还在。P2 的 Worker 全是脚本化的测试替身；P3 起 Codex 官方 CLI 是第一个真实 Worker（测试套件仍然只用假的 codex 程序，**真实模型调用只在你批准后发生**）。

## 它不做什么

- 不给 Worker 打分，也不调用任何 AI 来决定路由：链条由你配置，检查全是事实（是否启用、有没有适配器、近期是否出过明确的错误）。
- 不把结果应用到真实项目。`ACCEPTED` 的意思是「一个人接受了这份结果」，补丁留在 `work.patch`，真正应用是 P8 之后单独审批的动作。
- 不自动付费、不自动换成更高权限、不清理证据。
- 不在没有活跃主合同的项目里造权威合同（见「提交」）。

## 状态机

状态转换由一张显式的表规定（`core/states.py`）。同一张表被写进台账的 `transitions` 表，数据库触发器按它放行：写错的代码或别的进程都做不出非法跳转。

```text
DRAFT → READY → QUEUED → STARTING → RUNNING → VERIFYING → REVIEW_REQUIRED → ACCEPTED → CLOSED
                  │          │          │           │
                  │          └──────────┴───────────┴──→ FAILED / INTERRUPTED / RECOVERY_REQUIRED / CANCEL_REQUESTED
                  │                     └──→ RATE_LIMITED / QUOTA_EXHAUSTED / AUTH_REQUIRED / BLOCKED_NO_WORKER / PERMISSION_WAIT
                  └──→ PERMISSION_WAIT / BLOCKED_NO_WORKER / PAUSED / CANCELED
```

| 状态 | 含义 | 你能做什么 |
|---|---|---|
| `PERMISSION_WAIT` | 等你批准：写权限、协调者指定 Worker、基线漂移、换 Worker、被拒权限后重试 | `approve` `reject` `cancel`（只有换 Worker 的提议时也可 `retry`） |
| `QUEUED` | 可以运行 | `run` `pause` `cancel` |
| `STARTING` `RUNNING` `VERIFYING` | 一次 attempt 正在进行 | `cancel` |
| `REVIEW_REQUIRED` | 干净的结果等你审阅 | `accept` `reject` `cancel` |
| `ACCEPTED` → `CLOSED` | 你接受了结果 → 归档 | `close` |
| `FAILED` | 这次 attempt 失败（进程失败、越界、检查未过……） | `retry` `cancel` |
| `INTERRUPTED` | 超时或无法归类的结束，现场保留 | `retry` `pause` `cancel` |
| `RATE_LIMITED` `QUOTA_EXHAUSTED` `AUTH_REQUIRED` | 厂商明确返回了对应原因 | `retry`（`--force` 清除健康记录）`pause` `cancel` |
| `BLOCKED_NO_WORKER` | 没有可用 Worker，原因逐条列出 | `retry` `pause` `cancel` |
| `PAUSED` | 被暂停（你或失败策略） | `resume` `retry` `cancel` |
| `RECOVERY_REQUIRED` | 上一个 Kernel 消失了，现场已保存 | `recover` `retry` `cancel` |
| `CANCEL_REQUESTED` → `CANCELED` | 正在取消 → 已取消 | `retry`（新 attempt） |

明确不存在的边：`RUNNING → ACCEPTED`（绕过验证与审阅）、`ACCEPTED → RUNNING`（不原地重跑）、`CANCELED → RUNNING`（取消后只能回到 `QUEUED` 开新 attempt）、任何东西回到 `DRAFT`。没有走到 `REVIEW_REQUIRED` 的任务永远不能被接受（有测试覆盖每一种前置状态）。

## 任务合同

JSON，由 `task.schema.json` 加语义规则校验（`core/contract.py`）。关键字段：`project_id` `title` `description` `risk` `role` / `worker` `allowed_paths` `acceptance.checks` `dependencies` `ad_hoc` `timeouts` `idempotency_key`。

| 风险 | 含义 | Worker 得到什么 | 路径 / 检查 |
|---|---|---|---|
| R0 | 纯文本回答，不读写文件 | **空文件夹**，没有项目文件 | `allowed_paths` 必须为空，不能有检查 |
| R1 | 只读分析项目 | 私有克隆（只读请求） | 同上；任何文件改动都算越界 |
| R2 | 修改文件 | 私有克隆（可写）+ 路径白名单 | `allowed_paths` 必填；检查由 Kernel 运行 |
| R3 | 删数据、改凭据、部署、远程写入、付费 | — | **一律拒绝**：V1 不自动执行 |

- `allowed_paths`：相对项目根的前缀（`app/`）或精确文件；**没有通配符**；`.git`、`.ai/`、`AGENTS.md`、`CLAUDE.md`、敏感路径（`.env`、密钥、`runtime/accounts/` 等）永远不能被列入，也永远不能被改。
- `acceptance.checks`：Kernel 自己运行的命令（argv 列表、无 shell）。可执行文件限于 `python3 pytest ruff mypy tsc node npm make go cargo swift`，且每个都有更细的规则：`python -c`、`node -e`、`npm install/publish`、`go get/run`、`make -f/-C`、绝对路径与 `..` 一律拒绝。R2 没有检查时会提示「验收只靠你对 diff 的审阅」——这条规则现在由 Kernel 强制（2026-11-03）：协调者（MCP 桥的 `accept_task`）在这种任务上会被直接拒绝（`cannot_accept`），因为桥从不把 diff 内容交给协调者，只有 `diff_stat` 和检查结果；只有你自己在终端用 `par task diff` 看过改动后，`par task accept` 才能接受。有检查的 R2 任务不受影响——协调者照常按检查结果验收。
- 合同里出现疑似凭据、控制字符（可劫持终端的转义序列）、重复 JSON 键、超过 256 KB，整份拒绝。
- **幂等**：`(project_id, idempotency_key)` 唯一。相同 key + 相同合同 → 返回原任务（记一条 `SUBMIT_DEDUPLICATED`，不重复启动）；相同 key + 不同合同 → 拒绝。

### 提交（`par task submit`）的检查顺序

合同校验 → 项目已登记 → 幂等 → 依赖存在且同项目 → 读取项目视图与 Context Manifest → 父合同规则 → 写入就绪（R2）→ 记录基线（提交、Manifest 摘要、STATE 修订、父合同引用）→ 固化配置快照 → 入账。

- 项目有且仅有一份 active 合同时，任务挂在它之下；没有时只接受 `ad_hoc=true` 的 R0/R1 诊断；有多份时一律拒绝。
- **R2 需要 `write_readiness.allowed`**。真实项目的 `write_enabled` 默认 false（会被 `WRITE_DISABLED` 拦下），要由你用 `par project enable-write ID` 亲自打开（它自己会先按同一套 `write_readiness` 体检，见 [OPERATIONS.md](OPERATIONS.md)）；夹具项目恒可写。
- R1/R2 需要 git 基线提交；没有 git 的项目（如毕业论文）P2 只接受 R0。
- R2 提交后进入 `PERMISSION_WAIT`，等你批准 `WRITE_PERMISSION`。协调者提交且指定了 `worker` 时另需批准 `WORKER_OVERRIDE`。`run` 开始前还会再核对一次这些批准（取消后再重试不能绕过）。

## 台账（SQLite，`<数据目录>/runtime.sqlite3`，0600）

执行台账，**不是项目记忆**：只存任务事实（状态、attempt、事件、审批、锁、Provider 健康），不存项目知识。

- 持久性：WAL + `synchronous=FULL` + 外键；每次状态变化与它的事件在**同一个事务**里；所有写入必须在 `Store.tx()` 内（否则抛错）；嵌套事务是 SAVEPOINT。
- 数据库自己守规矩（触发器）：事件只增不改不删；任务与 attempt 不删除；已结束的 attempt 不可变，状态只向前；任务状态必须沿 `transitions` 表走；任务 `version` 每次恰好加 1；任务身份与合同冻结；已决定的审批不可变。`transitions` 表每次打开都与代码核对，被改动会被修复并留下 `TRANSITIONS_RESYNCED` 事件。
- 不在数据库文件上做原生 `open/close`：POSIX 下关闭任一描述符会丢掉同进程其他连接的锁（P2 的崩溃测试真实地抓到过这个缺陷，现在有回归测试）。
- 事件、原因、数据一律脱敏并截断；任务 ID 形如 `fixture-0001`，回滚的提交不消耗编号。

表：`tasks` `attempts` `events` `approvals` `project_locks` `provider_health` `transitions` `counters` `schema_meta`。

## 一次运行

`par task run TASK`（前台；Ctrl-C 与 SIGTERM 都会让 Worker 被停掉并留下干净记录）：

1. **恢复扫描**：先结算那些 Kernel 已消失的 attempt（见下）。
2. 前置：必须 `QUEUED`、`not_before` 已到、依赖都 `ACCEPTED/CLOSED`、批准齐全。
3. **漂移检查**：项目的提交、规则文件、STATE、父合同（R2 另加脏状态摘要）与提交时不同 → `PERMISSION_WAIT` + `BASE_DRIFT`；批准后以当前状态为新基线。
4. **路由**（见下），并按策略处理需要换 Worker 的情形。
5. **起步（一个事务）**：容量检查（同时在跑的 attempt 数 < Profile 的 `max_parallel_workers`，默认 1、最多 2）→ 插入 attempt → 取得项目锁（**同一个项目一次只有一个 attempt，不论容量**）→ `STARTING`。任一失败则什么都不留。`run --next` 遇到正被占用的项目会跳过去试别的项目的任务，容量满了才停。
6. **工作区**：在 `attempts/N/` 下先做一份克隆 `ref/`（Kernel 自留的干净基线），再复制出 `workspace/` 给 Worker（适配器可声明 `workspace_with_git = False`，这时副本不含顶层 `.git`：Codex 对「可写的 git 工作副本」会改写你的全局配置，所以它拿到的是普通文件副本；比较与补丁不受影响，因为两边本来就跳过顶层 `.git`）。克隆是 `git clone --no-hardlinks`，去掉 origin、关掉 hooks，已提交的敏感文件**不物化**（`ref/` 与 `workspace/` 都没有它们）。R0 只有空文件夹。
7. **进程**：先问适配器 `preflight`（可执行文件、签名、登录方式、隔离能否证明……任何一项不过就**不启动**，attempt 以 `spawn_failed` / `preflight` 结束为 `FAILED`，没有任何模型调用）；通过后若适配器有 `prepare(request, home, tmp)`，把 attempt 的私有 HOME 与 TMPDIR 交给它填配置（Antigravity 在这里写 Runtime 自己的 `settings.json` 与钥匙串链接；出错同样在启动前以 `FAILED` / `preflight` 结束）；再起独立会话与进程组；干净环境（固定 PATH、私有 HOME 与 TMPDIR，不继承你的任何环境变量，适配器声明什么才多给什么）；提示词由适配器经**标准输入**交给进程（不出现在命令行上）；启动 60 s / 无输出 5 min / 总计 20 min 三个独立超时；心跳线程用独立连接证明 Kernel 还活着。
8. **判断改动**：把 `workspace/` 与 `ref/` **当普通文件比较**（不跟随符号链接），**从不在 Worker 的副本里运行 git**——Worker 能改写 `.git/config`，下一条 git 命令就会执行它的代码。改动按 `allowed_paths` 分为：允许 / 越界（含受保护路径、符号链接、嵌套仓库、超大文件、不安全文件名）/ 噪声（`__pycache__`、`.DS_Store`、被 `.gitignore` 忽略的新文件，仅计数不算错）。
9. **补丁**：把允许的改动复制进 `verify/`（ref 的拷贝），由 Kernel 自己的 git 生成 `work.patch`，再把补丁**应用回干净基线并逐文件比对**，证明补丁与被验证的树一致。
10. **检查**：在 `verify/` 里运行合同的检查（干净环境、独立进程组、超时、输出脱敏）。Worker 自述的「测试通过」只是一段未验证的文字，永远不被采信。
11. **结算（一个事务）**：attempt 结束、释放项目锁、写结果信封、按结果与策略决定任务的下一个状态。
12. 清理 Kernel 自己的临时副本（`ref/` `verify/`）；Worker 的副本作为证据保留。

### 结果信封（`result.schema.json`）

`provider` `worker` `model_requested` 由 Kernel 记录；`model_observed` 只是厂商自报；`usage` 是厂商在终态事件里自报的 token 数（原样记录，不估算、不求和、不是账单）；适配器想让人看到的警告（被拒绝的命令、不该出现的工具……）以 `worker run: …` 的形式进入 `remaining_issues`；`summary` 是 Worker 的原话（≤1200 字，脱敏，`summary_verified` 恒为 false）；`tests` 与 `boundary` 完全由 Kernel 测得；还有 `diff_stat` `partial` `handoff_usable` `remaining_issues` `next_action`。`par task result` 显示时会把 Worker 文字里的控制字符转义。信封无法通过 schema 时仍进入 `REVIEW_REQUIRED` 保留改动，但**不能被接受**。

## 路由与失败策略

链条是确定的：合同显式 `worker` → 只用它（不会悄悄换别人）；否则 `role` 的 primary 再依次是 fallback；否则 Profile 的 `default_worker`。每个候选逐项检查：Profile 快照里有无定义、Provider 是否启用且为自动类型（`codex` `antigravity`）、模型 slug 不是占位符、能力与风险等级匹配（R2 需要 `edit`）、**注册了适配器**、没有健康记录挡着。

**角色与厂商解耦（T12）**：role 只是一个名字，映射到 worker primary/fallback；它不隐含「Claude 主控 + Codex Worker」。`WorkerEntry.surface` 与 `ProviderEntry.surface` 由 provider `kind` 派生（`native_cli` / `manual` / `programmatic_session` / `acp` 为描述，不改配置 schema），出现在配置快照里；旧 profiles.toml/providers.toml 与 `codex_manual_takeover` 别名照常可用。同一个软件可以同时是主控和 Worker：两者通过同一座 MCP 桥，但 Worker 使用 Kernel 新建的私有 HOME/TMPDIR，绝不继承主控的 MCP 配置或凭据。

**能力闸门**：`ProviderAdapter.capabilities()` 的八项 `yes/no/unknown` 是**构建期的静态事实**。`AdapterRegistry.register(..., capabilities=...)` 声明它们，路由层据此提前排除「headless 不是 yes」或「R2 但 write 不是 yes」的候选；读取声明**绝不实例化适配器工厂**。Kernel 在运行前再用**本次真正要启动的那个实例**复核一次，未证明的能力会在任何进程启动之前让 attempt 以 `FAILED`（`preflight`）结束。`unknown` 永远不会被当作 `yes`。

配置在**提交时固化**为快照存进任务；之后改 `profiles.toml` 不影响已排队的任务。`par task retry` 是你的显式批准，会采用当前配置（记 `CONFIG_REFRESHED`）。

| 结果 | 任务状态 | 健康记录 |
|---|---|---|
| 干净成功 + 检查全过 | `REVIEW_REQUIRED` | 清除该 Provider 的旧记录 |
| 结构化 `rate_limited`（含无原因的 429） | `RATE_LIMITED` | 该模型冷却至 `retry-after`，缺省 300 s |
| 结构化 `quota_exhausted` | `QUOTA_EXHAUSTED` | **整个 Provider**：到厂商消息里给出的重置时间（真实、未来 35 天内的时间戳才采用），否则直到你清除或一次成功 |
| `auth_required` | `AUTH_REQUIRED` | 整个 Provider，同上 |
| `model_unavailable` | `BLOCKED_NO_WORKER` | 仅该模型 |
| 权限被拒（适配器报告 `PERMISSION_BLOCKED` 时；Codex 适配器不产生它——被拒绝的命令只是结果上的警告） | `PERMISSION_WAIT` + `PERMISSION_RETRY` | 无；**绝不自动换更高权限** |
| `transient` | `INTERRUPTED`（`safe_auto` 下自动重试一次） | 无 |
| 超时、无终态的退出 0、无法归类 | `INTERRUPTED` | 无；沉默不等于进程已死 |
| 非零退出且无终态 | `FAILED` | 无 |
| 越界、检查未过、补丁对不上 | `FAILED`（补丁与日志保留） | 无 |

`worker_failure_policy`：

| 策略 | R0 / R1 | R2 |
|---|---|---|
| `ask`（默认） | 停在对应状态；有后备 Worker 时挂一个 `FALLBACK_SWITCH` 提议，**不自行切换** | 同左 |
| `pause` | 失败后进入 `PAUSED`，不挂提议 | 同左 |
| `safe_auto` | 自动重试暂时性错误一次；自动切换到下一个 Worker 一次 | **仍按 `ask`**：R2 永不自动 |

自动动作的次数用预算限制（`retry_budget` `switch_budget`，你显式 `retry` 时重置）。切换提议被你拒绝时：失败状态下只是清掉提议；在 `PERMISSION_WAIT` 里则转为 `PAUSED`，不会让任务卡在没有可决定事项的等待里。

## 取消、暂停、重试、审批

- `cancel`：未运行的任务直接 `CANCELED`；运行中的先置 `CANCEL_REQUESTED`，正在跑的 Kernel 在 0.25 s 内发现，请适配器温和停止，宽限期（5 s）后对**它自己启动的进程组**强杀；检查进程同样。取消先于结果：已完成但未结算的 attempt 也按取消处理。
- `pause`：只用于没在运行的任务；运行中的只能 `cancel`。
- `retry`：永远是**新 attempt**。写文件的任务（R2）若上一次尝试以失败/中断/孤儿结束并留下了通过边界检查的补丁，新 attempt **接着它继续**（接力，plan 6.2；只带 Kernel 量到的事实，不带上一个 Worker 的话；见 [HANDOFF.md](HANDOFF.md)）；被你拒绝、取消或已完成的尝试不会被接力；`--fresh` 从基线重来。采用当前配置、重置预算；`--worker` 指定 Worker（你的显式选择）；`--force` 清除该 Provider 的健康记录；撤回过期的换 Worker 提议；如果任务需要的权限从未被批准（例如在等待批准时被取消），会**重新要求批准**，不会借 `retry` 绕过。
- `approve`：有多项待批时必须用 `--id` 逐项指定，不存在「全部批准」。`reject`：写权限、Worker 指定、漂移、被拒后重试 → 取消任务；`REVIEW_REQUIRED` 时 `reject` 是拒绝结果（→ `FAILED`）。
- 所有批准都走 Kernel 自己的闸门。MCP 桥自 2026-10-20 起也暴露 `approve_task` `reject_task` `accept_task` `close_task`，各自只调用 `Kernel.approve`/`reject`/`accept`/`close_task`，固定传 `by="coordinator"` 并带上主控 id，谁决定的永远可查；能否决定由 profile 的 `approval_mode` 约束（默认 `coordinator_autonomous`，可设 `coordinator_scoped`/`human_only`），`coordinator_scoped` 还要求人在 CLI 创建的限定 grant。`clear_health`/`probe_providers`/配置写入依然完全不在桥里。提交者固定为 `coordinator`；一旦人确认了主控交接（`par takeover confirm`），被替换的主控再用桥提交任务或启动运行（含 `retry_task`）会被拒绝（`not_the_coordinator`）。

## 崩溃恢复

每个运行中的 attempt 有心跳（默认每 2 s，独立线程）。别的 Kernel 看到心跳超过 15 s 就认为它的主人已消失：

| 情形 | 做法 |
|---|---|
| 主人还活着（心跳新鲜） | 不动它 |
| 主人已消失、Worker 进程组**也没了** | 抓取工作区里的部分改动（补丁保存、`partial=true`），attempt 记为 `ORPHANED`，释放项目锁，任务进入 `RECOVERY_REQUIRED`（已请求取消的则 `CANCELED`） |
| 主人已消失、Worker **还活着** | 不启动第二个写入者：任务 `RECOVERY_REQUIRED`，项目锁与容量继续占用；`par task recover TASK --terminate` 只在**起始时间与记录一致**时才停止这个进程组 |
| Worker 领头进程已死但组里还有残留 | 无法确认身份，**拒绝动手**，提示你 `ps -g <pgid>` 自行检查 |
| 起步事务之后、进程启动之前就崩溃 | 同「没了」：记为 `ORPHANED`，`retry` 即可 |

`run` / `run_next` 开头会自动做这次扫描；对一个主人已消失的运行中任务执行 `cancel`，也会立刻完成取消。所以平时不需要手动恢复，`par task recover` 主要用于查看和 `--terminate`。

## 产物布局

```text
<数据目录>/                      0700
  runtime.sqlite3 (+ -wal -shm)  0600
  config/  projects.toml profiles.toml providers.toml
  scratch/                       git 用的私有 HOME
  tasks/<task_id>/attempts/<n>/  0700
    prompt.txt  stdout.log  stderr.log  changes.json  work.patch  check_<name>.log  result.json     0600
    workspace/   Worker 的副本（作为证据保留）      ref/ verify/   Kernel 的临时副本（结束时自动删除）
    home/  tmp/  Worker 与检查的私有 HOME 与 TMPDIR
```

不会自动清理 `workspace/`：它是证据，且「不做破坏性清理」是默认规则。体积大时由你决定何时删除（P8 会提供带审批的清理命令）。

## 夹具项目（P3）

真实 Worker 要能端到端演练，就得有一个**可以写**的项目——而真实项目在 P8 之前都不可写。`par fixture create` 在 `<数据目录>/fixtures/<id>/` 下造一个一次性的小项目（git 仓库、Harness 文件、`app/calc.py` 里一个故意的 bug、一个会失败的 unittest），登记为 `fixture = true` 与 `write_enabled = true`。它是**唯一**能被登记为可写的项目：加载器每次都独立核对「是 `fixtures/` 的直接子目录、文件夹名等于 id、带有合法的 `.par-fixture.json`、不是符号链接」，手工把真实项目改成可写、或把标记文件拷进真实项目，都会整条登记被丢弃（有测试）。

## 候选整合（`par candidate`）

三个词必须分开：**验收（accepted）**只记录结果；**候选整合（candidate integrated）**把已验收的补丁放到一条可追溯的基线上并重跑检查；**落地（applied）**是你自己对真实仓库 `git apply`。Runtime 不做第三步。

- **创建**：`par candidate create --task A [--task B ...]`。只接受 `ACCEPTED`/`CLOSED` 的 **R2** 任务，每个任务都必须有 `acceptance.checks`；项目必须干净，且 HEAD 等于第一个任务的 base（否则 `dirty_project` / `project_moved`）。
- **来源只有 Kernel 切出的补丁**：读 `attempts/<n>/work.patch`（不是 Worker 自述），记录每个补丁的 sha256、任务/尝试号、允许路径与提交。补丁若碰到 `.git`、`.ai/`、规则文件或敏感路径，直接拒绝。
- **顺序**：按 `dependencies` 拓扑排序；后一个任务的 base 可以是项目 base（独立任务）或前一个任务产生的候选提交（A→B 链）。其它 base 一律 `base_mismatch`。
- **provenance**：attempt 结束时 `changes.json` 记录 `patch_sha256`；整合前重新计算 `work.patch` 的哈希并与它比对。补丁在验收后被改动（即使在 allowed_paths 内）会以 `FAILED` 拒绝；没有记录哈希的旧 attempt 以 `missing_provenance` 拒绝。这样候选只使用**当时**被验收的那个补丁。
- **产物**：`<数据目录>/candidates/<candidate_id>/repo` 是 Kernel 自己的 git 仓库；提交时间固定，所以同样的 base + 同样的补丁会得到同样的 commit（依赖它的任务不会因为重新整合而失效）。`verify/` 是跑检查的一次性副本；`checks/` 留日志。整个目录 0700，真实项目一个字节都不动。
- **状态**：所有检查通过才是 `VERIFIED`；补丁冲突、受保护路径、缺补丁、检查失败都会留下 `FAILED` 记录与原因（缺补丁这类前置条件在创建记录前就 `Refused`）。失败/重复整合都不会改写旧 candidate 行。
- **B 如何使用**：`par task submit --contract FILE --candidate ID`，或对已排队但从未运行的任务 `par task rebase TASK --candidate ID`。B 的 `base.commit` 变成候选提交，`candidate_repo` 指向候选仓库；B 的补丁因此只含 B 自己的改动，不重复 A 的。
- **依赖语义**：本版本之后提交的任务（`base.integration = "candidate-v1"`）如果依赖一个**改过文件**（R2）的任务，必须有一个包含该依赖的 `VERIFIED` candidate 才能运行；只读依赖（R0/R1）不需要。此版本之前提交的任务保留旧语义（`ACCEPTED` 即可），不做历史改写。
- **落地顺序**：B 的补丁是针对候选提交的；要把 A、B 放进真实项目，先应用 A 的补丁，再应用 B 的。Runtime 不 push、不 merge、不自动应用。
- **项目漂移**：candidate 创建后项目 HEAD 再移动，不影响 B 在候选上运行（候选是不可变基线）；要让新链路基于新 HEAD，重新创建 candidate 并 rebase。候选绑定任务不会收到 `BASE_DRIFT` 审批，也不会被悄悄改回项目 HEAD。

## 证据分层（`par task result` 与 MCP）

结果信封本身不变（`schema_version = 1`），`par task result`、`get_task_result` 和 `review_task` 额外给一份**派生**的 `evidence_summary`：

| 字段 | 含义 |
|---|---|
| `level` | `MEASURED`（Kernel 跑了检查且都过）、`MEASURED_WITH_EDITED_CHECKS`（都过，但 Worker 改了检查会读到的文件）、`UNVERIFIED`（没有可运行的检查）、`FAILED`（检查/边界/尝试失败）、`INCOMPLETE`（中断/取消/孤儿）、`UNKNOWN`（旧结果或缺字段） |
| `claim` | Worker 自述，永远 `verified: false`；已脱敏、去控制字符、限长。主控看到的是同一个 `summary` 字段，不重复大段原文 |
| `boundary` | `CLEAN` / `VIOLATIONS` / `EXCLUSIONS`（有受保护文件被排除出副本，不是越界）/ `UNKNOWN` |
| `checks` | `status`、通过/总数、失败的检查名、从未运行的检查名、`worker_editable`（写任务为真）、`inputs_edited`（检查命令直接读到的被改文件）、`test_files_changed`（补丁里像测试的文件） |
| `review` | `NOT_REVIEWED` / `HUMAN_REVIEW` / `COORDINATOR_REVIEW` / `UNKNOWN`：谁验收或拒绝了这个结果 |
| `integration` | `NOT_INTEGRATED`（还没整合）/ `CANDIDATE_READY`（有 VERIFIED candidate 包含这个任务）/ `CANDIDATE_FAILED`（只有失败记录）/ `INTEGRATED`（预留给真正落地，Runtime 不设置）；验收不会自动改变它 |
| `applied_to_project` | 恒为 `false`：验收只记录结果，从不把补丁应用进项目 |
| `untested` | 从没跑过的检查名，或“合同没有 `acceptance.checks`”这句话 |

**派生，不新增列**：旧台账没有这些字段时按 `UNKNOWN`/`NOT_RUN` 返回，绝不会因为缺字段就当 `PASS`。检查仍然在 Kernel 自己搭的验证树里跑（Worker 能改测试脚本，这是测试的本质），所以“检查通过”只说明**该版本的检查**通过了；`inputs_edited`/`test_files_changed` 把“检查本身被改过”摆在明面上，独立参考断言应放在 Worker 无权修改的路径里。

## 已知限制

- 检查会运行补丁里的代码（这是测试的本质），P2 没有操作系统级沙箱。缓解：一次性副本、不含机密的干净环境、固定的可执行文件与参数规则、独立进程组与超时；而且 P2 根本不可能对真实项目提交写任务（P3 起只有夹具项目可写）。进入 P8 之前需要评估 `sandbox-exec` 之类的加固。
- 没有 git 的项目 P2 只支持 R0。
- 克隆用 `--no-hardlinks` 复制对象库，超大仓库会占空间（P8 前评估 `--shared`）。
- 接力已在 P6 实现（默认接着上一次已验证的改动；`--fresh` 重来）。仍然没有的：把**未验证**的改动、隐藏对话或上一个 Worker 的话交给下一个 Worker——这是有意不做的。
- 进程启动与「记录 pid」之间有一个极小的窗口；若恰在此刻崩溃，可能留下一个未记录的子进程（它在自己的进程组里，受超时之外无约束）。恢复时会看到 attempt 没有 pid，请检查 `ps`。
- 心跳判定依赖时钟；机器睡眠恰好跨过 15 s 时，另一个 Kernel 可能误判，结果是任务被标为 `RECOVERY_REQUIRED` 而不是丢数据。
- 能被 Kernel 终止的只有它自己启动的进程组；Worker 若 `setsid` 脱离进程组则无法追踪（读管道的线程不会因此卡死，但那个进程会留下）。
