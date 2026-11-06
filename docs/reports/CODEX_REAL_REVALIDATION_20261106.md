# Codex 真实复核记录（2026-11-06，T14 解锁尝试）

> 本文是**当前版本的实测证据**，不是历史快照。它记录用户授权解锁 T14 后，用
> **官方允许的 ChatGPT 订阅 + `codex exec` 路径**在非敏感夹具上做的真实 R1/R2 尝试，
> 以及为什么这次尝试**没有取得 `GREEN`**。
> 关联：[COMPATIBILITY.md](../COMPATIBILITY.md) · [CODEX.md](../CODEX.md) §12 ·
> [ADAPTERS.md](../ADAPTERS.md) · [RELEASE_NOTES_2026-11.md](../RELEASE_NOTES_2026-11.md)。

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| 是否解锁了 T14（新增正式自动 Provider）？ | **否**。按用户明确选定的路线，T14 收口为「**不新增自动 Provider**」；没有创建空适配器，也没有把 YELLOW 写成已支持。 |
| 是否跑了真实 R1/R2？ | **是**，2 次真实模型调用，收到真实厂商结构化事件与用量计数。 |
| 是否取得 `GREEN`？ | **否**。R1 收到干净终态但 Worker 自述读不到任何文件；R2 被 Kernel 自己的检查判定 `FAILED`，**没有成功的真实 R2**。 |
| 是 PAR 的适配器坏了吗？ | **不是**。根因是本宿主 shell 已在 macOS 沙箱内，CLI 无法再创建嵌套 Seatbelt 沙箱；用一条与 PAR 无关的命令即可复现。 |
| 有没有用全权限捷径让测试通过？ | **没有**。没有使用 `--dangerously-bypass-approvals-and-sandbox`、`--yolo` 或任何提权参数。 |
| 打 tag / 发 Release 了吗？ | **没有**，用户明确要求「先不打 tag」。 |

**安全上值得肯定的是失败本身**：R2 的厂商终态是 `SUCCESS`、退出码 0、边界 0 违规，
Kernel 仍因 `unit` 检查失败把任务判为 `FAILED`，没有产出补丁、没有应用到项目。
这正是「厂商自述成功不等于验收通过」的实际验证。

## 1. 授权、范围与明确不做的事

用户授权「解锁 T14，继续执行推进，先不打 tag」，并在四条路由中选定
**「用 ChatGPT 订阅复核」**：用现有 `codex exec` 官方脚本/CI 路径做真实 R1/R2/取消/权限复核，
不新增 Provider，T14 收口为「不新增自动 Provider」。

本轮**明确没有做**：

- 没有使用本机个人 Google 账号（`oauth-personal`）。Google 官方公告自 2026-06-18 起
  停止为免费 / Google AI Pro / Ultra 个人账号提供 Gemini CLI 服务；用它属于消费者订阅规避路径。
- 没有接受 Gemma 使用条款，也没有下载约 1GB 的本地模型（`/usr/bin/sandbox-exec` 环境探测后
  判定该路由需要额外的用户同意，未代为同意）。
- 没有读取或复制任何凭据、没有在配置或任务包里放 API key、没有联网调用模型以外的服务。
- 没有创建新的自动 Provider 适配器，没有打 tag、没有创建 Release。

## 2. 实测环境（脱敏）

| 项 | 值 |
|---|---|
| 日期 | 2026-11-06 |
| OS / 架构 | macOS 15.6.1（Darwin 24.6.0）, arm64 |
| Python | 项目 `.venv`，3.12 |
| 可执行文件 | `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`（绝对路径、签名二进制） |
| 签名 | `TeamIdentifier=2DC432GLL2`，`Developer ID Application: OpenAI OpCo, LLC` |
| CLI 版本 | `codex-cli 0.166.0-alpha.6`（**alpha 预览**） |
| 适配器 `TESTED_VERSION` | 仍为 `0.162.0-alpha.17.2`（有意不提高，见 §7） |
| 登录方式 | `codex login status` → `Logged in using ChatGPT`（订阅登录，非 API key、非按量计费） |
| 模型 | `gpt-6-luna`（在该版本的 `debug models` 目录内） |
| 隔离配方复核 | 22 个 feature flag 关闭、MCP servers `node_repl` / `par` 关闭、`memories=false` 复核通过、`notify=[]` |
| 数据目录 | `/tmp/par_t14_real_codex_evidence/data`（一次性私有目录，`PAR_DATA_DIR` 之外显式隔离） |
| 夹具 | `par fixture create` 生成的一次性 Git 仓库，只有一处故意写错的 `add()`，**无个人数据** |

