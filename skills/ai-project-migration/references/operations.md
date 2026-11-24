# 文件复制、离线切换与接续

脚本均位于已安装 Skill 的 `scripts` 目录。先做[迁移环境预检](preflight.md)，然后用通过检查的本机 CPython 绝对路径和 `-B` 运行以下 Python 脚本。把占位符换成本次完整路径，作为独立参数传递。给用户的离线命令使用 `cutover.py prepare` 的实际输出。普通检查从项目树外启动，**旧聊天读写探针必须继承选定会话的默认工作目录**。

## 运行目录与恢复

`OLD` 是用户确认的原实体目录，`NEW` 是其选定的目标项目目录；`BACKUP` 是 OLD 的同级目录“原名_迁移备份”。`RUN` 位于两棵项目树和备份之外，通常取目标父目录的私有 `.migration-checks/项目名-日期时间-短随机码`。审计目录可能含本机路径和会话标识，先核对访问权限。改名前把运行目录位置交给用户，并确保 Python 和 Skill 都位于重命名范围以外。

| 时机 | 调用 |
|---|---|
| 核对真实路径后 | `preflight.py --source OLD --target NEW` 检查两端环境、卷标识和文件系统。读回 `volumes` 检查中的 `same_volume` 判断是否同盘。目标外接盘断开时停止，避免在原挂载位置创建副本 |
| 预检通过且用户确认源盘、目标盘和路径后 | `run_record.py start OLD NEW --host HOST --run RUN`。HOST 取本次项目或旧会话的所属宿主 `codex`、`claude-code` 或 `generic`。Codex 还须传真实 `--project-id ID`，其他宿主无原生 ID 时用独立 migration_id 关联本次运行。预先创建专用审计父目录 |
| 源和目标位于同一个盘时 | 先告知“这次移动不会释放这个盘的空间”；只有用户明确选择在同一个盘里整理文件，才在 start 中添加 `--allow-same-volume` |
| 运行记录已建立后，每步结束或暂停 | `run_record.py mark --run RUN --step N --status STATUS --evidence "结果和证据位置" --next-action "下一步由谁做、怎么做"`。首次预检阻塞时只在聊天中报告。STATUS 可为 passed、waiting、blocked。仅步骤 6 可用 not_applicable（已确认没有适用旧聊天）或 unverified（无法取得真实证据，继续列出最终缺口并保留备份） |
| 重开后 | `run_record.py show --run RUN`。旧 Windows 检查点也应读回；核实实际路径、实体、配置和既有回执再接续 |

检查点顺序追加。实际操作成功但没来得及记录时，先验证并补登，不重复改名或发送消息。已通过的步骤发生变化，用 `run_record.py reopen --run RUN --step N --evidence "发现的变化" --next-action "修复和复核"` 使相关阶段重新待核。确需更换有冲突的候选时，保留旧运行记录并给新候选独立的所有权记录。不要编辑 owner.json 或计划文件强行认可替换过的实体。

