# Antigravity（`agy`）适配器（P4）：版本兼容记录与决策依据

> 本文记录「这个适配器依据什么、对哪个版本的 `agy` 做过什么验证」。每一条都标明证据来源：**官方文档**（Antigravity CLI 官方文档页面，括号里是页面名）、**离线实测**（本机运行了不调用模型、不联网的命令）、**真实实测**（用真实安装的 `agy` 做过：不调用模型的探测，以及获批的真实运行）、**未验证**（需要真实运行才能确认，列在 §12，并写明怎么验证）。
>
> **当前状态（诚实记录）：`agy` 1.3.5 已按你的批准私有安装、做过「不调用模型」的真实探测，并做过**多次**获批的真实写入烟测（§11）：前两次 `agy` 没有完成任务（第一次模型想运行测试被自动拒绝；第二次改文件被自动拒绝——**无头模式下 `request-review` 连工作副本内的文件写入也不自动允许**），Kernel 两次都正确判为失败。适配器据此给写任务配 `write_file(...)` 允许规则，**第三次/第四次真实调用（2026-10-15 下午）证实：规则被 1.3.5 接受并生效，`agy` 独立完成了夹具任务，Kernel 验证通过（`REVIEW_REQUIRED`，检查 PASS）——P4 Gate 达成**。** 标「官方文档」的内容是官方说法；标「真实实测」的是本机观察（并已发现几处官方文档与真实行为不符，见 §11 末尾）。事件解析既有**合成**样本（形状取自官方文档，见 `tests/agy_samples.py`），也有一份**真实**样本（`tests/data/agy_real/`，来自那次烟测）。

## 1. 被验证的安装

| 项 | 值 | 证据 |
|---|---|---|
| 可执行文件 | `~/Library/Application Support/PersonalAgentRuntime/vendor/agy/1.3.5/agy`（目录 0700，文件 0755；删掉 `vendor/agy/` 即回滚） | 真实实测 |
| 版本 | `agy --version` → `1.3.5`（Mach-O arm64，180,917,280 字节） | 真实实测 |
| 来源与哈希 | 官方发布清单 `…/manifests/darwin_arm64.json`（version 1.3.5）→ `cli_mac_arm64.tar.gz`，55,069,121 字节，**SHA-512 与清单一致**（`8ed16057…e080`）；压缩包**只含一个成员** `antigravity`，只解出它；二进制 SHA-256 `ebd71c4d…fff`（拷到最终位置后复核一致） | 真实实测 |
| 签名 | `Developer ID Application: Google LLC (EQHXZ8M8AV)`，Identifier `cli`，hardened runtime，`codesign --verify --strict` 通过；**在第一次运行它之前**就核对了（不执行） | 真实实测 |
| 没有做的事 | 没运行官方 `install.sh`（它会改 shell 配置并执行 `agy install`）；没改 PATH；没碰 `~/.gemini`；没运行 `agy update` | 设计决定 |
| 适配器如何信任它 | 配置里的 `executable` 必须是绝对路径；文件存在、可执行、不被组/其他用户写、属主是你或 root；是 Mach-O 二进制（脚本、纯文本一律拒绝）；`codesign --verify --strict` 通过且团队 ID 是 `EQHXZ8M8AV`（结果缓存 10 分钟）。对真实二进制测过：Google 的通过，OpenAI 的 `codex`（`2DC432GLL2`）和苹果的 `ssh`（无团队）被拒并说明原因 | 真实实测 + `test_agy_adapter.py` |
| 绝不使用 | PATH 上的 `agy`；旧的 `gemini` CLI（`gemini_legacy` 永远不能启用，也永远不是 `agy` 的降级后备） | 设计决定 + 测试 |

## 2. 命令行（`build_argv`）与标准输入

```
agy --input-format stream-json --output-format stream-json --model <模型> --sandbox --print-timeout 24h
    （提示词不在命令行里：它是标准输入上的一行 {"event":"user","message":{"content":"…"}}，Kernel 写完就关闭管道）
```

| 参数 | 为什么 | 证据 |
|---|---|---|
| `--input-format stream-json` + `--output-format stream-json` | 无头模式；提示词走标准输入（不出现在进程列表里，也不受命令行长度限制）；输出是逐行 JSON 事件。**关闭标准输入即优雅结束**：处理完当前一轮后进程以 0 退出 | 官方文档（headless） |
| `--model <slug>` | 精确指定模型；**未知模型不会静默回退**，而是 `ERROR` 状态、退出码 1 | 官方文档（headless） |
| `--sandbox` | 命令类工具在系统沙箱内运行（macOS 用 Seatbelt）。注意：只约束「执行命令」，**不约束文件读写工具** | 官方文档（sandbox、CLI reference） |
| `--print-timeout 24h` | 官方文档说 CLI 自己的等待上限默认 5 分钟；**真实 `--help` 写的是「默认 0s，0＝等到这一轮结束」**，两者不一致。显式给一个远大于 Kernel 总超时的值，两种情况下都保证 **Kernel 才是唯一的超时权威** | 官方文档 vs 真实 `--help`（矛盾，已绕开）；**在 stream 输入模式下是否生效：未验证** |
| 不传 `-p` | 官方明确说：流式输入模式下命令行上的提示词会被**丢弃** | 官方文档（headless，Common mistakes） |
| 永不传 `--dangerously-skip-permissions` | 会自动批准全部工具；测试里有一条守卫：适配器代码里不允许出现这个字符串 | 设计决定 + `test_no_real_calls.py` |
| 不传 `--agent`、`--continue`/`--conversation`、`--effort`、`--mode` | 每次都是全新会话；推理强度包含在模型 slug 里（如 `…-medium`）；**真实 `--help` 里有 `--mode`（`accept-edits`、`auto`、`plan`），官方标志表里没有**：它与 `toolPermission` 预设的关系未验证，先不用（可能是将来给只读任务再加一层的候选，见 §12） | 官方文档 + 真实 `--help` |