## 3. 实跑命令与结果

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python -B tests/tools/sandbox_probe.py --json` | `nested_sandbox_available: false`，`returncode 71`，`sandbox_apply / Operation not permitted` |
| 2 | `PAR_REAL_CODEX=1 python -B -m unittest tests.integration.test_real_codex.RealCodexOffline -v` | 7 PASS / **1 FAIL**（版本漂移闸门，有意为之，见 §7） |
| 3 | `PAR_REAL_CODEX_TRUST=1 python -B -m unittest tests.integration.test_real_codex.RealCodexTrustEntries -v` | 3 PASS；记录到「可写运行 + 有 `.git` 的工作副本仍会改写 `config.toml` = True」，因此 Worker 副本去掉顶层 `.git` 的设计仍必要 |
| 4 | `python -B tests/tools/smoke_codex.py --yes-use-quota --model gpt-6-luna --write --workdir /tmp/par_t14_real_codex_evidence` | 真实 R1 + R2；R1 `REVIEW_REQUIRED`、R2 `FAILED`；脚本退出码 1（因为 R2 检查失败） |

第 4 步是唯一产生模型调用与额度消耗的步骤，共 **2 次**。

## 4. R1（只读问答）真实记录

`fixture-0001` → `REVIEW_REQUIRED`。证据分层显示
`evidence UNVERIFIED · checks NO_MECHANICAL_CHECK · review NOT_REVIEWED · integration NOT_INTEGRATED · not applied`。

- 厂商事件直方图：`thread.started x1`、`turn.started x1`、`item.completed/agent_message x2`、`turn.completed x1`。
- 用量（厂商自报，非估算、非账单）：input 38,246（cached 12,416）、output 504（reasoning 259）。
- Worker 自述（**未验证**）：「I couldn't determine what `add(2, 3)` returns … because the sandbox
  blocked both read attempts; no files were changed」。
- 变更：0 文件；边界 0 违规；`model_observed: null`。

**发现的问题**：只读任务没有配置任何机械检查，终态干净就进入 `REVIEW_REQUIRED`，
但当时的状态行写的是 `the Worker finished and every check passed`。
「每个检查都通过」在零检查时是假陈述，且会掩盖上面这段自述失败。

已修正：

- `src/par/core/attempt.py`、`src/par/core/kernel.py`：零检查时改为
  `produced a clean terminal event; no Kernel checks ran - review the evidence before accepting`；
  有检查时明确写出实际通过了几项（`N Kernel check(s) passed`）。
- `tests/unit/test_kernel_run.py` 增加两条回归：只读成功不得再出现「every check passed」；
  写入成功必须报出实际检查数。

## 5. R2（限定写）真实记录

`fixture-0002` → `FAILED`，`failure_class EXECUTION_FAILED @ validation`，
`terminal_state SUCCESS`、退出码 0，但 Kernel 检查 `unit` **FAIL**。

- `check_unit.log`：`test_adds_two_numbers` 期望 5 得到 -1、`test_adds_negative_numbers` 期望 -3 得到 1；
  `Ran 2 tests ... FAILED (failures=2)`。
- 用量：input 87,128（cached 68,736）、output 650（reasoning 321）。
- Worker 自述（**未验证**）：「shell access was blocked with `sandbox_apply: Operation not permitted`,
  including when I specified the workspace path. I did not change any files or run the tests.」
- `result.json`：`diff_artifact: null`、`diff_stat: 0 files`、`boundary: 0 violations`、
  `summary_verified: false`、`handoff_usable: false`。
- `par task diff` 输出 `this attempt produced no patch`。

**结论**：Kernel 没有把厂商的 `SUCCESS` 终态当成验收通过，也没有自动换 Worker 提权重试。
这次失败是**正确的失败**，但同时也意味着本轮**没有成功的真实 R2**，不能记 `GREEN`。

## 6. 根因：宿主沙箱，不是适配器

独立复现（与 PAR 完全无关）：

```bash
printf '(version 1)\n(allow default)\n' > /tmp/par_seatbelt_probe.sb
/usr/bin/sandbox-exec -f /tmp/par_seatbelt_probe.sb /bin/echo par-sandbox-ok
# sandbox-exec: sandbox_apply: Operation not permitted   (exit 71)
```

本 Agent 的命令执行环境本身已在 macOS 沙箱内，macOS 不允许再创建嵌套 Seatbelt 沙箱。
Codex 的 `read-only` / `workspace-write` 都依赖它，因此模型侧所有文件访问被拒绝。
这解释了 R1/R2 的表现，也说明**换适配器、换参数都解决不了**。

为把这类环境限制和「Provider 不兼容」区分开，新增
`tests/tools/sandbox_probe.py`（+ `tests/unit/test_sandbox_probe.py`）：
只问 OS 能否进入一个 allow-all 的嵌套沙箱，不调用模型、不联网、不读凭据、不安装任何东西，
非 macOS 明确报 `not applicable`，原始命令输出不写入证据记录。

**严禁的捷径**：该版本 `codex exec --help` 提供
`--dangerously-bypass-approvals-and-sandbox`。本轮**没有**使用它；用它可以让 R2 看起来通过，
但会同时丢掉沙箱与审批，正是 [ADAPTERS.md](../ADAPTERS.md) 与 T14 任务卡禁止的
「用全权限捷径让写测试通过」。

## 7. 版本漂移与隔离配方（离线复核）

`PAR_REAL_CODEX=1` 的 8 项离线检查中 7 项 PASS，1 项 FAIL：

- PASS：OpenAI 签名、wrapper 脚本被拒、所有适配器参数仍被该版本接受、未知 feature 名会报错、
  `memories` 复核为 false、隔离配方确实改变了发给模型的内容、构造出的 argv 被参数解析器接受。
- **FAIL（有意的闸门）**：`test_the_probe_sees_a_ready_install` 报
  `Codex is 0.166.0-alpha.6, not the version this adapter was checked against (0.162.0-alpha.17.2)`。
  这不是 flaky 测试：版本升级不得静默继承旧版本的「已验证」标签。
  因为本轮真实 R2 未通过，`TESTED_VERSION` **保持旧值**，`par health --probe` 会继续提示
  `untested version`。这是刻意的保守行为，不为了「看起来全绿」而放宽。

隔离配方测试也做了版本适配（不是放宽，是改准）：

- 旧断言 `isolated.count("plugin") == 0` 在 0.166.0-alpha.6 产生**假失败**：
  该版本的静态系统说明文字里仍会出现「plugin」一词，但它不是工具定义。
- 新断言改为结构化核对：plain 中确有 `mcp__cua_repl` / `js` / `js_reset` 工具定义，
  isolated 中这些定义全部消失、无任何 `mcp__` 前缀工具、无 `<recommended_plugins>` 段、
  无原生应用/浏览器控制工具描述，且 isolated 严格更短。
- **如实记录的残留**：即使 `--disable multi_agent`，该版本的基础工具列表仍含
  `spawn_agent` / `list_agents`。测试会打印隔离后实际可见的工具名，
  文档**不宣称**「子代理已验证关闭」；这列为后续复核项。

### 本轮代码与测试改动清单

| 文件 | 改动 |
|---|---|
| `src/par/core/attempt.py`、`src/par/core/kernel.py` | 零检查时不再谎称「每个检查都通过」；有检查时写出实际数量 |
| `tests/unit/test_kernel_run.py` | 两条回归（只读零检查 / 写入有检查） |
| `tests/tools/mutation_check.py` | 新增 `M673`、`M674`：把状态行改回旧文案时必须被测试杀死；实跑均 `KILLED` |
| `tests/integration/test_real_codex.py` | 隔离断言改为结构化核对工具定义；版本漂移闸门的失败信息更明确 |
| `tests/tools/sandbox_probe.py`、`tests/unit/test_sandbox_probe.py` | 新增：离线探测宿主能否创建嵌套 OS 沙箱（5 项单测） |
| 文档 | `COMPATIBILITY.md`、`CODEX.md` §12–13、`ADAPTERS.md`、`QUICKSTART.md`、`MODES.md`、`RELEASE_NOTES_2026-11.md`、`CHANGELOG.md`、`README.en.md`、`reports/README.md` |

本地全量套件：**1552 项 / 69 模块**，与未修改的 `e1f4556` 基线相比**失败集合完全相同**
（9 failures + 1 error，全部是沙箱内无法向子进程组投递信号 / 取不到 `proc_identity` 的
4 个模块；见 §9）。`python -B tests/tools/mutation_check.py --patterns` 显示
331/338 条模式存在，7 条 `PATTERN NOT FOUND` 为本轮之前就存在的历史重构残留，与本轮改动无关。

## 8. 副作用核对

| 检查 | 结果 |
|---|---|
| 夹具工作树 | `clean (nothing applied)`；补丁永远不自动应用 |
| 真实数据目录 | `unchanged: True` |
| Codex `config.toml` | `unchanged: True` |
| `~/.codex`（名称与大小，不读内容） | added 0 / removed 0 / modified 3：`logs_2.sqlite`、`logs_2.sqlite-wal`、`models_cache.json`（CLI 自身留存，非凭据复制） |
| 仓库 / 任务包 | 无 HOME、secret、工具配置被复制进去；证据全在一次性私有 `/tmp` 目录，本文已脱敏 |
| `.git` 工作副本 | trust 测试确认「可写 + 有 .git」仍会改写 CLI 自己的 `config.toml`，Worker 副本继续去掉顶层 `.git` |

## 9. 仍然缺的证据（更新后的解锁条件）

1. **成功的真实 R2**：需要在允许创建嵌套沙箱的宿主（普通终端 / 未被沙箱化的 CI runner）重跑
   [CODEX.md](../CODEX.md) §12 的 1–3 步；只有那时才能把 `TESTED_VERSION` 提到
   `0.166.0-alpha.6` 并消除 `untested version`。
2. **取消 / 超时 / 断线 / 输出洪流、认证失败、额度与限流分类**的真实样本：
   本轮真实路径未覆盖（不为耗尽额度而人为造额度事故）。目前仍由 fake CLI 场景与
   `tests/unit/test_codex_adapter.py` / `test_codex_kernel.py` / `test_codex_real_samples.py`
   的可控样本覆盖，**不能**写成真实 Provider 已验证。
3. **一个新 Provider 的 `GREEN`**：本机 Gemini CLI 只有个人账号（官方已停服），
   没有付费 Gemini API key / 企业许可 / 服务账号；Claude Code CLI、OpenCode、ACP agent 均未安装。
   因此 T14 仍无 GREEN 候选。
4. **平台证据**：Linux 仍 `CORE_EXPERIMENTAL`、Windows 仍 `STATIC_GAP`。
5. 打 tag / 发 Release 仍需用户单独批准；本轮**没有执行**。

## 10. 与 Issue #16 / T14 的关系

Issue #16 原本是 `BLOCKED_BY_EVIDENCE`（T13 静态研究无 GREEN）。本轮用户已作出**范围决策**：
用官方允许的既有 Codex 路径做复核，**不新增自动 Provider**。
因此 T14 的结论从「等待一个尚未出现的 GREEN 候选」变成
「**经真实尝试后明确不新增**」：

- 没有 GREEN，也没有「看起来支持但不会跑」的空适配器；
- 没有把 YELLOW / RED 写成已支持；
- 没有宣称自动 Supervisor、同项目多 Worker 并行或自动真实仓库写入；
- 真实复核的阻塞原因（宿主嵌套沙箱）单独留档，供换宿主后继续。

**本文不声称 P3 自动执行 Gate 已通过。**
