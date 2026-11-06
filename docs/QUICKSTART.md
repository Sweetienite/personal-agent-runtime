# 快速开始（陌生人首次安装，2026-11-06 实跑记录）

这份文档假设你**没有**作者的工作环境：没有 Harness Core、没有 API key、没有订阅登录、没有预先写好的配置。所有命令都在一个全新的 clone 目录里跑过一遍，结果记录在文末。**零账号阶段没有任何模型调用、没有网络请求、没有凭据读取。**

## 0. 环境

| 项 | 本次实跑的值 |
|---|---|
| OS | macOS 15.6.1 (24G90), arm64 |
| Python | 3.13.12（`python3 -m venv`；项目要求 ≥ 3.11，日常验证最多的是 3.12） |
| clone | `git clone https://github.com/Sweetienite/personal-agent-runtime.git`，HEAD `fe3270d` |
| 依赖 | 零第三方包，`pip install -e .` 即可 |
| 数据目录 | **`/tmp/par_fresh_data`**（通过 `PAR_DATA_DIR` 显式隔离，见下方「重要」） |

> **重要：一定要设置 `PAR_DATA_DIR` 指向一个空目录。** 默认数据目录是
> `~/Library/Application Support/PersonalAgentRuntime`（macOS）。如果你机器上已经有 PAR 的配置，
> `par doctor` 会读到它，而演练想要的是「内置保守默认值：所有外部 Provider 都关闭」。
> 本次实跑不带这个变量时，`doctor` 读到作者已有的旧配置并报出一项 FAIL；设置后变成 **5 PASS / 0 FAIL**。
> 这不是项目 bug，而是「首次演练要用隔离目录」的原因。
>
> 另一个环境相关的提醒：某些桌面 Agent 会向 `PYTHONPATH` 注入 `sitecustomize`，
> 可能把 `mkdir(..., exist_ok=True)` 变成 `FileExistsError`。本次实跑遇到时，
> 用 `env -u PYTHONPATH ...` 清掉注入即恢复正常。普通终端不会有这个变量；遇到时先 `echo $PYTHONPATH` 检查。

## 1. 安装与只读体检（零账号）

```bash
git clone https://github.com/Sweetienite/personal-agent-runtime.git
cd personal-agent-runtime
python3 -m venv .venv && .venv/bin/pip install -e .     # 或 uv venv --python 3.12 .venv && uv pip install -e .
export PAR_DATA_DIR=/tmp/par_fresh_data                  # 换成你自己的空目录
.venv/bin/par init                                       # 创建 0700 的数据目录
.venv/bin/par doctor                                     # 期望 PASS 5 | WARN 0 | FAIL 0
.venv/bin/par version                                    # par 1.0.0 (phase RELEASE)
```

`doctor` 此时应报告：`config: no user config; built-in conservative defaults in use (every external provider disabled)`、
`projects: none registered`。它不启动任何外部程序、不联网。

## 2. 看一遍全流程（假 CLI + 一次性夹具，无模型无额度）

```bash
python3 -B tests/tools/smoke_codex.py --self-test
python3 -B tests/tools/smoke_agy.py --self-test
```

`smoke_codex.py --self-test` 用**真实适配器代码 + 假的 codex 程序 + 一次性夹具项目**跑完
submit → approve → run → Kernel 验证 → accept 的全流程（含一次 R1 与一次 R2）。本次实跑输出：

- `fixture-0001` / `fixture-0002` 各产生结构化厂商事件（`turn.started`、`item.completed`、`turn.completed`）；
- `fixture working tree: clean (nothing applied)`、`real data directory unchanged: True`、`Codex config.toml unchanged: True`；
- `self-test used a fake codex: 2 exec call(s), no model, no network`。

**这证明全流程能跑，但不代表任何真实模型被验证过。** 真实 Provider 的状态见 [COMPATIBILITY.md](COMPATIBILITY.md)。

## 3. 登记一个普通 Git 仓库（不需要 AGENTS.md / STATE.md）

```bash
mkdir -p /tmp/myrepo && cd /tmp/myrepo && git init -q -b main
printf '# demo\n' > README.md
printf 'def add(a, b):\n    return a - b\n' > calc.py
git add -A && git -c user.name=demo -c user.email=demo@example.invalid commit -qm init
cd -  # 回到 personal-agent-runtime
.venv/bin/par project add myrepo /tmp/myrepo
.venv/bin/par project show myrepo
```

本次实跑结果：`registered demo: /tmp/par_fresh_project (vcs auto; context generic; writes disabled)`；
`project show` 显示 `Context generic (README/AGENTS/CLAUDE + Git facts; no parent contract)`、
`Writes BLOCKED - WRITE_DISABLED`。**默认不写项目里任何文件**（`--scaffold-harness` 是显式开关，且只在 Harness 模式下用）。

想沿用旧的 Harness 规则才需要 `--context-mode harness`；细节见 [CONTEXT.md](CONTEXT.md)。

## 4. 用草案编译器提交一个任务（可选）

```bash
.venv/bin/par task draft --draft draft.json     # 只打印严格合同，不提交；缺路径/检查时列问题并退出 2
.venv/bin/par task submit --contract task.json  # 校验并入队；写任务会停在 PERMISSION_WAIT 等你批准
.venv/bin/par task status T
```

