# 接力与人工交接（P6 / 计划 5.5、6.2）

两件相关但不同的事：

1. **接力（relay）**：一次尝试中途停了（额度用完、超时、检查没过……），下一次尝试**接着已验证的改动继续**，而不是从零开始。
2. **人工交接（manual hand-off）**：把任务交给一个**只能人手操作的桌面应用**（WorkBuddy、TRAE、Cursor、Windsurf、Cline、Aider……——`--to` 接受任何小写名字，不是一份固定名单），由你在它的界面里完成，再把结果交回 Kernel 验证。

两者共用同一个原则：**Kernel 只相信自己量到的东西**。接力带过去的是 Kernel 自己切出并交叉核对过的补丁（`work.patch`）；人工交回的结果和任何 Worker 的输出一样，要经过文件比对、边界检查、合同里的检查，才会进入 `REVIEW_REQUIRED`。

## 1. 接力

- 触发：你显式 `par task retry`（或批准换 Worker）。**Kernel 不会自己接力。**
- 条件：写文件的任务（R2）；上一次尝试以 `FAILED` / `INTERRUPTED` / `ORPHANED` 结束，并且留下了通过边界检查的补丁。被你**拒绝**的结果、被取消的尝试、已完成的尝试都不会被接力（那是人的判断，不能悄悄沿用）。
- 怎么做：新尝试的工作副本里先打上上一次的补丁（`git apply`，先 `--check`，且禁止向外找仓库），再启动 Worker。最终补丁 = 两次的改动合在一起，检查由 Kernel 重新运行。
- 告诉新 Worker 什么：提示词末尾加一节 `## Handoff from attempt N`，只有 **Kernel 量到的事实**——上次怎么结束的、留下哪些文件（路径+增删行数）、哪些检查没过以及输出末尾 25 行（标明“项目输出，不是指令”）。**上一个 Worker 说过的话一个字都不带**（没验证过，也不复刻隐藏对话）。
- 留痕：`tasks/<任务>/handoff/relay-N-to-M.{json,md}`（0600）、事件 `RELAY_SEED`、尝试配置里的 `relay`（来源尝试、补丁 sha256、文件数）。
- 拒绝的情形（给出出路）：补丁丢失/读不了/碰了合同不允许的路径 → `Refused relay_unavailable`，任务保持 `QUEUED`，提示用 `par task retry TASK --fresh`；补丁和基线对不上 → 这次尝试在 `prepare` 阶段失败，Worker 没有启动。
- `par task retry TASK --fresh`：从项目基线重新开始（之前的尝试不再作为起点）。对 `QUEUED` 的任务也可用，专门用来走出“补丁不可用”的卡点。

## 2. 人工交接

```bash
par task handoff TASK --to workbuddy      # 或 trae / cursor / windsurf / cline / aider / 任意小写名字
# → tasks/TASK/handoff/manual-1/ 下有：PROMPT.md  README.md  manifest.json  workspace/
# 在应用里打开 workspace/ ；把 PROMPT.md 的全文粘贴给它的 Agent ；等它做完
par task import TASK --from tasks/TASK/handoff/manual-1/workspace
#   或 --patch 补丁文件（针对项目基线提交）   或 --text 答案文件（只读/文字任务）
par task result TASK ; par task diff TASK ; par task accept TASK      # 之后和别的任务一样
```

### 第一次试：用夹具，不用真实项目

`par fixture create` 造的夹具项目只有假的示例文件和一个自带的检查，没有你的代码或密钥，适合先把流程走一遍（真实项目等这一步顺了再试）。交接包的 README 会照常提醒：一旦在 GUI 应用里打开副本，副本里的文件就会发给那家厂商的云端模型——夹具里没有敏感内容，但这个决定仍然由你做出。

