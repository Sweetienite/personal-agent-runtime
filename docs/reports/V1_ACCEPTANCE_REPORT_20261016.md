# 阶段报告：P8 收尾与 V1.0 验收核对（2026-10-16）

> 本报告引用的原始证据文件（`evidence/*.txt`、`*.md`）留在作者本机的开发副本里，没有包含进本仓库；报告正文本身未改动。

> 接上一份报告（[P8_REPORT_20261015.md](P8_REPORT_20261015.md)：并发 2、写入开关、两次真实写入试运行）。正文写于版本 `1.0.0.dev8`（阶段 P8）；文末「追加」记录了之后发生的事——**Claude Desktop 已接通并验证，Harness 基线已确认，版本现为 `1.0.0`**。

## 阶段
P8 收尾：检查 `par_trial-0002` 与 Claude Desktop 接入、恢复演练、运维手册、遗留问题定状态；并按总方案第 13 节核对 V1.0 的最终交付清单与「最终反例」。

## 结果
| 项 | 结果 | 一句话 |
|---|---|---|
| P8 Gate：真实项目最低风险任务完成一次被验证的跨 Provider 工作 | **PASS** | `par_trial-0001`（Codex）与 `par_trial-0002`（agy）：两次都由 Kernel 自己量改动、自己跑检查；验收是你在控制台里点的（写权限：0002 是你在控制台里点的，0001 是你在对话里批准、由我执行，记录里写明）；补丁由我在你授权后应用并提交 |
| P8 Gate：主控切换、额度耗尽都能安全停止或恢复 | **PASS** | 主控切换在你真实数据的副本上演练通过（被替换者提交被拒绝、新主控可提交、切回成功）；Worker 额度耗尽见附录 A 的 T07–T09 |
| P8 Gate：Harness 同步检查健康 | **PASS，等你确认基线** | FAIL 0；与基线的 28 处差异**全部**来自新建的 `par_trial`，四个原有项目和 Core 没有任何变化。要让退出码回到 0 需要你确认：`par harness check --save-baseline` |
| P8 Gate：全部关键功能可由用户可视化操作 | **PASS** | 控制台三页（按你的要求做成中文轻量版）；你自己在里面批准、验收了两个真实任务 |
| P8 Gate：遗留问题有明确状态 | **PASS** | 见「问题与限制」 |
| P8 施工 2：两套主控能切换但不复制历史会话 | **PASS** | 切换只传「接管包」（任务树和 Runtime 量到的事实），不传任何对话 |
| P8 施工 3：运维手册 | **PASS** | [USER_GUIDE.md](../USER_GUIDE.md)（含第 13 节）、[OPERATIONS.md](../OPERATIONS.md)、[ROLLBACK.md](../ROLLBACK.md) |
| P8 施工 4：从备份恢复的演练 | **PASS，并发现一个会丢数据的备份误区** | 只拷 `runtime.sqlite3` 会丢最新数据；已写进文档并由演练工具复核 |
| P8 施工 1：再经你批准在 Runge 里做小范围写任务 | **未做（你已决定推迟）** | 要先处理 STATE 门禁、敏感账户文件与读范围 |
| P5 Gate：Claude 通过支持的接口创建/查询一次真实任务 | **等你** | 桥已测试，Codex 侧已写入配置；Claude Desktop 还没接通，需要你 ⌘Q 一次（见「需要我批准的动作」） |