用最新核验结果判断当前卡点。若命令将第 7 步记为 `blocked`，或 `show` 发现新差异，结合返回的 `verification_error`（如有）和最新差异报告，按实际缺口确定接下来的动作，依[阶段汇报与接续](../SKILL.md#阶段汇报与接续)直接告诉用户。

## 步骤 1–3 · 保全与复制

1. 确认项目、源实体、目标父目录及空间。源、目标、备份和审计不得互相包含或复用。保留已有的未知目标。核对文件系统、已打开文档、后台写入、工作树及特殊结构。让用户保存并暂停相关活动，助手不强制终止进程。按[宿主适配](hosts.md)和[聊天验收](chats.md)保存所属平台的基准。
2. 采集 `git_check.py baseline OLD --out RUN/git-before.json`。非 Git、只有主仓、带外部工作树分别记录。检测到仓库内子目录、独立工作树、子模块或外置 Git 数据时说明影响并逐个核实；非 Git 根的基准不能代表内部所有嵌套仓库。
3. 运行 `tree_copy.py copy OLD NEW --audit RUN/tree-copy` 创建副本，再独立运行 `tree_copy.py verify OLD NEW --audit RUN/tree-copy`。工具在哈希前用 `os.lstat()` 核对硬链接身份，Windows 的 `DirEntry.stat()` 缓存不能作为文件身份和链接计数依据。分别检查退出状态、真实回执、相对路径、空目录、文件字节和源稳定性。只在确认归属本次且身份仍匹配时接续候选。
   Windows 依赖文件可能与 pnpm 存储或其他项目共享硬链接。跨卷无法保留这种共享关系。用户明确同意为目标建立独立依赖副本后，单目录 `run_record.py start` 添加 `--materialize-dependency-hardlinks`，批次在所选来源设置同名布尔字段 `materialize_dependency_hardlinks: true`。仅允许全部树内成员路径含独立 `node_modules` 分量的外部组，继续保留树内分组。复制和校验从本次 `run.json` 继承选项，并将它绑定至 owner、回执和离线计划。检查回执的 `dependency_hardlink_materialization`，目标外部组数量必须为零。其他外部组仍需保全。已有失败记录保留，为新增授权建立新运行或批次。
4. Windows 两端须本地 NTFS。检查普通文件 SHA-256、命名流、树内硬链接、属性、权限、所有者和组；SACL 不在此助手核验范围。项目内的目录联接作为独立对象记录和复制，目标须是本树内的实体目录，目标路径的中间组件也须为实体目录。按相对位置把联接映射到新树，核验联接类型、目标和自身元数据，遍历到联接即停止。切换时和备份复核时，依据原迁移记录解释原件中的旧绝对联接目标。损坏联接、树外目标和其他重解析点须明确说明并保留原件。macOS 两端须本地已挂载可写 APFS，核验字节、树内硬链接、所有者与组、权限和 ACL、文件标志、时间、扩展属性及资源分支。名称碰撞、无法保持的元数据、树外硬链接或目录中的符号链接等须明确说明并保留原件。不能通过展开链接把外部内容意外纳入树。
5. 源在复制期间发生写入时，请用户暂缓编辑，查清新增或不同项并重新核验。只能补齐本次受管候选中能确认归属的内容。覆盖方向未知时保留两侧另建候选；严禁镜像清空或递归删除已有项目。严格核验通过才能换源。

## 步骤 4 · 原项目和 Git

按[宿主手册](hosts.md)切换原项目或在 NEW 打开新的会话，回读目标身份和实际工作目录。记录执行助手和所属宿主分别做了哪些操作、哪些证据可用。Codex 多源项目保留其他源及其顺序。没有可编辑原项目配置的宿主，使用可验证的新会话入口并记录状态，不虚构一个项目设置或原生 ID。

文件校验通过后，存在关联工作树时运行：

```text
git_check.py repair NEW --baseline RUN/git-before.json --out RUN/git-repair.json
git_check.py verify NEW --baseline RUN/git-before.json --out RUN/git-after-switch.json
```

核实各指针属于原仓或已绑定的目标。工作树状态清单与文件逐字节哈希分别报告；外部工作树保留自己的独立物理副本。出现偏差先查明用户的新修改和迁移影响；不要 prune、remove、reset、强制检出或用源索引覆盖目标的新工作。

## 步骤 5 · 退出前交付一段命令

让用户选择 A 手动改名或 B 由命令改名。在用户退出 Agent 前完成选择、生成命令、说明成功输出及重开后的接续方式。初次准备须绑定本次受管副本和最新成功的独立 verify 回执。

```text
cutover.py prepare --source OLD --target NEW --audit RUN --copy-audit RUN/tree-copy --mode manual
cutover.py prepare --source OLD --target NEW --audit RUN --copy-audit RUN/tree-copy --mode auto
```

只运行选择的一行 `prepare`，在用户退出前按[手动终端指引](terminal.md#offline-cutover)交付它生成的那一段离线命令、对应系统的图示、所选模式的操作顺序，以及执行后的回报和关窗说明。预期输出包含准确的 FullName、Target、Backup 和对应的 LinkType（Windows 为 Junction，macOS 为 SymbolicLink）。

离线执行期间继续暂停两端写入。命令在改名前复核原目录和目标的最新成功校验清单。手动改名或上次停在改名后时，命令在建链接前复核已改名的备份和目标。发现变化便停止，保留两端及报错。还未改名时，先保全和核对各自的新改动，再独立校验并重试。已经改名时先做只读接续检查，需要恢复原名重验时按下方的回退流程处理。

重开后运行 `cutover.py status --plan RUN/cutover-plan.json`。phase 为 linked 且旧入口、目标和备份身份都匹配，才进入步骤 6。遇到错误链接、重名备份、盘的系统标识变化或目录被占用，停止该次切换，保留原件并写出准确路径。实际已建立且身份正确的链接可直接验证，不重新创建。

## 步骤 6–7 · 会话验证、复核与清理

按[聊天验收](chats.md)获得同一旧会话的宿主证据后，从默认目录执行 `probe.py --plan RUN/cutover-plan.json --request-id UUID`。宿主已确认恢复同一原 ID 且默认目录可能解析为目标实体时，使用 `--resumed-session-id 原ID`。纯文件系统诊断单独使用 `--filesystem-only`；这两种证据各自标注，禁止以文件路径或脚本回执冒充宿主对原会话的认证。

确认备份实体和链接身份后，再运行 `tree_copy.py verify BACKUP NEW --audit RUN/backup-check`。Git 项目运行 `git_check.py verify NEW --baseline RUN/git-before.json --out RUN/git-final.json`。输出用新文件名保留每次尝试。列出备份独有、目标独有、内容、元数据及硬链接差异。只对已查证的探针根目录 mtime、Git 修复触及的特定反向指针 mtime 单独说明，逐项核对哈希和实际回执，不整体豁免 .git。对迁移后新编辑的文件保全新版、解释差异；备份有独有内容或存在未解释变化，保留原件并指出具体差异。

最终报告包括历史读取覆盖、恢复的原会话 ID 与实际双向测试范围、Git 工作树和源盘备份。读写测试和历史读取的计数分列；未验证栏写出原因。收尾和再次查看完成记录都读取 `run_record.py show` 的 `cleanup_notice`，**先展示 `warning` 中的旧路径警示及完整路径**。已确认入口直达目标时，保留回执中的实际链接类型、空间说明和删除风险。下一行显示必须保留的目标实体，再列备份实体的实际路径。未达到条件必用 **“⛔ 暂勿清理备份”**，同一条消息说明卡住的验收项和复验条件，并交代由谁完成下一项核对；只有验收通过，才提示用户可手动清理备份。审计文件保存在本机。

## 中断与回退

手动搬过文件、迁移暂停，或记录与现场不同，先做[只读接续检查](resume.md)。已核实的单目录记录继续使用原助手。出现新来源时先确认范围，再为未迁目录单独准备新的单目录计划或批次。

换源前可继续使用原件。换源或重连工作树后需要回退时，先暂停写入、保存并比较新工作、确认备份实体和项目/宿主身份。列出恢复原名、恢复宿主项目位置、恢复 Git 关联及重新验收的准确顺序。已存在链接、要合并新改动或需要变更现有数据时，先向用户说明具体影响并取得确认。不得递归删除目录。
