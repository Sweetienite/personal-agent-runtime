# 快速开始（陌生人首次安装，2026-11-09 复测）

这份文档假设你**没有**作者的工作环境：没有 Harness Core、没有 API key、没有订阅登录、没有预先写好的配置。下面的命令在 2026-11-09 的当前分支上重新跑过；历史版本的结果保留在文末，不悄悄改写。

**零账号阶段没有任何模型调用、没有网络请求、没有凭据读取。**

## 0. 环境

| 项 | 2026-11-09 复测值 |
|---|---|
| OS | macOS 15.6.1 (24G90), arm64 |
| Python | 3.12（项目 `.venv`；要求 ≥ 3.11） |
| clone | 当前 `release/final-productization` 分支的本地 clone |
| 依赖 | 零第三方包，`pip install -e .` 即可 |
| 数据目录 | 隔离的临时目录（`PAR_DATA_DIR`，见下） |

> **重要：一定要设置 `PAR_DATA_DIR` 指向一个空目录。** 默认数据目录是
> `~/Library/Application Support/PersonalAgentRuntime`（macOS）。如果你机器上已经有 PAR 的配置，
> `par doctor` 会读到它，而演练想要的是「内置保守默认值：所有外部 Provider 都关闭」。
>
> 另一个环境相关的提醒：某些桌面 Agent 会向 `PYTHONPATH` 注入 `sitecustomize`，
> 可能把 `mkdir(..., exist_ok=True)` 变成 `FileExistsError`。遇到时先 `echo $PYTHONPATH` 检查，
> 用 `env -u PYTHONPATH ...` 清掉注入。普通终端不会有这个变量。

## 1. 安装与只读体检（零账号）

```bash
git clone https://github.com/Sweetienite/personal-agent-runtime.git
cd personal-agent-runtime
python3 -m venv .venv && .venv/bin/pip install -e .     # 或 uv venv --python 3.12 .venv && uv pip install -e .
export PAR_DATA_DIR="$(mktemp -d)"                      # 换成你自己的空目录也可以
.venv/bin/par init                                      # 创建 0700 的数据目录
.venv/bin/par doctor                                    # 期望 PASS 5 | WARN 0 | FAIL 0
.venv/bin/par version                                   # par 1.0.0 (phase RELEASE) + release status
```

`par init` 会打印内置默认审批模式 `coordinator_autonomous` 的提示：MCP 主控可在当前租约内批准、拒绝、验收、关闭；要让人独占决定权，请在 `profiles.toml` 里设为 `coordinator_scoped` 或 `human_only`。

`doctor` 此时应报告 `config: no user config; built-in conservative defaults in use (every external provider disabled)`、`projects: none registered`。它不启动任何外部程序、不联网。

## 2. 看一遍全流程（假 CLI + 一次性夹具，无模型无额度）

```bash
.venv/bin/python -B tests/tools/smoke_codex.py --self-test
.venv/bin/python -B tests/tools/smoke_agy.py --self-test
```

`smoke_codex.py --self-test` 用**真实适配器代码 + 假的 codex 程序 + 一次性夹具项目**跑完 submit → approve → run → Kernel 验证 → accept 的全流程（含一次 R1 与一次 R2）。预期会看到项目工作树保持干净、真实数据目录未变、Codex 配置未变，并明确写出 `self-test used a fake codex ... no model, no network`。

**这证明全流程能跑，但不代表任何真实模型被验证过。** 真实 Provider 的状态见 [COMPATIBILITY.md](COMPATIBILITY.md)。

## 3. 零账号的完整人工交接（不需要 Provider）

这是不安装任何模型、也不需要订阅即可走完「导出 → 人工作业 → 导入 → Kernel 验证」的最短路径：

```bash
mkdir -p "$PAR_DATA_DIR/config"
cat > "$PAR_DATA_DIR/config/profiles.toml" <<'TOML'
schema_version = 1
active_profile = "manual"

[profiles.manual]
coordinator = "claude_desktop"
default_worker = "w_manual"
max_parallel_workers = 1
worker_failure_policy = "ask"
additional_paid_credits = false
approval_mode = "human_only"

[profiles.manual.roles.code]
primary = "w_manual"
fallback = []

[workers.w_manual]
provider = "manual_handoff"
model = "human"
capabilities = ["read", "edit"]
TOML
.venv/bin/par doctor
.venv/bin/par fixture create demo
```

```bash
cat > draft.json <<'JSON'
{
  "project_id": "demo",
  "title": "Fix add()",
  "description": "Make add() return the sum of its two arguments.",
  "risk": "R2",
  "allowed_paths": ["app/"],
  "acceptance": {
    "criteria": ["the fixture's unit test passes"],
    "checks": [{"name": "unit", "argv": ["python3", "-m", "unittest", "-q"], "timeout": 60}]
  }
}
JSON
.venv/bin/par task draft --draft draft.json > draft.out
tail -1 draft.out > task.json
.venv/bin/par task submit --contract task.json
.venv/bin/par task approve demo-0001
.venv/bin/par task handoff demo-0001 --to workbuddy --json
```

在 WorkBuddy（或任何能打开文件夹的 GUI 工具）里打开导出的 `workspace_absolute_path`，粘贴 `PROMPT.md`，把 `app/calc.py` 改成 `return a + b`，然后：

