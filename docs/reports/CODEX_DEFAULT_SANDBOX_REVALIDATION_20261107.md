# Codex 默认沙箱复核（2026-11-07）：`GREEN` 升级证据

> **本文是当前版本的实测证据，不是历史快照。** 它补完 2026-11-06 第二部分留下的唯一限定：
> 在**允许创建嵌套 OS 沙箱的普通终端**上，用 PAR 的**默认** `-s read-only|workspace-write`
> 跑真实 R1/R2，而不是 `-s danger-full-access` 的非默认配置。
>
> 关联：[COMPATIBILITY.md](../COMPATIBILITY.md) · [CODEX.md](../CODEX.md) §12 / §15 ·
> [ADAPTERS.md](../ADAPTERS.md) · [RELEASE_NOTES_2026-11.md](../RELEASE_NOTES_2026-11.md)。

## 0. 结论

| 问题 | 结论 |
|---|---|
| 默认 `-s` 模式是否真实跑通？ | **是**。R1 与 R2 都使用产品代码的默认沙箱模式，未改适配器、未加提权参数 |
| 真实 R1 结果 | `REVIEW_REQUIRED`（无机械检查）；模型正确答出 `add(2, 3) = -1`，与 `docs/spec.md` 的 `5` 不符，未改任何文件 |
| 真实 R2 结果 | `MEASURED · boundary CLEAN · checks PASSED (1/1)`；补丁 1 文件 +1 −1，0 边界违规，未应用到项目 |
| 一键复核 | `python -B tests/tools/codex_revalidate.py --yes-use-quota --model gpt-6-luna` → **exit 0**；offline / trust / real_smoke 三步全部 rc=0 |
| 真实模型调用 | **2 次**（R1 + R2，ChatGPT 订阅额度） |
| 证据等级 | 现有 Codex 从 `GREEN (scoped)` 升为 **`GREEN`**（仍只覆盖记录中的版本 / OS / 账号组合；不是新增 Provider） |
| 未做的事 | 未制造真实取消/超时/额度事故；未改 `TESTED_VERSION`（仍是 `0.166.0-alpha.6`）；未打 tag / 发 Release |

## 1. 环境

| 项 | 值 |
|---|---|
| 日期 | 2026-11-07（本机时区 Asia/Shanghai） |
| OS / 架构 | macOS 15.6.1 (24G90), arm64 |
| Python | 3.13.5（`python3`） |
| 宿主沙箱状态 | `sandbox_check(getpid(), NULL, 0) = 0`：**未被外部 App Sandbox 约束** |
| 嵌套沙箱 | `nested_sandbox_available: true`（`tests/tools/sandbox_probe.py --json`） |
| Codex 二进制 | ChatGPT.app 内置签名二进制（`codex-cli 0.166.0-alpha.6`，Team `2DC432GLL2`） |
| 账号 | ChatGPT 订阅登录（`codex login status` → `Logged in using ChatGPT`） |
| 模型 | `gpt-6-luna` |
| 代码基线 | `32cbaa7`（已合并为 `bcf32ee`） |
| 数据目录 | 一次性临时目录；夹具项目只含 `app/calc.py` 与 `docs/spec.md` 等无个人数据文件 |

## 2. 命令

```bash
python3 -B tests/tools/codex_revalidate.py \
  --yes-use-quota --model gpt-6-luna \
  --out /tmp/par-codex-reval-20261107
```

脚本先跑：
1. `PAR_REAL_CODEX=1 … tests.integration.test_real_codex.RealCodexOffline`（离线，无模型调用）；
2. `PAR_REAL_CODEX_TRUST=1 … RealCodexTrustEntries`（无登录 `exec`，真实认证失败，无模型调用）；
3. 只有确认宿主能创建嵌套沙箱时，才跑 `smoke_codex.py --yes-use-quota --model … --write`（2 次真实调用）。

它**从不**修改适配器的沙箱模式，也**从不**传 `--dangerously-bypass-approvals-and-sandbox`。

## 3. 结果

| 步骤 | 结果 |
|---|---|
| offline | **8/8 PASS**（含签名、参数、隔离配方、`par health --probe`） |
| trust | **3/3 PASS**（无登录 `exec` 的真实认证失败；不调用模型） |
| real_smoke R1 | `fixture-0001 REVIEW_REQUIRED`；`UNVERIFIED · boundary CLEAN · checks NO_MECHANICAL_CHECK`；模型答出 `add(2, 3) = -1`，与 spec 的 `5` 不符，0 文件变更 |
| real_smoke R2 | `fixture-0002 REVIEW_REQUIRED`；`MEASURED · boundary CLEAN · checks PASSED (1/1)`；补丁 1 文件 +1 −1；0 边界违规；未应用到项目 |
| 真实调用总数 | **2** |
| 退出码 | **0** |

R2 事件流：

```text
thread.started
turn.started
item.started|completed / command_execution ×4
item.started|completed / file_change ×1
item.completed / agent_message ×3
turn.completed
```

## 4. 副作用核对

| 检查 | 结果 |
|---|---|
| 夹具工作树 | `clean (nothing applied)` |
| 真实 PAR 数据目录 | 未变（`listing(real_data) == before_real`） |
| `~/.codex/config.toml` | 未变 |
| `~/.codex` 自身 | CLI 自有文件：新增 2 个 sqlite 临时文件、删除 3 个 `tmp/arg0-macos/...`、修改 3 个缓存/日志文件（只记名称与大小，不读内容） |
| 真实项目 | 没有注册、没有写入；只有一次性夹具 |

## 5. 为什么这次是 `GREEN` 而不是 `GREEN (scoped)`

2026-11-06 的 `GREEN (scoped)` 唯一限定是：默认 `-s read-only|workspace-write` 在当时的宿主上无法叠加
Sandbox，因此真实 R1/R2 只能使用 `-s danger-full-access`。本次在 `sandbox_check=0` 的普通终端上，
产品代码原样运行默认 `-s` 模式，R1/R2 都成功，因此该限定解除。

仍需保留的证据边界：

- 只覆盖 **`codex-cli 0.166.0-alpha.6` + macOS 15.6.1 arm64 + ChatGPT 订阅登录 + `gpt-6-luna`**；
  其它版本/OS/账号需重新复核（`CODEX.md` §12）。
- 真实**取消 / 超时 / 断线 / 输出洪流 / 额度耗尽**事故仍未人为制造；这些行为由 153 项可控假样本
  与离线/trust 测试覆盖（`CODEX.md` §12、§14）。
- `spawn_agent` / `list_agents` 仍出现在基础工具列表；不宣称子代理已验证关闭。
- 不是新增自动 Provider；T14 的「不新增自动 Provider」结论不变。
- 没有打 tag / 发 Release；版本号仍为 `1.0.0`。

## 6. 证据文件

- `/tmp/par-codex-reval-20261107/summary.json`（本次运行的结构化摘要）
- `/tmp/par-codex-reval-20261107/offline.log`
- `/tmp/par-codex-reval-20261107/trust.log`
- `/tmp/par-codex-reval-20261107/real_smoke.log`