> **提示词的补充**：适配器通过 `prompt_note` 钩子让 Kernel 在提示词末尾追加一段 `## Environment`（写任务：这里没有 shell 命令、不是「被拦住」、直接改文件、检查由 Kernel 代跑；只读任务：只读环境、务必以文字作答）。它计入 `prompt.txt` 与提示词哈希，所以审计看到的就是模型真正收到的（原因与真实证据见 §4）。

> 每个传入的标志是否存在于已安装版本，由 `tests/integration/test_real_agy.py`（`PAR_REAL_AGY=1`）对照真实 `--help` 逐个核对——**1.3.5 上全部存在**（`--help` 打印在 **stderr**、退出码 0）。真实 `--help` 还有官方标志表没列的：`--mode`、`--add-dir`、`--disable-slash-commands`（「在 print 模式下禁用斜杠命令和技能展开」）、`--log-file`、`--project`、`--new-project`、`-i/--prompt-interactive`，`--effort` 的取值多了 `xhigh`、`max`；子命令有 `install`（改 PATH 与 shell 配置——Runtime 永不调用）、`update`、`mcp`、`plugin`、`remote-control`、`mic-serve`、`models`、`agents`、`changelog`。

## 3. Worker 的私有 HOME：`agy` 看不到你真实的 `~/.gemini`

`agy` 的所有配置都在 `$HOME/.gemini/` 下（没有可以改位置的环境变量）。你的 `~/.gemini` 里有：Antigravity 图形应用的数据、旧版 Gemini CLI 的凭据与设置、全局规则 `GEMINI.md`……这些都不应该影响一个被调度的 Worker，也不应该被它读写。

所以：**每个 attempt 的 HOME 是 Kernel 建的私有目录**，适配器的 `prepare()`（Kernel 在启动进程前调用的新钩子）往里写两样东西，且**只写在这个目录里**：

1. `.gemini/antigravity-cli/settings.json`（0600）——`agy` 在这个 HOME 里读到的**唯一**配置：

   | 键 | 值 | 为什么 |
   |---|---|---|
   | `toolPermission` | 只读任务 `strict`；写任务 `request-review` | `strict`＝所有非读取工具都要审批 → 无头模式下**一律软拒绝**，相当于只读；`request-review` 在无头模式下**读**工作副本内的文件自动允许，**写不会**（第二次真实写烟测实测：`ReplaceFileContent` 被软拒绝），所以写任务另配下一行的 `write_file` 规则；命令一律软拒绝 |
   | `permissions.allow` | **仅写任务**：合同 `allowed_paths` 里每个条目生成一对 `write_file(<路径>)`——相对工作副本（官方 headless 文档的写法 `write_file(src/)`）和绝对路径（真实通知与厂商自带 automation 技能的写法）；只读任务**没有**这个键 | 绝不生成 `command(...)`、通配符、`regex:` 规则、`/` 或工作副本之外的路径；路径只允许字母数字和 `._-/`，工作副本绝对路径含括号/通配符/反斜杠时只写相对形式；**匹配语义未经真实验证**（§12 第 20 项） |
   | `allowNonWorkspaceAccess` | `false` | 文件工具不出工作副本 |
   | `enableTerminalSandbox` | `true` | 即使将来允许了某条命令，也在沙箱里跑 |
   | `useG1Credits` | `false` | 套餐额度用完后**不**花个人 AI 积分 |
   | `enableTelemetry` | `false` | 请求关闭使用统计。**未验证它是否生效**：CLI 改写 `settings.json` 时把这个键丢掉了（要么它的默认值已经是 false，要么 1.3.5 不认这个键），日志里仍有一行 analytics 的启动事件；日志里记录的网络请求只有 `daily-cloudcode-pa.googleapis.com`（`loadCodeAssist`、`fetchAvailableModels`） |
   | `notifications` `showTips` `showFeedbackSurvey` | `false` | 无头运行不需要 |
   | 不出现的键 | `modelProvider`（API key 模式）；只读任务的 `permissions` | API key 计费模式因此**无法被打开**；没有任何预先授权的命令 |

2. 在 macOS 上，`Library/Keychains` → 你真实的 `~/Library/Keychains` 的**符号链接**。原因（离线实测）：钥匙串查找是相对 HOME 的——把 HOME 换成空目录后 `security default-keychain` 报 `A default keychain could not be found`；加上这个链接后能找到 `login.keychain-db`。这是链接，不是拷贝；Runtime 从不打开它指向的东西。**链接只在 `agy` 运行期间存在**：适配器的 `cleanup` 钩子在子进程一结束就把它（以及为它建的空 `Library` 文件夹）删掉，证据目录里不留任何指向你钥匙串的指针（有测试：目标里的文件不会被动、真实目录不会被当成链接删除）。

结果：你的 `~/.gemini`（包括旧 `gemini` CLI 的凭据与配置）**对 Worker 不可见**；`agy` 首次启动时「自动导入旧版 profile 并把令牌迁入钥匙串」的行为在私有 HOME 里无从发生。

**真实实测（1.3.5，`tests/` 之外的探测脚本，只比较名字/大小/修改时间，不读内容）：**