## 做成了什么
1. **检查你上一条消息里的两件事**：`par_trial-0002`（agy）运行 70 秒、所有检查通过、越界 0；你在控制台里批准了写权限、也点了验收；补丁没有真的落进项目，我按你的授权用 `git apply --check` 检查后应用并提交（`f4bbd7e`，项目自己的 13 个测试通过，基线干净）。**Claude Desktop 没有接通**：它的配置里没有 `par`，没有备份也没有日志，说明你那次的操作没有生效。原因有两个：这个应用运行时会把自己内存里的配置写回文件、抹掉别人的改动；而我的接入小工具判断「应用是否在运行」的办法在你这台 Mac 上认不出它。后者已修好（`e83f147`）：现在只需要 ⌘Q 一次，其余它自己做。
2. **恢复演练**（`tests/tools/restore_drill.py`，16 项核对全部通过；证据 [restore_drill_20261016.txt](evidence/restore_drill_20261016.txt)）：把你真实数据目录整个拷到临时位置，在副本上核对台账完整性、任务/事件/attempt/批准的数量与每个任务的状态、`par doctor`、`task list/result/diff` 在新位置读出证据、项目与配置加载；再在副本上演练主控切换 Claude → Codex → Claude；原件只读、没有变化，副本结束即删。
3. **演练发现的真实问题**：台账是 WAL 模式，最新的数据先写在 `runtime.sqlite3-wal` 里。我第一版演练脚本就因此少数了数据（读到 1 个任务，实际 2 个）；只拷 `runtime.sqlite3` 的备份也一样（2 个任务只剩 1 个、26 条事件只剩 11 条）。已把「备份要拷整个数据目录，或用 `sqlite3 … .backup`」写进 [ROLLBACK.md](../ROLLBACK.md) 与使用指南，并在你的真实台账上试过 `.backup`（2 个任务、26 条事件、完整性 ok）。
4. **运维手册补全**：使用指南新增第 13 节（启动/停止/重启/升级/重新登录/审计记录在哪/数据会长多大与怎么清/导出/删除运行数据/完整卸载）；第 0、1、9、10 节改成当前事实。「怎么清」在你的数据副本上验证过：删掉已验收任务的 `workspace/` `home/` `tmp/`（14 MB → 84 KB）后，`status/result/diff/events/list` 照常工作，补丁仍在。
5. **卸载核对与演练**（证据 [uninstall_rehearsal_20261016.txt](evidence/uninstall_rehearsal_20261016.txt)）：Runtime 在这台机器上碰过的地方只有——Codex 的 `~/.codex/config.toml`（一条 `par`，有备份）、数据目录、项目目录、你新建的 `~/Documents/par_trial` 和 `projects.toml` 里它的条目；没有 LaunchAgent、没有改 shell 配置、Claude Desktop 配置里没有 `par`。在一份 Codex 配置**副本**上演练了卸载：`codex mcp remove par` 只拿掉那 7 行，别的一字不动，真实配置没碰。
6. **T01–T24 验收矩阵**（`tests/tools/acceptance_matrix.py`）：把总方案的 24 项故障逐项对应到 113 个现有测试，一条命令逐个重跑，**113/113 通过**（附录 A）。对照时我发现 7 处覆盖缺口并补上：T04、T11、T15、T16、T17、T18、T20；顺带改正一个标签错误（并发测试的 T17/T18 名字原先对调了）。每处新增的保护都有变异检验（M613–M619），全部被发现。
7. **安全加固（来自 T20）**：Host 不对、Origin 不对、没有令牌或令牌错的 POST，原来在门口被拒绝但不留痕；现在也留一行审计（动作名 `refused`），**每个原因每个来源每分钟最多一行**，恶意网页发几千个请求也撑不大审计文件；这一行从不写出对方的 Host、Origin 或令牌，写不进去也不改变拒绝。
8. **两处措辞修正**：控制台「验收通过」对话框原来写「要应用补丁，是以后另一项单独的批准」，容易让人以为 Runtime 会有应用补丁的功能（你上次点完验收以为已经应用，补丁其实没落进项目）——现在明说「Runtime 没有应用补丁的功能，要放进项目由你自己 `git apply`」；`par doctor` 原来一直说「所有真实项目都不可写」，现在会写明哪个项目是你打开了写入的。
9. 版本改为 `1.0.0.dev8`（阶段 P8）；README、CHANGELOG 同步。