`par fixture create` 会造一个「故意有 bug、无个人数据」的夹具项目并登记为可写，适合第一次试 R2。
注意夹具项目**有一份 active 主合同**（`FIXTURE-1.md`），所以它的任务用 `ad_hoc = false`；
generic 项目没有父合同，用 `ad_hoc = true`。两种模式的规则见 [CONTEXT.md](CONTEXT.md) 与
[docs/CONTEXT.md 的父合同规则](CONTEXT.md)。本次实跑用 `ad_hoc=true` 提交夹具任务时被明确拒绝
（`this project has an active contract: submit under it`），改成 `false` 后 `submitted fixture-0001 QUEUED`——
这是 Harness 模式在按设计工作。

## 5. 手工交接给 GUI-only 工具（可选）

```bash
.venv/bin/par task handoff T --to workbuddy --json
```

本次实跑输出 `mode: text`（只读任务）、`workspace_absolute_path`、`prompt_path`、`readme_path`、
`manifest_path`、`base_commit`、`allowed_paths` 和 4 条 `next_actions`，任务变成 `PAUSED`。
把 `PROMPT.md` 全文粘给应用，把答案存成文件后 `par task import T --text FILE` 带回，
由 Kernel 按普通 Worker 的标准验证。**打开错目录会被导入时的 marker 核对点名拒绝**，细节见 [HANDOFF.md](HANDOFF.md)。

## 6. 真实 Provider 之前先看这里

- 默认**没有任何适配器**。只有配置里启用 `codex` / `antigravity` 并写明绝对路径的 `executable` 才会注册。
- Google Antigravity 官方 FAQ 明确禁止把 `agy` 封装在第三方编码 Agent/应用中；Gemini CLI 自 2026-06-18 起
  停止为免费层 / AI Pro / Ultra 用户服务。**技术可行 ≠ 条款许可**，见 [COMPATIBILITY.md](COMPATIBILITY.md)。
- 真实项目写入要你亲自 `par project enable-write ID`（先体检、再敲一遍 id 确认），且**每个写任务**仍逐项批准；
  补丁永远不会自动应用到项目，要你自己 `git apply`。见 [SECURITY.md](SECURITY.md)。
- 不提供「强行关掉权限」的排障方案；无法运行时 `par health` / `doctor` 会给出原因。
- 真实 Codex 若报告 `sandbox_apply: Operation not permitted`、读不到任何文件，先跑
  `python -B tests/tools/sandbox_probe.py --json`：它会告诉你宿主能否创建**嵌套** macOS 沙箱，
  以及进程是否本来就继承了宿主沙箱。有些 Agent 宿主本身已在沙箱内，这时换普通终端 /
  未被沙箱化的 runner，或直接 `python -B tests/tools/codex_revalidate.py --check-only` 一键复核，
  与适配器无关；**不要**用 `--dangerously-bypass-approvals-and-sandbox` 之类的提权参数让写测试「通过」。
  2026-11-06 的两次实测见 [第一部分](reports/CODEX_REAL_REVALIDATION_20261106.md) 与
  [第二部分（成功的真实 R1/R2 及其限定条件）](reports/CODEX_REVALIDATION_PART2_20261106.md)。
  当前 `TESTED_VERSION` 为 `0.166.0-alpha.6`，但那是带限定的 `GREEN (scoped)`：
  默认沙箱模式仍需在允许嵌套沙箱的普通终端用 `codex_revalidate.py --yes-use-quota --model <slug>` 复核。

## 7. 测试与贡献

```bash
python3 -B tests/tools/parallel_tests.py       # 全量，按模块并行（本次 70 模块 / 1558 项）
python3 -B tests/tools/mutation_check.py M70   # 单个变异检查；全部约 250 条
```

CI 只跑三个明确命名的作业（`smoke_install`、`core_unittests` 在 macOS，`linux_security_core` 在 Ubuntu），
**不是全量套件**；见 [CONTRIBUTING.md](../CONTRIBUTING.md)。贡献前先读 [AGENTS.md](../AGENTS.md)。

## 本次演练的实际结果（可核对）

| 步骤 | 结果 |
|---|---|
| `git clone` → `python3 -m venv` → `pip install -e .` | 成功；`par version` = `1.0.0 (phase RELEASE)` |
| `par init` (PAR_DATA_DIR=/tmp/par_fresh_data) | `created data directory ... (mode 0700)` |
| `par doctor` | `PASS 5 \| WARN 0 \| FAIL 0 \| INFO 8`，全部 Provider disabled |
| `smoke_codex.py --self-test` | exit 0；R1+R2 全流程；真实项目/数据目录/Codex 配置均未改动；no model, no network |
| `par project add` 普通 Git 仓库 | generic 模式登记成功，写入开关关闭 |
| `par fixture create` | 生成夹具项目与 active 主合同 |
| `par task submit`（夹具 R1） | `ad_hoc=true` 被拒 → `ad_hoc=false` 成功入队 `QUEUED` |
| `par task handoff --to workbuddy --json` | 输出 mode / 绝对路径 / marker / next_actions，任务 `PAUSED` |
| 未验证 | 任何真实模型调用、真实 Provider、Linux/Windows 上的真实适配器、真实项目的写入应用 |

> 注：`docs/reports/` 里的历史验收报告是**当时那个版本的历史快照**，不是当前版本的支持保证；
> 当前证据以 `docs/COMPATIBILITY.md`、`docs/SECURITY.md` 与 CI 日志为准。