- **`agy` 完全尊重 HOME**：运行 `--version`、`--help`、`models`、`-p /usage` 之后，`~/.gemini`、shell 配置文件、`~/.config`、`~/.cache`、`~/.local`、`~/Library/{Application Support,Caches,Logs,Preferences,Keychains}` 全都**没有变化**（Antigravity 图形应用当时正在运行）。只有一次较长（60 秒）的对照运行里，macOS 与 WorkBuddy 自己的几个文件（`com.apple.sharedfilelist`、`com.apple.HIToolbox.plist`、`com.tencent.workbuddy.mac.plist`、`~/.workbuddy`）被后台改动，与 `agy` 无关。
- **钥匙串链接确实是登录的来源**：同一个 `agy -p /usage`，有链接 → 认出你已有的 Google 登录并返回额度表；去掉链接 → 要求登录（见 §5）。也就是说它读的不是 `~/.gemini` 里的令牌文件，而是钥匙串。
- **`agy` 在私有 HOME 里自己建了这些**（名字与大小，约 80 个文件）：`.gemini/antigravity-cli/` 下的 `brain/`、`builtin/`（它自带的 10 个内置技能：`SKILL.md` 与文档）、`cache/{onboarding.json,default_project_id.txt}`、`cli.log`→`log/cli-<时间>.log`、`conversation_summaries.db`（+`-shm`/`-wal`）、`conversations/`、`crashes/`、`implicit/*.pb`、`installation_id`（每次新建一个 UUID）、`jetski_state.pbtxt`；以及 `.gemini/config/{.migrated,mcp_config.json（0 字节）,projects/default-cli-project.json}`。
- **它会改写我们写的 `settings.json`**：261 字节 → 146 字节——CLI 只保留与它自己的默认值不同的键（官方文档也这么说）。留下的是 `toolPermission=strict`、`enableTerminalSandbox=true`、`showTips=false`、`showFeedbackSurvey=false`、`altScreenMode=never`；`allowNonWorkspaceAccess`、`useG1Credits`、`notifications`（等于默认）以及 **`enableTelemetry=false`**（见上表）被丢掉。CLI 自己的日志确认它**读到并应用了** `toolPermission=strict`（`CLI settings initialized: permissions=<nil>, toolPermission=strict`）。因此 `settings.json` 的「写入内容」不等于「读回内容」，适配器不依赖读回。
- **CLI 自己的日志还证实**：登录走钥匙串（`authenticated via keyring`，账号类型 `consumer`，访问令牌约 1 小时过期，由 CLI 自己续期）；`AGY_CLI_DISABLE_AUTO_UPDATE` 生效（`Auto-update disabled via environment variable`）；它还会去读 `$HOME/.gemini/antigravity/conversations`（Antigravity 应用/IDE 共用的对话目录）——在私有 HOME 里不存在，只记一条无害的错误；没指定模型时默认是「Gemini 3.8 Flash (High)」。
- **真实写入烟测的 CLI 日志**还证实：`loaded 0 named hooks from 0 hooks.json file(s)`（没有 hooks）；`Print mode: enabling terminal sandbox for this session`（`--sandbox` 生效）；`CLI settings initialized: permissions=<nil>, toolPermission=request-review`（没有任何允许规则）；项目级权限授予（`ApplyProjectPermissionGrants`）存在 HOME 里的 `config/projects/*.json`，而不是工作区里——所以随项目副本进来的东西不可能自带授权。日志里**没有**任何一行提到 `AGENTS.md` 或规则文件：工作区的 `AGENTS.md` 有没有被自动发现**未验证**（不影响我们：Kernel 的提示词已把它内联进去了）。
- 首次启动的「引导」状态（`onboarding.json`）在私有 HOME 里自动生成，`models` 与 `-p /usage` 没有被任何引导/信任提示卡住。**模型运行是否也不会被卡住：未验证**（§12 #3）。

> 一次性登录同理：`par provider login antigravity` 在 Runtime 自己的 `agy_login_home/`（同样布局）里把 `agy` 的官方交互登录作为**子进程**启动（不是 `exec`，所以它结束后能把钥匙串链接删掉），你在浏览器里完成 Google 登录；Runtime 不经手任何凭据。登录的结果存在钥匙串里，之后所有 Worker 通过那条链接找到它。
>
> **绝不使用真实 home 当私有 HOME**：`write_home` 和 `login_plan` 一开始就拒绝任何解析后等于你真实 home 的目录（`ValueError`，在创建或 chmod 任何东西之前）。这条规则是 P4 里被一次事故逼出来的，见 §11 末尾。

## 4. 权限模型与「软拒绝」

官方文档（headless、settings）：

- 默认权限预设 `request-review`：官方说读写**工作区内**文件自动允许；shell 命令默认「询问」，而无头模式没人能回答，于是 **软拒绝**——运行**继续**、**退出码 0**、在 stderr 打一条点名工具的提示。**真实 1.3.5 的无头模式并非如此：工作区内的读自动允许，写也要确认，于是被软拒绝（见下面第二次烟测）。**
- 这正是本 CLI 特有的失败模式：**「退出码 0」≠「做完了」**。适配器把它变成 `warning` 事件显示在结果上；Kernel 自己的文件级比对（只读任务不许有任何改动；写任务只许在允许路径内）才是裁判。
- `init` 事件会报告 CLI **实际**使用的 `permission_mode`。适配器核对它：**比请求的更宽（或缺失/未知）→ 这次运行失败**（`EXECUTION_FAILED`，并有警告说明原因）。没有 `init` 事件的「成功」也不被信任（无从证明权限模式）。

**真实实测（写入烟测，2026-10-15）**：`request-review` 下，工作区内的 `view_file` 自动允许；模型想运行 `python3 -m unittest -q`，**被自动拒绝**（`run_command` 步骤状态 `ERROR`，错误里写明 `user denied permission to run command` 并要求模型「不要换个说法或换个工具绕过」）；模型随后结束了这一轮——**厂商状态 `SUCCESS`、退出码 0、`response` 为空、没有任何文件被改**。同时：stderr 打了一行提示（说有工具需要无头模式无法询问的 `"command"` 权限所以被自动拒绝，并建议加允许规则或用跳过权限的参数——Runtime 永不这样做）；最后的 `result` 事件带一个结构化的 `denied_actions` 列表（`[{"action":"command","display_name":"RunCommand"}]`）。适配器把这三处同一件事合成**一条**结果警告，Kernel 自己的检查把它判为失败。Kernel 的提示词里有一句「被拦住就停下并说明」，模型照做了；所以适配器现在用 `prompt_note` 在提示词末尾（`## Environment`，保存并计入哈希）事先告诉模型：这里没有 shell 命令（包括项目的测试命令），不是「被拦住」，直接改文件，检查由 Kernel 代跑。**这个调整让模型去改文件了**（第二次烟测）——但改文件被拒了，见下。

