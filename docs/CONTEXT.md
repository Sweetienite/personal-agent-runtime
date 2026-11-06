# Context Manifest：给 Worker 的上下文契约

Kernel 派任务时，不会把项目的全部状态塞给 Worker，而是给一份 **Context Manifest**：列出这次任务会看到什么、以什么形式、凭什么确认它没被改过。它由实现了 `par.context.interface.Context` 的适配器只读生成；`par.context.resolver.default_context` 按项目登记的 `context_mode` 选择实现——新项目默认 `GenericContextAdapter`（`src/par/context/generic.py`），显式 `--context-mode harness` 的旧项目用 `HarnessContextAdapter`（`src/par/context/harness.py`）。Kernel 只依赖接口，不直接构造具体适配器。整条路径只读，不写任何东西。

查看：`par project show <id> [--manifest] [--json]`。

## 规则

1. `AGENTS.md` 是共享入口。唯一的 active 合同和**健康的** `STATE.md` 可以内联，其余一律是指针。
2. 超过上限的东西变成指针（路径、大小、哈希），**绝不静默截断**。上限：单条 20,000 B，合计 60,000 B，`STATE.md` 15,000 B（与 Core 的 hygiene 一致）。
3. 超大的 `STATE.md` 永远不内联，并且在它被对账之前拦住写入试运行。
4. 每一条都带来源、大小和 sha256。读取内联内容时会重新校验，不一致就抛 `ContextChanged`，对应 Core 的 `CONCURRENT_MODIFICATION_DETECTED` 语义。
5. 敏感路径只计数、不列名。事实源指针取自 `STATE.md` 的 `## Sources of Truth`；绝对路径、`..` 和符号链接逃逸一律标为 `outside_project` 并跳过。`README.md`、`AGENTS.md`、`CLAUDE.md`、`.ai/STATE.md`、active 合同本身是符号链接、指向项目外或不可读时，**不读取**、不进入清单，并在 warnings 里点名说明（符号链接指向项目内的另一个文件也一样拒绝）。
6. `digest` 只取决于内容，不含生成时间；相同输入得到相同 digest。
7. 默认全新会话：清单不携带任何旧对话或工具记忆。

## 一条 entry

| 字段 | 含义 |
|---|---|
| `kind` | `rules`（AGENTS.md）、`bridge`（CLAUDE.md）、`state`、`contract`、`source` |
| `mode` | `inline` 给内容；`pointer` 只给路径加大小加哈希，Worker 在权限允许时按需读取；`skip` 不给，并说明原因 |
| `bytes` `sha256` | 生成清单时的大小与哈希 |
| `reason` | 为什么不是 `inline` |
| `topic` | 仅 `source`：STATE 里对该文件的称呼（已脱敏） |

## 清单与任务登记的对应关系（总方案 4.1）

| 任务字段 | 来自 |
|---|---|
| `parent_contract_ref` | `manifest.parent_contract_ref`：唯一 active 合同的路径加哈希；没有或多于一个时为 `None` |
| `state_revision/hash` | `manifest.state_revision` |
| `base_git_commit` | `manifest.base_git_commit`（完整 SHA） |
| dirty manifest | `manifest.base.dirty` 计数加 `base.dirty_digest`（对未提交状态行取哈希，不泄露文件名） |

任务执行前后各取一次清单，`digest` 或 `dirty_digest` 变了就是「基线漂移」，必须人工处置，不能覆盖（T23）。

### P2 里 Kernel 怎么用它（`core/kernel.py`）