```bash
.venv/bin/par task import demo-0001 --from "<导出的 workspace 绝对路径>"
.venv/bin/par task result demo-0001
.venv/bin/par task diff demo-0001
.venv/bin/par task accept demo-0001
```

预期：导入后状态为 `REVIEW_REQUIRED`，Kernel 自己的 `unit` 检查 PASS；diff 只有一行；真实夹具项目保持干净（PAR 从不自动应用补丁）。打开错目录、旧包或伪造 marker 会在导入时被点名拒绝。`--to workbuddy` 只是标签，不代表该品牌已被验证。

## 4. 登记一个普通 Git 仓库（不需要 AGENTS.md / STATE.md）

```bash
mkdir -p /tmp/myrepo && cd /tmp/myrepo && git init -q -b main
printf '# demo\n' > README.md
printf 'def add(a, b):\n    return a - b\n' > calc.py
git add -A && git -c user.name=demo -c user.email=demo@example.invalid commit -qm init
cd -  # 回到 personal-agent-runtime
.venv/bin/par project add myrepo /tmp/myrepo
.venv/bin/par project show myrepo
```

预期：`registered myrepo: /tmp/myrepo (vcs git; context generic; writes disabled)`；`project show` 显示 `Context generic (...)` 与 `Writes BLOCKED - WRITE_DISABLED`。**默认不写项目里任何文件**。

想沿用旧的 Harness 规则才需要 `--context-mode harness`；细节见 [CONTEXT.md](CONTEXT.md)。

## 5. 真实 Provider 之前先看这里

- 默认**没有任何适配器**。只有配置里启用 `codex` / `antigravity` 并写明绝对路径的 `executable` 才会注册。
- Google Antigravity 官方 FAQ 明确禁止把 `agy` 封装在第三方编码 Agent/应用中；Gemini CLI 自 2026-06-18 起停止为免费层 / AI Pro / Ultra 用户服务。**技术可行 ≠ 条款许可**，见 [COMPATIBILITY.md](COMPATIBILITY.md)。
- 真实项目写入要你亲自 `par project enable-write ID`（先体检、再敲一遍 id 确认），且**每个写任务**仍逐项批准；补丁永远不会自动应用到项目，要你自己 `git apply`。见 [SECURITY.md](SECURITY.md)。
- 真实 Codex 需要 ChatGPT 订阅登录、签名二进制与绝对路径；模型调用会消耗你的订阅额度。`par health --probe` 是离线检查，不调用模型。
- 不提供「强行关掉权限」的排障方案；无法运行时 `par health` / `doctor` 会给出原因。
- 真实 Codex 若报告 `sandbox_apply: Operation not permitted`、读不到任何文件，先跑
  `.venv/bin/python -B tests/tools/sandbox_probe.py --json`：它会告诉你宿主能否创建**嵌套** macOS 沙箱。
  有些 Agent 宿主本身已在沙箱内，这时换普通终端 / 未被沙箱化的 runner，或直接
  `.venv/bin/python -B tests/tools/codex_revalidate.py --check-only` 一键复核；
  **不要**用 `--dangerously-bypass-approvals-and-sandbox` 之类的提权参数让写测试「通过」。

## 6. 测试与贡献

```bash
.venv/bin/python -B tests/tools/parallel_tests.py      # 全量，按模块并行
.venv/bin/python -B tests/tools/mutation_check.py      # 安全变异检查（较慢）
```

CI 只跑四个明确命名的作业（`smoke_install`、`core_unittests` 在 macOS，`linux_security_core`、`linux_process_control` 在 Ubuntu），**不是全量套件**；见 [CONTRIBUTING.md](../CONTRIBUTING.md)。首次使用不需要跑全量测试。

## 2026-11-09 复测结果（可核对）

| 步骤 | 结果 |
|---|---|
| `python3 -m venv` → `pip install -e .` | 成功 |
| `par init`（隔离 `PAR_DATA_DIR`） | 创建 0700 数据目录，并打印默认审批模式提示 |
| `par doctor` | `PASS 5 \| WARN 0 \| FAIL 0`，全部 Provider disabled |
| `smoke_codex.py --self-test` | 全流程通过，无模型、无网络，真实项目/数据目录未变 |
| 人工交接零账号流程 | 导出 `workspace` → 人工修改 → 导入 `REVIEW_REQUIRED`，`unit` PASS，diff 一行，夹具未被自动修改，`accept` 成功 |
| `par project add` 普通 Git 仓库 | generic 模式登记成功，写入开关默认关闭 |
| 未验证 | 任何真实模型调用、Linux/Windows 上的完整流程、真实项目的自动写入 |

## 历史记录（2026-11-06，未改写）

2026-11-06 的首次陌生人演练使用 Python 3.13、HEAD `fe3270d`、数据目录 `/tmp/par_fresh_data`，结果包括：`par doctor` 5 PASS、`smoke_codex.py --self-test` 通过、普通 Git 仓库登记成功、夹具任务 `ad_hoc=true` 被拒绝后改为 `false` 提交、`par task handoff --to workbuddy --json` 输出 `mode: text`。当时的 `par version` 只打印 `1.0.0 (phase RELEASE)`；当前版本额外打印 `release status: released 2026-11-09; P3 gate partial (Codex automatic + manual hand-off)`。

> 注：`docs/reports/` 里的历史验收报告是**当时那个版本的历史快照**，不是当前版本的支持保证；
> 当前证据以 `docs/COMPATIBILITY.md`、`docs/SECURITY.md` 与 CI 日志为准。