**第二次真实写入烟测（2026-10-15，约 13 秒，用量 input 44,391 / output 829）**：这次模型听了提示：读了 `app/calc.py` 和 `tests/test_calc.py`（`view_file`，自动允许），直接调用 `replace_file_content` 改 `app/calc.py`——**被自动拒绝**：工具步骤状态 `ERROR`，错误是 `permission check failed for write_file "<绝对路径>": user denied permission for write_file(<绝对路径>)`；stderr 提示 `"write_file"` 权限无法在无头模式下询问，并建议加允许规则（示例 `write_file(<target>)`）；`result` 的 `denied_actions` 是 `[{"action":"write_file","display_name":"ReplaceFileContent"}]`；厂商状态 `SUCCESS`、退出码 0、`response` 为空。厂商日志证明：`workspaceDirs=[<工作副本>]`（工作副本**被**认作工作区）、`toolPermission=request-review`、`permissions=<nil>`（当时没有规则）、`Print mode: soft-denying tool confirmation "ReplaceFileContent"`。结论：官方文档「读写工作区内文件自动允许」在 1.3.5 的**无头模式**下对写不成立。

**对策（已实现，并已被真实 1.3.5 验证，见第 10 项）**：Kernel 把合同的 `allowed_paths` 放进 `RunRequest`；适配器 `prepare` 只为写任务在私有 HOME 的 `settings.json` 里写 `permissions.allow = [write_file(...)…]`（相对 + 绝对两种写法，见 §3 表）。**命令仍不给任何规则**（§12 第 18 项仍待你决定）。备选（未实现）：`--mode accept-edits`——二进制里自带的说明是「auto-approve file edits, prompt for commands」，不依赖路径写法，但不限定路径（路径边界仍由 Kernel 的文件比对兜底）。

已知缺口（写在这里，不假装没有）：

| 缺口 | 说明 | 处理 |
|---|---|---|
| 子代理 | 官方：「主代理决定子代理拿到哪些工具和权限」，还有 `define_subagent` 工具。理论上模型能自己定义权限更宽的子代理 | 事件里出现 `subagent_info` 就给警告；Kernel 的文件比对兜底（工作副本是私有拷贝，碰不到真实项目）；**真实烟测要专门看它**（§12） |
| 文件工具不在沙箱里 | `--sandbox` 只管命令；读文件的范围靠 `allowNonWorkspaceAccess=false` | 与 Codex 相同的 P8 门槛：真实项目试运行前评估系统级收紧 |
| 网络工具 | `read_url`/`search_web` 之类可能出网 | 事件带 `note`；是否能关掉：未验证 |
| 项目自带的 hooks / MCP / 插件 | `.agents/hooks.json`（会执行 shell 命令）、`.agents/mcp_config.json`（会启动进程）、`.agents/plugins/` 会随项目副本进入 Worker 的工作区 | `preflight` **拒绝**：`.agents/` 下只接受 `rules`、`skills`、`rules.json`、`AGENTS.md`、`GEMINI.md`；`.agent/` 下只接受 `rules`、`skills`；未知条目一律拒绝（适配器没被教过它们做什么） |
| 全局来源 | `~/.gemini/config/mcp_config.json`、全局 hooks、sidecar 配置、全局规则 | 私有 HOME 里不存在，因此不生效 |

## 5. 认证与计费

| 项 | 做法 / 实测 | 证据 |
|---|---|---|
| 登录方式 | 系统钥匙串里的 Google 账号登录。**真实实测：私有 HOME + 钥匙串链接下，`agy` 直接认出了你已有的登录**（`models` 与 `-p /usage` 都成功，没有浏览器、没有输入）——这个登录是你的 Antigravity 应用留下的。去掉链接则不认：同一个 `-p /usage` 打印 Google 登录网址并**等待 60 秒**（等你粘贴授权码），不会像官方文档说的那样「报错退出」 | 真实实测（有对照）；官方文档（install、headless）与之不符 |
| 登录状态检查 | 用 **`agy models`**：已登录约 3 秒返回模型清单（`slug<TAB>显示名`，进度行在 stderr）；未登录**立刻**失败（退出码 1，「Please sign in to view available models…」）。**不能用 `-p /usage` 来检查**（未登录时它会等 60 秒）。每个 attempt 的 preflight 问一次（缓存 60 秒）；成功的回答同时就是账号可用的模型目录 | 真实实测 |
| 额度报告 | `agy -p /usage`（已登录）：4 行制表符分隔——「Gemini Models / Claude and GPT models」×「Weekly / Five Hour Limit Remaining」，每行 `剩余百分比` 与 `重置时间`（ISO UTC）。`par health --probe` 把它放进 `facts.usage`。**这是套餐额度窗口，不是 API 计量**（见下一行） | 真实实测 |
| 套餐 vs 计量 | 额度按「每 5 小时 / 每周」窗口计，与官方文档描述的套餐额度一致；`useG1Credits=false` 保证不会超额后改花个人积分；API key 模式不可能被打开（下一行）。你账号的具体套餐名 CLI 没有给出；模型清单是 `gemini-3.8-flash-{high,medium,low}`、`claude-sonnet-4-6`、`claude-opus-4-6-thinking`、`gpt-oss-120b-medium`（按官方模型表，没有 Gemini 4 Argon/3.1 Pro，也没有 Claude 5.5 系列，符合 Free/Plus 或 Pro 试用档，不是 Ultra） | 真实实测 + 官方文档 |
| API key 模式 | 需要 `settings.json` 里 `modelProvider="gemini"` **并且** 环境里有 `GEMINI_API_KEY`。**两者都不会出现**：settings 由 Runtime 写、不含该键；子进程环境是干净的、只放行代理/证书变量 | 官方文档 + `test_agy_adapter.py`（环境白名单、泄漏测试） |
| ADC / 自定义端点 | `AGY_ADC_AUTH`、`GOOGLE_GEMINI_BASE_URL`、`GOOGLE_CLOUD_QUOTA_PROJECT` 同样不会被传入 | 同上 |
| 模型可用性 | 官方模型表按套餐区分；**你的账号能用哪些以 `agy models` 为准**（上一行）。Runtime 不替你选模型 | 真实实测 |
| **官方 FAQ 的使用条款提示（请你判断）** | FAQ 两条：①用第三方软件（举例 Claude Code、OpenClaw、OpenCode）配合你的 Antigravity 登录属于违反服务条款，可能导致账号被暂停或终止，建议改用 Gemini Enterprise 或 Google AI Studio 的 API key；②无头模式 `agy -p` 「面向直接脚本与 CI 工作流」，把 `agy` **包进第三方编码代理或应用**、或自动轮换账号，在**消费者套餐下不受支持**。本 Runtime 是你自己的本地脚本直接调用官方 CLI 的无头模式，贴近「直接脚本」；但若 P5 的 MCP 桥让 Claude Desktop 驱动任务、进而由它触发 `agy` 运行，就可能被解读为「包进第三方编码代理」。**这是账号层面的政策风险，不是技术问题，需要你决定**（选项见 P4 报告） | 官方文档（faq） |