## 文件列表
新增：`tests/tools/restore_drill.py`、`tests/tools/acceptance_matrix.py`；证据 `docs/reports/evidence/{restore_drill,t01_t24_matrix,uninstall_rehearsal}_20261016.*`；本报告。
修改（代码）：`src/par/__init__.py`（版本）、`src/par/web/{audit,server}.py`（门口拒绝的审计）、`src/par/web/static/app.js`（验收措辞）、`src/par/doctor.py`（写入状态的说法）。
修改（测试）：`tests/unit/{test_concurrency,test_agy_adapter,test_takeover,test_workspace,test_kernel_run,test_web_api,test_web_static,test_project_write_gate}.py`、`tests/tools/mutation_check.py`。
修改（文档）：`README.md`、`docs/{CHANGELOG,OPERATIONS,ROLLBACK,USER_GUIDE,WEB}.md`、`docs/specs/LEAN_CONSOLE_SPEC.md`。
你机器上：没有新的改动（演练只读原件，副本已删；卸载演练用的是配置副本）。提交：`7bfdfd1`（恢复演练、备份/恢复文档、运维手册、版本）和紧随其后的一次提交（T01–T24 矩阵与补的测试、门口拒绝的审计、`par doctor`、本报告）；`git log` 可查。

## 测试
- 全量：`python3 -B -m unittest discover -s tests -t .` → **1404 项，全部通过**（另有 23 项默认跳过：需要真实 Codex / agy / Harness 的选择加入式核对），543 秒（上一份报告是 1386 项；净增 18 项：接入小工具 10、T01–T24 对照后补的 8）。
- 变异检验：**317 项**（本阶段新增接入小工具的 M431、M439–M444，和 T01–T24 对照后补的 M613–M619），**全部被发现**：前 310 项在最终代码上完整重跑一遍（`310/310 mutations detected; problems: 0`，约 1 小时），后加的 7 项另行重跑，也全部被发现。
- 总方案 T01–T24：**113/113 通过**，见附录 A；一条命令可重跑：`python -B tests/tools/acceptance_matrix.py`。
- 恢复演练：16 项核对全部通过（含主控切换演练）。
- 真实调用：本阶段没有新的模型调用；上一阶段的两次真实写入（Codex 1 次、agy 1 次）都由你批准。

## 与 Harness 兼容性
`par harness check`：FAIL 0 | WARN 13 | 与 2026-10-14 基线 28 处差异——**全部**是新项目 `par_trial` 出现了（发现列表加上它的各项事实，以及「与 Core 同步的项目数」3→4）；Runge、建模全流程、机场建模及 CFD、毕业论文四个原有项目没有任何 CHANGED 行。我**没有**改基线；是否接受由你决定：`par harness check --save-baseline`（旧基线会保留为 `baseline.prev.json`）。`par doctor` 全过。Runtime（程序）没有写过任何项目的 `.ai/`、`AGENTS.md`、`CLAUDE.md`；`par_trial` 里的 STATE.md 是我按你的授权手工更新的（`1d13978`），不是 Runtime 写的。

## Provider / 记忆 / 权限 / 凭据 / 计费安全
- **记忆**：没有新建任何长期记忆文件、全局规则或定时任务；项目 STATE 仍是唯一权威。
- **权限**：写入开关只由你敲命令打开（`par_trial` 一个）；每个写任务都经你批准；批准、验收都没有给主控的工具；补丁不会被自动应用。
- **凭据**：没有读取任何凭据文件；备份会带上每个任务的私有副本和 agy 的私有 HOME，文档已提醒放在只有你能读的位置。
- **计费**：没有 API 计费路径（配置拒绝 `additional_paid_credits = true`，Codex 适配器遇到 API key 登录就拒绝运行）；真实调用用的是你的订阅登录。
- **你终端里正在运行的控制台**（`par serve --allow-write`，已开了约 4 小时）：只绑 127.0.0.1、有令牌，但处于可写模式；不用时在那个终端 Ctrl-C 关掉即可。

