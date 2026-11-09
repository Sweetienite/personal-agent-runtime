# 中文文档索引

## 先看这些

| 文档 | 适合谁 |
|---|---|
| [README.zh-CN.md](../README.zh-CN.md) | 第一次了解：定位、三种接入、演示、限制 |
| [QUICKSTART.md](QUICKSTART.md) | 陌生人首次安装：隔离数据目录、零账号烟测、普通 Git 仓库、人工交接 |
| [USER_GUIDE.md](USER_GUIDE.md) | 每天怎么用：审批、结果、候选、重试、失败恢复 |
| [OPERATIONS.md](OPERATIONS.md) | 命令全集、退出码、排障 |
| [SECURITY.md](SECURITY.md) | 安全边界、审批模式、租约、检查执行、隐私 |
| [COMPATIBILITY.md](COMPATIBILITY.md) | 兼容性证据矩阵与未测项 |

## 理解设计

| 文档 | 内容 |
|---|---|
| [KERNEL.md](KERNEL.md) | 状态机、任务合同、台账、一次运行的完整流程 |
| [MODES.md](MODES.md) | 主控/Worker/接口三种角色与状态词汇 |
| [MCP.md](MCP.md) | MCP 桥：21 个工具、租约与动作策略、永远不能做什么 |
| [HANDOFF.md](HANDOFF.md) | 接力、人工交接、主控交接 |
| [COORDINATORS.md](COORDINATORS.md) | 主控接入、grant、切换 |
| [CONTEXT.md](CONTEXT.md) | generic 与 Harness 上下文 |
| [CODEX.md](CODEX.md) · [AGY.md](AGY.md) · [ADAPTERS.md](ADAPTERS.md) | 适配器细节、版本与证据 |
| [ROLLBACK.md](ROLLBACK.md) | 非破坏性回滚与恢复演练 |
| [specs/](specs/) | 模块级精确契约 |

## 项目与发布

| 文档 | 内容 |
|---|---|
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 开发、测试、变异检查、PR 约束 |
| [SUPPORT.md](../SUPPORT.md) | 提问渠道与诊断信息 |
| [.github/SECURITY.md](../.github/SECURITY.md) | 私下报告安全问题 |
| [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | 社区行为约定 |
| [CHANGELOG.md](../CHANGELOG.md) | 面向用户的变化摘要 |
| [RELEASE_NOTES_2026-11.md](RELEASE_NOTES_2026-11.md) | 发行审查与已知限制 |
| [reports/](reports/) | 带日期的历史证据，不是当前支持保证 |
| [product-capabilities.json](product-capabilities.json) | 机器可读的能力事实源 |

英文入门材料从 [docs/en/GETTING_STARTED.md](en/GETTING_STARTED.md) 开始，全部英文入口见
[docs/INDEX.md](INDEX.md)。两种语言的核心事实由 `tests/tools/docs_contract.py` 对照
`product-capabilities.json` 检查。