## 6. 与旧 `gemini` CLI 的关系

- `gemini_legacy` 在配置里**永远不能启用**，不会有适配器，也**永远不是 `agy` 的降级后备**（有测试，包括「绕过校验手工构造的配置」）。
- 适配器代码里不允许出现把 `gemini` 当程序名的字符串（`test_no_real_calls.py` 用 `ast` 检查）。
- 私有 HOME 使 `agy` 看不到旧 CLI 的任何配置或凭据。真实步骤里还会对 `~/.gemini` 做**前后快照比对**（只看文件名、大小、mtime、哈希，不读内容）来证明没有变化（§11）。

## 7. 自动更新

官方：`agy` 内置静态链接的自更新器，后台运行，15 分钟防抖；关闭方法是环境变量 `AGY_CLI_DISABLE_AUTO_UPDATE=true`（官方文档写的是放进 shell 配置文件）。适配器**每次启动都在子进程环境里设置它**，探测命令也是。`agy update` 永远不由 Runtime 触发——升级由你决定，升级后按 §13 复核。

## 8. 事件格式（`parse_event`）

官方的 stream-json：一个 `init`，若干 `step_update`，一个 `result`。

| 官方事件 | 适配器的事件 | 说明 |
|---|---|---|
| `init`（`permission_mode`、`model`、`tools`） | `session`；**权限模式比请求的宽 → `warning`**（带 `model`） | 记下工具名清单供解析 stderr 用 |
| `step_update`，`agent_response`，`ACTIVE` | 不产生事件 | 只是文本增量；完整答案在 `result.response` |
| `step_update`，`agent_response`，`DONE` | `message` | 空白片段忽略 |
| `step_update`，`tool` | `tool`（只留工具名、`CommandLine`、文件类参数路径；**绝不保留输出**）；带权限类错误 → `warning`；名字含 web/browser/url/http/mcp → 带 `note` | |
| `step_update` 带 `subagent_info` | `warning` | |
| `step_update`，`user_input`/`checkpoint`/其他 | `other` | |
| `result` | `terminal`：`status` ∈ SUCCESS/ERROR/CANCELED（CANCELED、INTERRUPTED→CANCELED；其余→ERROR）、`vendor_status` 原值、`text`＝`response`、`message`＝`error`、`usage` | 用量键：`cache_read_tokens`→`cached_input_tokens`，`thinking_tokens`→`reasoning_output_tokens` |
| 其他 | `other`；非 JSON → `unparsed`（**原始行不保留**） | |

**真实事件流（1.3.5）与官方文档的差异**：①工具步骤的 `state` 除 `ACTIVE`/`DONE` 外还有 **`ERROR`**（被拒或失败的工具）；②工具步骤的 `output` 是摘要（如 `3 lines, 32 bytes`），不是全文；③`agent_response` 的 `DONE` 步骤可以**没有** `text_delta`（只有耗时和用量）；④最后的 `result` 可能带 **`denied_actions`**（结构化的软拒绝记录）；⑤`result.response` 可以是空串而 `status` 仍是 `SUCCESS`；⑥`init` 的 `tools` 有 60 项（§14）。

软拒绝的三个信号现在都被利用：结构化的 `result.denied_actions`（最可靠）、工具步骤的 `ERROR`（带具体命令）、stderr 的真实提示（`parse_stderr` 按真实措辞取出被拒的权限名；不再依赖猜测的措辞）。三者说的是同一件事，用同一个措辞，所以结果里只出现一条（`soft_denial_message`）；工具步骤那条另列（带命令行）。

## 9. 失败分类（`classify_failure`）

「成功」必须同时满足：有 `init`（证明权限模式）、权限模式不宽于请求、有 `result` 且 `status=SUCCESS`、没有失败的退出码。`classify_text` 的优先级：登录 → 模型 → 额度 → 限速（裸 429 在没有额度措辞时才算限速）→ 瞬时错误 → 其余一律 `EXECUTION_FAILED`（**绝不猜**）。