## 问题与限制（遗留问题与状态）
| # | 问题 | 状态 |
|---|---|---|
| 1 | Claude Desktop 没接通；Claude、Codex 都还没在真实聊天里调用过 `list_tasks` | **等你**：⌘Q 一次（我先把后台等待的接入小工具开着）；接通后各做一次「贴起手提示词 → 让它调用 `list_tasks`」，`par coordinator status` 里的「从未出现」就会变成时间 |
| 2 | Harness 基线有 28 处差异（都是新项目） | **等你决定**：`par harness check --save-baseline` |
| 3 | `airport_cfd` 不能开写入：68 处未提交改动、STATE 18.6 KB 超过 15 KB 上限、没有生效的主合同 | **你的项目治理**，按你的决定推迟到最后；Runtime 不替你提交、不改 `.ai/` |
| 4 | Runge 之前要做：STATE 对账（153 KB）、收紧读范围、处理敏感账户文件 | **推迟**（你的决定）；在做完之前 Runge 一直是 `WRITE_DISABLED` |
| 5 | Worker 能读到你磁盘上任何你能读的文件（Codex 沙箱不限制读） | **已知限制**，所以第一次试运行选了没有敏感文件的小项目；重要项目之前必须收紧（SECURITY.md） |
| 6 | 同时跑 2 个 Codex Worker 会共用你本机的 `~/.codex`，额度消耗更快 | **已知限制**（设置里随时可改回 1） |
| 7 | Codex 额度用尽的**真实措辞**没有取得（额度已恢复），分类依据是官方源码措辞与合成样本 | **已知限制**，等下次真的遇到时用 `tests/tools/capture_codex_sample.py` 补进来 |
| 8 | WorkBuddy / TRAE 没有可用的程序接口 | **设计如此**：只做人工交接，结果仍由 Kernel 验证 |
| 9 | 总方案写的是「Web 四页面」，现在是三页 | **你要求的**（轻量版，没必要的东西不放）；模型配置页、健康页的功能由 `par health` 和配置文件承担 |
| 10 | 路线 B（Runtime 自己驱动主控） | **以后再说**（你的决定） |
| 11 | 一个与 Runtime 无关的 `python3 -m http.server 8934`（进程 44124）已运行 34 天 | **不是 Runtime 的**；想关可 `kill 44124` |
| 12 | `par_trial-0001/0002` 停在 `ACCEPTED`，没有 `close` | 无害；想归档可 `par task close 编号` |

## 需要我批准的动作（逐项，不合并）
1. **退出一次 Claude Desktop（⌘Q）**：这是接通 Claude 的唯一一步。我会先在后台启动接入小工具（备份配置、只加 `par` 一项、写后自检、自动重新打开应用）；它最多等 2 小时，你方便时退出即可。退出会结束你当前在 Claude Desktop 里的所有会话（包括这个）；重新打开后回到这个会话说一声「继续」。
2. （可选）接受 Harness 基线：`par harness check --save-baseline`。
3. （可选）`kill 44124`；在跑 `par serve --allow-write` 的终端里 Ctrl-C。
4. （之后）把版本定为 `1.0.0`：条件是 1 做完且 Claude / Codex 各成功调用过一次 `list_tasks`，以及你对基线的决定。这一步只是改版本号并提交，我会在你确认后做。

## 回滚
非破坏性：`git switch -c back-before-dev8 e83f147`（回到本阶段之前的提交）。你机器上本阶段没有新的改动；演练的副本已自动删除。要撤销前几阶段对你机器的改动，见 [ROLLBACK.md](../ROLLBACK.md)。

## 是否达到 Gate
- **P8 Gate**：除「Harness 同步检查健康」需要你确认基线外，**全部达到**。
- **V1.0 最终验收**：核对结果见附录 B、C。**没有出现任何一条「最终反例」。** 还差两件只有你能做的事：接通 Claude Desktop（P5 Gate 的真实调用）和确认基线；做完即可定为 `1.0.0`。

## 下一阶段建议（只是建议）
1. 先接通 Claude Desktop、两个主控各做一次真实 `list_tasks`，再定 `1.0.0`。
2. 之后按你的节奏：整理 `airport_cfd`（提交或收起 68 处改动、精简 STATE、建主合同）→ 再在它上面做第一个小范围写任务；Runge 要先收紧读范围、做 STATE 对账。
3. 路线 B 不动。

---

## 附录 A：总方案 T01–T24 逐项对应测试与结果

