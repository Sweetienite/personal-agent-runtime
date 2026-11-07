# WorkBuddy 真实人工交接（2026-11-07）：T09 的当前版本重跑

> **本文是当前版本的实测证据，不是历史快照。** 它补上 T09 的人工交接路径在
> **WorkBuddy 5.9.3 / macOS 15.6.1** 上的一次真实 GUI 重跑：导出包 → 用户在 WorkBuddy
> 里打开导出的 `workspace/` → 粘贴 `PROMPT.md` → 完成 → `par task import` → Kernel 自己验证。
>
> 关联：[HANDOFF.md](../HANDOFF.md) · [QUICKSTART.md](../QUICKSTART.md) ·
> [COMPATIBILITY.md](../COMPATIBILITY.md) · [RELEASE_NOTES_2026-11.md](../RELEASE_NOTES_2026-11.md)。

## 0. 结论

| 问题 | 结论 |
|---|---|
| 导出包是否完整？ | `manual-1`，`mode=workspace`，base `ba2d3fc9…`，allowed `app/`，`PROMPT.md` 带文件夹核对与验收边界 |
| WorkBuddy 是否真的改了文件？ | 是：`app/calc.py` 从 `a - b` 改为 `a + b`，1 文件 +1 −1 |
| 导入是否核对 provenance？ | 是：marker 的 task/export/base 与 ledger、导出包 manifest、真实 workspace 路径一致才接受 |
| Kernel 是否独立验证？ | 是：`MEASURED · boundary CLEAN · checks PASSED (1/1)`；`unit` exit 0；0 边界违规 |
| 原项目是否被改？ | 否：这是一次性夹具；补丁只在任务证据里，未应用到任何真实项目 |
| 是否使用 UI 自动化/厂商接口？ | 否：用户在 WorkBuddy GUI 里手动打开文件夹、粘贴、发送；没有点击脚本、没有抓取聊天记录 |

## 1. 环境与对象

| 项 | 值 |
|---|---|
| 日期 | 2026-11-07（Asia/Shanghai） |
| 应用 | WorkBuddy 5.9.3（macOS 桌面版，Electron；`com.tencent.workbuddy.mac`） |
| OS | macOS 15.6.1 arm64 |
| 数据目录 | 一次性隔离目录（`PAR_DATA_DIR`，未使用真实数据目录） |
| 夹具项目 | `wbtest`（`par fixture create`，故意在 `app/calc.py` 留一个 bug，无个人数据） |
| 任务 | `wbtest-0001`，R2，allowed `app/` |
| 导出 | `manual-1`，`mode=workspace`，base `ba2d3fc92c71518b508a2f2c512a902f7b221ffd` |
| Prompt 哈希 | `f76ce6b38c40524c1227ec897afc3dbb9342700e495692dbcf42b8e9ce388c62`（manifest 记录） |
| Workspace | 导出包里的 `workspace/`（带 `.par-handoff.json` marker） |

## 2. 实际步骤

1. `par task submit` → `PERMISSION_WAIT`；用户批准写权限 → `QUEUED`。
2. `par task handoff wbtest-0001 --to workbuddy --json` 导出：
   `folder`、`workspace_absolute_path`、`prompt_path`、`readme_path`、`manifest_path`、
   `base_commit`、`allowed_paths`、`next_actions`，任务转 `PAUSED`。
3. 在 WorkBuddy 桌面版里：
   - 新建任务；
   - 选择工作空间 → **打开本地文件夹** → 选中上面的 `workspace/`；
   - 粘贴 `PROMPT.md` 全文（Prompt 开头就是「先核对 `.par-handoff.json` 的 task_id」）；
   - 发送，等待完成。
4. 用户确认完成后，执行：

```bash
par task import wbtest-0001 --from \
  /tmp/par-wb-data/tasks/wbtest-0001/handoff/manual-1/workspace
```

5. Import 核对 marker、ledger、导出包 `manifest.json`、prompt 哈希与真实 workspace 路径；
   通过后由 Kernel 在一次性验证副本里跑 `unit` 检查。

## 3. 结果（Kernel 自己量到的）

```text
wbtest-0001  REVIEW_REQUIRED  the hand-off result finished and 1 Kernel check(s) passed
  evidence  MEASURED · boundary CLEAN · checks PASSED (1/1) · review NOT_REVIEWED · integration NOT_INTEGRATED · not applied
  worker workbuddy (manual_handoff, asked for manual, vendor reported nothing)
  change  1 file(s) +1 -1   patch: work.patch
  boundary  0 violation(s), 0 protected
  check unit: PASS (exit 0, 0.056s)
```

Diff：

```diff
diff --git a/app/calc.py b/app/calc.py
@@
 def add(a, b):
-    return a - b
+    return a + b
```

- 没有摘要时，结果明确写 `(the worker gave no summary)`；WorkBuddy 的自述（如果有）也只会是
  **未验证**的 Worker 文本。
- `boundary 0 violation(s), 0 protected`：没有越出 `app/`，没有碰 `.ai/`、`AGENTS.md`、凭据或
  workspace 外文件。
- 结果仍是 `REVIEW_REQUIRED`，**没有**被自动接受；接受与否仍由人决定。
- `applied_to_project` / integration 为 `NOT_INTEGRATED`：补丁只在任务证据里，未应用到任何项目。

## 4. 这次重跑确认了什么

- T09 的导出/导入契约在当前代码上仍然可用：`mode`、绝对路径、`next_actions`、marker、
  base/export 一致性、Kernel 检查都实际工作。
- WorkBuddy 真的打开导出的 `workspace/` 并只改了 `app/calc.py`；没有出现「打开旧项目/旧空间」
  导致改错目录的情况。
- 导入不是「相信 WorkBuddy 说完成」：Kernel 自己跑 `python3 -m unittest -q`，结果 PASS 才进入
  `REVIEW_REQUIRED`。

## 5. 没有做 / 仍要注意

- 没有使用 WorkBuddy 的 CLI、私有 API、UI 自动化或聊天记录抓取；这是人工路径的价值所在。
- WorkBuddy 会把打开的文件发给它自己的云端模型；本次夹具只含 `app/calc.py`、`docs/spec.md` 等
  无个人数据文件。真实项目使用前仍由你判断数据去向。
- 只重跑了 **WorkBuddy 5.9.3** 的这一次低敏感 R2；其它 GUI 工具（TRAE / Cursor / Windsurf /
  Cline / Aider）仍按文档标为未真实验证。
- 结果留在 `REVIEW_REQUIRED`；本次没有替用户执行 `accept`。