| 类别 | 官方明文的措辞 | 猜测的措辞（官方没写，见到真实样本前只是占位） |
|---|---|---|
| `AUTH_REQUIRED` | `authentication required`、`failed to retrieve token: secret keyring is locked` | `not logged in`、`token expired`、`invalid_grant`、macOS 的 `errSecInteractionNotAllowed`、`401` |
| `MODEL_UNAVAILABLE` | `model X is not recognized as a known model or custom model in settings` | `unknown model`、`not available`、`no access to` |
| `QUOTA_EXHAUSTED` | （无） | `quota`、`weekly limit`、`five hour limit`、`out of credits`、`usage limit` |
| `RATE_LIMITED` | （无） | `rate limit`、`too many requests`、`RESOURCE_EXHAUSTED`、裸 `429` |
| `TRANSIENT_ERROR` | （无） | `503`、`service unavailable`、`deadline exceeded`、`unexpected EOF` |
| `PERMISSION_BLOCKED` | `WAITING`（运行结束时在等输入） | |
| `UNKNOWN` | `CANCELED`/`INTERRUPTED`、没有 `result` 而退出码 0、被信号杀死、超时 | |

## 10. 取消与超时

官方：`SIGINT` 会得到 `INTERRUPTED` 状态。适配器发 `SIGINT`；Kernel 照常升级到 `SIGTERM`、`SIGKILL`。`SIGTERM` 的行为、CLI 自己的超时状态码、退出时是否清理子进程：官方都没写（§12）。

## 11. 真实步骤记录（2026-10-14/15，均已获你逐项批准）

1. **下载与校验**：官方清单 → `cli_mac_arm64.tar.gz`（55,069,121 字节）；SHA-512 与清单一致；压缩包只有一个成员；只解出它；**不执行**地核对 Google 签名；拷到 `vendor/agy/1.3.5/agy` 后复核 SHA-256 与签名不变（§1）。
2. **离线探测**（`agy --version`、`agy --help`，在适配器同款的干净环境 + 私有 HOME 里）：`1.3.5`；全部适配器用到的标志都在 `--help` 里；运行前后所有被监视的位置无任何变化（§2、§3）。
3. **账号探测**（已批准：`models`、`-p /usage`；不发送提示词、不耗模型额度）：已登录（经钥匙串链接）→ 模型清单 6 个、额度表 4 行，均秒级返回；`~/.gemini` 等位置无变化。
4. **对照实验**：同一私有 HOME 去掉钥匙串链接 → `models` 立刻失败（退出码 1），`-p /usage` 打印登录网址后等待（被我的 60 秒超时终止；退出码 124）。证明登录来源是钥匙串链接，也发现了官方文档与真实行为的差异。
5. **真实离线核对测试**（`PAR_REAL_AGY=1 PAR_REAL_AGY_ACCOUNT=1`）：8 项全过。
6. **通过 `par` 本身**：`par doctor` 全部 PASS；`par health --probe`（你的真实数据目录）：`antigravity: AVAILABLE … signed in … (version 1.3.5)`。
7. **你的真实 `providers.toml`** 已按批准启用 `antigravity`（备份 `providers.toml.par-backup-20261015`）；`profiles.toml` 未动。

8. **真实写入烟测（1 次真实调用，`gemini-3.8-flash-medium`，夹具项目，`request-review`）**：`init` 报告 `permission_mode='request-review'`、模型与 `cwd` 都符合；模型读了 `app/calc.py`，想运行测试被自动拒绝，随后结束，**9.3 秒，厂商状态 `SUCCESS`，退出码 0，`response` 为空，没有文件改动**；用量 `input 28,652 / output 485（其中 thinking 212）/ cache 0`（厂商自报）。Kernel 的判断：`FAILED`（`validation`：单元检查失败，补丁为空），结果里有软拒绝警告——**「软拒绝被正确判断」在真实运行里得到了验证**；「agy 独立完成夹具任务」**没有**达成。运行前后：`~/.gemini` 无任何变化（Antigravity 应用当时在运行），真实数据目录无变化，夹具工作树干净。该运行的私有 HOME 里 `agy` 留下约 13 MB（含一个 12.7 MB 的 `bin/webm_encoder`、对话库、日志）。原始输出留在作者本机的开发副本里，没有包含进本仓库；脱敏后的真实事件流进了测试样本（`tests/data/agy_real/`，随本仓库发布）。

10. **第三、四次真实写入烟测（获批的一批最多 3 次中的 2 次真实模型调用，`gemini-3.8-flash-medium`，夹具项目，规则策略，工作目录路径含空格）**：
    - **第三次**：厂商日志 `CLI settings initialized: permissions=&{Allow:[write_file(app/) write_file(<工作副本>/app/)] ...}, toolPermission=request-review`——CLI **读到并保留了**我们写的规则（改写后的 `settings.json` 里 `permissions.allow` 仍在）；`replace_file_content` 步骤状态 `DONE`（不再是 `ERROR`），工作副本里的 `app/calc.py` 已改对；stderr 没有软拒绝提示。随后这次运行以厂商状态 `ERROR` 结束：`API error (attempt 1): request failed: Post "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse": EOF`——一次**掉线**，不是工作失败。适配器此前把它归为 `EXECUTION_FAILED`，现已归为 `TRANSIENT_ERROR`（`INTERRUPTED`，已改对的补丁保留，重试时由接力带到下一次尝试）；真实事件流已存为测试样本 `tests/data/agy_real/write_ok_then_eof.jsonl`。
    - 之后一次启动在**预检**阶段因同一类掉线失败（`agy models`: `Eligibility check failed: Post …loadCodeAssist: EOF`），**没有调用模型、没有耗额度**；你机器走系统代理，到 Google 的连接偶尔被掐断。
    - **第四次（完整成功）**：`status=SUCCESS`；用量 input 58,110 / output 1,358 / thinking 503；Kernel：`REVIEW_REQUIRED`，补丁 1 个文件 +1 −1，边界 0 违规，Kernel 自己运行的检查 `unit` PASS，真实项目未动，`~/.gemini` 与真实数据目录无变化。真实事件流已存为 `tests/data/agy_real/write_success.jsonl`。额度窗口：Gemini 模型周额度 99%、五小时额度 99%。
    - 两种写法（相对 `app/` 与绝对 `<工作副本>/app/`）同时在场，**没有单独验证哪一种起作用**；留着两种，成本为零。备选 `--mode accept-edits` 没有用到（规则已够）。