每一行列出的测试都由 `python -B tests/tools/acceptance_matrix.py` 逐个重新运行（含真实 Codex 的离线核对，不调用模型）；完整的测试编号见 [evidence/t01_t24_matrix_20261016.md](evidence/t01_t24_matrix_20261016.md)。

| T | 场景 | 判定 | 测试 | 要点 |
|---|---|---|---|---|
| T01 | Claude 主控 + Codex Worker 固定；不做模型评分 | COVERED | 5/5 通过 | 选择 Worker 不启动任何程序；路由是确定性的；Claude 宿主不会当 Worker |
| T02 | Gemini 旧版已安装但未启用 | COVERED | 4/4 通过 | 旧版 gemini 不能启用、不会得到适配器、doctor 报配置 FAIL |
| T03 | Codex 的 PATH 入口损坏 | COVERED | 4/4 通过 | 从不在 PATH 上找可执行文件，只用配置里写明的绝对路径；相对/缺失/他人可写的一律拒绝；包装脚本被拒绝；真实 Codex 的离线核对（需 PAR_REAL_CODEX=1，本次已运行） |
| T04 | agy 未安装 | COVERED | 5/5 通过 | 缺失的 agy 是干净的拒绝并报 UNAVAILABLE 及原因，绝不换成 gemini（本阶段补了 UNAVAILABLE 的测试） |
| T05 | 模型 slug 不存在 | COVERED | 4/4 通过 | 归为 MODEL_UNAVAILABLE，只挡那个 Worker，不假装成功 |
| T06 | 退出码 0 但终态是错误 / 工具软拒绝 | COVERED | 4/4 通过 | 退出码 0 不等于成功；软拒绝由 Kernel 判断，不因厂商说成功就成功 |
| T07 | Provider 额度用尽 | COVERED | 7/7 通过 | 整个 Provider 被挡到厂商给的时间；只**提议**备用、不自己切换；pause 策略就暂停；没有按量计费的路径 |
| T08 | 429 但不是确定的额度耗尽 | COVERED | 5/5 通过 | 裸 429 归为限速、只让那个模型冷却，绝不变成额度耗尽 |
| T09 | Worker 写了 3 个文件后额度耗尽 | COVERED | 3/3 通过 | 已写的 3 个文件留在差异里；接力的下一个 Worker 从它们接着做，而不是重来 |
| T10 | 原进程仍在写入时触发接力 | COVERED | 4/4 通过 | 旧 Worker 还活着时第二个写入者不会启动，项目一直锁着，直到你处理旧进程（用「Kernel 崩溃、Worker 还在」这个场景证明） |
| T11 | 主控退出、所有 Provider 不可用 | COVERED | 4/4 通过 | 没有任何适配器时，任务、结果、补丁、接管包、切换主控照常可用（本阶段补了专门的测试；恢复演练在真实数据上做过同样的事） |
| T12 | Kernel 被强制终止后重启 | COVERED | 4/4 通过 | 孤儿 attempt 被标成孤儿、不假装完成；已提交的事件在 SIGKILL 后仍在 |
| T13 | 同一个幂等键重复提交 | COVERED | 3/3 通过 | 返回原任务，不多跑；任务不会跑两次 |
| T14 | 普通 Worker 尝试写 .ai/STATE.md | COVERED | 5/5 通过 | STATE、规则文件即使在允许的文件夹里也算越界，不进入验收；补丁导入也拒绝；写入就绪体检挡住不合格的项目 |
| T15 | 普通 Worker 读取/提交账户 JSON | COVERED | 4/4 通过 | 账户 JSON 路径是敏感路径：不进 Worker 的副本、写入被拒绝且不回显文件名（本阶段把账户 JSON 加进了端到端的用例） |
| T16 | 未授权的 git add -A / 删除 / push | COVERED | 7/7 通过 | Worker 的私有副本没有远端：push 无处可去，add/commit/删除都只发生在副本里（本阶段新增的测试）；检查命令有白名单；没批准的写任务不会启动 |
| T17 | 同时 2 个 Worker 改不同的地方 | COVERED | 3/3 通过 | 两个写任务同时在跑：各自的工作副本、各自的差异、各自的检查结果，真实项目都没动（同一个项目按设计不会有两个写入者，所以用两个项目演示）；容量 2 满了第三个被拒绝 |
| T18 | 同时 2 个 Worker 改同一个文件 | COVERED | 5/5 通过 | 同一个项目不会同时有两个写入者（项目锁）；第一个的结果落进项目之后，第二个任务被「项目变了」的确认挡住、不覆盖已落地的内容，它的补丁是对确认后的状态做的；补丁对不上会被发现 |
| T19 | 模型输出伪造「tests PASS」 | COVERED | 5/5 通过 | Worker 的「测试通过」只是没验证的文字；只认 Kernel 自己跑的检查 |
| T20 | UI：缺令牌 / 非法 Origin / 重复批准 | COVERED | 8/8 通过 | 无令牌或令牌错 401、外来 Origin 403、重复批准 409、只读模式 403；这些被拒绝的写请求都留审计行（门口拒绝的每个原因每分钟一行，本阶段补上）；审计写不进去就不执行 |
| T21 | 日志里有模拟的 API key / 邮箱 | COVERED | 6/6 通过 | 事件表、日志、结果、控制台回复里都经脱敏（API key 的端到端用例；邮箱在脱敏函数层测试） |
| T22 | 主控从 Claude 手动切到 Codex | COVERED | 5/5 通过 | 接管包只有任务事实和指针，没有对话和 Worker 的话；已验收的任务不会再跑；被替换的主控不能再提交 |
| T23 | 工作区有未提交代码、基线变化 | COVERED | 4/4 通过 | 基线漂移要你确认；写任务对未提交改动更严；拒绝漂移就取消任务；脏的项目拒绝写任务 |
| T24 | 调度器卸载 / 停用 | COVERED（设计 + 演练） | 5/5 通过 | Runtime 没有常驻调度器；可选的巡检只跑只读检查、只写自己的两个文件、不常驻；被检查的文件夹逐字节不变；Claude 接入工具的 --remove 只拿掉 par 一项（另有在你真实 Codex 配置副本上的卸载演练，见证据文件） |

