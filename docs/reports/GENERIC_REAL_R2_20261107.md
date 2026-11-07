# 普通 Git 仓库的真实 R2（2026-11-07）：Codex + generic context

> **本文是当前版本的实测证据，不是历史快照。** 它验证 P1 的目标体验在**真实模型**上成立：
> 一个没有 `AGENTS.md`、没有 `.ai/STATE.md`、没有父合同的普通 Git 仓库，登记为 `generic`、
> 打开写入、提交 R2、逐项批准后，由 Codex 在私有副本里改文件，Kernel 自己跑检查。
>
> 关联：[COMPATIBILITY.md](../COMPATIBILITY.md) · [CONTEXT.md](../CONTEXT.md) ·
> [USER_GUIDE.md](../USER_GUIDE.md) · [RELEASE_NOTES_2026-11.md](../RELEASE_NOTES_2026-11.md)。

## 0. 结论

| 问题 | 结论 |
|---|---|
| 普通 Git 仓库能否免 Harness 登记？ | 是：`par project add demo /tmp/...` 默认 `context generic`，不要求也不写 `AGENTS.md`/`.ai/STATE.md` |
| 默认登记后能否直接打开写入？ | 是（本轮发现并修复了一个 onboarding 缺陷，见 §3）：`auto` + 检测到 `.git` 时，`enable-write` 会把登记改成 `vcs = "git"` |
| R2 是否真实执行？ | 是：Codex 真实调用 1 次（`gpt-6-luna`，ChatGPT 订阅登录，产品默认 `-s workspace-write`） |
| Kernel 是否独立验证？ | 是：`MEASURED · boundary CLEAN · checks PASSED (1/1)`；`unit` exit 0 |
| 原项目是否被改？ | 否：补丁只在任务证据里，`REVIEW_REQUIRED`，未应用到项目 |
| 是否依赖作者 Harness？ | 否：项目没有 `AGENTS.md`/`.ai/STATE.md`/父合同，Context Manifest 只有 README + Git facts |

## 1. 环境与对象

| 项 | 值 |
|---|---|
| 日期 | 2026-11-07（Asia/Shanghai） |
| OS / Python | macOS 15.6.1 arm64；Python 3.13.5 |
| Codex | ChatGPT.app 内置签名二进制，`codex-cli 0.166.0-alpha.6`，ChatGPT 订阅登录 |
| 模型 | `gpt-6-luna` |
| 数据目录 | 一次性隔离目录（`PAR_DATA_DIR`） |
| 项目 | `demo`：普通 Git 仓库，只有 `README.md`、`app/calc.py`、`tests/test_calc.py`；**没有** `AGENTS.md` / `.ai/STATE.md` / 父合同 |
| 任务 | `demo-0001`，R2，`ad_hoc=true`，allowed `app/`，check `python3 -m unittest -q tests.test_calc` |

## 2. 实际步骤

```bash
par project add demo /tmp/par-generic-repo
par project enable-write demo --confirm demo
par task submit --contract /tmp/par-generic-task.json
par task approve demo-0001 --note "real generic R2 smoke ..."
par task run demo-0001
```

`project show demo` 显示 `Context generic (README/AGENTS/CLAUDE + Git facts; no parent contract)`，
`Writes ALLOWED`，base 为干净的 Git 提交。

## 3. 本轮发现并修复的 onboarding 缺陷

第一次 `par project add demo ...` 使用默认 `vcs = "auto"`，随后 `par project enable-write demo`
被配置校验拒绝：

```text
error: projects.demo.vcs: a real project may be written only with vcs = "git": the git baseline is the rollback
```

这不是用户操作错误：默认登记是 `auto`，项目实际是 Git，`write_readiness` 也通过了；但写开关
要求登记值字面为 `git`。修复：`set_project_write` 在**开启写入**且登记为 `auto`、项目根存在 `.git` 时，
把该项目的 `vcs` 行改成 `"git"`（保留其余行与备份）；`add_project` 的只读/非 Git 行为不变。
回归测试：`test_an_auto_registered_git_project_can_be_enabled_without_re_registering`。

## 4. 结果（Kernel 自己量到的）

```text
demo-0001  REVIEW_REQUIRED  the Worker finished and 1 Kernel check(s) passed
  evidence  MEASURED · boundary CLEAN · checks PASSED (1/1) · review NOT_REVIEWED · integration NOT_INTEGRATED · not applied
  worker w_code (codex, asked for gpt-6-luna, vendor reported nothing)
  change  1 file(s) +1 -1   patch: work.patch
  usage   input 50537 (cached 37248), output 304 (reasoning 46) tokens, as the vendor counted them (not an estimate, not a bill)
  boundary  0 violation(s), 0 protected
  check unit: PASS (exit 0, 0.052s)
```

Diff：

```diff
diff --git a/app/calc.py b/app/calc.py
@@
 def add(a, b):
-    return a - b
+    return a + b
```

- Worker 自述只是未验证文本；`unit` 是 Kernel 在一次性验证副本里自己跑的。
- `boundary 0 violation(s), 0 protected`：没有越出 `app/`。
- 结果留在 `REVIEW_REQUIRED`，没有自动接受；`applied_to_project` 为 false。

## 5. 这次验证满足的规划条目

- P1 Gate：全新干净 Git 仓库没有 Harness，仍可通过安全授权跑 R2 并通过独立检查（真实模型版）。
- P3 发布验收：普通 Git 项目完成受限 R2 的首次体验（用户授权、合规真实模型可用）。
- A05 §7：新用户 clone/install/fake smoke 之外，真实 R2 的普通项目路径已跑通。
- 没有把私有副本宣传成 OS 沙箱；Codex 的读范围仍由它自己的沙箱/配置决定。

## 6. 没有做 / 仍要注意

- 只跑了 1 次真实 R2；没有跑真实取消/超时/额度事故。
- 项目是 `/tmp` 下的一次性仓库，不含个人数据；真实项目仍需 `par project enable-write` + 逐任务批准。
- 没有接受（`accept`）这个 fixture 任务；验收仍由人决定。
- 没有打 tag / 发 Release。
