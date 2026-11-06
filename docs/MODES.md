# 协作模式：谁是主控、谁是 Worker、靠什么接口启动（2026-11）

三个概念互相独立，不绑定厂商，也没有「Solo 模式」这个新产品形态。

| 概念 | 由什么决定 | 可以是什么 |
|---|---|---|
| **主控（coordinator）** | `profiles.toml` 的 `coordinator`；你用 `par coordinator use NAME` 切换 | 一个通过 MCP 桥对话的模型（Claude Desktop/Code、Codex），或**你本人直接敲 CLI** |
| **Worker** | 合同的 `role` → profile 里的 primary/fallback 链，或显式 `worker` | 自动 CLI（默认只有 Codex）、实验性的 `agy`、或**手工交接**给 GUI 工具 |
| **接口（surface）** | provider 的 `kind` 派生，不是配置项 | `native_cli`（自动）、`manual`（人工交接）、`programmatic_session`（实验描述符）、`acp`（仅描述符，未实现） |

**同一个软件可以同时是主控和 Worker**（例如 Codex 两侧都出现），不需要第二个账号：
Kernel 给每次 attempt 新建私有 HOME/TMPDIR 与干净环境，不继承主控的 MCP 配置或凭据。
角色/能力的实现见 `src/par/config.py`、`src/par/coordinators.py`、`src/par/core/router.py`，
回归证据在 `tests/unit/test_role_provider_caps.py`。

## 三种协作方式

### A. CLI 自动（默认禁用，需你显式配置）

```
你 / 主控 --(MCP 桥或 par 命令)--> Kernel --> 私有克隆 --> 自动 Worker --> Kernel 自己验收 --> 待你审阅
```

- 适配器注册表默认为空；只有配置启用 `codex` 或 `antigravity` 且写明绝对路径的 `executable` 才注册。
- 任务在 `PERMISSION_WAIT` 等逐项批准，`run` 只启动一次 attempt；Kernel 自己比对文件、跑检查、生成补丁。
- 能力闸门：适配器必须静态声明 `headless=yes`，R2 还需 `write=yes`；Kernel 在运行前用真实实例复核。
- Codex 的 `codex exec --json` 是官方支持的非交互接口；`agy` 因官方条款风险保持实验性、默认禁用。

### B. 协议自动（研究 / 未实现）

ACP、OpenCode 等协议/工具有官方 headless 接口，但 **PAR 本轮没有实现、没有 GREEN 候选**。
「协议标准存在」不等于「PAR 支持」；版本、账号、条款与未测项见 [COMPATIBILITY.md](COMPATIBILITY.md)。
T14 的状态是 `BLOCKED_BY_EVIDENCE`（Issue #16），没有创建空适配器。

### C. 手工接力（通用，已实跑）

```
你 / 主控 --> Kernel --> 导出交接包（提示词 + 私有副本 + 绝对路径 + marker）
          --> 你在 GUI 应用里打开那个文件夹、粘贴提示词
          --> 结果/补丁/文字带回 `par task import`
          --> Kernel 按普通 Worker 的标准验证
```

- `par task handoff T --to APP` 的 `APP` 只是名字（WorkBuddy / TRAE 只是文档里写过实测的两个），
  不依赖任何应用专属分支，也不做 UI 自动化。
- 导入把回来的东西当作**不可信输出**：私有快照、marker 核对（task/export/base/真实导出路径）、
  文件比对、边界、Kernel 自己跑检查。旧包、副本、伪造标记、错目录都被点名拒绝。
- 只读任务用 `--text`，写任务用 `--from DIR` 或 `--patch`。数据去向（应用会把打开的文件发给自己的云端模型）
  写在每个包的 README 里，由你决定。

## 不做什么

- **不新增 Solo 产品模式**：人直接敲 `par` 命令一直是支持的路径，不需要第二个模式。
- **不自动选模型/评分/智能换模型**：primary/fallback 顺序由你的配置固定。
- **不自动 Supervisor、不同项目多 Worker 并行、不自动把补丁写进真实仓库**：`ACCEPTED` 只是记录结果，
  `CANDIDATE_READY` 只是候选整合成功，`APPLIED` 是你自己 `git apply` 之后的事。三者的区别见
  [KERNEL.md](KERNEL.md#候选整合par-candidate) 与 [USER_GUIDE.md 的验收章节](USER_GUIDE.md)。

## 状态词汇对照（避免误以为项目已被修改）

| 词 | 含义 | 真实项目 |
|---|---|---|
| `REVIEW_REQUIRED` | 一次 attempt 结束，Kernel 量到了结果，等你审阅 | 未动 |
| `ACCEPTED` / `CLOSED` | 你接受了结果 / 关闭任务，记在台账 | 未动 |
| `CANDIDATE_VERIFIED` / `integration = CANDIDATE_READY` | 已验收的补丁在 Kernel 自己的仓库里叠加并重跑过检查 | 未动 |
| `APPLIED` | **你自己** `git apply` 之后才成立；Runtime 不设置这个状态 | 由你改动 |

证据分层（`par task result` 的 `evidence_summary`）见 [KERNEL.md](KERNEL.md)；
`applied_to_project` 恒为 `false`，验收不是落地。