合计：113 项通过，0 项失败，0 项跳过，0 项找不到。

对照时发现并补上的覆盖缺口（都已有测试并经变异检验）：**T04**（agy 缺失要报 UNAVAILABLE，原来只有 Codex 有这个断言）、**T11**（没有任何适配器时，任务、证据、接管包仍可用）、**T15**（账户 JSON 加进端到端用例）、**T16**（Worker 的私有副本没有远端，`push` 无处可去，`add/commit/删除` 碰不到真实仓库）、**T17**（两个写任务同时在跑，各有各的工作副本、差异和检查结果）、**T18**（同一个文件：第一个结果落进项目后，第二个任务被「项目变了」的确认挡住、不覆盖）、**T20**（门口被拒绝的 POST 也要留审计，每个原因每个来源每分钟一行）。原先把并发测试标成 T17/T18 的名字对调了，已改正。

两点要如实说明：**T17/T18** 里「两个 Worker 同时改同一个项目」按设计不会发生（项目锁：同一个项目一次只做一件事），所以 T17 用两个项目演示隔离，T18 证明的是「不能同时」加「落地之后第二个任务被挡住、不覆盖」；**T10** 用「Kernel 崩溃但 Worker 还活着」这个场景证明旧写入者没处理之前不会启动第二个写入者。

## 附录 B：最终交付清单（总方案第 13 节）