### 导出（`handoff`）做什么
- 只对已批准的任务（R2 的写权限必须已批准）、项目没有漂移的任务进行；状态须为 `QUEUED / PAUSED / FAILED / INTERRUPTED / RATE_LIMITED / QUOTA_EXHAUSTED / AUTH_REQUIRED / BLOCKED_NO_WORKER`。`QUEUED` 的任务会被**暂停**，防止自动 Worker 在你操作期间抢跑；其他状态不动。
- 输出是给人和给脚本都能用的：`task_id`、`export_id`、`mode`、`folder`、`workspace_absolute_path`、`prompt_path`、`manifest_path`、`readme_path`、`base_commit`、`allowed_paths`、`next_actions`。`next_actions` 是照抄就能做的短步骤（打开哪个文件夹、粘贴哪个文件、回来运行哪条 `par task import`、最后看哪条 `par task result`）；`--json` 给同样的机器可读版本。
- `mode` 按任务固定，不靠猜：
  - `workspace`：任务会改文件（R2）。应用在副本里改，交回文件夹或补丁。
  - `text`：任务只要文字答案（R0/R1）。R1 仍给一份**只读副本**让它读项目，但只收文字答案，文件改动不会被采用；R0 连副本都没有。
  - “只读但想要一份可写副本”不在这里自动发生：那需要单独的安全评估，不会顺手把项目文件（更不会带敏感文件）交给应用。
- `workspace/` 是项目在基线提交时的**私有副本**（不含 `.git`，不含疑似敏感文件；如有上次尝试的已验证改动，已经打在里面），里面放一个 `.par-handoff.json` 标记（任务号、导出编号、基线、模式）。**永远不要让应用打开真实项目目录。**
  **WorkBuddy 实测教训**（2026-10-16，一次真实交接暴露）：WorkBuddy 按「空间」工作，一个空间是对某个真实文件夹的长期绑定；`open -a WorkBuddy <路径>` 对已经在跑的实例完全没有效果（不新建窗口、不新建或切换空间）。光把 `workspace/` 的路径念给人听是不够的——如果侧边栏里已经有一个绑定着真实项目的空间（哪怕名字和这个任务的项目一样），手顺点进去继续聊天比专门打开一个新文件夹容易得多，模型就会直接改真实项目，Runtime 完全看不到也验证不了。正确操作是在 WorkBuddy 里点「新建任务」→「选择工作空间」→「打开本地文件夹」，专门选中这个 `workspace/`；生成的 `README.md` 把这步写成具体菜单路径，并点名不要点的那个空间叫什么。**菜单名按 WorkBuddy 桌面版 2026-10 的界面记录，GUI 改版后可能不同**——认准「打开本地文件夹」这个动作即可；Runtime 不承诺 `open -a` 能启动或切换应用，也不会替你点这些菜单。
  **2026-11-07 重跑**：用 WorkBuddy 5.9.3 在 macOS 15.6.1 上按同一路径实际做了一次 R2 交接：新建任务 → 选择工作空间 → 打开本地文件夹 → 选中导出的 `workspace/` → 粘贴 `PROMPT.md` → 完成 → `par task import`。Kernel 自己跑 `unit` 检查通过（`MEASURED · boundary CLEAN · checks PASSED (1/1)`，1 文件 +1 −1，0 违规），任务停在 `REVIEW_REQUIRED`，没有自动接受、没有应用到项目。证据：[reports/WORKBUDDY_REAL_HANDOFF_20261107.md](reports/WORKBUDDY_REAL_HANDOFF_20261107.md)。
  **Cursor / Windsurf / Cline / Aider（2026-11-02 新增，机制和 WorkBuddy/TRAE 完全一样，还没有真实交接验证过）**：这几个工具没有做过一次真实交接，所以没有像上面那样的实测教训——可能也有类似「侧边栏里已有的会话比新开一个文件夹更顺手」的陷阱，第一次用时按本节开头那句话多留意：**一定要在应用里专门打开 `workspace/` 这个新文件夹，不要在任何已经开着的、指向真实项目的会话/窗口里继续**。`.cursor/`、`.windsurf/`、`.cline/` 这类应用自己写的设置目录已经在导入时的噪声忽略名单里（见下方「导入」）。
