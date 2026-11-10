# GitHub Copilot CLI 适配器（文本答题，实验性）

Copilot 适配器只回答 **R0/R1 文字任务**：它把合同和上下文通过标准输入交给
GitHub Copilot CLI，读取 JSONL 事件，把最后一条 `assistant.message` 作为未验证的
`summary` 交回 Kernel。它**不写文件、不产生补丁、不能用于 R2**，也不参与候选集成。
Provider 默认 `enabled = false`，在明确配置绝对路径并启用之前不会注册。

## 先决条件

- GitHub Copilot CLI 已安装，且 `copilot --version` 可用。本适配器锁定 npm 包自带的
  **1.0.5** 引擎；每次运行都加 `--no-auto-update`，避免 launcher 下载/切换到缓存的新版本。
- 用同一个 `COPILOT_HOME` 登录一次（Runtime 不读取、不复制凭据）：

  ```bash
  COPILOT_HOME="$HOME/.copilot" copilot --no-auto-update login
  ```

  如果登录状态只存在于缓存的新版本引擎，bundled 1.0.5 仍会报
  `No authentication information found`；这时需要用 bundled 引擎重新登录。
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
3. 子进程只收到干净环境：`COPILOT_HOME`、代理/证书变量、`NO_COLOR`、`CI`；没有
   `GITHUB_TOKEN` / `GH_TOKEN` / `COPILOT_GITHUB_TOKEN`。
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
- 2026-11-10 的一次受控真实调用使用 bundled 1.0.5、`--model claude-haiku-4.5`、临时
  HOME、`COPILOT_HOME=$HOME/.copilot`，在产生任何模型事件之前返回：
  `Error: No authentication information found.` 因此目前**没有**真实模型回答的成功证据，
  也没有 JSONL 事件的真实样本；本适配器的行为由 fake CLI 测试和厂商帮助/事件文档约束。
- 这意味着 `product-capabilities.json` 里它是 `EXPERIMENTAL`，不是 `VERIFIED_AUTO`。
  完成一次同环境的登录并取得真实回答后，才可以把证据升级。

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