9. **第二次真实写入烟测**（`gemini-3.8-flash-medium`，夹具项目，`request-review`，带环境提示）：见 §4。`FAILED`（`validation`），夹具工作树干净，`~/.gemini` 与真实数据目录无变化。原始输出留在作者本机的开发副本里，未随本仓库发布；脱敏后的真实事件流在 `tests/data/agy_real/`。

**官方文档与真实行为的差异（本次发现）**

| 官方文档说 | 真实 1.3.5 |
|---|---|
| 无终端、未登录时 `agy -p …` 报 `authentication required` 退出，不会卡住 | `-p /usage` 打印登录网址并**等 60 秒**；只有 `models` 立刻失败 |
| `--print-timeout` 默认 5 分钟 | `--help`：默认 `0s`，「0＝等到这一轮结束」 |
| 标志表里没有 `--mode` | `--help` 有 `--mode`（`accept-edits`、`auto`、`plan`） |
| `--effort` 取值 low/medium/high | `--help`：`low\|medium\|high\|xhigh\|max` |
| `agy --help` 的输出位置未提 | 打印在 stderr，退出码 0 |
| 工具步骤 `state` 是 `ACTIVE` 或 `DONE` | 还有 `ERROR` |
| `request-review`：读写工作区内文件自动允许 | 无头模式：读自动允许，**写被软拒绝**（要有 `permissions.allow` 的 `write_file` 规则，或其他放宽方式） |
| 被拒的工具「在 stderr 提示、退出码 0」 | 属实；另外 `result` 带结构化的 `denied_actions`，且模型可能因此**不输出任何内容**（`response` 空、状态仍 `SUCCESS`） |

**还没做（需要你另行批准的真实步骤，会调用模型、消耗你的套餐额度）**：只读烟测、「只读任务里要求写文件」的软拒绝观察、写烟测。见 §12。

**P4 里的一次事故（我造成的，已还原）**：一项变异检验（M126，故意把登录计划指向 `real_home()`，用来证明测试抓得到这种错）在运行时真的改了你机器上的东西：`~` 的权限 750→700、`~/.gemini` 的权限 755→700，并新建了 `~/.gemini/antigravity-cli/settings.json`（Runtime 自己写的那份只读预设，261 字节）。它**不是产品行为**——真实运行永远用私有 HOME；而是测试工具对真实 home 的误写，两个变异检验批次各触发一次。你批准后已还原（权限改回、只删了那一个文件和空目录；`~` 上多了一个 macOS 自己管理的空 `com.apple.provenance` 标记，`~/.gemini` 上本来就有，无害）。根因的修复：①私有 HOME 一律拒绝等于真实 home 的目录（上面）；②变异检验在每个变异前后核对真实 home 与 `~/.gemini` 的权限位和条目名，**任何变异改动它们就立刻中止整个运行**；③M126 现在由这条拒绝抓住，抓住时什么都没动（用假 home 验证过）。因此本文里「运行前后 `~/.gemini` 无变化」指的是每次真实探测/烟测自己的前后快照（这些快照确实一致），不是对整个 P4 期间的担保。

## 12. 未验证项清单（每一项都要在真实运行里确认，并回写本文）