| 交付项 | 状态 | 依据 |
|---|---|---|
| 可运行的 `par` CLI、Kernel、任务数据库、Profile 配置与 Provider 健康检查 | ✅ | `par version / doctor / health`；1396 项测试；台账为 SQLite（只追加、有触发器） |
| Codex 与 agy 两个独立的受控 Worker；不通的功能有真实状态和替代流程 | ✅ | 两次真实写入各一个；`gemini` 旧版永远停用；WorkBuddy / TRAE 走人工交接 |
| Claude Desktop MCP 主控入口与始终可用的手动 CLI/UI 路径 | ✅ 桥与手动路径；⏳ 真实接通 | 7 个受限工具、握手与接入片段已测试；Codex 侧已写入；Claude 侧等你 ⌘Q。CLI 与控制台随时可用 |
| 可查看、暂停、取消、失败恢复、按预设链切换的任务系统 | ✅ | 20 状态的状态机；`pause/cancel/retry/recover`；备用链（策略 `ask`） |
| 带真实 diff、机械测试和审批记录的任务验收 | ✅ | `par task diff`；Kernel 自己跑的检查日志；批准/验收事件见 `par task events` |
| 本地 Web 页面，任意模型耗尽后仍可操作 | ✅（三页，轻量版） | 停掉全部 Provider 仍能打开控制台看到持久化的任务（有测试）；与总方案的差别见「问题与限制」第 9 条 |
| 原 Harness State 唯一权威、项目长期记忆开关保持原控制方式 | ✅ | Runtime 从不写项目 `.ai/`、`AGENTS.md`、`CLAUDE.md`（合同路径规则禁止）；`par harness check` 对原有四个项目无变化 |
| 脱敏日志、明确留存、配置备份/回滚、禁止无授权付费 | ✅ | 脱敏有测试与变异检验；留存与清理见使用指南第 13 节；配置改动都留 `*.par-backup-*`；`additional_paid_credits` 必须为 false |
| P0–P8 阶段报告、T01–T24 测试结果、已知限制与日常操作手册 | ✅ | `docs/reports/` 下 P0–P8 与路线 A、轻量控制台的报告；附录 A；「问题与限制」；使用指南 |

## 附录 C：最终反例核对（任何一条出现即不得宣布全量验收通过）

| # | 反例 | 结论 | 依据 |
|---|---|---|---|
| 1 | 使用了未经批准的 API 计费或高权限启动参数 | **没有出现** | 配置拒绝 `additional_paid_credits = true`；Codex 适配器遇到 API key 登录就拒绝；测试断言启动参数里没有 `--dangerously*`、`danger-full-access`、`--full-auto`（`test_codex_adapter`、`test_agy_adapter`、`test_no_real_calls`）；真实运行用的是订阅登录 |
| 2 | Worker 成功报告只靠 LLM 自述 | **没有出现** | 两次真实任务的「通过」来自 Kernel 自己跑的 `python3 -m unittest -q`（每次 attempt 目录里的 `check_unit.log`）；Worker 自述在结果里标着「未验证」（T19） |
| 3 | 换 Provider 丢失未合并代码、覆盖主仓或重复执行外部副作用 | **没有出现** | 接力只用 Kernel 自己切出并核对过的补丁（T09、T10）；补丁从不被自动应用（每次运行后真实项目 `git status` 仍干净）；同一幂等键不重复执行（T13） |
| 4 | 新的隐蔽长期记忆、项目事实副本或多份互相竞争的 STATE | **没有出现** | 没有写过项目的 `.ai/`、`AGENTS.md`、`CLAUDE.md`（T14）；没有 LaunchAgent、全局规则或定时任务；Harness 体检对原有项目无变化 |
| 5 | 关闭 Claude 后整个任务树丢失/无法从 UI 恢复 | **没有出现** | 状态在 SQLite 台账里而不在任何聊天里；恢复演练在新位置读出了全部任务与证据；T11、T12、T22 |
| 6 | 官方额度耗尽被误判为可用、持续无限重试 | **没有出现** | 额度耗尽进入冷却且不重试；裸 429 不当作额度耗尽（变异检验 M11）；自动动作有预算（T07、T08） |
| 7 | 未处理敏感路径风险就把真实 Runge 文件交给宽权限 Worker | **没有出现** | Runge 与另外三个真实项目仍是 `WRITE_DISABLED`；写入开关的体检拒绝「git 里看得到敏感文件」「STATE 超限」等情形；真实写入只发生在没有敏感文件的 `par_trial` 上 |

## 追加（同一天，两个主控接通并验证之后；版本定为 `1.0.0`）

正文里「等你」的两件事都做完了，V1.0 的全部 Gate 条件现已满足。