- **提交时**取一次清单，把下面这些存进任务的 `base`：`commit`（完整 SHA）、`branch`、`dirty` 计数、`manifest_digest`、`state_revision`、`parent_contract_ref`，以及用于漂移判断的 `drift` 签名。
- **漂移签名**只取真正会改变任务前提的东西：提交（`head`）、规则文件哈希（`rules`）、STATE 哈希（`state`）、父合同哈希（`contract`）；R2 任务另加 `dirty_digest`（写任务要求干净基线）。只读任务不因为你在项目里随手改了无关代码文件而被打断；Worker 看到的是**已提交**的基线克隆，不含未提交的修改。
- **运行时**重新取清单与签名比对：不同 → `PERMISSION_WAIT` 加 `BASE_DRIFT`；你批准后以当前状态重写 `base`（记 `BASE_REFRESHED`），拒绝则取消任务。
- **给 Worker 的提示词**里的「Project context」一节就是 `render(manifest)` 的输出：内联条目（规则、健康的 STATE、唯一合同）原文、其余指针。渲染时会重新校验哈希，文件在取清单与渲染之间变了就拒绝运行（`context_changed`）。
- **父合同规则（Harness 模式）**：有且仅有一份 active 合同的项目，任务挂在它之下；没有 active 合同的项目只接受 `ad_hoc=true` 的 R0/R1 只读诊断；有多份则一律拒绝。Kernel 从不自己创建权威合同。generic 模式没有父合同：任务的 `ad_hoc` 必须为 `true`，R2 在写入开关打开、git 基线干净且逐项批准后同样可以运行。

## 写入就绪（`write_readiness`）

返回 `allowed` 和一组 `blockers`。两种模式都要求：写入开关已打开（`par project enable-write ID`；夹具项目除外）、项目目录存在、git 基线可回滚、工作树干净、git 里看不到敏感路径。Harness 模式额外要求健康的 `STATE.md` 和唯一 active 主合同；generic 模式没有这两项。

| 代码 | 含义 |
|---|---|
| `WRITE_DISABLED` | 写入开关还关着：`par project enable-write ID`（先体检，再敲一遍 id） |
| `STATE_OVERSIZED` | Harness：`STATE.md` 超过 15,000 B，先对账（Core 文档 06 §6），经你确认 |
| `NO_ACTIVE_CONTRACT` / `MULTIPLE_ACTIVE_CONTRACTS` | Harness：写任务需要唯一的已批准主合同 |
| `SENSITIVE_VISIBLE` | git 能看到敏感路径：先补忽略规则 |
| `DIRTY_BASE` | 有未提交改动：需要干净基线或显式的 dirty manifest |
| `NO_VCS` / `VCS_MISMATCH` | 没有版本控制可回滚：需要显式的备份或快照流程 |
| `NO_STATE` / `NO_PROJECT` | Harness 缺 `STATE.md` / 项目目录不存在 |

## Generic 模式（普通 Git 仓库）

`par project add ID PATH` 默认登记为 generic：普通 Git 仓库即可，不要求也不写 `AGENTS.md` / `.ai/STATE.md`。旧 `projects.toml` 里没有 `context_mode` 的条目保持 harness 行为（回退步骤见 `docs/OPERATIONS.md` 的「兼容与回退」）。

- 上下文只读仓库已有的 `README.md`（清单 kind `readme`）、`AGENTS.md`（`rules`）、`CLAUDE.md`（`bridge`），加 git 事实；没有文件也合法。
- `AGENTS.md` 与 `CLAUDE.md` 同时存在且后者不是 `@AGENTS.md` 时，清单两份都列、写明冲突，不替你决定优先级。两者都是不可信项目数据，不能改 Kernel 权限。
- 没有父合同：任务合同的 `ad_hoc` 必须为 `true`；R2 仍要 `allowed_paths`、`acceptance.checks`、项目写入开关、干净 git 基线和逐项批准。
- 内联/指针上限、`digest`、渲染时重新校验哈希、符号链接/越界/敏感路径拒绝，与 Harness 模式一致。
- 模式是可见的：`par project show ID` 有一行 `Context` 说明当前是 generic 还是 harness、各读什么；`par project list --json` 带 `context_mode`；`par doctor` 汇总已登记项目中两种模式的数量。

## 已知限制

- 事实源指针只认英文标题 `## Sources of Truth`（Harness 模式）。
- 指针指向的文件只做 `stat`，不读内容。
- 登记只记录「在哪」，不记录「里面有什么」，不构成第二份项目记忆。
