# 历史验收快照（不是当前版本的支持保证）

本目录里的 `P1_*` … `P8_*`、`LEAN_CONSOLE_REPORT_*` 与 `V1_ACCEPTANCE_REPORT_*` 是
**2026-10-14 ～ 10-16 当时那个版本、那台机器、那套账号**的验证记录。它们是开发过程的档案，
用于追溯「当时依据什么做的决定」，**不是**当前版本对任何工具、平台或账号类型的支持承诺。

特别注意：

- 这些报告使用的是**历史阶段编号 P1–P8**，与 2026-11 本轮规划里的 P0/P1/P2/P3 是**两套不同的阶段**，
  不要按编号对应。
- 报告中提到的 `agy` 1.3.5 真实写入烟测、Codex 真实烟测，记录的是当时的技术可行性。
  **技术可行不等于条款许可**：Google Antigravity 官方 FAQ 现已明确禁止把 `agy` 封装在第三方编码
  Agent/应用中；Gemini CLI 自 2026-06-18 起停止为免费层 / Google AI Pro / Ultra 用户服务。
  当前范围以 [../COMPATIBILITY.md](../COMPATIBILITY.md) 为准。
- 报告里可能出现的作者本机绝对路径、ChatGPT.app / Claude.app 特定路径、只限 macOS 的语气，
  都是当时的环境事实，不是陌生用户必须复现的步骤。

**当前版本的证据入口**：

| 想确认什么 | 看哪里 |
|---|---|
| 某个外部工具/协议能否作为官方支持路径 | [../COMPATIBILITY.md](../COMPATIBILITY.md)（证据等级、版本、OS、账号、未测项） |
| Runtime 保护什么、不保证什么、平台分级 | [../SECURITY.md](../SECURITY.md) |
| 陌生人第一次怎么装、怎么零账号跑通 | [../QUICKSTART.md](../QUICKSTART.md)（含 2026-11-06 实跑记录） |
| 主控/Worker/接口三种角色与状态词汇 | [../MODES.md](../MODES.md) |
| CI 实际覆盖哪些回归 | [../../CONTRIBUTING.md](../../CONTRIBUTING.md) 与 [../../.github/workflows/tests.yml](../../.github/workflows/tests.yml) |
| 面向用户的变化摘要 | [../../CHANGELOG.md](../../CHANGELOG.md) |

本轮（2026-11）没有新增正式自动 Provider；T14 标记为 `BLOCKED_BY_EVIDENCE`（Issue #16），
没有 GREEN 候选，也没有创建空适配器。
