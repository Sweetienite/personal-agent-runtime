# GitHub Copilot CLI 适配器（文本答题，实验性）

Copilot 适配器只回答 **R0/R1 文字任务**：它把合同和上下文通过标准输入交给
GitHub Copilot CLI，读取 JSONL 事件，把最后一条 `assistant.message` 作为未验证的
`summary` 交回 Kernel。它**不写文件、不产生补丁、不能用于 R2**，也不参与候选集成。
Provider 默认 `enabled = false`，在明确配置绝对路径并启用之前不会注册。

## 先决条件

- GitHub Copilot CLI 已安装，且 `copilot --version` 可用。本适配器锁定 npm 包自带的
  **1.0.5** 引擎；每次运行都加 `--no-auto-update`，避免 launcher 下载/切换到缓存的新版本。
- Copilot CLI 的凭据查找按 **HOME** 作用域；适配器把子进程的 `HOME` 和 `COPILOT_HOME`
  都固定到同一个稳定目录（默认 `~/.copilot`），因此要用这个 HOME 登录一次
  （Runtime 不读取、不复制凭据）：

  ```bash
  HOME="$HOME/.copilot" COPILOT_HOME="$HOME/.copilot" copilot --no-auto-update login
  ```

  只用临时 HOME 运行而不这样登录，会看到 `No authentication information found`；
  这不是账号问题，而是 CLI 找不到你登录时那个 HOME 下的凭据。launcher 的
  `copilot --version` 可能报 1.0.105，但 prompt 引擎仍是 bundled 1.0.5；
  `--disable-client-telemetry` 这类 1.0.105 flag 在 prompt 模式下不可用。
- 在 `<data dir>/config/providers.toml` 里启用并写明绝对路径：

  ```toml
  [providers.copilot]
  kind = "copilot"
  enabled = true
  executable = "/opt/homebrew/bin/copilot"
  ```

- 在 `profiles.toml` 里给它一个只读 Worker；`capabilities` 写 `["read"]`：

  ```toml
  [workers.copilot_answer]
  provider = "copilot"
  model = "claude-haiku-4.5"   # 用 `copilot --no-auto-update --help` 核对本机允许的 slug
  capabilities = ["read"]

  [profiles.default.roles.analysis]
  primary = "copilot_answer"
  fallback = []
  ```

## 一次运行实际发生什么

1. Kernel 准备私有工作副本、私有 HOME 和 TMPDIR；提示词由 Kernel 生成。
2. 适配器在 `preflight` 检查：绝对路径、文件归属/权限、`COPILOT_HOME` 存在、模型 slug、
   提示词大小、CLI 帮助里是否仍有必需的安全 flag；写任务直接拒绝。
3. 子进程收到干净环境：`HOME=COPILOT_HOME=<稳定目录>`、代理/证书变量、`NO_COLOR`、`CI`；
   没有 `GITHUB_TOKEN` / `GH_TOKEN` / `COPILOT_GITHUB_TOKEN`。TMPDIR 和工作副本仍由 Kernel
   按 attempt 私有生成。
4. 参数固定为：

   ```text
   --no-auto-update --output-format json --model <slug>
   --allow-all-tools --available-tools=par-no-tools
   --disable-builtin-mcps --no-custom-instructions --no-ask-user
   --no-bash-env --disallow-temp-dir --log-level none
   --secret-env-vars=GITHUB_TOKEN,GH_TOKEN,COPILOT_GITHUB_TOKEN
   --log-dir <attempt tmp>/copilot-logs
   ```

   提示词走 **stdin**，不出现在 argv。`--available-tools=par-no-tools` 指向一个不存在的
   工具，模型实际没有工具可调用；`--allow-all-tools` 是非交互模式的厂商要求，但在这个
   空 allowlist 下不授予任何真实工具。**不传** `--allow-all-paths`、
   `--allow-all-urls`、`--trust-folder`、`--yolo`、`--allow-all`。
5. JSONL 里 `assistant.message` 变成 `message`，`result` 变成 `terminal`；退出码 0
   但没有 `result` 不算 clean success（`UNKNOWN`，不会进入 `REVIEW_REQUIRED`）。
6. 结果进入正常的 `REVIEW_REQUIRED` 流程；是否接受仍由人决定，Kernel 不接受模型自述。

## 它不能做什么

- 不能写文件、运行验证、生成补丁或参与 R2；路由层和 `preflight` 都会拒绝。
- 没有操作系统级沙箱声明；工具被 allowlist 清空，但 Copilot CLI 仍以当前用户身份启动，
  且可能读取用户级 Copilot 配置。不要把敏感项目交给未评估的账号/配置。
- bundled 1.0.5 没有 `-C` 或 `--disable-client-telemetry`；工作目录由 Kernel 设置，
  客户端遥测不能由适配器关闭。CLI 的 prompt-mode 会话状态可能写在 `COPILOT_HOME` 下。
- 不提供登录状态探测：`par health --probe` 只做离线的 `--version` / `--help` 检查；
  登录失败在第一次真实运行时以 `AUTH_REQUIRED` 出现。
- 不读取、不复制、不转发任何 Copilot 凭据。

## 证据状态（诚实声明）

- 离线 probe 已在真实安装上跑过：可执行文件归属/权限检查通过，`--no-auto-update --version`
  报告 **1.0.5**，帮助里 13 个必需安全 flag 全部存在；登录状态仍无法离线确认。
- 2026-11-10 用真实 HOME 的受控调用成功：`--model claude-haiku-4.5`、临时工作目录、
  `assistant.message` 内容为 `PAR-COPILOT-OK`，`result.exitCode = 0`，
  `usage.premiumRequests = 1`，`codeChanges.filesModified = []`；`session.info` 明确列出
  `bash/create/edit/glob/grep/...` 全部被禁用。这证明 stdin 提示词、JSONL 事件、空工具
  allowlist 和“无写入”路径可用。
- 同一命令在**临时 HOME** 下仍返回 `No authentication information found`；因此适配器现在
  固定 `HOME=COPILOT_HOME`，并要求用同一个稳定 HOME 登录一次。稳定 HOME 下的最终受控调用
  仍需在你完成登录后补一次；当前证据等级仍是 `EXPERIMENTAL`，不是 `VERIFIED_AUTO`。
- 本适配器的行为由 fake CLI 测试、真实 JSONL 样本和厂商帮助共同约束；它还没有完整的
  R1/R2 真实验收证据。

## 失败分类

- `AUTH_REQUIRED`：`No authentication information found`、登录过期等。
- `QUOTA_EXHAUSTED`：厂商自己的 quota / usage limit / credits / billing 措辞。
- `RATE_LIMITED`：`rate limit`、`429`、容量不足。
- `MODEL_UNAVAILABLE`：模型不存在/不可用。
- `TRANSIENT_ERROR`：连接失败、5xx、超时。
- 其余非零退出：`EXECUTION_FAILED`；超时或无终态：`UNKNOWN`。退出码 0 单独从不等于成功。

## 相关文件

- 适配器实现：`src/par/providers/copilot.py`
- manifest：`src/par/providers/manifests.py`
- 配置示例：`config/providers.example.toml`
- 测试：`tests/fake_copilot.py`、`tests/fake_copilot_exe.py`、`tests/unit/test_cli_copilot.py`
- 安全边界：[SECURITY.md](SECURITY.md)