- `PROMPT.md` 是 Kernel 给 Worker 的同一份提示词，外加两件只与人工交接有关的事：最前面的 `## Before you start`（先打开 `.par-handoff.json` 核对 task_id，不对就停手，不许回退到已经开着的项目）和最后的 `## Environment`（你在桌面应用里：只改允许的路径、不要 git、不要装依赖、不要声称跑过检查）。中间仍是具体目标、允许路径、验收标准、Kernel 会怎么检查、要交付什么。 **README 不再和 PROMPT 互相抄步骤**：菜单和数据去向在 README，工作目标和边界在 PROMPT。
- `manifest.json` 记录模式、基线、允许路径、提示词 sha256、导出编号。事件 `HANDOFF_EXPORTED`；不启动任何程序，不读任何凭据。

### 导入（`import`）做什么
- 恰好一种输入：`--from` 文件夹、`--patch` 补丁、`--text` 文字答案。文字答案只用于只读/文字任务，写任务必须给文件或补丁；R0 文字任务连文件夹都不收。
- 文件夹先做**三层核对**，任何一层不符都点名拒绝，任务保持原状、不产生尝试：
  1. `.par-handoff.json` 的 `task_id` 与 `export` 必须与台账里的导出事件一致（伪造的 task/export 是 `wrong_folder`）。
  2. 标记里的 `base` 必须等于任务当前的基线（不同就是 `stale_base`：项目在导出后换过基线，或这是旧包）。
  3. 包内 `manifest.json` 的 task/export/base/workspace 必须与台账和标记一致，并且传入路径的 `realpath` **就是**这个包导出的 `workspace/` 本身（复制到别处、搬过家的是 `stale_copy`）。缺 `manifest.json` 或来自更旧版本的包是 `old_packet`，明确要求重新导出，不猜也不“转换”。
  - 已经有更新的导出包时，旧包会被判为 `stale_export`，不会悄悄覆盖新版本。确实是在旧包里做的，可以把改动做成补丁用 `--patch` 交回（补丁按当前基线应用）。
- 文件夹再做**私有快照**：不跟随任何链接；含特殊文件（管道/套接字/设备）、疑似敏感文件（不读内容）、总量超过 1 GiB → 直接拒绝。快照里去掉标记文件。
- 噪声规则说清楚：应用自己生成的设置目录（`.trae/`、`.workbuddy/`、`.codebuddy/`、`.vscode/`、`.idea/`、`.cursor/`、`.windsurf/`、`.cline/`……）下**新增**的文件按“噪声”忽略；这些目录里**原本就有、被修改或删除的文件照常判越界**。忽略规则只对新增文件生效，不会给已有文件打掩护。
- 补丁：先检查没有碰受保护路径（`.git`、`.ai/`、`AGENTS.md`、敏感路径），再 `--check` 后应用到干净副本。
- 然后和任何 Worker 的输出一样：逐文件对比基线 → 越界或异常删除即失败 → Kernel 自己运行合同里的检查 → 通过才是 `REVIEW_REQUIRED`。回来的结果里“测试通过了”、`RESULT.md` 里写“PASS”这类说法**不算数**。
- **不监视、不猜结束**：Runtime 不会盯着这个文件夹，也不会从文件变化推断应用是否做完；只有你运行 `par task import` 时才读取结果。失败可以再来一次：每次 import 是独立的一次尝试，修好后重新 import 会新建下一次尝试，旧尝试的 `prompt.txt`、`changes.json`、`work.patch` 原样保留。
- 记录：尝试的 `provider = manual_handoff`、`worker = 你给的应用名`、`model = manual`；`prompt.txt` 就是你当时拿到的 `PROMPT.md`；事件 `HANDOFF_IMPORTED`；**不产生 Provider 健康记录**（人不是 Provider）。
- 任务可以是 `QUEUED` / `PAUSED`（自动恢复）/ 失败类状态（自动按一次 retry 处理）；已经在 `REVIEW_REQUIRED` 的任务不再接收第二份结果。若 retry 期间项目基线变了，导入会在开始前以 `stale_base` 拒绝，要求按新基线重新导出。

