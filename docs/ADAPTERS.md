# Provider 适配器

协议在 P0 定义，P2 起由 Kernel 真正驱动它（`execution/subprocesses.py`、`core/attempt.py`）。**P3 接入了第一个真实适配器：Codex**（`providers/codex.py`，细节与证据见 [CODEX.md](CODEX.md)）；**P4 接入第二个：Antigravity**（`providers/antigravity.py`，见 [AGY.md](AGY.md)）。适配器通过 `providers/registry.py` 的注册表接入，**注册表默认为空**：只有配置里启用了 `codex` / `antigravity` 并写明绝对路径的可执行文件，`cli_task.build_adapters(config)` 才会注册它；旧的 `gemini_legacy` 永远没有适配器。

## 协议（`providers/base.py`）

适配器是**翻译器**：构造 argv、解析事件行、对一次结束的运行分类。启动进程、超时、进程组、取消升级、验收和恢复属于 Kernel，适配器不要自己做。

| 方法 | 作用 |
|---|---|
| `capabilities()` | 八项能力，每项 `yes/no/unknown`，默认 `unknown`，只依据证据设置。它是**构建期静态事实**：真实适配器用类级 `CAPABILITIES` 声明，`build_adapters` 注册时一并交给 `AdapterRegistry`；路由层不会为了读它而实例化工厂。Kernel 在启动前用本次实例再复核一次（R2 需要 write=yes、自动运行需要 headless=yes），未证明即拒绝 |
| `probe()` | 存在性、版本、认证状态；**不得产生模型调用**（`par health --probe` 调用它；Codex 的探测只运行 `--version`、`login status`、`features list`、`mcp list`、`debug models`，有测试证明从不运行 `exec`） |
| `build_argv(request)` | 返回参数**列表**，绝不拼 shell 命令行 |
| `environment(request)` | 子进程**额外**需要的环境变量。Kernel 总是从干净环境（固定 PATH、私有 HOME 与 TMPDIR）起步，不继承你的 shell；适配器要用自己的登录状态就在这里点名（Codex：`CODEX_HOME` 与代理/证书变量，**绝不含任何 API key**） |
| `stdin_data(request)` | 进程从标准输入读到的字节（`None`：什么都没有）。提示词走这里，不出现在进程列表里，也不受命令行长度限制 |
| `preflight(request)` | 拒绝启动的理由（可执行文件、签名、登录方式、隔离无法证明、提示词过大……），或 `None`。有理由就**什么都不启动**，attempt 以 `FAILED`（`preflight`）结束 |
| `workspace_with_git`（可选属性，默认 `True`） | `False` 让 Kernel 给 Worker 的副本去掉顶层 `.git`。Kernel 从不使用 Worker 的仓库；Codex 会把「可写的 git 工作副本」记进你的全局配置（实测），所以 Codex 适配器要求 `False` |
| `prepare(request, home, tmp)`（可选，P4 新增） | Kernel 在 `preflight` 通过之后、启动进程之前调用一次，传入本次 attempt 的**私有 HOME 与 TMPDIR**（Kernel 新建、为空、0700）。适配器可以在里面写 CLI 该读到的配置（Antigravity：Runtime 自己写的 `settings.json` 和指向钥匙串目录的符号链接）。只写这两个目录之内，绝不拷贝凭据；抛异常则 attempt 在启动任何东西之前以 `FAILED`（`preflight`）结束 |
| `RunRequest.allowed_paths`（P4 跟进） | 合同允许 Worker 改动的路径（相对工作副本；目录以 `/` 结尾；只读任务为空）。Kernel 只从合同取值填入；需要「按路径授权」的 CLI（Antigravity 的 `write_file(...)` 规则）由适配器从这里推导，绝不采用 Worker 或项目自己给的路径 |
| `prompt_note(allow_write=…)`（可选，P4 新增） | 适配器告诉 Worker「这个环境不允许什么」的文字；Kernel 把它附加到提示词的 `## Environment` 之下，**在提示词被保存和计入哈希之前**，所以 `prompt.txt` 就是模型真正收到的 |
| `cleanup(request, home, tmp)`（可选，P4 新增） | 子进程一结束（或根本没能启动、或 `prepare` 做到一半失败）Kernel 就调用一次：撤销 `prepare` 留下、不该比这次运行活得更久的东西——例如指向 Runtime 之外目录的链接（证据目录里指向别处的链接可能被粗心的工具顺着走出去）。异常被吞掉，绝不删链接指向的东西；Kernel 自己中途死掉则不会调用 |
| `parse_event(line)` | 解析一行；不保留原始文本（可能含机密），坏行变成只有长度的 `unparsed` |
| `parse_stderr(line)`（可选，P4 新增） | 对子进程写到标准错误的每一行调用一次，可返回一个事件（通常是 `warning`，例如「某个工具被软拒绝」）。同样不得保留原始行 |
| `classify_failure(outcome)` | `None` 仅表示 Provider 层面干净（对 Codex：收到了它自己的 `turn.completed` 且退出码 0）；其余返回 `FailureClass` |
| `request_cancel(process)` | 温和终止；强制终止由 Kernel 对它自己启动的进程组执行 |
| `persistence_report()` | 该 CLI 的会话、日志、记忆落在哪里 |