| # | 问题 | 状态 | 怎么验证 | 影响 |
|---|---|---|---|---|
| 1 | `--input-format stream-json` 不带 `-p` 能否在无终端下工作；退出码 | **未验证** | 真实只读烟测（可先用一个不存在的模型名做零额度预检：它应在调用模型之前就报错，能看到 stream 模式能否启动、标志是否被接受） | 整个调用方式 |
| 2 | 私有 HOME + 钥匙串链接下，CLI 能否找到登录 | **已验证：能**（有对照） | — | — |
| 3 | 私有 HOME 里没有「首次启动」标记时，**模型运行**会不会被引导/信任提示卡住 | 部分：`models`/`-p /usage` 没被卡住，`onboarding.json` 自动生成；模型运行**未验证** | 真实烟测 | 可能需要从登录 HOME 复制种子状态 |
| 4 | `agy -p /usage`、`agy models` 无登录时的退出码和措辞，会不会弹浏览器 | **已验证**：`models` 立刻失败（退出码 1）；`-p /usage` 打印登录网址并等 60 秒；**是否同时打开了浏览器窗口：不确定**（我这边看不到） | — | 已改用 `models` 做登录检查 |
| 5 | `--print-timeout` 在 stream 输入模式下是否被接受/生效 | 标志存在（`--help`）；**stream 模式下的效果未验证** | 烟测 | 超时权威 |
| 6 | `strict` 下写文件是否真的被软拒绝 | **未验证**（`request-review` 下的**命令**被软拒绝已验证，三个信号与措辞已取得，见 §4） | 只读任务里要求写文件（需再批准一次真实调用） | 软拒绝检测 |
| 7 | `request-review` 下工作区内写文件是否真的自动允许 | **已验证：不允许**（第二次写烟测：`ReplaceFileContent` 被软拒绝，见 §4） | — | 已改用 `write_file` 允许规则（第 20 项待验证） |
| 8 | `init.tools` 的真实工具清单；哪些是网络/浏览器类 | **已验证**：60 项，见 §14。**哪些在各预设下真被拒、哪些会自动放行：只验证了 `view_file`（放行）与 `run_command`（拒绝）** | 其余需要有针对性的真实提示词（可选，需单独批准） | `note` 规则、权限模型 |
| 9 | 套餐额度用尽、限速的真实措辞与退出方式 | **未验证**（只有碰到才知道；`/usage` 的重置时间可用来填 `reset_at`） | 碰到时记录 | 分类关键词 |
| 10 | 无头运行是否写入对话历史、写到哪里；遥测是否真的关闭 | 部分：**写入**——每个 attempt 的私有 HOME 里有对话库（`conversations/<id>.db`）、`brain/`、日志、`bin/webm_encoder`（12.7 MB），共约 13 MB；**不在你真实的 `~/.gemini`**。遥测开关效果**未验证**（§3） | — | 持久化报告；磁盘占用 |
| 11 | 子代理/`define_subagent` 能否绕过 `strict` | **未验证** | 专门的烟测提示词（可选，需单独批准） | 权限模型的可信度 |
| 12 | `agy` 是否写 `~/.gemini`（即使 HOME 是私有的）、是否读真实 HOME | **已验证**（含一次真实模型运行）：不写、不读（有对照；前后快照无变化） | — | 隔离的完整性 |
| 13 | `SIGTERM`、超时的行为；是否留下子进程 | **未验证** | 受控的取消烟测 | 取消路径 |
| 14 | `--mode plan` 与 `strict` 的关系；能否再给只读任务加一层 | **未验证**（`--help` 里有） | 对照烟测 | 只读任务的纵深防御 |
| 15 | `--disable-slash-commands` 是否值得加（防止提示词里的 `/…` 被当作命令/技能展开） | **未验证** | 烟测 | 提示词注入面 |
| 16 | 内置技能（`builtin/skills`，如 `permissioned-github`、`automation`）会不会把 Worker 引向我们没打算开放的行为 | **未验证**（烟测里模型没用到技能） | 更多真实运行 | 权限模型 |
| 17 | 加了环境提示之后，模型是否会直接改文件而不是去跑测试 | **已验证：会**（第二、三、四次写烟测：读文件后直接调用 `replace_file_content`，没有尝试运行命令） | — | — |
| 18 | 是否值得让 Worker 能在 agy 的系统沙箱里运行合同里的检查命令（`permissions.allow` 里加精确的 `command(...)` 规则）：与 Codex 的能力对等 vs 放宽权限；规则的匹配语义（前缀？整行？）官方没说清 | **未决定**（放宽权限，需你决定；需要先把检查命令从合同传给适配器） | — | 写任务的质量 |
| 19 | `agy` 是否自动发现工作区的 `AGENTS.md`（P4 计划里的「规则来源」检查） | **未验证**（日志里没有相关行；Kernel 的提示词已内联 `AGENTS.md`，所以不依赖它） | 一次有针对性的只读真实提问（需批准） | 规则来源的完整性 |
| 20 | `permissions.allow` 里的 `write_file(...)` 规则：相对写法（`app/`）和绝对写法（`<工作副本>/app/`，路径可含空格）哪一种被 1.3.5 匹配；规则是否限定了路径；规则是否被 CLI 保留 | **部分已验证（第 10 项）**：两种写法同时在场时规则被读到、保留、生效，写入通过（工作目录含空格）。**未验证**：哪一种单独起作用；规则外的文件是否仍被拒（只有假 agy 的模拟） | 若要精简规则再做对照（每种一次真实调用） | 规则可否缩减 |
| 21 | 备选：`--mode accept-edits`（厂商自述「auto-approve file edits, prompt for commands」）能否让写通过；`init.permission_mode` 会报什么 | **已实现（`PAR_AGY_WRITE_STRATEGY=accept-edits` / `smoke_agy.py --write-strategy accept-edits`），真实未试**（规则策略已成功，不需要） | 需要时再试 | 备选通道 |

## 13. 升级复核清单

升级 `agy` 之后：`par health --probe`（版本是否是已测版本）→ 重跑 §12 里与变化相关的项 → 跑 `tests/tools/smoke_agy.py`（只读，需单独批准）→ 更新本文的版本号与 `TESTED_VERSION`。

## 14. `init.tools` 的真实清单（1.3.5，60 项）与它们意味着什么

真实 `init` 事件列出的工具（**列出≠可用**：是否能自动执行取决于权限预设，已验证的只有 `view_file` 放行、`run_command` 拒绝）：

- **读取/搜索**：`view_file`、`list_dir`、`grep_search`、`find_by_name`、`read_resource`、`list_resources`、`command_status`
- **修改文件**：`write_to_file`、`replace_file_content`、`multi_replace_file_content`、`sed_file`、`notebook_edit`
- **执行**：`run_command`、`send_command_input`、`notebook_execution`、`run_workflow`
- **网络/浏览器**（约 20 项）：`open_browser_url`、`execute_browser_javascript`、`browser_*`（点击、输入、截图、DOM、网络请求……）、`capture_browser_*`、`click_browser_pixel`、`read_browser_page`、`list_browser_pages`、`browser_subagent`；`read_url_content`、`search_web`
- **MCP 与插件**：`call_mcp_tool`、`list_plugin_accounts`、`search_marketplace`
- **子代理与任务**：`define_subagent`、`invoke_subagent`、`manage_subagents`、`manage_task`、`schedule`、`wait`、`wait_5_seconds`、`finish`
- **其他**：`ask_permission`、`ask_custom_permission`、`ask_question`、`list_permissions`、`manage_inbox`、`send_message`、`generate_image`、`delete_knowledge`

值得你知道的几点：①浏览器类工具会驱动浏览器（`~/.gemini` 里有一个 `antigravity-browser-profile`）；官方说 `request-review` 对「写、命令、网络」类工具要审批，浏览器类应属此类，但**未验证**；②`schedule`、`send_message`、`manage_inbox`、`search_marketplace` 这类工具官方文档没有对应说明，它们在无头预设下做什么**未验证**；③`define_subagent`/`invoke_subagent` 就是 §4 里「子代理可能绕过 `strict`」的来源。适配器对名字含 `browser`/`web`/`url`/`http`/`mcp` 的工具调用加 `note`，对子代理加警告；Kernel 的文件比对兜底。要把这些逐项变成「已验证」需要有针对性的真实运行（可选、需单独批准），在 P8 之前建议做。