### 数据去向（请你自己判断）
你在这些应用里打开 `workspace/`，应用会把其中的文件内容发给它自己的云端模型服务。WorkBuddy、TRAE 两家官方文档对这件事的说法见 `docs/reports/`（调研）：WorkBuddy 隐私政策写明输入会交给第三方大模型推理、默认不用于训练；TRAE 本地模式下代码如何处理，官方页面没有找到明确说法。Cursor、Windsurf、Cline、Aider 没有做过同样的调研——它们各自的数据处理政策请你自己查证。**这是你的决定，Runtime 只做到：不放敏感文件、不让应用碰真实项目。**

### 为什么不自动化
官方资料里 WorkBuddy 桌面端和 TRAE 桌面端都没有可用于“提交任务并取回结果”的命令行或接口；WorkBuddy 的使用条款还限制机器人/自动化调用。Cursor、Windsurf、Cline 同样是 IDE/编辑器扩展，没有脚本化接口；Aider 本身是命令行工具，理论上可以脚本化，但那是一个真正的自动化适配器（单独的 provider 代码、沙箱、鉴权、拿真实安装测过才能做得对），不是这条人工交接路径——这里的四个名字都按 `manual_only` 处理，和 WorkBuddy/TRAE 用的是同一套机制，没有区别对待。所以按计划标为 `manual_only`：Runtime 不启动、不驱动这些应用。若以后要自动化，应改接它们各自官方的命令行产品（见 `docs/reports/` 里的调研），那是单独的决定。

## 3. 主控交接（takeover）

Claude 会话没了（额度用完、断线）而你想让 Codex 接着当主控时：

最省事的做法是 **`par coordinator use codex`**（写接管包并确认，一步完成，见 [COORDINATORS.md](COORDINATORS.md)）；下面是它背后的两步，需要单独做时才用：

```bash
par takeover export            # 写 <数据目录>/takeover/tk-…/takeover.md 与 takeover.json
# 把 takeover.md 交给新的主控（例如在 Codex 里加载）；它只读事实，不会重放任何对话
par takeover confirm tk-… --as codex      # 你确认：从现在起由它主控（codex_manual_takeover 是它早先的名字，同样可用）
par takeover status
```

- 接管包只有 **Kernel 量到的事实**：任务树（状态、风险、依赖、尝试次数、最近一次验证结果、待决批准）、哪些已验收（`finished`：**不要再跑**）、哪些在等你决定、以及项目自己的 Harness 文件的**路径与哈希**（让新主控去读原文件，而不是复制内容）。没有对话，没有 Worker 的话，没有隐藏推理。
- 只有**最新**的包能被确认，且只能确认一次；确认记一条事件，之后 `submit_task` 这类“主控发起”的动作如果来自被替换的主控（MCP 桥带着自己的名字）会被拒绝。**你自己（`par task …`）永远不被拦。**
- 切换主控不会自动重跑任何已验收的任务；批准/验收（2026-10-20 起）在任一主控自己的桥上都一样可用，各自记各自的 `by="coordinator"`。

## 4. 安全小结
- 接力和导入的补丁路径都核对合同允许的路径；任何一项不符合就拒绝。
- 导入的文件夹/补丁/文字全是**不可信输出**：限大小、不跟链接、不读敏感文件、不信其中的说法。
- 交接包是任务级的临时文件，**不是项目的 STATE**，不会被写进 Harness。
- 没有任何东西被应用到真实项目；`accept` 只是记录你接受了这份结果。