## 失败分类

| 类别 | 依据 | 处理方向 |
|---|---|---|
| `RATE_LIMITED` | 结构化 `rate_limited`，或**无结构化原因的 HTTP 429** | 冷却，不盲目重试 |
| `QUOTA_EXHAUSTED` | 仅限结构化的 `quota_exhausted` | 换 Worker 或等待，不自动付费 |
| `AUTH_REQUIRED` | 结构化 `auth_required` | 暂停，请你用官方客户端登录 |
| `MODEL_UNAVAILABLE` | 结构化 `model_unavailable` | 只标记该模型，不归咎整个 Provider |
| `PERMISSION_BLOCKED` | 结构化 `permission_denied`，**含退出码为 0 的软拒绝**（测试替身会产生；Codex 适配器不产生——`exec` 无法给出审批，被拒绝的命令只作为结果里的警告） | 暂停，绝不换高权限重试 |
| `TRANSIENT_ERROR` | 结构化 `transient` | 有上限的退避重试 |
| `EXECUTION_FAILED` | 非零退出且无终态；终态 ERROR 但原因未知；终态 SUCCESS 却有错误事件或退出码非 0 | 保留现场，等待审阅 |
| `UNKNOWN` | 超时、无终态且退出码 0、被取消 | 保留现场，人工确认或单次有界重试 |

规则：结构化原因永远优先于状态码；退出码 0 绝不单独等于成功；模型自述的 `tests: PASS` 只是一段未验证的文字。

Codex 没有结构化的错误码，所以 `providers/codex.py` 用**随版本记录的官方消息族**识别（见 [CODEX.md §7](CODEX.md)）：只有厂商自己的「usage limit / spend cap」措辞才算额度用尽，裸 `429` 永远是限流，**不认识的消息一律是 `EXECUTION_FAILED`，不会被猜成额度或登录问题**。额度用尽的消息里若带有「try again at …」，解析出的时间成为该 Provider 的到期时间。

## Codex 适配器（`providers/codex.py`）

一句话：只翻译，不做别的。它要求绝对路径、OpenAI 签名的二进制；用 `exec --json -s <只读|可写工作副本> -m <固定模型> -C <工作副本> … -` 运行；提示词走标准输入；每次都关掉你的 Codex 配置里会递给模型的额外能力（应用、插件、浏览器/桌面控制、子代理、hooks、MCP、`notify`……）并**先证明已关掉**；只接受 ChatGPT 订阅登录；环境里没有任何 API key；取消用 SIGINT。逐项依据、离线实测、尚未验证的部分、升级复核清单都在 [CODEX.md](CODEX.md)。

## Antigravity 适配器（`providers/antigravity.py`）

一句话：只翻译，不做别的——但这个 CLI 有一个 Codex 没有的失败模式：**软拒绝**（需要审批的工具被跳过，运行继续、退出码 0，只在 stderr 提示）。它要求绝对路径、Google 签名（团队 `EQHXZ8M8AV`）的二进制；用 `agy --input-format stream-json --output-format stream-json --model <模型> --sandbox --print-timeout 24h` 运行，提示词是标准输入上的一行 stream-json `user` 事件；**Worker 的 HOME 是 attempt 的私有目录，`agy` 看不到你真实的 `~/.gemini`**——`prepare()` 往里写 Runtime 自己的 `settings.json`（只读任务 `strict`，写任务 `request-review` 加合同路径对应的 `write_file(...)` 允许规则；沙箱开、遥测关、不花 AI 积分、不出工作副本），并在 macOS 上链接钥匙串目录以便找到你一次性的 Google 登录；`init` 事件报告的权限模式比请求的宽就让这次运行失败；项目自带的 hooks/MCP/插件一律拒绝；环境里没有任何 API key，且每次都关掉 CLI 的自动更新；取消用 SIGINT。逐项依据、尚未验证的部分、升级复核清单在 [AGY.md](AGY.md)。

## 测试替身（`providers/fake.py` + `fake_cli.py`）

`ScriptedProvider`（别名 `FakeProvider`）驱动一个独立的假 CLI 进程，走与真实适配器完全相同的链路，不需要模型、网络或订阅额度。假 CLI 只用标准库，写文件严格限制在工作区内，只读请求下一切写入都会被拒绝。