**Harness 基线**：你确认接受（本次对话里）。`par harness check --save-baseline` 已运行：28 处差异确认全部来自新项目 `par_trial`，其余四个真实项目和 Core 没有任何变化；旧基线保留为 `baseline.prev.json`。现在 `par harness check` 零差异，退出码 0。

**Claude Desktop 的接通，比预想的难一些**，记录一下走过的弯路（都没有碰你的真实数据，只影响这一个配置文件）：
1. 第一次：后台等待工具一秒查一次、要连续 3 次（约 2~3 秒）确认「真的退出了」才敢写。你按菜单正常退出（⌘Q）两次，但新进程几乎瞬间就起来，等待窗口从未抓到过空档，配置没被改动。
2. 第二次：改用 `pkill -9 -f` 一次性打 Claude.app 的整片进程（主进程加全部 helper）。逐 0.1 秒追踪验证：主进程这个 PID 在 6 秒内毫无变化——`pkill` 的这个用法根本没杀死它，只是顺手杀掉了部分 helper 进程，它们又被 Chromium 自己的机制重新拉起，看起来像「瞬间复活」，其实主进程从未死过。
3. 第三次（成功）：后台等待工具先开着，然后**只对主进程这一个 PID**发 `kill -9`——这次真正杀死了，而且**没有自动重开**（是你手动打开的，期间出现过一次短暂的「无法连接」，就是这一刀造成的）。但那一次后台工具没有同时在等，所以没人接住那个空档，配置仍是空的。
4. 第四次（成功，这次两件事一起做对）：后台等待工具先开着，再精准 `kill -9` 主进程这一个 PID——这次工具接住了那个空档：写入 `par` 条目、自检、自动重新打开 Claude Desktop。日志：「完成：已加入 par 条目。备份：`claude_desktop_config.json.par-backup-20261016-095921`」。

**两个主控都用真实调用验证过**（只读，没有提交任何任务）：
- **Codex**：`codex exec --approve-for-me`（非交互、只读沙箱，限制在一个空目录里）跑了一次，起手提示词之后让它调用 `list_tasks`，返回真实的 2 个任务（最新 `par_trial-0002`，已验收）；约 3.4 万 token 输入（缓存 1.6 万）、182 token 输出（厂商自报）。
- **Claude**：Claude Desktop 重新打开后，这个正在写报告的 Code 会话的工具列表里**立刻**多出了 `par` 的 7 个工具（不需要新开聊天、不需要贴起手提示词——因为这个会话本身就在新配置生效之后的那个应用里）；直接调用 `list_tasks`，同样返回真实的 2 个任务。
- `par coordinator status` 确认两者「最近出现」都是刚才；台账核对：任务数、批准数全程没变，只各加了一条心跳事件（`COORDINATOR_SEEN`，限速每分钟最多一条）。

**一个意外但有用的发现**：接入 Claude Desktop 用的是用户级配置（`claude_desktop_config.json`），这正如项目早先的预判——会让**你所有项目的所有 Code 会话**都多出 `par` 的 7 个工具，不只是「Claude Desktop」那个聊天界面。这与项目一直以来「默认不在用户级接 Claude Code，只在需要的项目里接」的选择是同一件事的两面：你选择了接通 Claude Desktop（用户级），这个副作用就跟着发生了；已在 [USER_GUIDE.md](../USER_GUIDE.md) 2.2 节写明。如果你不想所有项目都带着这 7 个工具，可以撤销用户级接入（`ops/connect_claude_desktop.py --remove`），改用项目级的 `claude mcp add-json`（见该节）。

**版本**：改为 `1.0.0`，`PHASE` 由 `P8` 改为 `RELEASE`；测试 `test_phase_matches_dev_number` 相应调整（区分「仍在 devN」与「已是最终版」两种情况）。全量测试 1404 项依旧全部通过。

**结论**：P5 Gate（Claude 通过支持的接口创建/查询一次真实任务）与 P8 Gate 的「Harness 同步检查健康」现在都是 **PASS**，没有保留项。附录 B、C 的核对结论不变（没有出现任何「最终反例」）。**V1.0 全部验收通过。**