| 场景 | 行为 |
|---|---|
| `success(files, claim_tests_pass)` | 写文件，终态 SUCCESS |
| `quota_exhausted` `rate_limited` `bare_429` `auth_required` `model_unavailable` | 对应的结构化错误加终态 ERROR |
| `permission_denied(soft)` | `soft=True` 时退出码为 0 |
| `timeout` | 写完文件后挂起，无终态 |
| `partial_write_then_quota(n)` | 先写 n 个文件，再额度耗尽 |
| `crash` `truncated` `garbage` | 崩溃、流截断、乱码行 |
| `escape_attempt(path)` | 试图写到工作区之外 |
| `cancellable` | 收到 SIGTERM 后输出 CANCELED 终态，退出码 130 |

P2 起还有：`scenarios.custom(name, *steps)` 加 `ops.*` 步骤构造器（`write` `delete` `symlink` `chmod` `sleep` `stray`（留下孤儿进程）`flood`（大量输出）`write_env`（记录环境变量名）`die(signal)` `ignore_sigterm`……）可以拼出任意行为；`ScenarioSequence(...)` 每次 attempt 发一个场景，用于「第一次失败、第二次成功」；`AdapterRegistry.register("codex", seq.factory("codex"))` 把替身注册到真实的 provider id 下（只在测试里做）。

它**不能**通过配置文件注册（`config.py` 没有 `fake` 这种类型），所以生产 Profile 不可能把任务路由给它。

## 兼容性证据（2026-11-07 更新）

本文的「接入清单」是**工程检查表**，不是兼容性声明。官方接口、版本、账号类型、条款与实测状态统一记在 [COMPATIBILITY.md](COMPATIBILITY.md)：`Antigravity CLI/agy` 判 `RED`，Gemini CLI / Claude Code CLI / ACP / OpenCode 判 `YELLOW`，Codex 为 `EXISTING + GREEN`，人工交接为 `EXISTING`。**仍没有第二条通用 `GREEN` 候选**；T14 的决策「不新增自动 Provider」不变。

2026-11-06 两次真实复核：第一次受宿主嵌套沙箱限制、没有成功的真实 R2（[第一部分](reports/CODEX_REAL_REVALIDATION_20261106.md)）；第二次在「宿主已由外部 OS 沙箱化」这一 `codex exec --help` 明确写出的适用情形下，用 `-s danger-full-access`（**仅一次性临时副本，主干适配器未改**）取得**成功的真实 R1/R2**，`§12` 复核完成、`TESTED_VERSION` 更新为 `0.166.0-alpha.6`，当时证据等级为 `GREEN (scoped)`（[第二部分](reports/CODEX_REVALIDATION_PART2_20261106.md)）。**2026-11-07 在 `sandbox_check=0` 的普通终端上用产品默认 `-s read-only|workspace-write` 补跑真实 R1/R2 成功**（[第三部分](reports/CODEX_DEFAULT_SANDBOX_REVALIDATION_20261107.md)），等级升为 `GREEN`；未改适配器、未新增开关。可复现的离线探测：`provider_probe.py`、`sandbox_probe.py --json`、`codex_revalidate.py --check-only`（均不调用模型）。没有打 tag / 发 Release。

接入清单必须记住的三条教训：**厂商终态 `SUCCESS` + 退出码 0 不等于任务完成**（第一次 R2 被 Kernel 检查正确判 FAILED）；**隔离配方是否生效要按工具定义核对，不能数关键词**（新版 CLI 静态说明文字仍出现 `plugin`，但插件/应用/MCP 工具定义确已移除；`spawn_agent` / `list_agents` 仍残留，不宣称子代理已验证关闭）；**遇到真实 R2 读不到文件先查宿主沙箱能力**（`sandbox_probe.py` 现在也报告继承沙箱的原始 `sandbox_check` 值），不要一上来就放宽 CLI 权限。

## 接入真实 Provider 的清单（P3 已对 Codex 逐条落实，P4 对 Antigravity 照做）

（P4 补充了两条经验：**真实 CLI 的配置文件位置若只能靠 HOME 改变，就用私有 HOME 把它与用户自己的配置隔开**；**任何「软失败」——退出码 0 却没做成——必须变成结果上可见的警告，并由 Kernel 自己的比对来裁决**。）

1. 先只读探测：存在性、版本、`--help`，把结果写成版本兼容记录；不要把示例模型名写死成「保证可用」。
2. 可执行文件用绝对路径；PATH 上的入口不作为回退。
3. 先在**无敏感文件的 fixture 工程**做小型只读烟测，再谈写入。
4. 终态以结构化状态为准，同时检查 stderr、工具事件、文件差异与验收结果。
5. 不得覆盖用户已有的工具配置；不使用会丢掉记忆关闭或沙箱设置的「便捷参数」。
6. 披露该 CLI 的实际留存行为，不声称「零持久化」。
7. 默认禁止跳过权限的参数；权限不足时报告 `PERMISSION_BLOCKED`，不静默升权。
